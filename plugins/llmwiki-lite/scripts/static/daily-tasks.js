/* Global daily tasks: server-owned dates, identity and revisions; no browser task store. */
(() => {
  'use strict';
  const page = document.getElementById('main-content'), root = page?.querySelector('#daily-tasks');
  if (!root) return;
  const lifecycle = new AbortController(), active = () => !lifecycle.signal.aborted && page.isConnected;
  const owns = event => event.detail?.root === page, $ = selector => root.querySelector(selector);
  const day = root.dataset.date, endpoint = '/api/daily-tasks', form = $('#daily-form');
  const field = name => form.elements.namedItem(name), fields = ['title', 'scheduled_date', 'description', 'project_id', 'estimated_minutes'];
  const labels = {title:'要做什么', scheduled_date:'安排日期', description:'补充说明', estimated_minutes:'预计用时'};
  const node = (tag, cls = '', text) => { const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const button = (text, run, cls = '') => { const n = node('button', cls, text); n.type = 'button'; n.addEventListener('click', run); return n; };
  const done = task => task.status === 'done';
  const pending = task => !done(task) && (task.review_state === 'pending' || task.status === 'pending');
  const owner = task => task?.project_id || '__workspace__';
  const sourceUrl = task => `/project/${encodeURIComponent(owner(task))}/todos?task=${encodeURIComponent(task.parent_id || task.id)}`;
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
  const recordUrl = (task, record) => { const id = record?.id || ''; if (!id || owner(task) === '__workspace__') return ''; const normalized = id.replace(/^records\//, '').replace(/^\/+/, ''); return `/project/${encodeURIComponent(owner(task))}/records/${normalized.split('/').map(encodeURIComponent).join('/')}`; };
  const sourceIcon = () => { const svg = document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true'); svg.classList.add('daily-source-icon'); const path = document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d','M7 17 17 7M7 7h10v10'); svg.append(path); return svg; };
  let data = {tasks:[], overdue:[], completed:[], projects:[], revisions:{}}, ready = false, busy = false, loading = null;
  let sequence = 0, editing = null, baseline = null, draftRevision, conflict = false;
  const editorOpen = () => !$('#daily-edit-view').hidden;
  const values = () => Object.fromEntries(fields.map(key => [key, field(key).value]));
  const changed = () => editorOpen() && baseline && fields.some(key => field(key).value !== baseline[key]);
  const unsaved = () => busy || changed();
  function message(text, retry = false) {
    $('#daily-message span').textContent = text; $('#daily-message').hidden = !text; $('#daily-retry').hidden = !retry;
  }
  function error(text) { $('#daily-error').textContent = text; $('#daily-error').hidden = !text; }
  async function request(payload) {
    if (!window.ResearchDocument?.request) throw new Error('请求组件未载入，请刷新重试。');
    return window.ResearchDocument.request(payload === undefined ? endpoint + '?date=' + encodeURIComponent(day) : endpoint, payload);
  }
  function revisionFor(task, projectId = owner(task)) {
    const revision = task?.revision ?? data.revisions[projectId];
    if (typeof revision !== 'string') throw new Error('未取得任务版本，请重新读取列表后重试。');
    return revision;
  }
  function source(task, container) {
    if (owner(task) === '__workspace__') { container.append(node('span', '', '临时待办')); return; }
    const link = node('a', '', task.parent_id ? (task.parent_title || '查看来源任务') : '查看科研进度');
    link.href = sourceUrl(task); link.append(sourceIcon()); container.append(link);
  }
  function row(task, overdue = false) {
    const item = node('div', 'daily-task' + (done(task) ? ' daily-done' : ''));
    item.dataset.taskId = task.id; item.dataset.projectId = owner(task);
    const check = node('input'); check.type = 'checkbox'; check.checked = done(task); check.defaultChecked = done(task);
    check.setAttribute('aria-label', (done(task) ? '取消完成：' : '验收完成：') + task.title);
    check.addEventListener('change', () => { check.checked = done(task); void mutate(done(task) ? 'restore' : 'accept', task); });
    const main = node('div', 'daily-task-main'), meta = node('div', 'daily-task-meta');
    main.append(button(task.title, () => openEditor(task), 'daily-task-title'));
    meta.append(node('span', '', owner(task) === '__workspace__' ? '不关联项目' : task.project_name || owner(task)), node('span', '', '·'));
    source(task, meta);
    if (pending(task)) meta.append(node('span', 'daily-review', '待你验收'));
    if (overdue) meta.append(node('span', '', '原定 ' + task.scheduled_date));
    main.append(meta); item.append(check, main);
    if (task.estimated_minutes) item.append(node('span', 'daily-duration', task.estimated_minutes + ' 分钟'));
    if (overdue) item.append(button('安排到这天', () => mutate('update', task, {scheduled_date:day}), 'daily-reschedule'));
    return item;
  }
  function render() {
    $('#daily-new').disabled = !ready || busy;
    $('#daily-summary').textContent = `${day === todayIso() ? '今天' : day} 还有 ${data.tasks.length} 项待办 · 已完成 ${data.completed.length} 项`;
    const empty = node('div', 'daily-empty', '这一天还没有待办。');
    empty.append(button('添加一件要做的事', () => openEditor()));
    $('#daily-todo').replaceChildren(...(data.tasks.length ? data.tasks.map(task => row(task)) : [empty]));
    for (const [key, list] of [['overdue', data.overdue], ['completed', data.completed]]) {
      const section = $('#daily-' + key); section.querySelector('summary span').textContent = String(list.length);
      section.querySelector('div').replaceChildren(...(list.length ? list.map(task => row(task, key === 'overdue')) : [node('p', 'daily-hint', '完成的事项会保留在原定日期。')]));
      if (key === 'overdue') section.hidden = !list.length;
    }
  }
  async function load(force = false) {
    if (loading && !force) return loading;
    const token = ++sequence;
    const operation = (async () => {
      const result = await request();
      if (!active() || token !== sequence) return;
      if (result.date !== day || !['tasks', 'overdue', 'completed', 'projects'].every(key => Array.isArray(result[key])) || !result.revisions) {
        throw new Error('每日待办响应不完整，请检查服务接口。');
      }
      data = result; ready = true; render();
    })();
    loading = operation;
    try { await operation; } finally { if (loading === operation) loading = null; }
  }
  function fillProjects(selected) {
    const list = [{id:'__workspace__', name:'不关联项目'}, ...data.projects];
    if (!list.some(project => project.id === selected)) list.push({id:selected, name:editing?.project_name || '原项目（保留归属）'});
    field('project_id').replaceChildren(...list.map(project => { const option = node('option', '', project.name); option.value = project.id; return option; }));
    field('project_id').value = selected;
    // Moving an existing task to another owner is deliberately not a client-side copy/delete.
    field('project_id').disabled = !!editing;
  }
  function showInfo(task) {
    const info = $('#daily-task-info'); info.hidden = !task;
    info.textContent = task ? `${task.scheduled_date} · ${done(task) ? '已完成' : pending(task) ? '待你验收' : '待完成'}` : '';
    const sourceBox = $('#daily-task-source'); sourceBox.replaceChildren(); if (task) source(task, sourceBox);
    const delivery = $('#daily-delivery-info'); delivery.replaceChildren(); delivery.hidden = !task || !(task.delivery_summary || task.completion_record || task.acceptance_record);
    if (!delivery.hidden) {
      if (task.delivery_summary) delivery.append(node('p', 'daily-delivery-summary', '助手交付：' + task.delivery_summary));
      for (const [label, record] of [['交付记录', task.completion_record], ['验收记录', task.acceptance_record]]) {
        if (!record?.id) continue;
        const line = node('p', 'daily-delivery-record', label + '：');
        const href = recordUrl(task, record);
        if (href) { const link = node('a', '', record.title || record.id); link.href = href; line.append(link); }
        else { line.append(node('span', '', record.title || record.id)); if (record.summary) line.append(node('span', 'daily-record-summary', record.summary)); }
        delivery.append(line);
      }
    }
  }
  function openEditor(task = null) {
    if (!ready || busy) return;
    try { draftRevision = revisionFor(task); } catch (e) { message(e.message, true); return; }
    editing = task; conflict = false;
    baseline = {title:task?.title || '', scheduled_date:task?.scheduled_date || day, description:task?.description || '', project_id:owner(task), estimated_minutes:String(task?.estimated_minutes ?? '')};
    fillProjects(baseline.project_id); fields.forEach(key => { field(key).value = baseline[key]; });
    $('#daily-editor-title').textContent = task ? '待办详情' : '新建待办';
    $('#daily-save').textContent = task ? '保存修改' : '添加待办'; $('#daily-save').disabled = false;
    $('#daily-delete').hidden = $('#daily-accept').hidden = !task;
    $('#daily-accept').textContent = done(task || {}) ? '取消完成' : '确认完成';
    $('#daily-reload').hidden = true; error(''); showInfo(task);
    $('#daily-list-view').hidden = true; $('#daily-edit-view').hidden = false; field('title').focus();
  }
  function closeEditor(force = false) {
    if (!force && busy) { error('请等待当前操作完成。'); return false; }
    if (!force && changed() && !confirm('尚未保存，放弃这次修改吗？')) return false;
    $('#daily-edit-view').hidden = true; $('#daily-list-view').hidden = false; editing = baseline = null; conflict = false;
    $('#daily-new').focus(); return true;
  }
  async function mutate(action, task = null, patch, baseRevision) {
    if (busy || !ready) return;
    if (action !== 'delete' && action !== 'create' && !patch && changed()) { error('请先保存当前填写，再验收或恢复待办。'); return; }
    const projectId = task ? owner(task) : field('project_id').value;
    let revision;
    try { revision = baseRevision ?? revisionFor(task, projectId); } catch (e) { editorOpen() ? error(e.message) : message(e.message, true); return; }
    busy = true; ++sequence;
    const controls = [...root.querySelectorAll('button,input,textarea,select')].map(control => [control, control.disabled]);
    controls.forEach(([control]) => { control.disabled = true; }); root.setAttribute('aria-busy', 'true'); error('');
    try {
      await request({action, project_id:projectId, revision, ...(task ? {id:task.id} : {}), ...(patch ? {task:patch} : {})});
      if (!active()) return;
      if (editorOpen()) closeEditor(true);
      message('');
      try { await load(true); } catch (e) { message('操作已保存，但列表刷新失败：' + e.message, true); }
      document.dispatchEvent(new CustomEvent('workbench:daily-tasks-changed', {detail:{project_id:projectId}}));
    } catch (e) {
      if (!active()) return;
      if (editorOpen()) {
        error(e.message);
        if (e.status === 409) { conflict = true; $('#daily-reload').hidden = false; }
      } else {
        message(e.status === 409 ? '任务已变化，请核对最新列表后再操作。' : e.message, true);
        if (e.status === 409) await load(true).catch(() => {});
      }
    } finally {
      busy = false; controls.forEach(([control, disabled]) => { control.disabled = disabled; });
      $('#daily-save').disabled = conflict; $('#daily-new').disabled = !ready; root.removeAttribute('aria-busy');
    }
  }
  async function reloadDraft() {
    if (busy || !baseline) return;
    const current = values(), old = {...baseline}, target = editing;
    try {
      await load(true); if (!active() || !editorOpen() || editing !== target) return;
      const latest = target && [...data.tasks, ...data.overdue, ...data.completed].find(task => task.id === target.id && owner(task) === owner(target));
      if (target && !latest) throw new Error('待办已删除或改期，当前填写保留。请取消后到对应日期核对，不会覆盖。');
      const next = latest ? {title:latest.title, scheduled_date:latest.scheduled_date, description:latest.description || '', project_id:owner(latest), estimated_minutes:String(latest.estimated_minutes ?? '')} : current;
      const collisions = [];
      for (const key of fields) {
        if (current[key] === old[key]) current[key] = next[key];
        else if (next[key] !== old[key] && current[key] !== next[key]) collisions.push(labels[key] || key);
      }
      baseline = next; editing = latest || null; draftRevision = revisionFor(latest, current.project_id);
      fields.forEach(key => { field(key).value = current[key]; }); showInfo(latest);
      conflict = false; $('#daily-save').disabled = false; $('#daily-reload').hidden = true;
      error(collisions.length ? '双方修改了' + collisions.join('、') + '。保留了你的填写，请核对后保存。' : '已读取最新版本，当前填写保留，请核对后保存。');
    } catch (e) { error(e.message); }
  }
  async function refresh() {
    if (!active() || document.hidden || busy) return;
    try { await load(); } catch (e) { if (active()) message('每日待办未载入：' + e.message, true); }
  }
  $('#daily-new').addEventListener('click', () => openEditor());
  $('#daily-back').addEventListener('click', () => closeEditor());
  $('#daily-cancel').addEventListener('click', () => closeEditor());
  $('#daily-retry').addEventListener('click', async () => { message(''); await refresh(); });
  $('#daily-reload').addEventListener('click', reloadDraft);
  field('project_id').addEventListener('change', () => { if (!editing) { try { draftRevision = revisionFor(null, field('project_id').value); } catch (e) { error(e.message); } } });
  form.addEventListener('submit', event => {
    event.preventDefault(); if (conflict || busy || !form.reportValidity()) return;
    if (!field('title').value.trim()) { error('请填写具体的待办名称。'); field('title').focus(); return; }
    const current = values(), patch = Object.fromEntries(['title','scheduled_date','description','estimated_minutes'].filter(key => !editing || current[key] !== baseline[key]).map(key => [key, current[key]]));
    if (Object.hasOwn(patch, 'estimated_minutes')) patch.estimated_minutes = patch.estimated_minutes === '' ? null : Number(patch.estimated_minutes);
    if (editing && Object.hasOwn(editing, 'parent_id')) patch.parent_id = editing.parent_id;
    void mutate(editing ? 'update' : 'create', editing, patch, draftRevision);
  });
  $('#daily-accept').addEventListener('click', () => { if (editing) void mutate(done(editing) ? 'restore' : 'accept', editing, undefined, draftRevision); });
  $('#daily-delete').addEventListener('click', () => {
    if (!busy && editing && confirm('确定删除此待办？当前未保存的填写将被放弃，不会删除关联项目或记录。')) void mutate('delete', editing, undefined, draftRevision);
  });
  $('#daily-date').addEventListener('change', event => {
    const date = event.target.value; event.target.value = day;
    if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return;
    const url = new URL('/daily', location.origin); url.searchParams.set('date', date); url.searchParams.set('context', document.body.dataset.workbenchProject || '');
    const link = node('a'); link.href = url.href; link.hidden = true; root.append(link); link.click(); link.remove();
  });
  document.addEventListener('workbench:before-leave', event => {
    if (owns(event) && active() && unsaved()) { event.preventDefault(); editorOpen() ? error('尚未保存，请先保存或取消当前修改。') : message('请等待当前操作完成。'); }
  }, {signal:lifecycle.signal});
  document.addEventListener('workbench:leave', event => { if (owns(event)) { ++sequence; loading = null; if (editorOpen()) closeEditor(true); } }, {signal:lifecycle.signal});
  document.addEventListener('workbench:enter', event => { if (owns(event)) void refresh(); }, {signal:lifecycle.signal});
  document.addEventListener('workbench:dispose', event => { if (owns(event)) { ++sequence; loading = null; lifecycle.abort(); } }, {signal:lifecycle.signal});
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); }, {signal:lifecycle.signal});
  window.addEventListener('beforeunload', event => { if (active() && unsaved()) { event.preventDefault(); event.returnValue = ''; } }, {signal:lifecycle.signal});
  void refresh();
})();
