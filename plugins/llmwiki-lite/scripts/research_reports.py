"""Deterministic Markdown report storage. No model, network, or background worker."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from llmwiki_core import LLMWikiError
from llmwiki_registry import get_project, list_projects
from research_notebook import LOCK, MAX_DOCUMENT, _atomic, _file_lock, safe_file

CST = timezone(timedelta(hours=8))
WORKSPACE_ID = "__workspace__"


class ReportError(LLMWikiError):
    def __init__(self, message: str, code: str = "INVALID_INPUT", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value: Any) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def identity(kind: str, start: str) -> tuple[str, str]:
    if kind not in {"daily", "weekly"} or not isinstance(start, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
        raise ReportError("报告类型或日期无效。")
    try:
        day = date.fromisoformat(start)
    except ValueError as exc:
        raise ReportError("日期无效。") from exc
    if kind == "weekly" and day.weekday() != 0:
        raise ReportError("周报开始日期必须是周一。")
    return f"{kind}-{start}", (day + timedelta(days=6 if kind == "weekly" else 0)).isoformat()


def body_text(value: Any) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ReportError("正文必须是文本。")
    if len(value.encode("utf-8")) > MAX_DOCUMENT:
        raise ReportError("正文超过 2 MiB。", "TOO_LARGE", 413)
    return value


def project_for(project_id: str, home: str | None = None) -> dict:
    if not isinstance(project_id, str) or not project_id:
        raise ReportError("必须明确指定已注册项目。")
    project = get_project(project_id, home=home)["project"]
    if project["id"] != project_id:
        raise ReportError("报告只接受注册项目 ID。")
    return project


def workspace(home=None) -> dict:
    """Storage context, not a registered or selected research project."""
    from llmwiki_registry import llmwiki_home
    root = safe_file(llmwiki_home(home), 'workspace')
    return {'id': WORKSPACE_ID, 'name': '工作台', 'scope': 'workspace',
            'wiki_root': str(root), 'state_root': str(root / '.state')}


def report_owner(owner_id, home=None) -> dict:
    return workspace(home) if owner_id in (None, WORKSPACE_ID) else project_for(owner_id, home)


def is_workspace(owner: dict) -> bool:
    return owner.get('scope') == 'workspace' and owner.get('id') == WORKSPACE_ID


def _state(project: dict, name: str) -> Path:
    return safe_file(Path(project["state_root"]), f"reports/{name}")


def _body_path(project: dict, key: str, name: str) -> Path:
    if not re.fullmatch(r"(?:draft|candidate|previous-draft|v\d{4,})\.md", name):
        raise ReportError("报告版本无效。")
    return safe_file(Path(project["wiki_root"]), f"records/reports/{key}/{name}")


def _json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, value: Any):
    _atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _recover(project: dict, key: str):
    journal = _state(project, key + ".pending")
    pending = _json(journal)
    if pending is None:
        return
    for name, body in pending["bodies"].items():
        _atomic(_body_path(project, key, name), body.encode("utf-8"))
    _write_json(_state(project, key + ".json"), pending["metadata"])
    journal.unlink()


@contextmanager
def locked(project: dict, key: str):
    with LOCK, _file_lock(project, "report-" + key):
        _recover(project, key)
        yield


def _commit(project: dict, key: str, meta: dict, bodies: dict | None = None):
    # A tiny redo journal makes body + metadata recoverable across process failure.
    _write_json(_state(project, key + ".pending"), {"metadata": meta, "bodies": bodies or {}})
    _recover(project, key)


def _new(project: dict, kind: str, start: str, ids: list[str]) -> dict:
    _, end = identity(kind, start)
    now = stamp()
    return {"schema_version": 1, "owner_project_id": None if is_workspace(project) else project["id"],
            "scope": "workspace" if is_workspace(project) else "project", "kind": kind,
            "period_start": start, "period_end": end, "project_ids": ids,
            "created_at": now, "updated_at": now, "draft": None, "candidate": None,
            "previous_draft": None, "versions": [], "last_input_fingerprint": "",
            "generation": {"requested": False, "state": "idle", "run_id": None,
                           "input_fingerprint": "", "started_at": None, "expires_at": None,
                           "attempts": 0, "retry_after": None, "last_error": None}}


def _meta(project: dict, key: str) -> dict:
    meta = _json(_state(project, key + ".json"))
    if meta is None:
        raise ReportError("报告不存在。", "NOT_FOUND", 404)
    return meta


def _entry(body: str, human: bool, **kw) -> dict:
    return {"sha256": digest(body), "human_edited": human, "updated_at": stamp(),
            "generation_id": None, "sources": [], "coverage_until": None, "gaps": [], **kw}


def _read_body(project: dict, key: str, name: str) -> str:
    path = _body_path(project, key, name)
    if not path.is_file():
        raise ReportError("报告正文文件缺失，已保留元数据。", "MISSING_BODY", 409)
    return body_text(path.read_text(encoding="utf-8"))


def _current(project: dict, key: str, meta: dict) -> tuple[str, str, str]:
    name = "draft.md" if meta["draft"] is not None else (f'v{meta["versions"][-1]["number"]:04}.md' if meta["versions"] else "")
    body = _read_body(project, key, name) if name else ""
    entry = meta["draft"] if meta["draft"] is not None else (meta["versions"][-1] if meta["versions"] else {})
    return body, digest([project["id"], key, name, digest(body), entry.get("comments", [])]), name


def url(project_id: str, kind: str, start: str) -> str:
    return f"/reports/{kind}/{start}" if project_id in (None, WORKSPACE_ID) else f"/project/{project_id}/reports/{kind}/{start}"


def _loaded(project: dict, key: str, meta: dict, view: str = "", version: int | None = None) -> dict:
    body, revision, name = _current(project, key, meta)
    selected = meta["draft"] if meta["draft"] is not None else (meta["versions"][-1] if meta["versions"] else None)
    if view and version is not None:
        raise ReportError("不能同时选择候选和历史版本。")
    if view:
        if view not in {"candidate", "previous"}:
            raise ReportError("报告视图无效。")
        selected = meta["candidate" if view == "candidate" else "previous_draft"]
        name = "candidate.md" if view == "candidate" else "previous-draft.md"
    elif version is not None:
        selected = next((v for v in meta["versions"] if v["number"] == version), None)
        name = f"v{version:04}.md"
    if (view or version is not None) and selected is None:
        raise ReportError("所选版本不存在。", "NOT_FOUND", 404)
    if name:
        body = _read_body(project, key, name)
    return {"ok": True, "revision": revision, "body": body, "comments": (selected or {}).get("comments", []),
            "mode": "readonly" if view or version is not None else ("draft" if meta["draft"] is not None else "formal"),
            "metadata": meta, "selected_metadata": selected, "body_sha256": digest(body),
            "externally_changed": bool(selected and selected["sha256"] != digest(body)),
            "has_candidate": meta["candidate"] is not None,
            "url": url(project["id"], meta["kind"], meta["period_start"])}


def load(project: dict, kind: str, start: str, *, view: str = "", version: int | None = None) -> dict:
    key, _ = identity(kind, start)
    with locked(project, key):
        return _loaded(project, key, _meta(project, key), view, version)


def create(project: dict, kind: str, start: str, project_ids=None, home=None) -> dict:
    key, _ = identity(kind, start)
    ids = project_ids if project_ids is not None else (report_settings(home)["project_ids"] if is_workspace(project) else [project["id"]])
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
        raise ReportError("请选择参与项目。")
    ids = list(dict.fromkeys(ids))
    if not is_workspace(project) and (project["id"] not in ids or (kind == "daily" and ids != [project["id"]])):
        raise ReportError("日报限当前项目，周报必须包含归档项目。")
    for pid in ids:
        project_for(pid, home)
    with locked(project, key):
        if _state(project, key + ".json").exists():
            return _loaded(project, key, _meta(project, key))
        meta = _new(project, kind, start, ids)
        meta["draft"] = _entry("", True, project_ids=ids)
        _commit(project, key, meta, {"draft.md": ""})
        return _loaded(project, key, meta)


def update(project: dict, kind: str, start: str, payload: dict, *, home=None) -> dict:
    key, _ = identity(kind, start)
    with locked(project, key):
        meta = _meta(project, key)
        current, revision, _ = _current(project, key, meta)
        if payload.get("expected_revision") != revision:
            raise ReportError("正文已在另一窗口或文件中修改，请比较后再保存。", "REVISION_CONFLICT", 409)
        action, bodies = payload.get("action"), {}
        if action == "save":
            if meta["draft"] is None:
                raise ReportError("请先进入编辑，创建修订草稿。")
            body = body_text(payload.get("body"))
            from research_notebook import validate_comments
            comments = validate_comments(payload.get("comments", meta["draft"].get("comments", [])))
            if len(json.dumps(comments, ensure_ascii=False).encode("utf-8")) + len(body.encode("utf-8")) > MAX_DOCUMENT:
                raise ReportError("正文与批注超过 2 MiB。")
            if body == current and comments == meta["draft"].get("comments", []):
                return _loaded(project, key, meta)
            meta["draft"] = {**meta["draft"], "sha256": digest(body), "human_edited": True, "updated_at": stamp(), "comments": comments}
            bodies["draft.md"] = body
        elif action == "start_edit":
            if meta["draft"] is not None:
                return _loaded(project, key, meta)
            if not meta["versions"]:
                raise ReportError("没有可修订的正式版。")
            meta["draft"] = {**meta["versions"][-1], "sha256": digest(current), "human_edited": True, "updated_at": stamp()}
            bodies["draft.md"] = current
        elif action == "confirm":
            if meta["draft"] is None or not current.strip():
                raise ReportError("非空草稿才能确认。")
            if "<!--report-upload:" in current:
                raise ReportError("请先完成或移除待上传图片。")
            number = len(meta["versions"]) + 1
            meta["versions"].append({**meta["draft"], "number": number, "sha256": digest(current), "confirmed_at": stamp()})
            bodies[f"v{number:04}.md"] = current
            meta["draft"] = None
        elif action in {"restore", "adopt_candidate"}:
            if action == "adopt_candidate":
                source = meta["candidate"]
                if source is None:
                    raise ReportError("没有新候选。")
                body = _read_body(project, key, "candidate.md")
                if payload.get("expected_candidate_sha256") != digest(body):
                    raise ReportError("候选已更新，请重新查看。", "CANDIDATE_CONFLICT", 409)
            else:
                source = next((v for v in meta["versions"] if v["number"] == payload.get("version")), None)
                if source is None:
                    raise ReportError("历史版本不存在。", "NOT_FOUND", 404)
                body = _read_body(project, key, f'v{source["number"]:04}.md')
            if meta["draft"] is not None:
                meta["previous_draft"] = {**meta["draft"], "sha256": digest(current)}
                bodies["previous-draft.md"] = current
            kept_comments = (meta["draft"] or (meta["versions"][-1] if meta["versions"] else {})).get("comments", [])
            meta["draft"] = {**source, "sha256": digest(body), "human_edited": True, "updated_at": stamp()}
            if action == "adopt_candidate":
                meta["draft"]["comments"] = kept_comments
            bodies["draft.md"] = body
            meta["project_ids"] = source.get("project_ids", meta["project_ids"])
            if action == "adopt_candidate":
                meta["candidate"] = None
        elif action == "regenerate":
            if not set(meta["project_ids"]).issubset(report_settings(home)["project_ids"]):
                raise ReportError("请先在设置中选择报告涉及的项目。", "PROJECT_NOT_ENABLED", 409)
            meta["generation"].update(requested=True, attempts=0, retry_after=None)
        else:
            raise ReportError("不支持的报告操作。")
        meta["updated_at"] = stamp()
        _commit(project, key, meta, bodies)
        return _loaded(project, key, meta)


def publish(project: dict, kind: str, start: str, body: str, *, generation_id: str, sources: list,
            fingerprint: str, project_ids: list[str], coverage_until: str, gaps: list, _locked: bool = False, _finish_signature: str | None = None) -> dict:
    """Internal commit, called only after the generation protocol validates its run."""
    body = body_text(body)
    if not body.strip():
        raise ReportError("生成正文不能为空。")
    key, _ = identity(kind, start)
    with (nullcontext() if _locked else locked(project, key)):
        meta = _json(_state(project, key + ".json")) or _new(project, kind, start, project_ids)
        draft = meta["draft"]
        # Actual disk changes count as human protection, even without a browser save.
        external = draft is not None and digest(_read_body(project, key, "draft.md")) != draft["sha256"]
        target = "candidate" if meta["versions"] or (draft is not None and (draft["human_edited"] or external)) else "draft"
        entry = _entry(body, False, generation_id=generation_id, sources=sources, coverage_until=coverage_until, gaps=gaps, project_ids=project_ids)
        same = meta[target] and meta[target]["sha256"] == entry["sha256"] and meta[target]["sources"] == sources
        if not same:
            meta[target] = entry
        if target == "draft":
            meta["project_ids"] = project_ids
        meta["last_input_fingerprint"] = fingerprint
        meta["generation"].update(requested=False, state="idle", last_error=None)
        meta["updated_at"] = stamp()
        revision = digest([project["id"], key, "draft.md", digest(body), meta["draft"].get("comments", [])]) if target == "draft" else _current(project, key, meta)[1]
        result = {"ok": True, "target": target, "revision": revision, "url": url(project["id"], kind, start), "changed": not bool(same)}
        if _finish_signature is not None:
            meta["finish_receipt"] = {"run_id": generation_id, "signature": _finish_signature, "result": result}
        _commit(project, key, meta, {} if same else {target + ".md": body})
        return result


def listing(project: dict, kind: str, *, home=None, offset: int = 0, limit: int = 30, query: str = "", project_filter: str = "", status_filter: str = "") -> dict:
    if kind not in {"daily", "weekly"}:
        raise ReportError("报告类型无效。")
    offset, limit = max(0, int(offset)), max(1, min(30, int(limit)))
    owners = ([project] + list_projects(home=home)["projects"]) if is_workspace(project) else ([project] if kind == "daily" else list_projects(home=home)["projects"])
    items = []
    for owner in owners:
        folder = _state(owner, "")
        for path in folder.glob(kind + "-????-??-??.json") if folder.exists() else []:
            safe_file(Path(owner["state_root"]), str(path.relative_to(Path(owner["state_root"]))))
            meta = _json(path)
            if not is_workspace(project) and project["id"] not in meta["project_ids"]:
                continue
            if project_filter and project_filter not in meta["project_ids"]:
                continue
            if is_workspace(project) and not (meta["draft"] or meta["versions"] or meta["candidate"]):
                continue
            title = ("日报 · " if kind == "daily" else "周报 · ") + meta["period_start"]
            if is_workspace(project) and not is_workspace(owner):
                title += " · 历史项目报告（" + owner["name"] + "）"
            if status_filter == "formal" and (not meta["versions"] or meta["draft"] is not None):
                continue
            if status_filter == "draft" and meta["draft"] is None:
                continue
            if query and query.lower() not in title.lower():
                continue
            status = "修订草稿" if meta["draft"] is not None and meta["versions"] else ("草稿" if meta["draft"] is not None else ("正式版" if meta["versions"] else "等待整理"))
            items.append({"key": path.stem, "title": title, "period_start": meta["period_start"],
                          "period_end": meta["period_end"], "status": status, "owner": owner["id"], "project_ids": meta["project_ids"],
                          "scope": "workspace" if is_workspace(owner) else "project",
                          "link": url(owner["id"], kind, meta["period_start"])})
    items.sort(key=lambda item: (item["period_start"], item["owner"]), reverse=True)
    return {"ok": True, "items": items[offset:offset + limit], "total": len(items), "has_more": offset + limit < len(items)}


def navigation_context(home: str, params: dict, fallback: str | None = None) -> str:
    """The browsing project is independent of report ownership/participation."""
    listed = list_projects(home=home)
    selected = (params.get("context") or [fallback])[0]
    if selected is None:
        selected = listed.get("current_project_id")
    return selected if any(p["id"] == selected for p in listed["projects"]) else ""


def tabs(project_id: str, active: str, navigation: dict | None = None) -> str:
    # Project research notes no longer host report tabs. Old repo links still work.
    if active == 'records':
        return ''
    from urllib.parse import urlencode
    from research_web_ui import esc
    return '<nav class="report-tabs" aria-label="报告类型">' + ''.join(
        f'<a href="{esc("/reports?" + urlencode({**(navigation or {}), "view": key}))}" aria-current="{"page" if key == active else "false"}">{label}</a>'
        for key, label in (("daily", "日报"), ("weekly", "周报"))) + '</nav>'


def list_page(home: str, project: dict, kind: str, params: dict) -> str:
    from research_web_ui import esc, layout, page_header, new_button, ui_icon
    from urllib.parse import urlencode
    query = (params.get('q') or [''])[0]
    project_filter = (params.get('project') or [''])[0]
    status_filter = (params.get('status') or [''])[0]
    context = navigation_context(home, params)
    navigation = dict(context=context, view=kind, q=query, project=project_filter, status=status_filter)
    offset = int((params.get('offset') or [0])[0])
    result = listing(project, kind, home=home, offset=offset, query=query,
                     project_filter=project_filter, status_filter=status_filter)
    label = '日报' if kind == 'daily' else '周报'
    back = '/reports?' + urlencode({**navigation, 'offset': offset})
    rows = ''.join(
        f'<a id="report-row-{index}" class="report-row rw-list-row" href="{esc(i["link"] + "?" + urlencode({"context": context, "return": back + "#report-row-" + str(index)}))}">'
        f'{ui_icon("reports")}<span class="rw-list-main"><span class="rw-title-button">{esc(i["title"])}</span><span class="rw-list-meta"><span>{esc(i["period_start"])}</span><span>{"跨项目汇总" if i.get("scope") == "all" or len(i.get("project_ids", [])) > 1 else "项目报告"}</span></span></span><span class="report-status status-{"formal" if i["status"] == "正式版" else "draft"}">{esc(i["status"])}</span><span class="rw-icon-button">{ui_icon("open")}</span></a>'
        for index, i in enumerate(result['items']))
    if not rows:
        rows = f'<p class="muted">还没有{label}。这里保存多项目汇总，可新建或等待已配置的自动整理。</p>'
    today = datetime.now(CST).date()
    if kind == 'weekly':
        today -= timedelta(days=today.weekday())
    projects = list_projects(home=home)['projects']
    chosen = report_settings(home)['project_ids'] or [p['id'] for p in projects]
    options = ''.join(f'<label><input type="checkbox" name="project" value="{esc(p["id"])}" {"checked" if p["id"] in chosen else ""}>{esc(p["name"])}</label>' for p in projects)
    filters = '<option value="">全部项目</option>' + ''.join(f'<option value="{esc(p["id"])}" {"selected" if p["id"] == project_filter else ""}>{esc(p["name"])}</option>' for p in projects)
    states = ''.join(f'<option value="{key}" {"selected" if key == status_filter else ""}>{value}</option>' for key, value in [('', '全部状态'), ('draft', '草稿'), ('formal', '正式版')])
    more = '<a class="button" href="?' + esc(urlencode({**navigation, 'offset': offset+30})) + '">下一页</a>' if result['has_more'] else ''
    body = f'<link rel="stylesheet" href="/static/reports.css"><section id="research-reports-list" data-project="{esc(project["id"])}" data-context="{esc(context)}" data-kind="{kind}" data-api="/api/reports">'
    body += page_header('日报与周报', f'<a class="rw-button" href="/settings#report-settings">{ui_icon("clock")}生成设置</a>' + new_button('新建' + label, element_id='report-new'))
    body += f'<form class="rw-filter-row report-filters">{tabs(project["id"], kind, navigation)}<input type="hidden" name="context" value="{esc(context)}"><input type="hidden" name="view" value="{kind}"><div class="report-filter-controls"><label>项目<select name="project" aria-label="筛选参与项目">{filters}</select></label><select name="status" aria-label="筛选报告状态">{states}</select><details class="report-search" {"open" if query else ""}><summary class="rw-icon-button" aria-label="搜索报告" title="搜索报告">{ui_icon("search")}</summary><div><input type="search" name="q" value="{esc(query)}" placeholder="搜索报告" aria-label="搜索报告"><button>搜索</button></div></details></div></form><div class="rw-list">{rows}</div>{more}<p class="report-list-note">日报详尽留痕，周报按你的模板提炼。确认后才成为正式版。</p>'
    body += f'<dialog id="report-create"><form method="dialog"><h2>新建{label}</h2><label>{"周一日期" if kind == "weekly" else "日期"}<input type="date" id="report-date" value="{today}" required></label><fieldset><legend>参与项目（汇总为一篇）</legend>{options}</fieldset><p id="report-create-error" role="alert"></p><button value="cancel">取消</button><button type="button" id="report-create-submit">创建</button></form></dialog></section><script src="/static/reports.js" defer></script>'
    return layout('日报与周报', body, project_id=context, active='reports', home=home, report_query=navigation)


def editor_page(home: str, project_id: str, kind: str, start: str, params: dict | None = None) -> str:
    from research_web_ui import esc, layout
    from urllib.parse import urlencode
    project = report_owner(project_id, home)
    context = navigation_context(home, params or {}, None if is_workspace(project) else project_id)
    api_base = "/api/reports" if is_workspace(project) else f"/api/project/{project_id}/reports"
    identity(kind, start)
    load(project, kind, start)  # Never create reports as a side effect of GET.
    title = ("日报 · " if kind == "daily" else "周报 · ") + start
    from document_editor import editor_markup
    body = (f'<section id="research-report" data-project="{esc(project_id)}" data-context="{esc(context)}" data-kind="{kind}" data-date="{start}" data-api="{api_base}">'
            + editor_markup("report", title, "/reports?" + urlencode(dict(view=kind, context=context)))
            + '</section><script src="/static/reports.js" defer></script>')
    return layout(title, body, project_id=context, active="reports", home=home, report_query={"view": kind})

# Host generation protocol: deterministic inputs/results, never an embedded model.
def report_settings(home=None) -> dict:
    from llmwiki_registry import llmwiki_home
    path = safe_file(llmwiki_home(home), 'reports-settings.json')
    defaults = {'schema_version': 1, 'enabled': False, 'knowledge_enabled': False, 'literature_enabled': False, 'timezone': 'Asia/Shanghai',
                'project_ids': [], 'daily_time': None, 'weekly_weekday': None, 'weekly_time': None,
                'weekly_owner_project_id': None, 'start_date': None, 'activity_db_paths': {}, 'capture_hosts': []}
    stored = _json(path, {})
    values = {key: stored.get(key, value) for key, value in defaults.items()}
    values['weekly_owner_project_id'] = None  # Read-only compatibility field; new reports never have an owner.
    runtime = stored.get('runtime') or {}
    return {'ok': True, **values, 'report_scope': 'workspace', 'reports_url': '/reports', 'revision': digest(values), 'runtime': runtime, 'config_path': str(path.resolve()),
            'workflow_cli': str(Path(__file__).with_name('research_cycle.py').resolve()),
            'workflow_skill': str(Path(__file__).resolve().parents[1] / 'skills/llmwiki-research-record/SKILL.md'),
            'connection': 'paused' if not values['enabled'] else ('configured' if runtime.get('automation_id') and runtime.get('target_thread_id') and str(runtime.get('status', 'ACTIVE')).upper() == 'ACTIVE' else 'pending')}


def save_settings(payload: dict, home=None) -> dict:
    from llmwiki_registry import _home_lock, llmwiki_home
    root = llmwiki_home(home)
    with _home_lock(root):
        current = report_settings(home)
        keys = {'schema_version', 'enabled', 'knowledge_enabled', 'literature_enabled', 'timezone', 'project_ids', 'daily_time', 'weekly_weekday',
                'weekly_time', 'weekly_owner_project_id', 'start_date', 'activity_db_paths', 'capture_hosts'}
        if set(payload) - keys - {'expected_revision'}:
            raise ReportError('不能从网页修改执行端回执。')
        if payload.get('expected_revision') != current['revision']:
            raise ReportError('设置已更新，请重新加载。', 'REVISION_CONFLICT', 409)
        config = {k: payload.get(k, current[k]) for k in keys}
        if any(type(config[k]) is not bool for k in ('enabled', 'knowledge_enabled', 'literature_enabled')) or config['timezone'] != 'Asia/Shanghai' or config['schema_version'] != 1:
            raise ReportError('报告配置无效。')
        ids = config['project_ids']
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ReportError('项目列表无效。')
        config['project_ids'] = list(dict.fromkeys(ids))
        for pid in ids:
            project_for(pid, home)
        for key in ('daily_time', 'weekly_time'):
            value = config[key]
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value)):
                raise ReportError('请输入 HH:mm 时间。')
        weekday = config['weekly_weekday']
        if weekday is not None and (type(weekday) is not int or not 1 <= weekday <= 7):
            raise ReportError('周报日期必须为周一至周日。')
        # Old clients may still send an owner. It never affects workspace storage.
        config['weekly_owner_project_id'] = None
        if config['start_date'] is not None:
            identity('daily', config['start_date'])
        if not isinstance(config['capture_hosts'], list) or any(h != 'codex' for h in config['capture_hosts']):
            raise ReportError('当前仅支持已验证的本机会话适配器。')
        if not isinstance(config['activity_db_paths'], dict):
            raise ReportError('活动库配置无效。')
        if 'project_ids' in payload and 'activity_db_paths' not in payload:
            config['activity_db_paths'] = {pid: path for pid, path in config['activity_db_paths'].items() if pid in ids}
        for pid, path in config['activity_db_paths'].items():
            project = project_for(pid, home)
            if pid not in ids or not isinstance(path, str):
                raise ReportError('活动库必须属于已选项目。')
            try:
                relative = Path(path).resolve().relative_to(Path(project['state_root']).resolve())
                safe_file(Path(project['state_root']), relative.as_posix())
            except ValueError as exc:
                raise ReportError('活动库必须在对应项目状态目录内。') from exc
        if config['enabled']:
            if not ids or any(config[k] is None for k in ('daily_time', 'weekly_time', 'weekly_weekday')):
                raise ReportError('启用前请填写参与项目和日报/周报时间。')
            config['start_date'] = config['start_date'] or datetime.now(CST).date().isoformat()
        if 'capture_hosts' in payload or (config['capture_hosts'] and ids != current['project_ids']):
            from research_capture_runtime import configure_capture
            for pid in set(current['project_ids']) - set(config['project_ids']):
                configure_capture(project_for(pid, home), [])
            for pid in config['project_ids']:
                project = project_for(pid, home)
                configure_capture(project, config['capture_hosts'])
                if config['capture_hosts']:
                    config['activity_db_paths'][pid] = str(safe_file(Path(project['state_root']), 'workbench/runtime.sqlite3'))
        _write_json(safe_file(root, 'reports-settings.json'), {**config, 'runtime': current['runtime']})
    return report_settings(home)


def _event_day(value: str | None) -> str | None:
    if not value:
        return None
    try:
        event = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return event.astimezone(CST).date().isoformat() if event.tzinfo else None
    except (ValueError, AttributeError):
        return None


def collect_sources(project: dict, day: str, *, now: datetime | None = None, home=None) -> tuple[list, list]:
    """Project evidence only; missing or partially read sources remain explicit gaps."""
    from research_records import _load_records_from_path
    from research_capture import redact
    from research_notebook import MARKER
    now = now or datetime.now(timezone.utc)
    identity('daily', day)
    items, gaps = [], []
    wiki = Path(project['wiki_root'])

    def add(kind, locator, text, occurred_at, certainty='event'):
        clean, count = redact(text)
        if count:
            gaps.append('部分材料包含敏感字段，已脱敏。')
        revision = digest(clean)
        items.append({'id': digest([project['id'], kind, locator, revision]), 'role': 'source',
                      'project_id': project['id'], 'kind': kind, 'occurred_at': occurred_at,
                      'observed_at': now.isoformat(), 'locator': locator, 'revision': revision,
                      'text': clean, 'certainty': certainty})

    records_root = safe_file(wiki, 'records')
    for path in sorted(records_root.rglob('*.md')) if records_root.exists() else []:
        relative = path.relative_to(wiki).as_posix()
        if relative.startswith('records/reports/'):
            continue
        try:
            safe_file(wiki, relative)
            entries = _load_records_from_path(path, wiki)
            for entry in entries:
                event = entry.get('recorded_at')
                manual = relative.startswith('records/manual/')
                text = MARKER.sub('', entry['content'])
                if manual:
                    from research_records import _frontmatter
                    metadata, _ = _frontmatter(path.read_text(encoding='utf-8'))
                    event = metadata.get('updated_at') or metadata.get('recorded_at')
                if _event_day(event) != day:
                    continue
                if manual:
                    text = '本日修改的笔记；无旧快照，不能将全文视为本日新增成果。\n\n' + text
                add('manual_note' if manual else 'research_record', entry['id'], text, event)
        except (LLMWikiError, OSError, ValueError, KeyError):
            gaps.append('部分科研记录无法读取，未计入覆盖。')
    try:
        tasks = _json(safe_file(wiki, '.research-progress/tasks.json'), {'tasks': []})['tasks']
        for task in tasks:
            for index, event in enumerate(task.get('history', [])):
                if _event_day(event.get('at')) == day:
                    add('task_event', f'task:{task["id"]}:{index}', '用户操作的任务历史（状态不是实验验证）：\n' + json.dumps(event, ensure_ascii=False), event['at'])
    except (LLMWikiError, OSError, ValueError, KeyError, TypeError):
        gaps.append('任务历史不可用。')
    from research_sources import extra_sources
    extra, missing = extra_sources(project, day, settings=report_settings(home), now=now)
    items.extend(extra)
    gaps.extend(missing)
    for item in items:
        item['project_name'] = project['name']
    return items, sorted(set(gaps))


def _report_sources(owner: dict, kind: str, start: str, ids: list, home, now: datetime) -> tuple[list, list]:
    items, gaps = [], []
    if kind == 'daily':
        for pid in ids:
            project = project_for(pid, home)
            raw, missing = collect_sources(project, start, now=now, home=home)
            items.extend(raw)
            gaps.extend(f'{project["name"]}：{gap}' for gap in missing)
        return items, sorted(set(gaps))
    # One workspace daily per date; never read the same multi-project text once per project.
    for offset in range(7):
        day = (date.fromisoformat(start) + timedelta(days=offset)).isoformat()
        if day > now.astimezone(CST).date().isoformat():
            gaps.append(f'{day}：未来日期未覆盖。')
            continue
        covered = set()
        owners = [workspace(home)] + [project_for(pid, home) for pid in ids]
        for daily_owner in owners:
            if not is_workspace(daily_owner) and daily_owner['id'] in covered:
                continue
            try:
                report = load(daily_owner, 'daily', day)
                if report['metadata']['versions']:
                    report = load(daily_owner, 'daily', day, version=report['metadata']['versions'][-1]['number'])
            except ReportError as exc:
                if exc.status != 404:
                    gaps.append(f'{day}：部分日报无法读取，回退原始材料。')
                continue
            selected = report['selected_metadata']
            # Old scope or withdrawn projects must not leak via a mixed daily document.
            included = (selected or {}).get('project_ids', report['metadata']['project_ids'])
            if not selected or not report['body'].strip() or not set(included).issubset(ids):
                continue
            revision = digest(report['body'])
            locator = f'report:daily:{day}:' + (f'v{selected["number"]}' if 'number' in selected and report['mode'] == 'readonly' else 'draft')
            items.append({'id': digest([daily_owner['id'], locator, revision]), 'role': 'source',
                          'project_id': None if is_workspace(daily_owner) else daily_owner['id'],
                          'project_ids': included, 'kind': 'daily_report', 'occurred_at': day,
                          'observed_at': now.isoformat(), 'locator': locator, 'revision': revision,
                          'text': report['body'], 'certainty': 'event'})
            covered.update(included)
            gaps.extend(selected.get('gaps', []))
            if not report['metadata']['versions']:
                gaps.append(f'{day}：使用未确认日报草稿。')
        for pid in ids:
            if pid not in covered:
                project = project_for(pid, home)
                raw, missing = collect_sources(project, day, now=now, home=home)
                items.extend(raw)
                gaps.extend(f'{project["name"]}：{gap}' for gap in missing)
    return items, sorted(set(gaps))


def _run_file(home, run_id: str) -> tuple[dict, Path, dict]:
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ReportError('运行 ID 无效。')
    for project in [workspace(home)] + list_projects(home=home)['projects']:
        path = _state(project, f'runs/{run_id}.json')
        if path.is_file():
            return project, path, _json(path)
    raise ReportError('运行不存在。', 'NOT_FOUND', 404)


def _conversation_auth(project, settings):
    from research_capture import consent_revision
    from research_capture_runtime import session_bindings
    return digest([consent_revision(project), session_bindings(project), settings.get('capture_hosts'),
                   settings.get('activity_db_paths', {}).get(project['id']), settings.get('start_date')])


def _authorize_run(run: dict, settings: dict, now: datetime, home=None):
    if not settings['enabled'] or settings['connection'] != 'configured' or not set(run['project_ids']).issubset(settings['project_ids']):
        raise ReportError('报告已暂停或项目授权已取消。', 'PROJECT_NOT_ENABLED', 409)
    if run.get('scope') != 'workspace' or run['owner_project_id'] is not None or run['project_ids'] != settings['project_ids']:
        raise ReportError('报告范围已变化，请重新计划。', 'PROJECT_NOT_ENABLED', 409)
    for pid, signature in run.get('conversation_auth', {}).items():
        if _conversation_auth(project_for(pid, home), settings) != signature:
            raise ReportError('对话取材授权已变化，请重新计划。', 'SOURCE_FORBIDDEN', 403)
    if datetime.fromisoformat(run['expires_at']) <= now:
        raise ReportError('本轮整理已过期，请重新计划。', 'RUN_EXPIRED', 409)


def report_plan(home=None, max_reports: int = 3, *, now: datetime | None = None) -> dict:
    from uuid import uuid4
    settings = report_settings(home)
    if not settings['enabled'] or settings['connection'] != 'configured':
        return {'ok': True, 'runs': [], 'pending_count': 0, 'gaps': ['报告自动整理已暂停。' if not settings['enabled'] else '待连接宿主执行端。']}
    if type(max_reports) is not int or not 1 <= max_reports <= 3:
        raise ReportError('每轮最多整理 3 篇报告。')
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(CST).date()
    start_date = date.fromisoformat(settings['start_date'])
    ids = settings['project_ids']
    candidates = []
    for pid in [WORKSPACE_ID]:
        p = workspace(home)
        folder = _state(p, '')
        for path in sorted(folder.glob('*.json')) if folder.exists() else []:
            if not re.fullmatch(r'(daily|weekly)-\d{4}-\d{2}-\d{2}\.json', path.name):
                continue
            meta = _json(path)
            if meta['generation']['requested'] and set(meta['project_ids']).issubset(ids):
                candidates.append((pid, meta['kind'], meta['period_start'], ids))
    for offset in range(14, -1, -1):
        day = today - timedelta(days=offset)
        if day < start_date or (offset == 0 and now.astimezone(CST).strftime('%H:%M') < settings['daily_time']):
            continue
        candidates.append((WORKSPACE_ID, 'daily', day.isoformat(), ids))
    monday = today - timedelta(days=today.weekday())
    for start in [monday - timedelta(days=7), monday]:
        due = start + timedelta(days=settings['weekly_weekday'] - 1)
        # Initial enablement must not invent missed weekly slots from before consent.
        if due < start_date:
            continue
        if today < due or (today == due and now.astimezone(CST).strftime('%H:%M') < settings['weekly_time']):
            continue
        candidates.append((WORKSPACE_ID, 'weekly', start.isoformat(), ids))
    unique = {}
    for candidate in candidates:
        unique.setdefault(tuple(candidate[:3]), candidate)
    runs, pending_count, gaps = [], 0, []
    blocked_daily = set()
    for pid, kind, start, project_ids in unique.values():
        if kind == 'weekly' and any(start <= day <= (date.fromisoformat(start) + timedelta(days=6)).isoformat() for day in blocked_daily):
            pending_count += 1
            continue
        project = report_owner(pid, home)
        key, end = identity(kind, start)
        # Material collection is intentionally outside every document lock.
        items, missing = _report_sources(project, kind, start, project_ids, home, now)
        template = (Path(__file__).resolve().parents[1] / 'templates/weekly-report.md').read_text(encoding='utf-8') if kind == 'weekly' else ''
        fingerprint = digest([project_ids, [(i['id'], i['revision']) for i in items], digest(template)])
        with locked(project, key):
            meta = _json(_state(project, key + '.json')) or _new(project, kind, start, project_ids)
            gen = meta['generation']
            if gen['state'] == 'running':
                expiry = datetime.fromisoformat(gen['expires_at'])
                if expiry > now:
                    if kind == 'daily':
                        blocked_daily.add(start)
                    continue
                gen.update(state='failed', retry_after=(expiry + timedelta(minutes=30)).isoformat(), last_error='RUN_EXPIRED')
                _commit(project, key, meta)
            if fingerprint == meta['last_input_fingerprint'] and not gen['requested']:
                continue
            if fingerprint != gen['input_fingerprint']:
                gen.update(attempts=0, retry_after=None, input_fingerprint=fingerprint)
            if gen['attempts'] >= 3:
                gaps.append(f'{pid} {start}：本输入已失败三次，等待手动重试。')
                continue
            if gen['retry_after'] and datetime.fromisoformat(gen['retry_after']) > now:
                if kind == 'daily':
                    blocked_daily.add(start)
                continue
            if not items:
                gen.update(state='no_evidence', requested=False)
                meta['last_input_fingerprint'] = fingerprint
                _commit(project, key, meta)
                continue
            if len(runs) >= max_reports:
                pending_count += 1
                if kind == 'daily':
                    blocked_daily.add(start)
                continue
            body, _, _ = _current(project, key, meta)
            if body:
                machine = meta['draft'] is not None and not meta['draft']['human_edited'] and digest(body) == meta['draft']['sha256']
                items.append({'id': 'previous', 'role': 'machine_previous' if machine else 'user_reference', 'text': body})
            if kind == 'weekly':
                items.append({'id': 'template', 'role': 'weekly_template', 'text': template})
            run_id = uuid4().hex
            run = {'run_id': run_id, 'owner_project_id': None, 'scope': 'workspace', 'kind': kind, 'period_start': start, 'period_end': end,
                   'project_ids': project_ids, 'input_fingerprint': fingerprint, 'expires_at': (now + timedelta(minutes=10)).isoformat(),
                   'items': items, 'coverage_until': now.isoformat(), 'gaps': missing, 'read_cursors': [], 'result': None,
                   'conversation_auth': {source_pid: _conversation_auth(project_for(source_pid, home), settings)
                                         for source_pid in {i['project_id'] for i in items if i.get('kind') == 'conversation'}}}
            _write_json(_state(project, f'runs/{run_id}.json'), run)
            gen.update(state='running', run_id=run_id, input_fingerprint=fingerprint, started_at=now.isoformat(), expires_at=run['expires_at'], attempts=gen['attempts'] + 1)
            _commit(project, key, meta)
            runs.append({k: v for k, v in run.items() if k not in {'items', 'read_cursors', 'result'}})
            if kind == 'daily':
                blocked_daily.add(start)
    return {'ok': True, 'runs': runs, 'pending_count': pending_count, 'gaps': gaps}


def _pages(items: list) -> list[list]:
    pages, page, size = [], [], 0
    for item in items:
        text = item['text']
        chunks = [text[i:i + 20000] for i in range(0, len(text), 20000)] or ['']
        for index, chunk in enumerate(chunks):
            if page and size + len(chunk) > 20000:
                pages.append(page)
                page, size = [], 0
            page.append({**item, 'text': chunk, 'part': index + 1, 'parts': len(chunks)})
            size += len(chunk)
    if page:
        pages.append(page)
    return pages


def report_sources(run_id: str, cursor=None, home=None, *, now: datetime | None = None) -> dict:
    project, path, run = _run_file(home, run_id)
    key, _ = identity(run['kind'], run['period_start'])
    with locked(project, key):
        run = _json(path)
        _authorize_run(run, report_settings(home), now or datetime.now(timezone.utc), home)
        pages = _pages(run['items'])
        try:
            number = 0 if cursor is None else int(cursor)
        except (ValueError, TypeError) as exc:
            raise ReportError('来源分页游标无效。') from exc
        if number < 0 or number >= len(pages):
            raise ReportError('来源分页游标无效。')
        run['read_cursors'] = sorted(set(run['read_cursors'] + [number]))
        _write_json(path, run)
        return {'ok': True, 'items': pages[number], 'coverage_until': run['coverage_until'], 'gaps': run['gaps'],
                'next_cursor': str(number + 1) if number + 1 < len(pages) else None}


def report_finish(run_id: str, outcome: str, body=None, source_ids=None, source_summaries=None,
                  error_code=None, home=None, *, now: datetime | None = None) -> dict:
    project, path, run = _run_file(home, run_id)
    key, _ = identity(run['kind'], run['period_start'])
    with locked(project, key):
        run = _json(path)
        now = now or datetime.now(timezone.utc)
        settings = report_settings(home)
        _authorize_run(run, settings, now, home)
        signature = digest([outcome, body, source_ids, source_summaries, error_code])
        if run['result'] is not None:
            if signature != run['finish_signature']:
                raise ReportError('本轮已提交其他结果。', 'RUN_FINISHED', 409)
            return run['result']
        if outcome not in {'generated', 'no_evidence', 'failed'}:
            raise ReportError('整理结果类型无效。')
        meta = _meta(project, key)
        receipt = meta.get('finish_receipt', {})
        if receipt.get('run_id') == run_id:
            if receipt['signature'] != signature:
                raise ReportError('本轮已提交其他结果。', 'RUN_FINISHED', 409)
            run['result'], run['finish_signature'] = receipt['result'], signature
            _write_json(path, run)
            return run['result']
        if meta['generation']['run_id'] != run_id or meta['generation']['state'] != 'running':
            raise ReportError('本轮运行已被替换。', 'RUN_EXPIRED', 409)
        if outcome != 'failed' and len(run['read_cursors']) != len(_pages(run['items'])):
            raise ReportError('请先读取所有来源分页，不得把截断材料当完整覆盖。')
        if outcome == 'generated':
            body_text(body)
            available = {i['id']: i for i in run['items'] if i['role'] == 'source'}
            if not isinstance(source_ids, list) or not source_ids or any(not isinstance(i, str) for i in source_ids):
                raise ReportError('生成稿必须提供本轮实际依据。')
            if len(set(source_ids)) != len(source_ids) or not set(source_ids).issubset(available):
                raise ReportError('来源 ID 重复或不属于本轮。')
            if not isinstance(source_summaries, dict) or set(source_summaries) != set(source_ids) or any(not isinstance(v, str) or not v.strip() or len(v) > 1000 for v in source_summaries.values()):
                raise ReportError('每条实际来源必须包含不超过 1000 字的摘要。')
            if run['kind'] == 'weekly':
                start, end = date.fromisoformat(run['period_start']), date.fromisoformat(run['period_end'])
                title = f'# 刘亚宁周报（{start:%Y年%m月%d日}—{end:%m月%d日}）'
                headings = re.findall(r'^## (.+)$', body, re.M)
                if not body.startswith(title + '\n') or headings != ['本周工作', '下周计划', '需要协调与帮助'] or re.search(r'【.*?】|^#+ 周报风格说明', body, re.M):
                    raise ReportError('生成周报不符合用户模板。', 'INVALID_GENERATED_REPORT')
            sources = [{k: v for k, v in available[sid].items() if k not in {'text', 'role'}} | {'summary': source_summaries[sid]} for sid in source_ids]
            result = publish(project, run['kind'], run['period_start'], body, generation_id=run_id, sources=sources,
                             fingerprint=run['input_fingerprint'], project_ids=run['project_ids'], coverage_until=run['coverage_until'], gaps=run['gaps'], _locked=True, _finish_signature=signature)
        else:
            with nullcontext():
                meta = _meta(project, key)
                gen = meta['generation']
                if outcome == 'failed':
                    gen.update(state='failed', last_error=error_code if isinstance(error_code, str) and re.fullmatch('[A-Z_]{1,80}', error_code) else 'GENERATION_FAILED', retry_after=(now + timedelta(minutes=30)).isoformat())
                else:
                    gen.update(state='no_evidence', requested=False, last_error=None)
                    meta['last_input_fingerprint'] = run['input_fingerprint']
                result = {'ok': True, 'target': 'none', 'revision': _current(project, key, meta)[1], 'url': url(project['id'], run['kind'], run['period_start'])}
                meta['finish_receipt'] = {'run_id': run_id, 'signature': signature, 'result': result}
                _commit(project, key, meta)
        run['result'], run['finish_signature'] = result, signature
        _write_json(path, run)
        return result


def settings_section(home: str) -> str:
    from research_web_ui import esc
    projects = list_projects(home=home)['projects']
    checks = ''.join(f'<label class="check-label"><input name="report-project" type="checkbox" value="{esc(p["id"])}">{esc(p["name"])}</label>' for p in projects)
    return f'''<details class="settings-section" id="report-settings"><summary>报告自动整理</summary>
<p id="report-connection" class="muted">读取配置中…</p>
<form id="report-settings-form"><label class="check-label"><input type="checkbox" id="report-capture">接入所选项目的本机对话（仅从授权后开始）</label><label class="check-label"><input type="checkbox" id="knowledge-enabled">同时维护知识库</label><label class="check-label"><input type="checkbox" id="literature-enabled">同时收录项目文献</label><label class="check-label"><input type="checkbox" id="report-enabled">允许自动整理所选项目（还需连接宿主计划）</label>
<fieldset><legend>参与项目</legend>{checks}</fieldset>
<label>日报时间（北京时间）<input type="time" id="report-daily-time"></label>
<label>周报日期<select id="report-weekday"><option value="">请选择</option>{''.join(f'<option value="{i}">周{label}</option>' for i, label in enumerate('一二三四五六日', 1))}</select></label>
<label>周报时间（北京时间）<input type="time" id="report-weekly-time"></label>
<p class="muted">日报、周报均在独立栏目保存；每个周期汇总所选项目，不归档到任何项目。</p>
<label>开始日期<input type="date" id="report-start-date"></label>
<div class="actions"><button>保存设置</button><button type="button" id="report-copy-enable">复制连接指令</button></div><p id="report-settings-status" role="status"></p>
<p class="muted">使用记录、任务历史和只读 Git 材料；勾选对话取材后仅读取关联所选项目的本机会话文字，不读取其他项目或网页聊天，不回填授权前历史。未适配的宿主会明确显示缺口。保存配置不会创建后台进程。</p></form></details><script src="/static/reports.js" defer></script>'''


# Shared saved-record access for literature. No raw-chat/account scan, model or
# report 14-day window. Pagination and frozen batching belong to the consumer.
def _literature_record_entries(project, relative, now):
    from research_records import _load_records_from_path, _frontmatter
    from research_capture import redact
    from research_notebook import MARKER
    if not isinstance(relative, str) or not relative.startswith("records/") or relative.startswith("records/reports/") or not relative.endswith(".md"):
        raise ReportError("文献取材仅限项目已保存科研记录。", "SOURCE_FORBIDDEN", 403)
    wiki = Path(project["wiki_root"])
    target = safe_file(wiki, relative)
    if target.is_symlink():
        raise ReportError("不读取符号链接记录。", "SOURCE_FORBIDDEN", 403)
    if not target.exists():
        return []
    if target.stat().st_size > MAX_DOCUMENT:
        raise ReportError("记录过大，未计入覆盖。", "SOURCE_UNAVAILABLE", 409)
    raw = target.read_text(encoding="utf-8")
    metadata, _ = _frontmatter(raw)
    if metadata.get("project_id") not in (None, "", project["id"]):
        raise ReportError("记录属于其他项目。", "SOURCE_FORBIDDEN", 403)
    if metadata.get("automation_self") in (True, "true"):
        return []
    entries = _load_records_from_path(target, wiki)
    if not entries:
        raise ReportError("记录无法解析，不视为已删除。", "SOURCE_UNAVAILABLE", 409)
    result = []
    for entry in entries:
        manual = relative.startswith("records/manual/")
        event = (metadata.get("updated_at") or metadata.get("recorded_at")) if manual else entry.get("recorded_at")
        text, redactions = redact(MARKER.sub("", entry["content"]))
        locator = relative + ("#" + entry["entry_key"] if entry.get("entry_key") else "")
        kind = "notebook" if manual else "record"
        revision = digest([text, event])
        result.append({"id": digest([project["id"], kind, locator, revision]),
                       "project_id": project["id"], "kind": kind, "locator": locator,
                       "revision": revision, "text": text, "occurred_at": event or None,
                       "observed_at": now.isoformat(), "certainty": "event" if event else "unknown",
                       "redacted": bool(redactions)})
    return result


def _literature_in_range(item, start_date, now):
    try:
        stamp = item.get("occurred_at")
        if not stamp and item.get("certainty") in {"observation", "observed", "verified_range"}:
            stamp = item.get("observed_at")
        event = datetime.fromisoformat(stamp)
        return event.tzinfo is not None and date.fromisoformat(start_date) <= event.astimezone(CST).date() and event <= now
    except (TypeError, ValueError):
        return False


def literature_inventory(project, *, start_date, now, home, known_sources=None):
    project = project_for(project["id"], home)
    root = safe_file(Path(project["wiki_root"]), "records")
    items, gaps = [], []
    for path in sorted(root.rglob("*.md")) if root.exists() else []:
        relative = path.relative_to(Path(project["wiki_root"])).as_posix()
        if relative.startswith("records/reports/"):
            continue
        try:
            for item in _literature_record_entries(project, relative, now):
                if not _literature_in_range(item, start_date, now):
                    gaps.append("部分记录不在启用日期范围或缺少可信时间，未计入覆盖。")
                    continue
                if item["redacted"]:
                    gaps.append("敏感字段已在冻结前脱敏。")
                items.append({k: v for k, v in item.items() if k not in {"text", "redacted"}})
        except (LLMWikiError, OSError, ValueError, UnicodeError):
            gaps.append("部分记录无法读取或超限，未计入覆盖。")
    from research_sources import activity_sources
    conversations, missing = activity_sources(project, settings=report_settings(home), start_date=start_date,
                                             now=now, known_sources=known_sources)
    gaps.extend(missing)
    items.extend({k: v for k, v in item.items() if k not in {"text", "role", "redacted"}} for item in conversations)
    return items, sorted(set(gaps))


def literature_read_source(project, descriptor, *, start_date, now, home):
    project = project_for(project["id"], home)
    if descriptor.get("project_id") != project["id"] or descriptor.get("kind") not in {"record", "notebook", "conversation"}:
        raise ReportError("文献来源项目或类型不符。", "SOURCE_FORBIDDEN", 403)
    locator = descriptor.get("locator", "")
    if descriptor["kind"] == "conversation":
        from research_sources import read_activity_source, ActivitySourceUnavailable
        try:
            item = read_activity_source(project, locator, settings=report_settings(home), now=now)
        except ActivitySourceUnavailable as exc:
            raise ReportError(str(exc), "SOURCE_FORBIDDEN", 403) from exc
    else:
        entries = _literature_record_entries(project, locator.split("#", 1)[0], now)
        item = next((e for e in entries if e["locator"] == locator and e["kind"] == descriptor["kind"]), None)
    if item is not None and not _literature_in_range(item, start_date, now):
        raise ReportError("来源时间不在授权范围。", "SOURCE_FORBIDDEN", 403)
    return item


def literature_authorize_source(project, source, *, start_date, now, home):
    try:
        return literature_read_source(project, source, start_date=start_date, now=now, home=home) is not None
    except (LLMWikiError, OSError, ValueError, UnicodeError):
        return False
