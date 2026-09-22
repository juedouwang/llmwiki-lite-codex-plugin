"""Loopback-only website for registered LLM Wiki Lite projects."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import secrets
import sys
import time
import webbrowser
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_notebook as notebook  # noqa: E402
import research_progress as progress  # noqa: E402
import daily_tasks  # noqa: E402
import research_reports as reports  # noqa: E402
import knowledge_maintenance as knowledge  # noqa: E402
from llmwiki_core import LLMWikiError, plugin_version  # noqa: E402
from literature_web import (  # noqa: E402
    literature_compare_page,
    literature_library_page,
    literature_read_page,
    source_document_path,
)
from literature_catalog_web import dispatch_literature_http  # noqa: E402
from llmwiki_registry import (  # noqa: E402
    get_project,
    llmwiki_home,
    load_settings,
    list_projects,
    update_project_preferences,
    register_project,
    rename_project,
    unregister_project,
    update_project_storage,
    update_settings,
)
from research_web_ui import (  # noqa: E402
    IMAGE_MIME_TYPES,
    STYLE,
    esc,
    home_page,
    layout,
    page_view,
    project_page,
    purl,
    record_view,
    records_page,
    safe_path,
    search_page,
    settings_page,
    todos_page,
)
import git_web  # noqa: E402
import web_session  # noqa: E402

MAX_FORM_BYTES = 65_536


def code_graph_page(home: str, project_id: str, params: dict) -> str:
    from git_web_page import page
    return page(home, project_id, params)


def redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(HTTPStatus.SEE_OTHER)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def msgurl(path: str, message: str | None = None, error: str | None = None) -> str:
    params: dict[str, str] = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    return path + (("?" + urlencode(params)) if params else "")


def create_handler(home: str) -> type[BaseHTTPRequestHandler]:
    epoch = secrets.token_hex(12)
    class Handler(BaseHTTPRequestHandler):
        server_version = "LLMWikiWeb/0.3"

        def headers_out(self, code: int, content_type: str, length: int) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            if content_type.startswith("text/html") and code < 400 and not self.headers.get("X-Workbench-Navigation") and web_session.session_id():
                selected = quote(web_session.current_project() or "", safe="")
                self.send_header("Set-Cookie", f"{web_session.COOKIE}={epoch}.{selected}; Path=/; SameSite=Strict")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self';img-src 'self' data:;style-src 'self';script-src 'self' 'unsafe-inline';object-src 'none';base-uri 'none'",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def html(self, content: str, code: int = 200) -> None:
            raw = content.encode("utf-8")
            self.headers_out(code, "text/html; charset=utf-8", len(raw))
            self.wfile.write(raw)

        def json(self, payload: dict[str, Any], code: int = 200) -> None:
            raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            try:
                self.headers_out(code, "application/json; charset=utf-8", len(raw))
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Client navigated away; completed disk writes remain valid.

        def form(self) -> dict[str, str]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise LLMWikiError("Content-Length 无效。") from exc
            if length < 0 or length > MAX_FORM_BYTES:
                raise LLMWikiError("表单内容过大。")
            try:
                body = self.rfile.read(length).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LLMWikiError("表单必须使用 UTF-8 编码。") from exc
            parsed = parse_qs(body, keep_blank_values=True)
            return {key: values[-1] for key, values in parsed.items()}

        def stream_source_image(self, project_id: str, relative: str) -> None:
            project = get_project(project_id, home=home)["project"]
            normalized = relative.replace("\\", "/")
            target = safe_path(
                Path(str(project["source_root"])).resolve(strict=False),
                normalized,
            )
            content_type = IMAGE_MIME_TYPES.get(target.suffix.lower())
            if content_type is None or not target.is_file():
                raise FileNotFoundError(relative)
            size = target.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{quote(target.name, safe='')}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            with target.open("rb") as stream:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def stream_literature(self, project_id: str, relative: str) -> None:
            project = get_project(project_id, home=home)["project"]
            target = source_document_path(project, relative)
            size = target.stat().st_size
            start = 0
            end = size - 1
            code = HTTPStatus.OK
            requested = self.headers.get("Range", "").strip()
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not match or not any(match.groups()) or size <= 0:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    suffix = int(last)
                    if suffix <= 0:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    start = max(0, size - suffix)
                    end = size - 1
                if start >= size or end < start:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = HTTPStatus.PARTIAL_CONTENT
            length = max(0, end - start + 1)
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            disposition = "inline" if target.suffix.lower() == ".pdf" else "attachment"
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if code == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename*=UTF-8''{quote(target.name, safe='')}",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            with target.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            # API/prefetch reads must never change the selected browser project.
            if parsed.path.startswith(("/static/", "/api/")) or parsed.path == "/health":
                self.dispatch_get()
                return
            listed = list_projects(home)
            ids = {item["id"] for item in listed["projects"]}
            cookie = web_session.cookie_project(self.headers.get("Cookie"), epoch)
            selected = cookie if cookie in ids else listed["landing_project_id"]
            explicit = re.match(r"/project/([^/]+)(?:/|$)", parsed.path)
            context = parse_qs(parsed.query, keep_blank_values=True).get("context")
            if explicit and unquote(explicit[1]) in ids:
                selected = unquote(explicit[1])
            elif context is not None:
                selected = context[0] if context[0] in ids else ""
            token = web_session.enter(selected, epoch)
            try:
                self.dispatch_get()
            finally:
                web_session.leave(token)

        def dispatch_get(self) -> None:
            if self.headers.get("Host", "") not in {
                f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"
            }:
                self.json({"ok": False, "error": "不允许的 Host。"}, 403)
                return
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            # Empty context means no selected project, not the registry default.
            context = parse_qs(parsed.query, keep_blank_values=True).get("context")
            if context is not None:
                params["context"] = context
            if dispatch_literature_http(self, home):
                return
            try:
                if parsed.path == "/health":
                    self.json({"ok": True, "service": "llmwiki-web", "version": plugin_version()})
                    return
                if parsed.path in {"/favicon.ico", "/static/workbench.ico"}:
                    raw = (SCRIPT_DIR / "static" / "workbench.ico").read_bytes()
                    self.headers_out(200, "image/x-icon", len(raw))
                    self.wfile.write(raw)
                    return
                if parsed.path in {"/static/daily-tasks.js", "/static/daily-tasks.css", "/static/theme.js", "/static/records.js", "/static/code.js", "/static/code.css", "/static/document-editor.css", "/static/document-editor.js", "/static/notebook.css", "/static/notebook.js", "/static/app.js", "/static/workbench-navigation.js", "/static/projects.js", "/static/progress.js", "/static/progress.css", "/static/reports.js", "/static/reports.css", "/static/knowledge-maintenance.js", "/static/knowledge-maintenance.css"}:
                    target = SCRIPT_DIR / "static" / parsed.path.rsplit("/", 1)[-1]
                    raw = target.read_bytes()
                    content_type = "text/css" if target.suffix == ".css" else "text/javascript"
                    self.headers_out(200, content_type + "; charset=utf-8", len(raw))
                    self.wfile.write(raw)
                    return
                knowledge_match = re.fullmatch(r"/api/project/([^/]+)/knowledge-maintenance(?:/(proposals)(?:/([a-f0-9]{32}))?)?", parsed.path)
                if knowledge_match:
                    try:
                        project = reports.project_for(unquote(knowledge_match[1]), home)
                        if knowledge_match[3]:
                            result = knowledge.proposal_detail(project, knowledge_match[3])
                        elif knowledge_match[2]:
                            result = knowledge.proposal_list(project, scope=(params.get("scope") or ["pending"])[0], offset=int((params.get("offset") or [0])[0]), limit=int((params.get("limit") or [20])[0]), page_path=(params.get("page_path") or [None])[0])
                        else:
                            result = knowledge.maintenance_status(project, home)
                        self.json(result)
                    except (knowledge.KnowledgeError, reports.ReportError) as exc:
                        self.json({"ok":False,"error":{"code":exc.code,"message":str(exc)}}, exc.status)
                    except (LLMWikiError, OSError, ValueError, TypeError):
                        self.json({"ok":False,"error":{"code":"INVALID_INPUT","message":"知识更新不可用或请求无效。"}}, 400)
                    return
                if parsed.path == "/api/daily-tasks":
                    try:
                        self.json(daily_tasks.list_day(home, (params.get("date") or [None])[0]))
                    except (LLMWikiError, ValueError, TypeError) as exc:
                        self.json({"ok": False, "error": str(exc)}, 400)
                    return
                if parsed.path == "/daily":
                    from daily_page import page as daily_page
                    self.html(daily_page(home, context=(params.get("context") or [None])[0], day=(params.get("date") or [None])[0]))
                    return
                if parsed.path == "/api/reports/settings":
                    self.json(reports.report_settings(home))
                    return
                workspace_match = re.fullmatch(r"/(api/)?reports(?:/(daily|weekly)/(\d{4}-\d{2}-\d{2}))?", parsed.path)
                if workspace_match:
                    try:
                        owner = reports.workspace(home)
                        kind = workspace_match[2] or (params.get("kind") or params.get("view") or ["daily"])[0]
                        if workspace_match[2]:
                            if workspace_match[1]:
                                version = (params.get("version") or [None])[0]
                                self.json(reports.load(owner, kind, workspace_match[3], view=(params.get("view") or [""])[0], version=int(version) if version is not None else None))
                            else:
                                self.html(reports.editor_page(home, owner["id"], kind, workspace_match[3], params))
                        elif workspace_match[1]:
                            self.json(reports.listing(owner, kind, home=home, offset=int((params.get("offset") or [0])[0]), limit=int((params.get("limit") or [30])[0]), query=(params.get("q") or [""])[0], project_filter=(params.get("project") or [""])[0], status_filter=(params.get("status") or [""])[0]))
                        else:
                            self.html(reports.list_page(home, owner, kind, params))
                    except reports.ReportError as exc:
                        self.json({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, exc.status)
                    except (LLMWikiError, OSError, ValueError, TypeError):
                        self.json({"ok": False, "error": {"code": "INVALID_INPUT", "message": "报告不可用或请求无效。"}}, 400)
                    return
                if parsed.path.startswith("/reports/asset/"):
                    relative = unquote(parsed.path[len("/reports/asset/"):])
                    if not relative.startswith("records/assets/"):
                        raise LLMWikiError("只能访问工作台报告附件。")
                    target = notebook.image_path(reports.workspace(home), relative[len("records/assets/"):])
                    raw = target.read_bytes()
                    self.headers_out(200, mimetypes.guess_type(target.name)[0] or "application/octet-stream", len(raw))
                    self.wfile.write(raw)
                    return
                match = re.fullmatch(r"/(api/)?project/([^/]+)/reports(?:/(daily|weekly)/(\d{4}-\d{2}-\d{2}))?", parsed.path)
                if match:
                    try:
                        project = reports.project_for(unquote(match[2]), home)
                        if not match[1] and match[3]:
                            self.html(reports.editor_page(home, project["id"], match[3], match[4], params))
                        elif match[1] and match[3]:
                            version = (params.get("version") or [None])[0]
                            self.json(reports.load(project, match[3], match[4], view=(params.get("view") or [""])[0], version=int(version) if version is not None else None))
                        elif match[1]:
                            self.json(reports.listing(project, (params.get("kind") or ["daily"])[0], home=home, offset=int((params.get("offset") or [0])[0]), limit=int((params.get("limit") or [30])[0]), query=(params.get("q") or [""])[0]))
                        else:
                            self.json({"ok": False, "error": {"code": "NOT_FOUND", "message": "报告地址不存在。"}}, 404)
                    except reports.ReportError as exc:
                        self.json({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, exc.status)
                    except (LLMWikiError, OSError, ValueError, TypeError):
                        self.json({"ok": False, "error": {"code": "INVALID_INPUT", "message": "报告不可用或请求无效。"}}, 400)
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/progress", parsed.path)
                if match:
                    try:
                        project = get_project(unquote(match[1]), home=home)["project"]
                        view = (params.get("view") or [None])[0]
                        if view not in {None, "summary", "records"}:
                            self.json({"ok": False, "error": "未知的进度视图。"}, 400)
                        else:
                            self.json(progress.load(project, view=view))
                    except (LLMWikiError, ValueError, OSError) as exc:
                        self.json({"ok": False, "error": str(exc)}, 400)
                    return
                match = re.fullmatch(r"/project/([^/]+)/notebook(?:/([a-f0-9]{32}))?", parsed.path)
                if match:
                    self.html(notebook.editor_page(home, unquote(match[1]), match[2]))
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/notebook/([a-f0-9]{32})(/history)?", parsed.path)
                if match:
                    try:
                        project = get_project(unquote(match[1]), home=home)["project"]
                        result = (notebook.history(project, match[2], (params.get("revision") or [None])[0], continuous_view=(params.get("format") or [""])[0] == "markdown")
                                  if match[3] else notebook.load(project, match[2], continuous_view=(params.get("format") or [""])[0] == "markdown"))
                        self.json(result)
                    except notebook.NotebookConflict as exc:
                        self.json({"ok": False, "error": str(exc)}, 409)
                    except (LLMWikiError, ValueError, OSError) as exc:
                        self.json({"ok": False, "error": str(exc)}, 400)
                    return
                if parsed.path == "/static/style.css":
                    raw = STYLE.encode("utf-8")
                    self.headers_out(200, "text/css; charset=utf-8", len(raw))
                    self.wfile.write(raw)
                    return
                if parsed.path == "/":
                    pid = web_session.current_project() or list_projects(home)["landing_project_id"]
                    redirect(self, purl(pid) + "/todos" if pid else "/projects")
                    return
                if parsed.path == "/projects":
                    self.html(home_page(home, params))
                    return
                if parsed.path == "/api/projects/preferences":
                    self.json(list_projects(home))
                    return
                if parsed.path == "/settings":
                    self.html(settings_page(home, params))
                    return
                if parsed.path == "/search":
                    self.html(search_page(home, params))
                    return
                match = re.fullmatch(r"/project/([^/]+)/literature/old", parsed.path)
                if match:
                    self.html(literature_library_page(home, unquote(match.group(1))))
                    return
                legacy_list = re.fullmatch(r"/project/([^/]+)/records", parsed.path)
                if legacy_list and (params.get("view") or [""])[0] in {"daily", "weekly"}:
                    get_project(unquote(legacy_list[1]), home=home)
                    self.send_response(HTTPStatus.FOUND)
                    navigation = {key: values[0] for key, values in params.items()}
                    navigation.setdefault('context', unquote(legacy_list[1]))
                    self.send_header('Location', '/reports?' + urlencode(navigation))
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                match = re.fullmatch(r"/project/([^/]+)/records", parsed.path)
                if match:
                    self.html(records_page(home, unquote(match.group(1)), params))
                    return
                match = re.fullmatch(r"/project/([^/]+)/todos", parsed.path)
                if match:
                    self.html(todos_page(home, unquote(match.group(1)), params))
                    return
                match = re.fullmatch(r"/project/([^/]+)/code", parsed.path)
                if match:
                    self.html(code_graph_page(home, unquote(match.group(1)), params))
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/code/(.+)", parsed.path)
                if match:
                    result, code = git_web.dispatch(home, unquote(match[1]), "GET", match[2],
                                                    {k: v[-1] for k, v in params.items()})
                    self.json(result, code)
                    return
                match = re.fullmatch(r"/project/([^/]+)/records/(.+)", parsed.path)
                if match:
                    self.html(
                        record_view(
                            home, unquote(match.group(1)), unquote(match.group(2))
                        )
                    )
                    return
                match = re.fullmatch(
                    r"/project/([^/]+)/literature/read/(.+)", parsed.path
                )
                if match:
                    self.html(
                        literature_read_page(
                            home, unquote(match.group(1)), unquote(match.group(2))
                        )
                    )
                    return
                match = re.fullmatch(
                    r"/project/([^/]+)/literature/compare/(.+)", parsed.path
                )
                if match:
                    selected_note = (params.get("note") or [None])[-1]
                    self.html(
                        literature_compare_page(
                            home,
                            unquote(match.group(1)),
                            unquote(match.group(2)),
                            selected_note,
                        )
                    )
                    return
                match = re.fullmatch(
                    r"/project/([^/]+)/literature/source/(.+)", parsed.path
                )
                if match:
                    self.stream_literature(
                        unquote(match.group(1)), unquote(match.group(2))
                    )
                    return
                match = re.fullmatch(r"/project/([^/]+)", parsed.path)
                if match:
                    self.html(project_page(home, unquote(match.group(1)), params))
                    return
                match = re.fullmatch(r"/project/([^/]+)/page/(.+)", parsed.path)
                if match:
                    self.html(
                        page_view(home, unquote(match.group(1)), unquote(match.group(2)))
                    )
                    return
                match = re.fullmatch(r"/project/([^/]+)/source-asset/(.+)", parsed.path)
                if match:
                    self.stream_source_image(
                        unquote(match.group(1)), unquote(match.group(2))
                    )
                    return
                match = re.fullmatch(r"/project/([^/]+)/asset/(.+)", parsed.path)
                if match:
                    project = get_project(unquote(match.group(1)), home=home)["project"]
                    target = safe_path(
                        Path(str(project["wiki_root"])).resolve(strict=False),
                        unquote(match.group(2)),
                    )
                    if not target.is_file():
                        raise FileNotFoundError(str(target))
                    raw = target.read_bytes()
                    self.headers_out(
                        200,
                        mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                        len(raw),
                    )
                    self.wfile.write(raw)
                    return
                self.html(
                    layout("页面不存在", '<div class="panel"><h1>404</h1><p>没有找到这个页面。</p><a class="button" href="/">返回研究项目</a></div>'),
                    404,
                )
            except FileNotFoundError:
                self.html(
                    layout("文件不存在", '<div class="panel"><h1>404</h1><p>文件不存在或已经移动。</p></div>'),
                    404,
                )
            except (LLMWikiError, OSError, ValueError) as exc:
                self.html(
                    layout("操作失败", f'<div class="notice error">{esc(exc)}</div>'),
                    400,
                )

        def notebook_post(self, path: str) -> None:
            # Browsers cannot forge this JSON/header combination cross-origin.
            # Reject rebinding Host values as well; never enable CORS for local files.
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if (host not in allowed or self.headers.get("Origin") != "http://" + host
                    or self.headers.get("X-Notebook-Request") != "1"
                    or self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
                self.json({"ok": False, "error": "已阻止非同源写入。"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 15 * 1024 * 1024:
                    self.json({"ok": False, "error": "请求过大或为空。"}, 413)
                    return
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise LLMWikiError("请求必须是 JSON 对象。")
                project_action = re.fullmatch(r"/api/projects/([^/]+)/(rename|unregister)", path)
                if project_action:
                    pid = unquote(project_action[1])
                    if project_action[2] == "rename":
                        if set(payload) != {"name"}:
                            raise LLMWikiError("重命名只接受项目名称。")
                        result = rename_project(pid, payload["name"], home=home)
                    else:
                        if payload != {"confirm": True}:
                            raise LLMWikiError("请确认从列表移除项目；不会删除文件。")
                        result = unregister_project(pid, home=home)
                    self.json({**result, **list_projects(home)})
                    return
                if path == "/api/projects/preferences":
                    if not payload or set(payload) - {"default_project_id", "project_order"}:
                        raise LLMWikiError("仅支持默认项目和项目排序。")
                    self.json(update_project_preferences(home=home, **payload))
                    return
                if path == "/api/daily-tasks":
                    self.json(daily_tasks.mutate(home, payload, actor="user"))
                    return
                if path == "/api/reports/settings":
                    try:
                        self.json(reports.save_settings(payload, home))
                    except reports.ReportError as exc:
                        self.json({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, exc.status)
                    return
                knowledge_match = re.fullmatch(r"/api/project/([^/]+)/knowledge-maintenance/proposals/([a-f0-9]{32})", path)
                if knowledge_match:
                    try:
                        project = reports.project_for(unquote(knowledge_match[1]), home)
                        self.json(knowledge.decide(project, knowledge_match[2], payload))
                    except (knowledge.KnowledgeError, reports.ReportError) as exc:
                        self.json({"ok":False,"error":{"code":exc.code,"message":str(exc)}}, exc.status)
                    except (LLMWikiError, OSError, ValueError, TypeError):
                        self.json({"ok":False,"error":{"code":"INVALID_INPUT","message":"建议不可用或请求无效。"}}, 400)
                    return
                workspace_match = re.fullmatch(r"/api/reports(?:/(preview|upload)|/(daily|weekly)/(\d{4}-\d{2}-\d{2}))?", path)
                if workspace_match:
                    try:
                        owner = reports.workspace(home)
                        if workspace_match[1] == "upload":
                            result = notebook.upload(owner, payload)
                        elif workspace_match[1] == "preview":
                            from markdown_renderer import render_markdown
                            key, _ = reports.identity(payload.get("kind"), payload.get("period_start"))
                            result = {"ok": True, "html": render_markdown(reports.body_text(payload.get("body")), owner["id"], f"records/reports/{key}/draft.md")}
                        elif workspace_match[2]:
                            result = reports.update(owner, workspace_match[2], workspace_match[3], payload, home=home)
                        elif payload.get("action") == "create":
                            result = reports.create(owner, payload.get("kind"), payload.get("period_start"), payload.get("project_ids"), home)
                        else:
                            raise reports.ReportError("报告操作无效。")
                        self.json(result)
                    except reports.ReportError as exc:
                        self.json({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, exc.status)
                    return
                report_match = re.fullmatch(r"/api/project/([^/]+)/reports(?:/(preview)|/(daily|weekly)/(\d{4}-\d{2}-\d{2}))?", path)
                if report_match:
                    try:
                        project = reports.project_for(unquote(report_match[1]), home)
                        if report_match[2]:
                            from markdown_renderer import render_markdown
                            key, _ = reports.identity(payload.get("kind"), payload.get("period_start"))
                            result = {"ok": True, "html": render_markdown(reports.body_text(payload.get("body")), project["id"], f"records/reports/{key}/draft.md")}
                        elif report_match[3]:
                            result = reports.update(project, report_match[3], report_match[4], payload, home=home)
                        elif payload.get("action") == "create":
                            result = reports.create(project, payload.get("kind"), payload.get("period_start"), payload.get("project_ids"), home)
                        else:
                            raise reports.ReportError("报告操作无效。")
                        self.json(result, 202 if payload.get("action") == "regenerate" else 200)
                    except reports.ReportError as exc:
                        self.json({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, exc.status)
                    except (LLMWikiError, OSError, ValueError, TypeError):
                        self.json({"ok": False, "error": {"code": "INVALID_INPUT", "message": "报告不可用或请求无效。"}}, 400)
                    return
                progress_match = re.fullmatch(r"/api/project/([^/]+)/progress", path)
                if progress_match:
                    project = get_project(unquote(progress_match[1]), home=home)["project"]
                    self.json(progress.update(project, payload))
                    return
                deletion = re.fullmatch(r"/api/project/([^/]+)/notebook/([a-f0-9]{32})/(delete|undo-delete)", path)
                if deletion:
                    project = get_project(unquote(deletion[1]), home=home)["project"]
                    action = notebook.remove if deletion[3] == "delete" else notebook.undo_remove
                    self.json(action(project, deletion[2], payload))
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/notebook/(upload|preview|[a-f0-9]{32})", path)
                if not match:
                    self.json({"ok": False, "error": "笔记接口不存在。"}, 404)
                    return
                project = get_project(unquote(match[1]), home=home)["project"]
                action = match[2]
                if action == "upload":
                    result = notebook.upload(project, payload)
                elif action == "preview":
                    from markdown_renderer import render_markdown
                    text = payload.get("text", "")
                    if not isinstance(text, str) or len(text.encode("utf-8")) > notebook.MAX_DOCUMENT:
                        raise LLMWikiError("预览文字过长。")
                    result = {"ok": True, "html": render_markdown(text, project["id"], "records/manual/preview.md")}
                else:
                    result = notebook.save(project, action, payload)
                self.json(result)
            except notebook.NotebookConflict as exc:
                self.json({"ok": False, "error": str(exc)}, 409)
            except (LLMWikiError, OSError, ValueError, TypeError) as exc:
                self.json({"ok": False, "error": str(exc)}, 400)

        def code_post(self, project_id: str, endpoint: str) -> None:
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if (host not in allowed or self.headers.get("Origin") != "http://" + host
                    or self.headers.get("X-Notebook-Request") != "1"
                    or self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
                self.json({"ok": False, "code": "forbidden_origin", "message": "已阻止非同源写入。"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError("size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("object")
            except (ValueError, UnicodeDecodeError):
                self.json({"ok": False, "code": "invalid_request", "message": "请求必须是至多 2MiB 的 JSON 对象。"}, 400)
                return
            result, code = git_web.dispatch(home, project_id, "POST", endpoint, payload,
                                            worktree_id=parse_qs(urlparse(self.path).query).get("worktree", [""])[-1])
            self.json(result, code)

        def do_POST(self) -> None:  # noqa: N802
            if dispatch_literature_http(self, home):
                return
            parsed = urlparse(self.path)
            code_match = re.fullmatch(r"/api/project/([^/]+)/code/(.+)", parsed.path)
            if code_match:
                self.code_post(unquote(code_match[1]), code_match[2])
                return
            if parsed.path == "/api/daily-tasks" or parsed.path.startswith("/api/projects/") or parsed.path == "/api/reports" or parsed.path.startswith("/api/reports/"):
                self.notebook_post(parsed.path)
                return
            if parsed.path.startswith("/api/project/"):
                self.notebook_post(parsed.path)
                return
            try:
                form = self.form()
                if parsed.path == "/settings/default-wiki-root":
                    update_settings(
                        home=home,
                        default_wiki_root=form.get("default_wiki_root") or None,
                        web_port=int(form.get("web_port") or 8765),
                    )
                    redirect(self, msgurl("/settings", message="默认设置已保存。"))
                    return
                if parsed.path == "/project/register":
                    result = register_project(
                        form.get("source_root", ""),
                        name=form.get("name") or None,
                        wiki_root=form.get("wiki_root") or None,
                        state_root=form.get("state_root") or None,
                        home=home,
                    )
                    redirect(
                        self,
                        msgurl(purl(result["project"]["id"]), message="研究项目已注册。"),
                    )
                    return
                match = re.fullmatch(r"/project/([^/]+)/storage", parsed.path)
                if match:
                    project_id = unquote(match.group(1))
                    update_project_storage(
                        project_id,
                        wiki_root=form.get("wiki_root") or None,
                        state_root=form.get("state_root") or None,
                        home=home,
                        copy_existing=form.get("copy_existing") == "1",
                    )
                    redirect(
                        self,
                        msgurl("/settings", message="项目存储位置已更新，旧目录未删除。")
                        + f"#project-{quote(project_id, safe='')}",
                    )
                    return
                match = re.fullmatch(r"/project/([^/]+)/unregister", parsed.path)
                if match:
                    unregister_project(unquote(match.group(1)), home=home)
                    redirect(
                        self,
                        msgurl("/settings", message="项目已取消注册，任何文件都未删除。"),
                    )
                    return
                self.html(layout("页面不存在", '<div class="panel"><h1>404</h1></div>'), 404)
            except (LLMWikiError, OSError, ValueError) as exc:
                redirect(self, msgurl("/settings", error=str(exc)))

        def log_message(self, fmt: str, *args: Any) -> None:
            try:
                if sys.stderr is not None:
                    sys.stderr.write(
                        f"[llmwiki-web] {self.address_string()} {fmt % args}\n"
                    )
                    sys.stderr.flush()
            except (AttributeError, OSError, ValueError):
                pass

    return Handler


def create_server(
    home: str | None = None, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise LLMWikiError("LLM Wiki 网站只能监听本机回环地址。")
    return ThreadingHTTPServer((host, int(port)), create_handler(str(llmwiki_home(home))))


def is_server(host: str, port: int) -> bool:
    try:
        connection = HTTPConnection(host, port, timeout=0.5)
        connection.request("GET", "/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status == 200 and payload.get("service") == "llmwiki-web"
    except (OSError, ValueError, json.JSONDecodeError):
        return False

def start_background(
    home: str | None = None, port: int | None = None, open_browser: bool = False
) -> dict[str, Any]:
    root = llmwiki_home(home)
    settings = load_settings(str(root))
    selected = int(port or settings["web_port"])
    host = "127.0.0.1"
    url = f"http://{host}:{selected}/"
    if is_server(host, selected):
        if open_browser:
            webbrowser.open(url)
        return {
            "ok": True,
            "running": True,
            "started": False,
            "url": url,
            "home": str(root),
        }
    executable = sys.executable
    if os.name == "nt":
        pythonw = Path(executable).with_name("pythonw.exe")
        if pythonw.is_file():
            executable = str(pythonw)
    command = [
        executable,
        "-I",
        "-B",
        str(Path(__file__).resolve()),
        "--home",
        str(root),
        "--host",
        host,
        "--port",
        str(selected),
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        base_flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        kwargs["creationflags"] = base_flags | breakaway
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError:
            kwargs["creationflags"] = base_flags
            process = subprocess.Popen(command, **kwargs)
    else:
        kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if is_server(host, selected):
            if open_browser:
                webbrowser.open(url)
            return {
                "ok": True,
                "running": True,
                "started": True,
                "pid": process.pid,
                "url": url,
                "home": str(root),
            }
        if process.poll() is not None:
            break
        time.sleep(0.1)
    raise LLMWikiError(f"网站未能在 {url} 启动。")


def serve(home: str | None, host: str, port: int, open_browser: bool = False) -> int:
    server = create_server(home, host, port)
    actual = int(server.server_address[1])
    url = f"http://{host}:{actual}/"
    root = llmwiki_home(home)
    root.mkdir(parents=True, exist_ok=True)
    state = root / "web-server.json"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "pid": os.getpid(),
                "host": host,
                "port": actual,
                "url": url,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        if sys.stdout is not None:
            print(f"LLM Wiki Lite 中文科研工作台：{url}", flush=True)
    except (AttributeError, OSError, ValueError):
        pass
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if json.loads(state.read_text(encoding="utf-8")).get("pid") == os.getpid():
                state.unlink()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="启动本机 LLM Wiki Lite 中文科研工作台。")
    parser.add_argument("--home")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()
    settings = load_settings(args.home)
    return serve(
        args.home,
        args.host,
        args.port or int(settings["web_port"]),
        args.open_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())