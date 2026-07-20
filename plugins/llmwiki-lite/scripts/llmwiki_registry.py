"""Lightweight project registry and storage preferences for LLM Wiki."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from llmwiki_core import LLMWikiError, init_project

REGISTRY_VERSION = 1
SETTINGS_VERSION = 1
HOME_ENV = "LLMWIKI_HOME"


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def llmwiki_home(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get(HOME_ENV)
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return (Path(base) / "LLMWiki").resolve(strict=False)
    return (Path.home() / ".local" / "share" / "llmwiki").resolve(strict=False)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
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
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMWikiError(f"Invalid LLM Wiki JSON: {path}") from exc


@contextmanager
def _home_lock(home: Path, timeout: float = 5.0) -> Iterator[None]:
    home.mkdir(parents=True, exist_ok=True)
    lock = home / ".registry.lock"
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > max(30.0, timeout * 4)
                if stale:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise LLMWikiError("Timed out waiting for the project registry lock.")
            time.sleep(0.05)
    try:
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _default_settings() -> dict[str, Any]:
    return {
        "version": SETTINGS_VERSION,
        "default_wiki_root": None,
        "current_project_id": None,
        "web_host": "127.0.0.1",
        "web_port": 8765,
    }


def load_settings(home: str | None = None) -> dict[str, Any]:
    root = llmwiki_home(home)
    payload = _read_json(root / "settings.json", _default_settings())
    if not isinstance(payload, dict) or payload.get("version") != SETTINGS_VERSION:
        raise LLMWikiError("Unsupported settings format.")
    merged = _default_settings()
    merged.update(payload)
    return merged


def update_settings(
    *,
    home: str | None = None,
    default_wiki_root: str | None | object = ...,
    current_project_id: str | None | object = ...,
    web_port: int | object = ...,
) -> dict[str, Any]:
    root = llmwiki_home(home)
    with _home_lock(root):
        settings = load_settings(str(root))
        if default_wiki_root is not ...:
            if default_wiki_root is None or not str(default_wiki_root).strip():
                settings["default_wiki_root"] = None
            else:
                selected = (
                    Path(str(default_wiki_root)).expanduser().resolve(strict=False)
                )
                if selected.exists() and not selected.is_dir():
                    raise LLMWikiError("default_wiki_root must be a directory path.")
                selected.mkdir(parents=True, exist_ok=True)
                settings["default_wiki_root"] = str(selected)
        if current_project_id is not ...:
            if current_project_id is not None and not isinstance(
                current_project_id, str
            ):
                raise LLMWikiError("current_project_id must be a string or null.")
            settings["current_project_id"] = current_project_id
        if web_port is not ...:
            port = int(web_port)
            if port < 1024 or port > 65535:
                raise LLMWikiError("web_port must be between 1024 and 65535.")
            settings["web_port"] = port
        _write_json(root / "settings.json", settings)
    return settings


def _default_registry() -> dict[str, Any]:
    return {"version": REGISTRY_VERSION, "projects": []}


def load_registry(home: str | None = None) -> dict[str, Any]:
    root = llmwiki_home(home)
    payload = _read_json(root / "registry.json", _default_registry())
    if not isinstance(payload, dict) or payload.get("version") != REGISTRY_VERSION:
        raise LLMWikiError("Unsupported project registry format.")
    projects = payload.get("projects")
    if not isinstance(projects, list) or any(
        not isinstance(item, dict) for item in projects
    ):
        raise LLMWikiError("Project registry projects must be an array of objects.")
    return payload


def _canonical_source(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise LLMWikiError("source_root must be a non-empty directory path.")
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_dir():
        raise LLMWikiError(f"Source project does not exist: {resolved}")
    return resolved


def _directory(path: str, field: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise LLMWikiError(f"{field} must be a non-empty directory path.")
    selected = Path(path).expanduser().resolve(strict=False)
    if selected.exists() and not selected.is_dir():
        raise LLMWikiError(f"{field} must be a directory path.")
    return selected


def _source_key(path: Path) -> str:
    rendered = str(path)
    return os.path.normcase(rendered) if os.name == "nt" else rendered


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return normalized[:48] or "project"


def _project_id(source: Path, name: str) -> str:
    digest = hashlib.sha256(_source_key(source).encode("utf-8")).hexdigest()[:8]
    return f"{_slug(name)}-{digest}"


def list_projects(home: str | None = None) -> dict[str, Any]:
    root = llmwiki_home(home)
    registry = load_registry(str(root))
    projects = sorted(
        registry["projects"],
        key=lambda item: (str(item.get("name", "")).lower(), str(item.get("id", ""))),
    )
    settings = load_settings(str(root))
    return {
        "ok": True,
        "home": str(root),
        "default_wiki_root": settings.get("default_wiki_root"),
        "current_project_id": settings.get("current_project_id"),
        "projects": projects,
    }


def _find_record(
    projects: list[dict[str, Any]], identifier: str
) -> dict[str, Any] | None:
    normalized = identifier.strip()
    for project in projects:
        if project.get("id") == normalized:
            return project
    lowered = normalized.lower()
    by_name = [
        project
        for project in projects
        if str(project.get("name", "")).lower() == lowered
    ]
    if len(by_name) == 1:
        return by_name[0]
    try:
        candidate = Path(normalized).expanduser().resolve(strict=False)
        key = _source_key(candidate)
        for project in projects:
            registered = Path(str(project.get("source_root", ""))).resolve(strict=False)
            if _source_key(registered) == key:
                return project
    except (OSError, RuntimeError):
        pass
    return None


def get_project(
    identifier: str | None = None,
    *,
    home: str | None = None,
    current_path: str | None = None,
) -> dict[str, Any]:
    root = llmwiki_home(home)
    registry = load_registry(str(root))
    projects = registry["projects"]
    selected = identifier
    if not selected and current_path:
        current = Path(current_path).expanduser().resolve(strict=False)
        matches: list[tuple[int, dict[str, Any]]] = []
        for project in projects:
            source = Path(str(project.get("source_root", ""))).resolve(strict=False)
            try:
                current.relative_to(source)
            except ValueError:
                continue
            matches.append((len(source.parts), project))
        if matches:
            return {"ok": True, "project": max(matches, key=lambda item: item[0])[1]}
    if not selected:
        selected = load_settings(str(root)).get("current_project_id")
    if not isinstance(selected, str) or not selected:
        raise LLMWikiError("No project identifier or current project is available.")
    record = _find_record(projects, selected)
    if record is None:
        raise LLMWikiError(f"Project is not registered: {selected}")
    return {"ok": True, "project": record}


def register_project(
    source_root: str,
    *,
    name: str | None = None,
    wiki_root: str | None = None,
    state_root: str | None = None,
    home: str | None = None,
    select: bool = True,
) -> dict[str, Any]:
    source = _canonical_source(source_root)
    display_name = (name or source.name).strip() or source.name
    root = llmwiki_home(home)
    root.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    with _home_lock(root):
        registry = load_registry(str(root))
        source_key = _source_key(source)
        existing = next(
            (
                record
                for record in registry["projects"]
                if _source_key(
                    Path(str(record.get("source_root", ""))).resolve(strict=False)
                )
                == source_key
            ),
            None,
        )
        if existing is not None:
            if name:
                existing["name"] = display_name
            if wiki_root:
                existing["wiki_root"] = str(_directory(wiki_root, "wiki_root"))
            if state_root:
                existing["state_root"] = str(_directory(state_root, "state_root"))
            existing["last_opened_at"] = now
            existing["updated_at"] = now
            record = existing
        else:
            project_id = _project_id(source, display_name)
            settings = load_settings(str(root))
            selected_state = (
                _directory(state_root, "state_root")
                if state_root
                else root / "projects" / project_id / "state"
            )
            if wiki_root:
                selected_wiki = _directory(wiki_root, "wiki_root")
            elif settings.get("default_wiki_root"):
                selected_wiki = Path(str(settings["default_wiki_root"])) / project_id
            else:
                selected_wiki = source / "wiki"
            record = {
                "id": project_id,
                "name": display_name,
                "source_root": str(source),
                "state_root": str(selected_state.resolve(strict=False)),
                "wiki_root": str(selected_wiki.resolve(strict=False)),
                "created_at": now,
                "updated_at": now,
                "last_opened_at": now,
            }
            registry["projects"].append(record)
        _write_json(root / "registry.json", registry)
        if select:
            settings = load_settings(str(root))
            settings["current_project_id"] = record["id"]
            _write_json(root / "settings.json", settings)
    init_project(
        str(source),
        state_root=str(record["state_root"]),
        wiki_root=str(record["wiki_root"]),
    )
    return {"ok": True, "project": record, "existing": existing is not None}


def select_project(identifier: str, *, home: str | None = None) -> dict[str, Any]:
    record = get_project(identifier, home=home)["project"]
    settings = update_settings(home=home, current_project_id=record["id"])
    return {
        "ok": True,
        "project": record,
        "current_project_id": settings["current_project_id"],
    }


def _copy_directory(previous: Path, selected: Path, field: str) -> bool:
    try:
        selected.relative_to(previous)
        raise LLMWikiError(
            f"The new {field} directory cannot be inside the old directory."
        )
    except ValueError:
        pass
    try:
        previous.relative_to(selected)
        raise LLMWikiError(
            f"The new {field} directory cannot contain the old directory."
        )
    except ValueError:
        pass
    if not previous.exists():
        selected.mkdir(parents=True, exist_ok=True)
        return False
    if selected.exists() and any(selected.iterdir()):
        raise LLMWikiError(
            f"The new {field} directory is not empty; refusing to merge or overwrite it."
        )
    if selected.exists():
        selected.rmdir()
    selected.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(previous, selected)
    return True


def update_project_storage(
    identifier: str,
    *,
    wiki_root: str | None = None,
    state_root: str | None = None,
    home: str | None = None,
    copy_existing: bool = True,
) -> dict[str, Any]:
    if wiki_root is None and state_root is None:
        raise LLMWikiError("Provide wiki_root, state_root, or both.")
    root = llmwiki_home(home)
    with _home_lock(root):
        registry = load_registry(str(root))
        record = _find_record(registry["projects"], identifier)
        if record is None:
            raise LLMWikiError(f"Project is not registered: {identifier}")
        previous_wiki = Path(str(record["wiki_root"])).resolve(strict=False)
        previous_state = Path(str(record["state_root"])).resolve(strict=False)
        selected_wiki = (
            _directory(wiki_root, "wiki_root") if wiki_root else previous_wiki
        )
        selected_state = (
            _directory(state_root, "state_root") if state_root else previous_state
        )
        if selected_wiki == selected_state:
            raise LLMWikiError(
                "wiki_root and state_root must be different directories."
            )
        wiki_copied = False
        state_copied = False
        if selected_wiki != previous_wiki:
            if copy_existing:
                wiki_copied = _copy_directory(previous_wiki, selected_wiki, "Wiki")
            else:
                selected_wiki.mkdir(parents=True, exist_ok=True)
        if selected_state != previous_state:
            if copy_existing:
                state_copied = _copy_directory(previous_state, selected_state, "state")
            else:
                selected_state.mkdir(parents=True, exist_ok=True)
        record["wiki_root"] = str(selected_wiki)
        record["state_root"] = str(selected_state)
        record["updated_at"] = utc_now()
        _write_json(root / "registry.json", registry)
    init_project(
        str(record["source_root"]),
        state_root=str(selected_state),
        wiki_root=str(selected_wiki),
    )
    return {
        "ok": True,
        "project": record,
        "wiki_copied": wiki_copied,
        "state_copied": state_copied,
        "previous_wiki_root": str(previous_wiki),
        "previous_state_root": str(previous_state),
        "old_files_preserved": True,
    }


def unregister_project(identifier: str, *, home: str | None = None) -> dict[str, Any]:
    root = llmwiki_home(home)
    with _home_lock(root):
        registry = load_registry(str(root))
        record = _find_record(registry["projects"], identifier)
        if record is None:
            raise LLMWikiError(f"Project is not registered: {identifier}")
        registry["projects"] = [
            item for item in registry["projects"] if item is not record
        ]
        _write_json(root / "registry.json", registry)
        settings = load_settings(str(root))
        if settings.get("current_project_id") == record.get("id"):
            settings["current_project_id"] = None
            _write_json(root / "settings.json", settings)
    return {"ok": True, "unregistered": record, "files_deleted": False}
