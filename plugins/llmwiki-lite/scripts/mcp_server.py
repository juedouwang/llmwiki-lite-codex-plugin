"""Dependency-free MCP stdio server for the lightweight LLM Wiki plugin."""

from __future__ import annotations
import json
import sys
import traceback
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from llmwiki_core import (  # noqa: E402
    LLMWikiError,
    init_project,
    list_files,
    read_file,
    search,
    snapshot,
    status,
    wiki_check,
    wiki_list,
    wiki_write,
)
from llmwiki_registry import (  # noqa: E402
    get_project,
    list_projects,
    load_settings,
    register_project,
    select_project,
    unregister_project,
    update_project_storage,
    update_settings,
)
from web_server import start_background  # noqa: E402

SERVER_NAME = "llmwiki"
SERVER_VERSION = "0.2.0"
SUPPORTED_PROTOCOL = "2025-03-26"


def schema(properties: dict[str, Any], required: list[str] = []) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


ROOT = {"type": "string", "description": "Source project root directory."}
STATE = {"type": "string", "description": "Optional custom machine-state directory."}
HOME = {"type": "string", "description": "Optional LLMWIKI_HOME override."}
IDENT = {
    "type": "string",
    "description": "Registered project ID, unique name, or source path.",
}
TOOLS = [
    {
        "name": "llmwiki_project_register",
        "description": "Register a source project and configure separate state and human-readable Wiki roots. Registration does not scan or summarize the project.",
        "inputSchema": schema(
            {
                "source_root": ROOT,
                "name": {"type": "string"},
                "wiki_root": {"type": "string"},
                "state_root": STATE,
                "home": HOME,
                "select": {"type": "boolean", "default": True},
            },
            ["source_root"],
        ),
    },
    {
        "name": "llmwiki_project_list",
        "description": "List registered projects and current/default storage settings.",
        "inputSchema": schema({"home": HOME}),
    },
    {
        "name": "llmwiki_project_get",
        "description": "Resolve one registered project by ID, name, source path, current path, or current selection.",
        "inputSchema": schema(
            {"identifier": IDENT, "home": HOME, "current_path": {"type": "string"}}
        ),
    },
    {
        "name": "llmwiki_project_select",
        "description": "Set the current registered project without scanning it.",
        "inputSchema": schema({"identifier": IDENT, "home": HOME}, ["identifier"]),
    },
    {
        "name": "llmwiki_project_storage_update",
        "description": "Change a project's Wiki and/or state directory. By default copy existing content and preserve old directories.",
        "inputSchema": schema(
            {
                "identifier": IDENT,
                "wiki_root": {"type": "string"},
                "state_root": STATE,
                "home": HOME,
                "copy_existing": {"type": "boolean", "default": True},
            },
            ["identifier"],
        ),
    },
    {
        "name": "llmwiki_project_unregister",
        "description": "Remove a project from the registry without deleting source, state, or Wiki files.",
        "inputSchema": schema({"identifier": IDENT, "home": HOME}, ["identifier"]),
    },
    {
        "name": "llmwiki_settings_get",
        "description": "Return global lightweight settings, including the default Wiki root and website port.",
        "inputSchema": schema({"home": HOME}),
    },
    {
        "name": "llmwiki_settings_update",
        "description": "Update global default Wiki root or website port. An empty/null Wiki root restores <project>/wiki for new users.",
        "inputSchema": schema(
            {
                "home": HOME,
                "default_wiki_root": {"type": ["string", "null"]},
                "web_port": {"type": "integer", "minimum": 1024, "maximum": 65535},
            }
        ),
    },
    {
        "name": "llmwiki_web_start",
        "description": "Start or reuse the loopback-only local Wiki website and return its URL.",
        "inputSchema": schema(
            {
                "home": HOME,
                "port": {"type": "integer", "minimum": 1024, "maximum": 65535},
                "open_browser": {"type": "boolean", "default": False},
            }
        ),
    },
    {
        "name": "llmwiki_init",
        "description": "Initialize lightweight state and Wiki directories without creating semantic knowledge pages.",
        "inputSchema": schema(
            {
                "project_root": ROOT,
                "state_root": STATE,
                "wiki_root": {"type": "string"},
            },
            ["project_root"],
        ),
    },
    {
        "name": "llmwiki_status",
        "description": "Return project configuration, last snapshot, Wiki page count, and dirty-path hints.",
        "inputSchema": schema(
            {"project_root": ROOT, "state_root": STATE}, ["project_root"]
        ),
    },
    {
        "name": "llmwiki_snapshot",
        "description": "Scan files, compute bounded hashes, report changes, and optionally update the baseline.",
        "inputSchema": schema(
            {
                "project_root": ROOT,
                "state_root": STATE,
                "hash_limit_bytes": {"type": "integer", "minimum": 0},
                "save": {"type": "boolean", "default": True},
                "max_changes": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            ["project_root"],
        ),
    },
    {
        "name": "llmwiki_files",
        "description": "List project files after default and .llmwikiignore exclusions.",
        "inputSchema": schema(
            {
                "project_root": ROOT,
                "pattern": {"type": "string", "default": "*"},
                "max_files": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
            ["project_root"],
        ),
    },
    {
        "name": "llmwiki_search",
        "description": "Search UTF-8 project text and return bounded literal or regex matches without semantic judgment.",
        "inputSchema": schema(
            {
                "project_root": ROOT,
                "query": {"type": "string", "minLength": 1},
                "path_pattern": {"type": "string", "default": "*"},
                "regex": {"type": "boolean", "default": False},
                "case_sensitive": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
                "max_file_bytes": {"type": "integer", "minimum": 1},
            },
            ["project_root", "query"],
        ),
    },
    {
        "name": "llmwiki_read",
        "description": "Read a bounded line range from one UTF-8 source-project file.",
        "inputSchema": schema(
            {
                "project_root": ROOT,
                "path": {"type": "string", "minLength": 1},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "max_chars": {"type": "integer", "minimum": 1},
            },
            ["project_root", "path"],
        ),
    },
    {
        "name": "llmwiki_wiki_write",
        "description": "Write Codex-authored Markdown beneath the configured Wiki root and optionally refresh the generated index region.",
        "inputSchema": schema(
            {
                "project_root": ROOT,
                "state_root": STATE,
                "page_path": {"type": "string", "minLength": 1},
                "content": {"type": "string", "minLength": 1},
                "overwrite": {"type": "boolean", "default": True},
                "update_index": {"type": "boolean", "default": True},
            },
            ["project_root", "page_path", "content"],
        ),
    },
    {
        "name": "llmwiki_wiki_list",
        "description": "List Markdown pages beneath the configured Wiki root.",
        "inputSchema": schema(
            {"project_root": ROOT, "state_root": STATE}, ["project_root"]
        ),
    },
    {
        "name": "llmwiki_wiki_check",
        "description": "Check Wiki wikilinks and frontmatter source paths structurally.",
        "inputSchema": schema(
            {"project_root": ROOT, "state_root": STATE}, ["project_root"]
        ),
    },
]
TOOL_NAMES = {x["name"] for x in TOOLS}


def only(args: dict[str, Any], allowed: set[str]) -> None:
    if not isinstance(args, dict):
        raise LLMWikiError("Tool arguments must be an object.")
    extra = set(args) - allowed
    if extra:
        raise LLMWikiError(f"Unknown argument(s): {', '.join(sorted(extra))}")


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "llmwiki_project_register":
        only(args, {"source_root", "name", "wiki_root", "state_root", "home", "select"})
        return register_project(**args)
    if name == "llmwiki_project_list":
        only(args, {"home"})
        return list_projects(**args)
    if name == "llmwiki_project_get":
        only(args, {"identifier", "home", "current_path"})
        return get_project(**args)
    if name == "llmwiki_project_select":
        only(args, {"identifier", "home"})
        return select_project(**args)
    if name == "llmwiki_project_storage_update":
        only(args, {"identifier", "wiki_root", "state_root", "home", "copy_existing"})
        return update_project_storage(**args)
    if name == "llmwiki_project_unregister":
        only(args, {"identifier", "home"})
        return unregister_project(**args)
    if name == "llmwiki_settings_get":
        only(args, {"home"})
        return {"ok": True, "settings": load_settings(**args)}
    if name == "llmwiki_settings_update":
        only(args, {"home", "default_wiki_root", "web_port"})
        kwargs = dict(args)
        if "default_wiki_root" not in kwargs:
            kwargs["default_wiki_root"] = ...
        if "web_port" not in kwargs:
            kwargs["web_port"] = ...
        return {"ok": True, "settings": update_settings(**kwargs)}
    if name == "llmwiki_web_start":
        only(args, {"home", "port", "open_browser"})
        return start_background(**args)
    if name == "llmwiki_init":
        only(args, {"project_root", "state_root", "wiki_root"})
        return init_project(**args)
    if name == "llmwiki_status":
        only(args, {"project_root", "state_root"})
        return status(**args)
    if name == "llmwiki_snapshot":
        only(
            args,
            {"project_root", "state_root", "hash_limit_bytes", "save", "max_changes"},
        )
        return snapshot(**args)
    if name == "llmwiki_files":
        only(args, {"project_root", "pattern", "max_files"})
        return list_files(**args)
    if name == "llmwiki_search":
        only(
            args,
            {
                "project_root",
                "query",
                "path_pattern",
                "regex",
                "case_sensitive",
                "max_results",
                "max_file_bytes",
            },
        )
        return search(**args)
    if name == "llmwiki_read":
        only(args, {"project_root", "path", "start_line", "end_line", "max_chars"})
        return read_file(**args)
    if name == "llmwiki_wiki_write":
        only(
            args,
            {
                "project_root",
                "state_root",
                "page_path",
                "content",
                "overwrite",
                "update_index",
            },
        )
        return wiki_write(**args)
    if name == "llmwiki_wiki_list":
        only(args, {"project_root", "state_root"})
        return wiki_list(**args)
    if name == "llmwiki_wiki_check":
        only(args, {"project_root", "state_root"})
        return wiki_check(**args)
    raise LLMWikiError(f"Unknown tool: {name}")


def response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
        ],
        "isError": is_error,
    }


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params")
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return response(
            request_id,
            {
                "protocolVersion": requested
                if isinstance(requested, str)
                else SUPPORTED_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": "Use registry and filesystem tools for mechanics. Codex remains responsible for reading, reasoning, and Wiki content.",
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return response(request_id, {})
    if method == "tools/list":
        return response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        if not isinstance(params, dict):
            return response(
                request_id,
                tool_result({"ok": False, "error": "params must be an object"}, True),
            )
        name = params.get("name")
        args = params.get("arguments", {})
        if not isinstance(name, str) or name not in TOOL_NAMES:
            return response(
                request_id, tool_result({"ok": False, "error": "unknown tool"}, True)
            )
        try:
            return response(request_id, tool_result(dispatch(name, args)))
        except (LLMWikiError, OSError, ValueError) as exc:
            return response(
                request_id, tool_result({"ok": False, "error": str(exc)}, True)
            )
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return response(
                request_id,
                tool_result(
                    {"ok": False, "error": f"internal error: {type(exc).__name__}"},
                    True,
                ),
            )
    if request_id is None:
        return None
    return error_response(request_id, -32601, f"Method not found: {method}")


def write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def main() -> int:
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            write_message(error_response(None, -32700, "Parse error"))
            continue
        if not isinstance(message, dict):
            write_message(error_response(None, -32600, "Invalid Request"))
            continue
        outgoing = handle(message)
        if outgoing is not None:
            write_message(outgoing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
