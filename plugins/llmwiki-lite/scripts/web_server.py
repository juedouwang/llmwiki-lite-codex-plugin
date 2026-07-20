"""Loopback-only website for registered LLM Wiki projects."""

from __future__ import annotations
import argparse
import html
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
from llmwiki_core import LLMWikiError, status, wiki_list  # noqa: E402
from llmwiki_registry import (  # noqa: E402
    get_project,
    list_projects,
    llmwiki_home,
    load_settings,
    register_project,
    unregister_project,
    update_project_storage,
    update_settings,
)
from markdown_renderer import render_markdown  # noqa: E402

MAX_FORM_BYTES = 65536
MAX_SEARCH_BYTES = 2 * 1024 * 1024
STYLE = """
:root{--bg:#f5f6f8;--panel:#fff;--text:#20242a;--muted:#68707b;--line:#dfe3e8;--accent:#315c9b;--soft:#edf3fb;--danger:#9a3030}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}header{position:sticky;top:0;z-index:5;background:#fffffff5;border-bottom:1px solid var(--line)}nav{max-width:1240px;margin:auto;padding:12px 24px;display:flex;align-items:center;gap:20px}.brand{font-weight:700!important;color:var(--text)}.spacer{flex:1}main{max-width:1240px;margin:24px auto;padding:0 24px 48px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:18px;box-shadow:0 1px 2px #00000008}.card h2,.panel h2{margin-top:0}.meta,.muted{color:var(--muted);font-size:13px}.path{font-family:Consolas,monospace;overflow-wrap:anywhere}.badge{display:inline-block;padding:2px 8px;background:var(--soft);border-radius:99px;font-size:12px;margin-right:6px}.actions{display:flex;flex-wrap:wrap;gap:9px;margin-top:14px}.button,button{display:inline-block;border:1px solid #b9c4d2;border-radius:6px;padding:7px 12px;background:#fff;color:var(--text);cursor:pointer;font:inherit}.primary{color:#fff!important;background:var(--accent)!important;border-color:var(--accent)!important}.danger{color:var(--danger)!important;border-color:#d8abab!important}label{display:block;font-weight:600;margin:12px 0 5px}input[type=text],input[type=number],input[type=search]{width:100%;padding:9px 10px;border:1px solid #c8ced6;border-radius:6px;background:#fff;font:inherit}input[type=checkbox]{width:auto;margin-right:7px}form.inline{display:flex;gap:8px;align-items:center}form.inline input{width:auto;flex:1}.notice{padding:10px 13px;background:#edf7ed;border:1px solid #bfdcbe;border-radius:7px;margin-bottom:16px}.error{background:#fff0f0;border-color:#e2b8b8}.project-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:18px;align-items:start}.sidebar{position:sticky;top:70px;max-height:calc(100vh - 94px);overflow:auto}.page-list{list-style:none;padding:0;margin:0}.page-list li{border-bottom:1px solid #eef0f2;padding:7px 0}.document{min-width:0}.document h1,.document h2,.document h3{line-height:1.3;margin-top:1.5em;scroll-margin-top:80px}.document h1:first-child{margin-top:0}.document pre{overflow:auto;margin:0;padding:16px;background:#15191f;color:#e7edf5;border-radius:7px}.document code{font-family:Consolas,monospace;font-size:.92em;background:#eef1f4;padding:.12em .3em;border-radius:4px}.document pre code{background:none;padding:0}.code-block{position:relative;margin:1em 0}.code-language{position:absolute;right:10px;top:6px;color:#aab6c4;font-size:11px}.document blockquote{margin:1em 0;padding:3px 16px;border-left:4px solid #b8c3d0;color:#4e5966}.callout{margin:1em 0;border:1px solid #bfd0e7;border-left:4px solid var(--accent);background:#f5f9ff;border-radius:6px;padding:12px 15px}.callout-title{font-weight:700}.frontmatter{background:#f8fafc;border:1px solid var(--line);border-radius:7px;padding:9px 12px;margin-bottom:20px}.frontmatter summary{cursor:pointer;font-weight:600}.frontmatter dl{display:grid;grid-template-columns:minmax(100px,180px) 1fr}.frontmatter dt,.frontmatter dd{border-top:1px solid #e8ebef;padding:5px 0;margin:0;overflow-wrap:anywhere}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{border:1px solid var(--line);padding:7px 9px;text-align:left}th{background:#f1f4f7}figure{margin:1em 0}figure img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px}figcaption{color:var(--muted);font-size:12px}.wikilink{background:#eef4ff;padding:0 3px;border-radius:3px}.search-result{padding:12px 0;border-bottom:1px solid var(--line)}.storage-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}footer{color:var(--muted);text-align:center;padding:25px}@media(max-width:800px){main,nav{padding-left:14px;padding-right:14px}.project-layout{grid-template-columns:1fr}.sidebar{position:static;max-height:none}.storage-grid{grid-template-columns:1fr}}
"""


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_path(root: Path, relative: str) -> Path:
    if not relative or "\0" in relative or Path(relative).is_absolute():
        raise LLMWikiError("Invalid Wiki-relative path.")
    target = (root / relative.replace("\\", "/")).resolve(strict=False)
    if not within(target, root.resolve(strict=False)):
        raise LLMWikiError("Path escapes the Wiki root.")
    return target


def purl(pid: str) -> str:
    return f"/project/{quote(pid, safe='')}"


def pageurl(pid: str, page: str) -> str:
    return f"{purl(pid)}/page/{quote(page, safe='/')}"


def layout(title: str, body: str, query: str = "") -> str:
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · LLM Wiki</title><link rel="stylesheet" href="/static/style.css"></head><body><header><nav><a class="brand" href="/">LLM Wiki</a><a href="/">项目</a><a href="/settings">设置</a><span class="spacer"></span><form class="inline" action="/search"><input type="search" name="q" value="{esc(query)}" placeholder="搜索全部 Wiki"><button>搜索</button></form></nav></header><main>{body}</main><footer>本地知识视图 · 仅监听 127.0.0.1</footer></body></html>'''


def notice(params: dict[str, list[str]]) -> str:
    if params.get("message"):
        return f'<div class="notice">{esc(params["message"][0])}</div>'
    if params.get("error"):
        return f'<div class="notice error">{esc(params["error"][0])}</div>'
    return ""


def summary(project: dict[str, Any]) -> dict[str, Any]:
    try:
        return status(
            str(project["source_root"]), state_root=str(project["state_root"])
        )
    except (LLMWikiError, OSError, ValueError) as exc:
        return {
            "wiki_page_count": 0,
            "snapshot_at": None,
            "dirty_paths": [],
            "error": str(exc),
        }


def home_page(home: str, params: dict[str, list[str]]) -> str:
    listed = list_projects(home)
    cards = []
    for p in listed["projects"]:
        s = summary(p)
        dirty = s.get("dirty_paths") or []
        badges = (
            f'<span class="badge">{int(s.get("wiki_page_count", 0))} 个页面</span>'
            + (f'<span class="badge">{len(dirty)} 个变更提示</span>' if dirty else "")
        )
        cards.append(
            f'''<article class="card"><h2><a href="{purl(p["id"])}">{esc(p["name"])}</a></h2><div>{badges}</div><p class="meta">源项目</p><div class="path">{esc(p["source_root"])}</div><p class="meta">Wiki</p><div class="path">{esc(p["wiki_root"])}</div><p class="meta">最近快照：{esc(s.get("snapshot_at") or "尚未建立")}</p><div class="actions"><a class="button primary" href="{purl(p["id"])}">打开</a><a class="button" href="/settings#project-{quote(p["id"], safe="")}">修改位置</a></div></article>'''
        )
    empty = '<div class="panel"><h2>还没有注册项目</h2><p>在设置页注册 Codex 当前打开的项目。未配置全局目录时，新用户默认使用 <code>&lt;project-root&gt;/wiki</code>。</p><a class="button primary" href="/settings">注册项目</a></div>'
    body = (
        notice(params)
        + '<h1>项目</h1><p class="muted">注册只建立项目身份和存储位置，不会假装已经扫描或理解项目。</p>'
        + (f'<div class="grid">{"".join(cards)}</div>' if cards else empty)
    )
    return layout("项目", body)


def project_page(home: str, pid: str, params: dict[str, list[str]]) -> str:
    p = get_project(pid, home=home)["project"]
    s = summary(p)
    pages = wiki_list(str(p["source_root"]), state_root=str(p["state_root"]))["pages"]
    items = (
        "".join(
            f'<li><a href="{pageurl(pid, x["path"])}">{esc(x["title"])}</a><div class="meta path">{esc(x["path"])}</div></li>'
            for x in pages
        )
        or "<li>尚无 Markdown 页面</li>"
    )
    dirty = (
        "".join(
            f'<li class="path">{esc(x)}</li>' for x in (s.get("dirty_paths") or [])[:50]
        )
        or "<li>无</li>"
    )
    body = (
        notice(params)
        + f'''<h1>{esc(p["name"])}</h1><div class="panel"><div class="storage-grid"><div><div class="meta">源项目</div><div class="path">{esc(p["source_root"])}</div></div><div><div class="meta">Wiki</div><div class="path">{esc(p["wiki_root"])}</div></div><div><div class="meta">状态</div><div class="path">{esc(p["state_root"])}</div></div><div><div class="meta">最近快照</div><div>{esc(s.get("snapshot_at") or "尚未建立")}</div></div></div><div class="actions"><a class="button" href="/settings#project-{quote(pid, safe="")}">修改存储位置</a></div></div><div class="project-layout"><aside class="panel sidebar"><h2>Wiki 页面</h2><form action="/search"><input type="hidden" name="project_id" value="{esc(pid)}"><input type="search" name="q" placeholder="搜索此项目"><button>搜索</button></form><ul class="page-list">{items}</ul></aside><section class="panel"><h2>最近变化提示</h2><p class="muted">Hook 只是提示；需要准确结果时应重新执行 snapshot。</p><ul>{dirty}</ul></section></div>'''
    )
    return layout(str(p["name"]), body)


def page_view(home: str, pid: str, relative: str) -> str:
    p = get_project(pid, home=home)["project"]
    root = Path(str(p["wiki_root"])).resolve(strict=False)
    target = safe_path(root, relative)
    if not target.is_file() or target.suffix.lower() not in {".md", ".markdown"}:
        raise FileNotFoundError(relative)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LLMWikiError("Markdown page must be UTF-8.") from exc
    pages = wiki_list(str(p["source_root"]), state_root=str(p["state_root"]))["pages"]
    items = "".join(
        f'<li><a href="{pageurl(pid, x["path"])}">{esc(x["title"])}</a></li>'
        for x in pages
    )
    body = f'''<div class="project-layout"><aside class="panel sidebar"><a href="{purl(pid)}">← {esc(p["name"])}</a><h2>页面</h2><ul class="page-list">{items}</ul></aside><article class="panel document"><div class="meta path">{esc(relative)}</div>{render_markdown(text, pid, relative)}</article></div>'''
    return layout(target.stem, body)


def search_page(home: str, params: dict[str, list[str]]) -> str:
    query = (params.get("q") or [""])[0].strip()
    selected = (params.get("project_id") or [""])[0].strip()
    projects = list_projects(home)["projects"]
    if selected:
        projects = [p for p in projects if p.get("id") == selected]
    results = []
    lower = query.lower()
    if query:
        for p in projects:
            root = Path(str(p["wiki_root"]))
            if not root.exists():
                continue
            for page in sorted(root.rglob("*.md")):
                try:
                    if page.stat().st_size > MAX_SEARCH_BYTES:
                        continue
                    text = page.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                rel = page.relative_to(root).as_posix()
                line = next(
                    (x.strip()[:300] for x in text.splitlines() if lower in x.lower()),
                    "",
                )
                if lower in rel.lower() or line:
                    results.append(
                        f'<article class="search-result"><a href="{pageurl(p["id"], rel)}"><strong>{esc(page.stem)}</strong></a><div class="meta">{esc(p["name"])} · <span class="path">{esc(rel)}</span></div><div>{esc(line)}</div></article>'
                    )
                    if len(results) >= 500:
                        break
            if len(results) >= 500:
                break
    body = f'''<h1>搜索 Wiki</h1><form class="inline" action="/search"><input type="search" name="q" value="{esc(query)}" autofocus><button class="primary">搜索</button></form><p class="muted">搜索 Markdown 路径和 UTF-8 正文，最多显示 500 项。</p>{"".join(results) if results else '<div class="panel">没有匹配结果。</div>'}'''
    return layout("搜索", body, query)


def settings_page(home: str, params: dict[str, list[str]]) -> str:
    s = load_settings(home)
    projects = list_projects(home)["projects"]
    forms = []
    for p in projects:
        forms.append(
            f'''<section class="panel" id="project-{quote(p["id"], safe="")}"><h2>{esc(p["name"])}</h2><p class="meta path">{esc(p["source_root"])}</p><form method="post" action="/project/{quote(p["id"], safe="")}/storage"><div class="storage-grid"><div><label>Wiki 目录</label><input type="text" name="wiki_root" value="{esc(p["wiki_root"])}" required></div><div><label>状态目录</label><input type="text" name="state_root" value="{esc(p["state_root"])}" required></div></div><label><input type="checkbox" name="copy_existing" value="1" checked>复制现有内容后切换（旧目录保留）</label><button class="primary">保存位置</button></form><form method="post" action="/project/{quote(p["id"], safe="")}/unregister" onsubmit="return confirm('只取消注册，不删除任何文件。继续？')"><button class="danger">取消注册</button></form></section>'''
        )
    body = (
        notice(params)
        + f'''<h1>设置</h1><section class="panel"><h2>默认 Wiki 根目录</h2><p>新注册项目使用 <code>&lt;默认根目录&gt;/&lt;project_id&gt;</code>。清空后恢复默认 <code>&lt;project-root&gt;/wiki</code>。</p><form method="post" action="/settings/default-wiki-root"><label>默认 Wiki 根目录</label><input type="text" name="default_wiki_root" value="{esc(s.get("default_wiki_root") or "")}" placeholder="留空表示项目内 wiki"><label>网站端口</label><input type="number" name="web_port" min="1024" max="65535" value="{int(s.get("web_port", 8765))}"><button class="primary">保存全局设置</button></form></section><section class="panel"><h2>注册项目</h2><p class="muted">source_root 是项目身份。注册不会扫描、总结或写知识页。</p><form method="post" action="/project/register"><label>源项目目录</label><input type="text" name="source_root" required><label>显示名称（可选）</label><input type="text" name="name"><div class="storage-grid"><div><label>Wiki 目录（可选）</label><input type="text" name="wiki_root" placeholder="按全局默认策略选择"></div><div><label>状态目录（可选）</label><input type="text" name="state_root" placeholder="默认使用 LLM Wiki Home"></div></div><button class="primary">注册</button></form></section><h1>已注册项目</h1><div class="grid">{"".join(forms) if forms else '<div class="panel">尚无项目。</div>'}</div>'''
    )
    return layout("设置", body)


def redirect(h: BaseHTTPRequestHandler, location: str) -> None:
    h.send_response(HTTPStatus.SEE_OTHER)
    h.send_header("Location", location)
    h.send_header("Content-Length", "0")
    h.end_headers()


def msgurl(path: str, message: str | None = None, error: str | None = None) -> str:
    return (
        path
        + ("&" if "?" in path else "?")
        + urlencode({"message": message} if message else {"error": error or "操作失败"})
    )


def create_handler(home: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "LLMWikiWeb/0.2"

        def headers_out(self, code: int, ctype: str, length: int) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
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
                raise LLMWikiError("Invalid Content-Length.") from exc
            if length < 0 or length > MAX_FORM_BYTES:
                raise LLMWikiError("Form body is too large.")
            parsed = parse_qs(
                self.rfile.read(length).decode("utf-8"), keep_blank_values=True
            )
            return {k: v[-1] for k, v in parsed.items()}

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    self.json({"ok": True, "service": "llmwiki-web"})
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
                m = re.fullmatch(r"/project/([^/]+)", parsed.path)
                if m:
                    self.html(project_page(home, unquote(m.group(1)), params))
                    return
                m = re.fullmatch(r"/project/([^/]+)/page/(.+)", parsed.path)
                if m:
                    self.html(page_view(home, unquote(m.group(1)), unquote(m.group(2))))
                    return
                m = re.fullmatch(r"/project/([^/]+)/asset/(.+)", parsed.path)
                if m:
                    p = get_project(unquote(m.group(1)), home=home)["project"]
                    target = safe_path(
                        Path(str(p["wiki_root"])).resolve(strict=False),
                        unquote(m.group(2)),
                    )
                    if not target.is_file():
                        raise FileNotFoundError(str(target))
                    raw = target.read_bytes()
                    self.headers_out(
                        200,
                        mimetypes.guess_type(target.name)[0]
                        or "application/octet-stream",
                        len(raw),
                    )
                    self.wfile.write(raw)
                    return
                self.html(
                    layout(
                        "未找到",
                        '<div class="panel"><h1>404</h1><p>页面不存在。</p></div>',
                    ),
                    404,
                )
            except FileNotFoundError:
                self.html(
                    layout(
                        "未找到",
                        '<div class="panel"><h1>404</h1><p>文件不存在。</p></div>',
                    ),
                    404,
                )
            except (LLMWikiError, OSError, ValueError) as exc:
                self.html(
                    layout("错误", f'<div class="notice error">{esc(exc)}</div>'), 400
                )

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                f = self.form()
                if parsed.path == "/settings/default-wiki-root":
                    update_settings(
                        home=home,
                        default_wiki_root=f.get("default_wiki_root") or None,
                        web_port=int(f.get("web_port") or 8765),
                    )
                    redirect(self, msgurl("/settings", message="全局设置已保存。"))
                    return
                if parsed.path == "/project/register":
                    r = register_project(
                        f.get("source_root", ""),
                        name=f.get("name") or None,
                        wiki_root=f.get("wiki_root") or None,
                        state_root=f.get("state_root") or None,
                        home=home,
                    )
                    redirect(
                        self, msgurl(purl(r["project"]["id"]), message="项目已注册。")
                    )
                    return
                m = re.fullmatch(r"/project/([^/]+)/storage", parsed.path)
                if m:
                    pid = unquote(m.group(1))
                    update_project_storage(
                        pid,
                        wiki_root=f.get("wiki_root") or None,
                        state_root=f.get("state_root") or None,
                        home=home,
                        copy_existing=f.get("copy_existing") == "1",
                    )
                    redirect(
                        self,
                        msgurl(
                            "/settings", message="项目存储位置已更新；旧目录未删除。"
                        )
                        + f"#project-{quote(pid, safe='')}",
                    )
                    return
                m = re.fullmatch(r"/project/([^/]+)/unregister", parsed.path)
                if m:
                    unregister_project(unquote(m.group(1)), home=home)
                    redirect(
                        self,
                        msgurl("/settings", message="项目已取消注册；文件未删除。"),
                    )
                    return
                self.html(
                    layout("未找到", '<div class="panel"><h1>404</h1></div>'), 404
                )
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
        raise LLMWikiError("The LLM Wiki website may only bind to a loopback address.")
    return ThreadingHTTPServer(
        (host, int(port)), create_handler(str(llmwiki_home(home)))
    )


def is_server(host: str, port: int) -> bool:
    try:
        c = HTTPConnection(host, port, timeout=0.5)
        c.request("GET", "/health")
        r = c.getresponse()
        payload = json.loads(r.read().decode("utf-8"))
        c.close()
        return r.status == 200 and payload.get("service") == "llmwiki-web"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def start_background(
    home: str | None = None, port: int | None = None, open_browser: bool = False
) -> dict[str, Any]:
    root = llmwiki_home(home)
    settings = load_settings(str(root))
    selected = int(port or settings.get("web_port", 8765))
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
        pythonw = Path(sys.executable).with_name("pythonw.exe")
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
    raise LLMWikiError(f"The website did not start on {url}")


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
            print(f"LLM Wiki website: {url}", flush=True)
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
    parser = argparse.ArgumentParser(
        description="Serve registered LLM Wiki projects on loopback."
    )
    parser.add_argument("--home")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()
    settings = load_settings(args.home)
    return serve(
        args.home, args.host, args.port or int(settings["web_port"]), args.open_browser
    )


if __name__ == "__main__":
    raise SystemExit(main())
