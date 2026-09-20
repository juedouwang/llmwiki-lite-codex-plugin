/* Only explicit, version-checked manual-note removals. Never touch assistant records. */
(() => {
  'use strict';
  const root = document.getElementById('research-records');
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = 'true';
  const dialog = root.querySelector('#record-delete-dialog'), confirm = root.querySelector('[data-confirm-delete]');
  const feedback = root.querySelector('.record-feedback'), undo = root.querySelector('[data-undo-delete]');
  const error = root.querySelector('[data-delete-error]'), lifecycle = new AbortController();
  let selected = null, busy = false;
  const removed = [];
  async function request(id, action, revision) {
    const response = await fetch('/api/project/' + encodeURIComponent(root.dataset.project) + '/notebook/' + id + '/' + action, {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Notebook-Request': '1'}, body: JSON.stringify({revision})
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || '操作失败，请稍后重试。');
    return result;
  }
  root.addEventListener('click', event => {
    const button = event.target.closest('[data-delete-note]');
    if (!button || busy) return;
    selected = {id: button.dataset.deleteNote, revision: button.dataset.revision, row: button.closest('.rw-list-row'), button};
    root.querySelector('[data-delete-title]').textContent = selected.row.querySelector('.rw-title-button').textContent;
    error.textContent = ''; dialog.showModal(); dialog.querySelector('[value=cancel]').focus();
  });
  function pending(value) {
    busy = value; confirm.disabled = value; undo.disabled = value;
    dialog.querySelector('[value=cancel]').disabled = value;
  }
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  confirm.addEventListener('click', async () => {
    if (!selected || busy) return;
    pending(true); error.textContent = '';
    const item = selected;
    try {
      await request(item.id, 'delete', item.revision);
      // Retain the actual row, including its position, so undo also restores the view.
      item.anchor = document.createComment('removed-note'); item.row.replaceWith(item.anchor);
      removed.push(item); selected = null; dialog.close();
      feedback.hidden = false; feedback.querySelector('span').textContent = '笔记已删除。'; undo.hidden = false; undo.focus();
    } catch (e) { error.textContent = e.message; }
    finally { pending(false); }
  });
  undo.addEventListener('click', async () => {
    if (busy || !removed.length) return;
    const item = removed.at(-1); pending(true);
    try {
      await request(item.id, 'undo-delete', item.revision);
      item.anchor.replaceWith(item.row); removed.pop();
      feedback.querySelector('span').textContent = removed.length ? '笔记已恢复，仍有已删除的笔记可撤销。' : '笔记已恢复。';
      undo.hidden = !removed.length; item.button.focus();
    } catch (e) { feedback.querySelector('span').textContent = e.message; }
    finally { pending(false); }
  });
  const owns = event => event.detail?.root?.contains(root);
  document.addEventListener('workbench:before-leave', event => { if (owns(event) && busy) event.preventDefault(); }, {signal: lifecycle.signal});
  document.addEventListener('workbench:leave', event => { if (owns(event)) dialog.close(); }, {signal: lifecycle.signal});
  document.addEventListener('workbench:dispose', event => { if (owns(event)) lifecycle.abort(); }, {signal: lifecycle.signal});
  window.addEventListener('beforeunload', event => { if (root.isConnected && busy) { event.preventDefault(); event.returnValue = ''; } }, {signal: lifecycle.signal});
})();
