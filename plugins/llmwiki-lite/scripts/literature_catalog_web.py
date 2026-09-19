"""
Literature catalog web UI and API routes.

Implements T-04: literature list, detail, add form, migration preview/apply/rollback.
Integrates with literature_catalog.py for catalog operations.
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from llmwiki_core import LLMWikiError
from llmwiki_registry import get_project
from research_web_ui import esc, layout, page_header, purl, ui_icon
from literature_catalog import (
    LiteratureCatalog,
    LiteratureCatalogError,
    RevisionConflictError,
)


# Migration helpers

def _classify_migration_file(filename: str, size_bytes: int) -> dict[str, Any]:
    """
    Classify a file for migration.

    Returns:
        {
            "category": "candidate" | "excluded" | "needs_confirmation",
            "reason": str
        }
    """
    name_lower = filename.lower()
    stem_lower = Path(filename).stem.lower()

    # Exclude icons, dependencies, caches
    if any(x in name_lower for x in ["matplotlib", "icon", "logo", ".git", "cache", "venv", "node_modules"]):
        return {"category": "excluded", "reason": "图标或依赖文件"}

    # Exclude experiment reports
    if any(x in stem_lower for x in ["experiment", "qa_render", "修改稿", "report"]):
        return {"category": "excluded", "reason": "实验报告或其他文档"}

    # Exclude dependencies HTML
    if filename.lower().endswith(".html") and any(x in stem_lower for x in ["requirement", "depend", "package"]):
        return {"category": "excluded", "reason": "依赖文件"}

    # Valid PDF candidate (large enough)
    if filename.lower().endswith(".pdf") and size_bytes > 100_000:
        return {"category": "candidate", "reason": ""}

    # HTML needs confirmation
    if filename.lower().endswith((".html", ".htm")):
        return {"category": "needs_confirmation", "reason": "HTML 文件需确认是否为学术论文"}

    # Small files need confirmation
    if size_bytes < 100_000:
        return {"category": "needs_confirmation", "reason": "文件大小异常"}

    # Other formats need confirmation
    return {"category": "needs_confirmation", "reason": "文件格式需确认"}


def _scan_migration_candidates(source_root: Path) -> dict[str, Any]:
    """
    Scan references/papers/ for migration candidates.

    Returns:
        {
            "candidates": [...],
            "excluded": [...],
            "needs_confirmation": [...]
        }
    """
    papers_dir = source_root / "references" / "papers"
    if not papers_dir.exists():
        return {"candidates": [], "excluded": [], "needs_confirmation": []}

    candidates = []
    excluded = []
    needs_confirmation = []

    for path in papers_dir.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in {".pdf", ".html", ".htm", ".epub", ".docx"}:
            continue

        size = path.stat().st_size
        rel_path = str(path.relative_to(source_root))

        classification = _classify_migration_file(path.name, size)

        item = {
            "path": rel_path,
            "size": size,
            "title": path.stem.replace("_", " ").replace("-", " ")
        }

        if classification["category"] == "candidate":
            candidates.append(item)
        elif classification["category"] == "excluded":
            item["reason"] = classification["reason"]
            excluded.append(item)
        else:  # needs_confirmation
            item["reason"] = classification["reason"]
            needs_confirmation.append(item)

    return {
        "candidates": candidates,
        "excluded": excluded,
        "needs_confirmation": needs_confirmation
    }


def apply_migration(
    catalog: LiteratureCatalog,
    source_root: Path,
    selected_paths: list[str],
    collection_source: str = "migration"
) -> dict[str, Any]:
    """
    Apply migration: import selected files into catalog.

    Returns:
        {
            "ok": bool,
            "imported": [...],
            "skipped": [...],
            "errors": [...],
            "migration_id": str
        }
    """
    imported = []
    skipped = []
    errors = []

    migration_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    for rel_path in selected_paths:
        try:
            file_path = source_root / rel_path.replace("/", "\\")
            if not file_path.exists():
                errors.append({"path": rel_path, "error": "文件不存在"})
                continue

            # Extract title from filename
            title = file_path.stem.replace("_", " ").replace("-", " ")

            # Create catalog entry
            result = catalog.create_item(
                title=title,
                collection_source=collection_source,
                attachments=[{
                    "path": rel_path,
                    "filename": file_path.name,
                    "size_bytes": file_path.stat().st_size,
                    "mime_type": "application/pdf" if file_path.suffix.lower() == ".pdf" else "application/octet-stream"
                }]
            )

            if result.get("warnings"):
                # Already exists or other warning
                skipped.append({"path": rel_path, "reason": str(result["warnings"])})
            else:
                imported.append({"path": rel_path, "item_id": result["item"]["id"]})

        except Exception as e:
            errors.append({"path": rel_path, "error": str(e)})

    # Save migration manifest
    manifest = {
        "migration_id": migration_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "imported": imported,
        "skipped": skipped,
        "errors": errors
    }

    migrations_dir = catalog.wiki_root / ".literature" / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = migrations_dir / f"{migration_id}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": len(errors) == 0,
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "migration_id": migration_id
    }


def rollback_migration(catalog: LiteratureCatalog, migration_id: str) -> dict[str, Any]:
    """
    Rollback migration: remove imported items from catalog.

    Does NOT delete original files (per AT-17).

    Returns:
        {
            "ok": bool,
            "removed": [...],
            "errors": [...]
        }
    """
    migrations_dir = catalog.wiki_root / ".literature" / "migrations"
    manifest_path = migrations_dir / f"{migration_id}.json"

    if not manifest_path.exists():
        return {"ok": False, "error": "迁移记录不存在"}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    imported = manifest.get("imported", [])

    removed = []
    errors = []

    for item_info in imported:
        item_id = item_info["item_id"]
        try:
            result = catalog.remove_item(item_id)
            if result["ok"]:
                removed.append(item_id)
            else:
                errors.append({"item_id": item_id, "error": "移除失败"})
        except Exception as e:
            errors.append({"item_id": item_id, "error": str(e)})

    return {
        "ok": len(errors) == 0,
        "removed": removed,
        "errors": errors
    }


def literature_catalog_list_page(home: str, project_id: str, params: dict[str, list[str]]) -> str:
    """
    Render literature catalog list page.

    Shows:
    - Search and filter controls
    - Literature entries with metadata
    - Add literature button
    - Migration preview/apply if old data exists
    """
    project = get_project(project_id, home=home)["project"]
    wiki_root = Path(str(project["wiki_root"]))

    try:
        catalog = LiteratureCatalog(wiki_root)
        result = catalog.list_items()

        items = result["items"]
        revision = result["revision"]

        # Check for old literature data to migrate
        old_papers_dir = Path(str(project["source_root"])) / "references" / "papers"
        has_migration_candidates = old_papers_dir.exists() and any(old_papers_dir.glob("*.pdf"))

    except LiteratureCatalogError as e:
        return layout(
            "文献目录",
            f'<section class="panel error">文献目录加载失败: {esc(str(e))}</section>',
            project_id=project_id,
            active="literature",
            home=home
        )

    # Filter by status
    status_filter = (params.get("status") or ["all"])[0]
    if status_filter == "read":
        items = [item for item in items if item.get("reading_status") == "read"]
    elif status_filter == "unread":
        items = [item for item in items if item.get("reading_status") == "unread"]
    elif status_filter == "reading":
        items = [item for item in items if item.get("reading_status") == "reading"]

    # Filter by identity_status
    identity_filter = (params.get("identity") or ["all"])[0]
    if identity_filter == "verified":
        items = [item for item in items if item.get("identity_status") == "verified"]
    elif identity_filter == "unverified":
        items = [item for item in items if item.get("identity_status") == "unverified"]

    # Render cards
    cards: list[str] = []
    for item in items:
        item_id = item["id"]
        title = item["title"]
        authors = item.get("authors", [])
        year = item.get("year")
        venue = item.get("venue", "")
        pub_type = item.get("publication_type", "")

        reading_status = item.get("reading_status", "unread")
        identity_status = item.get("identity_status", "unverified")
        archived = item.get("archived", False)

        # Status labels
        reading_label = {"unread": "待读", "reading": "在读", "read": "已读"}.get(reading_status, "待读")
        reading_class = {"unread": "pending", "reading": "in-progress", "read": ""}.get(reading_status, "pending")

        identity_label = {"verified": "已核实", "unverified": "待核实"}.get(identity_status, "待核实")
        identity_class = {"verified": "", "unverified": "pending"}.get(identity_status, "pending")

        # Authors display
        authors_text = ", ".join(authors[:3])
        if len(authors) > 3:
            authors_text += f" 等 {len(authors)} 人"

        # Year and venue
        meta_parts = []
        if year:
            meta_parts.append(str(year))
        if venue:
            meta_parts.append(venue)
        meta_text = " · ".join(meta_parts) if meta_parts else ""

        # Identifiers
        identifiers = item.get("identifiers", {})
        doi = identifiers.get("doi", "")
        arxiv = identifiers.get("arxiv", "")

        identifier_badges = ""
        if doi:
            identifier_badges += f'<span class="badge">DOI</span>'
        if arxiv:
            identifier_badges += f'<span class="badge">arXiv</span>'

        # Attachments
        attachments = item.get("attachments", [])
        attachment_badge = ""
        if attachments:
            attachment_badge = f'<span class="badge">附件 {len(attachments)}</span>'

        # URLs
        urls = item.get("urls", [])
        url_links = ""
        if urls:
            url_links = '<div class="lit-urls">'
            for url_item in urls[:3]:
                url_kind = url_item.get("kind", "other")
                url_href = url_item.get("url", "")
                url_label = {"publisher": "出版商", "preprint": "预印本", "repository": "仓库", "other": "链接"}.get(url_kind, "链接")
                url_links += f'<a href="{esc(url_href)}" target="_blank" rel="noopener noreferrer" class="lit-url">{esc(url_label)}</a>'
            url_links += '</div>'

        detail_url = f"{purl(project_id)}/literature/item/{item_id}"
        search_value = esc((title + " " + authors_text).casefold())

        cards.append(f'''<article class="lit-card" data-lit-card data-lit-status="{reading_status}" data-lit-identity="{identity_status}" data-page-title="{search_value}">
<div class="lit-row-heading">
    <h3><a href="{detail_url}">{esc(title)}</a></h3>
    <span class="reading-state {reading_class}">{reading_label}</span>
</div>
<div class="lit-row-meta">
    <span class="meta">{esc(authors_text)}</span>
    {f'<span class="meta">{esc(meta_text)}</span>' if meta_text else ''}
    <span class="identity-state {identity_class}">{identity_label}</span>
</div>
<div class="lit-row-badges">
    {identifier_badges}
    {attachment_badge}
    <span class="badge">{esc(pub_type)}</span>
</div>
{url_links}
</article>''')

    cards_html = "".join(cards) if cards else '<div class="empty">文献目录为空。点击"添加文献"开始收录。</div>'

    # Filter controls
    filter_bar = f'''<div class="filter-bar">
<input class="library-search" type="search" aria-label="搜索文献" oninput="filterLiterature(this)" placeholder="搜索文献标题或作者">
<select aria-label="阅读状态" onchange="setLiteratureFilter('status',this.value)">
    <option value="all" {'selected' if status_filter == 'all' else ''}>全部状态</option>
    <option value="unread" {'selected' if status_filter == 'unread' else ''}>待读</option>
    <option value="reading" {'selected' if status_filter == 'reading' else ''}>在读</option>
    <option value="read" {'selected' if status_filter == 'read' else ''}>已读</option>
</select>
<select aria-label="核实状态" onchange="setLiteratureFilter('identity',this.value)">
    <option value="all" {'selected' if identity_filter == 'all' else ''}>全部</option>
    <option value="verified" {'selected' if identity_filter == 'verified' else ''}>已核实</option>
    <option value="unverified" {'selected' if identity_filter == 'unverified' else ''}>待核实</option>
</select>
<span class="meta"><span id="lit-result-count">{len(items)}</span> 篇</span>
</div>'''

    # Migration section
    migration_section = ""
    if has_migration_candidates:
        migration_url = f"{purl(project_id)}/literature/migrate"
        migration_section = f'''<section class="panel notice">
<h3>发现旧文献数据</h3>
<p>检测到项目中可能存在旧格式的文献文件。可以预览并迁移到新的文献目录。</p>
<a class="button primary" href="{migration_url}">预览迁移</a>
</section>'''

    body = page_header("文献目录", '<a class="button primary" href="#add-literature">添加文献</a>') + f'''
<section class="literature-content">
{migration_section}
{filter_bar}
<div class="literature-grid" id="literature-list">
{cards_html}
</div>
<div class="empty literature-filter-empty" id="literature-filter-empty" style="display:none">没有匹配的文献</div>
</section>

<details class="subtle-details" id="add-literature" open>
<summary>添加文献</summary>
<form class="add-lit-form" method="POST" action="{purl(project_id)}/api/literature/add">
<p>粘贴 DOI、arXiv ID、URL 或论文标题：</p>
<input type="text" name="input" placeholder="10.1234/example 或 https://arxiv.org/abs/1234.56789 或论文标题" required>
<div class="form-row">
<button type="submit" class="primary">添加</button>
<button type="button" onclick="this.closest('details').removeAttribute('open')">取消</button>
</div>
<input type="hidden" name="revision" value="{esc(revision)}">
</form>
</details>

<script>
function filterLiterature(input) {{
    const query = input.value.toLowerCase();
    const cards = document.querySelectorAll('[data-lit-card]');
    let count = 0;
    cards.forEach(card => {{
        const title = card.getAttribute('data-page-title') || '';
        if (title.includes(query)) {{
            card.style.display = '';
            count++;
        }} else {{
            card.style.display = 'none';
        }}
    }});
    document.getElementById('lit-result-count').textContent = count;
    document.getElementById('literature-filter-empty').style.display = count === 0 ? '' : 'none';
}}

function setLiteratureFilter(type, value) {{
    const url = new URL(window.location);
    url.searchParams.set(type, value);
    window.location = url.toString();
}}
</script>
'''

    return layout("文献目录", body, project_id=project_id, active="literature", home=home)


def literature_detail_page(home: str, project_id: str, item_id: str) -> str:
    """Render literature item detail page."""
    project = get_project(project_id, home=home)["project"]
    wiki_root = Path(str(project["wiki_root"]))

    try:
        catalog = LiteratureCatalog(wiki_root)
        item = catalog.get_item(item_id)

        if not item:
            return layout(
                "文献详情",
                '<section class="panel error">文献条目不存在</section>',
                project_id=project_id,
                active="literature",
                home=home
            )

    except LiteratureCatalogError as e:
        return layout(
            "文献详情",
            f'<section class="panel error">加载失败: {esc(str(e))}</section>',
            project_id=project_id,
            active="literature",
            home=home
        )

    # Extract fields
    title = item["title"]
    authors = item.get("authors", [])
    year = item.get("year")
    venue = item.get("venue", "")
    pub_type = item.get("publication_type", "")
    identifiers = item.get("identifiers", {})
    urls = item.get("urls", [])
    attachments = item.get("attachments", [])
    tags = item.get("tags", [])
    reading_status = item.get("reading_status", "unread")
    identity_status = item.get("identity_status", "unverified")
    collection_source = item.get("collection_source", "manual")
    source_refs = item.get("source_refs", [])
    reading_note_paths = item.get("reading_note_paths", [])
    created_at = item.get("created_at", "")
    updated_at = item.get("updated_at", "")

    # Authors section
    authors_html = ""
    if authors:
        authors_list = "".join(f"<li>{esc(author)}</li>" for author in authors)
        authors_html = f"<div><strong>作者:</strong><ul class='author-list'>{authors_list}</ul></div>"

    # Metadata section
    metadata_html = f"""<div><strong>年份:</strong> {year if year else '未知'}</div>
<div><strong>发表于:</strong> {esc(venue) if venue else '未知'}</div>
<div><strong>类型:</strong> {esc(pub_type)}</div>
<div><strong>身份状态:</strong> {identity_status}</div>
<div><strong>阅读状态:</strong> {reading_status}</div>
<div><strong>收录来源:</strong> {collection_source}</div>"""

    # Identifiers section
    identifiers_html = ""
    if identifiers:
        id_items = []
        if identifiers.get("doi"):
            doi_url = f"https://doi.org/{identifiers['doi']}"
            id_items.append(f'<li><strong>DOI:</strong> <a href="{esc(doi_url)}" target="_blank" rel="noopener noreferrer">{esc(identifiers["doi"])}</a></li>')
        if identifiers.get("arxiv"):
            arxiv_url = f"https://arxiv.org/abs/{identifiers['arxiv']}"
            id_items.append(f'<li><strong>arXiv:</strong> <a href="{esc(arxiv_url)}" target="_blank" rel="noopener noreferrer">{esc(identifiers["arxiv"])}</a></li>')
        if id_items:
            identifiers_html = f"<div><strong>标识符:</strong><ul>{''.join(id_items)}</ul></div>"

    # URLs section
    urls_html = ""
    if urls:
        url_items = "".join(
            f'<li><strong>{esc(url_item.get("kind", "other"))}:</strong> <a href="{esc(url_item["url"])}" target="_blank" rel="noopener noreferrer">{esc(url_item["url"])}</a></li>'
            for url_item in urls
        )
        urls_html = f"<div><strong>链接:</strong><ul>{url_items}</ul></div>"

    # Attachments section
    attachments_html = ""
    if attachments:
        att_items = "".join(
            f'<li>{esc(att.get("filename", "附件"))} ({att.get("size_bytes", 0)} bytes)</li>'
            for att in attachments
        )
        attachments_html = f"<div><strong>附件:</strong><ul>{att_items}</ul></div>"

    # Tags section
    tags_html = ""
    if tags:
        tag_badges = "".join(f'<span class="badge">{esc(tag)}</span>' for tag in tags)
        tags_html = f"<div><strong>标签:</strong> {tag_badges}</div>"

    # Actions
    edit_url = f"{purl(project_id)}/literature/item/{item_id}/edit"
    delete_url = f"{purl(project_id)}/api/literature/item/{item_id}/delete"

    body = page_header(esc(title), f'<a class="button" href="{purl(project_id)}/literature">返回列表</a>') + f'''
<section class="lit-detail">
<article class="panel">
<h2>{esc(title)}</h2>
{authors_html}
{metadata_html}
{identifiers_html}
{urls_html}
{attachments_html}
{tags_html}
<div class="meta">
<div>创建时间: {created_at}</div>
<div>更新时间: {updated_at}</div>
<div>ID: {item_id}</div>
</div>
<div class="actions">
<a class="button" href="{edit_url}">编辑</a>
<form method="POST" action="{delete_url}" style="display:inline" onsubmit="return confirm('确定要从文献库移除此条目吗？')">
<button type="submit" class="danger">移除</button>
</form>
</div>
</article>
</section>
'''

    return layout(f"{title} - 文献详情", body, project_id=project_id, active="literature", home=home)


def literature_migration_preview_page(home: str, project_id: str) -> str:
    """
    Preview literature migration from old format.

    Scans references/papers/ directory for PDF files and shows classification:
    - Valid papers (can import)
    - Dependencies/icons/reports (exclude)
    - Needs confirmation (HTML, other formats)
    """
    project = get_project(project_id, home=home)["project"]
    source_root = Path(str(project["source_root"]))
    papers_dir = source_root / "references" / "papers"

    if not papers_dir.exists():
        return layout(
            "文献迁移",
            '<section class="panel">未找到旧文献目录</section>',
            project_id=project_id,
            active="literature",
            home=home
        )

    # Scan for files
    candidates = []
    excluded = []
    needs_confirmation = []

    for path in papers_dir.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in {".pdf", ".html", ".htm", ".epub", ".docx"}:
            continue

        name_lower = path.name.lower()
        stem_lower = path.stem.lower()

        # Classify
        if any(x in name_lower for x in ["matplotlib", "icon", "logo", ".git", "cache", "venv"]):
            excluded.append({"path": str(path.relative_to(source_root)), "reason": "图标或依赖文件"})
        elif any(x in stem_lower for x in ["experiment", "report", "qa_render", "修改稿"]):
            excluded.append({"path": str(path.relative_to(source_root)), "reason": "实验报告或其他文档"})
        elif path.suffix.lower() == ".pdf" and path.stat().st_size > 100_000:  # > 100KB
            candidates.append({
                "path": str(path.relative_to(source_root)),
                "size": path.stat().st_size,
                "title": path.stem.replace("_", " ").replace("-", " ")
            })
        elif path.suffix.lower() in {".html", ".htm"}:
            needs_confirmation.append({
                "path": str(path.relative_to(source_root)),
                "size": path.stat().st_size,
                "reason": "HTML 文件需确认是否为学术论文"
            })
        else:
            needs_confirmation.append({
                "path": str(path.relative_to(source_root)),
                "size": path.stat().st_size,
                "reason": "文件格式或大小异常"
            })

    # Render candidates
    candidates_html = ""
    if candidates:
        candidate_rows = "".join(
            f'''<tr>
<td><input type="checkbox" name="import_path" value="{esc(c['path'])}" checked></td>
<td>{esc(c["title"])}</td>
<td>{esc(c["path"])}</td>
<td>{c["size"] // 1024} KB</td>
</tr>'''
            for c in candidates
        )
        candidates_html = f'''<section class="panel">
<h3>可导入候选 ({len(candidates)} 篇)</h3>
<table class="migration-table">
<thead>
<tr><th>导入</th><th>标题</th><th>路径</th><th>大小</th></tr>
</thead>
<tbody>
{candidate_rows}
</tbody>
</table>
</section>'''

    # Render needs confirmation
    confirmation_html = ""
    if needs_confirmation:
        confirm_rows = "".join(
            f'''<tr>
<td><input type="checkbox" name="import_path" value="{esc(c['path'])}"></td>
<td>{esc(c["path"])}</td>
<td>{esc(c["reason"])}</td>
<td>{c["size"] // 1024} KB</td>
</tr>'''
            for c in needs_confirmation
        )
        confirmation_html = f'''<section class="panel notice">
<h3>需要确认 ({len(needs_confirmation)} 个)</h3>
<table class="migration-table">
<thead>
<tr><th>导入</th><th>路径</th><th>原因</th><th>大小</th></tr>
</thead>
<tbody>
{confirm_rows}
</tbody>
</table>
</section>'''

    # Render excluded
    excluded_html = ""
    if excluded:
        excluded_rows = "".join(
            f'<tr><td>{esc(e["path"])}</td><td>{esc(e["reason"])}</td></tr>'
            for e in excluded
        )
        excluded_html = f'''<details class="subtle-details">
<summary>已排除 ({len(excluded)} 个)</summary>
<table class="migration-table">
<thead>
<tr><th>路径</th><th>原因</th></tr>
</thead>
<tbody>
{excluded_rows}
</tbody>
</table>
</details>'''

    apply_url = f"{purl(project_id)}/api/literature/migrate/apply"

    body = page_header("文献迁移预览", f'<a class="button" href="{purl(project_id)}/literature">取消</a>') + f'''
<section class="migration-content">
<p class="notice">以下是从旧文献目录扫描到的文件。请确认需要导入的条目，然后应用迁移。</p>

<form method="POST" action="{apply_url}">
{candidates_html}
{confirmation_html}
{excluded_html}

<div class="actions">
<button type="submit" class="primary">应用迁移</button>
<a class="button" href="{purl(project_id)}/literature">取消</a>
</div>
</form>
</section>
'''

    return layout("文献迁移", body, project_id=project_id, active="literature", home=home)
