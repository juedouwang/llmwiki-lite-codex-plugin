"""Small, local research timeline: explicit tasks and durable hand-off context.

No dates, completion percentages or scientific outcomes are inferred from files.
Automatic summaries are stored separately and never wait on a model.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from llmwiki_core import LLMWikiError
from research_notebook import NotebookConflict, _atomic, _file_lock, safe_file
from research_records import _split_record_id, list_records, read_record, write_record

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
MAX_PLAN_TASKS = 100
MAX_PLAN_REQUESTS = 500
DAILY_DEFAULTS = {
    "parent_id": "", "scheduled_date": "", "review_state": "none",
    "delivery_summary": "", "completion_record_id": "", "acceptance_record_id": "",
    "rejection_reason": "", "rejection_record_id": "",
    "kind": "task", "estimated_minutes": None,
}
DAILY_FIELDS = tuple(DAILY_DEFAULTS)
RECEIPT_FIELDS = ("completion_cycle", "delivery_receipt", "acceptance_receipt", "rejection_receipt")
PROTECTED_FIELDS = (
    "review_state", "delivery_summary", "completion_record_id", "acceptance_record_id",
    "completed_at", "resume_status", "rejection_reason", "rejection_record_id", *RECEIPT_FIELDS,
)


def day_string(value, *, required: bool = False) -> str:
    value = _text(value, 10)
    if not value and not required:
        return ""
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError as exc:
        raise LLMWikiError("日期须为有效的 YYYY-MM-DD。") from exc
    return value


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
    for key in DAILY_DEFAULTS:
        if key in value:
            if key == "estimated_minutes":
                minutes = value[key]
                if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 1440):
                    raise LLMWikiError("预计用时须为 1 至 1440 的整数分钟，或 null。")
                task[key] = minutes
            else:
                task[key] = _text(value[key], MAX_DESCRIPTION if key == "delivery_summary" else 240)
    if task.get("review_state", "none") not in {"none", "pending", "accepted", "rejected"}:
        raise LLMWikiError("验收状态无效。")
    if task.get("kind", "task") not in {"goal", "task"}:
        raise LLMWikiError("任务类型无效。")
    if task.get("parent_id") and not TASK_ID.fullmatch(task["parent_id"]):
        raise LLMWikiError("父目标 ID 无效。")
    for key in ("completion_record_id", "acceptance_record_id", "rejection_record_id"):
        if key in task:
            task[key] = _link(task[key])
    if "scheduled_date" in task:
        task["scheduled_date"] = day_string(task["scheduled_date"])
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
    _relations(data["tasks"])
    requests = data.get("plan_requests", {})
    if not isinstance(requests, dict) or len(requests) > MAX_PLAN_REQUESTS:
        raise LLMWikiError("拆解请求记录损坏或过多。")
    for key, receipt in requests.items():
        if (not isinstance(key, str) or not key or len(key) > 160 or not isinstance(receipt, dict)
                or not isinstance(receipt.get("fingerprint"), str) or not SHA.fullmatch(receipt["fingerprint"])
                or not isinstance(receipt.get("goal_id"), str) or not TASK_ID.fullmatch(receipt["goal_id"])
                or not isinstance(receipt.get("task_ids"), list) or len(receipt["task_ids"]) > MAX_PLAN_TASKS
                or any(not isinstance(tid, str) or not TASK_ID.fullmatch(tid) for tid in receipt["task_ids"])):
            raise LLMWikiError("拆解请求记录损坏。")
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


def _record_source(project: dict, *, initialize: bool = False) -> str:
    # Workspace is a storage context, never a fake registered project.
    source = project.get("source_root", project["wiki_root"])
    if initialize and project.get("id") == "__workspace__":
        from llmwiki_core import init_project, _load_config
        root = Path(source).resolve()
        root.mkdir(parents=True, exist_ok=True)
        config = safe_file(Path(project["state_root"]), "config.json")
        if not config.exists():
            init_project(str(root), state_root=project["state_root"], wiki_root=project["wiki_root"])
        _, wiki, _ = _load_config(root, project["state_root"])
        if wiki != Path(project["wiki_root"]).resolve():
            raise LLMWikiError("工作台记录目录配置不匹配。")
    return source


def _all_records(project: dict) -> list[dict]:
    if project.get("id") == "__workspace__" and not Path(project["state_root"], "config.json").is_file():
        return []
    return list_records(
        _record_source(project),
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
    item.update({key: task.get(key, default) for key, default in DAILY_DEFAULTS.items()})
    for name in ("completion_record", "rejection_record", "acceptance_record"):
        record_id = task.get(name + "_id", "")
        receipt = next((task.get(key) for key in ("delivery_receipt", "rejection_receipt", "acceptance_receipt")
                        if (task.get(key) or {}).get("record_id") == record_id), None)
        item[name] = ({"id": record_id, **{key: receipt.get(key, "") for key in
                                         ("title", "summary", "actor", "recorded_at")}} if receipt else None)
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
    snapshot.update({key: deepcopy(task[key]) for key in TASK_FIELDS + DAILY_FIELDS + RECEIPT_FIELDS if key in task})
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
    read_record(_record_source(project), record_id, state_root=project["state_root"])


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


def _relations(tasks: list[dict]) -> None:
    by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        parent_id = task.get("parent_id", "")
        if task.get("kind") == "goal":
            if parent_id or task.get("scheduled_date"):
                raise LLMWikiError("父目标不能是每日子任务。")
            day_string(task.get("start", ""), required=True)
            day_string(task.get("end", ""), required=True)
        if parent_id:
            parent = by_id.get(parent_id)
            if parent is None or parent is task or parent.get("kind") != "goal":
                raise LLMWikiError("父目标须为同一任务存储中的目标 ID。")
            scheduled = day_string(task.get("scheduled_date", ""), required=True)
            if scheduled < parent["start"] or scheduled > parent["end"]:
                raise LLMWikiError("子任务日期须在父目标起止日期内。")
        review = task.get("review_state", "none")
        if (review == "pending" and task["status"] == "done"
                or review == "accepted" and task["status"] != "done"
                or review == "rejected" and task["status"] == "done"):
            raise LLMWikiError("任务状态与验收状态不一致。")
        cycle = task.get("completion_cycle", 0)
        if type(cycle) is not int or cycle < 0:
            raise LLMWikiError("完成留痕元数据无效。")
        for key in ("delivery_receipt", "acceptance_receipt", "rejection_receipt"):
            receipt = task.get(key)
            if receipt is None:
                continue
            if (not isinstance(receipt, dict) or receipt.get("actor") not in ("agent", "user")
                    or type(receipt.get("cycle")) is not int or not 0 <= receipt["cycle"] <= cycle
                    or type(receipt.get("sequence")) is not int or receipt["sequence"] < 1
                    or not isinstance(receipt.get("key"), str) or not SHA.fullmatch(receipt["key"])
                    or not isinstance(receipt.get("revision"), str)
                    or not receipt.get("record_id")):
                raise LLMWikiError("完成留痕元数据无效。")
            _link(receipt["record_id"])


def _guard(incoming: dict, previous: dict, actor: str) -> None:
    if actor == "agent" and incoming.get("status") == "done":
        raise LLMWikiError("助手只能提交交付，不能确认完成。")
    for key in PROTECTED_FIELDS:
        default = DAILY_DEFAULTS.get(key)
        if key in incoming and incoming[key] != previous.get(key, default):
            raise LLMWikiError("验收及交付字段只允许通过 submit / accept / restore 修改。")
    if "kind" in incoming and previous and incoming["kind"] != previous.get("kind", "task"):
        raise LLMWikiError("已有任务不能改换类型。")


def _incoming(payload: dict) -> dict:
    incoming = payload.get("task", {})
    if not isinstance(incoming, dict):
        raise LLMWikiError("task 必须是对象。")
    return incoming


def _new_task(incoming: dict, now: str, actor: str) -> dict:
    _guard(incoming, {}, actor)
    fields = validate({**DAILY_DEFAULTS, **incoming})
    raw_modes = incoming.get("context_mode")
    raw_modes = raw_modes if isinstance(raw_modes, dict) else {}
    modes = {field: raw_modes.get(field) if raw_modes.get(field) in MODES
             else ("manual" if fields.get(field) else "auto") for field in CONTEXT_FIELDS}
    return {**fields, "id": uuid4().hex, "created_at": now,
            "context_mode": modes, "completed_at": None}


def _queue_receipt(task: dict, kind: str, actor: str, revision: str, now: str,
                   jobs: list[dict], summary: str = "") -> dict:
    old = task.get(kind + "_receipt") or {}
    sequence = old.get("sequence", 0) + 1
    cycle = task.get("completion_cycle", 0)
    identity = [task["id"], cycle, kind, sequence, actor, summary,
                task.get("completion_record_id", "") if kind in {"acceptance", "rejection"} else ""]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    placeholder = "records/receipt-" + key + ".md"
    receipt = {"key": key, "sequence": sequence, "cycle": cycle, "actor": actor,
               "revision": revision, "record_id": placeholder}
    task[kind + "_receipt"] = receipt
    label = {"delivery": "任务交付", "acceptance": "用户验收", "rejection": "退回修改"}[kind]
    if kind == "delivery":
        understanding = (f"{'助手' if actor == 'agent' else '用户'}提交了任务交付，尚待用户验收。"
                         "此记录仅保存提交者陈述，不验证或推断科研结论。\n\n交付摘要：\n" + summary)
    elif kind == "rejection":
        understanding = "用户退回本轮交付，任务重新进入执行中；原交付记录保留。\n\n退回意见：\n" + summary
    else:
        understanding = "用户明确确认此任务完成。此记录仅留存用户操作，不验证或推断科研结论。"
        if summary:
            understanding += "\n\n用户验收的交付摘要：\n" + summary
    receipt.update(title=f"{label}：{task['title']}", summary=understanding, recorded_at=now)
    jobs.append({"placeholder": placeholder, "key": key, "task_id": task["id"], "kind": kind, "title": receipt["title"],
                 "understanding": understanding, "recorded_at": now,
                 "discussion_context": f"任务 ID：{task['id']}；操作人：{actor}；完成轮次：{cycle}。",
                 "related_pages": [task[LINK_FIELD]] if task.get(LINK_FIELD) else []})
    return receipt


def _submit(task: dict, summary: str, actor: str, revision: str, now: str, jobs: list[dict]) -> None:
    if not summary:
        raise LLMWikiError("提交交付须填写非空 summary。")
    if task["status"] == "done":
        raise LLMWikiError("已完成任务须由用户恢复后重新提交。")
    if task.get("review_state") == "pending":
        if task.get("delivery_summary") == summary:
            return
        raise LLMWikiError("任务正在待审批，请用户先验收或退回修改。")
    receipt = _queue_receipt(task, "delivery", actor, revision, now, jobs, summary)
    task.update(review_state="pending", delivery_summary=summary,
                completion_record_id=receipt["record_id"], acceptance_record_id="")
    if task["status"] not in {"planned", "active"}:
        task["status"] = "active"


def _reject(task: dict, reason: str, actor: str, revision: str, now: str, jobs: list[dict]) -> None:
    if actor != "user" or task.get("review_state") != "pending" or not reason:
        raise LLMWikiError("只有用户可以填写退回意见并退回待审批任务。")
    receipt = _queue_receipt(task, "rejection", actor, revision, now, jobs, reason)
    task.update(review_state="rejected", status="active", rejection_reason=reason,
                rejection_record_id=receipt["record_id"], completed_at=None)
    task["completion_cycle"] = task.get("completion_cycle", 0) + 1


def _accept(task: dict, actor: str, revision: str, now: str, jobs: list[dict]) -> None:
    if actor != "user":
        raise LLMWikiError("只有用户可以验收任务。")
    if task["status"] == "done" and task.get("review_state") == "accepted":
        return
    pending = task.get("review_state") == "pending"
    receipt = _queue_receipt(task, "acceptance", actor, revision, now, jobs,
                             task.get("delivery_summary", "") if pending else "")
    if not pending:
        task["completion_record_id"] = receipt["record_id"]
    task["acceptance_record_id"] = receipt["record_id"]
    task["review_state"] = "accepted"
    previous = task["status"]
    _apply_completion(task, previous, "done", now)
    if not task.get("completed_at"):
        task["completed_at"] = now
    task["status"] = "done"


def _restore(task: dict, status: str) -> None:
    # Keep both record links and their old history; a later completion is a new cycle.
    task["completion_cycle"] = task.get("completion_cycle", 0) + 1
    task["status"] = status
    task["review_state"] = "none"
    task["rejection_reason"] = ""
    task["rejection_record_id"] = ""
    task["completed_at"] = None


def _write_receipt(project: dict, job: dict) -> dict:
    source = _record_source(project, initialize=True)
    tag = "task-receipt-" + job["key"]
    # A record may have been appended before a failed tasks.json replace. Reuse it
    # on retry, including when the retry occurs on a different day or process.
    with _file_lock(project, "task-receipts"):
        records = list_records(source, state_root=project["state_root"], tag=tag, max_records=1)["records"]
        if records:
            return records[0]
        fields = {key: job[key] for key in ("title", "understanding", "discussion_context", "recorded_at", "related_pages")}
        return write_record(source, state_root=project["state_root"], project_id=project.get("id", ""),
                            tags=["task-receipt", tag], **fields)["record"]


def _encoded(data: dict, *, reserve: int = 0) -> bytes:
    if len(data["tasks"]) > MAX_TASKS:
        raise LLMWikiError("任务数量已达 500 项上限。")
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    if len(raw) + reserve > MAX_BYTES:
        raise LLMWikiError("任务记录已达容量上限，不会覆盖原文件。")
    return raw


def _plan_identity(payload: dict) -> tuple[str, str]:
    request_id = _text(payload.get("request_id"), 160)
    if not request_id:
        raise LLMWikiError("plan 须提供非空 request_id。")
    content = {key: payload.get(key) for key in ("task", "subtasks", "parent_id")}
    try:
        raw = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    except (ValueError, TypeError) as exc:
        raise LLMWikiError("拆解请求内容无效。") from exc
    if len(raw) > MAX_BYTES:
        raise LLMWikiError("拆解请求过大。")
    return request_id, hashlib.sha256(raw).hexdigest()


def _plan(project: dict, data: dict, payload: dict, actor: str, now: str) -> dict:
    request_id, fingerprint = _plan_identity(payload)
    requests = data.setdefault("plan_requests", {})
    if len(requests) >= MAX_PLAN_REQUESTS:
        raise LLMWikiError("拆解请求数量已达上限。")
    incoming = _incoming(payload)
    subtasks = payload.get("subtasks")
    if not isinstance(subtasks, list) or not 1 <= len(subtasks) <= MAX_PLAN_TASKS:
        raise LLMWikiError("每次拆解须包含 1 至 100 项子任务。")
    parent_id = payload.get("parent_id", "")
    if parent_id:
        goal = next((item for item in data["tasks"] if item["id"] == parent_id), None)
        if goal is None or goal.get("kind") != "goal" or incoming:
            raise LLMWikiError("追加拆解须指定同存储的已有父目标，且不能同时修改目标。")
    else:
        if incoming.get("parent_id") or incoming.get("scheduled_date") or incoming.get("status", "planned") != "planned":
            raise LLMWikiError("新父目标须为未完成的非每日任务。")
        day_string(incoming.get("start", ""), required=True)
        day_string(incoming.get("end", ""), required=True)
        goal = _new_task({**incoming, "kind": "goal"}, now, actor)
        _require_record(project, goal[LINK_FIELD])
        _event(goal, now)
        data["tasks"].append(goal)
    ids = []
    for fields in subtasks:
        if not isinstance(fields, dict):
            raise LLMWikiError("子任务必须是对象。")
        if fields.get("parent_id", goal["id"]) != goal["id"] or fields.get("kind", "task") != "task":
            raise LLMWikiError("子任务只能关联本次拆解的父目标。")
        if fields.get("status", "planned") not in ("planned", "active", "blocked"):
            raise LLMWikiError("拆解不能创建已完成子任务。")
        day_string(fields.get("scheduled_date", ""), required=True)
        task = _new_task({**fields, "parent_id": goal["id"]}, now, actor)
        _require_record(project, task[LINK_FIELD])
        _event(task, now)
        data["tasks"].append(task)
        ids.append(task["id"])
    receipt = {"fingerprint": fingerprint, "goal_id": goal["id"], "task_ids": ids}
    requests[request_id] = receipt
    return receipt


def _response(project: dict, data: dict, revision: str, task_id=None, plan=None) -> dict:
    contexts, _, warning = _load_contexts_safe(project)
    tasks = _attach(data["tasks"], contexts)
    result = {"ok": True, "revision": revision, "tasks": tasks}
    by_id = {task["id"]: task for task in tasks}
    if task_id in by_id:
        result["task"] = by_id[task_id]
    if plan is not None:
        if any(tid not in by_id for tid in [plan["goal_id"], *plan["task_ids"]]):
            raise LLMWikiError("该拆解请求已执行，但部分任务已删除；不会重新创建。")
        result["goal"] = by_id[plan["goal_id"]]
        result["tasks"] = [by_id[tid] for tid in plan["task_ids"]]
    if warning:
        result["warning"] = warning
    return result


def update(project: dict, payload: dict, actor: str = "user") -> dict:
    """One mutation engine for legacy progress and daily tasks; actor is trusted by the caller.

    Plan batches commit once. Receipt records use the standard append-only writer
    and deterministic tags; interrupted final task writes are safe to retry.
    """
    if not isinstance(payload, dict) or actor not in ("user", "agent"):
        raise LLMWikiError("任务操作或操作人无效。")
    action = _text(payload.get("action"), 40)
    if not isinstance(payload.get("revision"), str):
        raise LLMWikiError("任务操作须包含 revision 字符串。")
    incoming = _incoming(payload)
    if actor == "agent" and (action in {"accept", "complete", "restore", "reject"} or incoming.get("status") == "done"
                              or action == "import" and payload.get("completed")):
        raise LLMWikiError("助手只能提交交付，不能验收或恢复已完成任务。")
    summary = _text(payload.get("summary", ""), MAX_DESCRIPTION) if action == "submit" else ""
    reason = _text(payload.get("reason", ""), 4000) if action == "reject" else ""
    with LOCK, _file_lock(project, "research-progress"):
        path, data, revision = _read(project)
        original = deepcopy(data)
        task = next((item for item in data["tasks"] if item["id"] == payload.get("id")), None)
        plan = None
        if action == "plan":
            request_id, fingerprint = _plan_identity(payload)
            plan = data.get("plan_requests", {}).get(request_id)
            if plan is not None:
                if plan["fingerprint"] != fingerprint:
                    raise LLMWikiError("request_id 已用于不同的拆解内容。")
                return _response(project, data, revision, plan=plan)
        if task is not None:
            _guard(incoming, task, actor)
        if payload.get("revision") != revision:
            # Only exact successful receipt retries may use their original revision.
            receipt = (task or {}).get("delivery_receipt" if action == "submit" else "rejection_receipt" if action == "reject" else "acceptance_receipt", {})
            retry = (task is not None and not incoming and receipt.get("revision") == payload.get("revision")
                     and receipt.get("actor") == actor and (
                         action == "submit" and task.get("review_state") == "pending" and summary == task.get("delivery_summary")
                         or action == "reject" and task.get("review_state") == "rejected" and reason == task.get("rejection_reason")
                         or action in {"accept", "complete"} and task.get("review_state") == "accepted"))
            if retry:
                return _response(project, data, revision, task["id"])
            raise NotebookConflict("科研进度已被另一页面修改。请先重新载入；当前填写内容不会被覆盖。")
        now = _now()
        jobs: list[dict] = []
        if action == "plan":
            plan = _plan(project, data, payload, actor, now)
        elif action == "create":
            task = _new_task(incoming, now, actor)
            _require_record(project, task[LINK_FIELD])
            if task["status"] == "done":
                task["status"] = "planned"
                _accept(task, actor, revision, now, jobs)
            _event(task, now)
            data["tasks"].append(task)
        elif action in {"update", "complete", "accept", "reject", "submit", "restore", "delete"}:
            if task is None:
                raise LLMWikiError("未找到任务。")
            before = deepcopy(task)
            if action == "delete":
                data["tasks"].remove(task)
            elif action == "submit":
                if incoming:
                    raise LLMWikiError("submit 使用顶层 summary，不接受 task 修改。")
                _submit(task, summary, actor, revision, now, jobs)
            elif action == "reject":
                if incoming:
                    raise LLMWikiError("reject 使用顶层 reason，不接受 task 修改。")
                _reject(task, reason, actor, revision, now, jobs)
            elif action in {"accept", "complete"}:
                if incoming:
                    raise LLMWikiError("accept / complete 不接受 task 修改。")
                _accept(task, actor, revision, now, jobs)
            elif action == "restore":
                if incoming:
                    raise LLMWikiError("restore 不接受 task 修改。")
                if task["status"] == "done":
                    status = task.get("resume_status", "planned")
                    _restore(task, status if status in STATUSES - {"done"} else "planned")
            else:
                _guard(incoming, task, actor)
                fields = validate({**task, **incoming})
                fields.update({key: task[key] for key in fields if key in task and key not in incoming})
                if fields[LINK_FIELD] != task.get(LINK_FIELD, ""):
                    _require_record(project, fields[LINK_FIELD])
                modes = _apply_modes(task, fields, incoming)
                target_status = fields.pop("status")
                task.update(fields)
                task["context_mode"] = modes
                if target_status == "done" and (task["status"] != "done" or "status" in incoming and task.get("review_state") != "accepted"):
                    _accept(task, actor, revision, now, jobs)
                elif task["status"] == "done" and target_status != "done":
                    if actor != "user":
                        raise LLMWikiError("只有用户可以恢复已验收任务。")
                    _restore(task, target_status)
                else:
                    task["status"] = target_status
            if action != "delete" and task != before:
                _event(task, now)
        elif action == "import":
            selected, completed = payload.get("ids", []), payload.get("completed", [])
            if (not isinstance(selected, list) or not isinstance(completed, list)
                    or len(selected) > MAX_TASKS or len(completed) > MAX_TASKS
                    or any(not isinstance(x, str) for x in selected + completed)):
                raise LLMWikiError("导入列表无效。")
            pending = {item["id"]: item for item in candidates(project)}
            known = {item["id"] for item in data["tasks"]}
            for cid in dict.fromkeys(selected):
                if cid in known:
                    continue
                if cid not in pending:
                    raise LLMWikiError("旧待办已发生变化，请重新载入后导入。")
                item = pending[cid]
                task = _new_task({"title": item["title"]}, now, actor)
                task.update(id=cid, record_id=item["record_id"])
                # Historical migration is not a new completion claim; keep its
                # original unknown completion date and original record link.
                task["status"] = "done" if cid in completed else "planned"
                _event(task, now)
                data["tasks"].append(task)
        else:
            raise LLMWikiError("未知的任务操作。")
        _relations(data["tasks"])
        if data == original:
            return _response(project, data, revision, (task or {}).get("id"), plan)
        # Check the entire batch and budget BEFORE any append-only record writes.
        _encoded(data, reserve=len(jobs) * 8192)
        for job in jobs:
            record = _write_receipt(project, job)
            record_id = _link(record["id"])
            target = next(item for item in data["tasks"] if item["id"] == job["task_id"])
            # Replace only engine-owned references, never matching user prose.
            for item in (target, target["history"][-1]):
                for field in ("completion_record_id", "acceptance_record_id", "rejection_record_id"):
                    if item.get(field) == job["placeholder"]:
                        item[field] = record_id
                receipt = item[job["kind"] + "_receipt"]
                receipt["record_id"] = record_id
                receipt["recorded_at"] = record["recorded_at"]
                if job["kind"] == "acceptance" and item.get("completed_at") == job["recorded_at"]:
                    item["completed_at"] = record["recorded_at"]
        raw = _encoded(data)
        _atomic(path, raw)
        return _response(project, data, hashlib.sha256(raw).hexdigest(), (task or {}).get("id"), plan)


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
