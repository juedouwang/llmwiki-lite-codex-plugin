"""Asynchronous Stop hook for intentional research-result receipts.

The hook is deliberately conservative: it only writes when the assistant emits a
hidden ``llmwiki-research-result`` JSON marker. Ordinary replies, failed work,
and partial progress are ignored. A marker with ``task_id`` submits delivery to
the shared task store (pending user acceptance); a marker without ``task_id``
appends an independent research record.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from llmwiki_registry import list_projects  # noqa: E402
from research_notebook import _file_lock  # noqa: E402
from research_records import list_records, write_record  # noqa: E402

MAX_INPUT_BYTES = 1024 * 1024
MAX_MARKER_BYTES = 256 * 1024
MARKER_RE = re.compile(
    r"<!--\s*llmwiki-research-result\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)
PROJECT_ID = re.compile(r"[a-z0-9-]{8,100}")
TASK_ID = re.compile(r"[a-f0-9]{32}")


def now() -> str:
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


def marker(value: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    if value.get("hook_event_name") != "Stop":
        return None
    if value.get("stop_hook_active") is True:
        return None
    message = value.get("last_assistant_message")
    if not isinstance(message, str) or len(message.encode("utf-8")) > MAX_MARKER_BYTES:
        return None
    match = MARKER_RE.search(message)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version", 1) != 1:
        return None
    return data, raw


def text(value: Any, field: str, *, required: bool = False, limit: int = 20_000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise ValueError(f"{field} invalid")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} required")
    return value


def items(value: Any, field: str, *, limit: int = 100) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{field} invalid")
    result = []
    for item in value:
        result.append(text(item, field, limit=4000))
    return [item for item in result if item]


def resolved(path: str | None) -> Path | None:
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def contains(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def project_context(value: dict[str, Any], data: dict[str, Any]) -> tuple[dict[str, Any], Path, Path] | None:
    try:
        projects = list_projects().get("projects", [])
    except Exception:
        projects = []
    explicit_id = data.get("project_id")
    if explicit_id is not None:
        explicit_id = text(explicit_id, "project_id", limit=120)
        if explicit_id and not PROJECT_ID.fullmatch(explicit_id):
            return None
    chosen = None
    if explicit_id:
        chosen = next((item for item in projects if item.get("id") == explicit_id), None)
        if chosen is None:
            return None
    cwd = resolved(value.get("cwd")) or Path.cwd().resolve(strict=False)
    if chosen is None:
        candidates = []
        for item in projects:
            source = resolved(item.get("source_root"))
            if source and contains(source, cwd):
                candidates.append((len(source.parts), item, source))
        if candidates:
            _, chosen, source = max(candidates, key=lambda item: item[0])
        else:
            source = cwd
            for candidate in (cwd, *cwd.parents):
                if (candidate / ".llmwiki" / "config.json").is_file():
                    source = candidate
                    break
            state = source / ".llmwiki"
            return {"id": "", "source_root": str(source), "state_root": str(state)}, source, state
    source = resolved(chosen.get("source_root"))
    state = resolved(chosen.get("state_root"))
    if source is None or state is None:
        return None
    # Explicit selection supports conversations outside the source folder;
    # implicit selection remains scoped to the current working directory.
    if not explicit_id and not contains(source, cwd):
        return None
    return chosen, source, state


def ledger_path(state: Path) -> Path:
    return state / "hook-research-results.jsonl"


def request_key(value: dict[str, Any], data: dict[str, Any], raw_marker: str) -> str:
    supplied = data.get("request_id")
    if supplied is not None:
        supplied = text(supplied, "request_id", limit=160)
        if supplied:
            return supplied
    session = text(value.get("session_id"), "session_id", limit=240)
    return hashlib.sha256((session + "\n" + raw_marker).encode("utf-8")).hexdigest()


def already_done(path: Path, key: str) -> bool:
    if not path.is_file():
        return False
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-5000:]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("request_id") == key and item.get("status") == "completed":
                return True
    except (OSError, UnicodeError):
        return False
    return False


def ledger_append(path: Path, key: str, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": now(), "request_id": key, "status": "completed", **result}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def append_event(state: Path, data: dict[str, Any], key: str, result: dict[str, Any]) -> None:
    event = {
        "timestamp": now(),
        "kind": "research-result-captured",
        "source": "research-stop-hook",
        "request_id": key,
        "session_id": str(data.get("session_id") or "")[:240],
        "task_id": data.get("task_id") or "",
        "record_id": result.get("record_id", ""),
        "changed_paths": items(data.get("changed_paths"), "changed_paths", limit=100),
    }
    try:
        with (state / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def normalized_record(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": text(data.get("title"), "title", required=True, limit=240),
        "discussion_context": text(data.get("discussion_context"), "discussion_context"),
        "understanding": text(data.get("understanding"), "understanding", required=True),
        "evidence": items(data.get("evidence"), "evidence"),
        "conclusion": text(data.get("conclusion"), "conclusion"),
        "decisions": items(data.get("decisions"), "decisions"),
        "open_questions": items(data.get("open_questions"), "open_questions"),
        "next_steps": items(data.get("next_steps"), "next_steps"),
        "related_files": items(data.get("related_files"), "related_files"),
        "related_pages": items(data.get("related_pages"), "related_pages"),
        "tags": items(data.get("tags"), "tags"),
    }


def handle(value: dict[str, Any]) -> None:
    parsed = marker(value)
    if parsed is None:
        return
    data, raw_marker = parsed
    context = project_context(value, data)
    if context is None:
        return
    project, source, state = context
    key = request_key(value, data, raw_marker)
    task_id = data.get("task_id")
    if task_id is not None:
        task_id = text(task_id, "task_id", limit=80)
        if not TASK_ID.fullmatch(task_id):
            return
    ledger = ledger_path(state)
    lock_project = {"wiki_root": str(state)}
    with _file_lock(lock_project, "research-result-hook"):
        if already_done(ledger, key):
            return
        if task_id:
            from daily_tasks import mutate
            from research_progress import load_summary

            if not project.get("id"):
                return
            summary = text(data.get("summary") or data.get("understanding"), "summary", required=True, limit=20_000)
            evidence = items(data.get("evidence"), "evidence")
            remaining = text(data.get("remaining"), "remaining", limit=4000)
            paths = items(data.get("changed_paths"), "changed_paths", limit=100)
            session_id = text(value.get("session_id"), "session_id", limit=240)
            # Receipt content is written by the shared task engine; the hook does not
            # create a second independent record for an existing task.
            details = [summary]
            if evidence:
                details.append("验证及依据：\n" + "\n".join("- " + item for item in evidence))
            if paths:
                details.append("变更路径：\n" + "\n".join("- " + item for item in paths))
            if remaining:
                details.append("未解决：" + remaining)
            if session_id:
                details.append("会话：" + session_id)
            summary = "\n\n".join(details)
            current = load_summary(project)
            result = mutate(
                None,
                {
                    "project_id": project["id"],
                    "action": "submit",
                    "revision": current["revision"],
                    "id": task_id,
                    "summary": summary,
                },
                actor="agent",
            )
            if not result.get("ok"):
                return
            task = result.get("task") or {}
            record_id = task.get("completion_record_id", "")
            outcome = {"mode": "task-delivery", "task_id": task_id, "record_id": record_id}
        else:
            record = normalized_record(data)
            hook_tag = "hook-request-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
            if hook_tag not in record["tags"]:
                record["tags"].append(hook_tag)
            existing = list_records(
                str(source), state_root=str(state), tag=hook_tag, max_records=1
            )["records"]
            if existing:
                outcome = {
                    "mode": "independent-research-record",
                    "record_id": existing[0]["id"],
                }
            else:
                result = write_record(
                    str(source),
                    state_root=str(state),
                    project_id=str(project.get("id") or ""),
                    **record,
                )
                outcome = {
                    "mode": "independent-research-record",
                    "record_id": result["record"]["id"],
                }
        ledger_append(ledger, key, outcome)
        append_event(state, {**data, "session_id": value.get("session_id")}, key, outcome)


def main() -> int:
    try:
        value = read_payload()
        if value is not None:
            handle(value)
    except Exception:
        # A hook must never make the host stop/final response fail.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


