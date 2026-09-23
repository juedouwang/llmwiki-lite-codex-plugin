"""Daily projections of the shared research-progress task store (stdlib only).

No copies of project tasks are stored here. The workspace owner uses the existing
research_reports storage context without adding a project to the registry.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib

from llmwiki_core import LLMWikiError
from llmwiki_registry import list_projects
import research_progress as progress
from research_reports import WORKSPACE_ID, workspace
from research_notebook import NotebookConflict, _atomic, _file_lock


def _owner(home, project_id) -> dict:
    if project_id == WORKSPACE_ID:
        return workspace(home)
    if not isinstance(project_id, str) or not project_id:
        raise LLMWikiError("必须明确指定项目 ID 或 __workspace__。")
    project = next((item for item in list_projects(home)["projects"] if item["id"] == project_id), None)
    if project is None:
        raise LLMWikiError("每日任务只接受已注册项目的精确 ID。")
    return project


def _public(task: dict, owner: dict, tasks: dict, revision: str) -> dict:
    return {**task, "project_id": owner["id"], "project_name": owner["name"],
            "parent_title": tasks.get(task.get("parent_id"), {}).get("title", ""),
            "revision": revision}


def list_day(home=None, day=None) -> dict:
    """Read-only day/overdue/completed partitions, with per-owner CAS revisions."""
    day = progress.day_string(date.today().isoformat() if day is None else day, required=True)
    projects = list_projects(home)["projects"]
    result = {"ok": True, "date": day, "tasks": [], "overdue": [], "completed": [],
              "projects": [{"id": item["id"], "name": item["name"]} for item in projects], "revisions": {}}
    for owner in [workspace(home), *projects]:
        loaded = progress.load_summary(owner)
        revision = loaded["revision"]
        result["revisions"][owner["id"]] = revision
        tasks = {item["id"]: item for item in loaded["tasks"]}
        for task in tasks.values():
            scheduled = task.get("scheduled_date", "")
            if not scheduled:
                continue
            if scheduled == day:
                group = "completed" if task["status"] == "done" else "tasks"
            elif scheduled < day and task["status"] != "done":
                group = "overdue"
            else:
                continue
            result[group].append(_public(task, owner, tasks, revision))
    for group in ("tasks", "overdue", "completed"):
        result[group].sort(key=lambda task: (task["scheduled_date"], task["project_id"], task["id"]))
    return result


def _move(home, payload, actor="user") -> dict:
    """Move one standalone daily task between owner stores with dual CAS."""
    if actor != "user":
        raise LLMWikiError("只有用户可以调整每日待办的关联项目。")
    source_id = payload.get("from_project_id")
    target_id = payload.get("project_id")
    if not isinstance(source_id, str) or not isinstance(target_id, str) or not source_id or not target_id:
        raise LLMWikiError("移动每日待办必须同时指定原项目和目标项目。")
    if source_id == target_id:
        payload = {**payload, "action": "update", "revision": payload.get("source_revision", payload.get("revision", ""))}
        return mutate(home, payload, actor=actor)
    source = _owner(home, source_id)
    target = _owner(home, target_id)
    source_revision = payload.get("source_revision")
    target_revision = payload.get("target_revision")
    if not isinstance(source_revision, str) or not isinstance(target_revision, str):
        raise LLMWikiError("移动每日待办必须包含原项目和目标项目版本。")
    # Stable lock ordering prevents two opposite moves from deadlocking.
    owners = sorted((source, target), key=lambda item: item["id"])
    with progress.LOCK, _file_lock(owners[0], "research-progress"), _file_lock(owners[1], "research-progress"):
        source_path, source_data, actual_source_revision = progress._read(source)
        target_path, target_data, actual_target_revision = progress._read(target)
        if source_revision != actual_source_revision or target_revision != actual_target_revision:
            raise NotebookConflict("每日待办已被另一页面修改。请先重新载入；当前填写内容不会被覆盖。")
        task_id = payload.get("id")
        task = next((item for item in source_data["tasks"] if item["id"] == task_id), None)
        if task is None:
            raise LLMWikiError("未找到待移动的每日待办。")
        if task.get("parent_id"):
            raise LLMWikiError("科研进度子任务不能跨项目移动，请在原项目中调整。")
        if task.get("kind", "task") != "task":
            raise LLMWikiError("只有每日待办可以移动，科研进度目标不能移动。")
        if any(item["id"] == task_id for item in target_data["tasks"]):
            raise LLMWikiError("目标项目中已存在相同任务。")
        moved = deepcopy(task)
        incoming = progress._incoming(payload)
        if incoming:
            allowed = {"title", "description", "priority", "ddl", "scheduled_date", "estimated_minutes"}
            if set(incoming) - allowed:
                raise LLMWikiError("移动每日待办只允许同时修改标题、描述、日期、预计用时和优先级。")
            if "scheduled_date" in incoming:
                progress.day_string(incoming["scheduled_date"], required=True)
            progress._guard(incoming, moved, actor)
            fields = progress.validate({**moved, **incoming})
            fields.update({key: moved[key] for key in fields if key in moved and key not in incoming})
            moved.update(fields)
            moved["context_mode"] = progress._apply_modes(moved, fields, incoming)
            progress._event(moved, progress._now())
        source_data["tasks"].remove(task)
        target_data["tasks"].append(moved)
        progress._relations(source_data["tasks"])
        progress._relations(target_data["tasks"])
        source_raw = progress._encoded(source_data)
        target_raw = progress._encoded(target_data)
        _atomic(source_path, source_raw)
        _atomic(target_path, target_raw)
        revision = hashlib.sha256(target_raw).hexdigest()
        tasks = {item["id"]: item for item in target_data["tasks"]}
        return {"ok": True, "revision": revision, "task": _public(moved, target, tasks, revision),
                "tasks": [_public(item, target, tasks, revision) for item in target_data["tasks"]]}


def mutate(home, payload, actor="user") -> dict:
    """Mutate the same IDs as research_progress; callers, not payloads, set actor.

    Delivery summaries and record links are returned even for workspace tasks so
    consumers without a project record route can still display the delivery.
    """
    if not isinstance(payload, dict):
        raise LLMWikiError("每日任务操作必须是对象。")
    action = payload.get("action")
    if not isinstance(action, str) or action not in {"create", "update", "move", "delete", "plan", "submit", "accept", "reject", "complete", "restore"}:
        raise LLMWikiError("未知的每日任务操作。")
    if action == "move":
        return _move(home, payload, actor=actor)
    owner = _owner(home, payload.get("project_id"))
    incoming = progress._incoming(payload)
    if action == "create":
        progress.day_string(incoming.get("scheduled_date", ""), required=True)
        if incoming.get("kind", "task") != "task":
            raise LLMWikiError("每日父目标须通过 plan 创建。")
    elif action == "update" and "scheduled_date" in incoming:
        progress.day_string(incoming["scheduled_date"], required=True)
    result = progress.update(owner, payload, actor=actor)
    # Use the exact same locked mutation snapshot, never reload into another revision.
    tasks = {task["id"]: task for task in result.get("tasks", [])}
    if "goal" in result:
        tasks[result["goal"]["id"]] = result["goal"]
    for key in ("task", "goal"):
        if key in result:
            result[key] = _public(result[key], owner, tasks, result["revision"])
    result["tasks"] = [_public(task, owner, tasks, result["revision"]) for task in result.get("tasks", [])]
    return result
