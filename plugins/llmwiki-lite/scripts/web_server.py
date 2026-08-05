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

from llmwiki_core import LLMWikiError  # noqa: E402
from literature_web import (  # noqa: E402
    literature_compare_page,
    literature_library_page,
    literature_read_page,
    source_document_path,
)
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
)

MAX_FORM_BYTES = 65_536


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
                "default-src 'self';img-src 'self' data:;style-src 'self';script-src 'unsafe-inline'",
            )
            self.end_headers()

        def html(self, content: str, code: int = 200) -> None:
            raw = content.encode("utf-8")
            self.headers_out(code, "text/html; charset=utf-8", len(raw))
            self.wfile.write(raw)

        def json(self, payload: dict[str, Any], code: int = 200) -> None:
            raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.headers_out(code, "application/json; charset=utf-8", len(raw))
            self.wfile.write(raw)

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
            params = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    self.json({"ok": True, "service": "llmwiki-web", "version": "0.3.0"})
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
                    self.html(literature_library_page(home, unquote(match.group(1))))
                    return
                match = re.fullmatch(r"/project/([^/]+)/records", parsed.path)
                if match:
                    self.html(records_page(home, unquote(match.group(1)), params))
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

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
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