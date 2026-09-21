"""Small, local research timeline: explicit tasks and durable hand-off context.

No dates, completion percentages or scientific outcomes are inferred from files.
Automatic summaries are stored separately and never wait on a model.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from llmwiki_core import LLMWikiError
from research_notebook import NotebookConflict, _atomic, _file_lock, safe_file
from research_records import _split_record_id, list_records, read_record

LOCK = threading.RLock()
MAX_BYTES = 8 * 1024 * 1024
MAX_TASKS = 500
STATUSES = {"planned", "active", "blocked", "done"}
RESUME_STATUSES = {"active", "blocked"}
FIELDS = ("title", "status", "start", "end", "checkpoint", "next_step")
TASK_FIELDS = ("description", "priority", "ddl")
PRIORITIES = {"high": 0, "medium": 1, "low": 2}
MAX_DESCRIPTION = 100_000
CONTEXT_FIELDS = ("checkpoint", "next_step")
LINK_FIELD = "record_id"
MODES = {"manual", "auto"}
TASK_ID = re.compile(r"[a-f0-9]{32}")
SHA = re.compile(r"[a-f0-9]{64}")
MCP_LIST_LIMIT = 20
MCP_PREVIEW = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise LLMWikiError("任务内容格式无效或过长。")
    return value.strip()


def _link(value) -> str:
    """Normalise a task's link to one 科研记录.

    The link is a reference, never a copy: the note keeps owning its content, and
    a note that is later deleted only leaves a stale id behind. Only the id format
    is checked here, because this runs on read as well; whether the note currently
    exists is checked on write.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LLMWikiError("关联笔记格式无效。")
    value = value.strip()
    if not value:
        return ""
    if len(value) > 240 or "\0" in value:
        raise LLMWikiError("关联笔记格式无效。")
    path, fragment = _split_record_id(value)
    return f"{path}#{fragment}" if fragment else path


def _stamp(value, *, optional: bool = False) -> str | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError()
    if not isinstance(value, str):
        raise ValueError()
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def validate(value: dict) -> dict:
    if not isinstance(value, dict):
        raise LLMWikiError("任务必须是对象。")
    task = {key: _text(value.get(key, "planned" if key == "status" else ""), 240 if key == "title" else 4000) for key in FIELDS}
    # Missing new keys mean a legacy task, not an instruction to erase its context.
    for key in TASK_FIELDS:
        if key in value:
            checked = _text(value[key], MAX_DESCRIPTION if key == "description" else 20)
            task[key] = value[key] if key == "description" else checked
    if "priority" in task and task["priority"] not in PRIORITIES:
        raise LLMWikiError("优先级须为 high、medium 或 low。")
    task[LINK_FIELD] = _link(value.get(LINK_FIELD, ""))
    if not task["title"]:
        raise LLMWikiError("请填写任务名称。")
    if task["status"] not in STATUSES:
        raise LLMWikiError("任务状态无效。")
    for key in ("start", "end", "ddl"):
        if task.get(key):
            try:
                if date.fromisoformat(task[key]).isoformat() != task[key]:
                    raise ValueError()
            except ValueError as exc:
                raise LLMWikiError("日期须为 YYYY-MM-DD。") from exc
    if task["start"] and task["end"] and task["end"] < task["start"]:
        raise LLMWikiError("结束日期不能早于开始日期。")
    return task


def context_mode(task: dict) -> dict[str, str]:
    raw = task.get("context_mode")
    modes: dict[str, str] = {}
    for field in CONTEXT_FIELDS:
        current = raw.get(field) if isinstance(raw, dict) else None
        if current in MODES:
            modes[field] = current
        else:
            modes[field] = "manual" if str(task.get(field) or "") else "auto"
    return modes


def _completed(task: dict) -> str | None:
    value = task.get("completed_at", None)
    if value in (None, ""):
        return None
    return _stamp(value, optional=False)


def _read(project: dict) -> tuple[Path, dict, str]:
    path = safe_file(Path(project["wiki_root"]), ".research-progress/tasks.json")
    if not path.exists():
        return path, {"version": 1, "tasks": []}, ""
    if path.stat().st_size > MAX_BYTES:
        raise LLMWikiError("任务文件过大，已停止读取，不会覆盖原文件。")
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("tasks"), list) or len(data["tasks"]) > MAX_TASKS:
            raise ValueError()
        seen = set()
        for task in data["tasks"]:
            validate(task)
            if not TASK_ID.fullmatch(task["id"]) or task["id"] in seen:
                raise ValueError()
            seen.add(task["id"])
            if not isinstance(task.get("history"), list) or len(task["history"]) > 100:
                raise ValueError()
            for key in ("created_at", "updated_at"):
                _stamp(task[key])
            if "completed_at" in task:
                _stamp(task.get("completed_at"), optional=True)
            if not isinstance(task.get("record_id"), str):
                raise ValueError()
            if task.get("context_mode") not in (None, ) and not isinstance(task.get("context_mode"), dict):
                raise ValueError()
            for event in task["history"]:
                validate(event)
                _stamp(event["at"])
                if "completed_at" in event:
                    _stamp(event.get("completed_at"), optional=True)
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError) as exc:
        raise LLMWikiError("任务文件损坏或版本不兼容，已停止写入，请检查原文件。") from exc
    return path, data, hashlib.sha256(raw).hexdigest()


def _empty_contexts() -> dict:
    return {"version": 1, "items": {}}


def _context_item(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError()
    item = {
        "checkpoint": _text(value.get("checkpoint", ""), 4000),
        "next_step": _text(value.get("next_step", ""), 4000),
        "source_record_id": _link(value.get("source_record_id", "")),
        "generated_at": _stamp(value.get("generated_at")),
        "revision": value.get("revision"),
    }
    if not isinstance(item["revision"], str) or not SHA.fullmatch(item["revision"]):
        raise ValueError()
    return item


def _context_revision(item: dict) -> str:
    payload = {
        "checkpoint": item["checkpoint"],
        "next_step": item["next_step"],
        "source_record_id": item["source_record_id"],
        "generated_at": item["generated_at"],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _read_contexts(project: dict) -> tuple[Path, dict, str]:
    path = safe_file(Path(project["wiki_root"]), ".research-progress/contexts.json")
    if not path.exists():
        return path, _empty_contexts(), ""
    if path.stat().st_size > MAX_BYTES:
        raise LLMWikiError("自动整理文件过大，已停止读取，不会覆盖原文件。")
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), dict):
            raise ValueError()
        items = {}
        for key, value in data["items"].items():
            if not isinstance(key, str) or not TASK_ID.fullmatch(key):
                raise ValueError()
            items[key] = _context_item(value)
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError, json.JSONDecodeError) as exc:
        raise LLMWikiError("自动整理文件损坏或版本不兼容，已停止写入，请检查原文件。") from exc
    return path, {"version": 1, "items": items}, hashlib.sha256(raw).hexdigest()


def _load_contexts_safe(project: dict) -> tuple[dict, str, str]:
    try:
        _, data, revision = _read_contexts(project)
        return data, revision, ""
    except FileNotFoundError:
        return _empty_contexts(), "", ""
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError, json.JSONDecodeError, LLMWikiError, OSError):
        return _empty_contexts(), "", "自动整理文件无法读取，人工任务仍可使用；未改写该文件。"


def _all_records(project: dict) -> list[dict]:
    return list_records(
        project["source_root"],
        state_root=project["state_root"],
        max_records=500,
        include_content=True,
    ).get("records", [])


def _legacy_candidates(records: list[dict]) -> list[dict]:
    """Legacy bullets are suggestions, never automatically new/finished tasks."""
    items = []
    for record in records:
        text = str(record.get("content", "")).replace("\r\n", "\n")
        bullets = []
        for section in ("尚未解决的问题", "下一步行动"):
            match = re.search(rf"(?ms)^###\s+{section}\s*\n(.*?)(?=^###\s|\Z)", text)
            if match:
                bullets.extend(m[1].strip() for line in match[1].splitlines() if (m := re.match(r"^\s*[-+*]\s+(.+?)\s*$", line)))
        occurrences: dict[str, int] = {}
        for index, title in enumerate(bullets):
            occurrences[title] = occurrences.get(title, 0) + 1
            identity = f'{record["id"]}\0{title}\0{occurrences[title]}'
            items.append({"id": hashlib.sha256(identity.encode()).hexdigest()[:32], "title": title[:240],
                          "record_id": record["id"], "legacy_key": f'{record["id"]}::{index}'})
            if len(items) >= MAX_TASKS:
                return items
    return items


def candidates(project: dict) -> list[dict]:
    return _legacy_candidates(_all_records(project))


def _link_targets(records: list[dict]) -> list[dict]:
    """What a task may link to: manual notes and daily entries, newest path first."""
    targets = [
        {"id": str(record["id"]), "title": str(record.get("title") or record["id"])[:240]}
        for record in records
        if record.get("id")
    ]
    return sorted(targets, key=lambda item: item["id"], reverse=True)


def _effective(task: dict, auto: dict | None, field: str) -> str:
    if context_mode(task)[field] == "manual":
        return str(task.get(field) or "")
    return str((auto or {}).get(field) or "")


def task_description(task: dict) -> str:
    """Read-only migration view; an explicit empty description stays empty."""
    if "description" in task:
        return task["description"]
    context = task.get("effective_context") or task
    checkpoint = str(context.get("checkpoint") or "")
    next_step = str(context.get("next_step") or "")
    return "\n\n".join(part for part in (checkpoint, "下一步：" + next_step if next_step else "") if part)


def task_ddl(task: dict) -> str:
    return task["ddl"] if "ddl" in task else (task.get("end") or task.get("start") or "")


def sort_todo(tasks: list[dict]) -> list[dict]:
    return sorted((task for task in tasks if task.get("status") != "done"), key=lambda task: (
        PRIORITIES.get(task.get("priority"), 1), task_ddl(task) or "9999-12-31",
        task.get("created_at", ""), task["id"],
    ))


def public_task(task: dict, auto: dict | None = None) -> dict:
    item = {key: task.get(key, "") for key in FIELDS}
    item[LINK_FIELD] = task.get(LINK_FIELD, "")
    item["id"] = task["id"]
    item["created_at"] = task["created_at"]
    item["updated_at"] = task["updated_at"]
    item["history"] = list(task.get("history") or [])
    try:
        item["completed_at"] = _completed(task)
    except (TypeError, ValueError):
        item["completed_at"] = None
    item["context_mode"] = context_mode(task)
    auto_item = auto or {}
    item["context_revision"] = str(auto_item.get("revision") or "")
    item["effective_context"] = {field: _effective(task, auto_item, field) for field in CONTEXT_FIELDS}
    item["auto_context"] = {
        "checkpoint": auto_item.get("checkpoint", ""),
        "next_step": auto_item.get("next_step", ""),
        "source_record_id": auto_item.get("source_record_id", ""),
        "generated_at": auto_item.get("generated_at", ""),
    } if item["context_revision"] else None
    item["resume_eligible"] = task.get("status") != "done" and (
        task.get("status") in RESUME_STATUSES or any(key in task for key in TASK_FIELDS)
    )
    item["description_source"] = "manual" if "description" in task else "legacy"
    item["description"] = task_description({**item, **({"description": task["description"]} if "description" in task else {})})
    item["priority"] = task.get("priority", "medium")
    item["ddl"] = task_ddl(task)
    # Opaque legacy assistant metadata is not reinterpreted, flattened or discarded.
    if "assistant_context" in task:
        item["assistant_context"] = task["assistant_context"]
    return item


def _attach(tasks: list[dict], contexts: dict) -> list[dict]:
    items = contexts.get("items") if isinstance(contexts, dict) else {}
    return [public_task(task, items.get(task["id"])) for task in tasks]


def sort_resume(tasks: list[dict]) -> list[dict]:
    # Old planned ideas remain excluded. Explicit edits in the new task model
    # count as recent work without asking users to pick an in-progress status.
    active = [task for task in tasks if task.get("status") != "done" and (
        task.get("status") in RESUME_STATUSES or task.get("resume_eligible")
        or any(key in task for key in TASK_FIELDS) and "resume_eligible" not in task
    )]
    active.sort(key=lambda task: task.get("id") or "")
    active.sort(key=lambda task: task.get("updated_at") or "", reverse=True)
    active.sort(key=lambda task: PRIORITIES.get(task.get("priority"), 1))
    return active


def sort_done(tasks: list[dict]) -> list[dict]:
    done = [task for task in tasks if task.get("status") == "done"]
    known = [task for task in done if task.get("completed_at")]
    unknown = [task for task in done if not task.get("completed_at")]
    known.sort(key=lambda task: task.get("id") or "")
    known.sort(key=lambda task: task.get("completed_at") or "", reverse=True)
    unknown.sort(key=lambda task: task.get("id") or "")
    return known + unknown


def load_summary(project: dict) -> dict:
    with LOCK:
        _, data, revision = _read(project)
        contexts, _, warning = _load_contexts_safe(project)
        payload = {"ok": True, "revision": revision, "tasks": _attach(data["tasks"], contexts)}
        if warning:
            payload["warning"] = warning
        return payload


def load_records(project: dict) -> dict:
    with LOCK:
        _, data, revision = _read(project)
        ids = {task["id"] for task in data["tasks"]}
        records = _all_records(project)
        pending = [item for item in _legacy_candidates(records) if item["id"] not in ids]
        return {"ok": True, "revision": revision, "candidates": pending, "records": _link_targets(records)}


def load(project: dict, view: str | None = None) -> dict:
    if view == "summary":
        return load_summary(project)
    if view == "records":
        return load_records(project)
    with LOCK:
        _, data, revision = _read(project)
        ids = {task["id"] for task in data["tasks"]}
        records = _all_records(project)
        pending = [item for item in _legacy_candidates(records) if item["id"] not in ids]
        contexts, _, warning = _load_contexts_safe(project)
        payload = {
            "ok": True,
            "revision": revision,
            "tasks": _attach(data["tasks"], contexts),
            "candidates": pending,
            "records": _link_targets(records),
        }
        if warning:
            payload["warning"] = warning
        return payload


def _event(task: dict, now: str) -> None:
    task["updated_at"] = now
    snapshot = {key: task.get(key, "") for key in FIELDS + (LINK_FIELD,)}
    snapshot.update({key: task[key] for key in TASK_FIELDS if key in task})
    snapshot["completed_at"] = task.get("completed_at")
    snapshot["context_mode"] = dict(task.get("context_mode") or context_mode(task))
    task.setdefault("history", []).append({"at": now, **snapshot})
    task["history"] = task["history"][:1] + task["history"][-99:] if len(task["history"]) > 100 else task["history"]


def _require_record(project: dict, record_id: str) -> None:
    """Unlinking is always allowed; linking must point at a note that exists.

    Format is already checked by _link, which runs on read too. Deleting the note
    afterwards leaves a stale id behind on purpose: the task keeps its history and
    the UI reports the missing target instead of silently dropping the link.
    """
    if not record_id:
        return
    read_record(project["source_root"], record_id, state_root=project["state_root"])


def _apply_completion(task: dict, previous: str, new_status: str, now: str) -> None:
    if previous != "done" and new_status == "done":
        task["resume_status"] = previous if previous in STATUSES - {"done"} else "planned"
        task["completed_at"] = now
    elif new_status != "done":
        task["completed_at"] = None


def _apply_modes(task: dict, fields: dict, incoming: dict) -> dict[str, str]:
    modes = context_mode(task)
    raw = incoming.get("context_mode") if isinstance(incoming, dict) else None
    for field in CONTEXT_FIELDS:
        if isinstance(raw, dict) and raw.get(field) in MODES:
            modes[field] = raw[field]
        elif fields.get(field, "") != task.get(field, ""):
            modes[field] = "manual"
    return modes


def update(project: dict, payload: dict) -> dict:
    # Shared cross-process lock: the installed and development web servers may
    # legitimately access the same Wiki. Revision checks must be inside it.
    with LOCK, _file_lock(project, "research-progress"):
        path, data, revision = _read(project)
        if payload.get("revision") != revision:
            raise NotebookConflict("科研进度已被另一页面修改。请先重新载入；当前填写内容不会被覆盖。")
        action = payload.get("action")
        now = _now()
        if action == "create":
            incoming = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            fields = validate(incoming)
            _require_record(project, fields[LINK_FIELD])
            modes = {}
            raw_modes = incoming.get("context_mode") if isinstance(incoming.get("context_mode"), dict) else {}
            for field in CONTEXT_FIELDS:
                modes[field] = raw_modes[field] if raw_modes.get(field) in MODES else ("manual" if fields.get(field) else "auto")
            task = {**fields, "id": uuid4().hex, "created_at": now, "context_mode": modes,
                    "completed_at": now if fields["status"] == "done" else None}
            _event(task, now)
            data["tasks"].append(task)
        elif action in {"update", "complete", "restore", "delete"}:
            task = next((item for item in data["tasks"] if item["id"] == payload.get("id")), None)
            if task is None:
                raise LLMWikiError("未找到任务。")
            if action == "delete":
                data["tasks"].remove(task)
            incoming = payload.get("task") if isinstance(payload.get("task"), dict) else {}
            if action == "complete":
                incoming = {"status": "done"}
            elif action == "restore":
                incoming = {"status": task.get("resume_status", "planned")} if task["status"] == "done" else {}
            elif action == "delete":
                incoming = {}
            # PATCH semantics keep old clients from deleting new fields (and vice versa).
            fields = validate({**task, **incoming})
            # Untouched legacy strings/metadata stay byte-for-byte meaningful.
            fields.update({key: task[key] for key in fields if key in task and key not in incoming})
            if fields[LINK_FIELD] != task.get(LINK_FIELD, ""):
                _require_record(project, fields[LINK_FIELD])
            modes = _apply_modes(task, fields, incoming)
            next_task = dict(task)
            previous_status = task.get("status")
            next_task.update(fields)
            next_task["context_mode"] = modes
            _apply_completion(next_task, previous_status, fields["status"], now)
            changed = any(task.get(key, "") != next_task.get(key, "") for key in FIELDS + TASK_FIELDS + (LINK_FIELD,))
            changed = changed or any((key in task) != (key in next_task) for key in TASK_FIELDS)
            changed = changed or context_mode(task) != modes or task.get("completed_at") != next_task.get("completed_at")
            if changed:
                task.update(fields)
                task["context_mode"] = modes
                task["completed_at"] = next_task.get("completed_at")
                if "resume_status" in next_task:
                    task["resume_status"] = next_task["resume_status"]
                _event(task, now)
        elif action == "import":
            selected = payload.get("ids", [])
            completed = payload.get("completed", [])
            if not isinstance(selected, list) or not isinstance(completed, list) or any(not isinstance(x, str) for x in selected + completed):
                raise LLMWikiError("导入列表无效。")
            pending = {item["id"]: item for item in candidates(project)}
            known = {item["id"] for item in data["tasks"]}
            for cid in dict.fromkeys(selected):
                if cid in known:
                    continue
                if cid not in pending:
                    raise LLMWikiError("旧待办已发生变化，请重新载入后导入。")
                item = pending[cid]
                status = "done" if cid in completed else "planned"
                task = {**validate({"title": item["title"], "status": status}),
                        "id": cid, "record_id": item["record_id"], "created_at": now,
                        "context_mode": {"checkpoint": "auto", "next_step": "auto"},
                        "completed_at": None}
                _event(task, now)
                data["tasks"].append(task)
        else:
            raise LLMWikiError("未知的任务操作。")
        if len(data["tasks"]) > MAX_TASKS:
            raise LLMWikiError("任务数量已达 500 项上限。")
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        if len(raw) > MAX_BYTES:
            raise LLMWikiError("任务记录已达容量上限，不会覆盖原文件。")
        _atomic(path, raw)
        contexts, _, warning = _load_contexts_safe(project)
        payload_out = {
            "ok": True,
            "revision": hashlib.sha256(raw).hexdigest(),
            "tasks": _attach(data["tasks"], contexts),
        }
        if warning:
            payload_out["warning"] = warning
        return payload_out


def write_context(project: dict, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise LLMWikiError("自动整理请求必须是对象。")
    allowed = {"task_id", "checkpoint", "next_step", "source_record_id", "base_revision"}
    extra = set(payload) - allowed
    if extra:
        raise LLMWikiError(f"未允许的字段：{', '.join(sorted(extra))}")
    missing = [key for key in ("task_id", "checkpoint", "next_step", "source_record_id", "base_revision") if key not in payload]
    if missing:
        raise LLMWikiError("自动整理缺少必要字段。")
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise LLMWikiError("任务 ID 无效。")
    checkpoint = _text(payload.get("checkpoint"), 4000)
    next_step = _text(payload.get("next_step"), 4000)
    source_record_id = _link(payload.get("source_record_id"))
    if not source_record_id:
        raise LLMWikiError("请提供已保存的科研记录来源。")
    base_revision = payload.get("base_revision")
    if not isinstance(base_revision, str) or (base_revision and not SHA.fullmatch(base_revision)):
        raise LLMWikiError("自动整理版本无效。")
    read_record(project["source_root"], source_record_id, state_root=project["state_root"])
    with LOCK, _file_lock(project, "research-progress-context"):
        _, tasks, _ = _read(project)
        if not any(task["id"] == task_id for task in tasks["tasks"]):
            raise LLMWikiError("未找到任务。")
        path, data, _ = _read_contexts(project)
        current = data["items"].get(task_id)
        same = (
            current is not None
            and current.get("checkpoint") == checkpoint
            and current.get("next_step") == next_step
            and current.get("source_record_id") == source_record_id
        )
        if same:
            return {
                "ok": True,
                "changed": False,
                "context_revision": current["revision"],
                "generated_at": current["generated_at"],
            }
        expected = current["revision"] if current else ""
        if base_revision != expected:
            raise LLMWikiError("自动整理存在较新版本，已保留当前结果。")
        generated_at = _now()
        item = {
            "checkpoint": checkpoint,
            "next_step": next_step,
            "source_record_id": source_record_id,
            "generated_at": generated_at,
        }
        item["revision"] = _context_revision(item)
        data["items"][task_id] = item
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        if len(raw) > MAX_BYTES:
            raise LLMWikiError("自动整理记录已达容量上限，不会覆盖原文件。")
        _atomic(path, raw)
        return {
            "ok": True,
            "changed": True,
            "context_revision": item["revision"],
            "generated_at": generated_at,
        }


def _preview(text: str) -> str:
    value = text or ""
    return value if len(value) <= MCP_PREVIEW else value[:MCP_PREVIEW]


def resolve_project(project_root: str, state_root: str | None = None) -> dict:
    from llmwiki_registry import get_project

    # A result must name its project. Letting a blank value fall through to the
    # registry's "currently selected project" would deliver one project's summary
    # into whichever project the user happened to have open.
    if not isinstance(project_root, str) or not project_root.strip():
        raise LLMWikiError("请明确指定项目，不能用当前选中项目代替。")
    project = dict(get_project(project_root)["project"])
    if state_root:
        project["state_root"] = state_root
    return project


def mcp_get(project_root: str, state_root: str | None = None, task_id: str | None = None) -> dict:
    project = resolve_project(project_root, state_root)
    summary = load_summary(project)
    tasks = summary["tasks"]
    if task_id:
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise LLMWikiError("任务 ID 无效。")
        task = next((item for item in tasks if item["id"] == task_id), None)
        if task is None:
            raise LLMWikiError("未找到任务。")
        return {
            "ok": True,
            "revision": summary["revision"],
            "context_revision": task.get("context_revision") or "",
            "task": task,
            "warning": summary.get("warning", ""),
        }
    active = sort_resume(tasks)[:MCP_LIST_LIMIT]
    truncated = len(sort_resume(tasks)) > MCP_LIST_LIMIT
    items = []
    for task in active:
        context = task.get("effective_context") or {}
        items.append({
            "id": task["id"],
            "title": task["title"],
            "status": task["status"],
            "description": _preview(task["description"]),
            "priority": task["priority"],
            "ddl": task["ddl"],
            "updated_at": task["updated_at"],
            "revision": summary["revision"],
            "context_revision": task.get("context_revision") or "",
            "checkpoint": _preview(context.get("checkpoint", "")),
            "next_step": _preview(context.get("next_step", "")),
        })
    return {
        "ok": True,
        "revision": summary["revision"],
        "tasks": items,
        "truncated": truncated,
        "warning": summary.get("warning", ""),
    }


def mcp_context_write(
    project_root: str,
    task_id: str,
    checkpoint: str,
    next_step: str,
    source_record_id: str,
    base_revision: str,
    state_root: str | None = None,
) -> dict:
    project = resolve_project(project_root, state_root)
    return write_context(
        project,
        {
            "task_id": task_id,
            "checkpoint": checkpoint,
            "next_step": next_step,
            "source_record_id": source_record_id,
            "base_revision": base_revision,
        },
    )


def active_tasks(project: dict, limit: int = 3) -> list[dict]:
    """Recently touched work for the landing page (priority, then human edit time).

    Deliberately avoids the records scan that load() does: the landing page only
    needs what the user was last doing, and must stay cheap to render.
    """
    summary = load_summary(project)
    return sort_resume(summary["tasks"])[:limit]


def page(home: str, project_id: str) -> str:
    # Stable shared-server entry point; markup belongs to the progress feature.
    from progress_page import page as render_page
    return render_page(home, project_id)
