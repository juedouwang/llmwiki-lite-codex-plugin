"""Daily projections of the shared research-progress task store (stdlib only).

No copies of project tasks are stored here. The workspace owner uses the existing
research_reports storage context without adding a project to the registry.
"""
from __future__ import annotations

from datetime import date

from llmwiki_core import LLMWikiError
from llmwiki_registry import list_projects
import research_progress as progress
from research_reports import WORKSPACE_ID, workspace


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


def mutate(home, payload, actor="user") -> dict:
    """Mutate the same IDs as research_progress; callers, not payloads, set actor.

    Delivery summaries and record links are returned even for workspace tasks so
    consumers without a project record route can still display the delivery.
    """
    if not isinstance(payload, dict):
        raise LLMWikiError("每日任务操作必须是对象。")
    action = payload.get("action")
    if not isinstance(action, str) or action not in {"create", "update", "delete", "plan", "submit", "accept", "complete", "restore"}:
        raise LLMWikiError("未知的每日任务操作。")
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
