"""Small, local research timeline: explicit tasks and durable hand-off context.

No dates, completion percentages or scientific outcomes are inferred from files.
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
FIELDS = ("title", "status", "start", "end", "checkpoint", "next_step")
# Snapshotted and compared alongside FIELDS, but validated separately below.
LINK_FIELD = "record_id"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def validate(value: dict) -> dict:
    if not isinstance(value, dict):
        raise LLMWikiError("任务必须是对象。")
    task = {key: _text(value.get(key, ""), 240 if key == "title" else 4000) for key in FIELDS}
    task[LINK_FIELD] = _link(value.get(LINK_FIELD, ""))
    if not task["title"]:
        raise LLMWikiError("请填写任务名称。")
    if task["status"] not in STATUSES:
        raise LLMWikiError("任务状态无效。")
    for key in ("start", "end"):
        if task[key]:
            try:
                if date.fromisoformat(task[key]).isoformat() != task[key]:
                    raise ValueError()
            except ValueError as exc:
                raise LLMWikiError("日期须为 YYYY-MM-DD。") from exc
    if bool(task["start"]) != bool(task["end"]):
        raise LLMWikiError("请同时填写开始和结束日期，或都留空作为未排期任务。")
    if task["start"] and task["end"] < task["start"]:
        raise LLMWikiError("结束日期不能早于开始日期。")
    return task


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
            if not re.fullmatch(r"[a-f0-9]{32}", task["id"]) or task["id"] in seen:
                raise ValueError()
            seen.add(task["id"])
            if not isinstance(task.get("history"), list) or len(task["history"]) > 100:
                raise ValueError()
            for key in ("created_at", "updated_at"):
                datetime.fromisoformat(task[key].replace("Z", "+00:00"))
            if not isinstance(task.get("record_id"), str):
                raise ValueError()
            for event in task["history"]:
                validate(event)
                datetime.fromisoformat(event["at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError) as exc:
        raise LLMWikiError("任务文件损坏或版本不兼容，已停止写入，请检查原文件。") from exc
    return path, data, hashlib.sha256(raw).hexdigest()


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
        occurrences = {}
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


def load(project: dict) -> dict:
    with LOCK:
        _, data, revision = _read(project)
        ids = {t["id"] for t in data["tasks"]}
        records = _all_records(project)
        pending = [c for c in _legacy_candidates(records) if c["id"] not in ids]
        return {"ok": True, "revision": revision, "tasks": data["tasks"],
                "candidates": pending, "records": _link_targets(records)}


def _event(task: dict, now: str) -> None:
    task["updated_at"] = now
    snapshot = {k: task.get(k, "") for k in FIELDS + (LINK_FIELD,)}
    task.setdefault("history", []).append({"at": now, **snapshot})
    # Preserve the initial snapshot, plus the most recent 99 explicit edits.
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
            fields = validate(payload.get("task"))
            _require_record(project, fields[LINK_FIELD])
            task = {**fields, "id": uuid4().hex, "created_at": now}
            _event(task, now)
            data["tasks"].append(task)
        elif action == "update":
            task = next((t for t in data["tasks"] if t["id"] == payload.get("id")), None)
            if task is None:
                raise LLMWikiError("未找到任务。")
            fields = validate(payload.get("task"))
            if fields[LINK_FIELD] != task.get(LINK_FIELD, ""):
                _require_record(project, fields[LINK_FIELD])
            if any(task.get(k, "") != fields[k] for k in FIELDS + (LINK_FIELD,)):
                task.update(fields)
                _event(task, now)
        elif action == "import":
            selected = payload.get("ids", [])
            completed = payload.get("completed", [])
            if not isinstance(selected, list) or not isinstance(completed, list) or any(not isinstance(x, str) for x in selected + completed):
                raise LLMWikiError("导入列表无效。")
            pending = {c["id"]: c for c in candidates(project)}
            known = {t["id"] for t in data["tasks"]}
            for cid in dict.fromkeys(selected):
                if cid in known:
                    continue
                if cid not in pending:
                    raise LLMWikiError("旧待办已发生变化，请重新载入后导入。")
                item = pending[cid]
                task = {**validate({"title": item["title"], "status": "done" if cid in completed else "planned"}),
                        "id": cid, "record_id": item["record_id"], "created_at": now}
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
        return {"ok": True, "revision": hashlib.sha256(raw).hexdigest(), "tasks": data["tasks"]}


def active_tasks(project: dict, limit: int = 3) -> list[dict]:
    """In-progress work for the project landing page.

    Deliberately avoids the records scan that load() does: the landing page only
    needs what the user was last doing, and must stay cheap to render.
    """
    with LOCK:
        _, data, _ = _read(project)
    active = [t for t in data["tasks"] if t["status"] in {"active", "blocked"}]
    active.sort(key=lambda task: task.get("updated_at", ""), reverse=True)
    return active[:limit]


def page(home: str, project_id: str) -> str:
    from llmwiki_registry import get_project
    from research_web_ui import esc, layout

    project = get_project(project_id, home=home)["project"]
    body = f'''<link rel="stylesheet" href="/static/progress.css">
<section id="research-progress" data-project="{esc(project_id)}">
<header class="page-header"><h1>科研进度</h1><button id="progress-import" hidden>导入旧待办</button></header>
<div id="progress-message" role="status" hidden></div>
<section id="progress-resume" aria-label="继续上次" hidden></section>
<form id="progress-add" class="progress-add"><input type="text" name="title" maxlength="240" aria-label="新任务" placeholder="添加研究任务，回车保存" required autocomplete="off"><button type="submit">添加</button></form>
<div class="progress-range"><div><button id="progress-prev" aria-label="上一段时间">←</button><button id="progress-today">今天</button><button id="progress-next" aria-label="下一段时间">→</button><span id="progress-range-label"></span></div><label class="meta">范围 <select id="progress-days" aria-label="时间范围"><option value="14">两周</option><option value="28">四周</option></select></label></div>
<div id="progress-timeline" aria-label="研究时间轴" tabindex="0"></div>
<section id="progress-unscheduled"><h2>未排期</h2><div></div></section>
<details id="progress-done"><summary>已完成 <span></span></summary><div></div></details>
<dialog id="progress-dialog"><form id="progress-form">
<div class="progress-dialog-head"><h2>任务</h2><button type="button" id="progress-close" aria-label="关闭任务">×</button></div>
<label>任务名称<input type="text" name="title" maxlength="240" required></label>
<label>状态<select name="status"><option value="planned">未开始</option><option value="active">进行中</option><option value="blocked">卡住了</option><option value="done">已完成</option></select></label>
<div class="progress-dates"><label>开始<input name="start" type="date"></label><label>结束<input name="end" type="date"></label></div>
<label>上次做到哪<textarea name="checkpoint" rows="3" maxlength="4000" placeholder="如：已跑完基线，夜间数据还没验证；参数保存在 config.yaml"></textarea></label>
<label>下一步<textarea name="next_step" rows="2" maxlength="4000" placeholder="如：先检查昨晚的实验日志，再补夜间数据"></textarea></label>
<label>关联笔记<select name="record_id"><option value="">不关联</option></select></label>
<div id="progress-source"></div><details id="progress-history"><summary>修改记录</summary><div></div></details>
<p id="progress-error" role="alert" hidden></p><button type="button" id="progress-reload" hidden>读取最新版本，保留当前填写</button>
<div class="progress-dialog-actions"><span class="meta">日期可都留空</span><button type="submit" class="primary">保存</button></div>
</form></dialog>
<dialog id="progress-import-dialog"><h2>导入旧待办</h2><p class="muted">从科研记录中选择要跟进的事项，不自动推断日期。此浏览器的旧完成标记会一并保存。</p><form id="progress-import-form"><div id="progress-candidates"></div><p id="progress-import-error" role="alert" hidden></p><div class="actions"><button type="button" id="progress-import-close">取消</button><button class="primary">导入所选</button></div></form></dialog>
</section><script src="/static/progress.js" defer></script>'''
    return layout("科研进度 · " + str(project["name"]), body, project_id=project_id, active="todos", home=home)
