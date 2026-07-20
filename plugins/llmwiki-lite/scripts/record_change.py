"""Fail-open PostToolUse hint recorder for local and registered projects."""

from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from llmwiki_registry import list_projects  # noqa: E402

MAX_INPUT_BYTES = 1024 * 1024
MAX_PATHS = 100


def now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def payload() -> dict[str, Any] | None:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_INPUT_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def successful(value: dict[str, Any]) -> bool:
    if value.get("hook_event_name") not in (None, "PostToolUse"):
        return False
    response = value.get("tool_response")
    return not (
        isinstance(response, dict)
        and (
            response.get("ok") is False
            or response.get("success") is False
            or response.get("isError") is True
            or response.get("is_error") is True
        )
    )


def values(tool_input: dict[str, Any]) -> list[str]:
    out = []
    for key in ("path", "file_path", "paths", "files"):
        raw = tool_input.get(key)
        if isinstance(raw, str):
            out.append(raw)
        elif isinstance(raw, list):
            out.extend(x for x in raw if isinstance(x, str))
    return out[:MAX_PATHS]


def cwd_of(value: dict[str, Any]) -> Path:
    raw = value.get("cwd")
    return (
        Path(raw).expanduser().resolve(strict=False)
        if isinstance(raw, str) and raw.strip()
        else Path.cwd().resolve(strict=False)
    )


def local_project(start: Path) -> tuple[Path, Path] | None:
    start = start.resolve(strict=False)
    start = start.parent if start.is_file() else start
    for candidate in (start, *start.parents):
        state = candidate / ".llmwiki"
        if (state / "config.json").is_file():
            return candidate, state
    return None


def registered_project(cwd: Path, candidates: list[Path]) -> tuple[Path, Path] | None:
    try:
        projects = list_projects()["projects"]
    except Exception:
        return None
    best: tuple[int, Path, Path] | None = None
    for item in projects:
        source = Path(str(item.get("source_root", ""))).resolve(strict=False)
        state = Path(str(item.get("state_root", ""))).resolve(strict=False)
        for path in [cwd, *candidates]:
            try:
                path.resolve(strict=False).relative_to(source)
            except ValueError:
                continue
            match = (len(source.parts), source, state)
            if best is None or match[0] > best[0]:
                best = match
    if best:
        return best[1], best[2]
    return None


def relative_paths(value: dict[str, Any], source: Path, cwd: Path) -> list[str]:
    tool_input = (
        value.get("tool_input") if isinstance(value.get("tool_input"), dict) else {}
    )
    out = []
    seen = set()
    for raw in values(tool_input):
        try:
            candidate = Path(raw).expanduser()
            candidate = (
                candidate.resolve(strict=False)
                if candidate.is_absolute()
                else (cwd / candidate).resolve(strict=False)
            )
            relative = candidate.relative_to(source).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        if (
            relative in ("", ".")
            or relative.startswith(".llmwiki/")
            or relative in seen
        ):
            continue
        seen.add(relative)
        out.append(relative)
    return out


def append(state: Path, value: dict[str, Any], paths: list[str]) -> None:
    if not (state / "config.json").is_file():
        return
    state.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": now(),
        "kind": "file-change-hint",
        "tool": value.get("tool_name"),
        "paths": paths,
    }
    with (state / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        )


def main() -> int:
    try:
        value = payload()
        if value is None or not successful(value):
            return 0
        cwd = cwd_of(value)
        tool_input = (
            value.get("tool_input") if isinstance(value.get("tool_input"), dict) else {}
        )
        candidates = []
        for raw in values(tool_input):
            try:
                path = Path(raw).expanduser()
                candidates.append(
                    path.resolve(strict=False)
                    if path.is_absolute()
                    else (cwd / path).resolve(strict=False)
                )
            except (OSError, RuntimeError):
                pass
        selected = local_project(cwd)
        if selected is None:
            for path in candidates:
                selected = local_project(path)
                if selected:
                    break
        if selected is None:
            selected = registered_project(cwd, candidates)
        if selected is None:
            return 0
        source, state = selected
        paths = relative_paths(value, source, cwd)
        if paths:
            append(state, value, paths)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
