"""Smart-capture hook: recording rules at session start, recording checkpoints at stop.

SessionStart injects the project's recording rules plus a short recall of recent
records and next steps, so the host model can decide what is worth keeping
without being asked, and can pick up where the last session stopped.

Stop is a safety net. It counts activity since the last recording checkpoint
(tool calls, user turns, reply volume) and notices record-tool calls in the
transcript. Once enough unrecorded work has accumulated it asks the host model,
once, to review that work against the rules. Checks that end without a record
back off, so routine sessions are not nagged. Python never judges what is worth
recording; that stays with the host model.

The hook is fail-open: any error produces no output and exit code 0.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from llmwiki_registry import CAPTURE_MODES as MODES  # noqa: E402
from llmwiki_registry import list_projects, load_settings  # noqa: E402

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 1024 * 1024
MAX_SCAN_BYTES = 4 * 1024 * 1024
MAX_CONTEXT_CHARS = 6000
RULES_TEMPLATE = PLUGIN_ROOT / "templates" / "capture-rules.md"
MODE_ENV = "LLMWIKI_CAPTURE"

# A checkpoint is due after this much unrecorded work. Each check that ends
# without a record doubles the thresholds and the spacing (up to MAX_BACKOFF).
MIN_TOOL_CALLS = 6
MIN_TURNS = 3
MIN_REPLY_CHARS = 2500
# Hosts do not promise a stable transcript format. When new lines are present
# but none is recognisable, transcript growth stands in for activity.
MIN_UNKNOWN_BYTES = 256 * 1024
NUDGE_SPACING_SECONDS = 10 * 60
MAX_BACKOFF = 3
SESSION_TTL_SECONDS = 14 * 24 * 3600

RECALL_SCAN = 40
RECALL_RECORDS = 6
RECALL_STEPS = 5
KINDS = ("决策", "结果", "发现", "踩坑", "约定", "问题")
RECEIPT_TAGS = ("task-receipt", "hook-request-")
RECORD_TOOL = "llmwiki_record_write"
RESULT_MARKER = "<!-- llmwiki-research-result"
# Turns started by a scheduled automation, not by the user.
AUTOMATION_PREFIX = "<heartbeat"
CALL_TYPES = {
    "function_call",
    "custom_tool_call",
    "local_shell_call",
    "web_search_call",
    "mcp_tool_call",
    "tool_search_call",
}
NEXT_STEPS_RE = re.compile(r"(?ms)^#{2,3}\s+下一步行动\s*\n(.*?)(?=^#{1,3}\s|\Z)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_payload() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_INPUT_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def capture_mode(home: str | None = None) -> str:
    override = os.environ.get(MODE_ENV, "").strip().lower()
    if override in MODES:
        return override
    try:
        value = load_settings(home).get("capture_mode")
    except Exception:
        value = None
    return value if value in MODES else "auto"


def _resolved(raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _contains(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_project(cwd: Any, home: str | None = None) -> dict[str, Any] | None:
    """The registered project whose source or Wiki directory holds ``cwd``."""
    path = _resolved(cwd) or Path.cwd().resolve(strict=False)
    try:
        projects = list_projects(home).get("projects", [])
    except Exception:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for item in projects:
        for key in ("source_root", "wiki_root"):
            root = _resolved(item.get(key))
            if root and _contains(root, path) and (best is None or len(root.parts) > best[0]):
                best = (len(root.parts), item)
    return best[1] if best else None


# ----- transcript activity ------------------------------------------------


@dataclass
class Activity:
    tool_calls: int = 0
    turns: int = 0
    reply_chars: int = 0
    recorded: bool = False
    automatic: bool = False  # the latest user message was a scheduled trigger
    scanned: int = 0  # bytes of complete lines read
    end: int = 0

    def unrecognised(self) -> bool:
        return self.scanned > 0 and not (self.tool_calls or self.turns or self.reply_chars)


def _record_call(name: Any, activity: Activity) -> None:
    if isinstance(name, str) and name.endswith(RECORD_TOOL):
        activity.recorded = True


def _user_message(texts: list[str], activity: Activity) -> None:
    activity.automatic = any(text.lstrip().startswith(AUTOMATION_PREFIX) for text in texts)


def _count(entry: dict[str, Any], activity: Activity) -> None:
    kind = entry.get("type")
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if kind == "assistant":
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                activity.tool_calls += 1
                _record_call(block.get("name"), activity)
            elif block.get("type") == "text":
                activity.reply_chars += len(str(block.get("text") or ""))
        return
    if kind == "user":
        if entry.get("isMeta") or entry.get("isSidechain"):
            return
        if isinstance(content, str) and content.strip():
            activity.turns += 1
            _user_message([content], activity)
        elif isinstance(content, list):
            types = {block.get("type") for block in content if isinstance(block, dict)}
            if "text" in types and "tool_result" not in types:
                activity.turns += 1
                _user_message([str(block.get("text") or "") for block in content if isinstance(block, dict)], activity)
        return
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return
    ptype = payload.get("type")
    if kind == "response_item":
        blocks = [block for block in payload.get("content") or [] if isinstance(block, dict)]
        if ptype in CALL_TYPES:
            activity.tool_calls += 1
            _record_call(payload.get("name"), activity)
        elif ptype == "message" and payload.get("role") == "assistant":
            for block in blocks:
                if block.get("type") in ("output_text", "text"):
                    activity.reply_chars += len(str(block.get("text") or ""))
        elif ptype == "message" and payload.get("role") == "user":
            _user_message([str(block.get("text") or "") for block in blocks], activity)
    elif kind == "event_msg" and ptype == "task_started":
        activity.turns += 1


def scan_transcript(path: Any, start: int) -> Activity:
    """Count activity in complete JSONL lines after byte offset ``start``.

    Only the last MAX_SCAN_BYTES are read. A transcript shorter than ``start``
    was rewritten (for example compacted), so its tail is scanned from scratch.
    """
    start = max(int(start or 0), 0)
    activity = Activity(end=start)
    if not isinstance(path, str) or not path.strip():
        return activity
    try:
        target = Path(path)
        size = target.stat().st_size
        if start > size:
            start = 0
        begin = max(start, size - MAX_SCAN_BYTES)
        with target.open("rb") as handle:
            handle.seek(begin)
            data = handle.read(size - begin)
    except OSError:
        return activity
    if begin > start:
        cut = data.find(b"\n")
        if cut < 0:
            return activity
        begin += cut + 1
        data = data[cut + 1 :]
    last = data.rfind(b"\n")
    if last < 0:
        activity.end = begin
        return activity
    activity.end = begin + last + 1
    activity.scanned = last + 1
    for raw in data[: last + 1].splitlines():
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(entry, dict):
            _count(entry, activity)
    return activity


def transcript_size(path: Any) -> int:
    try:
        return Path(path).stat().st_size if isinstance(path, str) and path.strip() else 0
    except OSError:
        return 0


# ----- per-session checkpoint state ---------------------------------------


def _sessions_dir(project: dict[str, Any]) -> Path:
    return Path(str(project["state_root"])) / "capture" / "sessions"


def _state_path(project: dict[str, Any], session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return _sessions_dir(project) / f"{digest}.json"


def _new_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "offset": 0,
        "nudged": False,
        "nudges": 0,
        "backoff": 0,
        "records": 0,
        "last_nudge_at": None,
        "updated_at": now_iso(),
    }


def load_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return None
    state = _new_state()
    state.update(value)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def prune_states(project: dict[str, Any]) -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    try:
        for item in _sessions_dir(project).glob("*.json"):
            if item.stat().st_mtime < cutoff:
                item.unlink()
    except OSError:
        pass


# ----- SessionStart: rules and recall ------------------------------------


def _fill(template: str, project: dict[str, Any]) -> str:
    values = {
        "project_name": str(project.get("name") or project.get("id") or ""),
        "project_id": str(project.get("id") or ""),
        "source_root": str(project.get("source_root") or ""),
        "wiki_root": str(project.get("wiki_root") or ""),
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def rules_text(project: dict[str, Any]) -> str:
    try:
        template = RULES_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        template = (
            "【LLM Wiki 智能记录】本会话属于已注册科研项目「{{project_name}}」。"
            "一件事告一段落时，主动把值得长期保留的决策、结果、发现、踩坑、约定、问题或下一步"
            "用 llmwiki_record_write（project_root=\"{{source_root}}\"，project_id=\"{{project_id}}\"）记录，"
            "记录后在回复末尾写一行「📝 已记录：<标题>」。"
        )
    return _fill(template.strip(), project)


def _oneline(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _snippet(content: str) -> str:
    text = re.sub(r"(?s)\A---\n.*?\n---\n", "", content)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"(?m)^#{1,6}\s+.*$", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _next_steps(content: str) -> list[str]:
    match = NEXT_STEPS_RE.search(content)
    if not match:
        return []
    return [item.strip() for item in re.findall(r"(?m)^\s*-\s+(.+)$", match.group(1)) if item.strip()]


def recall_text(project: dict[str, Any]) -> str:
    from research_records import list_records

    try:
        listing = list_records(
            str(project["source_root"]),
            state_root=str(project["state_root"]),
            max_records=RECALL_SCAN,
            include_content=True,
        )
    except Exception:
        return ""
    lines: list[str] = []
    steps: list[str] = []
    for record in listing.get("records", []):
        tags = [str(tag) for tag in record.get("tags") or []]
        if any(tag.startswith(RECEIPT_TAGS) for tag in tags):
            continue
        content = str(record.get("content") or "")
        summary = str(record.get("summary") or "") or _snippet(content)
        if len(summary) < 8:
            continue
        if len(lines) < RECALL_RECORDS:
            # Legacy single-record pages may carry no date; fall back to the file time.
            day = str(record.get("date") or record.get("updated_at") or "")[5:10]
            kind = next((tag for tag in tags if tag in KINDS), "")
            label = f"〔{kind}〕" if kind else ""
            title = _oneline(str(record.get("title") or ""), 40)
            prefix = f"{day}｜" if day else ""
            lines.append(f"- {prefix}{title}{label} {_oneline(summary, 90)}")
        if len(steps) < RECALL_STEPS:
            for step in _next_steps(content):
                step = _oneline(step, 80)
                if step not in steps and len(steps) < RECALL_STEPS:
                    steps.append(step)
        if len(lines) >= RECALL_RECORDS and len(steps) >= RECALL_STEPS:
            break
    parts = []
    if lines:
        parts.append("最近记录（用于接续工作和避免重复记录，详情用 llmwiki_record_list / llmwiki_record_read 查）：\n" + "\n".join(lines))
    else:
        parts.append("最近记录：暂无可用于接续的科研记录，从这次对话开始记。")
    if steps:
        parts.append("最近记录里的下一步（可能已完成，接续前先核对）：\n" + "\n".join(f"- {step}" for step in steps))
    wiki_root = _resolved(project.get("wiki_root"))
    if wiki_root is not None:
        knowledge = wiki_root / "knowledge"
        try:
            pages = sorted(path.name for path in knowledge.glob("*.md"))[:8]
        except OSError:
            pages = []
        if pages:
            parts.append(
                f"知识库（{knowledge}）：" + "、".join(pages) + "。回答项目问题前可先读相关页面。"
            )
    return "\n\n".join(parts)


def session_context(project: dict[str, Any]) -> str:
    text = rules_text(project) + "\n\n" + recall_text(project)
    return text[:MAX_CONTEXT_CHARS].rstrip()


def session_start(value: dict[str, Any], home: str | None = None) -> dict[str, Any] | None:
    if capture_mode(home) == "off":
        return None
    project = resolve_project(value.get("cwd"), home)
    if project is None:
        return None
    session_id = str(value.get("session_id") or "")
    if session_id:
        prune_states(project)
        path = _state_path(project, session_id)
        state = load_state(path)
        # Compaction keeps the conversation going; any other start begins a
        # fresh checkpoint so earlier work is not counted twice.
        if state is None or value.get("source") != "compact":
            state = state or _new_state()
            state.update(offset=transcript_size(value.get("transcript_path")), nudged=False)
            save_state(path, state)
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": session_context(project),
        }
    }


# ----- Stop: recording checkpoint -----------------------------------------


def awaiting_user(message: str) -> bool:
    """The reply ends by asking the user something; check again after the answer."""
    tail = message.rstrip().rstrip("*_`>） )")
    return tail.endswith(("?", "？"))


def checkpoint_due(activity: Activity, message: str, state: dict[str, Any], now: float) -> bool:
    factor = 2 ** min(int(state.get("backoff") or 0), MAX_BACKOFF)
    busy = activity.tool_calls >= MIN_TOOL_CALLS * factor
    replies = max(activity.reply_chars, len(message))
    talked = activity.turns >= MIN_TURNS * factor and replies >= MIN_REPLY_CHARS * factor
    grown = activity.unrecognised() and activity.scanned >= MIN_UNKNOWN_BYTES * factor
    if not (busy or talked or grown):
        return False
    last = state.get("last_nudge_at")
    if state.get("nudges") and isinstance(last, str):
        try:
            elapsed = now - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
        except ValueError:
            elapsed = NUDGE_SPACING_SECONDS * factor
        if elapsed < NUDGE_SPACING_SECONDS * factor:
            return False
    return True


def reminder(project: dict[str, Any], activity: Activity) -> str:
    amount = (
        "" if activity.unrecognised()
        else f"（约 {activity.tool_calls} 次工具调用、{activity.turns} 轮对话）"
    )
    return (
        f"【LLM Wiki 记录检查】上次记录之后又完成了一段工作{amount}，还没有记录。请按记录规则回顾这段工作：\n"
        "- 有值得长期保留的决策、实验结果、新发现、踩坑、项目约定、未决问题或下一步：调用 llmwiki_record_write"
        f"（project_root=\"{project.get('source_root')}\"，project_id=\"{project.get('id')}\"）写入，"
        "同一主题合并为一条，最多两条；写完只回复一行「📝 已记录：<标题>」。\n"
        "- 没有：只回复「（本段无需记录）」。\n"
        "不要重复已经记录过的内容，不要向用户征求是否记录，也不要继续做其他工作。"
    )


def stop(value: dict[str, Any], home: str | None = None, now: float | None = None) -> dict[str, Any] | None:
    if capture_mode(home) != "auto":
        return None
    session_id = str(value.get("session_id") or "")
    project = resolve_project(value.get("cwd"), home) if session_id else None
    if project is None:
        return None
    now = time.time() if now is None else now
    path = _state_path(project, session_id)
    state = load_state(path)
    transcript = value.get("transcript_path")
    if state is None:
        # No SessionStart seen (older session or other host): look back one window.
        state = _new_state()
        state["offset"] = max(transcript_size(transcript) - MAX_SCAN_BYTES, 0)
    message = value.get("last_assistant_message")
    message = message if isinstance(message, str) else ""
    activity = scan_transcript(transcript, int(state.get("offset") or 0))
    recorded = activity.recorded or RESULT_MARKER in message
    if value.get("stop_hook_active") is True:
        # Never block twice in a row. After our own check, close it out.
        if state.get("nudged"):
            backoff = int(state.get("backoff") or 0)
            state["backoff"] = 0 if recorded else min(backoff + 1, MAX_BACKOFF)
            state["records"] = int(state.get("records") or 0) + (1 if recorded else 0)
            state.update(offset=activity.end, nudged=False)
            save_state(path, state)
        return None
    if recorded:
        state["records"] = int(state.get("records") or 0) + 1
        state.update(offset=activity.end, nudged=False)
        save_state(path, state)
        return None
    if activity.automatic:
        # Scheduled maintenance is not the user's research work: skip past it.
        state.update(offset=activity.end, nudged=False)
        save_state(path, state)
        return None
    if awaiting_user(message) or not checkpoint_due(activity, message, state, now):
        save_state(path, state)
        return None
    state.update(
        nudged=True,
        nudges=int(state.get("nudges") or 0) + 1,
        last_nudge_at=datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    save_state(path, state)
    return {"decision": "block", "reason": reminder(project, activity)}


def handle(value: dict[str, Any]) -> dict[str, Any] | None:
    event = value.get("hook_event_name")
    if event == "SessionStart":
        return session_start(value)
    if event == "Stop":
        return stop(value)
    return None


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        # Show what a session started in the given directory would receive.
        project = resolve_project(sys.argv[2])
        text = session_context(project) if project else "该目录不属于任何已注册项目，不会注入记录规则。"
        sys.stdout.buffer.write((f"[capture_mode={capture_mode()}]\n" + text + "\n").encode("utf-8"))
        return 0
    try:
        value = read_payload()
        output = handle(value) if value is not None else None
        if output:
            # ASCII-only JSON survives any console code page.
            sys.stdout.write(json.dumps(output, ensure_ascii=True))
            sys.stdout.flush()
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
