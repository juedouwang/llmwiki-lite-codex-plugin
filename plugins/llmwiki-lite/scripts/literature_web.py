"""中文文献中心、论文原文阅读与 LLM 辅助阅读对照页。"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from llmwiki_core import LLMWikiError, wiki_list
from llmwiki_registry import get_project
from markdown_renderer import render_markdown
from research_web_ui import esc, layout, pageurl, purl, safe_path

PAPER_EXTENSIONS = {".pdf", ".epub", ".docx", ".html", ".htm"}
INLINE_EXTENSIONS = {".pdf"}
NOTE_MAX_BYTES = 2 * 1024 * 1024
MAX_DISCOVERED_FILES = 10_000
MAX_PAPERS = 1_000
MAX_SOURCE_NOTES = 300
SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".llmwiki",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}
NOTE_NAME_HINTS = (
    "精读",
    "阅读",
    "论文笔记",
    "文献笔记",
    "组会",
    "调研",
    "综述",
    "报告",
    "summary",
    "review",
    "reading",
    "literature",
    "paper-note",
)
STOP_TOKENS = {
    "pdf",
    "paper",
    "official",
    "supplemental",
    "supplement",
    "cvpr",
    "iccv",
    "eccv",
    "wacv",
    "arxiv",
    "ieee",
    "acm",
    "2021",
    "2022",
    "2023",
    "2024",
    "2025",
    "2026",
    "中文",
    "精读",
    "报告",
    "论文",
}


def _relative(path: Path, root: Path) -> str:
    return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()


def _display_title(path: str) -> str:
    title = Path(path).stem.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", title).strip()


def _paper_kind(path: str) -> str:
    normalized = path.lower()
    name = Path(path).stem.lower()
    if "supplement" in name or "supplemental" in name or "附录" in name:
        return "补充材料"
    if re.search(r"(?:^|[/_])cn\d", normalized) or "专利" in normalized:
        return "专利文献"
    if any(token in normalized for token in ("qa_render", "qa_v", "修改稿")):
        return "报告或其他 PDF"
    if Path(path).suffix.lower() != ".pdf":
        return "其他文献格式"
    return "学术论文"


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _frontmatter_paths(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return []
    end = normalized.find("\n---\n", 4)
    if end < 0:
        return []
    lines = normalized[4:end].splitlines()
    values: list[str] = []
    collecting_sources = False
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and not line[:1].isspace():
            collecting_sources = key.strip() == "sources"
            if key.strip() in {"paper_file", "paper_path", "source_file"}:
                cleaned = value.strip().strip('"\'')
                if cleaned:
                    values.append(cleaned)
            elif collecting_sources and value.strip().startswith("["):
                values.extend(
                    item.strip().strip('"\'')
                    for item in value.strip().strip("[]").split(",")
                    if item.strip()
                )
            continue
        if collecting_sources and re.match(r"^\s*-\s+", line):
            cleaned = re.sub(r"^\s*-\s+", "", line).strip().strip('"\'')
            if cleaned:
                values.append(cleaned)
    return values


def _note_title(text: str, fallback: str) -> str:
    frontmatter = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if frontmatter:
        match = re.search(r'(?m)^title:\s*["\']?(.*?)["\']?\s*$', frontmatter.group(1))
        if match and match.group(1).strip():
            return match.group(1).strip()
    body = text[frontmatter.end() :] if frontmatter else text
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", body)
    return heading.group(1).strip() if heading else fallback


def _tokens(value: str) -> set[str]:
    normalized = value.lower().replace("\\", "/")
    tokens = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", normalized))
    return {token for token in tokens if token not in STOP_TOKENS}


def _normalized_path(value: str) -> str:
    return value.strip().strip('"\'').replace("\\", "/").lstrip("./").casefold()


def discover_literature(project: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    source_root = Path(str(project["source_root"])).resolve(strict=False)
    wiki_root = Path(str(project["wiki_root"])).resolve(strict=False)
    papers: list[dict[str, Any]] = []
    source_notes: list[dict[str, Any]] = []
    scanned = 0
    for current, directories, files in os.walk(source_root, followlinks=False):
        current_path = Path(current).resolve(strict=False)
        directories[:] = [
            name
            for name in directories
            if name.lower() not in SKIP_DIRECTORIES
            and not (current_path / name).is_symlink()
            and (current_path / name).resolve(strict=False) != wiki_root
        ]
        for name in files:
            scanned += 1
            if scanned > MAX_DISCOVERED_FILES:
                break
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative = _relative(path, source_root)
                stat = path.stat()
            except (OSError, ValueError):
                continue
            suffix = path.suffix.lower()
            if suffix in PAPER_EXTENSIONS and len(papers) < MAX_PAPERS:
                papers.append(
                    {
                        "path": relative,
                        "title": _display_title(relative),
                        "extension": suffix,
                        "bytes": int(stat.st_size),
                        "updated": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "kind": _paper_kind(relative),
                        "inline": suffix in INLINE_EXTENSIONS,
                    }
                )
                continue
            if suffix != ".md" or len(source_notes) >= MAX_SOURCE_NOTES:
                continue
            normalized = relative.lower()
            if not any(hint in normalized for hint in NOTE_NAME_HINTS):
                continue
            if stat.st_size > NOTE_MAX_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            source_notes.append(
                {
                    "id": f"source:{relative}",
                    "location": "source",
                    "path": relative,
                    "title": _note_title(text, _display_title(relative)),
                    "text": text,
                    "declared_sources": _frontmatter_paths(text),
                    "updated": datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                }
            )
        if scanned > MAX_DISCOVERED_FILES:
            break
    wiki_notes: list[dict[str, Any]] = []
    pages = wiki_list(
        str(project["source_root"]), state_root=str(project["state_root"])
    )["pages"]
    for page in pages:
        target = safe_path(wiki_root, str(page["path"]))
        try:
            if target.stat().st_size > NOTE_MAX_BYTES:
                continue
            text = target.read_text(encoding="utf-8")
            stat = target.stat()
        except (OSError, UnicodeDecodeError):
            continue
        normalized = f'{page["path"]} {page["title"]}'.lower()
        declared = _frontmatter_paths(text)
        if not declared and not any(hint in normalized for hint in NOTE_NAME_HINTS):
            if not any(
                token in normalized
                for token in ("paper", "literature", "文献", "论文", "reading")
            ):
                continue
        wiki_notes.append(
            {
                "id": f'wiki:{page["path"]}',
                "location": "wiki",
                "path": str(page["path"]),
                "title": str(page["title"]),
                "text": text,
                "declared_sources": declared,
                "updated": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                ),
            }
        )
    papers.sort(key=lambda item: (str(item["kind"]), str(item["path"]).lower()))
    notes = wiki_notes + source_notes
    notes.sort(key=lambda item: (str(item["location"]), str(item["title"]).lower()))
    return {"papers": papers, "notes": notes}


def _distinctive_tokens(value: str) -> set[str]:
    generic = {
        "rgbd",
        "point",
        "cloud",
        "registration",
        "matching",
        "feature",
        "features",
        "method",
        "novel",
        "robust",
        "using",
        "geometry",
        "visual",
        "official",
        "report",
        "review",
        "reading",
        "paper",
        "supplemental",
        "supplement",
        "cvpr2021",
        "cvpr2024",
        "cvpr2025",
        "cvpr2026",
        "iccv2023",
        "iccv2025",
        "wacv2026",
    }
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9]{2,}", value.casefold())
        if token not in generic and not re.fullmatch(r"(?:19|20)\d{2}", token)
    }


def note_match_score(paper: dict[str, Any], note: dict[str, Any]) -> int:
    paper_path = _normalized_path(str(paper["path"]))
    paper_name = Path(paper_path).name
    paper_stem = Path(paper_path).stem
    declared = {_normalized_path(str(item)) for item in note["declared_sources"]}
    if paper_path in declared:
        return 100
    if paper_name in {Path(item).name for item in declared}:
        return 90
    note_identity = _normalized_path(f'{note["path"]} {note["title"]}')
    compact_stem = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", paper_stem)
    compact_note = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", note_identity)
    if len(compact_stem) >= 8 and compact_stem in compact_note:
        return 85
    shared = _distinctive_tokens(paper_stem) & _distinctive_tokens(note_identity)
    if not shared:
        return 0
    strong = [
        token for token in shared if len(token) >= 4 or any(c.isdigit() for c in token)
    ]
    if strong:
        return min(85, 60 + 12 * (len(strong) - 1))
    return 60 if len(shared) >= 2 else 30

def matched_notes(
    paper: dict[str, Any], notes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    matches = []
    for note in notes:
        score = note_match_score(paper, note)
        if score >= 55:
            matches.append({**note, "match_score": score})
    return sorted(
        matches,
        key=lambda item: (-int(item["match_score"]), str(item["title"]).casefold()),
    )


def load_note(
    project: dict[str, Any], note_id: str, *, library: dict[str, list[dict[str, Any]]] | None = None
) -> dict[str, Any]:
    data = library or discover_literature(project)
    note = next((item for item in data["notes"] if item["id"] == note_id), None)
    if note is None:
        raise FileNotFoundError(note_id)
    return note


def source_document_path(project: dict[str, Any], relative: str) -> Path:
    root = Path(str(project["source_root"])).resolve(strict=True)
    normalized = relative.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise LLMWikiError("文献路径无效。")
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise LLMWikiError("不允许通过符号链接读取文献。")
    try:
        target = candidate.resolve(strict=True)
        target.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise FileNotFoundError(relative) from exc
    if not target.is_file() or target.suffix.lower() not in PAPER_EXTENSIONS:
        raise FileNotFoundError(relative)
    return target


def _paper_url(project_id: str, action: str, path: str) -> str:
    return f"{purl(project_id)}/literature/{action}/{quote(path, safe='/')}"


def _prompt_for(paper: dict[str, Any]) -> str:
    return f'''请精读项目中的文献：{paper["path"]}

请先阅读原文，不要只根据文件名推测。生成一份简体中文辅助阅读 Markdown，并保存到本项目 Wiki。frontmatter 至少包含：
---
title: "{paper["title"]} 中文精读"
type: literature-note
language: zh-CN
paper_file: {paper["path"]}
sources:
  - {paper["path"]}
---

正文按以下结构组织：文献信息、一句话结论、研究问题、方法概览、关键公式或机制、实验设计、主要结果、局限、与本课题关系、复现线索、待验证问题。保留论文原标题、算法名、数据集名与准确数值；把原文事实、你的解释和待验证推断明确区分。'''


def literature_library_page(home: str, project_id: str) -> str:
    project = get_project(project_id, home=home)["project"]
    library = discover_literature(project)
    papers = library["papers"]
    notes = library["notes"]
    kind_counts = Counter(str(paper["kind"]) for paper in papers)
    matched_note_ids: set[str] = set()
    cards: list[str] = []
    matched_papers = 0
    for paper in papers:
        matches = matched_notes(paper, notes)
        matched_note_ids.update(str(item["id"]) for item in matches)
        if matches:
            matched_papers += 1
        read_url = _paper_url(project_id, "read", str(paper["path"]))
        compare_url = _paper_url(project_id, "compare", str(paper["path"]))
        status_key = "read" if matches else "unread"
        status_label = "已精读" if matches else "待精读"
        status_class = "" if matches else " pending"
        note_list = "".join(
            f'<li><a href="{compare_url}?note={quote(str(note["id"]), safe="")}">{esc(note["title"])}</a><span class="meta"> · {"Wiki 知识库" if note["location"] == "wiki" else "项目内只读记录"}</span></li>'
            for note in matches[:4]
        )
        prompt_id = "prompt-" + hashlib.sha1(
            str(paper["path"]).encode("utf-8")
        ).hexdigest()[:10]
        assistant = (
            f'<div class="paper-notes"><strong>LLM 辅助阅读</strong><ul>{note_list}</ul></div>'
            if matches
            else f'<details class="paper-prompt"><summary>让 Codex 精读这篇论文</summary><div class="prompt" id="{prompt_id}">{esc(_prompt_for(paper))}<button onclick="copyText(\'{prompt_id}\',this)">复制</button></div></details>'
        )
        compare_action = (
            f'<a class="button primary" href="{compare_url}?note={quote(str(matches[0]["id"]), safe="")}">原文 + LLM 辅助阅读</a>'
            if matches and paper["inline"]
            else ""
        )
        extension = str(paper["extension"]).upper().lstrip(".")
        search_value = esc(
            (str(paper["title"]) + " " + str(paper["path"])).casefold()
        )
        cards.append(
            f'''<article class="paper-card" data-literature-card data-literature-kind="{esc(paper["kind"])}" data-literature-status="{status_key}" data-page-title="{search_value}"><div class="paper-card-top"><span class="paper-file-type">{esc(extension)}</span><span class="reading-state{status_class}">{status_label}</span></div><h2>{esc(paper["title"])}</h2><div><span class="category-tag">{esc(paper["kind"])}</span></div><div class="paper-path path">{esc(paper["path"])}</div><p class="meta">{_format_size(int(paper["bytes"]))} · 更新于 {esc(paper["updated"])}</p>{assistant}<div class="actions"><a class="button" href="{read_url}">阅读原文</a>{compare_action}</div></article>'''
        )

    unread_papers = len(papers) - matched_papers
    unpaired_notes = [note for note in notes if str(note["id"]) not in matched_note_ids]
    unpaired_parts: list[str] = []
    for note in unpaired_notes:
        title = esc(note["title"])
        if note["location"] == "wiki":
            title = f'<a href="{pageurl(project_id, str(note["path"]))}">{title}</a>'
        location = "Wiki 知识库" if note["location"] == "wiki" else "项目内只读记录"
        unpaired_parts.append(
            f'<li>{title}<span class="meta"> · {esc(note["path"])} · {location}</span></li>'
        )
    unpaired_html = "".join(unpaired_parts)
    kind_buttons = "".join(
        f'''<button class="library-nav-item" data-filter-group="kind" onclick="setLiteratureFilter('kind','{esc(kind)}',this)"><span>{esc(kind)}</span><span class="nav-count">{count}</span></button>'''
        for kind, count in sorted(kind_counts.items())
    )
    workflow_prompt = f'''请使用 llmwiki-literature 工作流处理当前项目“{project["name"]}”的文献任务：

1. 围绕我的研究问题调研并推荐 5–8 篇高相关论文，说明推荐理由、优先级、年份、出处和可获取的原文来源；这一步先不要下载。
2. 等我明确选择某一篇后，再把原文下载到项目目录 references/papers/，不要覆盖已有文件。
3. 校验下载文件确实是论文 PDF；如果来源受限，不要绕过权限，告诉我需要手动提供文件。
4. 阅读原文后，在 Wiki 中生成简体中文精读 Markdown，使用 paper_file 精确绑定项目内 PDF。
5. 把论文原文事实、你的解释和待验证推断分开写，准确数值、公式和表格必须回到原文核验。
6. 完成后启动或刷新文献中心，并告诉我可以在哪里进行原文与 LLM 辅助阅读对照。'''
    workflow_prompt_id = "literature-workflow-prompt"
    empty_papers = (
        '<section class="panel empty">项目目录中尚未发现文献。让 Codex 推荐论文并在你确认后下载，或把已有 PDF 放入项目目录。</section>'
        if not papers
        else ""
    )
    body = f'''<div class="literature-app"><aside class="literature-sidebar"><div class="library-title"><span class="library-mark">W</span><div><strong>文献库</strong><span>{esc(project["name"])}</span></div></div><input class="library-search" type="search" oninput="filterLiterature(this)" placeholder="搜索标题或路径"><nav class="library-nav" aria-label="文献快速筛选"><div class="library-nav-group"><h2>阅读状态</h2><button class="library-nav-item is-active" data-filter-group="status" onclick="setLiteratureFilter('status','all',this)"><span>全部文献</span><span class="nav-count">{len(papers)}</span></button><button class="library-nav-item" data-filter-group="status" onclick="setLiteratureFilter('status','read',this)"><span>已精读</span><span class="nav-count">{matched_papers}</span></button><button class="library-nav-item" data-filter-group="status" onclick="setLiteratureFilter('status','unread',this)"><span>待精读</span><span class="nav-count">{unread_papers}</span></button></div><div class="library-nav-group"><h2>文献类型</h2><button class="library-nav-item is-active" data-filter-group="kind" onclick="setLiteratureFilter('kind','all',this)"><span>全部类型</span><span class="nav-count">{len(papers)}</span></button>{kind_buttons}</div><div class="library-nav-group"><h2>辅助阅读</h2><a class="library-nav-item" href="#unpaired-notes"><span>待关联笔记</span><span class="nav-count">{len(unpaired_notes)}</span></a><a class="library-nav-item" href="#codex-literature-flow"><span>交给 Codex 调研</span><span class="nav-count">→</span></a></div></nav><div class="library-nav-separator"></div><div class="library-sidebar-help">论文原文保存在项目目录中并保持只读；中文精读保存在 Wiki，通过 <code>paper_file</code> 与原文精确关联。</div></aside><section class="literature-content"><section class="library-header"><div><div class="eyebrow">文献阅读工作台</div><h1>项目文献</h1><p>从待精读论文进入原文阅读，再与 Codex 生成的中文精读并排核对。</p></div><div class="library-header-actions"><a class="button" href="{purl(project_id)}">返回项目研究台</a><a class="button primary" href="#codex-literature-flow">推荐并入库论文</a></div></section><div class="library-summary"><span class="summary-pill"><strong>{len(papers)}</strong> 篇原文</span><span class="summary-pill"><strong>{matched_papers}</strong> 篇已精读</span><span class="summary-pill"><strong>{unread_papers}</strong> 篇待精读</span><span class="summary-pill"><strong>{len(unpaired_notes)}</strong> 份笔记待关联</span></div><div class="library-results-head"><h2><span id="literature-result-count">{len(papers)}</span> 篇文献</h2><div class="view-switch" aria-label="切换文献显示方式"><button class="is-active" data-literature-view="grid" onclick="setLiteratureView('grid',this)">卡片</button><button data-literature-view="list" onclick="setLiteratureView('list',this)">列表</button></div></div>{empty_papers}<div class="literature-grid is-list" id="literature-list">{"".join(cards)}</div><div class="panel empty literature-filter-empty" id="literature-filter-empty">当前筛选条件下没有文献。可以清除搜索词或切换左侧分类。</div><section class="panel literature-support" id="codex-literature-flow"><div class="eyebrow">CODEX 文献工作流</div><h2>从推荐到网页对照阅读</h2><ol class="workflow-steps"><li><span>1</span><strong>调研推荐</strong>先给出高相关论文和理由</li><li><span>2</span><strong>用户选择</strong>明确选中后才下载</li><li><span>3</span><strong>下载原文</strong>保存到项目 references/papers</li><li><span>4</span><strong>中文精读</strong>写入 Wiki 并绑定 paper_file</li><li><span>5</span><strong>网页核对</strong>原文与 LLM 讲解双栏阅读</li></ol><p class="muted">当前系统已经具备下载完成后的自动发现、精读关联和网页阅读；下面这段指令用于让 Codex 完整执行推荐、下载、分析和入库。</p><div class="prompt" id="{workflow_prompt_id}">{esc(workflow_prompt)}<button onclick="copyText('{workflow_prompt_id}',this)">复制给 Codex</button></div></section><section class="panel literature-support" id="unpaired-notes"><h2>待关联的辅助阅读</h2>{f'<ul class="recent-list">{unpaired_html}</ul>' if unpaired_notes else '<p class="muted">当前辅助阅读记录均已找到候选原文。</p>'}<p class="meta">如自动配对不准确，请在辅助阅读 Markdown frontmatter 中填写相对于项目根目录的 <code>paper_file</code>。</p></section></section></div>'''
    return layout("\u6587\u732e\u4e2d\u5fc3", body, project_id=project_id, active="literature", home=home)

def _paper_viewer(project_id: str, paper: dict[str, Any]) -> str:
    source_url = _paper_url(project_id, "source", str(paper["path"]))
    if paper["inline"]:
        return f'<iframe class="paper-frame" src="{source_url}#view=FitH" title="{esc(paper["title"])} 原文"></iframe>'
    return f'''<div class="empty document-fallback"><h2>当前格式暂不支持网页内嵌</h2><p>可以从本站安全打开或下载原文件，再使用本机阅读器查看。</p><a class="button primary" href="{source_url}">打开原文件</a></div>'''


def literature_read_page(home: str, project_id: str, paper_path: str) -> str:
    project = get_project(project_id, home=home)["project"]
    target = source_document_path(project, paper_path)
    stat = target.stat()
    paper = {
        "path": _relative(target, Path(str(project["source_root"]))),
        "title": _display_title(paper_path),
        "extension": target.suffix.lower(),
        "bytes": int(stat.st_size),
        "updated": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "kind": _paper_kind(paper_path),
        "inline": target.suffix.lower() in INLINE_EXTENSIONS,
    }
    library = discover_literature(project)
    matches = matched_notes(paper, library["notes"])
    compare = (
        f'<a class="button primary" href="{_paper_url(project_id, "compare", str(paper["path"]))}?note={quote(str(matches[0]["id"]), safe="")}">原文 + LLM 辅助阅读</a>'
        if matches and paper["inline"]
        else ""
    )
    body = f'''<div class="doc-toolbar"><div class="actions">{compare}<a class="button" href="{_paper_url(project_id, "source", str(paper["path"]))}">在新窗口打开原文</a></div></div><section class="paper-viewer"><div class="paper-viewer-head"><div><span class="category-tag">{esc(paper["kind"])}</span><h1>{esc(paper["title"])}</h1></div><p class="meta">{_format_size(int(paper["bytes"]))} · 更新于 {esc(paper["updated"])}<br><span class="path">{esc(paper["path"])}</span></p></div>{_paper_viewer(project_id, paper)}</section>'''
    return layout(str(paper["title"]), body, project_id=project_id, active="literature", home=home)


def literature_compare_page(
    home: str, project_id: str, paper_path: str, note_id: str | None = None
) -> str:
    project = get_project(project_id, home=home)["project"]
    target = source_document_path(project, paper_path)
    stat = target.stat()
    paper = {
        "path": _relative(target, Path(str(project["source_root"]))),
        "title": _display_title(paper_path),
        "extension": target.suffix.lower(),
        "bytes": int(stat.st_size),
        "updated": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "kind": _paper_kind(paper_path),
        "inline": target.suffix.lower() in INLINE_EXTENSIONS,
    }
    if not paper["inline"]:
        return literature_read_page(home, project_id, paper_path)
    library = discover_literature(project)
    matches = matched_notes(paper, library["notes"])
    selected: dict[str, Any] | None = None
    if note_id:
        candidate = load_note(project, note_id, library=library)
        if note_match_score(paper, candidate) < 55:
            raise LLMWikiError("所选辅助阅读未与这篇文献建立可靠关联。")
        selected = candidate
    elif matches:
        selected = matches[0]
    options = "".join(
        f'<option value="{esc(note["id"])}" {"selected" if selected and note["id"] == selected["id"] else ""}>{esc(note["title"])}</option>'
        for note in matches
    )
    compare_url = _paper_url(project_id, "compare", str(paper["path"]))
    if selected:
        rendered = render_markdown(
            str(selected["text"]), project_id, str(selected["path"])
        )
        source_label = "Wiki 知识库" if selected["location"] == "wiki" else "项目内只读记录"
        note_html = f'''<div class="compare-pane-head"><div><div class="eyebrow">LLM 辅助阅读</div><h2>{esc(selected["title"])}</h2></div><span class="badge">{source_label}</span></div><article class="note-document">{rendered}</article>'''
    else:
        prompt_id = "compare-prompt"
        note_html = f'''<div class="compare-pane-head"><div><div class="eyebrow">LLM 辅助阅读</div><h2>尚无匹配记录</h2></div></div><div class="empty"><p>把下面的指令复制给 Codex，即可生成可配对的中文精读 Markdown。</p><div class="prompt" id="{prompt_id}">{esc(_prompt_for(paper))}<button onclick="copyText('{prompt_id}',this)">复制</button></div></div>'''
    selector = (
        f'<form class="note-selector" method="get" action="{compare_url}"><label for="note">切换辅助阅读</label><select id="note" name="note">{options}</select><button>切换</button></form>'
        if matches
        else ""
    )
    body = f'''<div class="doc-toolbar"><div class="actions"><a class="button" href="{_paper_url(project_id, "read", str(paper["path"]))}">只看原文</a></div></div>{selector}<div class="compare-layout"><section class="compare-pane"><div class="compare-pane-head"><div><div class="eyebrow">论文原文</div><h2>{esc(paper["title"])}</h2></div><a class="button" href="{_paper_url(project_id, "source", str(paper["path"]))}">新窗口打开</a></div>{_paper_viewer(project_id, paper)}</section><section class="compare-pane note-pane">{note_html}</section></div>'''
    return layout(f'{paper["title"]} · 对照阅读', body, project_id=project_id, active="literature", home=home)
