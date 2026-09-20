"""Project-bound Git content inside the shared research workbench shell."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from llmwiki_registry import get_project
from research_web_ui import esc, layout, ui_icon


def page(home: str, project_id: str, params: Any = None) -> str:
    """Render only the code workspace; repository reads belong to its JSON API."""
    project = get_project(project_id, home=home)["project"]
    project_id = str(project["id"])
    api = f"/api/project/{quote(project_id, safe='')}/code"
    body = f'''
<link rel="stylesheet" href="/static/code.css">
<section id="code-app" class="code-app" data-project-id="{esc(project_id)}"
         data-api="{esc(api)}" aria-label="代码版本管理">
  <div class="code-toolbar" aria-label="版本操作">
    <details id="code-branches" class="code-branch-picker">
      <summary aria-label="切换或新建分支">{ui_icon("git")}
        <span id="code-branch-label">读取分支…</span>{ui_icon("chevron")}</summary>
      <div id="code-branch-menu" class="code-branch-menu" aria-label="本地分支"></div>
    </details>
    <button id="code-merge" type="button" data-action="merge" disabled>{ui_icon("merge")}合并</button>
    <span class="code-spacer"></span>
    <button id="code-pull" type="button" data-action="pull" disabled>{ui_icon("download")}pull</button>
    <button id="code-push" type="button" data-action="push" disabled>{ui_icon("upload")}push
      <span id="code-push-count" class="code-tag" hidden></span></button>
  </div>
  <p id="code-capability" class="code-notice" role="status" hidden></p>
  <section class="code-changes" aria-label="工作区状态">
    <span class="code-change-icon" aria-hidden="true">{ui_icon("files")}</span>
    <div><strong id="code-change-title">正在读取工作区…</strong>
      <small id="code-change-note">只读取本地状态，不自动拉取或保存。</small></div>
    <button id="code-save" type="button" class="code-primary" data-action="save" disabled>查看并保存</button>
  </section>
  <div id="code-workspace" class="code-workspace code-no-detail">
    <section class="code-history" aria-label="版本记录">
      <div class="code-section-heading"><h2>版本记录</h2><span class="code-muted">全部分支</span>
        <button id="code-refresh" type="button" data-action="refresh" aria-label="刷新版本记录" title="刷新版本记录">{ui_icon("refresh")}</button></div>
      <div id="code-graph-scroll" class="code-graph-scroll" tabindex="0" aria-label="版本图，可横向滚动">
        <div id="code-graph" class="code-graph" aria-label="真实提交父子关系；外圈表示选中版本，当前分支标记当前 HEAD，虚线连接未加载父版本"></div>
      </div>
      <p id="code-graph-empty" class="code-empty">正在读取版本…</p>
      <button id="code-graph-more" type="button" data-action="graph-more" hidden>加载更早版本</button>
    </section>
    <aside id="code-detail" class="code-detail" aria-label="版本详情" hidden></aside>
  </div>
  <section id="code-conflicts" class="code-conflicts" aria-label="合并冲突处理" hidden></section>
  <footer class="code-footer">
    <span id="code-feedback" role="status" aria-live="polite" aria-atomic="true">本地状态；操作需手动确认。</span>
    <span id="code-cache" class="code-muted"></span>
  </footer>
  <dialog id="code-dialog" class="code-dialog" aria-labelledby="code-dialog-title">
    <div class="code-dialog-heading"><h2 id="code-dialog-title"></h2>
      <button id="code-dialog-close" type="button" aria-label="关闭对话框">{ui_icon("close")}</button></div>
    <div id="code-dialog-body"></div>
    <p id="code-dialog-feedback" role="status" aria-live="polite" aria-atomic="true"></p>
    <div id="code-dialog-actions" class="code-dialog-actions"></div>
  </dialog>
  <noscript><p class="code-notice">请启用 JavaScript 以读取真实仓库和操作版本；本页不会自动修改文件。</p></noscript>
</section>
<script src="/static/code.js" defer></script>
'''
    return layout("代码", body, project_id=project_id, active="code", home=home)
