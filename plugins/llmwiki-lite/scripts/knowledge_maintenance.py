"""Deterministic knowledge maintenance. Only the host supplies reasoning."""

from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4
from llmwiki_core import (
    LLMWikiError,
    TEXT_EXTENSIONS,
    _walk_files,
    _ignored,
    _ignore_patterns,
    _frontmatter_sources,
)
from llmwiki_registry import list_projects
from research_notebook import _atomic, safe_file, reader_markdown
from research_reports import project_for, report_settings, CST

LOCK = threading.RLock()
SOURCE_LIMIT = 128 * 1024
PAGE_LIMIT = 256 * 1024
HEX = re.compile(r"[a-f0-9]{32}\Z")


class KnowledgeError(LLMWikiError):
    def __init__(self, message, code="INVALID_INPUT", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().isoformat(timespec="seconds")


def sha(value):
    if not isinstance(value, bytes):
        value = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True)
        ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def path(project, name):
    return safe_file(Path(project["state_root"]), "knowledge-maintenance/" + name)


def read_json(target, default=None):
    return (
        json.loads(target.read_bytes().decode("utf-8")) if target.exists() else default
    )


def write_json(target, value):
    _atomic(
        target, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


@contextmanager
def locked(project):
    with LOCK:
        target = path(project, ".lock")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a+b") as stream:
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise KnowledgeError(
                        "另一窗口正在保存，请重试。", "BUSY", 409
                    ) from exc
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def state(project):
    return read_json(
        path(project, "state.json"),
        {
            "schema_version": 1,
            "project_id": project["id"],
            "last_checked_at": None,
            "last_updated_at": None,
            "last_daily_slot": None,
            "last_attempt_at": None,
            "failure_fingerprint": None,
            "failure_count": 0,
            "sources": {},
            "event_cursor": 0,
            "page_sources": {},
            "proposal_index": {},
            "recheck_pages": [],
            "active_run": None,
            "pending_source_count": 0,
            "last_result": "no_change",
            "last_error": None,
            "gaps": ["尚未检查来源覆盖。"],
        },
    )


def save_state(project, value):
    write_json(path(project, "state.json"), value)


def _id(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise KnowledgeError("标识无效。")
    return value


def bounded(target, limit):
    if target.stat().st_size > limit:
        raise KnowledgeError("材料超过读取上限。", "TOO_LARGE", 413)
    raw = target.read_bytes()
    if len(raw) > limit:
        raise KnowledgeError("材料超过读取上限。", "TOO_LARGE", 413)
    return raw.decode("utf-8")


def page_target(project, relative, *, create=False):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise KnowledgeError("知识页路径无效。")
    parts = relative.split("/")
    if (
        any(p.startswith(".") or not p for p in parts)
        or parts[0].lower() in {"records", "literature", "papers"}
        or parts[-1].lower() == "index.md"
        or not relative.endswith(".md")
    ):
        raise KnowledgeError("不能维护记录、文献、隐藏目录或索引正文。")
    if create and not re.fullmatch(r"knowledge/[a-z0-9][a-z0-9-]{0,63}\.md", relative):
        raise KnowledgeError("新页须位于 knowledge/<ascii-slug>.md。")
    target = safe_file(Path(project["wiki_root"]), relative)
    if target.exists() and re.search(
        r"(?m)^paper_file\s*:", bounded(target, PAGE_LIMIT)
    ):
        raise KnowledgeError("阅读笔记不属于知识维护范围。")
    return target


def _source_path(project, locator):
    if not isinstance(locator, str) or ":" not in locator:
        raise KnowledgeError("来源定位无效。")
    kind, relative = locator.split(":", 1)
    if kind == "record":
        if any(p.startswith(".") for p in relative.split("/")):
            raise KnowledgeError("隐藏记录不纳入材料。")
        relative = relative.split("#", 1)[0]
        if (
            not relative.startswith("records/")
            or relative.startswith("records/reports/")
            or not relative.endswith(".md")
        ):
            raise KnowledgeError("记录来源无效。")
        return safe_file(Path(project["wiki_root"]), relative)
    if kind != "source":
        raise KnowledgeError("来源类别无效。")
    root = Path(project["source_root"]).resolve()
    target = safe_file(root, relative)
    if any(
        target.resolve().is_relative_to(Path(project[k]).resolve())
        for k in ("wiki_root", "state_root")
    ):
        raise KnowledgeError("生成资料不属于原始来源。")
    parts, patterns = Path(relative).parts, _ignore_patterns(root)
    if target.suffix.lower() not in TEXT_EXTENSIONS or any(
        _ignored("/".join(parts[: i + 1]), is_dir=i < len(parts) - 1, patterns=patterns)
        for i in range(len(parts))
    ):
        raise KnowledgeError("来源被忽略。")
    if any(
        re.search(r"(?i)(^\.env|credential|secret|token|private[-_]key)", p)
        for p in parts
    ):
        raise KnowledgeError("敏感配置不纳入材料。")
    return target


def source_item(project, locator):
    from research_capture import redact

    if locator.startswith("conversation:"):
        from research_sources import read_activity_source, ActivitySourceUnavailable
        try:
            item = read_activity_source(project, locator)
        except ActivitySourceUnavailable as exc:
            raise KnowledgeError(str(exc), "SOURCE_UNAVAILABLE", 409) from exc
        if item is None:
            raise KnowledgeError("对话来源不存在。", "SOURCE_UNAVAILABLE", 409)
        return {"source_id": sha(locator + "\n" + item["revision"]), "locator": locator,
                "revision": item["revision"], "text": item["text"],
                "redacted": item.get("redacted", False), "old_revision": None}
    raw = bounded(_source_path(project, locator), SOURCE_LIMIT)
    revision = sha(raw)
    text = raw
    if locator.startswith("record:"):
        from research_records import _load_records_from_path

        text = reader_markdown(raw, project["id"])
        if "#" in locator:
            entry = locator.split("#", 1)[1]
            records = _load_records_from_path(
                _source_path(project, locator), Path(project["wiki_root"])
            )
            match = next((r for r in records if r.get("entry_key") == entry), None)
            if match is None:
                raise KnowledgeError("记录条目不存在。")
            text = match["content"]
    text, count = redact(text)
    return {
        "source_id": sha(locator + "\n" + revision),
        "locator": locator,
        "revision": revision,
        "text": text,
        "redacted": bool(count),
        "old_revision": None,
    }


def scan(project, saved):
    items, gaps, seen = {}, [], set()
    source, wiki = Path(project["source_root"]), Path(project["wiki_root"])
    if not source.is_dir():
        raise KnowledgeError(
            "项目源目录不可读，不将不可读误作删除。", "SOURCE_UNAVAILABLE", 409
        )
    candidates = [
        ("source:" + p.relative_to(source).as_posix(), p) for p in _walk_files(source)
    ]
    records = safe_file(wiki, "records")
    candidates += (
        [("record:" + p.relative_to(wiki).as_posix(), p) for p in records.rglob("*.md")]
        if records.exists()
        else []
    )
    expanded = []
    from research_records import _load_records_from_path

    for locator, target in candidates:
        if locator.startswith("record:"):
            try:
                _source_path(project, locator)
                bounded(target, SOURCE_LIMIT)
                entries = _load_records_from_path(target, wiki)
                keyed = [e for e in entries if e.get("entry_key")]
                if keyed:
                    expanded.extend(
                        (locator + "#" + e["entry_key"], target) for e in keyed
                    )
                    continue
            except (LLMWikiError, OSError, UnicodeError, ValueError):
                pass
        expanded.append((locator, target))
    for locator, target in expanded:
        try:
            _source_path(project, locator)
        except (LLMWikiError, ValueError):
            if target.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
                gaps.append("图片/PDF未解析，不能仅凭文件名推断内容。")
            continue
        seen.add(locator)
        try:
            item = source_item(project, locator)
            item["old_revision"] = saved.get(locator, {}).get("checked_revision")
            items[locator] = item
            if item["redacted"]:
                gaps.append("部分来源的敏感字段已脱敏。")
        except (OSError, UnicodeError, LLMWikiError, ValueError):
            gaps.append("无法完整读取：" + locator)
    # Existing project activity storage only. Acquisition happens in the shared outer cycle.
    from research_sources import activity_sources
    known = {locator: old["checked_revision"] for locator, old in saved.items()
             if locator.startswith("conversation:") and old.get("checked_revision")}
    conversations, missing = activity_sources(project, settings=None, start_date="1970-01-01",
                                             now=now(), known_sources=known)
    gaps.extend(missing)
    for item in conversations:
        locator = item["locator"]
        seen.add(locator)
        items[locator] = {"source_id": sha(locator + "\n" + item["revision"]),
                          "locator": locator, "revision": item["revision"], "text": item["text"],
                          "redacted": item.get("redacted", False),
                          "old_revision": saved.get(locator, {}).get("checked_revision")}
    for locator, old in saved.items():
        if locator in seen:
            continue
        # Revocation, unavailable storage, or bounded coverage is NOT source deletion.
        if locator.startswith("conversation:"):
            continue
        try:
            target = _source_path(project, locator)
            if target.exists():
                if "#" not in locator or not locator.startswith("record:"):
                    continue
                bounded(target, SOURCE_LIMIT)
                entries = _load_records_from_path(target, wiki)
                if any(e.get("entry_key") == locator.split("#", 1)[1] for e in entries):
                    continue
            items[locator] = {
                "source_id": sha(locator + "\ndeleted"),
                "locator": locator,
                "revision": "deleted",
                "old_revision": old["checked_revision"],
                "text": "来源已删除\n" + old.get("text", ""),
            }
        except (OSError, UnicodeError, LLMWikiError, ValueError):
            gaps.append("来源不再可核实：" + locator)
    return items, sorted(set(gaps))


def catalog(project):
    result = []
    wiki = Path(project["wiki_root"])
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(wiki).as_posix()
        try:
            text = bounded(page_target(project, rel), PAGE_LIMIT)
        except (LLMWikiError, OSError, UnicodeError, ValueError):
            continue
        title = next(
            (line.lstrip("# ") for line in text.splitlines() if line.startswith("# ")),
            p.stem,
        )
        result.append(
            {
                "path": rel,
                "title": title[:200],
                "sha256": sha(text),
                "source_locators": ["source:" + s for s in _frontmatter_sources(text)],
            }
        )
    return result


def _evidence_current(project, refs):
    for ref in refs:
        if ref["locator"].startswith("conversation:"):
            try:
                if source_item(project, ref["locator"])["revision"] != ref["revision"]:
                    return False
            except (LLMWikiError, OSError, ValueError):
                return False
            continue
        try:
            target = _source_path(project, ref["locator"])
            if ref["revision"] == "deleted":
                if target.exists():
                    if (
                        not ref["locator"].startswith("record:")
                        or "#" not in ref["locator"]
                    ):
                        return False
                    from research_records import _load_records_from_path

                    bounded(target, SOURCE_LIMIT)
                    entries = _load_records_from_path(
                        target, Path(project["wiki_root"])
                    )
                    if any(
                        e.get("entry_key") == ref["locator"].split("#", 1)[1]
                        for e in entries
                    ):
                        return False
            elif sha(bounded(target, SOURCE_LIMIT)) != ref["revision"]:
                return False
        except (LLMWikiError, OSError, UnicodeError, ValueError):
            return False
    return True


def _staleness(project, proposal):
    try:
        target = page_target(project, proposal["page_path"])
        current = sha(bounded(target, PAGE_LIMIT)) if target.exists() else None
        if current != proposal["base_sha256"]:
            return "STALE_PAGE"
    except (OSError, UnicodeError, LLMWikiError, ValueError):
        return "STALE_PAGE"
    return (
        None
        if _evidence_current(project, proposal["evidence_refs"])
        else "STALE_EVIDENCE"
    )


def _store_proposal(project, saved, proposal):
    write_json(path(project, f"proposals/{proposal['id']}.json"), proposal)
    saved["proposal_index"][proposal["id"]] = {
        k: proposal[k]
        for k in (
            "id",
            "page_path",
            "status",
            "created_at",
            "decided_at",
            "evidence_key",
        )
    }
    saved["proposal_index"][proposal["id"]]["reason"] = proposal["reason"][:240]


def _mark_stale(project, saved, proposal):
    proposal["status"] = "stale"
    previous = saved["page_sources"].get(proposal["page_path"], {})
    saved["page_sources"][proposal["page_path"]] = {
        **previous,
        "locators": sorted(
            set(previous.get("locators", []))
            | {r["locator"] for r in proposal["evidence_refs"]}
        ),
    }
    _store_proposal(project, saved, proposal)
    if proposal["page_path"] not in saved["recheck_pages"]:
        saved["recheck_pages"].append(proposal["page_path"])


def _recover_written(project, saved, run):
    """Reconcile durable intents after expiry; never replay a body write."""
    proposals = run.get("submitted_proposals", [])
    for proposal in proposals:
        receipt = run["receipts"].get(proposal["id"], {})
        if receipt.get("status") != "prepared":
            continue
        try:
            target = page_target(project, proposal["page_path"])
            if sha(target.read_bytes()) != proposal["result_sha256"]:
                continue
            proposal["status"], proposal["decided_at"] = "applied", stamp()
            _store_proposal(project, saved, proposal)
            saved["last_updated_at"] = proposal["decided_at"]
            saved["page_sources"][proposal["page_path"]] = {
                "locators": sorted({r["locator"] for r in proposal["evidence_refs"]}),
                "updated_at": proposal["decided_at"],
                "action_id": proposal["id"],
            }
            _update_index(project)
            run["receipts"][proposal["id"]] = {
                "status": "created" if proposal["mode"] == "create" else "appended"
            }
        except (OSError, LLMWikiError, ValueError):
            continue
    complete = {"created", "appended", "pending", "rejected_suppressed", "unchanged"}
    for item in run.get("sources", []):
        related = [
            p
            for p in proposals
            if any(r["source_id"] == item["source_id"] for r in p["evidence_refs"])
        ]
        if (
            item["source_id"] in run.get("reviewed_source_ids", [])
            and all(
                run["receipts"].get(p["id"], {}).get("status") in complete
                for p in related
            )
            and _evidence_current(project, [item])
        ):
            saved["sources"][item["locator"]] = {
                "checked_revision": item["revision"],
                "text": item["text"],
            }
    write_json(path(project, f"runs/{run['id']}.json"), run)


def _allowed(settings, pid):
    runtime = settings["runtime"]
    return bool(
        settings["enabled"]
        and settings.get("knowledge_enabled")
        and pid in settings["project_ids"]
        and settings["daily_time"]
        and runtime.get("automation_id")
        and runtime.get("target_thread_id")
        and runtime.get("knowledge_bound_at")
        and runtime.get("status") not in {"paused", "PAUSED", "deleted"}
    )


def event_hints(project, cursor):
    """Consume bounded complete JSONL lines as *priority hints*, not scientific evidence.

    The revision scanner remains authoritative and compensates for truncation,
    malformed events, missed hooks, or a failed maintenance run.
    """
    target = Path(project["state_root"]) / "events.jsonl"
    try:
        size = target.stat().st_size
        offset = cursor if isinstance(cursor, int) and 0 <= cursor <= size else 0
        hints = set()
        with target.open("rb") as stream:
            stream.seek(offset)
            for _ in range(512):
                start = stream.tell()
                line = stream.readline(65537)
                if not line:
                    break
                if not line.endswith(b"\n") or len(line) > 65536:
                    stream.seek(start)
                    break
                offset = stream.tell()
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("kind") == "file-change-hint":
                    paths = event.get("paths")
                    if isinstance(paths, list):
                        hints.update("source:" + p for p in paths[:100]
                                     if isinstance(p, str) and p and len(p) <= 500)
                elif event.get("kind") == "research-result-captured":
                    record = event.get("record_id")
                    if isinstance(record, str) and record.startswith("records/"):
                        hints.add("record:" + record)
        return hints, offset
    except OSError:
        return set(), cursor if isinstance(cursor, int) and cursor >= 0 else 0


def knowledge_plan(trigger, project_id=None, home=None):
    if trigger not in {"manual", "scheduled"} or (
        trigger == "manual" and not project_id
    ):
        raise KnowledgeError("手动维护必须明确指定项目。")
    settings = report_settings(home)
    local = now().astimezone(CST)
    slot = local.date().isoformat() + "T" + (settings["daily_time"] or "00:00")
    if trigger == "manual":
        projects = [project_for(project_id, home)]
    else:
        if project_id is not None:
            raise KnowledgeError("定时维护由计划选择项目。")
        projects = [
            p
            for p in list_projects(home=home)["projects"]
            if _allowed(settings, p["id"])
        ]
        if not projects:
            return {"ok": True, "run_id": None, "reason": "paused_or_unbound"}
        if local.strftime("%H:%M") < settings["daily_time"] or (
            settings["start_date"] and local.date().isoformat() < settings["start_date"]
        ):
            return {"ok": True, "run_id": None, "reason": "not_due"}
        projects.sort(
            key=lambda p: (
                state(p)["last_daily_slot"] or "",
                state(p)["last_attempt_at"] or "",
                p["id"],
            )
        )
    for project in projects:
        with locked(project):
            saved = state(project)
            if saved["active_run"]:
                active = read_json(path(project, f"runs/{saved['active_run']}.json"))
                if (
                    active
                    and not active.get("result")
                    and active["expires_at"] > stamp()
                ):
                    if trigger == "manual":
                        return {"ok": True, "run_id": None, "reason": "busy"}
                    continue
                if active and not active.get("result"):
                    _recover_written(project, saved, active)
                    saved["failure_count"] += 1
                    saved["failure_fingerprint"] = active["fingerprint"]
                    saved["last_error"] = "维护运行超时，等待重试。"
                saved["active_run"] = None
            for pid, brief in list(saved["proposal_index"].items()):
                if brief["status"] == "pending":
                    proposal = read_json(path(project, f"proposals/{pid}.json"))
                    if _staleness(project, proposal):
                        _mark_stale(project, saved, proposal)
            hints, cursor = event_hints(project, saved.get("event_cursor", 0))
            if (
                trigger == "scheduled"
                and not hints
                and saved["last_daily_slot"] == slot
                and not saved["pending_source_count"]
                and not saved["recheck_pages"]
            ):
                save_state(project, saved)
                continue
            items, gaps = scan(project, saved["sources"])
            # A complete source scan is the fallback when a hook event was missed.
            saved["event_cursor"] = cursor
            pages = catalog(project)
            for page in pages:
                related = saved["page_sources"].setdefault(
                    page["path"], {"locators": []}
                )
                related["locators"] = sorted(
                    set(related["locators"]) | set(page["source_locators"])
                )
            recheck = {
                loc
                for p in saved["recheck_pages"]
                for loc in saved["page_sources"].get(p, {}).get("locators", [])
            }
            changes = [
                i
                for loc, i in items.items()
                if loc in recheck
                or saved["sources"].get(loc, {}).get("checked_revision")
                != i["revision"]
            ]
            changes.sort(
                key=lambda i: (
                    0
                    if i["locator"] in recheck
                    else 1
                    if i["locator"] in hints
                    else 3
                    if i["revision"] == "deleted"
                    else 2,
                    i["locator"],
                )
            )
            fingerprint = sha([(i["locator"], i["revision"]) for i in changes])
            if fingerprint != saved["failure_fingerprint"]:
                saved["failure_count"] = 0
            saved["gaps"], saved["pending_source_count"] = gaps, len(changes)
            if (
                trigger == "scheduled"
                and saved["failure_count"]
                and (
                    saved["failure_count"] >= 3
                    or (
                        saved["last_attempt_at"]
                        and now() - datetime.fromisoformat(saved["last_attempt_at"])
                        < timedelta(minutes=30)
                    )
                )
            ):
                save_state(project, saved)
                continue
            saved["last_attempt_at"] = stamp()
            if not changes:
                saved.update(
                    last_checked_at=stamp(), last_result="no_change", last_error=None
                )
                if trigger == "scheduled":
                    saved["last_daily_slot"] = slot
                save_state(project, saved)
                continue
            chosen, size = [], 0
            for item in changes:
                if len(chosen) == 20 or size + len(item["text"]) > 200000:
                    break
                chosen.append(item)
                size += len(item["text"])
            run_id = uuid4().hex
            run = {
                "schema_version": 1,
                "id": run_id,
                "project_id": project["id"],
                "trigger": trigger,
                "slot": slot,
                "expires_at": (now() + timedelta(minutes=10)).isoformat(
                    timespec="seconds"
                ),
                "sources": chosen,
                "conversation_consent": _conversation_consent(project) if any(i["locator"].startswith("conversation:") for i in chosen) else None,
                "catalog": pages,
                "contexts": {},
                "pages": {},
                "reads": {},
                "receipts": {},
                "fingerprint": fingerprint,
                "result": None,
            }
            write_json(path(project, f"runs/{run_id}.json"), run)
            saved["active_run"] = run_id
            save_state(project, saved)
            return {
                "ok": True,
                "run_id": run_id,
                "project_id": project["id"],
                "expires_at": run["expires_at"],
                "source_count": len(chosen),
                "pending_source_count": len(changes),
                "gaps": gaps,
            }
    return {"ok": True, "run_id": None, "reason": "no_changes_or_not_due"}


def _locate(run_id, home):
    _id(run_id)
    for project in list_projects(home=home)["projects"]:
        if path(project, f"runs/{run_id}.json").is_file():
            return project
    raise KnowledgeError("运行不存在。", "NOT_FOUND", 404)


def _conversation_consent(project):
    from research_capture import consent_revision
    return consent_revision(project)


def _check_conversation_consent(project, run):
    if run.get("conversation_consent") and run["conversation_consent"] != _conversation_consent(project):
        raise KnowledgeError("对话授权已变化，请重新准备。", "SOURCE_UNAVAILABLE", 409)


def _live(run):
    if run["expires_at"] <= stamp():
        raise KnowledgeError("运行已过期，请重新准备。", "EXPIRED", 409)


def _chunks(items):
    pages, page, length = [], [], 0
    for item in items:
        text = item["text"]
        for start in range(0, max(1, len(text)), 20000):
            part = {
                **item,
                "text": text[start : start + 20000],
                "start": start,
                "complete": start + 20000 >= len(text),
            }
            if page and length + len(part["text"]) > 20000:
                pages.append(page)
                page, length = [], 0
            page.append(part)
            length += len(part["text"])
    return pages + ([page] if page else [[]])


def knowledge_sources(
    run_id, view="changes", cursor=None, page_path=None, locator=None, home=None
):
    project = _locate(run_id, home)
    if (
        view not in {"changes", "catalog", "page", "source"}
        or ((page_path is not None) != (view == "page"))
        or ((locator is not None) != (view == "source"))
    ):
        raise KnowledgeError("读取视图和路径参数不匹配。")
    with locked(project):
        run = read_json(path(project, f"runs/{run_id}.json"))
        _live(run)
        _check_conversation_consent(project, run)
        key = view + ":" + (page_path or locator or "")
        if view == "page":
            if page_path not in run["pages"]:
                if len(run["pages"]) >= 10:
                    raise KnowledgeError("每轮最多读取十个知识页。")
                text = bounded(page_target(project, page_path), PAGE_LIMIT)
                run["pages"][page_path] = {
                    "text": text,
                    "base_sha256": sha(text),
                    "page_path": page_path,
                }
            pages = _chunks([run["pages"][page_path]])
        elif view == "source":
            if locator not in run["contexts"]:
                if len(run["contexts"]) >= 10:
                    raise KnowledgeError("每轮最多读取十个上下文来源。")
                run["contexts"][locator] = source_item(project, locator)
                if locator.startswith("conversation:"):
                    run["conversation_consent"] = _conversation_consent(project)
            pages = _chunks([run["contexts"][locator]])
        elif view == "catalog":
            pages = [
                run["catalog"][i : i + 100] for i in range(0, len(run["catalog"]), 100)
            ] or [[]]
        else:
            pages = _chunks(run["sources"])
        namespace = sha(run_id + key)[:16]
        if cursor is None:
            index = 0
        elif not isinstance(cursor, str) or not re.fullmatch(
            namespace + r":\d+", cursor
        ):
            raise KnowledgeError("分页游标无效。")
        else:
            index = int(cursor.split(":")[1])
        read = run["reads"].setdefault(key, {"next": 0, "complete": False})
        if index >= len(pages) or index > read["next"]:
            raise KnowledgeError("请按顺序读取全部分页。")
        read["next"] = max(read["next"], index + 1)
        read["complete"] = read["next"] == len(pages)
        write_json(path(project, f"runs/{run_id}.json"), run)
        return {
            "ok": True,
            "run_id": run_id,
            "view": view,
            "items": pages[index],
            "next_cursor": f"{namespace}:{index + 1}"
            if index + 1 < len(pages)
            else None,
            "rejected_keys": [
                v["evidence_key"]
                for v in state(project)["proposal_index"].values()
                if v["status"] == "rejected"
            ][:100]
            if view == "catalog"
            else [],
        }


def _read_complete(run, key):
    return run["reads"].get(key, {}).get("complete", False)


def _update_index(project):
    # Generated region only; outside bytes (including CRLF/trailing spaces) stay exact.
    target = safe_file(Path(project["wiki_root"]), "index.md")
    text = (
        target.read_bytes().decode("utf-8") if target.exists() else "# Wiki Index\n\n"
    )
    rows = [f"- [[{p['path'][:-3]}|{p['title']}]]" for p in catalog(project)]
    region = (
        "<!-- llmwiki:index:start -->\n"
        + "\n".join(rows)
        + "\n<!-- llmwiki:index:end -->"
    )
    pattern = re.compile(
        r"(?s)<!-- llmwiki:index:start -->.*?<!-- llmwiki:index:end -->"
    )
    updated = (
        pattern.sub(lambda _: region, text, count=1)
        if pattern.search(text)
        else text + "\n\n" + region + "\n"
    )
    _atomic(target, updated.encode("utf-8"))


def _validate_actions(project, run, reviewed, actions):
    if (
        not isinstance(reviewed, list)
        or any(not isinstance(i, str) for i in reviewed)
        or not isinstance(actions, list)
        or len(actions) > 10
    ):
        raise KnowledgeError("检查来源或动作列表无效。")
    originals = {i["source_id"]: i for i in run["sources"]}
    sources = {**originals, **{i["source_id"]: i for i in run["contexts"].values()}}
    delivered = run["reads"].get("changes:", {}).get("next", 0)
    full_ids = {
        i["source_id"]
        for page in _chunks(run["sources"])[:delivered]
        for i in page
        if i["complete"]
    }
    if set(reviewed) - originals.keys() or set(reviewed) - full_ids:
        raise KnowledgeError("不能提交未完整读取的变化来源。")
    pages, action_ids, proposals = set(), set(), []
    for action in actions:
        if not isinstance(action, dict) or set(action) != {
            "action_id",
            "mode",
            "page_path",
            "base_sha256",
            "content",
            "reason",
            "evidence_refs",
        }:
            raise KnowledgeError("动作字段无效。")
        aid = _id(action["action_id"])
        mode, relative = action["mode"], action["page_path"]
        if (
            mode not in {"create", "append", "replace"}
            or not isinstance(relative, str)
            or relative in pages
            or aid in action_ids
        ):
            raise KnowledgeError("动作重复或类型无效。")
        pages.add(relative)
        action_ids.add(aid)
        page_target(project, relative, create=mode == "create")
        previous_action = read_json(path(project, f"proposals/{aid}.json"))
        if previous_action and previous_action["run_id"] != run["id"]:
            raise KnowledgeError("action_id 已用于其他运行。")
        text, reason, refs = (
            action["content"],
            action["reason"],
            action["evidence_refs"],
        )
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-8")) > PAGE_LIMIT
            or "\x00" in text
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 4000
        ):
            raise KnowledgeError("内容或原因无效。")
        if mode == "create":
            if action["base_sha256"] is not None or not _read_complete(run, "catalog:"):
                raise KnowledgeError("新建之前必须读完知识目录。")
            base = None
        else:
            base = run["pages"].get(relative)
            if (
                not base
                or not _read_complete(run, "page:" + relative)
                or base["base_sha256"] != action["base_sha256"]
            ):
                raise KnowledgeError("必须完整读取原页并携带原始版本。")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 30:
            raise KnowledgeError("动作必须提供有界来源依据。")
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {
                "source_id",
                "locator",
                "revision",
                "excerpt",
            }:
                raise KnowledgeError("依据字段无效。")
            item = sources.get(ref["source_id"])
            if (
                not item
                or any(item[k] != ref[k] for k in ("locator", "revision"))
                or not isinstance(ref["excerpt"], str)
                or not 0 < len(ref["excerpt"]) <= 1000
                or ref["excerpt"] not in item["text"]
            ):
                raise KnowledgeError("依据不是已冻结的原始材料。")
            if item["revision"] == "deleted" and "来源已删除" not in reason:
                raise KnowledgeError("删除线索须在原因中明确注明来源已删除。")
            if item["source_id"] in originals:
                if item["source_id"] not in reviewed:
                    raise KnowledgeError("动作依据必须属于本轮已检查来源。")
            elif not _read_complete(run, "source:" + item["locator"]):
                raise KnowledgeError("上下文来源未读完。")
        base_text = base["text"] if base else ""
        proposed = (
            base_text + ("\n\n" if not base_text.endswith("\n\n") else "") + text
            if mode == "append"
            else text
        )
        if len(proposed.encode("utf-8")) > PAGE_LIMIT:
            raise KnowledgeError("落地后知识页超过上限。")
        ek = sha([relative, sorted({(r["locator"], r["revision"]) for r in refs})])
        proposals.append(
            {
                "schema_version": 1,
                "id": aid,
                "run_id": run["id"],
                "page_path": relative,
                "mode": mode,
                "base_sha256": action["base_sha256"],
                "base_text": base_text,
                "proposed_text": proposed,
                "reason": reason,
                "evidence_refs": refs,
                "evidence_key": ek,
                "status": "pending",
                "created_at": stamp(),
                "decided_at": None,
                "result_sha256": sha(proposed),
            }
        )
    return proposals


def _apply_action(project, saved, run, proposal):
    aid = proposal["id"]
    receipt = run["receipts"].get(aid)
    if receipt and receipt["status"] != "prepared":
        return receipt["status"]
    target = page_target(
        project, proposal["page_path"], create=proposal["mode"] == "create"
    )
    # Durable intent precedes body writes; recovery never overwrites external edits.
    if receipt and receipt["status"] == "prepared":
        if target.exists() and sha(target.read_bytes()) == proposal["result_sha256"]:
            proposal["status"], proposal["decided_at"] = "applied", stamp()
        elif _staleness(project, proposal):
            _mark_stale(project, saved, proposal)
            return "stale"
    if proposal["status"] != "applied":
        for previous in saved["proposal_index"].values():
            if (
                previous["id"] != aid
                and previous["evidence_key"] == proposal["evidence_key"]
                and previous["status"] in {"rejected", "pending"}
            ):
                return (
                    "rejected_suppressed"
                    if previous["status"] == "rejected"
                    else "pending"
                )
        if _staleness(project, proposal):
            _mark_stale(project, saved, proposal)
            return "stale"
        if proposal["mode"] == "replace":
            if proposal["base_text"] == proposal["proposed_text"]:
                return "unchanged"
            _store_proposal(project, saved, proposal)
            saved["page_sources"][proposal["page_path"]] = {
                "locators": sorted({r["locator"] for r in proposal["evidence_refs"]}),
                "action_id": aid,
                "updated_at": None,
            }
            return "pending"
        run["receipts"][aid] = {"status": "prepared"}
        _store_proposal(project, saved, proposal)
        write_json(path(project, f"runs/{run['id']}.json"), run)
        _atomic(target, proposal["proposed_text"].encode("utf-8"))
        proposal["status"], proposal["decided_at"] = "applied", stamp()
    _store_proposal(project, saved, proposal)
    saved["last_updated_at"] = proposal["decided_at"]
    saved["page_sources"][proposal["page_path"]] = {
        "locators": sorted({r["locator"] for r in proposal["evidence_refs"]}),
        "updated_at": stamp(),
        "action_id": aid,
    }
    if proposal["page_path"] in saved["recheck_pages"]:
        saved["recheck_pages"].remove(proposal["page_path"])
    _update_index(project)
    return "created" if proposal["mode"] == "create" else "appended"


def knowledge_finish(
    run_id, outcome, reviewed_source_ids=None, actions=None, error_code=None, home=None
):
    project = _locate(run_id, home)
    if outcome not in {"reviewed", "failed"}:
        raise KnowledgeError("结果类型无效。")
    with locked(project):
        run = read_json(path(project, f"runs/{run_id}.json"))
        if run.get("result") is not None:
            return run["result"]
        _live(run)
        _check_conversation_consent(project, run)
        if run["trigger"] == "scheduled" and not _allowed(
            report_settings(home), project["id"]
        ):
            raise KnowledgeError("定时维护已暂停或撤销授权。", "PAUSED", 409)
        saved = state(project)
        proposals = (
            _validate_actions(project, run, reviewed_source_ids or [], actions or [])
            if outcome == "reviewed"
            else []
        )
        signature = sha([outcome, reviewed_source_ids, actions, error_code])
        if run.get("submission") and run["submission"] != signature:
            raise KnowledgeError("重试必须使用同一份提交。", "SUBMISSION_CHANGED", 409)
        run["submission"] = signature
        run["submitted_proposals"] = proposals
        run["reviewed_source_ids"] = reviewed_source_ids or []
        write_json(path(project, f"runs/{run_id}.json"), run)
        result = {
            "ok": True,
            "run_id": run_id,
            **dict.fromkeys(
                (
                    "created",
                    "appended",
                    "pending",
                    "rejected_suppressed",
                    "stale",
                    "failed",
                ),
                0,
            ),
            "url": f"/project/{project['id']}",
        }
        failed_sources = set()
        if outcome == "failed":
            result["failed"] = 1
            saved["last_error"] = "维护未完成：" + (
                error_code
                if isinstance(error_code, str)
                and re.fullmatch(r"[A-Z_]{1,48}", error_code)
                else "HOST_FAILED"
            )
        else:
            for proposal in proposals:
                try:
                    status = _apply_action(project, saved, run, proposal)
                except OSError:
                    status = "failed"
                if status in result:
                    result[status] += 1
                if status in {"failed", "stale"}:
                    failed_sources.update(
                        r["source_id"] for r in proposal["evidence_refs"]
                    )
                else:
                    run["receipts"][proposal["id"]] = {"status": status}
                save_state(project, saved)
                write_json(path(project, f"runs/{run_id}.json"), run)
            for item in run["sources"]:
                if (
                    item["source_id"] in (reviewed_source_ids or [])
                    and item["source_id"] not in failed_sources
                    and _evidence_current(project, [item])
                ):
                    saved["sources"][item["locator"]] = {
                        "checked_revision": item["revision"],
                        "text": item["text"],
                    }
            for relative in list(saved["recheck_pages"]):
                related = saved["page_sources"].get(relative, {}).get("locators", [])
                if related and all(
                    any(
                        i["locator"] == loc
                        and i["source_id"] in (reviewed_source_ids or [])
                        and i["source_id"] not in failed_sources
                        and _evidence_current(project, [i])
                        for i in run["sources"]
                    )
                    for loc in related
                ):
                    saved["recheck_pages"].remove(relative)
            saved["last_error"] = (
                "部分页面等待重检。"
                if result["stale"]
                else "部分页面写入失败。"
                if result["failed"]
                else None
            )
        remaining = sum(
            saved["sources"].get(i["locator"], {}).get("checked_revision")
            != i["revision"]
            for i in run["sources"]
        )
        outside = run.setdefault(
            "outside_count", max(0, saved["pending_source_count"] - len(run["sources"]))
        )
        saved["pending_source_count"] = outside + remaining
        result["remaining"] = saved["pending_source_count"]
        saved["last_checked_at"] = stamp()
        saved["active_run"] = (
            run_id if result["failed"] and outcome != "failed" else None
        )
        if result["failed"]:
            saved["failure_count"] += 1
            saved["failure_fingerprint"] = run["fingerprint"]
        else:
            saved["failure_count"] = 0
        saved["last_result"] = (
            "partial"
            if result["remaining"] or result["stale"]
            else "updated"
            if result["created"] + result["appended"]
            else "no_change"
        )
        if outcome == "failed":
            saved["last_result"] = "failed"
        if run["trigger"] == "scheduled":
            saved["last_daily_slot"] = run["slot"]
        if not result["failed"] or outcome == "failed":
            run["result"] = result
        save_state(project, saved)
        write_json(path(project, f"runs/{run_id}.json"), run)
        return result


def proposal_list(project, scope="pending", offset=0, limit=20, page_path=None):
    if (
        scope not in {"pending", "history"}
        or type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 20
    ):
        raise KnowledgeError("列表分页无效。")
    items = [
        v
        for v in state(project)["proposal_index"].values()
        if (scope == "history" or v["status"] == "pending")
        and (not page_path or v["page_path"] == page_path)
    ]
    items.sort(key=lambda p: (p["created_at"], p["id"]), reverse=True)
    return {
        "ok": True,
        "items": items[offset : offset + limit],
        "total": len(items),
        "has_more": offset + limit < len(items),
    }


def _proposal(project, pid):
    value = read_json(path(project, f"proposals/{_id(pid)}.json"))
    if not value:
        raise KnowledgeError("建议不存在。", "NOT_FOUND", 404)
    return value


def _proposal_sha(p):
    return sha(
        {
            k: p[k]
            for k in (
                "page_path",
                "base_sha256",
                "proposed_text",
                "evidence_refs",
                "reason",
            )
        }
    )


def proposal_detail(project, proposal_id):
    from markdown_renderer import render_markdown

    p = _proposal(project, proposal_id)
    stale = _staleness(project, p) if p["status"] == "pending" else None
    return {
        "ok": True,
        **p,
        "proposal_sha256": _proposal_sha(p),
        "effective_status": "stale" if stale else p["status"],
        "stale_reason": stale,
        "base_html": render_markdown(p["base_text"], project["id"], p["page_path"]),
        "proposed_html": render_markdown(
            p["proposed_text"], project["id"], p["page_path"]
        ),
        "diff": "\n".join(
            difflib.unified_diff(
                p["base_text"].splitlines(),
                p["proposed_text"].splitlines(),
                fromfile="原文",
                tofile="建议",
                lineterm="",
            )
        ),
    }


def decide(project, proposal_id, payload):
    with locked(project):
        p = _proposal(project, proposal_id)
        if payload.get("action") not in {"accept", "keep"}:
            raise KnowledgeError("确认动作无效。")
        if (
            payload.get("expected_proposal_sha256") != _proposal_sha(p)
            or payload.get("expected_base_sha256") != p["base_sha256"]
        ):
            raise KnowledgeError("建议已变化，请刷新后比较。", "PROPOSAL_CHANGED", 409)
        desired = "applied" if payload["action"] == "accept" else "rejected"
        if p["status"] == desired:
            return {"ok": True, "status": desired, "id": p["id"]}
        if p["status"] not in {"pending", "stale"}:
            raise KnowledgeError("该建议已处理。", "PROPOSAL_CHANGED", 409)
        saved = state(project)
        if desired == "applied":
            target = page_target(project, p["page_path"])
            recovered = (
                p.get("accept_intent")
                and target.exists()
                and sha(target.read_bytes()) == p["result_sha256"]
            )
            stale = None if recovered else _staleness(project, p)
            if stale or (p["status"] == "stale" and not recovered):
                _mark_stale(project, saved, p)
                save_state(project, saved)
                raise KnowledgeError(
                    "原文或依据已变化，等待重新检查。", stale or "STALE_PAGE", 409
                )
            if not recovered:
                p["accept_intent"] = True
                _store_proposal(project, saved, p)
                _atomic(target, p["proposed_text"].encode("utf-8"))
            saved["last_updated_at"] = stamp()
            saved["page_sources"][p["page_path"]] = {
                "locators": sorted({r["locator"] for r in p["evidence_refs"]}),
                "updated_at": stamp(),
                "action_id": p["id"],
            }
            _update_index(project)
        p["status"], p["decided_at"] = desired, stamp()
        _store_proposal(project, saved, p)
        save_state(project, saved)
        return {"ok": True, "status": desired, "id": p["id"]}


def maintenance_status(project, home=None):
    saved = state(project)
    settings = report_settings(home)
    return {
        "ok": True,
        "status": "running" if saved["active_run"] else saved["last_result"],
        "last_checked_at": saved["last_checked_at"],
        "last_updated_at": saved["last_updated_at"],
        "pending_count": sum(
            p["status"] == "pending" for p in saved["proposal_index"].values()
        ),
        "remaining": saved["pending_source_count"],
        "gaps": saved["gaps"],
        "last_error": saved["last_error"],
        "executor_status": "已连接共享计划"
        if _allowed(settings, project["id"])
        else "知识维护待连接（可在对话中手动维护）",
    }


def widget(project_id, page_path=""):
    from html import escape

    return f'''<link rel="stylesheet" href="/static/knowledge-maintenance.css"><section id="knowledge-maintenance" data-project="{escape(project_id)}" data-page="{escape(page_path)}">
<button id="km-pending" hidden></button><details id="km-details" class="subtle-details"><summary>维护详情</summary><div id="km-status"></div><button id="km-history">查看历史</button></details>
<div id="km-list" hidden></div><p id="km-error" role="status"></p>
<dialog id="km-dialog" aria-label="知识更新对比"><button id="km-close" aria-label="关闭对比">×</button><h2 id="km-title"></h2><div class="km-columns"><section><h3>原文</h3><article id="km-base" class="document"></article></section><section><h3>建议</h3><article id="km-proposed" class="document"></article></section></div><details><summary>查看文字差异</summary><pre id="km-diff"></pre></details><p id="km-reason"></p><details><summary>来源依据</summary><pre id="km-evidence"></pre></details><p id="km-conflict" role="status"></p><footer><button id="km-accept">采用修改</button><button id="km-keep">保留原文</button></footer></dialog></section><script src="/static/knowledge-maintenance.js" defer></script>'''
