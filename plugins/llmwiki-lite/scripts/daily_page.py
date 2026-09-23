"""Global daily tasks UI; storage and HTTP routing belong to the server."""
from datetime import date, timedelta
from urllib.parse import urlencode

from llmwiki_core import LLMWikiError
from research_web_ui import esc, layout, new_button, page_header, ui_icon


def page(home: str, context: str | None = None, day: str | None = None) -> str:
    try:
        selected = date.fromisoformat(day) if day else date.today()
        if day and selected.isoformat() != day:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise LLMWikiError("日期必须为有效的 YYYY-MM-DD。") from exc

    def day_url(value: date) -> str:
        return esc("/daily?" + urlencode({"date": value.isoformat(), "context": context or ""}))

    previous = selected - timedelta(days=1) if selected > date.min else selected
    following = selected + timedelta(days=1) if selected < date.max else selected
    body = f'''<link rel="stylesheet" href="/static/daily-tasks.css">
<section id="daily-tasks" data-date="{selected.isoformat()}">
<div id="daily-message" role="status" hidden><span></span> <button id="daily-retry" type="button">重新读取</button></div>
<div id="daily-list-view">
{page_header("每日待办", actions=new_button("新建待办", element_id="daily-new", attributes="disabled"))}
<div class="daily-datebar">
<a class="button" href="{day_url(previous)}" aria-label="前一天">{ui_icon("left")}</a>
<input id="daily-date" type="date" value="{selected.isoformat()}" min="0001-01-01" max="9999-12-31" aria-label="查看日期">
<a class="button" href="{day_url(following)}" aria-label="后一天">{ui_icon("right")}</a>
<span class="daily-dayname">{('周一', '周二', '周三', '周四', '周五', '周六', '周日')[selected.weekday()]}</span>
<a class="button daily-today" href="{day_url(date.today())}">今天</a></div>
<p id="daily-summary" class="daily-summary" aria-live="polite">正在读取待办…</p>
<div class="daily-list-heading"><strong>待完成</strong><span>所有项目</span></div>
<div id="daily-todo" aria-label="当天待办"></div>
<details id="daily-overdue" class="daily-fold" hidden><summary>之前未完成 · <span>0</span></summary>
<p class="daily-hint">保留原定日期；点击“安排到这天”才会调整排期。</p><div></div></details>
<details id="daily-completed" class="daily-fold"><summary>已完成 · <span>0</span></summary><div></div></details>
<noscript><p>请启用 JavaScript 读取和编辑每日待办。</p></noscript>
</div>
<section id="daily-edit-view" class="daily-form-sheet" hidden aria-labelledby="daily-editor-title">
<button id="daily-back" type="button" class="daily-back">{ui_icon("left")}每日待办</button>
<h1 id="daily-editor-title">新建待办</h1>
<p id="daily-task-info" class="daily-hint" hidden></p><div id="daily-task-source" class="daily-hint"></div><div id="daily-delivery-info" class="daily-delivery-info" hidden></div>
<form id="daily-form">
<label for="daily-title">要做什么</label><input id="daily-title" name="title" type="text" maxlength="240" required placeholder="写下一个具体的行动">
<div class="daily-form-grid"><div><label for="daily-scheduled-date">安排日期</label>
<input id="daily-scheduled-date" name="scheduled_date" type="date" min="0001-01-01" max="9999-12-31" required></div>
<div><label for="daily-project">关联项目</label><select id="daily-project" name="project_id"><option value="__workspace__">不关联项目</option></select></div></div>
<label for="daily-estimated-minutes">预计用时（分钟，可选）</label><input id="daily-estimated-minutes" name="estimated_minutes" type="number" min="1" max="1440" step="1" placeholder="例如 60">
<label for="daily-description">描述（可选）</label><textarea id="daily-description" name="description" rows="5" maxlength="100000" placeholder="做到什么程度算完成"></textarea>
<p id="daily-error" role="alert" hidden></p><button id="daily-reload" type="button" hidden>读取最新版本，保留当前填写</button>
<p class="daily-hint">助手执行不等于完成，由你验收后勾选。项目仅表示待办归属，不限制每日列表。</p>
<div class="daily-actions"><button id="daily-delete" class="daily-danger" type="button" hidden>删除待办</button>
<button id="daily-reject" type="button" hidden>退回修改</button><button id="daily-accept" type="button" hidden>确认完成</button><span></span><button id="daily-cancel" type="button">取消</button>
<button id="daily-save" type="submit" class="primary">添加待办</button></div>
</form></section></section>
<script src="/static/document-editor.js" defer></script><script src="/static/daily-tasks.js" defer></script>'''
    return layout("每日待办", body, project_id=context, active="daily", home=home, daily_date=selected.isoformat())
