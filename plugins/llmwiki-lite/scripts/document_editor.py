"""Shared continuous document markup. Storage stays in notebook/report adapters."""
from research_web_ui import esc, ui_icon


def editor_markup(prefix: str, title: str, back: str, *, notebook=False) -> str:
    def ident(name):
        return f'{prefix}-{name}'
    heading = f'<input id="{ident("title")}" class="rw-title-input" aria-label="{"笔记" if notebook else "报告"}标题" placeholder="无标题" maxlength="200" autocomplete="off" value="{esc(title)}">'
    primary = '' if notebook else f'<button id="{ident("confirm")}" class="rw-button rw-primary">确认为正式版</button>'
    info = "笔记信息" if notebook else "文档信息"
    return f'''<link rel="stylesheet" href="/static/document-editor.css">
<header class="rw-document-toolbar"><a class="rw-icon-button" href="{esc(back)}" aria-label="返回列表">{ui_icon("left")}</a>
<span id="{ident("state")}" class="muted"></span><span id="{ident("save-state")}" class="muted" role="status" aria-live="polite">正在打开…</span><button id="{ident("retry")}" class="rw-button rw-save-retry" hidden>重试保存</button>
<span class="rw-spacer"></span><button id="{ident("export")}" class="rw-button">导出</button>{primary}
<details class="action-menu rw-document-more"><summary aria-label="文档更多操作">···</summary><div class="action-menu-items">
<button id="{ident("info")}">{info}</button><button id="{ident("history")}">版本历史</button><button id="{ident("copy")}">复制 Markdown</button>
{'<button id="'+ident('save-as')+'">另存为新笔记</button>' if notebook else '<button id="'+ident('regenerate')+'">重新整理</button>'}
</div></details></header>
<div id="{ident("notice")}" class="document-notice" role="status" hidden></div>
<div id="{ident("candidate-notice")}" hidden>有新的整理结果 <button id="{ident("candidate")}">查看新稿</button></div>
<div class="rw-editor-body">{heading}
<div class="rw-editor-header"><button id="{ident("edit")}" class="rw-button" aria-pressed="false">编辑</button><button id="{ident("preview-mode")}" class="rw-button" aria-pressed="true">预览</button><span class="rw-spacer"></span>{'' if notebook else '<button id="'+ident('sources')+'" class="rw-button">来源</button>'}</div>
<div id="{ident("document")}" class="document-surface"><textarea id="{ident("source")}" class="rw-editor-text" aria-label="{'笔记' if notebook else '报告'} Markdown 正文" spellcheck="false" placeholder="直接开始记录，或 Ctrl+V 粘贴文字、截图…" hidden></textarea><article id="{ident("preview")}" class="prose rw-preview" tabindex="0" aria-label="文档预览"></article><div id="{ident("tail")}" class="document-tail" tabindex="0" aria-label="在文末继续写作"></div></div>
<div id="{ident("uploads")}" role="status"></div><section id="{ident("comments")}" class="document-comments" aria-label="批注" hidden></section></div>
<dialog id="{ident("dialog")}" class="document-dialog"><div id="{ident("dialog-content")}"></div><div id="{ident("dialog-actions")}" class="document-dialog-actions"></div><button id="{ident("dialog-close")}">关闭</button></dialog>
<script src="/static/document-editor.js" defer></script>'''
