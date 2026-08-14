"""Deterministic storage helpers for Codex-authored research process records.

New records use one Markdown file per calendar day. Each explicit Codex action
appends one human-readable entry to that day's file. Legacy one-record pages
remain readable and are included in listings without being rewritten.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llmwiki_core import LLMWikiError, wiki_list, wiki_write

RECORDS_DIR = "records"
RECORD_TYPE = "research-record"
DAILY_RECORD_TYPE = "research-record-daily"
DAILY_TITLE_SUFFIX = "\u79d1\u7814\u8bb0\u5f55"
MAX_RECORD_BYTES = 4 * 1024 * 1024
MAX_LIST_RECORDS = 500
MAX_SLUG_LENGTH = 60
ENTRY_MARKER = "<!-- llmwiki-record-entry "
ENTRY_HEADER_RE = re.compile(r"(?m)^##(?!#)\s+(\d{2}:\d{2})\s*[|\uFF5C]\s*(.+?)\s*$")
MARKER_RE = re.compile(r"(?m)^<!-- llmwiki-record-entry (\{.*?\}) -->\s*$")

SECTION_TITLES = {
    "context": "\u8ba8\u8bba\u80cc\u666f",
    "understanding": "\u9636\u6bb5\u6027\u7406\u89e3",
    "evidence": "\u4f9d\u636e\u4e0e\u5173\u8054\u6750\u6599",
    "conclusion": "\u5f53\u524d\u7ed3\u8bba",
    "decisions": "\u79d1\u7814\u51b3\u7b56",
    "open_questions": "\u5c1a\u672a\u89e3\u51b3\u7684\u95ee\u9898",
    "next_steps": "\u4e0b\u4e00\u6b65\u884c\u52a8",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _project_wiki_root(project_root: str, state_root: str | None = None) -> tuple[Path, Path]:
    listing = wiki_list(project_root, state_root=state_root)
    root = Path(str(listing["wiki_root"])).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root, root / RECORDS_DIR


def _split_record_id(record_id: str) -> tuple[str, str | None]:
    if not isinstance(record_id, str) or not record_id.strip() or "\x00" in record_id:
        raise LLMWikiError("\u79d1\u7814\u8bb0\u5f55 ID \u4e0d\u80fd\u4e3a\u7a7a\u3002")
    normalized = record_id.replace("\\", "/").lstrip("/")
    if "#" in normalized:
        path, fragment = normalized.split("#", 1)
        if not fragment or "#" in fragment or "/" in fragment or "\\" in fragment:
            raise LLMWikiError("\u79d1\u7814\u8bb0\u5f55 ID \u7684 \u6761\u76ee\u6807\u8bc6\u65e0\u6548\u3002")
    else:
        path, fragment = normalized, None
    if not path or path.lower() == RECORDS_DIR:
        raise LLMWikiError("\u79d1\u7814\u8bb0\u5f55\u8def\u5f84\u65e0\u6548\u3002")
    if not path.lower().startswith(RECORDS_DIR + "/"):
        path = f"{RECORDS_DIR}/{path}"
    return path, fragment


def _safe_record_path(wiki_root: Path, record_id: str) -> Path:
    normalized, _ = _split_record_id(record_id)
    candidate = (wiki_root / normalized).resolve(strict=False)
    records_root = (wiki_root / RECORDS_DIR).resolve(strict=False)
    try:
        candidate.relative_to(records_root)
    except ValueError as exc:
        raise LLMWikiError("\u79d1\u7814\u8bb0\u5f55\u8def\u5f84\u4e0d\u80fd\u79bb\u5f00 records \u76ee\u5f55\u3002") from exc
    if candidate == records_root or candidate.suffix.lower() != ".md":
        raise LLMWikiError("\u79d1\u7814\u8bb0\u5f55\u5fc5\u987b\u662f records \u76ee\u5f55\u4e0b\u7684 Markdown \u6587\u4ef6\u3002")
    return candidate


def _text(value: Any, field: str, *, required: bool = False, single_line: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise LLMWikiError(f"{field} \u5fc5\u987b\u662f\u6587\u672c\u3002")
    result = value.strip()
    if single_line:
        result = re.sub(r"\s+", " ", result)
    if required and not result:
        raise LLMWikiError(f"{field} \u4e0d\u80fd\u4e3a\u7a7a\u3002")
    return result


def _items(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise LLMWikiError(f"{field} \u5fc5\u987b\u662f\u5b57\u7b26\u4e32\u5217\u8868\u3002")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise LLMWikiError(f"{field} \u5fc5\u987b\u662f\u5b57\u7b26\u4e32\u5217\u8868\u3002")
        item = item.strip()
        if item:
            result.append(item)
    return result


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _section(title: str, value: str | list[str], level: int = 3) -> str:
    if isinstance(value, list):
        body = "\n".join(f"- {item}" for item in value) or "\uff08\u672a\u586b\u5199\uff09"
    else:
        body = value or "\uff08\u672a\u586b\u5199\uff09"
    return f"{'#' * level} {title}\n\n{body}\n"


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", value, flags=re.UNICODE)
    return value.strip("-")[:MAX_SLUG_LENGTH] or "research-record"


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise LLMWikiError("recorded_at \u5fc5\u987b\u662f ISO 8601 \u65f6\u95f4\u3002") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _date_path(recorded_at: str) -> str:
    parsed = _parse_datetime(recorded_at)
    return f"{RECORDS_DIR}/{parsed:%Y/%m/%Y-%m-%d}.md"


def _entry_key(title: str, recorded_at: str, existing: set[str]) -> str:
    parsed = _parse_datetime(recorded_at)
    base = f"{parsed:%H%M%S}-{_slug(title)}"
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _entry_metadata(
    *,
    entry_key: str,
    title: str,
    recorded_at: str,
    project_id: str,
    tags: list[str],
    related_files: list[str],
    related_pages: list[str],
) -> str:
    payload = {
        "entry_key": entry_key,
        "title": title,
        "recorded_at": recorded_at,
        "project_id": project_id,
        "tags": tags,
        "related_files": related_files,
        "related_pages": related_pages,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{ENTRY_MARKER}{encoded} -->"


def _entry_content(
    *,
    entry_key: str,
    title: str,
    recorded_at: str,
    project_id: str,
    discussion_context: str,
    understanding: str,
    evidence: list[str],
    conclusion: str,
    decisions: list[str],
    open_questions: list[str],
    next_steps: list[str],
    related_files: list[str],
    related_pages: list[str],
    tags: list[str],
) -> str:
    parsed = _parse_datetime(recorded_at)
    lines = [
        f"## {parsed:%H:%M}\uFF5C{title}",
        _entry_metadata(
            entry_key=entry_key,
            title=title,
            recorded_at=recorded_at,
            project_id=project_id,
            tags=tags,
            related_files=related_files,
            related_pages=related_pages,
        ),
        "",
        _section(SECTION_TITLES["context"], discussion_context),
        _section(SECTION_TITLES["understanding"], understanding),
        _section(SECTION_TITLES["evidence"], evidence),
        _section(SECTION_TITLES["conclusion"], conclusion),
        _section(SECTION_TITLES["decisions"], decisions),
        _section(SECTION_TITLES["open_questions"], open_questions),
        _section(SECTION_TITLES["next_steps"], next_steps),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _daily_document(recorded_at: str, project_id: str, entry: str) -> str:
    day = _parse_datetime(recorded_at).strftime("%Y-%m-%d")
    frontmatter = [
        "---",
        f'title: {_yaml_scalar(day + " " + DAILY_TITLE_SUFFIX)}',
        f"type: {DAILY_RECORD_TYPE}",
        f"date: {_yaml_scalar(day)}",
        f"project_id: {_yaml_scalar(project_id)}",
        "---",
        "",
        f"# {day} \u79d1\u7814\u8bb0\u5f55",
        "",
        entry.rstrip(),
        "",
    ]
    return "\n".join(frontmatter)


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    metadata: dict[str, Any] = {}
    list_key: str | None = None
    for line in raw.splitlines():
        match = re.match(r"^([A-Za-z_][\w-]*):(?:\s*(.*))?$", line)
        if match and not line.startswith(" "):
            key, value = match.group(1), (match.group(2) or "").strip()
            list_key = None
            if value == "[]":
                metadata[key] = []
            elif value:
                try:
                    metadata[key] = json.loads(value)
                except json.JSONDecodeError:
                    metadata[key] = value.strip("\"'")
            else:
                metadata[key] = []
                list_key = key
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and list_key:
            value = item.group(1).strip()
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value.strip("\"'")
            metadata.setdefault(list_key, []).append(value)
    return metadata, body


def _clean_entry_content(content: str) -> str:
    cleaned = re.sub(r"(?m)^<!-- llmwiki-record-entry \{.*?\} -->\s*\n?", "", content)
    return cleaned.strip() + "\n"


def _summary(body: str) -> str:
    names = "|".join(re.escape(value) for value in (SECTION_TITLES["understanding"],))
    match = re.search(rf"(?ms)^###\s+(?:{names})\s*\n\s*(.*?)(?=^###\s|\Z)", body)
    if not match:
        match = re.search(rf"(?ms)^##\s+(?:{names})\s*\n\s*(.*?)(?=^##\s|\Z)", body)
    value = match.group(1).strip() if match else ""
    value = re.sub(r"\s+", " ", value).strip("- ")
    return value[:240] + ("\u2026" if len(value) > 240 else "")


def _metadata_record(
    *,
    path: Path,
    root: Path,
    metadata: dict[str, Any],
    body: str,
    content: str,
    record_id: str,
    entry_key: str | None = None,
) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    stat = path.stat()
    record_type = str(metadata.get("type") or RECORD_TYPE)
    recorded_at = str(metadata.get("recorded_at") or "")
    return {
        "id": record_id,
        "path": relative,
        "daily_path": relative if record_type == RECORD_TYPE and entry_key else None,
        "entry_key": entry_key,
        "title": str(metadata.get("title") or path.stem),
        "type": record_type,
        "recorded_at": recorded_at,
        "date": str(metadata.get("date") or (recorded_at[:10] if recorded_at else "")),
        "tags": metadata.get("tags") if isinstance(metadata.get("tags"), list) else [],
        "related_files": metadata.get("related_files") if isinstance(metadata.get("related_files"), list) else [],
        "related_pages": metadata.get("related_pages") if isinstance(metadata.get("related_pages"), list) else [],
        "summary": _summary(body),
        "bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "content": content,
    }


def _read_utf8(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_RECORD_BYTES:
            raise LLMWikiError(f"\u79d1\u7814\u8bb0\u5f55\u6587\u4ef6\u8fc7\u5927\uff1a{path.name}")
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LLMWikiError(f"\u79d1\u7814\u8bb0\u5f55\u4e0d\u662f UTF-8\uff1a{path.name}") from exc


def _parse_daily_records(path: Path, root: Path, text: str) -> list[dict[str, Any]]:
    frontmatter, body = _frontmatter(text)
    if str(frontmatter.get("type")) != DAILY_RECORD_TYPE:
        return []
    relative = path.relative_to(root).as_posix()
    matches = list(ENTRY_HEADER_RE.finditer(body))
    records: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        segment = body[match.start() : end].strip()
        marker = MARKER_RE.search(segment)
        metadata: dict[str, Any] = {}
        if marker:
            try:
                candidate = json.loads(marker.group(1))
                if isinstance(candidate, dict):
                    metadata = candidate
            except json.JSONDecodeError:
                metadata = {}
        title = str(metadata.get("title") or match.group(2).strip())
        day = str(frontmatter.get("date") or path.stem)
        recorded_at = str(metadata.get("recorded_at") or f"{day}T{match.group(1)}:00Z")
        try:
            _parse_datetime(recorded_at)
        except LLMWikiError:
            recorded_at = f"{day}T{match.group(1)}:00Z"
        key = str(metadata.get("entry_key") or f"{match.group(1).replace(':', '')}-{_slug(title)}-{index + 1}")
        record_metadata = {
            "title": title,
            "type": RECORD_TYPE,
            "recorded_at": recorded_at,
            "date": day,
            "project_id": str(metadata.get("project_id") or frontmatter.get("project_id") or ""),
            "tags": metadata.get("tags") if isinstance(metadata.get("tags"), list) else [],
            "related_files": metadata.get("related_files") if isinstance(metadata.get("related_files"), list) else [],
            "related_pages": metadata.get("related_pages") if isinstance(metadata.get("related_pages"), list) else [],
        }
        clean = _clean_entry_content(segment)
        records.append(
            _metadata_record(
                path=path,
                root=root,
                metadata=record_metadata,
                body=clean,
                content=clean,
                record_id=f"{relative}#{key}",
                entry_key=key,
            )
        )
    return records


def _load_legacy_record(path: Path, root: Path, text: str) -> dict[str, Any]:
    metadata, body = _frontmatter(text)
    relative = path.relative_to(root).as_posix()
    return _metadata_record(
        path=path,
        root=root,
        metadata=metadata,
        body=body,
        content=text,
        record_id=relative,
    )


def _load_records_from_path(path: Path, root: Path) -> list[dict[str, Any]]:
    try:
        if path.is_symlink():
            return []
        text = _read_utf8(path)
        frontmatter, _ = _frontmatter(text)
        if str(frontmatter.get("type")) == DAILY_RECORD_TYPE:
            return _parse_daily_records(path, root, text)
        return [_load_legacy_record(path, root, text)]
    except (OSError, LLMWikiError):
        return []


def write_record(
    project_root: str,
    title: str,
    understanding: str,
    *,
    discussion_context: str = "",
    evidence: list[str] | None = None,
    conclusion: str = "",
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
    next_steps: list[str] | None = None,
    related_files: list[str] | None = None,
    related_pages: list[str] | None = None,
    tags: list[str] | None = None,
    project_id: str = "",
    recorded_at: str | None = None,
    state_root: str | None = None,
) -> dict[str, Any]:
    """Append one entry to the current day's Markdown file."""

    title = _text(title, "title", required=True, single_line=True)
    understanding = _text(understanding, "understanding", required=True)
    discussion_context = _text(discussion_context, "discussion_context")
    conclusion = _text(conclusion, "conclusion")
    project_id = _text(project_id, "project_id", single_line=True)
    recorded_at = _text(recorded_at, "recorded_at", single_line=True) or utc_now()
    _parse_datetime(recorded_at)
    evidence = _items(evidence, "evidence")
    decisions = _items(decisions, "decisions")
    open_questions = _items(open_questions, "open_questions")
    next_steps = _items(next_steps, "next_steps")
    related_files = _items(related_files, "related_files")
    related_pages = _items(related_pages, "related_pages")
    tags = _items(tags, "tags")

    root, records_root = _project_wiki_root(project_root, state_root)
    records_root.mkdir(parents=True, exist_ok=True)
    relative = _date_path(recorded_at)
    target = _safe_record_path(root, relative)
    existing_keys: set[str] = set()
    if target.exists():
        text = _read_utf8(target)
        frontmatter, _ = _frontmatter(text)
        if str(frontmatter.get("type")) != DAILY_RECORD_TYPE:
            raise LLMWikiError(f"\u5f53\u5929\u7684\u79d1\u7814\u8bb0\u5f55\u6587\u4ef6\u5df2\u5b58\u5728\uff0c\u4f46\u4e0d\u662f\u65e5\u6863\u683c\u5f0f\uff1a{relative}")
        existing_keys = {
            str(item.get("entry_key"))
            for item in _parse_daily_records(target, root, text)
            if item.get("entry_key")
        }
    key = _entry_key(title, recorded_at, existing_keys)
    entry = _entry_content(
        entry_key=key,
        title=title,
        recorded_at=recorded_at,
        project_id=project_id,
        discussion_context=discussion_context,
        understanding=understanding,
        evidence=evidence,
        conclusion=conclusion,
        decisions=decisions,
        open_questions=open_questions,
        next_steps=next_steps,
        related_files=related_files,
        related_pages=related_pages,
        tags=tags,
    )
    if target.exists():
        new_content = _read_utf8(target).rstrip() + "\n\n" + entry
    else:
        new_content = _daily_document(recorded_at, project_id, entry)
    if len(new_content.encode("utf-8")) > MAX_RECORD_BYTES:
        raise LLMWikiError("\u5f53\u5929\u7684\u79d1\u7814\u8bb0\u5f55\u6587\u4ef6\u8fc7\u5927\uff0c\u8bf7\u5f00\u59cb\u65b0\u7684\u65e5\u6863\u3002")
    result = wiki_write(
        project_root,
        relative,
        new_content,
        state_root=state_root,
        overwrite=target.exists(),
        update_index=True,
    )
    record = next(
        item
        for item in _parse_daily_records(target, root, new_content)
        if str(item.get("entry_key")) == key
    )
    record.pop("content", None)
    record["id"] = f"{relative}#{key}"
    record["path"] = relative
    record["daily_path"] = relative
    record["type"] = RECORD_TYPE
    record["bytes"] = result["bytes"]
    return {"ok": True, "record": record, "wiki_root": str(root)}


def list_records(
    project_root: str,
    *,
    state_root: str | None = None,
    query: str = "",
    tag: str = "",
    max_records: int = 200,
    include_content: bool = False,
) -> dict[str, Any]:
    root, records_root = _project_wiki_root(project_root, state_root)
    max_records = max(1, min(int(max_records), MAX_LIST_RECORDS))
    records: list[dict[str, Any]] = []
    if records_root.is_dir():
        for path in records_root.rglob("*.md"):
            records.extend(_load_records_from_path(path, root))
    query = _text(query, "query", single_line=True).lower()
    if query:
        records = [
            item
            for item in records
            if query
            in " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("summary", "")),
                    " ".join(str(x) for x in item.get("tags", [])),
                    " ".join(str(x) for x in item.get("related_files", [])),
                    " ".join(str(x) for x in item.get("related_pages", [])),
                    str(item.get("path", "")),
                    str(item.get("content", "")),
                ]
            ).lower()
        ]
    tag = _text(tag, "tag", single_line=True).lower()
    if tag:
        records = [
            item
            for item in records
            if any(str(value).lower() == tag for value in item.get("tags") or [])
        ]
    records.sort(
        key=lambda item: (
            str(item.get("recorded_at") or ""),
            str(item.get("updated_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    if not include_content:
        for item in records:
            item.pop("content", None)
    return {
        "ok": True,
        "wiki_root": str(root),
        "records_root": str(records_root),
        "count": len(records),
        "records": records[:max_records],
        "truncated": len(records) > max_records,
    }


def read_record(
    project_root: str,
    record_id: str,
    *,
    state_root: str | None = None,
) -> dict[str, Any]:
    root, _ = _project_wiki_root(project_root, state_root)
    path_id, fragment = _split_record_id(record_id)
    target = _safe_record_path(root, path_id)
    if not target.is_file():
        raise LLMWikiError("\u7814\u7a76\u8bb0\u5f55\u4e0d\u5b58\u5728\u3002")
    text = _read_utf8(target)
    frontmatter, _ = _frontmatter(text)
    if str(frontmatter.get("type")) == DAILY_RECORD_TYPE:
        entries = _parse_daily_records(target, root, text)
        if fragment:
            entries = [item for item in entries if str(item.get("entry_key")) == fragment]
        elif len(entries) != 1:
            raise LLMWikiError("\u65e5\u6863\u5305\u542b\u591a\u6761\u8bb0\u5f55\uff0c\u8bf7\u4f7f\u7528\u5e26 # \u6761\u76ee\u6807\u8bc6\u7684\u79d1\u7814\u8bb0\u5f55 ID\u3002")
        if not entries:
            raise LLMWikiError("\u79d1\u7814\u8bb0\u5f55\u6761\u76ee\u4e0d\u5b58\u5728\u3002")
        return {"ok": True, "wiki_root": str(root), "record": entries[0]}
    if fragment:
        raise LLMWikiError("\u65e7\u7248\u5355\u6761\u79d1\u7814\u8bb0\u5f55\u4e0d\u652f\u6301\u6761\u76ee\u6807\u8bc6\u3002")
    record = _load_legacy_record(target, root, text)
    return {"ok": True, "wiki_root": str(root), "record": record}
