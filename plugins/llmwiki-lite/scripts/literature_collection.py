"""Deterministic literature plan/sources/finish. Never collects chats or starts workers.

The second-change source helper is deliberately an explicit integration boundary:
``literature_inventory``, ``literature_read_source``, ``literature_authorize_source``
in research_reports (see verification.md). If absent, fail closed and disclose gaps.
Internal tests can inject a provider implementing inventory/read/authorize; this is
not a production connector and is never evidence of host automation being bound.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime, timedelta, timezone

from llmwiki_registry import list_projects, llmwiki_home
from literature_catalog import (
    HEX_ID, LiteratureCatalog, LiteratureCatalogError, atomic_json, item_revision,
    project_for, safe_path, short_lock, source_ref, _canonical_json, _new_id,
    parse_locator, _normalize_doi, _normalize_arxiv, _normalize_url,
)

CST = timezone(timedelta(hours=8))
SOURCE_GAP = "第二份三类来源 helper 未接通：未读取或覆盖对话、科研记录与手动笔记。"
MAX_BODY = 2 * 1024 * 1024
KINDS = {"conversation", "record", "notebook"}
ERRORS = {"MODEL_FAILED", "READ_FAILED", "WRITE_FAILED", "RUN_EXPIRED", "SOURCE_UNAVAILABLE", "INTERNAL_ERROR"}


def _now(now):
    value = now or datetime.now(timezone.utc)
    if value.utcoffset() is None:
        raise LiteratureCatalogError("clock 必须包含时区。")
    return value.astimezone(timezone.utc)


def _settings(home):
    # Read-only shared settings; this module never invents or persists binding receipts.
    path = safe_path(llmwiki_home(home), "reports-settings.json")
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def _binding(settings):
    runtime = settings.get("runtime") or {}
    if not isinstance(runtime, dict):
        return False
    return bool(runtime.get("automation_id") and runtime.get("target_thread_id") and runtime.get("literature_bound_at") and runtime.get("status") not in {"PAUSED", "paused", "deleted"})


def _authorized(project_id, settings, now):
    if settings.get("enabled") is not True or settings.get("literature_enabled") is not True or project_id not in settings.get("project_ids", []) or not _binding(settings):
        raise LiteratureCatalogError("文献收录已关闭、项目未授权或共享任务未连接。", "FORBIDDEN", 403)
    try:
        start = date.fromisoformat(settings["start_date"])
        if start > now.astimezone(CST).date() or settings.get("timezone") != "Asia/Shanghai" or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", settings["daily_time"]):
            raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise LiteratureCatalogError("共享任务日期或时刻设置无效。", "FORBIDDEN", 403) from exc


class ReportSourceProvider:
    """No fallback account scanning and no 14-day report-window reuse."""
    def __init__(self):
        import research_reports
        self.module = research_reports
        self.available = all(callable(getattr(research_reports, name, None)) for name in (
            "literature_inventory", "literature_read_source", "literature_authorize_source"))

    def inventory(self, project, *, start_date, now, home):
        if not self.available:
            return [], [SOURCE_GAP]
        known = {s["locator"]: s["checked_revision"] for s in _state(project)["checked_sources"].values()
                 if s.get("kind") == "conversation" and s.get("locator") and s.get("checked_revision")}
        return self.module.literature_inventory(project, start_date=start_date, now=now, home=home,
                                                known_sources=known)

    def read(self, project, descriptor, *, start_date, now, home):
        if not self.available:
            raise LiteratureCatalogError(SOURCE_GAP, "SOURCE_UNAVAILABLE", 409)
        return self.module.literature_read_source(project, descriptor, start_date=start_date, now=now, home=home)

    def authorize(self, project, source, *, start_date, now, home):
        return self.available and self.module.literature_authorize_source(project, source, start_date=start_date, now=now, home=home) is True


def _path(project, name):
    return safe_path(project["state_root"], "literature-collection/" + name)


def _lock(project):
    return short_lock(_path(project, ".lock"))


def _read(path, default):
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise LiteratureCatalogError("收录状态无法读取，未覆盖原状态。", "STATE_UNREADABLE", 409) from exc


def _state(project):
    return _read(_path(project, "state.json"), {
        "schema_version": 1, "project_id": project["id"], "last_daily_slot": None,
        "last_checked_at": None, "last_updated_at": None, "last_attempt_at": None,
        "pending_sources": [], "checked_sources": {}, "active_run": None,
        "retry_requested": False, "failure_fingerprint": None, "failure_count": 0,
        "retry_after": None, "last_result": "no_change", "last_error": None, "gaps": [],
    })


def _save_state(project, state):
    atomic_json(_path(project, "state.json"), state)


def _source_key(source):
    return item_revision([source["kind"], source["locator"]])


def _meta(source):
    return {key: source.get(key) for key in ("id", "project_id", "kind", "occurred_at", "observed_at", "locator", "revision", "certainty")}


def _validate_source(source, project, start_date):
    if not isinstance(source, dict) or source.get("project_id") != project["id"] or source.get("kind") not in KINDS:
        raise LiteratureCatalogError("来源项目或类型越权。", "SOURCE_FORBIDDEN", 403)
    for key in ("id", "locator", "revision", "certainty"):
        if not isinstance(source.get(key), str) or not source[key] or len(source[key]) > 4096:
            raise LiteratureCatalogError("来源身份不完整。", "SOURCE_FORBIDDEN", 403)
    # Source IDs belong to the helper; part IDs additionally freeze content revision.
    occurred = source.get("occurred_at")
    observed = source.get("observed_at")
    timestamp = occurred or (observed if source.get("certainty") in {"observation", "observed", "event", "verified_range"} else None)
    try:
        moment = datetime.fromisoformat(timestamp) if timestamp else None
        if moment is None or moment.utcoffset() is None:
            return "来源无可核验时间，未计入启用范围。"
        if moment.astimezone(CST).date() < date.fromisoformat(start_date):
            return "before_start"
    except (ValueError, TypeError):
        return "来源时间无效，未计入覆盖。"
    # Defense in depth; helper must also exclude automation/ignored/redacted content.
    locator = source["locator"].replace("\\", "/")
    if locator.startswith(("records/reports/", "reports/", "knowledge/", ".research-knowledge/")) or source.get("automation_self"):
        raise LiteratureCatalogError("不能从报告、知识或自动化自身取材。", "SOURCE_FORBIDDEN", 403)
    return None


def _failure(project, state, run, code, now, *, persist=True):
    state["last_result"], state["last_error"] = "failed", code
    if state["failure_fingerprint"] != run["input_fingerprint"]:
        state["failure_fingerprint"], state["failure_count"] = run["input_fingerprint"], 0
    state["failure_count"] += 1
    state["retry_after"] = (now + timedelta(minutes=30)).isoformat()
    state["active_run"] = None
    if persist:
        _save_state(project, state)


def _parts(source):
    text = source.get("text")
    if not isinstance(text, str):
        raise LiteratureCatalogError("来源不可读，不能冒充删除。", "READ_FAILED", 409)
    count = max(1, (len(text) + 19999) // 20000)
    for index in range(count):
        yield {**_meta(source), "source_id": source["id"], "id": item_revision([source["id"], source["revision"], index]),
               "part_index": index, "part_count": count, "text": text[index * 20000:(index + 1) * 20000]}


def _clock(value):
    # An explicit clock stays deterministic; the real clock is re-read after I/O.
    return lambda: _now(value)


def _pending_fingerprint(pending):
    return item_revision([[s["id"], s["kind"], s["locator"], s["revision"], s.get("done_parts", [])]
                          for s in pending])


def _inventory_fingerprint(project, settings):
    return item_revision(["inventory", project["id"], settings["start_date"], settings.get("activity_db_paths", [])])


def _can_attempt(state, fingerprint, now):
    return (state["retry_requested"] or fingerprint != state["failure_fingerprint"] or
            (state["failure_count"] < 3 and
             (not state["retry_after"] or now >= datetime.fromisoformat(state["retry_after"]))))


def _reset_budget(state, fingerprint):
    if state["retry_requested"] or fingerprint != state["failure_fingerprint"]:
        state.update(failure_fingerprint=fingerprint, failure_count=0, retry_after=None)
    state["retry_requested"] = False


def _complete(project, path, run, state):
    # Write the recovery envelope first. An interrupted state write is replayed once.
    run["completed_state"] = state
    run.pop("items", None)
    atomic_json(path, run)
    _save_state(project, state)
    run.pop("completed_state", None)
    atomic_json(path, run)


def _recover(project, state, now, *, persist=True):
    active = state.get("active_run")
    if not active:
        return state
    if not isinstance(active, str) or not HEX_ID.fullmatch(active):
        raise LiteratureCatalogError("收录领取状态无效。", "STATE_UNREADABLE", 409)
    path = _path(project, f"runs/{active}.json")
    run = _read(path, {})
    if run.get("run_id") != active or run.get("project_id") != project["id"]:
        raise LiteratureCatalogError("收录领取文件缺失或不匹配。", "STATE_UNREADABLE", 409)
    if run.get("completed_state"):
        state = copy.deepcopy(run["completed_state"])
        if persist:
            _save_state(project, state)
            run.pop("completed_state")
            atomic_json(path, run)
    elif now >= datetime.fromisoformat(run["expires_at"]):
        _failure(project, state, run, "RUN_EXPIRED", datetime.fromisoformat(run["expires_at"]), persist=False)
        if persist:
            _complete(project, path, run, state)
    return state


def _after_io(project, run, state, home, now, settings):
    current = _check_run(project, run, state, home, now)
    registered = project_for(project["id"], home)
    if current != settings or any(registered[k] != project[k] for k in ("source_root", "wiki_root", "state_root")):
        raise LiteratureCatalogError("取材期间授权或项目存储已变化，请重新检查。", "FORBIDDEN", 403)


def _inventory_pending(project, state, inventory, start_date, *, refresh=False):
    known = {_source_key(s): s for s in state["pending_sources"]}
    pending = dict(known) if refresh else {}
    for source in inventory:
        reason = _validate_source(source, project, start_date)
        if reason:
            if reason != "before_start":
                state["gaps"].append(reason)
            continue
        key = _source_key(source)
        if refresh:
            # Inventory omissions are not proof of deletion. Never add late locators.
            if key in known and source["revision"] != known[key]["revision"]:
                pending[key] = {**_meta(source), "done_parts": []}
                state["gaps"].append("排队来源已变化；旧版本无法恢复：" + source["locator"][:500])
        else:
            checked = state["checked_sources"].get(key, {})
            if checked.get("checked_revision") != source["revision"]:
                pending[key] = {**_meta(source), "done_parts": checked.get("done_parts", []) if checked.get("revision") == source["revision"] else []}
    state["pending_sources"] = sorted(pending.values(), key=lambda s: (s.get("occurred_at") or s.get("observed_at") or "", s["locator"]))


def _prepare_run(project, home, provider, clock):
    with _lock(project):
        now, settings = clock(), _settings(home)
        _authorized(project["id"], settings, now)
        state = _recover(project, _state(project), now)
        if state["active_run"]:
            return None
        slot = now.astimezone(CST).date().isoformat()
        pending = bool(state["pending_sources"])
        if not pending and (state["last_daily_slot"] == slot or now.astimezone(CST).strftime("%H:%M") < settings["daily_time"]):
            return None
        fingerprint = _pending_fingerprint(state["pending_sources"]) if pending else _inventory_fingerprint(project, settings)
        if (not pending or state.get("inventory_failed")) and not _can_attempt(state, fingerprint, now):
            return None
        # A short, fenced preparation lease prevents two planners from reading the
        # same input concurrently. No source helper is ever called with this lock.
        run_id = _new_id()
        run = {"run_id": run_id, "project_id": project["id"], "slot": state["last_daily_slot"] if pending else slot,
               "input_fingerprint": fingerprint, "expires_at": (now + timedelta(minutes=10)).isoformat(),
               "phase": "preparing", "start_date": settings["start_date"], "finish_digest": None, "receipt": None}
        path = _path(project, f"runs/{run_id}.json")
        atomic_json(path, run)
        state["active_run"] = run_id
        _save_state(project, state)
        local = copy.deepcopy(state)
        blocked = not _can_attempt(state, fingerprint, now)

    frozen, selected_chars, read_error = [], 0, False
    inventory_failed = False
    try:
        # Refresh only after a failure to detect a new revision even at the retry
        # ceiling. This metadata probe never consumes another read/model attempt.
        if not pending or state["failure_count"]:
            inventory_failed = True
            inventory, source_gaps = provider.inventory(project, start_date=settings["start_date"], now=clock(), home=home)
            local["gaps"] = list(dict.fromkeys(str(g)[:1000] for g in source_gaps))[:100]
            _inventory_pending(project, local, inventory, settings["start_date"], refresh=pending)
            inventory_failed = False
            if not pending:
                local["last_daily_slot"] = slot
        fingerprint = _pending_fingerprint(local["pending_sources"]) if local["pending_sources"] else fingerprint
        with _lock(project):
            state = _recover(project, _state(project), clock())
            _after_io(project, run, state, home, clock(), settings)
            if not _can_attempt(state, fingerprint, clock()):
                state.update(active_run=None, pending_sources=local["pending_sources"], gaps=local["gaps"], inventory_failed=False)
                _save_state(project, state)
                return None
            _reset_budget(state, fingerprint)
            run["input_fingerprint"] = fingerprint
            state.update(pending_sources=local["pending_sources"], gaps=local["gaps"], inventory_failed=False,
                         last_daily_slot=local["last_daily_slot"], last_attempt_at=clock().isoformat())
            atomic_json(path, run)
            _save_state(project, state)
            local = copy.deepcopy(state)
            blocked = False
        remaining = []
        for descriptor in local["pending_sources"]:
            remaining.append(descriptor)
            if len(frozen) >= 20 or selected_chars >= 100000:
                continue
            source = provider.read(project, _meta(descriptor), start_date=settings["start_date"], now=clock(), home=home)
            if source is None:  # Only positively verified deletion may return None.
                remaining.pop()
                local["gaps"].append("来源已删除；历史版本无法恢复：" + descriptor["locator"][:500])
                continue
            reason = _validate_source(source, project, settings["start_date"])
            if reason:
                remaining.pop()
                local["gaps"].append(reason)
                continue
            if _source_key(source) != _source_key(descriptor):
                raise LiteratureCatalogError("来源定位变化。", "SOURCE_FORBIDDEN", 403)
            if not provider.authorize(project, source, start_date=settings["start_date"], now=clock(), home=home):
                raise LiteratureCatalogError("来源读取未授权。", "SOURCE_FORBIDDEN", 403)
            if source["revision"] != descriptor["revision"]:
                local["gaps"].append("排队来源已变化；旧版本无法恢复：" + descriptor["locator"][:500])
                descriptor.update(_meta(source))
                descriptor["done_parts"] = []
            for part in _parts(source):
                descriptor["part_count"] = part["part_count"]
                if part["part_index"] in descriptor["done_parts"]:
                    continue
                if len(frozen) >= 20 or selected_chars + len(part["text"]) > 100000:
                    break
                frozen.append(part)
                selected_chars += len(part["text"])
        local["pending_sources"] = remaining
    except Exception:
        read_error = True

    with _lock(project):
        state = _recover(project, _state(project), clock())
        # A late helper must not resurrect an expired lease or overwrite a successor.
        if state["active_run"] != run_id:
            return None
        try:
            _after_io(project, run, state, home, clock(), settings)
        except LiteratureCatalogError:
            state["active_run"] = None
            _save_state(project, state)
            return None
        state.update(pending_sources=local["pending_sources"], last_daily_slot=local["last_daily_slot"],
                     inventory_failed=inventory_failed, gaps=list(dict.fromkeys(local["gaps"]))[:100])
        if read_error:
            if not blocked:
                # Metadata-only input identity works even when no text was readable.
                if state["pending_sources"]:
                    run["input_fingerprint"] = _pending_fingerprint(state["pending_sources"])
                _reset_budget(state, run["input_fingerprint"])
                state["last_attempt_at"] = clock().isoformat()
                _failure(project, state, run, "READ_FAILED", clock(), persist=False)
                _complete(project, path, run, state)
            else:
                state["active_run"] = None
                _save_state(project, state)
            return None
        if not frozen:
            state.update(active_run=None, last_checked_at=clock().isoformat(), last_result="partial" if state["gaps"] else "no_change",
                         last_error=None, failure_count=0, retry_after=None)
            _save_state(project, state)
            return None
        fingerprint = _pending_fingerprint(state["pending_sources"])
        _reset_budget(state, fingerprint)
        run.update(phase="ready", input_fingerprint=fingerprint, items=frozen, read_pages=[], gaps=state["gaps"])
        atomic_json(path, run)
        state["last_error"] = None
        _save_state(project, state)
        return {k: run[k] for k in ("run_id", "project_id", "slot", "input_fingerprint", "expires_at")}


def literature_plan(home=None, max_projects=3, *, now=None, source_provider=None):
    clock = _clock(now)
    if type(max_projects) is not int or not 1 <= max_projects <= 3:
        raise LiteratureCatalogError("max_projects 须为1至3。")
    settings, provider = _settings(home), source_provider or ReportSourceProvider()
    projects, gaps, runs = [], [], []
    for candidate in list_projects(home=home)["projects"]:
        try:
            _authorized(candidate["id"], settings, clock())
            project = project_for(candidate["id"], home)
            state = _state(project)
            projects.append((state.get("last_daily_slot") or "", state.get("last_attempt_at") or "", project["id"], project))
        except LiteratureCatalogError as exc:
            if exc.code != "FORBIDDEN":
                gaps.append({"project_id": candidate["id"], "message": str(exc)})
    for _, _, _, project in sorted(projects):
        if len(runs) >= max_projects:
            break
        try:
            run = _prepare_run(project, home, provider, clock)
            if run:
                runs.append(run)
        except LiteratureCatalogError as exc:
            if exc.code != "FORBIDDEN":
                gaps.append({"project_id": project["id"], "message": str(exc)})
    # Count every persisted queue, including projects not selected by max_projects.
    pending_count = 0
    for _, _, _, project in projects:
        state = _state(project)
        pending_count += len(state["pending_sources"])
        gaps.extend({"project_id": project["id"], "message": g} for g in state["gaps"])
    return {"ok": True, "runs": runs, "pending_count": pending_count, "gaps": gaps[:100]}


def _find_run(run_id, home):
    if not isinstance(run_id, str) or not HEX_ID.fullmatch(run_id):
        raise LiteratureCatalogError("run_id 须为32位小写 hex。")
    for registered in list_projects(home=home)["projects"]:
        path = _path(registered, f"runs/{run_id}.json")
        if path.is_file():
            project = project_for(registered["id"], home)
            run = _read(path, {})
            if run.get("project_id") != project["id"] or run.get("run_id") != run_id:
                raise LiteratureCatalogError("run 项目越权。", "SOURCE_FORBIDDEN", 403)
            return project, path
    raise LiteratureCatalogError("收录任务不存在。", "NOT_FOUND", 404)


def _check_run(project, run, state, home, now):
    settings = _settings(home)
    _authorized(project["id"], settings, now)
    if state["active_run"] != run["run_id"] or now >= datetime.fromisoformat(run["expires_at"]):
        raise LiteratureCatalogError("任务已到期或不再有效。", "RUN_EXPIRED", 409)
    if settings["start_date"] != run["start_date"]:
        raise LiteratureCatalogError("来源授权范围已变化。", "FORBIDDEN", 403)
    return settings


def _pages(items):
    pages, page, size = [], [], 0
    for item in items:
        if page and size + len(item["text"]) > 20000:
            pages.append(page)
            page, size = [], 0
        page.append(item)
        size += len(item["text"])
    if page:
        pages.append(page)
    return pages


def literature_sources(run_id, cursor=None, home=None, *, now=None, source_provider=None):
    clock = _clock(now)
    project, path = _find_run(run_id, home)
    provider = source_provider or ReportSourceProvider()
    with _lock(project):
        run, state = _read(path, {}), _state(project)
        settings = _check_run(project, run, state, home, clock())
        if run.get("phase") == "preparing":
            raise LiteratureCatalogError("来源仍在读取。", "SOURCE_UNAVAILABLE", 409)
        pages = _pages(run["items"])
        cursors = [item_revision([run_id, i]) for i in range(len(pages))]
        if cursor is None:
            index = 0
        elif isinstance(cursor, str) and cursor in cursors:
            index = cursors.index(cursor)
        else:
            raise LiteratureCatalogError("分页游标无效。")
    if any(not provider.authorize(project, item, start_date=settings["start_date"], now=clock(), home=home) for item in pages[index]):
        raise LiteratureCatalogError("来源权限已撤回。", "SOURCE_FORBIDDEN", 403)
    with _lock(project):
        run, state = _read(path, {}), _state(project)
        _after_io(project, run, state, home, clock(), settings)
        if index not in run["read_pages"]:
            run["read_pages"].append(index)
            atomic_json(path, run)
        return {"ok": True, "items": pages[index], "coverage": {"read_pages": len(run["read_pages"]), "total_pages": len(pages)},
                "gaps": run["gaps"], "next_cursor": cursors[index + 1] if index + 1 < len(cursors) else None}


def _evidence_keys(text):
    # A candidate's exact normalized identity must occur, not arbitrary substring text.
    tokens = re.findall(r"https?://[^\s<>\"']+|(?:doi:\s*)?10\.\d{4,9}/[^\s<>\"']+|(?:arxiv:\s*)?(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v\d+)?", text, re.I)
    keys = set()
    for token in tokens:
        token = token.rstrip(".,;。；，")
        while token.endswith(")") and token.count(")") > token.count("("):
            token = token[:-1]
        token = token.rstrip("]}，。；")
        try:
            ids, url = parse_locator(token)
            keys.add(("url", _normalize_url(url)))
            keys.update((kind, value) for kind, value in ids.items() if value)
        except LiteratureCatalogError:
            continue
    return keys


def _check_evidence(candidate, sources):
    if not isinstance(candidate, dict):
        raise LiteratureCatalogError("候选必须有来源证据。", "INVALID_EVIDENCE", 403)
    ids, evidence = candidate.get("source_ids"), candidate.get("evidence")
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not isinstance(evidence, dict) or set(evidence) != set(ids):
        raise LiteratureCatalogError("来源与证据映射不完整。", "INVALID_EVIDENCE", 403)
    keys = set()
    for source_id in ids:
        excerpt = evidence[source_id]
        if source_id not in sources or not isinstance(excerpt, str) or not 1 <= len(excerpt) <= 1000 or excerpt not in sources[source_id]["text"]:
            raise LiteratureCatalogError("来源越权或摘录不属于冻结原文。", "INVALID_EVIDENCE", 403)
        keys |= _evidence_keys(excerpt)
    # Field-format errors can skip just one candidate; forged valid identities abort all.
    try:
        identity, url = parse_locator(candidate.get("locator"))
    except LiteratureCatalogError:
        return
    required = {(kind, value) for kind, value in identity.items() if value}
    if not required:
        required.add(("url", _normalize_url(url)))
    for kind, normalize in (("doi", _normalize_doi), ("arxiv", _normalize_arxiv)):
        value = candidate.get(kind)
        if value and normalize(value):
            required.add((kind, normalize(value)))
    if not required <= keys:
        raise LiteratureCatalogError("候选的每个标识都须在原文摘录中出现。", "INVALID_EVIDENCE", 403)


class _ReplayCatalog(LiteratureCatalog):
    """A bounded write intent in the existing run, not a new catalog transaction log."""
    def __init__(self, project, path, run, index, now):
        super().__init__(project["wiki_root"])
        self.run_path, self.run, self.index, self.now = path, run, index, now
        self.statistics_unproven = False

    def _save_catalog(self, data, expected_revision=None):
        # upsert already holds the catalog lock; record proof before the atomic write.
        before = {item["id"]: item for item in self._load_catalog()["items"]}
        item = next(item for item in data["items"] if item != before.get(item["id"]))
        old = before.get(item["id"])
        prior_refs = {item_revision(ref) for ref in (old or {}).get("source_refs", [])}
        intent = {"index": self.index, "item_id": item["id"], "action": "updated" if old else "created",
                  "before_revision": item_revision(old) if old else None,
                  "after_revision": item_revision(item), "changed_at": self.now.isoformat(),
                  "new_refs": [item_revision(ref) for ref in item.get("source_refs", []) if item_revision(ref) not in prior_refs]}
        self.run["applying"] = intent
        atomic_json(self.run_path, self.run)
        super()._save_catalog(data, expected_revision)

    def applied_intent(self):
        intent = self.run.get("applying")
        if not intent or intent["index"] != self.index:
            return None
        data = self._load_catalog()
        items = data["items"] + [d.get("item", {}) for d in data["dismissed_candidates"]]
        item = next((item for item in items if item.get("id") == intent["item_id"]), None)
        if item and (intent["action"] == "created" or item_revision(item) == intent["after_revision"] or
                     (intent["new_refs"] and set(intent["new_refs"]) <= {item_revision(r) for r in item.get("source_refs", [])})):
            return intent
        # A metadata-only write followed by a human edit may erase the only proof.
        # Do not invent a count or timestamp, and do not silently call it exact.
        self.statistics_unproven = bool(item and intent["action"] == "updated" and item_revision(item) != intent.get("before_revision"))
        return None


def _existing_receipt(project, path, run, state, digest):
    if run.get("finish_digest") and run["finish_digest"] != digest:
        raise LiteratureCatalogError("同一 run 不能提交不同结果。", "RECEIPT_CONFLICT", 409)
    if run.get("receipt"):
        if run.get("completed_state") and state.get("active_run") == run["run_id"]:
            _save_state(project, run["completed_state"])
            run.pop("completed_state")
            atomic_json(path, run)
        return run["receipt"]
    return None


def literature_finish(run_id, outcome, candidates=None, error_code=None, home=None, *, now=None, source_provider=None):
    clock = _clock(now)
    request = {"outcome": outcome, "candidates": candidates, "error_code": error_code}
    try:
        size = len(_canonical_json(request))
    except (ValueError, TypeError) as exc:
        raise LiteratureCatalogError("finish JSON 无效。") from exc
    if size > MAX_BODY:
        raise LiteratureCatalogError("finish 超过2 MiB。", "TOO_LARGE", 413)
    if outcome not in {"reviewed", "failed"} or (outcome == "reviewed" and candidates is not None and not isinstance(candidates, list)):
        raise LiteratureCatalogError("finish outcome 或 candidates 无效。")
    if outcome == "failed" and (candidates or error_code not in ERRORS):
        raise LiteratureCatalogError("失败回执仅接受安全错误码，不接受正文或候选。")
    digest = item_revision(request)
    project, path = _find_run(run_id, home)
    provider = source_provider or ReportSourceProvider()
    with _lock(project):
        run, state = _read(path, {}), _state(project)
        receipt = _existing_receipt(project, path, run, state, digest)
        if receipt:
            return receipt
        settings = _check_run(project, run, state, home, clock())
        if run.get("phase") == "preparing":
            raise LiteratureCatalogError("来源仍在读取。", "SOURCE_UNAVAILABLE", 409)
        if outcome == "failed":
            run["finish_digest"] = digest
            receipt = {"ok": True, "run_id": run_id, "project_id": project["id"], "outcome": "failed", "error_code": error_code, "results": [], "counts": {}, "url": f"/project/{project['id']}/literature"}
            _failure(project, state, run, error_code, clock(), persist=False)
            run["receipt"] = receipt
            _complete(project, path, run, state)
            return receipt
        pages = _pages(run["items"])
        if set(run["read_pages"]) != set(range(len(pages))):
            raise LiteratureCatalogError("必须读完本 run 的全部来源分页再提交。", "UNREAD_SOURCES", 409)
        sources = {item["id"]: item for item in run["items"]}
    # Source helpers may read files or a slow activity store. Never hold state/catalog
    # locks here. Each subsequent write rechecks the lease, settings and registered roots.
    for item in sources.values():
        if not provider.authorize(project, item, start_date=settings["start_date"], now=clock(), home=home):
            raise LiteratureCatalogError("来源权限已撤回。", "SOURCE_FORBIDDEN", 403)
    for candidate in candidates or []:
        _check_evidence(candidate, sources)
    with _lock(project):
        run, state = _read(path, {}), _state(project)
        receipt = _existing_receipt(project, path, run, state, digest)
        if receipt:
            return receipt
        _after_io(project, run, state, home, clock(), settings)
        run["finish_digest"] = digest
        atomic_json(path, run)
    allowed = {"locator", "title", "authors", "year", "doi", "arxiv", "source_ids", "evidence"}
    for candidate_index, candidate in enumerate(candidates or []):
        if any(not provider.authorize(project, sources[i], start_date=settings["start_date"], now=clock(), home=home) for i in candidate["source_ids"]):
            raise LiteratureCatalogError("来源权限已撤回。", "SOURCE_FORBIDDEN", 403)
        with _lock(project):
            run, state = _read(path, {}), _state(project)
            receipt = _existing_receipt(project, path, run, state, digest)
            if receipt:
                return receipt
            _after_io(project, run, state, home, clock(), settings)
            catalog = _ReplayCatalog(project, path, run, candidate_index, clock())
            proven = catalog.applied_intent()
            if proven:
                run["last_applied_at"] = max(run.get("last_applied_at") or "", proven["changed_at"])
            previous = run.get("applied_results", {}).get(str(candidate_index)) or proven
            try:
                if set(candidate) - allowed:
                    raise LiteratureCatalogError("每日候选不能绑定文件或更改人工状态。")
                refs = []
                for sid in candidate["source_ids"]:
                    source = sources[sid]
                    ref = source_ref(source["kind"], source["locator"], source["revision"], occurred_at=source.get("occurred_at"),
                                     observed_at=source.get("observed_at"), collected_at=clock().isoformat(), summary=candidate["evidence"][sid])
                    if ref["id"] not in {r["id"] for r in refs}:
                        refs.append(ref)
                result = catalog.upsert(**{k: v for k, v in candidate.items() if k not in {"source_ids", "evidence"}}, source_refs=refs, explicit=False)
                if result["action"] in {"created", "updated"}:
                    run["last_applied_at"] = clock().isoformat()
                if previous and previous.get("item_id") == result.get("item_id") and (
                        (result["action"] == "unchanged" and previous["action"] in {"created", "updated"}) or
                        (result["action"] == "updated" and previous["action"] == "created")):
                    result["action"] = previous["action"]
                if catalog.statistics_unproven and result["action"] in {"unchanged", "skipped"}:
                    result.setdefault("warnings", []).append({"code": "replay_statistics_unproven"})
                safe_result = {k: v for k, v in result.items() if k not in {"item", "item_revision", "ok"}}
            except LiteratureCatalogError as exc:
                if exc.code in {"LOCK_TIMEOUT", "CATALOG_UNREADABLE", "STORAGE_OVERLAP", "UNSAFE_PATH"}:
                    raise
                safe_result = {"action": "skipped", "reason": exc.code}
            run.setdefault("applied_results", {})[str(candidate_index)] = safe_result
            run.pop("applying", None)
            atomic_json(path, run)
    with _lock(project):
        run, state = _read(path, {}), _state(project)
        receipt = _existing_receipt(project, path, run, state, digest)
        if receipt:
            return receipt
        _after_io(project, run, state, home, clock(), settings)
        results = [run["applied_results"][str(i)] for i in range(len(candidates or []))]
        by_key = {}
        for source in sources.values():
            by_key.setdefault(_source_key(source), []).append(source)
        remaining = []
        for descriptor in state["pending_sources"]:
            key = _source_key(descriptor)
            parts = by_key.get(key, [])
            if parts:
                done = sorted(set(descriptor.get("done_parts", [])) | {p["part_index"] for p in parts})
                descriptor["done_parts"] = done
                checked = {"revision": descriptor["revision"], "done_parts": done,
                           "locator": descriptor["locator"], "kind": descriptor["kind"]}
                if len(done) == parts[0]["part_count"]:
                    checked["checked_revision"] = descriptor["revision"]
                else:
                    remaining.append(descriptor)
                state["checked_sources"][key] = checked
            else:
                remaining.append(descriptor)
        counts = {action: sum(r["action"] == action for r in results) for action in ("created", "updated", "unchanged", "skipped")}
        changed = bool(counts["created"] + counts["updated"])
        state.update(pending_sources=remaining, active_run=None, last_checked_at=clock().isoformat(),
                     last_result="partial" if counts["skipped"] or run["gaps"] or any(r.get("warnings") for r in results) else ("updated" if changed else "no_change"),
                     last_error=None, failure_count=0, retry_after=None)
        if run.get("last_applied_at"):
            state["last_updated_at"] = max(state.get("last_updated_at") or "", run["last_applied_at"])
        elif changed:  # Legacy runs can have applied_results without timestamps.
            state["last_updated_at"] = clock().isoformat()
        receipt = {"ok": True, "run_id": run_id, "project_id": project["id"], "outcome": "reviewed", "results": results, "counts": counts,
                   "url": f"/project/{project['id']}/literature"}
        run["receipt"] = receipt
        run["source_refs"] = [{**_meta(s), "id": s["id"], "summary": next((c["evidence"][s["id"]] for c in candidates or [] if s["id"] in c["evidence"]), "")} for s in sources.values()]
        run.pop("applying", None)
        _complete(project, path, run, state)
        return receipt


def collection_status(project_id, home=None):
    project = project_for(project_id, home)
    # Projection only: opening the status page never mutates a run or scans sources.
    state = _recover(project, _state(project), _now(None), persist=False)
    settings = _settings(home)
    enabled = settings.get("enabled") is True and settings.get("literature_enabled") is True and project_id in settings.get("project_ids", [])
    status = "disabled" if not enabled else ("pending_connection" if not _binding(settings) else ("running" if state["active_run"] else ("failed" if state["last_result"] == "failed" else "idle")))
    gaps = list(state["gaps"])
    if not ReportSourceProvider().available and SOURCE_GAP not in gaps:
        gaps.append(SOURCE_GAP)
    return {"ok": True, "status": status, "last_checked_at": state["last_checked_at"], "last_updated_at": state["last_updated_at"],
            "pending_count": len(state["pending_sources"]), "gaps": gaps[:100], "last_error": state["last_error"],
            "failure_count": state["failure_count"], "retry_after": state["retry_after"],
            "executor_status": "last_binding_recorded_not_live" if _binding(settings) else "not_connected"}


def collection_retry(project_id, home=None):
    project = project_for(project_id, home)
    with _lock(project):
        state = _recover(project, _state(project), _now(None))
        if state["active_run"] or state["last_result"] != "failed":
            raise LiteratureCatalogError("没有失败收录可重试。", "NO_FAILED_RUN", 409)
        state.update(retry_requested=True, failure_count=0, retry_after=None)
        _save_state(project, state)
    return {"ok": True, "accepted": True, "message": "已排入共享入口；关闭或未连接时不会读取材料。"}
