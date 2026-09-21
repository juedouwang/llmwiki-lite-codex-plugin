"""Research task page. No shared navigation or server ownership."""
from llmwiki_registry import get_project
from research_web_ui import esc, layout, new_button, page_header, ui_icon


def page(home: str, project_id: str) -> str:
    from research_progress import active_tasks, task_description

    project = get_project(project_id, home=home)["project"]
    # Keep meaningful, escaped server-rendered context before JS loads.
    resume = "".join(
        f'<li><a href="/project/{esc(project_id)}/todos#task-{task["id"]}">{esc(task["title"])}</a>'
        f'<p>{esc(task_description(task))}</p>'
        + (f'<span class="meta">关联笔记：{esc(task["record_id"])}</span>' if task.get("record_id") else "")
        + '</li>' for task in active_tasks(project)
    )
    body = f'''<link rel="stylesheet" href="/static/progress.css">
<section id="research-progress" data-project="{esc(project_id)}">
{page_header("科研进度", actions=new_button("新建任务", element_id="progress-new"))}
<div id="progress-message" role="status" hidden></div>
<section id="progress-resume" class="progress-resume" {"" if resume else "hidden"}><h2>继续上次</h2><ul>{resume}</ul></section>
<div class="progress-toolbar"><div role="group" aria-label="任务视图" class="progress-tabs">
<button id="progress-todo-tab" type="button" aria-pressed="true">Todo <span></span></button>
<button id="progress-done-tab" type="button" aria-pressed="false">Done <span></span></button></div>
<label>优先级 <select id="progress-priority-filter"><option value="">全部优先级</option><option value="high">高优先级</option><option value="medium">中优先级</option><option value="low">低优先级</option></select></label></div>
<section id="progress-todo" aria-label="待办任务"><div></div></section>
<section id="progress-done" aria-label="已完成任务" hidden><div></div></section>
<details id="progress-schedule" open><summary>DDL 排期</summary>
<div class="progress-range"><span id="progress-range-label" class="meta"></span><div class="progress-week-controls">
<button id="progress-prev" type="button" aria-label="上一段时间">{ui_icon("left")}</button><button id="progress-today" type="button">本周</button><button id="progress-next" type="button" aria-label="下一段时间">{ui_icon("right")}</button>
<select id="progress-days" aria-label="时间范围"><option value="7">一周</option><option value="14">两周</option><option value="28">四周</option></select></div></div>
<div id="progress-timeline" aria-label="截止日排期" tabindex="0"></div></details>
<dialog id="progress-dialog"><form id="progress-form">
<div class="progress-dialog-head"><h2 id="progress-dialog-title">任务</h2><button type="button" id="progress-close" aria-label="关闭任务">×</button></div>
<label>任务名称<input type="text" name="title" maxlength="240" required autocomplete="off"></label>
<div class="progress-task-options"><label>优先级<select name="priority"><option value="high">高优先级</option><option value="medium" selected>中优先级</option><option value="low">低优先级</option></select></label>
<label>截止日 DDL（可选）<input name="ddl" type="date" aria-label="截止日 DDL"></label></div>
<div class="progress-date-shortcuts" role="group" aria-label="快捷选择截止日"><button type="button" data-ddl="0">今天</button><button type="button" data-ddl="1">明天</button><button type="button" data-ddl="7">一周后</button><button type="button" data-ddl="">清除日期</button></div>
<label for="progress-description">任务描述</label>
<textarea id="progress-description" name="description" rows="6" maxlength="100000" placeholder="记录进展和下一步；支持 Markdown、Ctrl+V 粘贴截图" spellcheck="false"></textarea>
<div id="progress-uploads" role="status"></div>
<details id="progress-description-preview" open><summary>描述预览</summary><article id="progress-preview" class="prose" aria-label="任务描述预览"></article></details>
<label>关联笔记<select name="record_id"><option value="">不关联</option></select></label><div id="progress-source"></div>
<details id="progress-legacy"><summary>原始记录与助手上下文（只读）</summary><pre></pre></details>
<details id="progress-history"><summary>修改记录</summary><div></div></details>
<p id="progress-error" role="alert" hidden></p><button type="button" id="progress-reload" hidden>读取最新版本，保留当前填写</button>
<div class="progress-dialog-actions"><button type="button" id="progress-delete" class="progress-danger">删除任务</button><button type="button" id="progress-complete">完成任务</button><span class="rw-spacer"></span><button type="submit" class="primary">保存</button></div>
</form></dialog>
<div class="progress-more"><button id="progress-import" type="button">导入旧待办</button></div>
<dialog id="progress-import-dialog"><h2>导入旧待办</h2><p>仅导入所选事项；旧完成标记需单独确认。</p><form id="progress-import-form"><div id="progress-candidates"></div><label><input type="checkbox" id="progress-import-completed">同时采用此浏览器明确保存的旧完成标记</label><p id="progress-import-error" role="alert" hidden></p><div class="actions"><button type="button" id="progress-import-close">取消</button><button class="primary">导入所选</button></div></form></dialog>
</section><script src="/static/document-editor.js" defer></script><script src="/static/progress.js" defer></script>'''
    return layout("科研进度 · " + str(project["name"]), body, project_id=project_id, active="todos", home=home)
