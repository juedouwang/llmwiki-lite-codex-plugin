"""Loopback-only website for registered LLM Wiki Lite projects."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
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
from llmwiki_core import LLMWikiError, plugin_version  # noqa: E402
from literature_web import (  # noqa: E402
    literature_compare_page,
    literature_library_page,
    literature_read_page,
    source_document_path,
)
from literature_catalog_web import (  # noqa: E402
    literature_catalog_list_page,
    literature_detail_page,
    literature_migration_preview_page,
    _scan_migration_candidates,
    apply_migration,
    rollback_migration,
)
from literature_catalog import LiteratureCatalog  # noqa: E402
from llmwiki_registry import (  # noqa: E402
    get_project,
    llmwiki_home,
    load_settings,
    register_project,
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
from git_service import (  # noqa: E402
    detect_git_executable,
    is_git_repository,
)
from git_graph import build_graph  # noqa: E402

MAX_FORM_BYTES = 65_536


def code_graph_page(home: str, project_id: str, params: dict) -> str:
    """Render git commit graph page.

    Args:
        home: LLM Wiki home directory
        project_id: Project ID
        params: Query parameters

    Returns:
        HTML page
    """
    from research_web_ui import layout, esc, purl, ui_icon

    project = get_project(project_id, home=home)["project"]
    source_root = Path(str(project["source_root"]))

    # Check if git repository
    try:
        git_exe = detect_git_executable()
        is_git_repo = is_git_repository(source_root, git_exe)
    except Exception:
        is_git_repo = False

    if not is_git_repo:
        content = f'''
        <div class="panel">
            <h1>代码提交图谱</h1>
            <div class="notice error">
                <p>源项目不是 Git 仓库，无法显示提交图谱。</p>
                <p>路径：{esc(source_root)}</p>
            </div>
            <a class="button" href="{purl(project_id)}">返回项目</a>
        </div>
        '''
        return layout(f"{project['name']} - 代码图谱", content)

    # Render page
    content = f'''
    <div class="page-header">
        <div class="breadcrumb">
            <a href="/">研究项目</a>
            <span>/</span>
            <a href="{purl(project_id)}">{esc(project['name'])}</a>
            <span>/</span>
            <span>代码提交图谱</span>
        </div>
    </div>

    <div class="panel">
        <div class="panel-header">
            <h1>代码提交图谱</h1>
        </div>

        <div id="graph-controls" class="graph-controls">
            <label>
                分支：
                <select id="branch-select">
                    <option value="HEAD">HEAD</option>
                </select>
            </label>
            <button id="load-more" class="button secondary" style="display: none;">加载更多</button>
            <span id="commit-count"></span>
        </div>

        <div id="graph-container" class="graph-container">
            <svg id="commit-graph" width="100%" height="600"></svg>
        </div>

        <div id="commit-detail" class="commit-detail" style="display: none;">
            <h3>提交详情</h3>
            <div id="detail-content"></div>
        </div>
    </div>

    <script src="/static/graph.js"></script>
    <script>
        const projectId = {json.dumps(project_id)};
        const apiUrl = `/api/project/${{encodeURIComponent(projectId)}}/code/graph`;

        let currentPage = 0;
        let currentRef = "HEAD";
        let hasMore = true;
        let allNodes = [];

        async function loadGraph(ref, page) {{
            const url = `${{apiUrl}}?ref=${{encodeURIComponent(ref)}}&page=${{page}}&page_size=100`;
            const response = await fetch(url);
            const data = await response.json();

            if (!data.ok) {{
                alert(data.error || "加载失败");
                return;
            }}

            // Update branches dropdown
            if (page === 0 && data.branches) {{
                const select = document.getElementById("branch-select");
                select.innerHTML = data.branches.map(b =>
                    `<option value="${{b.name}}" ${{b.current ? 'selected' : ''}}>${{b.name}}${{b.current ? ' (当前)' : ''}}</option>`
                ).join('');
            }}

            // Append nodes
            if (page === 0) {{
                allNodes = data.nodes;
            }} else {{
                allNodes = allNodes.concat(data.nodes);
            }}

            hasMore = data.has_more;
            document.getElementById("load-more").style.display = hasMore ? "inline-block" : "none";
            document.getElementById("commit-count").textContent = `已加载 ${{allNodes.length}} 个提交`;

            renderGraph(allNodes);
        }}

        function renderGraph(nodes) {{
            const svg = document.getElementById("commit-graph");
            const width = svg.clientWidth;
            const rowHeight = 40;
            const laneWidth = 30;
            const height = Math.max(600, nodes.length * rowHeight + 50);

            svg.setAttribute("height", height);
            svg.innerHTML = "";

            // Draw lanes and connections
            const maxLane = Math.max(...nodes.map(n => n.lane), 0);

            // Draw edges (parent-child connections)
            nodes.forEach(node => {{
                const x1 = 50 + node.lane * laneWidth;
                const y1 = 30 + node.row * rowHeight;

                node.parents.forEach(parentOid => {{
                    const parent = nodes.find(n => n.oid === parentOid);
                    if (parent) {{
                        const x2 = 50 + parent.lane * laneWidth;
                        const y2 = 30 + parent.row * rowHeight;

                        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                        const d = `M ${{x1}} ${{y1}} L ${{x2}} ${{y2}}`;
                        path.setAttribute("d", d);
                        path.setAttribute("stroke", getLaneColor(parent.lane));
                        path.setAttribute("stroke-width", "2");
                        path.setAttribute("fill", "none");
                        svg.appendChild(path);
                    }}
                }});
            }});

            // Draw commit nodes
            nodes.forEach(node => {{
                const x = 50 + node.lane * laneWidth;
                const y = 30 + node.row * rowHeight;

                // Node circle
                const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                circle.setAttribute("cx", x);
                circle.setAttribute("cy", y);
                circle.setAttribute("r", "6");
                circle.setAttribute("fill", getLaneColor(node.lane));
                circle.setAttribute("stroke", "#fff");
                circle.setAttribute("stroke-width", "2");
                circle.style.cursor = "pointer";
                circle.addEventListener("click", () => showCommitDetail(node));
                svg.appendChild(circle);

                // Commit message
                const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
                text.setAttribute("x", x + 15);
                text.setAttribute("y", y + 4);
                text.setAttribute("font-size", "13");
                text.setAttribute("fill", "#1a1a1a");
                text.textContent = `${{node.short_oid}} ${{node.subject.substring(0, 60)}}`;
                text.style.cursor = "pointer";
                text.addEventListener("click", () => showCommitDetail(node));
                svg.appendChild(text);

                // Branch labels
                if (node.branches.length > 0) {{
                    node.branches.forEach((branch, i) => {{
                        const branchLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
                        branchLabel.setAttribute("x", width - 100);
                        branchLabel.setAttribute("y", y + 4);
                        branchLabel.setAttribute("font-size", "11");
                        branchLabel.setAttribute("fill", "#0066cc");
                        branchLabel.setAttribute("font-weight", "bold");
                        branchLabel.textContent = branch;
                        svg.appendChild(branchLabel);
                    }});
                }}
            }});
        }}

        function getLaneColor(lane) {{
            const colors = ["#0066cc", "#00aa66", "#cc6600", "#cc0066", "#6600cc", "#00ccaa"];
            return colors[lane % colors.length];
        }}

        function showCommitDetail(node) {{
            const detail = document.getElementById("commit-detail");
            const content = document.getElementById("detail-content");

            content.innerHTML = `
                <p><strong>提交：</strong> ${{node.oid}}</p>
                <p><strong>消息：</strong> ${{node.subject}}</p>
                <p><strong>作者：</strong> ${{node.author_name}} &lt;${{node.author_email}}&gt;</p>
                <p><strong>时间：</strong> ${{new Date(node.committed_at).toLocaleString('zh-CN')}}</p>
                <p><strong>分支：</strong> ${{node.branches.join(", ") || "无"}}</p>
                <p><strong>父提交：</strong> ${{node.parents.length || "无"}}</p>
            `;

            detail.style.display = "block";
        }}

        document.getElementById("branch-select").addEventListener("change", (e) => {{
            currentRef = e.target.value;
            currentPage = 0;
            loadGraph(currentRef, currentPage);
        }});

        document.getElementById("load-more").addEventListener("click", () => {{
            currentPage++;
            loadGraph(currentRef, currentPage);
        }});

        // Initial load
        loadGraph("HEAD", 0);
    </script>
    '''

    return layout(f"{project['name']} - 代码图谱", content)


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
    class Handler(BaseHTTPRequestHandler):
        server_version = "LLMWikiWeb/0.3"

        def headers_out(self, code: int, content_type: str, length: int) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
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
            if self.headers.get("Host", "") not in {
                f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"
            }:
                self.json({"ok": False, "error": "不允许的 Host。"}, 403)
                return
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    self.json({"ok": True, "service": "llmwiki-web", "version": plugin_version()})
                    return
                if parsed.path in {"/static/notebook.css", "/static/notebook.js", "/static/app.js", "/static/progress.js", "/static/progress.css"}:
                    target = SCRIPT_DIR / "static" / parsed.path.rsplit("/", 1)[-1]
                    raw = target.read_bytes()
                    content_type = "text/css" if target.suffix == ".css" else "text/javascript"
                    self.headers_out(200, content_type + "; charset=utf-8", len(raw))
                    self.wfile.write(raw)
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/progress", parsed.path)
                if match:
                    try:
                        project = get_project(unquote(match[1]), home=home)["project"]
                        self.json(progress.load(project))
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
                        result = (notebook.history(project, match[2], (params.get("revision") or [None])[0])
                                  if match[3] else notebook.load(project, match[2]))
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
                    self.html(home_page(home, params))
                    return
                if parsed.path == "/settings":
                    self.html(settings_page(home, params))
                    return
                if parsed.path == "/search":
                    self.html(search_page(home, params))
                    return
                match = re.fullmatch(r"/project/([^/]+)/literature", parsed.path)
                if match:
                    self.html(literature_catalog_list_page(home, unquote(match.group(1)), params))
                    return
                match = re.fullmatch(r"/project/([^/]+)/literature/catalog", parsed.path)
                if match:
                    self.html(literature_catalog_list_page(home, unquote(match.group(1)), params))
                    return
                match = re.fullmatch(r"/project/([^/]+)/literature/item/([a-f0-9]{32})", parsed.path)
                if match:
                    self.html(literature_detail_page(home, unquote(match.group(1)), match.group(2)))
                    return
                match = re.fullmatch(r"/project/([^/]+)/literature/migrate", parsed.path)
                if match:
                    self.html(literature_migration_preview_page(home, unquote(match.group(1))))
                    return
                match = re.fullmatch(r"/project/([^/]+)/literature/old", parsed.path)
                if match:
                    self.html(literature_library_page(home, unquote(match.group(1))))
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
                match = re.fullmatch(r"/api/project/([^/]+)/code/graph", parsed.path)
                if match:
                    try:
                        project = get_project(unquote(match[1]), home=home)["project"]
                        source_root = Path(str(project["source_root"]))

                        # Check if it's a git repository
                        git_exe = detect_git_executable()
                        if not is_git_repository(source_root, git_exe):
                            self.json({"ok": False, "error": "Not a git repository"}, 400)
                            return

                        # Get parameters
                        ref = (params.get("ref") or ["HEAD"])[-1]
                        page = int((params.get("page") or ["0"])[-1])
                        page_size = int((params.get("page_size") or ["100"])[-1])

                        # Build graph
                        result = build_graph(source_root, git_exe, ref=ref, page=page, page_size=page_size)
                        self.json(result)
                    except (LLMWikiError, ValueError, OSError) as exc:
                        self.json({"ok": False, "error": str(exc)}, 400)
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
            match = re.fullmatch(r"/api/project/([^/]+)/notebook/([a-f0-9]{32})", path)
            if not match:
                self.json({"ok": False, "error": "请求路径无效。"}, 400)
                return
            try:
                project = get_project(unquote(match[1]), home=home)["project"]
                form = self.form()
                notebook.save(
                    project,
                    match[2],
                    content=form.get("content", ""),
                    title=form.get("title", ""),
                    tags=form.get("tags", ""),
                    expected_revision=form.get("expected_revision") or None,
                )
                self.json({"ok": True})
            except notebook.NotebookConflict as exc:
                self.json({"ok": False, "error": str(exc)}, 409)
            except (LLMWikiError, ValueError, OSError) as exc:
                self.json({"ok": False, "error": str(exc)}, 400)

        def literature_add_post(self, project_id: str) -> None:
            """Handle adding a literature item."""
            try:
                project = get_project(project_id, home=home)["project"]
                wiki_root = Path(str(project["wiki_root"]))
                catalog = LiteratureCatalog(wiki_root)

                form = self.form()
                input_text = form.get("input", "").strip()
                expected_revision = form.get("revision")

                if not input_text:
                    redirect(self, msgurl(purl(project_id) + "/literature", error="请输入文献信息"))
                    return

                # Parse input: DOI, arXiv, URL, or title
                doi = None
                arxiv = None
                url = None
                title = input_text

                if input_text.startswith("10.") and "/" in input_text:
                    doi = input_text
                    title = f"Paper with DOI {doi}"
                elif "arxiv.org" in input_text.lower() or input_text.startswith("arXiv:"):
                    arxiv = input_text
                    title = f"Paper on arXiv {arxiv}"
                elif input_text.startswith("http://") or input_text.startswith("https://"):
                    url = input_text
                    title = f"Paper at {url[:50]}..."

                result = catalog.create_item(
                    title=title,
                    doi=doi,
                    arxiv=arxiv,
                    urls=[{"url": url, "kind": "publisher"}] if url else [],
                    identity_status="unverified",
                    collection_source="manual",
                    expected_revision=expected_revision
                )

                if result.get("warnings"):
                    redirect(self, msgurl(purl(project_id) + "/literature", error=str(result["warnings"][0])))
                else:
                    item_id = result["item"]["id"]
                    redirect(self, msgurl(purl(project_id) + f"/literature/item/{item_id}", message="文献已添加"))

            except RevisionConflictError:
                redirect(self, msgurl(purl(project_id) + "/literature", error="目录已被其他操作修改，请刷新后重试"))
            except Exception as exc:
                redirect(self, msgurl(purl(project_id) + "/literature", error=str(exc)))

        def literature_delete_post(self, project_id: str, item_id: str) -> None:
            """Handle removing a literature item."""
            try:
                project = get_project(project_id, home=home)["project"]
                wiki_root = Path(str(project["wiki_root"]))
                catalog = LiteratureCatalog(wiki_root)

                result = catalog.remove_item(item_id)

                if result["ok"]:
                    redirect(self, msgurl(purl(project_id) + "/literature", message="文献已移除"))
                else:
                    redirect(self, msgurl(purl(project_id) + "/literature", error="移除失败"))

            except Exception as exc:
                redirect(self, msgurl(purl(project_id) + "/literature", error=str(exc)))

        def literature_migrate_apply_post(self, project_id: str) -> None:
            """Handle applying migration."""
            try:
                project = get_project(project_id, home=home)["project"]
                wiki_root = Path(str(project["wiki_root"]))
                source_root = Path(str(project["source_root"]))
                catalog = LiteratureCatalog(wiki_root)

                form = self.form()

                # Get selected paths from form (multiple checkboxes)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                parsed = parse_qs(body, keep_blank_values=True)
                selected_paths = parsed.get("import_path", [])

                if not selected_paths:
                    redirect(self, msgurl(purl(project_id) + "/literature/migrate", error="未选择任何文件"))
                    return

                result = apply_migration(catalog, source_root, selected_paths, "migration")

                if result["ok"]:
                    message = f"已导入 {len(result['imported'])} 篇文献"
                    redirect(self, msgurl(purl(project_id) + "/literature", message=message))
                else:
                    errors = ", ".join(e["error"] for e in result["errors"][:3])
                    redirect(self, msgurl(purl(project_id) + "/literature/migrate", error=errors))

            except Exception as exc:
                redirect(self, msgurl(purl(project_id) + "/literature/migrate", error=str(exc)))

        def literature_migrate_rollback_post(self, project_id: str) -> None:
            """Handle rolling back migration."""
            try:
                project = get_project(project_id, home=home)["project"]
                wiki_root = Path(str(project["wiki_root"]))
                catalog = LiteratureCatalog(wiki_root)

                form = self.form()
                migration_id = form.get("migration_id", "")

                if not migration_id:
                    redirect(self, msgurl(purl(project_id) + "/literature", error="未指定迁移ID"))
                    return

                result = rollback_migration(catalog, migration_id)

                if result["ok"]:
                    redirect(self, msgurl(purl(project_id) + "/literature", message="迁移已回滚"))
                else:
                    redirect(self, msgurl(purl(project_id) + "/literature", error=result.get("error", "回滚失败")))

            except Exception as exc:
                redirect(self, msgurl(purl(project_id) + "/literature", error=str(exc)))

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
                progress_match = re.fullmatch(r"/api/project/([^/]+)/progress", path)
                if progress_match:
                    project = get_project(unquote(progress_match[1]), home=home)["project"]
                    self.json(progress.update(project, payload))
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
                    if not isinstance(text, str) or len(text) > 100_000:
                        raise LLMWikiError("预览文字过长。")
                    result = {"ok": True, "html": render_markdown(text, project["id"], "records/manual/preview.md")}
                else:
                    result = notebook.save(project, action, payload)
                self.json(result)
            except notebook.NotebookConflict as exc:
                self.json({"ok": False, "error": str(exc)}, 409)
            except (LLMWikiError, OSError, ValueError, TypeError) as exc:
                self.json({"ok": False, "error": str(exc)}, 400)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/project/"):
                # Check for literature API routes first
                match = re.fullmatch(r"/api/project/([^/]+)/literature/add", parsed.path)
                if match:
                    self.literature_add_post(unquote(match.group(1)))
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/literature/item/([a-f0-9]{32})/delete", parsed.path)
                if match:
                    self.literature_delete_post(unquote(match.group(1)), match.group(2))
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/literature/migrate/apply", parsed.path)
                if match:
                    self.literature_migrate_apply_post(unquote(match.group(1)))
                    return
                match = re.fullmatch(r"/api/project/([^/]+)/literature/migrate/rollback", parsed.path)
                if match:
                    self.literature_migrate_rollback_post(unquote(match.group(1)))
                    return
                # Fallback to notebook routes
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