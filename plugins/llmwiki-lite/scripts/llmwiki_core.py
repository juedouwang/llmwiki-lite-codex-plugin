"""Small deterministic helpers for the LLM Wiki Codex plugin.

The module deliberately performs filesystem mechanics only. It does not summarize,
rank scientific importance, infer claims, or decide what the Wiki should say.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

def plugin_version() -> str:
    """Return the plugin version from the manifest, falling back to a safe default."""
    manifest = Path(__file__).resolve().parent.parent / ".codex-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        version = str(data.get("version") or "").strip()
        if version:
            return version
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return "0.0.0"


DEFAULT_STATE_DIR = ".llmwiki"
DEFAULT_WIKI_DIR = "wiki"
DEFAULT_HASH_LIMIT = 10 * 1024 * 1024
DEFAULT_READ_CHARS = 100_000
DEFAULT_SEARCH_FILE_BYTES = 2 * 1024 * 1024

DEFAULT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".llmwiki",
    "wiki",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".idea",
    ".vscode",
}

DEFAULT_IGNORED_GLOBS = (
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.o",
    "*.obj",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.exe",
    "*.bin",
    "*.ckpt",
    "*.safetensors",
    "*.pt",
    "*.pth",
    "*.onnx",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.7z",
    "*.rar",
)

TEXT_EXTENSIONS = {
    "",
    ".c",
    ".cc",
    ".cfg",
    ".clj",
    ".cljs",
    ".cmake",
    ".conf",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".cu",
    ".cuh",
    ".dart",
    ".env",
    ".ex",
    ".exs",
    ".go",
    ".graphql",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".kt",
    ".kts",
    ".less",
    ".lua",
    ".m",
    ".md",
    ".mdx",
    ".mk",
    ".mm",
    ".php",
    ".pl",
    ".properties",
    ".proto",
    ".ps1",
    ".py",
    ".r",
    ".rb",
    ".rs",
    ".rst",
    ".sass",
    ".scala",
    ".scss",
    ".sh",
    ".sql",
    ".svelte",
    ".swift",
    ".tex",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}


class LLMWikiError(ValueError):
    """Expected user-facing failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMWikiError(f"Invalid JSON state: {path}") from exc


def _resolve_directory(raw: str, *, must_exist: bool = True) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise LLMWikiError("A non-empty absolute or relative directory path is required.")
    path = Path(raw).expanduser().resolve(strict=False)
    if must_exist and not path.is_dir():
        raise LLMWikiError(f"Directory does not exist: {path}")
    return path


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_child(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or "\x00" in relative:
        raise LLMWikiError("A non-empty relative path is required.")
    candidate_path = Path(relative)
    if candidate_path.is_absolute():
        raise LLMWikiError("Path must be relative to the configured root.")
    candidate = (root / candidate_path).resolve(strict=False)
    if not _within(candidate, root):
        raise LLMWikiError("Path escapes the configured root.")
    return candidate


def _state_root(project_root: Path, raw: str | None) -> Path:
    if raw:
        return _resolve_directory(raw, must_exist=False)
    return project_root / DEFAULT_STATE_DIR


def _load_config(project_root: Path, state_root_raw: str | None = None) -> tuple[Path, Path, dict[str, Any]]:
    state_root = _state_root(project_root, state_root_raw)
    config_path = state_root / "config.json"
    config = _read_json(config_path, {})
    if not config:
        wiki_root = project_root / DEFAULT_WIKI_DIR
        config = {
            "version": 1,
            "project_root": str(project_root),
            "state_root": str(state_root),
            "wiki_root": str(wiki_root),
        }
        return state_root, wiki_root, config
    if config.get("version") != 1:
        raise LLMWikiError("Unsupported config version.")
    configured_project = Path(str(config.get("project_root", ""))).resolve(strict=False)
    if configured_project != project_root:
        raise LLMWikiError("Config belongs to a different project root.")
    wiki_root = Path(str(config.get("wiki_root", ""))).expanduser().resolve(strict=False)
    return state_root, wiki_root, config


def init_project(
    project_root: str,
    *,
    state_root: str | None = None,
    wiki_root: str | None = None,
) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    state = _state_root(project, state_root)
    wiki = _resolve_directory(wiki_root, must_exist=False) if wiki_root else project / DEFAULT_WIKI_DIR
    state.mkdir(parents=True, exist_ok=True)
    wiki.mkdir(parents=True, exist_ok=True)
    config = {
        "version": 1,
        "plugin_version": plugin_version(),
        "project_root": str(project),
        "state_root": str(state),
        "wiki_root": str(wiki),
        "created_at": utc_now(),
    }
    existing = _read_json(state / "config.json", {})
    if existing:
        config["created_at"] = existing.get("created_at", config["created_at"])
    config["updated_at"] = utc_now()
    _write_json(state / "config.json", config)
    if not (state / "manifest.json").exists():
        _write_json(
            state / "manifest.json",
            {"version": 1, "generated_at": None, "files": {}},
        )
    (state / "events.jsonl").touch(exist_ok=True)
    return {
        "ok": True,
        "project_root": str(project),
        "state_root": str(state),
        "wiki_root": str(wiki),
        "created": not bool(existing),
    }


def _ignore_patterns(project: Path) -> list[str]:
    path = project / ".llmwikiignore"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise LLMWikiError(".llmwikiignore must be valid UTF-8 text.") from exc
    patterns: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        patterns.append(stripped.replace("\\", "/").lstrip("/"))
    return patterns


def _ignored(relative: str, *, is_dir: bool, patterns: Iterable[str]) -> bool:
    normalized = relative.replace("\\", "/").strip("/")
    name = normalized.rsplit("/", 1)[-1]
    if is_dir and name in DEFAULT_IGNORED_DIRS:
        return True
    if not is_dir and any(fnmatch.fnmatch(name.lower(), pat.lower()) for pat in DEFAULT_IGNORED_GLOBS):
        return True
    for pattern in patterns:
        pattern = pattern.rstrip("/")
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        if is_dir and normalized.startswith(pattern + "/"):
            return True
    return False


def _walk_files(project: Path) -> Iterable[Path]:
    patterns = _ignore_patterns(project)
    for current, dirs, files in os.walk(project, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            candidate = current_path / dirname
            if candidate.is_symlink():
                continue
            relative = candidate.relative_to(project).as_posix()
            if not _ignored(relative, is_dir=True, patterns=patterns):
                kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for filename in sorted(files):
            candidate = current_path / filename
            if candidate.is_symlink() or not candidate.is_file():
                continue
            relative = candidate.relative_to(project).as_posix()
            if not _ignored(relative, is_dir=False, patterns=patterns):
                yield candidate


def _sha256(path: Path, size: int, limit: int) -> str | None:
    if size > limit:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_entry(path: Path, project: Path, hash_limit_bytes: int) -> dict[str, Any]:
    stat = path.stat()
    size = int(stat.st_size)
    return {
        "path": path.relative_to(project).as_posix(),
        "size": size,
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": _sha256(path, size, hash_limit_bytes),
        "text_candidate": path.suffix.lower() in TEXT_EXTENSIONS,
    }


def list_files(
    project_root: str,
    *,
    pattern: str = "*",
    max_files: int = 500,
) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    max_files = max(1, min(int(max_files), 5000))
    records: list[dict[str, Any]] = []
    total = 0
    for path in _walk_files(project):
        relative = path.relative_to(project).as_posix()
        if not fnmatch.fnmatch(relative, pattern) and not fnmatch.fnmatch(path.name, pattern):
            continue
        total += 1
        if len(records) < max_files:
            stat = path.stat()
            records.append(
                {
                    "path": relative,
                    "size": int(stat.st_size),
                    "extension": path.suffix.lower(),
                    "text_candidate": path.suffix.lower() in TEXT_EXTENSIONS,
                }
            )
    return {"ok": True, "total": total, "truncated": total > len(records), "files": records}


def snapshot(
    project_root: str,
    *,
    state_root: str | None = None,
    hash_limit_bytes: int = DEFAULT_HASH_LIMIT,
    save: bool = True,
    max_changes: int = 200,
) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    state, _, _ = _load_config(project, state_root)
    state.mkdir(parents=True, exist_ok=True)
    hash_limit_bytes = max(0, min(int(hash_limit_bytes), 1024 * 1024 * 1024))
    previous_payload = _read_json(state / "manifest.json", {"files": {}})
    previous = previous_payload.get("files", {}) if isinstance(previous_payload, dict) else {}
    if not isinstance(previous, dict):
        previous = {}
    current: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for path in _walk_files(project):
        try:
            entry = _file_entry(path, project, hash_limit_bytes)
        except OSError as exc:
            failures.append({"path": path.relative_to(project).as_posix(), "error": str(exc)})
            continue
        current[entry["path"]] = entry

    previous_paths = set(previous)
    current_paths = set(current)
    added = sorted(current_paths - previous_paths)
    deleted = sorted(previous_paths - current_paths)
    modified: list[str] = []
    for relative in sorted(previous_paths & current_paths):
        before = previous.get(relative)
        after = current[relative]
        if not isinstance(before, dict):
            modified.append(relative)
            continue
        before_hash = before.get("sha256")
        after_hash = after.get("sha256")
        if before_hash is not None and after_hash is not None:
            changed = before_hash != after_hash
        else:
            changed = (before.get("size"), before.get("mtime_ns")) != (
                after.get("size"),
                after.get("mtime_ns"),
            )
        if changed:
            modified.append(relative)

    generated_at = utc_now()
    if save:
        _write_json(
            state / "manifest.json",
            {
                "version": 1,
                "generated_at": generated_at,
                "hash_limit_bytes": hash_limit_bytes,
                "files": current,
            },
        )
    max_changes = max(1, min(int(max_changes), 2000))
    all_changes = added + modified + deleted
    return {
        "ok": True,
        "saved": bool(save),
        "generated_at": generated_at,
        "file_count": len(current),
        "changes": {
            "added": added[:max_changes],
            "modified": modified[:max_changes],
            "deleted": deleted[:max_changes],
            "total": len(all_changes),
            "truncated": len(all_changes) > max_changes,
        },
        "failures": failures[:50],
    }


def _read_events(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def status(project_root: str, *, state_root: str | None = None) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    state, wiki, config = _load_config(project, state_root)
    manifest = _read_json(state / "manifest.json", {})
    files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
    pages = list(wiki.rglob("*.md")) if wiki.exists() else []
    events = _read_events(state / "events.jsonl", 50)
    dirty_paths = sorted(
        {
            path
            for event in events
            for path in event.get("paths", [])
            if isinstance(path, str)
        }
    )
    return {
        "ok": True,
        "initialized": (state / "config.json").exists(),
        "project_root": str(project),
        "state_root": str(state),
        "wiki_root": str(wiki),
        "snapshot_at": manifest.get("generated_at") if isinstance(manifest, dict) else None,
        "snapshot_file_count": len(files) if isinstance(files, dict) else 0,
        "wiki_page_count": len(pages),
        "recent_event_count": len(events),
        "dirty_paths": dirty_paths[:200],
        "config": config,
    }


def read_file(
    project_root: str,
    path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = DEFAULT_READ_CHARS,
) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    target = _safe_child(project, path)
    if not target.is_file():
        raise LLMWikiError(f"File does not exist: {path}")
    if target.suffix.lower() not in TEXT_EXTENSIONS:
        raise LLMWikiError("File is not in the supported text-extension set.")
    start = max(1, int(start_line))
    end = start + 499 if end_line is None else max(start, min(int(end_line), start + 4999))
    max_chars = max(1, min(int(max_chars), 500_000))
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise LLMWikiError("File is not valid UTF-8 text.") from exc
    selected = lines[start - 1 : end]
    text = "\n".join(selected)
    truncated = len(text) > max_chars or end < len(lines)
    if len(text) > max_chars:
        text = text[:max_chars]
    return {
        "ok": True,
        "path": target.relative_to(project).as_posix(),
        "start_line": start,
        "end_line": start + max(0, len(selected) - 1),
        "total_lines": len(lines),
        "truncated": truncated,
        "content": text,
    }


def search(
    project_root: str,
    query: str,
    *,
    path_pattern: str = "*",
    regex: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
    max_file_bytes: int = DEFAULT_SEARCH_FILE_BYTES,
) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    if not isinstance(query, str) or not query:
        raise LLMWikiError("query must be a non-empty string.")
    max_results = max(1, min(int(max_results), 1000))
    max_file_bytes = max(1, min(int(max_file_bytes), 50 * 1024 * 1024))
    flags = 0 if case_sensitive else re.IGNORECASE
    expression = query if regex else re.escape(query)
    try:
        matcher = re.compile(expression, flags)
    except re.error as exc:
        raise LLMWikiError(f"Invalid regular expression: {exc}") from exc
    results: list[dict[str, Any]] = []
    searched_files = 0
    skipped_files = 0
    for path in _walk_files(project):
        relative = path.relative_to(project).as_posix()
        if not fnmatch.fnmatch(relative, path_pattern) and not fnmatch.fnmatch(path.name, path_pattern):
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS or path.stat().st_size > max_file_bytes:
            skipped_files += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped_files += 1
            continue
        searched_files += 1
        for number, line in enumerate(text.splitlines(), 1):
            if matcher.search(line):
                results.append({"path": relative, "line": number, "text": line[:500]})
                if len(results) >= max_results:
                    return {
                        "ok": True,
                        "query": query,
                        "results": results,
                        "truncated": True,
                        "searched_files": searched_files,
                        "skipped_files": skipped_files,
                    }
    return {
        "ok": True,
        "query": query,
        "results": results,
        "truncated": False,
        "searched_files": searched_files,
        "skipped_files": skipped_files,
    }


def _wiki_page_title(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return path.stem
    frontmatter = re.search(r"(?ms)\A---\s*\n(.*?)\n---\s*\n", text)
    if frontmatter:
        match = re.search(r'(?m)^title:\s*["\']?(.*?)["\']?\s*$', frontmatter.group(1))
        if match:
            return match.group(1).strip()
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    return heading.group(1).strip() if heading else path.stem


def _update_wiki_index(wiki_root: Path) -> None:
    index_path = wiki_root / "index.md"
    pages = sorted(path for path in wiki_root.rglob("*.md") if path != index_path)
    entries = []
    for page in pages:
        relative = page.relative_to(wiki_root).as_posix()
        target = relative[:-3]
        entries.append(f"- [[{target}]] — {_wiki_page_title(page)}")
    generated = "<!-- llmwiki:index:start -->\n" + ("\n".join(entries) or "_暂无知识页。_") + "\n<!-- llmwiki:index:end -->"
    if index_path.exists():
        text = index_path.read_text(encoding="utf-8")
        pattern = re.compile(r"(?s)<!-- llmwiki:index:start -->.*?<!-- llmwiki:index:end -->")
        if pattern.search(text):
            text = pattern.sub(generated, text)
        else:
            text = text.rstrip() + "\n\n" + generated + "\n"
    else:
        text = "# Wiki Index\n\n" + generated + "\n"
    _atomic_write_text(index_path, text)


def wiki_write(
    project_root: str,
    page_path: str,
    content: str,
    *,
    state_root: str | None = None,
    overwrite: bool = True,
    update_index: bool = True,
) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    _, wiki_root, _ = _load_config(project, state_root)
    wiki_root.mkdir(parents=True, exist_ok=True)
    normalized = page_path.replace("\\", "/")
    if not normalized.lower().endswith(".md"):
        normalized += ".md"
    target = _safe_child(wiki_root, normalized)
    if target.exists() and not overwrite:
        raise LLMWikiError("Wiki page already exists and overwrite is false.")
    if not isinstance(content, str) or not content.strip():
        raise LLMWikiError("Wiki content must be non-empty.")
    _atomic_write_text(target, content.rstrip() + "\n")
    if update_index and target.name.lower() != "index.md":
        _update_wiki_index(wiki_root)
    return {
        "ok": True,
        "page": target.relative_to(wiki_root).as_posix(),
        "bytes": target.stat().st_size,
        "index_updated": bool(update_index and target.name.lower() != "index.md"),
    }


def wiki_list(project_root: str, *, state_root: str | None = None) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    _, wiki_root, _ = _load_config(project, state_root)
    pages: list[dict[str, Any]] = []
    if wiki_root.exists():
        for path in sorted(wiki_root.rglob("*.md")):
            pages.append(
                {
                    "path": path.relative_to(wiki_root).as_posix(),
                    "title": _wiki_page_title(path),
                    "bytes": path.stat().st_size,
                }
            )
    return {"ok": True, "wiki_root": str(wiki_root), "pages": pages}


def _frontmatter_sources(text: str) -> list[str]:
    match = re.match(r"(?s)\A---\s*\n(.*?)\n---\s*\n", text)
    if not match:
        return []
    lines = match.group(1).splitlines()
    sources: list[str] = []
    in_sources = False
    for line in lines:
        if re.match(r"^sources:\s*$", line):
            in_sources = True
            continue
        if in_sources:
            item = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if item:
                sources.append(item.group(1).strip().strip("\"'"))
                continue
            if line and not line[0].isspace():
                break
    return sources


def wiki_check(project_root: str, *, state_root: str | None = None) -> dict[str, Any]:
    project = _resolve_directory(project_root)
    _, wiki_root, _ = _load_config(project, state_root)
    if not wiki_root.exists():
        return {"ok": True, "pages": 0, "broken_links": [], "missing_sources": []}
    page_paths = sorted(wiki_root.rglob("*.md"))
    known: set[str] = set()
    for path in page_paths:
        relative = path.relative_to(wiki_root).as_posix()
        known.add(relative[:-3].lower())
        known.add(path.stem.lower())
        known.add(_wiki_page_title(path).lower())
    broken: list[dict[str, str]] = []
    missing_sources: list[dict[str, str]] = []
    for path in page_paths:
        relative = path.relative_to(wiki_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for raw_target in re.findall(r"\[\[([^\]]+)\]\]", text):
            target = raw_target.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
            if target and target.lower() not in known:
                broken.append({"page": relative, "target": target})
        for source in _frontmatter_sources(text):
            source_path = (project / source).resolve(strict=False)
            if not _within(source_path, project) or not source_path.exists():
                missing_sources.append({"page": relative, "source": source})
    return {
        "ok": True,
        "pages": len(page_paths),
        "broken_links": broken[:500],
        "missing_sources": missing_sources[:500],
        "truncated": len(broken) > 500 or len(missing_sources) > 500,
    }