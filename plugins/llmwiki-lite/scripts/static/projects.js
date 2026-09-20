/* Explicit startup preference and folder-handle ordering; never changes source files. */
(() => {
  'use strict';
  const root = document.getElementById('project-manager');
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = 'true';
  const list = root.querySelector('.project-list');
  if (!list) return;
  const message = root.querySelector('#project-preferences-status');
  const listeners = new AbortController();
  let pending = false, drag = null;
  const rows = () => [...list.querySelectorAll('.project-row')];
  const order = () => rows().map(row => row.dataset.projectId);
  function restore(ids) {
    const indexed = new Map(rows().map(row => [row.dataset.projectId, row]));
    ids.forEach(id => { if (indexed.has(id)) list.append(indexed.get(id)); });
  }
  async function save(payload, previousOrder = null) {
    if (pending) return;
    pending = true; root.setAttribute('aria-busy', 'true');
    root.querySelectorAll('button').forEach(button => { button.disabled = true; });
    message.textContent = '正在保存…';
    try {
      const response = await fetch('/api/projects/preferences', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Notebook-Request': '1'}, body: JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(typeof result.error === 'string' ? result.error : '保存失败，请重试。');
      root.dataset.defaultProject = result.web_default_project_id || '';
      restore(result.project_order);
      rows().forEach(row => row.querySelector('.project-default').setAttribute('aria-pressed', String(row.dataset.projectId === root.dataset.defaultProject)));
      message.textContent = '已保存';
      document.dispatchEvent(new CustomEvent('workbench:project-preferences', {detail: result}));
    } catch (error) {
      if (previousOrder) restore(previousOrder);
      message.textContent = '未保存：' + error.message;
    } finally {
      pending = false; root.setAttribute('aria-busy', 'false');
      root.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
  }
  root.addEventListener('click', event => {
    const button = event.target.closest('.project-default');
    if (!button || pending || drag) return;
    const id = button.closest('.project-row').dataset.projectId;
    void save({default_project_id: id === root.dataset.defaultProject ? null : id});
  });
  list.addEventListener('pointerdown', event => {
    const handle = event.target.closest('.project-drag-handle');
    if (!handle || event.button !== 0 || pending || drag) return;
    event.preventDefault(); handle.focus();
    drag = {handle, row: handle.closest('.project-row'), id: event.pointerId,
      startX: event.clientX, startY: event.clientY, before: order(), moved: false};
    handle.setPointerCapture(event.pointerId);
  });
  list.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 6) return;
    event.preventDefault(); drag.moved = true;
    drag.row.classList.add('is-dragging');
    const next = rows().filter(row => row !== drag.row).find(row => {
      const box = row.getBoundingClientRect(); return event.clientY < box.top + box.height / 2;
    });
    list.insertBefore(drag.row, next || null);
    // Pointer capture keeps the same handle active during scrolling and DOM moves.
    if (!drag.handle.hasPointerCapture(drag.id)) drag.handle.setPointerCapture(drag.id);
    if (event.clientY < 50) window.scrollBy(0, -18);
    else if (event.clientY > innerHeight - 50) window.scrollBy(0, 18);
  });
  function finish(cancel = false) {
    if (!drag) return;
    const previous = drag; drag = null;
    previous.row.classList.remove('is-dragging');
    if (previous.handle.hasPointerCapture(previous.id)) previous.handle.releasePointerCapture(previous.id);
    if (cancel) { restore(previous.before); return; }
    if (previous.moved && JSON.stringify(previous.before) !== JSON.stringify(order())) {
      void save({project_order: order()}, previous.before);
    }
  }
  list.addEventListener('pointerup', () => finish());
  list.addEventListener('pointercancel', () => finish(true));
  list.addEventListener('lostpointercapture', () => { if (drag) finish(true); });
  list.addEventListener('keydown', event => {
    if (event.key === 'Escape' && drag) { event.preventDefault(); finish(true); return; }
    const handle = event.target.closest('.project-drag-handle');
    if (!handle || pending || drag || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const row = handle.closest('.project-row'), before = order();
    if (event.key === 'ArrowUp' && row.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
    else if (event.key === 'ArrowDown' && row.nextElementSibling) list.insertBefore(row.nextElementSibling, row);
    else return;
    void save({project_order: order()}, before).then(() => handle.focus());
  });
  document.addEventListener('workbench:before-leave', event => {
    if (event.detail?.root?.contains(root) && (pending || drag)) {
      event.preventDefault(); message.textContent = pending ? '正在保存项目设置，请稍候。' : '请先结束拖动。';
    }
  }, {signal: listeners.signal});
  window.addEventListener('beforeunload', event => {
    if (root.isConnected && pending) { event.preventDefault(); event.returnValue = ''; }
  }, {signal: listeners.signal});
  document.addEventListener('workbench:dispose', event => {
    if (event.detail?.root?.contains(root)) listeners.abort();
  }, {signal: listeners.signal});
})();
