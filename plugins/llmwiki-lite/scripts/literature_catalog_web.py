"""Independent literature pages and opt-in HTTP adapter.

Integration: call dispatch_literature_http(handler, home) BEFORE legacy literature
routes from both do_GET/do_POST. True means response sent. No server is started here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from research_web_ui import esc, layout, purl
from literature_catalog import (
    LiteratureCatalog, LiteratureCatalogError, attachment_for, atomic_json,
    item_revision, literature_collect, note_matches, project_for, safe_path,
    _new_id, _normalize_url, _utc_now,
)

MAX_BODY = 2 * 1024 * 1024


def _classify_migration_file(filename, size_bytes):
    # This is a file picker, not a Python judgment that a file is a paper.
    return {"category": "needs_confirmation", "reason": "仅明确选择后收录，不凭文件名判断文献。"}


def _scan_migration_candidates(source_root):
    root = Path(source_root).resolve()
    folder = safe_path(root, "references/papers")
    candidates = []
    if folder.exists():
        for file in sorted(folder.rglob("*")):
            if len(candidates) >= 1000:
                break
            relative = file.relative_to(root).as_posix()
            try:
                target = safe_path(root, relative)
                if target.is_file() and target.suffix.lower() in {".pdf", ".html", ".htm", ".epub", ".docx", ".md"}:
                    candidates.append({"path": relative, "size": target.stat().st_size, "title": target.stem})
            except (LiteratureCatalogError, OSError):
                continue
    return {"candidates": [], "excluded": [], "needs_confirmation": candidates}


def apply_migration(catalog, source_root, selected_paths, collection_source="migration"):
    if not isinstance(selected_paths, list) or len(selected_paths) > 100 or any(not isinstance(p, str) for p in selected_paths):
        raise LiteratureCatalogError("一次最多明确选择100个文件。")
    imported, skipped, errors = [], [], []
    for relative in dict.fromkeys(selected_paths):
        try:
            attachment = attachment_for({"source_root": str(source_root)}, relative)
            result = catalog.upsert(title=Path(relative).stem, attachments=[attachment], explicit=True, collection_source=collection_source)
            entry = {"path": relative, "item_id": result["item_id"], "item_revision": result["item_revision"]}
            (imported if result["action"] in {"created", "restored"} else skipped).append(entry)
        except LiteratureCatalogError as exc:
            errors.append({"path": relative, "error": str(exc), "code": exc.code})
    migration_id = _new_id()
    manifest = {"migration_id": migration_id, "timestamp": _utc_now(), "imported": imported, "skipped": skipped, "errors": errors}
    atomic_json(safe_path(catalog.wiki_root, f".literature/migrations/{migration_id}.json"), manifest)
    return {"ok": not errors, **manifest}


def rollback_migration(catalog, migration_id):
    if not isinstance(migration_id, str) or not re.fullmatch(r"[a-f0-9]{32}", migration_id):
        raise LiteratureCatalogError("导入记录 ID 无效。")
    path = safe_path(catalog.wiki_root, f".literature/migrations/{migration_id}.json")
    if not path.exists():
        raise LiteratureCatalogError("导入记录不存在。", "NOT_FOUND", 404)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    removed, errors = [], []
    for entry in manifest["imported"]:
        try:
            catalog.remove_item(entry["item_id"], expected_item_revision=entry["item_revision"])
            removed.append(entry["item_id"])
        except LiteratureCatalogError as exc:
            errors.append({"item_id": entry["item_id"], "code": exc.code, "error": str(exc)})
    return {"ok": not errors, "removed": removed, "errors": errors}


def _shell(home, project_id, title, body):
    assets = '<link rel="stylesheet" href="/static/literature-catalog.css"><script defer src="/static/literature-catalog.js"></script>'
    return layout(title, assets + f'<section id="literature" data-project="{esc(project_id)}">{body}</section>', project_id=project_id, active="literature", home=home)


def _form(edit=False, item=None):
    item = item or {}
    locator = (item.get("urls") or [{}])[0].get("url", "")
    fields = f'''<label>地址 / DOI / arXiv<input name="locator" value="{esc(locator)}" required maxlength="4096" autocomplete="off"></label>
<label>标题{'' if edit else '（选填）'}<input name="title" value="{esc(item.get('title', ''))}" maxlength="1000" {'required' if edit else ''}></label>'''
    if edit:
        fields += f'''<label>作者（每行一位，选填）<textarea name="authors">{esc(chr(10).join(item.get('authors', [])))}</textarea></label>
<label>年份（选填）<input name="year" type="number" min="1000" max="9999" value="{esc(str(item.get('year') or ''))}"></label>'''
    return f'''<form data-literature-form="{'edit' if edit else 'add'}" hidden>{fields}<p role="alert" class="literature-error"></p>
<button type="submit">{'保存' if edit else '收藏'}</button><button type="button" data-cancel>取消</button></form>'''


def literature_catalog_list_page(home, project_id, params=None):
    params = params or {}
    project = project_for(project_id, home)
    query = params.get("q", [""])[0][:1000]
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
    except ValueError:
        page = 1
    result = LiteratureCatalog(project["wiki_root"]).list_items(query=query, offset=(page - 1) * 50, limit=50)
    base = purl(project_id) + "/literature"
    rows = []
    for item in result["items"]:
        author = ", ".join(item.get("authors", [])[:3])
        meta = " · ".join(filter(None, [author, str(item.get("year") or "")]))
        badges = ("<span>有原文</span>" if item.get("attachments") else "") + ("<span>有笔记</span>" if item.get("reading_note_paths") else "")
        rows.append(f'<li id="lit-{esc(item["id"])}"><a data-item href="{base}/item/{esc(item["id"])}">{esc(item["title"])}</a><div class="literature-meta">{esc(meta)} {badges}</div></li>')
    paging = []
    for label, target in (("上一页", page - 1), ("下一页", page + 1)):
        if target >= 1 and (target < page or page * 50 < result["count"]):
            paging.append(f'<a href="{base}?q={quote(query)}&amp;page={target}">{label}</a>')
    empty = '<p class="literature-empty">还没有收藏文献。点击“添加文献”粘贴地址即可。</p>' if not result["count"] and not query else '<p>没有匹配的文献。</p>'
    body = f'''<header class="literature-toolbar"><h1>文献</h1><label>项目内搜索<input type="search" id="literature-search" value="{esc(query)}" placeholder="标题、作者、DOI / arXiv"></label><button data-add>添加文献</button>
<details><summary>更多</summary><a href="{base}/migrate">明确导入本地文件</a></details></header>
{_form()}<p role="status" id="literature-message"></p><div id="literature-results"><ul class="literature-list">{''.join(rows)}</ul>{empty if not rows else ''}<nav class="literature-paging">{''.join(paging)}</nav></div>
<details id="literature-collection"><summary>收录详情</summary><div data-collection-status>展开查看收录状态。</div><button data-retry hidden>请求重试</button></details>'''
    return _shell(home, project_id, "文献", body)


def literature_detail_page(home, project_id, item_id):
    project = project_for(project_id, home)
    item = LiteratureCatalog(project["wiki_root"]).get_item(item_id)
    if not item:
        raise LiteratureCatalogError("文献不存在。", "NOT_FOUND", 404)
    base = purl(project_id)
    links = []
    for index, entry in enumerate(item.get("urls", [])):
        url = _normalize_url(entry.get("url", ""))
        if url:
            links.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc("原地址" if index == 0 else "其他地址 " + str(index + 1))}</a>')
    papers = []
    for attachment in item.get("attachments", []):
        relative = attachment.get("path", "")
        try:
            valid = safe_path(project["source_root"], relative).is_file()
        except (LiteratureCatalogError, OSError):
            valid = False
        if valid:
            papers.append(relative)
            links.append(f'<a href="{base}/literature/read/{quote(relative, safe="/")}">原文：{esc(Path(relative).name)}</a>')
        else:
            links.append(f'<span>原文不可用：{esc(Path(relative).name)}</span>')
    for relative in item.get("reading_note_paths", []):
        try:
            target = safe_path(project["wiki_root"], relative)
            valid = target.is_file() and target.suffix.lower() == ".md"
        except (LiteratureCatalogError, OSError):
            valid = False
        if valid:
            links.append(f'<a href="{base}/page/{quote(relative, safe="/")}">笔记：{esc(Path(relative).name)}</a>')
            for paper in papers:
                if Path(paper).suffix.lower() == ".pdf" and note_matches(project, relative, [paper]):
                    links.append(f'<a href="{base}/literature/compare/{quote(paper, safe="/")}?note={quote("wiki:" + relative)}">对照阅读：{esc(Path(relative).name)}</a>')
        else:
            links.append(f'<span>笔记不可用：{esc(Path(relative).name)}</span>')
    refs = []
    for ref in item.get("source_refs", []):
        if not isinstance(ref, dict) or "kind" not in ref:
            refs.append(f'<li>{esc(json.dumps(ref, ensure_ascii=False) if isinstance(ref, dict) else str(ref))}</li>')
            continue
        locator = ref.get("locator", "")
        # Unverifiable chat deep links remain inert text, never invented navigation.
        label = "本次明确收藏" if ref.get("kind") == "manual" else "来源原文入口未核验"
        times = " · ".join(f"{label}：{ref[key]}" for key, label in (("occurred_at", "发生"), ("observed_at", "观察"), ("collected_at", "收录")) if ref.get(key))
        refs.append(f'<li><strong>{label}</strong><p>{esc(locator)}</p><p>{esc(ref.get("summary", ""))}</p><small>{esc(times)}</small></li>')
    meta = " · ".join(filter(None, [", ".join(item.get("authors", [])), str(item.get("year") or "")]))
    body = f'''<a data-back href="{base}/literature">返回文献列表</a><article data-item-id="{esc(item_id)}" data-revision="{item_revision(item)}">
<h1>{esc(item['title'])}</h1><p>{esc(meta)}</p><div class="literature-reading">{''.join(links)}</div>
<button data-edit>编辑资料</button>{_form(True, item)}<p role="status" id="literature-message"></p>
<details><summary>时间与来源</summary><p>收藏：{esc(item.get('created_at', ''))}</p><p>修改：{esc(item.get('updated_at', ''))}</p><ul>{''.join(refs) or '<li>原目录未记录来源。</li>'}</ul></details>
<details><summary>更多操作</summary><button data-remove>从文献清单移除</button></details></article>'''
    return _shell(home, project_id, item["title"], body)


def literature_migration_preview_page(home, project_id):
    project = project_for(project_id, home)
    options = _scan_migration_candidates(Path(project["source_root"]))["needs_confirmation"]
    checkboxes = ''.join(f'<label><input type="checkbox" name="selected_paths" value="{esc(i["path"])}">{esc(i["path"])}</label>' for i in options)
    body = f'''<a href="{purl(project_id)}/literature">返回文献</a><h1>明确导入本地文献</h1><p>文件列表不代表论文判定；只引用勾选文件，不搬移、不删除。最多显示1000项。</p>
<form data-literature-form="import">{checkboxes}<p role="alert" class="literature-error"></p><button type="submit">导入选中文献</button></form><p role="status" id="literature-message"></p>'''
    return _shell(home, project_id, "导入文献", body)


def _payload(handler):
    host = handler.headers.get("Host", "")
    port = handler.server.server_address[1]
    valid_hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
    if host not in valid_hosts or handler.headers.get("Origin") != "http://" + host or handler.headers.get("X-Literature-Request") != "1":
        raise LiteratureCatalogError("拒绝跨站或未授权的文献写请求。", "FORBIDDEN", 403)
    if handler.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json" or handler.headers.get("Transfer-Encoding"):
        raise LiteratureCatalogError("请求须为有明确长度的 JSON。")
    try:
        size = int(handler.headers.get("Content-Length", ""))
    except ValueError as exc:
        raise LiteratureCatalogError("请求长度无效。") from exc
    if size < 0 or size > MAX_BODY:
        raise LiteratureCatalogError("请求超过2 MiB。", "TOO_LARGE", 413)
    try:
        data = json.loads(handler.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError) as exc:
        raise LiteratureCatalogError("JSON 请求无效。") from exc


def _keys(payload, allowed, required=()):
    if set(payload) - set(allowed) or set(required) - set(payload):
        raise LiteratureCatalogError("请求字段缺失或含不允许的字段。")


def _send(handler, status, value, content_type="application/json; charset=utf-8"):
    body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else (value.encode() if isinstance(value, str) else value)
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def dispatch_literature_http(handler, home):
    """Return False for non-literature routes, including existing read/compare/page routes."""
    parsed = urlsplit(handler.path)
    path = parsed.path
    asset = re.fullmatch(r"/static/(literature-catalog\.(?:js|css))", path)
    page = re.fullmatch(r"/project/([^/]+)/literature(?:/(catalog|migrate)|/item/([a-f0-9]{32})(/edit)?)?", path)
    api = re.fullmatch(r"/api/project/([^/]+)/literature/(add|item/[a-f0-9]{32}/(?:update|delete)|collection(?:/retry)?|migrate/(?:apply|rollback))", path)
    if not (asset or page or api):
        return False
    try:
        if asset and handler.command == "GET":
            _send(handler, 200, (Path(__file__).parent / "static" / asset[1]).read_bytes(), "text/javascript; charset=utf-8" if asset[1].endswith("js") else "text/css; charset=utf-8")
        elif page and handler.command == "GET":
            project_id = unquote(page[1])
            if page[4]:
                handler.send_response(303)
                handler.send_header("Location", purl(project_id) + "/literature/item/" + page[3] + "?edit=1")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
            else:
                body = literature_detail_page(home, project_id, page[3]) if page[3] else (literature_migration_preview_page(home, project_id) if page[2] == "migrate" else literature_catalog_list_page(home, project_id, parse_qs(parsed.query)))
                _send(handler, 200, body, "text/html; charset=utf-8")
        elif api:
            project_id, action = unquote(api[1]), api[2]
            if handler.command == "GET" and action == "collection":
                from literature_collection import collection_status
                result = collection_status(project_id, home=home)
            elif handler.command == "POST" and action != "collection":
                payload = _payload(handler)  # exactly once, including migration apply
                project = project_for(project_id, home)
                catalog = LiteratureCatalog(project["wiki_root"])
                if action == "add":
                    _keys(payload, {"locator", "title", "request_id"}, {"locator", "request_id"})
                    result = literature_collect(project_id, home=home, **payload)
                elif action.endswith("/update"):
                    _keys(payload, {"title", "locator", "authors", "year", "expected_item_revision"}, {"title", "locator", "authors", "year", "expected_item_revision"})
                    revision = payload.pop("expected_item_revision")
                    result = catalog.update_item(action.split("/")[1], payload, expected_item_revision=revision)
                    result.pop("item", None)
                elif action.endswith("/delete"):
                    _keys(payload, {"expected_item_revision"}, {"expected_item_revision"})
                    result = catalog.remove_item(action.split("/")[1], **payload)
                    result["url"] = purl(project_id) + "/literature"
                elif action == "collection/retry":
                    _keys(payload, set())
                    from literature_collection import collection_retry
                    result = collection_retry(project_id, home=home)
                elif action == "migrate/apply":
                    _keys(payload, {"selected_paths"}, {"selected_paths"})
                    result = apply_migration(catalog, Path(project["source_root"]), payload["selected_paths"])
                else:
                    _keys(payload, {"migration_id"}, {"migration_id"})
                    result = rollback_migration(catalog, payload["migration_id"])
            else:
                raise LiteratureCatalogError("不支持此请求方法。", "METHOD_NOT_ALLOWED", 405)
            _send(handler, 200, result)
        else:
            raise LiteratureCatalogError("不支持此请求方法。", "METHOD_NOT_ALLOWED", 405)
    except LiteratureCatalogError as exc:
        _send(handler, exc.status, {"ok": False, "error": {"code": exc.code, "message": str(exc)}})
    except Exception:
        _send(handler, 500, {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "文献操作失败，原输入保留；请稍后重试。"}})
    return True
