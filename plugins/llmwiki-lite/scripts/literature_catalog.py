"""Project-local literature identities and short, cross-process catalog transactions.

No network, source discovery, model calls, or file-content writes. Public host callers
use literature_collect(project_id, ...); LiteratureCatalog is the internal store.
"""
from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, urlunsplit

from llmwiki_core import LLMWikiError
from llmwiki_registry import get_project, list_projects

MAX_TITLE_LENGTH = 1000
MAX_AUTHORS = 100
MAX_AUTHOR_LENGTH = 200
MAX_VENUE_LENGTH = 300
MAX_URLS = MAX_ATTACHMENTS = MAX_READING_NOTE_PATHS = 20
MAX_SOURCE_REFS = 100
MAX_TAGS = 20
MAX_TAG_LENGTH = 40
MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024
YEAR_MIN, YEAR_MAX = 1000, 9999
HEX_ID = re.compile(r"[a-f0-9]{32}\Z")
DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"#?]+\Z", re.I)
ARXIV = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})(?:v\d+)?\Z", re.I)


class LiteratureCatalogError(LLMWikiError):
    def __init__(self, message, code="INVALID_INPUT", status=400):
        super().__init__(message)
        self.code, self.status = code, status


class RevisionConflictError(LiteratureCatalogError):
    def __init__(self, message="此条文献已变化，请重新加载；当前输入仍保留。"):
        super().__init__(message, "REVISION_CONFLICT", 409)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _new_id():
    return uuid.uuid4().hex


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _compute_sha256(data):
    return hashlib.sha256(data).hexdigest()


def item_revision(item):
    return _compute_sha256(_canonical_json(item))


def _text(value, maximum=1000, *, empty=True):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise LiteratureCatalogError("文字为空、类型错误或超过长度限制。")
    return value.strip()


def _normalize_url(value):
    if not isinstance(value, str) or re.search(r"[\x00-\x20\x7f\\]", value.strip()):
        return ""
    try:
        p = urlsplit(value.strip())
        if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username is not None or p.password is not None:
            return ""
        host = p.hostname.lower()
        if any(c in host for c in '<>"{}'):
            return ""
        port = p.port
        if ":" in host:
            host = f"[{host}]"
        if port is not None and (p.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        # Preserve business query order, encoding and repeated values, not parse/re-encode.
        query = "&".join(q for q in p.query.split("&") if not unquote(q.partition("=")[0]).lower().startswith("utm_"))
        return urlunsplit((p.scheme.lower(), host, p.path, query, ""))
    except (ValueError, TypeError):
        return ""


def _normalize_doi(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if re.match(r"https?://", value, re.I):
        url = _normalize_url(value)
        if not url:
            return ""
        p = urlsplit(url)
        if p.hostname not in {"doi.org", "dx.doi.org"}:
            return ""
        value = unquote(p.path.lstrip("/"))
    value = re.sub(r"^doi:\s*", "", value, flags=re.I).lower()
    return value if DOI.fullmatch(value) else ""


def _normalize_arxiv(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if re.match(r"https?://", value, re.I):
        url = _normalize_url(value)
        if not url:
            return ""
        p = urlsplit(url)
        if p.hostname not in {"arxiv.org", "www.arxiv.org"} or not re.match(r"^/(abs|pdf)/", p.path):
            return ""
        value = re.sub(r"^/(abs|pdf)/", "", p.path)
        value = re.sub(r"\.pdf$", "", value, flags=re.I)
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.I)
    return re.sub(r"v\d+$", "", value, flags=re.I).lower() if ARXIV.fullmatch(value) else ""


def parse_locator(locator):
    if isinstance(locator, str) and re.search(r"[\x00-\x1f\x7f]", locator):
        raise LiteratureCatalogError("地址不能包含控制字符。")
    locator = _text(locator, 4096, empty=False)
    doi, arxiv, url = _normalize_doi(locator), _normalize_arxiv(locator), _normalize_url(locator)
    if not (doi or arxiv or url):
        raise LiteratureCatalogError("请提供 HTTP(S) 地址、DOI 或 arXiv 标识。")
    if not url:
        if doi:
            url = "https://doi.org/" + doi
        else:
            raw = re.sub(r"^arxiv:\s*", "", locator, flags=re.I)
            url = "https://arxiv.org/abs/" + raw
    return {"doi": doi or None, "arxiv": arxiv or None}, url


def _resolved_path(path):
    value = str(Path(path).resolve())
    # Windows may retain the extended prefix while another process replaces a file.
    # Normalize that spelling, not the actual target, before containment comparison.
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\") and re.match(r"[A-Za-z]:\\", value[4:]):
            value = value[4:]
    return Path(value)


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or len(relative) > 4096:
        raise LiteratureCatalogError("文件路径无效。")
    relative = relative.replace("\\", "/")
    p = PurePosixPath(relative)
    if p.is_absolute() or ".." in p.parts or ":" in relative or re.search(r"[\x00-\x1f]", relative):
        raise LiteratureCatalogError("不允许越界或绝对文件路径。", "UNSAFE_PATH", 403)
    root = _resolved_path(root)
    path = root.joinpath(*p.parts)
    try:
        _resolved_path(path).relative_to(root)
        current = path
        while current != root:
            if current.is_symlink() or (current.exists() and getattr(current.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                raise ValueError("link")
            current = current.parent
    except (ValueError, OSError, RuntimeError) as exc:
        raise LiteratureCatalogError("不允许越界或链接文件。", "UNSAFE_PATH", 403) from exc
    return path


def project_for(project_id, home=None):
    if not isinstance(project_id, str) or not project_id:
        raise LiteratureCatalogError("必须明确指定已注册项目。")
    try:
        project = get_project(project_id, home=home)["project"]
    except LLMWikiError as exc:
        raise LiteratureCatalogError("项目不存在。", "NOT_FOUND", 404) from exc
    if project["id"] != project_id:
        raise LiteratureCatalogError("请使用注册 project_id，不接受名称或写入路径。")
    # A shared or nested storage root cannot enforce project-local isolation.
    for other in list_projects(home=home)["projects"]:
        if other["id"] == project_id:
            continue
        for key in ("wiki_root", "state_root"):
            a, b = Path(project[key]).resolve(), Path(other[key]).resolve()
            if a == b or a in b.parents or b in a.parents:
                raise LiteratureCatalogError("项目文献存储目录重叠，请先调整存储位置。", "STORAGE_OVERLAP", 409)
    return project


@contextmanager
def short_lock(path, timeout=2.0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        # Byte-range locking beyond EOF is supported on Windows; no racing
        # initialization write may touch a byte another process already locked.
        until = time.monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= until:
                    raise LiteratureCatalogError("另一操作正在保存，请稍后重试。", "LOCK_TIMEOUT", 409) from exc
                time.sleep(.025)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".literature-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical_json(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_metadata(title=None, authors=None, year=None):
    if title is not None:
        _text(title, MAX_TITLE_LENGTH, empty=False)
    if authors is not None and (not isinstance(authors, list) or len(authors) > MAX_AUTHORS or any(not isinstance(a, str) or len(a) > MAX_AUTHOR_LENGTH for a in authors)):
        raise LiteratureCatalogError("作者须为字符串数组，最多100人，每项最多200字符。")
    if year is not None and (type(year) is not int or not YEAR_MIN <= year <= YEAR_MAX):
        raise LiteratureCatalogError("年份须为空或四位整数。")


def source_ref(kind, locator, revision, *, occurred_at=None, observed_at=None, collected_at=None, summary=""):
    if kind not in {"manual", "record", "notebook", "conversation"}:
        raise LiteratureCatalogError("来源种类无效。")
    locator, revision = _text(locator, 4096, empty=False), _text(revision, 128, empty=False)
    summary = _text(summary, 1000)
    for date in (occurred_at, observed_at, collected_at):
        if date is not None:
            try:
                if datetime.fromisoformat(date).utcoffset() is None:
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise LiteratureCatalogError("来源时间须含时区。") from exc
    return {"id": item_revision([kind, locator, revision]), "kind": kind, "locator": locator, "revision": revision,
            "occurred_at": occurred_at, "observed_at": observed_at, "collected_at": collected_at or _utc_now(), "summary": summary}


def _identities(item):
    ids = item.get("identifiers") or {}
    keys = set()
    for key, normal in (("doi", _normalize_doi), ("arxiv", _normalize_arxiv)):
        if normal(ids.get(key)):
            keys.add((key, normal(ids[key])))
    for entry in item.get("urls", []):
        value = entry.get("url", "")
        if _normalize_url(value):
            keys.add(("url", _normalize_url(value)))
        for key, normal in (("doi", _normalize_doi), ("arxiv", _normalize_arxiv)):
            if normal(value):
                keys.add((key, normal(value)))
    return keys


def _identity_match(incoming, data):
    keys = _identities(incoming)
    matches = []
    for dismissed, entries in ((False, data["items"]), (True, data["dismissed_candidates"])):
        for entry in entries:
            old = entry.get("item", {}) if dismissed else entry
            oldkeys = _identities(old)
            # File identity is permitted only for explicit file imports.
            files = {a.get("path") for a in incoming.get("attachments", [])}
            filematch = bool(files & {a.get("path") for a in old.get("attachments", [])})
            if keys & oldkeys or filematch:
                matches.append((dismissed, entry, old))
                for key in ("doi", "arxiv"):
                    values = {v for k, v in keys | oldkeys if k == key}
                    if len(values) > 1:
                        raise LiteratureCatalogError("同一身份携带矛盾标识，请移除后以新论文收藏。", "IDENTITY_CONFLICT", 409)
    if len(matches) > 1:
        raise LiteratureCatalogError("标识命中多个条目，不能自动合并。", "IDENTITY_CONFLICT", 409)
    return matches[0] if matches else None


class LiteratureCatalog:
    def __init__(self, wiki_root):
        self.wiki_root = Path(wiki_root).resolve()
        self.literature_dir = safe_path(self.wiki_root, ".literature")
        self.catalog_path = safe_path(self.wiki_root, ".literature/catalog.json")
        self.history_dir = safe_path(self.wiki_root, ".literature/history")

    def _lock(self):
        return short_lock(safe_path(self.wiki_root, ".literature/.lock"))

    def _load_catalog(self):
        safe_path(self.wiki_root, ".literature/catalog.json")
        if not self.catalog_path.exists():
            return {"schema_version": 1, "items": [], "dismissed_candidates": []}
        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1 or not isinstance(data.get("items"), list) or not isinstance(data.get("dismissed_candidates", []), list):
                raise ValueError()
            data.setdefault("dismissed_candidates", [])
            return data
        except (ValueError, TypeError, OSError) as exc:
            raise LiteratureCatalogError("文献清单无法读取；未修改原文件。", "CATALOG_UNREADABLE", 409) from exc

    def _save_catalog(self, data, expected_revision=None):
        if expected_revision is not None and self.get_revision() != expected_revision:
            raise RevisionConflictError()
        safe_path(self.wiki_root, ".literature/catalog.json")
        if self.catalog_path.exists():
            safe_path(self.wiki_root, ".literature/history")
            self.history_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.catalog_path, self.history_dir / f"catalog_{time.time_ns()}_{_new_id()}.json")
        atomic_json(self.catalog_path, data)

    def get_revision(self):
        return _compute_sha256(self.catalog_path.read_bytes()) if self.catalog_path.exists() else ""

    def list_items(self, *, status=None, archived=None, query="", offset=0, limit=None):
        items = self._load_catalog()["items"]
        query = query.casefold().strip()
        items = [i for i in items if (status is None or i.get("reading_status") == status) and (archived is None or i.get("archived", False) == archived)
                 and (not query or query in " ".join([i["title"], *i.get("authors", []), *[v or "" for v in i.get("identifiers", {}).values()]]).casefold())]
        count = len(items)
        items = items[offset:offset + limit if limit is not None else None]
        return {"items": items, "count": count, "revision": self.get_revision()}

    def get_item(self, item_id):
        return next((i for i in self._load_catalog()["items"] if i["id"] == item_id), None)

    def upsert(self, locator=None, *, title=None, authors=None, year=None, doi=None, arxiv=None, source_refs=None,
               explicit=False, attachments=None, reading_note_paths=None, collection_source="agent"):
        validate_metadata(title, authors, year)
        ids, url = parse_locator(locator) if locator else ({"doi": None, "arxiv": None}, None)
        for key, value, normal in (("doi", doi, _normalize_doi), ("arxiv", arxiv, _normalize_arxiv)):
            if value is not None:
                normalized = normal(value)
                if not normalized:
                    raise LiteratureCatalogError("DOI/arXiv 格式无效。")
                if ids[key] and ids[key] != normalized:
                    raise LiteratureCatalogError("提交的强标识互相矛盾。", "IDENTITY_CONFLICT", 409)
                ids[key] = normalized
        if not (url or attachments):
            raise LiteratureCatalogError("请补充可定位地址或明确导入文件。")
        now = _utc_now()
        incoming = {"id": _new_id(), "title": title.strip() if title else str(locator), "authors": authors or [], "year": year,
                    "venue": "", "publication_type": "unknown", "identifiers": ids,
                    "urls": [{"id": _new_id(), "url": url, "kind": "other"}] if url else [], "attachments": attachments or [],
                    "identity_status": "unverified", "reading_status": "unread", "collection_source": collection_source,
                    "source_refs": source_refs or [], "tags": [], "reading_note_paths": reading_note_paths or [], "archived": False,
                    "created_at": now, "updated_at": now, "manual_fields": [k for k, v in (("title", title), ("authors", authors), ("year", year)) if explicit and v is not None],
                    "title_is_placeholder": not bool(title)}
        for key, cap in (("urls", MAX_URLS), ("attachments", MAX_ATTACHMENTS), ("source_refs", MAX_SOURCE_REFS), ("reading_note_paths", MAX_READING_NOTE_PATHS)):
            if len(incoming[key]) > cap:
                raise LiteratureCatalogError(f"{key} 达到资料上限 {cap}。", "limit_reached", 409)
        with self._lock():
            data = self._load_catalog()
            match = _identity_match(incoming, data)
            warnings = []
            if not match:
                item, action = incoming, "created"
                data["items"].append(item)
            else:
                dismissed, entry, old = match
                if dismissed and not explicit:
                    return {"ok": True, "action": "skipped", "reason": "dismissed", "item_id": old["id"], "warnings": []}
                item = copy.deepcopy(old)
                protected = item.get("manual_fields", [])
                for key in ("title", "authors", "year"):
                    value = incoming[key]
                    if key not in protected and (not item.get(key) or (key == "title" and item.get("title_is_placeholder", False))) and value and not (key == "title" and incoming["title_is_placeholder"]):
                        item[key] = value
                        if key == "title":
                            item["title_is_placeholder"] = False
                        if explicit:
                            item.setdefault("manual_fields", []).append(key)
                for key in ("doi", "arxiv"):
                    if ids[key] and not item.setdefault("identifiers", {}).get(key):
                        item["identifiers"][key] = ids[key]
                for key, cap, identity in (("urls", MAX_URLS, lambda x: _normalize_url(x["url"])),
                                           ("attachments", MAX_ATTACHMENTS, lambda x: x["path"]),
                                           ("source_refs", MAX_SOURCE_REFS, lambda x: x.get("id") if isinstance(x, dict) else str(x)),
                                           ("reading_note_paths", MAX_READING_NOTE_PATHS, lambda x: x)):
                    entries = item.setdefault(key, [])
                    known = {identity(x) for x in entries}
                    for value in incoming[key]:
                        if identity(value) in known:
                            continue
                        if len(entries) >= cap:
                            warnings.append({"code": "limit_reached", "field": key, "limit": cap})
                            continue
                        entries.append(value)
                        known.add(identity(value))
                action = "restored" if dismissed else ("updated" if item != old else "unchanged")
                if dismissed:
                    data["dismissed_candidates"].remove(entry)
                    data["items"].append(item)
                else:
                    data["items"][data["items"].index(old)] = item
            if action != "unchanged":
                item["updated_at"] = now
                self._save_catalog(data)
            return {"ok": True, "action": action, "item": item, "item_id": item["id"], "item_revision": item_revision(item), "warnings": warnings}

    def create_item(self, title, *, doi=None, arxiv=None, urls=None, source_refs=None, collection_source="manual", authors=None, year=None, request_key=""):
        """Legacy internal entry point; callers must supply a real locator, never title inference."""
        locator = doi or arxiv or ((urls or [{}])[0].get("url"))
        return self.upsert(locator, title=title, authors=authors, year=year, doi=doi, arxiv=arxiv,
                           source_refs=source_refs, explicit=collection_source != "agent", collection_source=collection_source)

    def update_item(self, item_id, updates, *, expected_item_revision=None):
        allowed = {"title", "locator", "authors", "year"}
        if not isinstance(updates, dict) or set(updates) - allowed:
            raise LiteratureCatalogError("仅允许编辑标题、地址、作者和年份。")
        validate_metadata(updates.get("title"), updates.get("authors"), updates.get("year"))
        if "title" in updates and not updates["title"]:
            raise LiteratureCatalogError("标题不能为空。")
        if "authors" in updates and updates["authors"] is None:
            raise LiteratureCatalogError("作者须为字符串数组。")
        with self._lock():
            data = self._load_catalog()
            old = next((i for i in data["items"] if i["id"] == item_id), None)
            if not old:
                raise LiteratureCatalogError("文献不存在。", "NOT_FOUND", 404)
            if expected_item_revision != item_revision(old):
                raise RevisionConflictError()
            item = copy.deepcopy(old)
            for key, value in updates.items():
                if key != "locator":
                    item[key] = value.strip() if key == "title" else value
            if "locator" in updates:
                ids, url = parse_locator(updates["locator"])
                for key in ("doi", "arxiv"):
                    if ids[key] and item["identifiers"].get(key) and ids[key] != item["identifiers"][key]:
                        raise LiteratureCatalogError("新地址与原强标识矛盾，请移除后以新论文收藏。", "IDENTITY_CONFLICT", 409)
                    if ids[key]:
                        item["identifiers"][key] = ids[key]
                urls = item.setdefault("urls", [])
                entry = {"id": urls[0]["id"] if urls else _new_id(), "url": url, "kind": "other"}
                item["urls"] = [entry] + [u for u in urls[1:] if _normalize_url(u["url"]) != url]
            others = {**data, "items": [i for i in data["items"] if i["id"] != item_id]}
            if _identity_match(item, others):
                raise LiteratureCatalogError("新地址已在另一条文献或移除记录中。", "IDENTITY_CONFLICT", 409)
            item["manual_fields"] = sorted(set(item.get("manual_fields", [])) | set(updates))
            if "title" in updates:
                item["title_is_placeholder"] = False
            if item != old:
                item["updated_at"] = _utc_now()
                data["items"][data["items"].index(old)] = item
                self._save_catalog(data)
            return {"ok": True, "item_id": item_id, "item": item, "item_revision": item_revision(item), "warnings": []}

    def remove_item(self, item_id, *, expected_item_revision=None):
        with self._lock():
            data = self._load_catalog()
            item = next((i for i in data["items"] if i["id"] == item_id), None)
            if item is None:
                if any(d.get("item", {}).get("id") == item_id for d in data["dismissed_candidates"]):
                    return {"ok": True, "action": "unchanged"}
                raise LiteratureCatalogError("文献不存在。", "NOT_FOUND", 404)
            if expected_item_revision != item_revision(item):
                raise RevisionConflictError()
            data["items"].remove(item)
            data["dismissed_candidates"].append({"item": item, "dismissed_at": _utc_now()})
            self._save_catalog(data)
            return {"ok": True, "action": "removed", "item_id": item_id}


def attachment_for(project, relative):
    target = safe_path(project["source_root"], relative)
    if not target.is_file() or target.stat().st_size > MAX_ATTACHMENT_SIZE:
        raise LiteratureCatalogError("原文不存在或超过50 MiB限制。")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    path = target.relative_to(Path(project["source_root"]).resolve()).as_posix()
    return {"id": item_revision([path, digest.hexdigest()])[:32], "path": path, "sha256": digest.hexdigest(),
            "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream", "added_at": _utc_now()}


def note_matches(project, relative, papers):
    target = safe_path(project["wiki_root"], relative)
    if target.suffix.lower() != ".md" or not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
        return False
    text = target.read_text(encoding="utf-8")
    # Only an explicit paper_file field is binding evidence; not title similarity or sources.
    front = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    field = re.search(r"^paper_file:\s*(.*?)\s*$", front[1], re.M) if front else None
    if not field:
        return False
    value = field[1].strip().strip("\"'")
    try:
        bound = safe_path(project["source_root"], value)
    except LiteratureCatalogError:
        return False
    return any(bound == safe_path(project["source_root"], p) for p in papers)


def literature_collect(project_id, locator, request_id, *, title=None, authors=None, year=None, doi=None, arxiv=None,
                       source=None, paper_file=None, reading_note_paths=None, home=None):
    if not isinstance(request_id, str) or not HEX_ID.fullmatch(request_id):
        raise LiteratureCatalogError("request_id 须为32位小写 hex。")
    project = project_for(project_id, home)
    catalog = LiteratureCatalog(project["wiki_root"])
    source = {} if source is None else source
    if not isinstance(source, dict) or set(source) - {"locator", "occurred_at", "summary"}:
        raise LiteratureCatalogError("明确收藏的来源字段无效。")
    ref = source_ref("manual", source.get("locator") or "manual:" + request_id, request_id,
                     occurred_at=source.get("occurred_at"), summary=source.get("summary", ""))
    attachments = [attachment_for(project, paper_file)] if paper_file is not None else []
    notes = reading_note_paths if reading_note_paths is not None else []
    if not isinstance(notes, list) or len(notes) > MAX_READING_NOTE_PATHS or any(not isinstance(n, str) for n in notes):
        raise LiteratureCatalogError("笔记路径须为最多20项的字符串数组。")
    if notes:
        ids, url = parse_locator(locator)
        probe = {"identifiers": ids, "urls": [{"url": url}]}
        match = _identity_match(probe, catalog._load_catalog())
        papers = [a["path"] for a in attachments] + ([a["path"] for a in match[2].get("attachments", [])] if match else [])
        if any(not note_matches(project, n, papers) for n in notes):
            raise LiteratureCatalogError("笔记必须存在且以 paper_file 明确绑定本条目原文。")
    result = catalog.upsert(locator, title=title, authors=authors, year=year, doi=doi, arxiv=arxiv,
                            source_refs=[ref], explicit=True, attachments=attachments, reading_note_paths=notes, collection_source="manual")
    # Host/HTTP results intentionally do not return all source refs or the full catalog.
    return {k: v for k, v in result.items() if k != "item"} | {"project_id": project_id, "url": f"/project/{project_id}/literature/item/{result['item_id']}"}
