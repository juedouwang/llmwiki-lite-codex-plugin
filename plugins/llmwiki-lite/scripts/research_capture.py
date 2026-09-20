"""codex rollout 采集适配层（M-02）：授权、脱敏、解析与活动落账。

边界：
- 本模块只做机制，不做调度；`capture_enabled` 默认 false，未授权时拒绝采集且不写任何东西。
- 只读来源文件；所有写入都通过 WorkbenchStore（schema v1）与 state_root 下的 consent.json。
- 落盘前先经过 `redact()`。这是尽力而为的脱敏，不是完整 DLP，不能声称识别所有秘密。

三个已知陷阱（真实 schema 实测）：
1. 注入块：`response_item` 里 `role=="user"` 的消息并不都是用户打的字，
   content 里第一块常是注入的 AGENTS.md 指令、第二块是 environment_context，必须逐块过滤。
2. 文件不是严格只追加：投影偏移可能大于文件长度（compaction/迁移），
   此时必须判定为文件被重写，从头发起一轮扫描并推进 generation，不能当成"没有新内容"。
3. 归档是移动文件：同一 session_id 从 sessions/ 挪到 archived_sessions/ 后，
   路径变了但会话没变，不能被当成两个会话；路径每次重新解析，不做缓存。

命名：smoke_test.py 的 test_platform_metadata 要求 scripts/*.py 不含宿主专属的大小写
字样（首字母大写的宿主名），所以本模块对外用宿主中立的 `RolloutReader` 与
`RolloutCaptureAdapter`；具体宿主由模块常量 HOST（"codex"）与适配器入口决定。
"""

from __future__ import annotations

import fnmatch
from datetime import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workbench_store import (  # noqa: E402
    WorkbenchStore,
    canonical_json,
    new_id,
    utc_now,
)

HOST = "codex"
HOSTS = ("codex", "claude_code", "opencode")
CONSENT_SCHEMA_VERSION = 1
SESSION_SCOPE = "new_bound_sessions"
DEFAULT_EXCLUDE_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "**/secrets/**")
REDACTED = "[REDACTED]"

CONSENT_FILE_NAME = "consent.json"
WORKBENCH_DIR_NAME = "workbench"
ROLLOUT_GLOB = "rollout-*.jsonl"
ARCHIVED_SESSIONS_DIR = "archived_sessions"
SESSIONS_DIR = "sessions"

_RFC3339 = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


# ===== 错误 =====

class CaptureError(Exception):
    """采集层错误基类，带稳定 code 供上层转成 HTTP/CLI 错误。"""

    code = "CAPTURE_ERROR"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class ConsentRequired(CaptureError):
    """未授权就调用采集入口。上层应转成 CONSENT_REQUIRED。"""

    code = "CONSENT_REQUIRED"


class CaptureInputError(CaptureError):
    """输入不合法。"""

    code = "INVALID_INPUT"


# ===== 授权 D-05 =====

def _project_id(project: Mapping[str, Any]) -> str:
    value = project.get("id") if isinstance(project, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise CaptureInputError("项目缺少有效 id，无法定位授权文件。")
    return value


def state_workbench_root(project: Mapping[str, Any]) -> Path:
    state_root = project.get("state_root") if isinstance(project, Mapping) else None
    if not isinstance(state_root, str) or not state_root.strip():
        raise CaptureInputError("项目缺少有效 state_root，无法定位授权文件。")
    return Path(state_root)


def consent_path(project: Mapping[str, Any]) -> Path:
    return state_workbench_root(project) / WORKBENCH_DIR_NAME / CONSENT_FILE_NAME


def default_consent(project_id: str) -> dict[str, Any]:
    """首次授权契约：全部关闭，capture_enabled=false，authorized_at=null。"""
    return {
        "schema_version": CONSENT_SCHEMA_VERSION,
        "revision_no": 1,
        "project_id": project_id,
        "capture_enabled": False,
        "hosts": {host: False for host in HOSTS},
        "session_scope": SESSION_SCOPE,
        "allow_source_text": True,
        "allow_image_content": False,
        "allow_online_metadata": False,
        "allow_automatic_knowledge": False,
        "allow_scheduled_ai": False,
        "exclude_patterns": list(DEFAULT_EXCLUDE_PATTERNS),
        "authorized_at": None,
    }


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise CaptureInputError(f"{field_name} 必须是 true/false。")
    return value


def _optional_timestamp(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        raise CaptureInputError(f"{field_name} 必须是 UTC RFC3339 时间或 null。")
    return value


def validate_consent(value: Any, project_id: str) -> dict[str, Any]:
    """严格校验授权对象：未知字段、缺字段、类型错误一律拒绝。"""
    if not isinstance(value, Mapping):
        raise CaptureInputError("授权内容必须是对象。")
    allowed = set(default_consent(project_id))
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise CaptureInputError(f"授权包含未知字段：{', '.join(unknown)}。")
    merged = default_consent(project_id)
    merged.update(value)
    if merged.get("schema_version") != CONSENT_SCHEMA_VERSION:
        raise CaptureInputError("授权 schema_version 不受支持。")
    if merged.get("project_id") != project_id:
        raise CaptureInputError("授权 project_id 与当前项目不一致。")
    revision_no = merged.get("revision_no")
    if isinstance(revision_no, bool) or not isinstance(revision_no, int) or revision_no < 1:
        raise CaptureInputError("授权 revision_no 无效。")
    merged["capture_enabled"] = _strict_bool(
        merged.get("capture_enabled"), "capture_enabled"
    )
    hosts = merged.get("hosts")
    if not isinstance(hosts, Mapping) or set(hosts) != set(HOSTS):
        raise CaptureInputError("hosts 必须且只能包含 codex/claude_code/opencode。")
    merged["hosts"] = {host: _strict_bool(hosts[host], f"hosts.{host}") for host in HOSTS}
    if merged.get("session_scope") != SESSION_SCOPE:
        raise CaptureInputError("session_scope 只支持 new_bound_sessions。")
    for name in (
        "allow_source_text",
        "allow_image_content",
        "allow_online_metadata",
        "allow_automatic_knowledge",
        "allow_scheduled_ai",
    ):
        merged[name] = _strict_bool(merged.get(name), name)
    patterns = merged.get("exclude_patterns")
    if (
        not isinstance(patterns, list)
        or len(patterns) > 200
        or any(not isinstance(item, str) or not item.strip() for item in patterns)
    ):
        raise CaptureInputError("exclude_patterns 必须是最多 200 条的非空字符串。")
    merged["authorized_at"] = _optional_timestamp(merged.get("authorized_at"), "authorized_at")
    return merged


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def read_consent(project: Mapping[str, Any]) -> dict[str, Any]:
    """读取授权；文件不存在时返回默认值且**不写盘**。"""
    project_id = _project_id(project)
    path = consent_path(project)
    if not path.is_file():
        return default_consent(project_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureInputError(f"授权文件无法解析：{path}") from exc
    return validate_consent(payload, project_id)


def ensure_consent(project: Mapping[str, Any]) -> dict[str, Any]:
    """确保授权文件存在（写入的永远是默认关闭状态）。不会启用采集。"""
    path = consent_path(project)
    if not path.is_file():
        consent = default_consent(_project_id(project))
        _atomic_write_text(
            path, json.dumps(consent, ensure_ascii=False, indent=2) + "\n"
        )
        return consent
    return read_consent(project)


def write_consent(
    project: Mapping[str, Any], patch: Mapping[str, Any], *, now: str | None = None
) -> dict[str, Any]:
    """按 patch 更新授权：revision_no 递增；authorized_at 只在首次真正启用时写入。"""
    if not isinstance(patch, Mapping):
        raise CaptureInputError("授权更新必须是对象。")
    current = read_consent(project)
    unknown = sorted(set(patch) - set(default_consent(_project_id(project))))
    if unknown:
        raise CaptureInputError(f"授权更新包含未知字段：{', '.join(unknown)}。")
    merged = dict(current)
    merged.update(patch)
    merged = validate_consent(merged, _project_id(project))
    merged["revision_no"] = int(current["revision_no"]) + 1

    activation = bool(
        merged["capture_enabled"]
        or any(merged["hosts"].values())
        or merged["allow_automatic_knowledge"]
        or merged["allow_scheduled_ai"]
    )
    if activation and merged["authorized_at"] is None:
        merged["authorized_at"] = now or utc_now()

    _atomic_write_text(
        consent_path(project), json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    )
    return merged


def consent_revision(project: Mapping[str, Any]) -> str:
    """授权内容哈希，用于乐观并发与审计。"""
    return hashlib.sha256(canonical_json(read_consent(project))).hexdigest()


def require_capture(project: Mapping[str, Any]) -> dict[str, Any]:
    """采集入口的统一闸门：未授权直接抛 ConsentRequired，不静默采集。"""
    consent = read_consent(project)
    if not consent["capture_enabled"] or not any(consent["hosts"].values()):
        raise ConsentRequired(
            "尚未授权采集，来源正文不会被读取。",
            details={
                "project_id": consent["project_id"],
                "consent_revision_no": consent["revision_no"],
            },
        )
    return consent


# ===== 脱敏 =====

_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.S,
)
_SECRET_LITERALS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?([^\s'\"]{8,})"
)


def redact(text: str) -> tuple[str, int]:
    """落盘前替换明显秘密，返回 (脱敏后文本, 替换计数)。

    只覆盖常见私钥块与令牌字面量，属尽力而为；不要据此声称能识别所有秘密。
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", 0
    count = 0

    def bump(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return REDACTED

    def bump_assignment(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}={REDACTED}"

    redacted = _PEM_BLOCK.sub(bump, text)
    for pattern in _SECRET_LITERALS:
        redacted = pattern.sub(bump, redacted)
    redacted = _SECRET_ASSIGNMENT.sub(bump_assignment, redacted)
    return redacted, count


# ===== 项目归属 =====

_VERBATIM_UNC = re.compile(r"^\\\\\?\\UNC\\", re.I)
_VERBATIM = re.compile(r"^\\\\\?\\")


def strip_verbatim_prefix(raw: str) -> str:
    """去掉 Windows \\\\?\\ verbatim 前缀；实测 38/39 个真实 cwd 带这个前缀。

    必须先去前缀再 resolve，否则 Path.resolve() 会原样保留前缀，normcase 后无法与
    已注册的 source_root 匹配。
    """
    if not isinstance(raw, str):
        return raw
    if _VERBATIM_UNC.match(raw):
        return "\\\\" + raw[len("\\\\?\\UNC\\") :]
    if _VERBATIM.match(raw):
        return raw[4:]
    return raw


def _resolve(raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Path(strip_verbatim_prefix(raw.strip())).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def project_for_cwd(cwd: Any, projects: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """把 cwd 归属到已注册项目：最长前缀匹配，不命中返回 None（不猜）。"""
    target = _resolve(cwd)
    if target is None:
        return None
    key = _path_key(target)
    best: tuple[int, Mapping[str, Any]] | None = None
    for project in projects or ():
        if not isinstance(project, Mapping):
            continue
        root = _resolve(project.get("source_root"))
        if root is None:
            continue
        root_key = _path_key(root)
        if key == root_key or key.startswith(root_key.rstrip(os.sep) + os.sep):
            depth = len(root_key)
            if best is None or depth > best[0]:
                best = (depth, project)
    return dict(best[1]) if best is not None else None


# ===== 排除模式 =====

def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                out.append(".*")
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    index += 1
            else:
                out.append("[^/]*")
                index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return re.compile("^" + "".join(out) + "$")


_TOKEN = re.compile(r"[^\s\"',;|]+")


def matches_exclude_patterns(text: Any, patterns: Sequence[str]) -> str | None:
    """检查文本里的路径样式 token 是否命中授权排除项；命中则返回该模式。"""
    if not isinstance(text, str) or not text:
        return None
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns or ():
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        normalized = pattern.strip().replace("\\", "/")
        compiled.append((normalized, _glob_to_regex(normalized)))
    if not compiled:
        return None
    for token in _TOKEN.findall(text):
        candidate = token.replace("\\", "/")
        if not candidate:
            continue
        basename = candidate.rsplit("/", 1)[-1]
        for pattern, regex in compiled:
            if "/" in pattern:
                if regex.match(candidate) or regex.match(candidate.lstrip("/")):
                    return pattern
            elif regex.match(basename):
                return pattern
            if fnmatch.fnmatchcase(basename, pattern):
                return pattern
    return None


# ===== rollout 解析 =====

INJECTION_PREFIXES = (
    "# AGENTS.md",
    "<environment_context>",
    "<environment_context ",
    "<user_instructions>",
    "<permissions instructions>",
    "<ENVIRONMENT_CONTEXT>",
)


def _block_text(block: Any) -> str | None:
    if not isinstance(block, Mapping):
        return None
    text = block.get("text")
    return text if isinstance(text, str) else None


def _is_injected_block(block: Any, *, role: str) -> bool:
    """陷阱 1：role=user 的 content 里常有注入块，不能一律取 content[0]。"""
    if not isinstance(block, Mapping):
        return True
    text = _block_text(block)
    if text is None or not text.strip():
        return True
    block_type = block.get("type")
    if block_type not in (None, "input_text", "output_text", "text"):
        return True
    if role != "user":
        return False
    head = text.lstrip()[:200]
    return any(head.startswith(prefix) for prefix in INJECTION_PREFIXES)


def _visible_text(payload: Mapping[str, Any], *, role: str) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if _is_injected_block(block, role=role):
            continue
        text = _block_text(block)
        if text is not None:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _message_source_key(
    session_id: str, kind: str, origin: str, occurred_at: Any, text: str, call_id: str = ""
) -> str:
    """跨行稳定：同一 message 重复解析得到同一个 key；重写后内容不变即 key 不变。

    刻意不把 origin 与 occurred_at 计入：同一条消息在 response_item 与 event_msg
    两条路径下的时间戳可能差一毫秒，若计进去会对同一句话写出两条活动。
    按 (会话, 类型, 正文) 归一后，两条路径自然收敛成同一条记录。
    """
    material = canonical_json(
        {"session_id": session_id, "kind": kind, "text": text, "call_id": call_id or ""}
    )
    return f"codex:{session_id}:{kind}:{hashlib.sha256(material).hexdigest()[:32]}"


def _split_complete_lines(data: bytes) -> tuple[bytes, bytes]:
    """切出完整行与残余；残余可能是写到一半的半截 JSON 或半截多字节字符。"""
    if not data:
        return b"", b""
    if data.endswith(b"\n"):
        return data, b""
    index = data.rfind(b"\n")
    if index == -1:
        return b"", data
    return data[: index + 1], data[index + 1 :]


@dataclass
class RolloutRead:
    messages: list[dict[str, Any]] = field(default_factory=list)
    next_offset: int = 0
    size: int = 0
    rewritten: bool = False
    truncated: bool = False
    bad_lines: int = 0
    reconcile: dict[str, Any] = field(default_factory=dict)


class RolloutReader:
    """解析单个 codex rollout 文件，产出规范化消息。"""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id

    def read(
        self,
        path: str | Path,
        *,
        offset: int = 0,
        session_id: str | None = None,
        file_label: str | None = None,
    ) -> RolloutRead:
        target = Path(path)
        size = target.stat().st_size
        sid = session_id or self.session_id or target.stem
        # 陷阱 2：偏移大于长度 => 文件被重写（compaction/迁移），必须从头再扫。
        rewritten = offset > size
        start = 0 if rewritten else max(0, offset)

        with target.open("rb") as handle:
            handle.seek(start)
            data = handle.read(8 * 1024 * 1024)

        complete, remainder = _split_complete_lines(data)
        if not complete and len(data) == 8 * 1024 * 1024:
            raise CaptureInputError("单条消息超过读取上限，保留原读取位置。")
        next_offset = start + len(complete)
        label = file_label if file_label is not None else target.name

        messages: list[dict[str, Any]] = []
        bad_lines = 0
        for raw in complete.split(b"\n"):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                bad_lines += 1
                continue
            if not isinstance(record, Mapping):
                continue
            messages.extend(self._records(record, sid, label))

        reconcile = self._reconcile(messages)
        return RolloutRead(
            messages=reconcile.pop("messages"),
            next_offset=next_offset,
            size=size,
            rewritten=rewritten,
            truncated=bool(remainder) or next_offset < size,
            bad_lines=bad_lines,
            reconcile=reconcile,
        )

    # -- 单行解析 --

    def _records(self, record: Mapping[str, Any], sid: str, label: str) -> list[dict[str, Any]]:
        record_type = record.get("type")
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            return []
        occurred_at = record.get("timestamp")
        if record_type == "response_item":
            return self._response_item(payload, sid, label, occurred_at)
        if record_type == "event_msg":
            return self._event_msg(payload, sid, label, occurred_at)
        return []

    def _response_item(
        self, payload: Mapping[str, Any], sid: str, label: str, occurred_at: Any
    ) -> list[dict[str, Any]]:
        item_type = payload.get("type")
        if item_type == "message":
            role = payload.get("role")
            if role not in ("user", "assistant"):
                return []
            text = _visible_text(payload, role=role)
            if not text:
                return []
            kind = "user_message" if role == "user" else "assistant_message"
            return [self._message(kind, "response_item", text, sid, label, occurred_at)]
        if item_type == "function_call":
            name = payload.get("name") if isinstance(payload.get("name"), str) else ""
            call_id = payload.get("call_id") if isinstance(payload.get("call_id"), str) else ""
            arguments = payload.get("arguments")
            text = arguments if isinstance(arguments, str) else ""
            if not name and not text:
                return []
            return [
                self._message(
                    "tool_call",
                    "function_call",
                    text,
                    sid,
                    label,
                    occurred_at,
                    tool_name=name,
                    call_id=call_id,
                )
            ]
        return []

    def _event_msg(
        self, payload: Mapping[str, Any], sid: str, label: str, occurred_at: Any
    ) -> list[dict[str, Any]]:
        item_type = payload.get("type")
        if item_type == "user_message":
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                return []
            if _is_injected_block({"type": "input_text", "text": message}, role="user"):
                return []
            return [self._message("user_message", "event_msg", message.strip(), sid, label, occurred_at)]
        if item_type == "agent_message":
            phase = payload.get("phase")
            if phase not in (None, "final_answer"):
                return []
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                return []
            return [
                self._message(
                    "assistant_message", "event_msg", message.strip(), sid, label, occurred_at
                )
            ]
        return []

    def _message(
        self,
        kind: str,
        origin: str,
        text: str,
        sid: str,
        label: str,
        occurred_at: Any,
        *,
        tool_name: str = "",
        call_id: str = "",
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "origin": origin,
            "text": text,
            "occurred_at": occurred_at if isinstance(occurred_at, str) else None,
            "source_key": _message_source_key(
                sid, kind, origin, occurred_at, text, call_id
            ),
            "tool_name": tool_name,
            "call_id": call_id,
            "file": label,
        }

    # -- A/B 对账 --

    def _reconcile(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """两条路径要不重不漏地对齐；对不上的如实记为缺口，不假装都收到了。"""
        report: dict[str, Any] = {}
        emitted: list[dict[str, Any]] = []
        for kind in ("user_message", "assistant_message"):
            group = [m for m in messages if m["kind"] == kind]
            if not group:
                continue
            primary = [m for m in group if m["origin"] == "event_msg"]
            secondary = [m for m in group if m["origin"] == "response_item"]
            remaining = list(primary)
            matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
            secondary_only: list[dict[str, Any]] = []
            for item in secondary:
                hit = next((p for p in remaining if p["text"] == item["text"]), None)
                if hit is None:
                    secondary_only.append(item)
                else:
                    remaining.remove(hit)
                    matched.append((item, hit))
            report[kind] = {
                "response_item": len(secondary),
                "event_msg": len(primary),
                "matched": len(matched),
                "response_item_only": len(secondary_only),
                "event_msg_only": len(remaining),
            }
            # 对上的优先用 event_msg（更接近用户原始输入）
            emitted.extend(hit for _, hit in matched)
            emitted.extend(secondary_only)
            emitted.extend(remaining)

        tools = [m for m in messages if m["kind"] == "tool_call"]
        emitted.extend(tools)
        report["tool_call"] = {"parsed": len(tools)}
        report["messages"] = emitted
        return report


def read_session_meta(path: str | Path, *, max_bytes: int = 262144) -> dict[str, Any]:
    """只读文件头部，找出 session_meta。路径每次重新解析，不做缓存（陷阱 3）。"""
    target = Path(path)
    try:
        with target.open("rb") as handle:
            head = handle.read(max_bytes)
    except OSError:
        return {}
    for raw in head.split(b"\n"):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(record, Mapping) and record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, Mapping):
                return {
                    "session_id": payload.get("session_id") or payload.get("id"),
                    "cwd": payload.get("cwd"),
                    "timestamp": payload.get("timestamp") or record.get("timestamp"),
                    "thread_source": payload.get("thread_source"),
                    "forked_from_id": payload.get("forked_from_id"),
                    "source": payload.get("source"),
                    "originator": payload.get("originator"),
                }
    return {}


# ===== 采集适配器 =====

def read_rollout(
    path: str | Path, *, offset: int = 0, session_id: str | None = None
) -> RolloutRead:
    """函数式入口，等价于 RolloutReader().read(...)。"""
    return RolloutReader().read(path, offset=offset, session_id=session_id)


def _after_authorization(value, floor):
    try:
        stamp = datetime.fromisoformat(value)
        start = datetime.fromisoformat(floor)
        return stamp.tzinfo is not None and start.tzinfo is not None and stamp >= start
    except (TypeError, ValueError):
        return False


class RolloutCaptureAdapter:
    """扫描 <codex_home> 下的 rollout，按项目归属写入活动与游标。"""

    def __init__(
        self,
        store: WorkbenchStore,
        codex_home: str | Path,
        projects: Iterable[Mapping[str, Any]],
    ):
        self.store = store
        self.codex_home = Path(codex_home)
        self.projects = [dict(p) for p in projects or ()]
        self.reader = RolloutReader()

    # -- 发现文件 --

    def rollout_paths(self) -> list[Path]:
        """每次重新解析路径：归档是移动文件，路径不能缓存（陷阱 3）。"""
        found: list[Path] = []
        sessions_root = self.codex_home / SESSIONS_DIR
        if sessions_root.is_dir():
            found.extend(sorted(sessions_root.rglob(ROLLOUT_GLOB)))
        archived_root = self.codex_home / ARCHIVED_SESSIONS_DIR
        if archived_root.is_dir():
            found.extend(sorted(archived_root.rglob("*.jsonl")))
        deduped: list[Path] = []
        seen: set[str] = set()
        for path in found:
            key = _path_key(path)
            if key not in seen:
                seen.add(key)
                deduped.append(path)
        return deduped

    def _label(self, path: Path) -> str:
        try:
            return path.relative_to(self.codex_home).as_posix()
        except ValueError:
            return path.name

    # -- 游标 --

    def _get_cursor(self, session_id: str, source_key: str) -> dict[str, Any] | None:
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT generation, position_json FROM cursors WHERE session_id = ? AND source_key = ?",
                (session_id, source_key),
            ).fetchone()
        if row is None:
            return None
        try:
            position = json.loads(row["position_json"])
        except (TypeError, ValueError):
            position = {}
        return {"generation": int(row["generation"]), "position": position}

    def _put_cursor(
        self, session_id: str, source_key: str, generation: int, position: Mapping[str, Any]
    ) -> None:
        payload = json.dumps(dict(position), ensure_ascii=False, separators=(",", ":"))
        now = utc_now()
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM cursors WHERE session_id = ? AND source_key = ?",
                (session_id, source_key),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO cursors (id, session_id, source_key, generation, position_json, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id(), session_id, source_key, generation, payload, now),
                )
            else:
                conn.execute(
                    "UPDATE cursors SET generation = ?, position_json = ?, updated_at = ? WHERE id = ?",
                    (generation, payload, now, row["id"]),
                )

    # -- 落账 --

    def ingest_messages(
        self,
        project: Mapping[str, Any],
        session_id: str,
        source_key: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        generation: int,
        position: Mapping[str, Any],
    ) -> dict[str, int]:
        """写入一批规范化消息。activities 的 UNIQUE 约束保证重复写入幂等。"""
        project_id = _project_id(project)
        written = 0
        redacted_total = 0
        for message in messages:
            raw_text = message.get("text")
            text, count = redact(raw_text if isinstance(raw_text, str) else "")
            redacted_total += count
            revision = hashlib.sha256(text.encode("utf-8")).hexdigest()
            evidence: dict[str, Any] = {
                "host": HOST,
                "origin": message.get("origin"),
                "kind": message.get("kind"),
                "text": text,
                "occurred_at": message.get("occurred_at"),
                "file": message.get("file"),
                "source_key": message.get("source_key"),
                "redacted": count > 0,
            }
            if message.get("tool_name"):
                evidence["tool_name"] = message["tool_name"]
            if message.get("call_id"):
                evidence["call_id"] = message["call_id"]
            result = self.store.create_activity(
                project_id=project_id,
                kind=str(message.get("kind")),
                source_key=str(message.get("source_key")),
                source_revision=revision,
                evidence_json=evidence,
                occurred_at=message.get("occurred_at"),
                session_id=session_id,
            )
            if not result.get("data", {}).get("already_exists"):
                written += 1
        self._put_cursor(session_id, source_key, generation, position)
        return {"written": written, "redacted": redacted_total}

    # -- 扫描 --

    def scan(
        self,
        *,
        session_ids: Iterable[str] | None = None,
        history_range: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """扫描一次。未授权项目直接跳过且不写任何东西。"""
        selected = set(session_ids) if session_ids is not None else None
        report: dict[str, Any] = {
            "files": [],
            "sessions": [],
            "skipped_unauthorized": [],
            "unassigned": [],
            "rewrites": [],
            "truncated": [],
            "excluded_by_pattern": [],
            "errors": [],
            "activities_written": 0,
            "redacted_count": 0,
            "reconcile": {},
        }

        for path in self.rollout_paths():
            label = self._label(path)
            meta = read_session_meta(path)
            host_session_id = meta.get("session_id") or path.stem
            if selected is not None and host_session_id not in selected:
                continue

            project = project_for_cwd(meta.get("cwd"), self.projects)
            if project is None:
                report["unassigned"].append(
                    {"file": label, "host_session_id": host_session_id, "cwd": meta.get("cwd")}
                )
                continue
            try:
                consent = require_capture(project)
                if not consent["hosts"].get(HOST) or not consent["allow_source_text"]:
                    raise ConsentRequired("当前来源类型未授权。")
            except ConsentRequired:
                report["skipped_unauthorized"].append(
                    {"file": label, "project_id": project.get("id")}
                )
                continue

            try:
                entry = self._scan_file(path, label, meta, host_session_id, project, consent, history_range)
            except (OSError, CaptureError) as exc:  # 单文件失败不拖垮整轮扫描
                report["errors"].append({"file": label, "error": str(exc)})
                continue

            report["files"].append(entry["file"])
            report["sessions"].append(entry["session"])
            report["rewrites"].extend(entry["rewrites"])
            report["truncated"].extend(entry["truncated"])
            report["excluded_by_pattern"].extend(entry["excluded"])
            report["activities_written"] += entry["written"]
            report["redacted_count"] += entry["redacted"]
            for kind, detail in entry["reconcile"].items():
                bucket = report["reconcile"].setdefault(kind, {})
                for key, value in detail.items():
                    bucket[key] = bucket.get(key, 0) + value

        return report

    def _scan_file(
        self,
        path: Path,
        label: str,
        meta: Mapping[str, Any],
        host_session_id: str,
        project: Mapping[str, Any],
        consent: Mapping[str, Any],
        history_range: tuple[str, str] | None,
    ) -> dict[str, Any]:
        session_result = self.store.upsert_session(
            project_id=_project_id(project),
            host=HOST,
            host_session_id=host_session_id,
            consent_revision=f"r{consent['revision_no']}",
            started_at=meta.get("timestamp") if isinstance(meta.get("timestamp"), str) else None,
        )
        session_id = session_result["data"]["session_id"]
        source_key = f"codex_rollout:{host_session_id}:{path.name}"

        cursor = self._get_cursor(session_id, source_key)
        generation = int(cursor["generation"]) if cursor else 1
        offset = int(cursor["position"].get("offset", 0)) if cursor else 0

        result = self.reader.read(path, offset=offset, session_id=host_session_id, file_label=label)
        rewrites: list[dict[str, Any]] = []
        if result.rewritten:
            generation += 1
            rewrites.append(
                {
                    "file": label,
                    "offset": offset,
                    "size": result.size,
                    "generation": generation,
                }
            )

        messages = list(result.messages)

        # 排除项：命中授权排除模式的工具调用不入账（密钥/依赖/自生成报告）。
        excluded: list[dict[str, Any]] = []
        patterns = consent.get("exclude_patterns") or []
        kept: list[dict[str, Any]] = []
        for message in messages:
            if message["kind"] != "tool_call":
                kept.append(message)
                continue
            hit = matches_exclude_patterns(message.get("text"), patterns)
            if hit is None:
                kept.append(message)
            else:
                excluded.append(
                    {"file": label, "pattern": hit, "source_key": message["source_key"]}
                )
        messages = kept

        # 新绑定会话的默认采集起点是授权保存时间，不回溯全部历史。
        if history_range is not None:
            low, high = history_range
            messages = [
                m for m in messages if low <= (m.get("occurred_at") or "") <= high
            ]
        else:
            floor = consent.get("authorized_at")
            if isinstance(floor, str) and floor:
                messages = [
                    m for m in messages if _after_authorization(m.get("occurred_at"), floor)
                ]

        outcome = self.ingest_messages(
            project,
            session_id,
            source_key,
            messages,
            generation=generation,
            position={"offset": result.next_offset},
        )

        return {
            "file": {
                "file": label,
                "offset_from": offset,
                "offset_to": result.next_offset,
                "size": result.size,
                "generation": generation,
                "truncated": result.truncated,
                "bad_lines": result.bad_lines,
            },
            "session": {
                "host_session_id": host_session_id,
                "thread_source": meta.get("thread_source"),
                "forked_from_id": meta.get("forked_from_id"),
                "messages": len(messages),
            },
            "rewrites": rewrites,
            "truncated": [label] if result.truncated else [],
            "excluded": excluded,
            "written": outcome["written"],
            "redacted": outcome["redacted"],
            "reconcile": result.reconcile,
        }


# ===== 便捷入口 =====

def registered_projects(home: str | None = None) -> list[dict[str, Any]]:
    """从项目注册表读取项目列表（惰性导入，避免无谓的模块级依赖）。"""
    from llmwiki_registry import list_projects

    return list(list_projects(home).get("projects", []))


def open_store(state_root: str | Path) -> WorkbenchStore:
    """打开项目级 workbench 库：<state_root>/workbench/runtime.sqlite3。"""
    db_path = Path(state_root) / WORKBENCH_DIR_NAME / "runtime.sqlite3"
    store = WorkbenchStore(str(db_path))
    store.open()
    return store
