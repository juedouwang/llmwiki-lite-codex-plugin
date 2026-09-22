"""Chinese-first research cockpit pages for the loopback LLM Wiki website."""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from llmwiki_core import LLMWikiError, _wiki_page_title, status, wiki_list
from llmwiki_registry import get_project, list_projects, load_settings
from markdown_renderer import render_markdown
from research_records import MAX_LIST_RECORDS, list_records, read_record
import web_session

IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Fixed local SVG geometry: no icon font, third-party CDN, or user-supplied markup.
_ICON_PATHS = {
    "workbench": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 9v12"/>',
    "switch": '<path d="m7 9 5-5 5 5m-10 6 5 5 5-5"/>',
    "merge": '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>',
    "download": '<path d="M12 5v14m-7-7 7 7 7-7"/>',
    "upload": '<path d="M12 19V5m-7 7 7-7 7 7"/>',
    "files": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4"/>',
    "refresh": '<path d="M3 11a9 9 0 0 1 15.4-6.4L21 7M21 3v4h-4M21 13a9 9 0 0 1-15.4 6.4L3 17M7 17H3v4"/>',
    "git": '<path d="M15 6a9 9 0 0 0-9 9V3"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/>',
    "reports": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"/><path d="M14 2v5a1 1 0 0 0 1 1h5"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    "daily": '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/><path d="m9 16 2 2 4-4"/>',
    "reports-stack": '<path d="M15 2h-4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V8"/><path d="M16.706 2.706A2.4 2.4 0 0 0 15 2v5a1 1 0 0 0 1 1h5a2.4 2.4 0 0 0-.706-1.706z"/><path d="M5 7a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h8a2 2 0 0 0 1.732-1"/>',
    "folder": '<path d="M3.5 8V6.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V8Z"/><path d="M3.5 9h17"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 4.5 4.5"/>',
    "notebook": '<path d="M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4"/><path d="M2 6h4"/><path d="M2 10h4"/><path d="M2 14h4"/><path d="M2 18h4"/><path d="M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z"/>',
    "papers": '<path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/>',
    "book": '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    "tasks": '<path d="M6 5h12"/><path d="M4 12h10"/><path d="M12 19h8"/>',
    "settings": '<path d="M4 7h8m4 0h4M4 17h4m4 0h8"/><circle cx="14" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "sidebar": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9 4.5v15"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "chevron": '<path d="m8 10 4 4 4-4"/>',
    "left": '<path d="m14 6-6 6 6 6"/>',
    "right": '<path d="m10 6 6 6-6 6"/>',
    "arrow": '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "open": '<path d="M7 17 17 7M7 7h10v10"/>',
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "trash": '<path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    "moon": '<path d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z"/>',
    "monitor": '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4M8 21h8"/>',
    "resume": '<path d="M3 3v8a4 4 0 0 0 4 4h14m-6-6 6 6-6 6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "star": '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
}


def ui_icon(name: str) -> str:
    """Render one decorative icon; controls retain their own text/ARIA labels."""
    return (
        '<svg class="ui-icon" width="20" height="20" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true" focusable="false">'
        + _ICON_PATHS[name] + '</svg>'
    )


STYLE = (Path(__file__).parent / "static" / "style.css").read_text(encoding="utf-8")

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("研究总览", ("index", "overview", "readme", "project", "研究总览", "项目总览", "概览")),
    ("文献与阅读", ("paper", "papers", "literature", "survey", "文献", "论文", "阅读", "综述")),
    ("方法与实现", ("method", "methods", "algorithm", "implementation", "architecture", "方法", "算法", "实现", "模型")),
    ("数据与样本", ("dataset", "data", "sample", "数据集", "数据", "样本", "标注")),
    ("实验记录", ("experiment", "experiments", "ablation", "run", "实验", "消融", "训练记录", "运行记录")),
    ("结果与分析", ("result", "results", "metric", "evaluation", "analysis", "结果", "指标", "评估", "分析")),
    ("结论与问题", ("claim", "conclusion", "question", "finding", "结论", "问题", "发现", "假设")),
    ("计划与待办", ("plan", "roadmap", "todo", "backlog", "goal", "计划", "路线图", "待办", "目标")),
    ("论文与成果", ("thesis", "patent", "publication", "defense", "manuscript", "学位论文", "专利", "投稿", "答辩", "成果")),
    ("决策记录", ("decision", "decisions", "meeting", "决策", "会议", "讨论记录")),
    ("资料索引", ("source", "sources", "reference", "bibliography", "资料", "来源", "参考文献", "索引")),
    ("研究笔记", ("note", "notes", "journal", "diary", "笔记", "日志", "日记")),
)
CATEGORY_ORDER = [name for name, _ in CATEGORY_RULES] + ["其他页面"]


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def safe_path(root: Path, relative: str) -> Path:
    target = (root / relative.replace("/", str(Path("/")))).resolve(strict=False)
    if not within(target, root):
        raise LLMWikiError("请求的文件不在 Wiki 目录中。")
    return target


def purl(project_id: str) -> str:
    return f"/project/{quote(project_id, safe='')}"


def pageurl(project_id: str, page: str) -> str:
    return f"{purl(project_id)}/page/{quote(page, safe='/')}"


def recordurl(project_id: str, record_id: str) -> str:
    normalized = record_id.replace(chr(92), "/").lstrip("/")
    prefix = "records/"
    if normalized.lower().startswith(prefix):
        normalized = normalized[len(prefix) :]
    return f"{purl(project_id)}/records/{quote(normalized, safe='/')}"


def source_asset_url(project_id: str, relative: str) -> str:
    normalized = relative.replace(chr(92), "/").lstrip("/")
    return f"{purl(project_id)}/source-asset/{quote(normalized, safe='/')}"





LIGHTBOX_HTML = (
    '<div class="lightbox" id="lightbox" hidden role="dialog" aria-modal="true" aria-label="图片预览">'
    f'<button class="lightbox-close" type="button" aria-label="关闭预览">{ui_icon("close")}</button>'
    f'<button class="lightbox-nav lightbox-prev" type="button" aria-label="上一张">{ui_icon("left")}</button>'
    f'<button class="lightbox-nav lightbox-next" type="button" aria-label="下一张">{ui_icon("right")}</button>'
    '<div class="lightbox-stage">'
    '<img class="lightbox-image" id="lightbox-image" src="" alt="" draggable="false">'
    '<div class="lightbox-caption" id="lightbox-caption"></div>'
    '</div></div>'
)

def _layout_context(
    home: str | None, project_id: str | None
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    try:
        listed = list_projects(home)
        projects = list(listed.get("projects") or [])
        current_id = project_id if project_id is not None else (web_session.current_project() if web_session.current_project() is not None else listed.get("landing_project_id"))
    except (LLMWikiError, OSError, ValueError):
        return [], project_id, None
    project: dict[str, Any] | None = None
    if current_id:
        project = next(
            (item for item in projects if str(item.get("id")) == str(current_id)), None
        )
        if project is None:
            try:
                project = get_project(str(current_id), home=home)["project"]
            except (LLMWikiError, OSError, ValueError):
                project = None
    return projects, str(current_id) if current_id else None, project


def _console_nav_item(
    href: str,
    label: str,
    icon: str,
    key: str,
    active: str,
) -> str:
    active_class = " is-active" if key == active else ""
    current = ' aria-current="page"' if key == active else ""
    return (
        f'<a class="console-nav-item{active_class}" href="{href}" data-workbench-nav="{esc(key)}" aria-label="{esc(label)}"{current}>'
        f'<span class="console-nav-icon" aria-hidden="true">{ui_icon(icon)}</span>'
        f'<span>{esc(label)}</span></a>'
    )


def _console_breadcrumbs(title: str, project: dict[str, Any] | None, active: str) -> str:
    labels = {"home": "项目", "daily": "每日待办", "overview": "知识库", "records": "科研记录", "todos": "科研进度", "search": "搜索", "settings": "设置", "literature": "文献", "pages": title, "reports": "日报与周报", "code": "代码"}
    label = labels.get(active, title)
    prefix = f'<a href="{purl(str(project["id"]))}">{esc(project["name"])}</a><span aria-hidden="true">/</span>' if project and active in {"overview", "literature", "records", "pages", "todos", "code"} else ""
    if active in {"daily", "reports"}:
        prefix = '<span>我的工作</span><span aria-hidden="true">/</span>'
    return f'<nav class="console-breadcrumbs" aria-label="当前位置">{prefix}<span>{esc(label)}</span></nav>'


def layout(title: str, body: str, query: str = "", project_id: str | None = None, active: str = "", home: str | None = None, report_query: dict[str, str] | None = None, daily_date: str | None = None) -> str:
    projects, current_id, project = _layout_context(home, project_id)
    context_query = "?" + urlencode({"context": current_id or ""})
    # Switching project keeps the section, never a different project's document id.
    suffix = {"records": "/records", "literature": "/literature", "code": "/code",
              "todos": "/todos", "overview": "", "pages": ""}.get(active, "/todos")
    def project_href(item: dict[str, Any]) -> str:
        if active == "daily":
            return "/daily?" + urlencode({"date": daily_date or datetime.now().date().isoformat(), "context": str(item["id"])})
        if active == "reports":
            return "/reports?" + urlencode({**(report_query or {}), "context": str(item["id"])})
        return purl(str(item["id"])) + suffix

    project_menu = "".join(
        f'<a data-project-id="{esc(item["id"])}" href="{esc(project_href(item))}" class="{"is-current" if str(item["id"]) == current_id else ""}">{esc(item["name"])}</a>'
        for item in projects
    ) or '<span class="muted">暂无项目</span>'
    project_picker = (
        '<details class="console-project-switcher"><summary aria-label="切换研究项目">'
        f'<span class="console-project-label"><small>当前项目</small><b title="{esc(project["name"]) if project else "选择项目"}">{esc(project["name"]) if project else "选择项目"}</b></span><span class="switcher-chevron">{ui_icon("switch")}</span></summary>'
        f'<div class="console-project-menu">{project_menu}</div></details>'
    )
    work_section = (
        '<section class="console-my-work" aria-label="我的工作"><h2 class="console-group-label">我的工作</h2>'
        + _console_nav_item("/daily" + context_query, "每日待办", "daily", "daily", active)
        + _console_nav_item("/reports" + context_query, "日报与周报", "reports-stack", "reports", active)
        + '</section>'
    )
    project_section = ""
    if project:
        pid = str(project["id"])
        project_section = (
            '<section class="console-project-section" aria-label="项目"><h2 class="console-group-label">项目</h2>' + project_picker
            + _console_nav_item(f"{purl(pid)}/todos", "科研进度", "tasks", "todos", active)
            + _console_nav_item(f"{purl(pid)}/records", "科研记录", "notebook", "records", active)
            + _console_nav_item(purl(pid), "知识库", "book", "overview", "overview" if active == "pages" else active)
            + _console_nav_item(f"{purl(pid)}/literature", "文献", "papers", "literature", active)
            + _console_nav_item(f"{purl(pid)}/code", "代码", "git", "code", active)
            + '</section>'
        )
    if not project_section:
        project_section = '<section class="console-project-section" aria-label="项目"><h2 class="console-group-label">项目</h2>' + project_picker + '</section>'
    sidebar = (
        '<aside class="console-sidebar" id="console-sidebar" aria-label="侧栏">'
        f'<div class="console-sidebar-heading"><a class="console-sidebar-brand" href="/projects{esc(context_query)}" aria-label="野人工作台：项目总览">'
        + ui_icon("workbench") + '<span>野人工作台</span></a>'
        f'<button class="sidebar-close" aria-label="关闭导航菜单" onclick="toggleConsoleSidebar(false)">{ui_icon("close")}</button></div>'
        '<nav class="console-navigation" aria-label="主导航">'
        + work_section + project_section
        + '</nav><div class="console-sidebar-footer">'
        + '<span class="console-avatar" aria-hidden="true">野</span><span class="console-profile-label">本地</span>'
        + '<details class="theme-menu"><summary class="rw-icon-button" aria-label="外观" title="外观">' + ui_icon("monitor") + '</summary><div class="theme-options" aria-label="外观模式">' + theme_choices() + '</div></details>'
        + _console_nav_item("/settings" + context_query, "设置", "settings", "settings", active) + '</div></aside>'
    )
    return (
        '<!doctype html><html lang="zh-CN" data-workbench-scale="1.25"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" type="image/x-icon" href="/static/workbench.ico?v=1">'
        f'<title>{esc(title)} · 野人工作台</title><script src="/static/theme.js"></script><link rel="stylesheet" href="/static/style.css"></head>'
        f'<body data-workbench-page="{esc(active)}" data-workbench-project="{esc(current_id or "")}" data-workbench-session="{esc(web_session.session_id())}"><a class="skip-link" href="#main-content">跳到内容</a><div class="console-shell">' + sidebar
        + '<button class="console-overlay" id="console-overlay" aria-label="关闭导航"></button>'
        '<div class="console-main">'
        f'<header class="console-topbar"><button class="console-menu-button" type="button" aria-label="打开导航菜单" aria-expanded="false" aria-controls="console-sidebar" onclick="toggleConsoleSidebar()">{ui_icon("sidebar")}</button>'
        + _console_breadcrumbs(title, project, active)
        + f'<a class="console-search" href="/search{esc(context_query)}" aria-label="搜索">' + ui_icon('search') + '</a></header>'
        + f'<div class="console-content"><main id="main-content" tabindex="-1">{body}</main></div>'
        + '<footer class="workbench-footer"><span>从上次停下的地方继续。</span><span>本地工作台 · 内容保存在你的设备</span></footer></div></div>'
        + LIGHTBOX_HTML + '<script src="/static/app.js" defer></script>'
        + '<script src="/static/workbench-navigation.js" defer></script></body></html>'
    )


def page_header(title: str, actions: str = "") -> str:
    return f'<header class="page-header"><h1>{esc(title)}</h1><div class="actions">{actions}</div></header>'



def new_button(label: str, *, href: str = "", element_id: str = "", attributes: str = "") -> str:
    """The same prototype-derived primary action on every collection page."""
    attrs = f' class="button primary rw-button rw-primary" {attributes}'
    if element_id:
        attrs += f' id="{esc(element_id)}"'
    content = ui_icon("plus") + f'<span>{esc(label)}</span>'
    if href:
        return f'<a{attrs} href="{esc(href)}">{content}</a>'
    return f'<button type="button"{attrs}>{content}</button>'


def theme_choices() -> str:
    return ''.join(f'<button type="button" data-theme-choice="{value}" aria-pressed="false">{ui_icon(icon)}<span>{label}</span></button>'
                   for value, label, icon in [("light", "浅色", "sun"), ("dark", "深色", "moon"), ("system", "跟随系统", "monitor")])


def notice(params: dict[str, list[str]]) -> str:
    if params.get("message"):
        return f'<div class="notice">{esc(params["message"][0])}</div>'
    if params.get("error"):
        return f'<div class="notice error">{esc(params["error"][0])}</div>'
    return ""


def project_status(project: dict[str, Any]) -> dict[str, Any]:
    try:
        return status(str(project["source_root"]), state_root=str(project["state_root"]))
    except (LLMWikiError, OSError, ValueError) as exc:
        return {"wiki_page_count": 0, "snapshot_file_count": 0, "snapshot_at": None, "dirty_paths": [], "error": str(exc)}


def format_time(value: str | None) -> str:
    if not value:
        return "尚未扫描"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def classify_page(path: str, title: str) -> str:
    normalized = f"{path} {title}".replace("\\", "/").lower()
    parts = re.split(r"[/_.\-\s]+", normalized)
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            key = keyword.lower()
            if key in parts or key in normalized:
                return category
    return "其他页面"


def page_records(project: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wiki_root = Path(str(project["wiki_root"]))
    records: list[dict[str, Any]] = []
    for page in pages:
        item = dict(page)
        item["category"] = classify_page(str(page["path"]), str(page["title"]))
        target = safe_path(wiki_root, str(page["path"]))
        try:
            item["mtime"] = target.stat().st_mtime
            item["updated"] = datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d %H:%M")
        except OSError:
            item["mtime"] = 0.0
            item["updated"] = "未知"
        records.append(item)
    return records


def grouped(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {name: [] for name in CATEGORY_ORDER}
    for page in records:
        result[str(page["category"])].append(page)
    for pages in result.values():
        pages.sort(key=lambda item: (str(item["title"]).lower(), str(item["path"])))
    return result


def page_link(project_id: str, page: dict[str, Any], *, show_path: bool = False) -> str:
    path = str(page["path"])
    details = f'<div class="meta path">{esc(path)}</div>' if show_path else ""
    return f'<li data-page-title="{esc((str(page["title"]) + " " + path).lower())}"><a href="{pageurl(project_id, path)}">{esc(page["title"])}</a>{details}</li>'

def project_management_controls() -> str:
    return '''<div id="project-context-menu" class="project-context-menu" role="menu" aria-label="项目操作" hidden>
<button type="button" role="menuitem" data-project-action="rename">重命名</button>
<button type="button" role="menuitem" data-project-action="remove" class="danger">从列表移除</button></div>
<dialog id="project-name-dialog" aria-labelledby="project-name-heading"><form id="project-name-form">
<h2 id="project-name-heading">重命名项目</h2><label>项目名称<input id="project-new-name" maxlength="120" required autocomplete="off"></label>
<p class="meta">只修改显示名称，不移动目录或修改项目文件。</p><p class="project-dialog-status" role="alert"></p>
<div class="actions"><button type="button" data-project-cancel>取消</button><button type="submit" class="primary">确定</button></div></form></dialog>
<dialog id="project-remove-dialog" aria-labelledby="project-remove-heading"><h2 id="project-remove-heading">从列表移除项目？</h2>
<p id="project-remove-name"></p><p class="meta">只取消注册，代码、知识库、科研记录和附件全部保留。以后可重新添加原目录。</p>
<p class="project-dialog-status" role="alert"></p><div class="actions"><button type="button" data-project-cancel>取消</button>
<button type="button" id="project-remove-confirm" class="danger">确认移除</button></div></dialog>'''


def home_page(home: str, params: dict[str, list[str]]) -> str:
    from research_progress import active_tasks

    listed = list_projects(home)
    projects = listed["projects"]
    rows = []
    for project in projects:
        state = project_status(project)
        count = int(state.get("wiki_page_count", 0))
        pid = str(project["id"])
        try:
            current = active_tasks(project, limit=1)
        except (LLMWikiError, OSError, ValueError):
            current = []
        task_html = (
            f'<a class="project-row-task" href="{purl(pid)}/todos#task-{esc(current[0]["id"])}">{esc(current[0]["title"])}</a>'
            if current else ""
        )
        rows.append(
            f'<div class="project-row" data-project-id="{esc(pid)}" data-project-name="{esc(project["name"])}"><div class="project-row-heading">'
            f'<button type="button" class="project-drag-handle row-icon" aria-label="拖动排序：{esc(project["name"])}" title="拖动排序；聚焦后按上下方向键" aria-describedby="project-order-hint">{ui_icon("folder")}</button>'
            f'<a class="project-row-main" href="{purl(pid)}/todos"><span class="row-title">{esc(project["name"])}</span>'
            f'<span class="meta">{count} 篇知识页</span><span class="row-arrow">{ui_icon("arrow")}</span></a>'
            f'<button type="button" class="project-default" aria-pressed="{str(listed["web_default_project_id"] == pid).lower()}" aria-label="设为启动项目：{esc(project["name"])}" title="设为启动项目，再次点击取消">{ui_icon("star")}</button>'
            f'<button type="button" class="project-more rw-icon-button" aria-label="更多操作：{esc(project["name"])}" aria-haspopup="menu" title="更多操作">{ui_icon("more")}</button>'
            f'</div>{task_html}</div>'
        )
    content = '<div class="project-list">' + "".join(rows) + '</div>' if rows else '<div class="empty"><h2>添加你的第一个项目</h2><p>关联本地项目，开始整理文献与研究记录。</p><a class="button primary" href="/settings#register-project">添加项目</a></div>'
    body = notice(params) + page_header("项目", '<a class="button" href="/settings#register-project">＋ 添加项目</a>') + content
    body = (f'<section id="project-manager" data-default-project="{esc(listed["web_default_project_id"] or "")}">' + body
            + '<p class="meta" id="project-order-hint">拖动文件夹调整顺序；星标指定启动项目，未指定时进入第一项。</p>'
            + project_management_controls()
            + '<p id="project-preferences-status" class="meta" role="status" aria-live="polite"></p></section>'
            + '<script src="/static/projects.js" defer></script>')
    return layout("项目", body, active="home", home=home)


def knowledge_records(project: dict[str, Any]) -> list[dict[str, Any]]:
    from knowledge_maintenance import page_target
    pages = wiki_list(str(project["source_root"]), state_root=str(project["state_root"]))["pages"]
    eligible = []
    for page in pages:
        try:
            page_target(project, str(page["path"]))
        except (LLMWikiError, ValueError, OSError):
            continue
        eligible.append(page)
    return sorted(page_records(project, eligible), key=lambda item: (str(item["title"]), str(item["path"])))


def knowledge_header() -> str:
    actions = (
        f'<a class="rw-button" href="/settings#report-settings">{ui_icon("settings")}维护设置</a>'
        f'<button type="button" class="rw-button rw-outline" id="knowledge-updates">{ui_icon("git")}<span>查看更新</span></button>'
    )
    return page_header("知识库", actions)


def knowledge_surface(
    project: dict[str, Any], records: list[dict[str, Any]], selected: str, *, project_updates: bool = False,
) -> str:
    """The prototype's page directory and actual Markdown share one reading surface."""
    from knowledge_maintenance import widget
    project_id = str(project["id"])
    rows = ''.join(f'<li data-page-title="{esc((str(item["title"])+" "+str(item["path"])).casefold())}"><a class="knowledge-row" aria-current="{"page" if item["path"] == selected else "false"}" href="{pageurl(project_id, item["path"])}">{ui_icon("reports")}<span>{esc(item["title"])}</span></a></li>' for item in records)
    directory = (
        '<aside class="rw-page-list"><div class="knowledge-directory-heading"><small>项目理解</small>'
        f'<details class="knowledge-search"><summary class="rw-icon-button" aria-label="搜索知识页">{ui_icon("search")}</summary>'
        '<input class="page-filter" type="search" aria-label="筛选知识页" oninput="filterPages(this)" placeholder="搜索知识页"></details></div>'
        '<ul class="page-list">' + rows + '</ul><p class="filter-empty empty" hidden>没有匹配的知识页</p></aside>'
    )
    target = safe_path(Path(str(project["wiki_root"])), selected)
    text = target.read_text(encoding="utf-8")
    article = '<article class="document">' + render_markdown(text, project_id, selected) + '</article>'
    info = (
        '<details class="subtle-details" id="knowledge-page-info"><summary>页面信息</summary>'
        f'<p class="path">{esc(selected)}</p><p>共 {len(records)} 篇知识页 · 知识讲解不按日期重复堆积。</p>'
        '<button type="button" class="rw-button" onclick="window.print()">打印 / 导出 PDF</button></details>'
    )
    toolbar = '<div class="knowledge-document-toolbar"><span>项目知识与理解</span><button type="button" class="rw-button" id="knowledge-page-details">页面信息</button></div>'
    return '<div class="rw-split knowledge-list">' + directory + '<div class="rw-knowledge-document">' + toolbar + article + info + widget(project_id, '' if project_updates else selected) + '</div></div>'


def project_page(home: str, project_id: str, params: dict[str, list[str]]) -> str:
    project = get_project(project_id, home=home)["project"]
    state = project_status(project)
    records = knowledge_records(project)
    dirty = state.get("dirty_paths") or []
    dirty_html = '<details class="subtle-details"><summary>待核对变化 · ' + str(len(dirty)) + '</summary><ul>' + ''.join(f'<li class="path">{esc(path)}</li>' for path in dirty[:30]) + '</ul></details>' if dirty else ''
    prompt = f'请理解并维护研究项目“{project["name"]}”的 Wiki，检查变化，只更新受影响的页面，保留原有内容并说明依据。'
    help_html = f'''<details class="subtle-details"><summary>如何更新知识库</summary><div class="prompt" id="project-prompt">{esc(prompt)}<button onclick="copyText('project-prompt',this)">复制指令</button></div></details>'''
    if records:
        content = knowledge_surface(project, records, str(records[0]["path"]), project_updates=True)
    else:
        from knowledge_maintenance import widget
        content = '<div class="empty"><h2>还没有知识页</h2><p>让 AI 助手理解项目，或将已有 Markdown 放入 Wiki。</p></div>' + widget(project_id)
    body = notice(params) + knowledge_header() + content + dirty_html + help_html
    return layout(str(project["name"]), body, project_id=project_id, active="overview", home=home)


def _record_day(record: dict[str, Any]) -> tuple[str, str]:
    value = str(record.get("recorded_at") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d"), parsed.strftime("%Y\u5e74%m\u6708%d\u65e5")
    except ValueError:
        path = str(record.get("path") or "")
        match = re.search(r"(\d{4})/(\d{2})/(\d{4}-\d{2}-\d{2})\.md$", path)
        if match:
            return match.group(3), f"{match.group(1)}\u5e74{match.group(2)}\u6708{match.group(3)[-2:]}\u65e5"
        return "0000-00-00", "\u672a\u77e5\u65e5\u671f"


def _record_time(record: dict[str, Any]) -> str:
    value = str(record.get("recorded_at") or "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%H:%M")
    except ValueError:
        return ""


def _record_day_groups(records: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    grouped_records: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, str] = {}
    for record in records:
        day_key, label = _record_day(record)
        grouped_records.setdefault(day_key, []).append(record)
        labels[day_key] = label
    return [(key, labels[key], grouped_records[key]) for key in sorted(grouped_records, reverse=True)]


def records_page(home: str, project_id: str, params: dict[str, list[str]]) -> str:
    project = get_project(project_id, home=home)["project"]
    from research_reports import list_page, workspace
    view = (params.get("view") or ["records"])[0]
    if view in {"daily", "weekly"}:
        return list_page(home, workspace(home), view, params)
    query = (params.get("q") or [""])[0].strip()
    tag = (params.get("tag") or [""])[0].strip()
    try:
        limit = max(1, min(int((params.get("limit") or ["60"])[0]), MAX_LIST_RECORDS))
    except ValueError:
        limit = 60

    universe = list_records(
        str(project["source_root"]),
        state_root=str(project["state_root"]),
        max_records=MAX_LIST_RECORDS,
    )
    all_tags = sorted(
        {
            str(tag_value)
            for record in universe.get("records") or []
            for tag_value in (record.get("tags") or [])
            if str(tag_value).strip()
        }
    )

    result = list_records(
        str(project["source_root"]),
        state_root=str(project["state_root"]),
        query=query,
        tag=tag,
        max_records=limit,
    )
    records = list(result.get("records") or [])
    total_count = int(result.get("count") or 0)
    truncated = bool(result.get("truncated"))
    from research_notebook import load as load_notebook
    entries = []
    for record in records:
        record_id = str(record["id"])
        manual = record.get("type") == "research-notebook" and re.fullmatch(r"records/manual/([a-f0-9]{32})\.md", record_id)
        note_id, revision, comment_count = "", "", None
        if manual:
            note_id = manual[1]
            try:
                note = load_notebook(project, note_id, continuous_view=True)
                revision = note["revision"]
                comment_count = len(note["document"].get("comments", []))
            except (LLMWikiError, OSError, ValueError):
                pass
        source = "手动记录" if manual else "助手记录"
        date, _ = _record_day(record)
        metadata = f'<time title="{esc(record.get("recorded_at", ""))}">{esc(date if date != "0000-00-00" else "日期未知")}</time><span>{source}</span>'
        if comment_count is not None:
            metadata += f'<span>{comment_count} 条批注</span>'
        remove = (f'<button type="button" class="rw-icon-button record-delete" data-delete-note="{note_id}" data-revision="{revision}" aria-label="删除笔记：{esc(record["title"])}" title="删除笔记">{ui_icon("trash")}</button>' if note_id and revision else "")
        entries.append(f'<article class="rw-list-row" data-record-id="{esc(record_id)}">{ui_icon("notebook" if manual else "message")}<div class="rw-list-main"><a class="timeline-card rw-title-button" href="{recordurl(project_id, record_id)}">{esc(record["title"])}</a><div class="rw-list-meta">{metadata}</div></div><div class="rw-row-actions">{remove}<a class="rw-icon-button" href="{recordurl(project_id, record_id)}" aria-label="打开记录：{esc(record["title"])}" title="打开记录">{ui_icon("open")}</a></div></article>')
    record_html = '<div class="rw-list records-timeline">' + ''.join(entries) + '</div>'
    if not entries:
        record_html += ('<p class="record-empty muted">没有找到匹配的科研记录。</p>' if query or tag else '<p class="record-empty muted">还没有科研记录。新建一篇笔记，或让 AI 助手“记录刚才的讨论”。</p>')

    tag_options = '<option value="">全部标签</option>' + ''.join(
        f'<option value="{esc(value)}" {"selected" if value == tag else ""}>{esc(value)}</option>' for value in sorted(set(all_tags + ([tag] if tag else [])))
    )

    load_more_html = ""
    if truncated:
        params_parts = [f"limit={min(limit + 60, MAX_LIST_RECORDS)}"]
        if query:
            params_parts.append(f"q={quote(query, safe='')}")
        if tag:
            params_parts.append(f"tag={quote(tag, safe='')}")
        load_more_url = f"{purl(project_id)}/records?{'&'.join(params_parts)}"
        load_more_html = (
            f'<div class="actions" style="justify-content:center"><a class="button" href="{load_more_url}">'
            f"加载更多（已显示 {len(records)} / 共 {total_count} 条）</a></div>"
        )

    body = page_header("科研记录", new_button("新建笔记", href=f"{purl(project_id)}/notebook"))
    body += f'''<section id="research-records" data-project="{esc(project_id)}"><div class="records-intro"><span>手动笔记与助手记录，保留在同一处。日报周报独立管理。</span><details class="records-filter" {"open" if query or tag else ""}><summary class="rw-icon-button" aria-label="搜索与筛选记录" title="搜索与筛选">{ui_icon("search")}</summary><form class="filter-bar" action="{purl(project_id)}/records"><input type="search" name="q" aria-label="搜索记录" value="{esc(query)}" placeholder="搜索记录"><select name="tag" aria-label="按标签筛选">{tag_options}</select><button>搜索</button></form></details></div>{record_html}{load_more_html}<div class="record-feedback" role="status" hidden><span></span><button type="button" data-undo-delete>撤销</button></div><dialog id="record-delete-dialog"><form method="dialog"><h2>删除这篇笔记？</h2><p data-delete-title></p><p class="muted">删除后可以撤销。图片附件和历史版本会保留。</p><p role="alert" data-delete-error></p><div class="actions"><button value="cancel">取消</button><button type="button" class="danger" data-confirm-delete>删除笔记</button></div></form></dialog></section><script src="/static/records.js" defer></script>'''

    return layout("科研记录 · " + str(project["name"]), body, query=query, project_id=project_id, active="records", home=home)


def todos_page(home: str, project_id: str, params: dict[str, list[str]]) -> str:
    from research_progress import page

    return page(home, project_id)


MATERIAL_THUMBNAIL_SENTINEL = "LLMWIKIMATERIALTHUMBNAILS7F31E9C4"
EVIDENCE_SECTION_TITLE = "依据与关联材料"


def _material_path(item: str) -> str:
    """Normalize a bullet body into a source-relative path when possible."""
    item = item.strip()
    if item.startswith("!") and "](" in item:
        item = item.split("](", 1)[1].rstrip(")").strip()
    elif item.startswith("[") and "](" in item:
        item = item.split("](", 1)[1].rstrip(")").strip()
    return item.strip("` ").replace("\\", "/")


def _is_image_path(item: str) -> bool:
    return Path(_material_path(item)).suffix.lower() in IMAGE_MIME_TYPES


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def material_thumbnail(
    project: dict[str, Any], project_id: str, item: str
) -> str | None:
    """Render a small, lazily loaded, click-to-enlarge thumbnail."""
    normalized = _material_path(item).lstrip("/")
    if Path(normalized).suffix.lower() not in IMAGE_MIME_TYPES:
        return None
    try:
        target = safe_path(
            Path(str(project["source_root"])).resolve(strict=False), normalized
        )
    except (LLMWikiError, OSError, ValueError):
        return None
    if not target.is_file():
        return None
    url = esc(source_asset_url(project_id, normalized))
    label = esc(normalized)
    name = esc(Path(normalized).name)
    parent = esc(str(Path(normalized).parent).replace("\\", "/")) or "/"
    try:
        size_label = _format_bytes(target.stat().st_size)
    except OSError:
        size_label = ""
    dir_line = parent if not size_label else f"{parent} · {size_label}"
    return (
        f'<li class="material-thumb" role="listitem">'
        f'<button type="button" class="material-thumb-button" '
        f'data-lightbox-src="{url}" data-lightbox-title="{label}" title="{label}（点击放大）">'
        f'<img src="{url}" alt="{label}" loading="lazy" decoding="async">'
        f'<span class="material-thumb-caption">'
        f'<span class="material-thumb-name">{name}</span>'
        f'<span class="material-thumb-dir">{esc(dir_line)}</span>'
        f'</span></button></li>'
    )


def _extract_evidence_image_bullets(content: str) -> tuple[str, list[str], bool]:
    """Inline material images into the evidence section as thumbnails.

    Image bullets inside the "依据与关联材料" section are collected and replaced
    by a thumbnail placeholder; plain file bullets are wrapped in inline code so
    underscores in paths are not interpreted as emphasis. Returns
    (transformed_content, image_paths, has_evidence_section).
    """
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    images: list[str] = []
    in_evidence = False
    sentinel_inserted = False
    for line in text.split("\n"):
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            in_evidence = heading.group(2).strip() == EVIDENCE_SECTION_TITLE
            out.append(line)
            if in_evidence and not sentinel_inserted:
                out.extend(["", MATERIAL_THUMBNAIL_SENTINEL, ""])
                sentinel_inserted = True
            continue
        if in_evidence:
            bullet = re.match(
                r"^(?P<indent>\s*)(?P<mark>[-+*])\s+(?P<body>.+?)\s*$", line
            )
            if bullet:
                item = bullet.group("body").strip()
                candidate = _material_path(item)
                if candidate and Path(candidate).suffix.lower() in IMAGE_MIME_TYPES:
                    images.append(candidate)
                    continue
                if "`" not in item and "[" not in item and "]" not in item:
                    out.append(f'{bullet.group("indent")}{bullet.group("mark")} `{item}`')
                    continue
            out.append(line)
        else:
            out.append(line)
    return "\n".join(out), images, sentinel_inserted


def _material_gallery(
    project: dict[str, Any], project_id: str, images: list[str]
) -> str:
    thumbs: list[str] = []
    for item in images:
        thumb = material_thumbnail(project, project_id, item)
        if thumb:
            thumbs.append(thumb)
    if not thumbs:
        return ""
    return (
        '<div class="record-material-block" role="group" aria-label="实验图片预览">'
        f'<div class="material-grid-hint">实验图片 · {len(thumbs)} 张 · 点击放大，再次点击或按 Esc 关闭</div>'
        f'<ul class="record-material-grid" role="list">{"".join(thumbs)}</ul>'
        "</div>"
    )


def _material_panel(
    project_id: str,
    gallery: str,
    text_files: list[str],
    pages: list[str],
) -> str:
    """Fallback panel for materials that are not rendered inline in the body."""
    parts: list[str] = []
    if gallery:
        parts.append(gallery)
    if text_files:
        items = "".join(f"<li><code>{esc(x)}</code></li>" for x in text_files)
        parts.append(f'<h3>其他文件</h3><ul class="related-file-list">{items}</ul>')
    if pages:
        links = "".join(
            f'<li><a href="{pageurl(project_id, x)}">{esc(x)}</a></li>' for x in pages
        )
        parts.append(f'<h3>Wiki 页面</h3><ul>{links}</ul>')
    if not parts:
        return ""
    return '<section class="panel record-related"><h2>关联材料</h2>' + "".join(parts) + "</section>"


def _record_neighbors(
    project_root: str, state_root: str | None, current_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (older, newer) neighbors for chronological record paging."""
    result = list_records(
        project_root, state_root=state_root, max_records=MAX_LIST_RECORDS
    )
    records = list(reversed(list(result.get("records") or [])))
    ids = [str(item["id"]) for item in records]
    if current_id not in ids:
        return None, None
    index = ids.index(current_id)
    prev_record = records[index - 1] if index > 0 else None
    next_record = records[index + 1] if index + 1 < len(records) else None
    return prev_record, next_record


def record_view(home: str, project_id: str, record_id: str) -> str:
    project = get_project(project_id, home=home)["project"]
    result = read_record(
        str(project["source_root"]),
        record_id,
        state_root=str(project["state_root"]),
    )
    record = result["record"]
    content = str(record.get("content") or "")
    if record.get("type") == "research-notebook" and re.fullmatch(r"records/manual/[a-f0-9]{32}\.md", str(record["path"])):
        from research_notebook import editor_page
        return editor_page(home, project_id, Path(record["path"]).stem)


    related_files = [str(x) for x in record.get("related_files") or [] if str(x).strip()]
    images: list[str] = [x for x in related_files if _is_image_path(x)]
    text_files: list[str] = [x for x in related_files if not _is_image_path(x)]
    pages = [str(x) for x in record.get("related_pages") or [] if str(x).strip()]

    content_for_render, evidence_images, has_evidence = _extract_evidence_image_bullets(content)
    for item in evidence_images:
        if item not in images:
            images.append(item)

    rendered = render_markdown(content_for_render, project_id, str(record["path"]))
    gallery = _material_gallery(project, project_id, images)

    if has_evidence:
        rendered = rendered.replace(f"<p>{MATERIAL_THUMBNAIL_SENTINEL}</p>", gallery or "")
        missing_text = [x for x in text_files if _material_path(x) not in content]
        extra = _material_panel(project_id, "", missing_text, pages) if (missing_text or pages) else ""
    else:
        extra = _material_panel(project_id, gallery, text_files, pages)

    tags_html = "".join(
        f'<span class="badge">{esc(tag)}</span>' for tag in record.get("tags") or []
    )
    prev_record, next_record = _record_neighbors(
        str(project["source_root"]), str(project["state_root"]), str(record["id"])
    )
    prev_link = (
        f'<a class="button" href="{recordurl(project_id, str(prev_record["id"]))}" title="{esc(prev_record["title"])}">&larr; 上一篇</a>'
        if prev_record
        else ""
    )
    next_link = (
        f'<a class="button" href="{recordurl(project_id, str(next_record["id"]))}" title="{esc(next_record["title"])}">下一篇 &rarr;</a>'
        if next_record
        else ""
    )
    pager_html = f'<div class="record-pager">{prev_link}{next_link}</div>' if (prev_link or next_link) else ""
    body = f'''<div class="doc-toolbar"><a href="{purl(project_id)}/records">← 科研记录</a></div><section class="record-document"><details class="reader-contents"><summary>记录信息</summary><div class="document-meta"><span class="meta">记录时间：{esc(format_time(str(record.get("recorded_at") or "")))}</span><span class="meta path">{esc(record["path"])}</span>{tags_html}</div></details><article class="document">{rendered}</article>{pager_html}{extra}</section>'''
    return layout(str(record["title"]), body, project_id=project_id, active="records", home=home)

def heading_slug(text: str) -> str:
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", text.strip().lower()).strip("-") or "section"


def extract_headings(markdown: str) -> list[tuple[int, str, str]]:
    headings: list[tuple[int, str, str]] = []
    in_code = False
    for line in markdown.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = re.match(r"^ {0,3}(#{1,4})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"[`*_~\[\]]", "", match.group(2)).strip()
        headings.append((len(match.group(1)), title, heading_slug(match.group(2))))
    return headings


def page_view(home: str, project_id: str, relative: str) -> str:
    project = get_project(project_id, home=home)["project"]
    wiki_root = Path(str(project["wiki_root"])).resolve(strict=False)
    target = safe_path(wiki_root, relative)
    if target.suffix.lower() != ".md" or not target.is_file():
        raise FileNotFoundError(relative)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise LLMWikiError("Markdown 文件必须是 UTF-8 编码。") from exc
    current = {"title": _wiki_page_title(target)}
    headings = extract_headings(text)
    toc = "".join(f'<li class="level-{level}"><a href="#{esc(slug)}">{esc(title)}</a></li>' for level, title, slug in headings)
    rendered = render_markdown(text, project_id, relative)
    contents = f'<details class="reader-contents"><summary>目录</summary><ul>{toc}</ul></details>' if toc else ''
    body = f'''<div class="doc-toolbar"><a href="{purl(project_id)}">← 知识库</a><details class="action-menu"><summary>更多</summary><div class="action-menu-items"><button onclick="navigator.clipboard.writeText({esc(repr(relative))})">复制页面路径</button><button onclick="window.print()">打印 / 导出 PDF</button></div></details></div><div class="reading-layout">{contents}<article class="document">{rendered}</article></div>'''
    from knowledge_maintenance import page_target
    try:
        page_target(project, relative)
    except (LLMWikiError, ValueError):
        pass  # Notes, reports, and literature keep their existing reading surface.
    else:
        records = knowledge_records(project)
        body = knowledge_header() + knowledge_surface(project, records, relative)
    return layout(str(current["title"]), body, project_id=project_id, active="pages", home=home)


def highlighted_excerpt(text: str, query: str, radius: int = 110) -> str:
    lowered = text.lower()
    index = lowered.find(query.lower())
    if index < 0:
        return esc(text[: radius * 2].replace("\n", " "))
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    before = esc(text[start:index].replace("\n", " "))
    match = esc(text[index : index + len(query)])
    after = esc(text[index + len(query) : end].replace("\n", " "))
    return ("…" if start else "") + before + f"<mark>{match}</mark>" + after + ("…" if end < len(text) else "")


def search_page(home: str, params: dict[str, list[str]]) -> str:
    query = (params.get("q") or [""])[0].strip()
    selected = (params.get("project") or [""])[0].strip()
    projects = list_projects(home)["projects"]
    options = ['<option value="">全部研究项目</option>'] + [f'<option value="{esc(project["id"])}" {"selected" if selected == project["id"] else ""}>{esc(project["name"])}</option>' for project in projects]
    results: list[dict[str, Any]] = []
    if query:
        for project in projects:
            if selected and project["id"] != selected:
                continue
            pages = wiki_list(str(project["source_root"]), state_root=str(project["state_root"]))["pages"]
            for page in pages:
                target = safe_path(Path(str(project["wiki_root"])), str(page["path"]))
                try:
                    if target.stat().st_size > 2 * 1024 * 1024:
                        continue
                    text = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                haystack = f'{page["title"]}\n{page["path"]}\n{text}'
                if query.lower() not in haystack.lower():
                    continue
                results.append({"project": project, "page": page, "category": classify_page(str(page["path"]), str(page["title"])), "excerpt": highlighted_excerpt(haystack, query)})
    result_html = "".join(f'''<article class="search-result"><div><span class="category-tag">{esc(item["category"])}</span><span class="meta">{esc(item["project"]["name"])}</span></div><h2><a href="{pageurl(item["project"]["id"], item["page"]["path"])}">{esc(item["page"]["title"])}</a></h2><div class="meta path">{esc(item["page"]["path"])}</div><p>{item["excerpt"]}</p></article>''' for item in results)
    if query and not results:
        result_html = '<div class="empty"><h2>没有找到相关内容</h2><p>可尝试项目术语、算法名、实验指标、作者名或更短的关键词。</p></div>'
    if not query:
        result_html = '<p class="empty">搜索项目中的笔记、文献解读和知识页。</p>'
    body = page_header("搜索") + f'''<form class="filter-bar" action="/search"><input id="q" type="search" name="q" aria-label="搜索关键词" value="{esc(query)}" placeholder="搜索笔记、方法、实验…"><select id="project" name="project" aria-label="研究项目">{"".join(options)}</select><button class="primary">搜索</button></form>{result_html}'''
    return layout("科研检索", body, query, active="search", home=home)


def settings_page(home: str, params: dict[str, list[str]]) -> str:
    settings = load_settings(home)
    projects = list_projects(home)["projects"]
    project_forms = []
    for project in projects:
        project_forms.append(f'''<details class="settings-section" id="project-{quote(project["id"], safe='')}"><summary>{esc(project["name"])}</summary><p class="path">{esc(project["source_root"])}</p><form method="post" action="{purl(project["id"])}/storage"><label>Wiki 目录<input type="text" name="wiki_root" value="{esc(project["wiki_root"])}" required></label><details class="settings"><summary>高级设置</summary><label>机器状态目录<input type="text" name="state_root" value="{esc(project["state_root"])}"></label></details><label class="check-label"><input type="checkbox" name="copy_existing" value="1" checked>复制现有内容到新位置（保留旧目录）</label><div class="actions"><button class="primary">保存项目位置</button></div></form><form method="post" action="{purl(project["id"])}/unregister" onsubmit="return confirm('只取消注册，不删除任何文件。确定继续吗？')"><button class="danger">取消注册</button></form></details>''')
    default_root = settings.get("default_wiki_root") or ""
    try:
        web_port = int(settings.get("web_port") or 8765)
    except (TypeError, ValueError):
        web_port = 8765
    body = notice(params) + page_header("设置") + f'''<div class="settings-content"><details class="settings-section" open><summary>默认存储</summary><form method="post" action="/settings/default-wiki-root"><label>Wiki 默认根目录<input type="text" name="default_wiki_root" value="{esc(default_root)}" placeholder="留空则使用项目下的 wiki 目录"></label><p class="meta">仅影响之后添加的项目。</p><details class="settings"><summary>高级设置</summary><label>本地网站端口<input type="number" name="web_port" min="1024" max="65535" value="{web_port}"></label><p class="path">注册表：{esc(home)}</p></details><div class="actions"><button class="primary">保存默认设置</button></div></form></details><details class="settings-section" id="register-project"><summary>添加项目</summary><form method="post" action="/project/register"><label>项目目录<input type="text" name="source_root" required placeholder="项目的绝对路径"></label><label>项目名称<input type="text" name="name" placeholder="默认使用目录名"></label><label>Wiki 目录<input type="text" name="wiki_root" placeholder="留空使用默认位置"></label><details class="settings"><summary>高级设置</summary><label>机器状态目录<input type="text" name="state_root" placeholder="留空由插件管理"></label></details><p class="meta">添加项目不会修改源文件，也不会自动扫描。</p><div class="actions"><button class="primary">注册项目</button></div></form></details><h2 class="section-title">已添加的项目</h2>{"".join(project_forms) if project_forms else '<p class="muted">暂无项目</p>'}</div>'''
    from research_reports import settings_section
    body += '<section class="settings-section appearance-settings"><h2>外观</h2><div class="theme-settings">' + theme_choices() + '</div><p class="meta">只保存在当前浏览器，跟随系统会自动切换。</p></section>'
    body += settings_section(home)
    return layout("设置", body, active="settings", home=home)
