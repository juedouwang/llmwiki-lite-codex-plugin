/* Explicit default-project preference and folder-handle ordering; never changes source files. */
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
  const menu = root.querySelector('#project-context-menu');
  const nameDialog = root.querySelector('#project-name-dialog');
  const removeDialog = root.querySelector('#project-remove-dialog');
  let targetRow = null, menuTrigger = null;
  const nameField = root.querySelector('#project-new-name');
  function closeMenu(focus = false) {
    menu.hidden = true;
    if (focus) menuTrigger?.focus();
  }
  function openMenu(row, x, y, trigger) {
    if (pending || drag) return;
    targetRow = row; menuTrigger = trigger || row.querySelector('.project-more');
    menu.hidden = false;
    const scale = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    const box = menu.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(x, innerWidth - box.width - 8)) / scale + 'px';
    menu.style.top = Math.max(8, Math.min(y, innerHeight - box.height - 8)) / scale + 'px';
    menu.querySelector('button').focus();
  }
  list.addEventListener('contextmenu', event => {
    const row = event.target.closest('.project-row');
    if (!row) return;
    event.preventDefault(); openMenu(row, event.clientX, event.clientY);
  });
  list.addEventListener('click', event => {
    const trigger = event.target.closest('.project-more');
    if (!trigger) return;
    event.preventDefault(); event.stopPropagation();
    const rect = trigger.getBoundingClientRect();
    openMenu(trigger.closest('.project-row'), rect.left, rect.bottom + 4, trigger);
  });
  list.addEventListener('keydown', event => {
    if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
    const row = event.target.closest('.project-row'); if (!row) return;
    event.preventDefault(); const rect = row.getBoundingClientRect();
    openMenu(row, rect.right - 150, rect.bottom, event.target);
  });
  document.addEventListener('pointerdown', event => {
    if (!menu.hidden && !menu.contains(event.target) && !event.target.closest('.project-more')) closeMenu();
  }, {signal: listeners.signal});
  document.addEventListener('keydown', event => {
    if (menu.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeMenu(true); }
    else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); const buttons = [...menu.querySelectorAll('button')];
      const i = buttons.indexOf(document.activeElement), direction = event.key === 'ArrowUp' ? -1 : 1;
      buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (i + direction + buttons.length) % buttons.length].focus();
    } else if (event.key === 'Tab') closeMenu();
  }, {signal: listeners.signal});
  window.addEventListener('resize', () => closeMenu(), {signal: listeners.signal});
  document.addEventListener('scroll', () => closeMenu(), {capture: true, signal: listeners.signal});
  menu.addEventListener('click', event => {
    const action = event.target.closest('[data-project-action]')?.dataset.projectAction;
    if (!action || !targetRow || pending) return;
    closeMenu(); const dialog = action === 'rename' ? nameDialog : removeDialog;
    dialog.querySelector('.project-dialog-status').textContent = '';
    if (action === 'rename') nameField.value = targetRow.dataset.projectName;
    else root.querySelector('#project-remove-name').textContent = targetRow.dataset.projectName;
    dialog.showModal();
    if (action === 'rename') { nameField.focus(); nameField.select(); }
    else dialog.querySelector('[data-project-cancel]').focus();
  });
  for (const dialog of [nameDialog, removeDialog]) {
    dialog.querySelector('[data-project-cancel]').addEventListener('click', () => { if (!pending) dialog.close(); });
    dialog.addEventListener('cancel', event => { if (pending) event.preventDefault(); });
    dialog.addEventListener('close', () => menuTrigger?.focus());
  }
  async function manage(action, payload, dialog) {
    if (pending || !targetRow) return;
    pending = true; root.setAttribute('aria-busy', 'true');
    root.querySelectorAll('button').forEach(button => { button.disabled = true; });
    const status = dialog.querySelector('.project-dialog-status'); status.textContent = '正在保存…';
    let result;
    const previousName = targetRow.dataset.projectName;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(targetRow.dataset.projectId)}/${action}`, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Notebook-Request': '1'}, body: JSON.stringify(payload)});
      result = await response.json();
      if (!response.ok || !result.ok) throw new Error(typeof result.error === 'string' ? result.error : '操作失败，请重试。');
      if (action === 'rename') {
        const name = result.project.name;
        targetRow.dataset.projectName = name; targetRow.querySelector('.row-title').textContent = name;
        for (const [selector, prefix] of [['.project-drag-handle','拖动排序：'],['.project-default','设为默认项目：'],['.project-more','更多操作：']]) {
          targetRow.querySelector(selector).setAttribute('aria-label', prefix + name);
        }
      } else { targetRow.remove(); }
      root.dataset.defaultProject = result.web_default_project_id || '';
      message.textContent = action === 'rename' ? '名称已更新' : '已从列表移除，所有文件均保留';
      status.textContent = ''; dialog.close();
    } catch (error) { result = null; status.textContent = error.message; }
    finally {
      pending = false; root.setAttribute('aria-busy', 'false');
      root.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
    if (result) document.dispatchEvent(new CustomEvent('workbench:projects-changed', {detail: {...result, renamed: action === 'rename' ? {id: result.project.id, previousName} : null}}));
  }
  root.querySelector('#project-name-form').addEventListener('submit', event => {
    event.preventDefault(); const name = nameField.value.trim();
    if (!name) { nameField.setCustomValidity('请输入项目名称'); nameField.reportValidity(); return; }
    void manage('rename', {name}, nameDialog);
  });
  nameField.addEventListener('input', () => nameField.setCustomValidity(''));
  root.querySelector('#project-remove-confirm').addEventListener('click', () => void manage('unregister', {confirm: true}, removeDialog));
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
    if (event.detail?.root?.contains(root) && (pending || drag || nameDialog.open || removeDialog.open)) {
      event.preventDefault(); message.textContent = pending ? '正在保存项目设置，请稍候。' : drag ? '请先结束拖动。' : '请先完成或取消当前操作。';
    }
  }, {signal: listeners.signal});
  window.addEventListener('beforeunload', event => {
    if (root.isConnected && pending) { event.preventDefault(); event.returnValue = ''; }
  }, {signal: listeners.signal});
  document.addEventListener('workbench:dispose', event => {
    if (event.detail?.root?.contains(root)) listeners.abort();
  }, {signal: listeners.signal});
})();
