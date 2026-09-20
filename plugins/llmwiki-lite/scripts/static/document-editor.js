/* Shared body interaction only; adapters retain document identity and storage. */
window.ResearchDocument = (() => {
  'use strict';
  async function request(url, data) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, data === undefined ? {signal: controller.signal} : {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Notebook-Request': '1'},
        body: JSON.stringify(data), signal: controller.signal
      });
      const value = await response.json();
      if (!response.ok || value.ok === false) {
        const error = new Error(value.error?.message || value.error || '请求失败');
        error.status = response.status; error.code = value.error?.code; throw error;
      }
      return value;
    } finally { clearTimeout(timeout); }
  }
  const node = (text, tag = 'p') => { const n = document.createElement(tag); n.textContent = text; return n; };
  const clone = value => JSON.parse(JSON.stringify(value));
  function mount(options) {
    const root = typeof options.root === 'string' ? document.getElementById(options.root) : options.root;
    const $ = name => root.querySelector(`#${options.prefix}-${name}`);
    const page = root.closest('#main-content') || root, lifecycle = new AbortController();
    const active = () => !lifecycle.signal.aborted && root.isConnected;
    const owns = event => event.detail?.root === page;
    let pendingActions = 0, deferredModal, dialogBaseline = '', allowUnload = false;
    const dialogValue = () => JSON.stringify([...$('dialog').querySelectorAll('input,textarea,select')].map(e => [e.value, e.checked]));
    const dialogDirty = () => $('dialog').open && dialogValue() !== dialogBaseline;
    const source = $('source'), preview = $('preview'), surface = $('document'), title = $('title');
    const recoveryKey = options.key + ':' + crypto.randomUUID();
    let item, body = '', comments = [], tags = [], mode = 'preview', sequence = 0, saved = 0;
    let saveJob, editJob, timer, composing = false, renderSequence = 0, selection = [0, 0], scroll = {edit: 0, preview: 0};
    let ready = false, busy = false, conflictOpen = false, undo = [], redo = [], remembered = '', selectedQuote = '', nativeBase = '', nativeTip = '', nativeSelection = [0, 0];
    const uploads = new Map();
    const message = text => { $('notice').textContent = text; $('notice').hidden = !text; };
    const close = () => { $('dialog').close(); conflictOpen = false; };
    function modal(heading, content, actions = []) {
      if (lifecycle.signal.aborted) return;
      if (!active()) { deferredModal = [heading, content, actions]; return; }
      $('dialog-content').replaceChildren(node(heading, 'h2'), ...content);
      $('dialog-actions').replaceChildren();
      for (const [label, run] of actions) {
        const b = node(label, 'button'); b.type = 'button';
        b.onclick = async () => { pendingActions++; b.disabled = true; try { await run(); } catch (e) { message(e.message); } finally { pendingActions--; b.disabled = false; } };
        $('dialog-actions').append(b);
      }
      root.querySelectorAll('details[open]').forEach(e => { e.open = false; });
      dialogBaseline = dialogValue();
      if (!$('dialog').open) $('dialog').showModal();
    }
    $('dialog-close').onclick = close;
    $('dialog').addEventListener('cancel', () => { conflictOpen = false; });
    function content() { return {body, comments: clone(comments), title: title?.value || '', tags: [...tags]}; }
    function recover() {
      try { localStorage.setItem(recoveryKey, JSON.stringify({...content(), base_revision: item?.revision, updated_at: Date.now()})); }
      catch { message('本机恢复稿不可用，请保持页面打开直到保存成功。'); }
    }
    function height() { if (!active()) return; source.style.height = 'auto'; source.style.height = Math.max(300, source.scrollHeight) + 'px'; }
    window.addEventListener('resize', () => { if (active()) requestAnimationFrame(height); }, {signal: lifecycle.signal});
    function state() {
      const readonly = !ready || !item || item.mode === 'readonly';
      for (const name of ['preview-mode','export','copy','info','history','sources','candidate','regenerate','save-as']) if ($(name)) $(name).disabled = !ready || !item || busy;
      for (const name of ['export','copy','save-as']) if ($(name)) $(name).disabled ||= uploads.size > 0 || body.includes('<!--report-upload:');
      $('edit').disabled = readonly || busy; $('code').disabled = readonly || busy; $('comment').disabled = readonly || busy;
      source.readOnly = readonly || busy;
      if (title) title.readOnly = readonly || busy;
      $('edit').setAttribute('aria-pressed', String(mode === 'edit'));
      $('preview-mode').setAttribute('aria-pressed', String(mode === 'preview'));
      if ($('save')) $('save').disabled = readonly || busy;
      if ($('confirm')) {
        $('confirm').textContent = item?.mode === 'formal' ? '修改正式版' : '确认为正式版';
        $('confirm').disabled = busy || uploads.size > 0 || body.includes('<!--report-upload:') || readonly;
      }
      $('state').textContent = options.label?.(item) || '';
      $('candidate-notice').hidden = !item?.has_candidate;
    }
    async function render() {
      const serial = ++renderSequence;
      await new Promise(resolve => setTimeout(resolve, 150));
      if (serial !== renderSequence || !active()) return;
      try { const html = await options.preview(body); if (serial === renderSequence && mode === 'preview') preview.innerHTML = html; }
      catch (e) { if (serial === renderSequence) message('预览失败，正文仍保留：' + e.message); }
    }
    async function editable() {
      if (!active() || !item || item.mode === 'readonly' || busy) return false;
      if (item.mode === 'formal') {
        try { editJob ||= options.startEdit(item); item = await editJob; state(); }
        catch (e) { message(e.message); return false; }
        finally { editJob = null; }
      }
      return true;
    }
    async function setMode(next, end = false) {
      if (!item || (next === 'edit' && !(await editable()))) return;
      scroll[mode] = window.scrollY;
      mode = next; source.hidden = next !== 'edit'; preview.hidden = next === 'edit'; state();
      if (next === 'edit') {
        if (source.value !== body) source.value = body; height(); if (end) selection = [body.length, body.length];
        if (active()) { source.focus({preventScroll: true}); source.setSelectionRange(...selection); }
      } else { await render(); if (active()) preview.focus({preventScroll: true}); }
      if (active()) window.scrollTo(0, scroll[next]);
    }
    function commentList() {
      const container = $('comments'); container.replaceChildren(); container.hidden = comments.length === 0;
      if (!comments.length) return;
      container.append(node('批注', 'h3'));
      comments.forEach(c => {
        const row = document.createElement('div'); row.className = 'document-comment';
        if (c.quote) row.append(node(c.quote, 'blockquote'));
        row.append(node(c.text));
        container.append(row);
      });
    }
    function dirty() {
      sequence++; recover(); clearTimeout(timer); height(); state(); $('save-state').textContent = '保存中';
      if (!composing && active()) timer = setTimeout(flush, 600);
    }
    function resetNative() {
      nativeBase = nativeTip = body; nativeSelection = [...selection];
    }
    function freezeTyping() {
      // Native history handles ordinary input. Keep only a boundary snapshot when
      // a programmatic insertion/replacement has to reset that browser history.
      if (body !== nativeBase) undo.push({body: nativeBase, selection: [...nativeSelection]});
      if (nativeTip !== body) redo.push({body: nativeTip, selection: [nativeTip.length, nativeTip.length]});
      if (undo.length > 100) undo.shift();
    }
    function changed(event) {
      body = source.value; remembered = body; selection = [source.selectionStart, source.selectionEnd];
      if (!['historyUndo', 'historyRedo'].includes(event.inputType)) { nativeTip = body; redo = []; }
      dirty();
    }
    function insert(text, start = selection[0], end = selection[1]) {
      freezeTyping(); undo.push({body, selection: [...selection]}); redo = [];
      source.value = body; source.setSelectionRange(start, end); source.setRangeText(text, start, end, 'end');
      body = remembered = source.value; selection = [source.selectionStart, source.selectionEnd]; resetNative(); dirty();
    }
    function replaceToken(token, replacement) {
      function transform(entry) {
        const at = entry.body.indexOf(token); if (at < 0) return;
        entry.selection = entry.selection.map(pos => pos <= at ? pos : pos >= at + token.length ? pos + replacement.length - token.length : at + replacement.length);
        entry.body = entry.body.replace(token, replacement);
      }
      // Resolving an upload stays within the original insertion transaction. Its
      // cursor must be remapped too, including snapshots currently on redo.
      if (body.includes(token)) freezeTyping();
      for (const stack of [undo, redo]) for (const entry of stack) transform(entry);
      if (!body.includes(token)) return;
      const current = {body, selection}; transform(current);
      body = remembered = current.body; selection = current.selection; source.value = body;
      source.setSelectionRange(...selection); resetNative(); dirty(); if (mode === 'preview') render();
    }
    async function display(data, initial = false) {
      clearTimeout(timer); message('');
      item = data; body = data.body; comments = clone(data.comments || []); tags = [...(data.tags || [])];
      if (title) title.value = data.title || '';
      source.value = remembered = body; selection = [body.length, body.length]; sequence = saved = 0; undo = []; redo = []; resetNative();
      $('uploads').replaceChildren(); uploads.clear();
      for (const token of body.match(/<!--report-upload:[^>]+-->/g) || []) {
        const row = node('这张截图未完成上传，请移除占位后重新粘贴。','div'), remove = node('移除占位','button');
        remove.onclick=()=>{replaceToken(token,'');row.remove();state();};row.append(remove);$('uploads').append(row);
      }
      commentList(); $('save-state').textContent = data.exists === false ? '尚未保存' : '已保存';
      if (data.error_message || data.externally_changed) message(data.error_message || '文件已在外部改变，当前显示磁盘内容。');
      await setMode(initial && !body && data.mode === 'draft' ? 'edit' : 'preview');
    }
    async function conflict() {
      if (conflictOpen) return; conflictOpen = true;
      const remote = await options.load();
      modal('正文发生冲突', [node('当前稿保留在本机。请选择保留哪个版本，不会自动合并。'), node('当前稿' + (title ? ' · '+title.value : '')+'\n' + body, 'pre'), node('服务器稿' + (title ? ' · '+(remote.title || '') : '')+'\n' + remote.body, 'pre')], [
        ['保留当前稿', async () => { item = remote; if (!(await editable())) return; close(); await flush(); }],
        ['加载服务器稿', async () => { close(); localStorage.removeItem(recoveryKey); await display(remote); }], ['取消', close]
      ]);
    }
    async function flush() {
      clearTimeout(timer);
      if (!active() || !item || composing || conflictOpen) return false;
      if (item.mode !== 'draft') return sequence === saved;
      if (saveJob) { const ok = await saveJob; return ok && sequence !== saved ? flush() : ok; }
      if (saved === sequence) return true;
      const serial = sequence, value = content(); $('save-state').textContent = '保存中';
      saveJob = (async () => {
        try {
          const next = await options.save(value, item); item = next; saved = serial; state();
          if (serial === sequence) { localStorage.removeItem(recoveryKey); $('save-state').textContent = '已保存'; }
          return true;
        } catch (e) {
          recover(); $('save-state').textContent = '保存失败';
          if (e.status === 409) await conflict(); else message('保存失败，输入已保留：' + e.message);
          return false;
        }
      })();
      const ok = await saveJob; saveJob = null;
      if (ok && saved !== sequence && !composing) return flush();
      return ok;
    }
    source.addEventListener('input', changed);
    for (const event of ['select', 'click', 'keyup']) source.addEventListener(event, () => { selection = [source.selectionStart, source.selectionEnd]; });
    source.addEventListener('compositionstart', () => { composing = true; clearTimeout(timer); });
    source.addEventListener('compositionend', () => { composing = false; clearTimeout(timer); if (active()) timer = setTimeout(flush, 600); });
    title?.addEventListener('input', dirty);
    $('edit').onclick = () => setMode('edit'); $('preview-mode').onclick = () => setMode('preview');
    preview.ondblclick = () => setMode('edit'); preview.onclick = () => { if (!body) setMode('edit'); };
    $('tail').onclick = () => setMode('edit', true);
    $('tail').onkeydown = e => { if (e.key === 'Enter') setMode('edit', true); };
    if ($('save')) $('save').onclick = async () => { if (!item?.exists && sequence === saved) dirty(); await flush(); };
    async function upload(file, token, row) {
      row = row || document.createElement('div'); row.replaceChildren(node('图片上传中…', 'span')); $('uploads').append(row);
      uploads.set(token, file); state();
      try {
        if (file.size > 10 * 1024 * 1024) throw new Error('图片不能超过 10 MiB');
        if (!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)) throw new Error('支持 PNG、JPEG、GIF、WebP');
        const data = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result.split(',')[1]); r.onerror = reject; r.readAsDataURL(file); });
        const href = await options.upload(data); replaceToken(token, `![截图](${href})`); uploads.delete(token); row.remove();
      } catch (e) {
        row.replaceChildren(node('图片上传失败：' + e.message + ' ', 'span'));
        const retry = node('重试', 'button'), remove = node('移除', 'button');
        retry.onclick = () => { if (body.includes(token)) upload(file, token, row); else { uploads.delete(token); row.remove(); state(); } };
        remove.onclick = () => { replaceToken(token, ''); uploads.delete(token); row.remove(); state(); }; row.append(retry, remove);
      }
      state();
    }
    async function paste(e) {
      const inTitle = title && e.target === title;
      if (!ready || !item || (!surface.contains(e.target) && !inTitle)) return;
      const data = e.clipboardData, images = [...data.items].filter(x => x.kind === 'file' && x.type.startsWith('image/')).map(x => x.getAsFile()).filter(Boolean);
      let text = data.getData('text/plain');
      if (inTitle && (text || !images.length)) return;
      if (!images.length && !text && data.getData('text/html')) {
        const template = document.createElement('template'); template.innerHTML = data.getData('text/html');
        template.content.querySelectorAll('script,style').forEach(n => n.remove()); text = template.content.textContent;
      }
      if (!images.length && !text) return;
      e.preventDefault(); if (e.target === source) selection=[source.selectionStart,source.selectionEnd];
      const toPreview = mode === 'preview' || !body;
      if (!(await editable())) return;
      if (images.length) {
        const tokens=images.map(()=>`<!--report-upload:${crypto.randomUUID()}-->`);
        insert('\n'+tokens.join('\n\n')+'\n');images.forEach((file,i)=>upload(file,tokens[i]));
      }
      else insert(text);
      if (toPreview) await setMode('preview'); else { source.focus({preventScroll:true}); source.setSelectionRange(...selection); }
    }
    root.addEventListener('paste', paste);
    document.addEventListener('dragover', e => { if (active() && e.dataTransfer.types.includes('Files')) e.preventDefault(); }, {signal: lifecycle.signal});
    document.addEventListener('drop', async e => {
      if (!active()) return;
      const files = [...e.dataTransfer.files]; if (!files.length) return; e.preventDefault();
      if (!surface.contains(e.target) || !(await editable())) return;
      const tokens=files.map(()=>`<!--report-upload:${crypto.randomUUID()}-->`);
      insert('\n'+tokens.join('\n\n')+'\n');files.forEach((file,i)=>upload(file,tokens[i]));
      if (mode === 'preview' || !body) setMode('preview');
    }, {signal: lifecycle.signal});
    root.addEventListener('keydown', async e => {
      if (e.isComposing || !(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      if (key === 's') { e.preventDefault(); await flush(); return; }
      if (!surface.contains(e.target)) return;
      if (key === 'enter') { e.preventDefault(); setMode(mode === 'edit' ? 'preview' : 'edit'); }
      if (key === 'z' || key === 'y') {
        const backward = key === 'z' && !e.shiftKey;
        // Do not intercept the textarea's ordinary typing undo/redo chain.
        if (e.target === source && (backward ? body !== nativeBase : body !== nativeTip)) return;
        e.preventDefault(); if (!(await editable())) return;
        if (backward && nativeTip !== body) redo.push({body: nativeTip, selection: [nativeTip.length, nativeTip.length]});
        const from = backward ? undo : redo, to = backward ? redo : undo, value = from.pop();
        if (!value) return;
        to.push({body, selection: [...selection]}); body = remembered = value.body; selection = value.selection;
        source.value = body; source.setSelectionRange(...selection); resetNative(); dirty(); if (mode === 'preview') render();
      }
    });
    $('code').onclick = async () => { if (!(await editable())) return; insert('\n```\n\n```\n'); await setMode('edit'); };
    surface.addEventListener('pointerup', () => { selectedQuote = mode === 'edit' ? source.value.slice(source.selectionStart, source.selectionEnd) : window.getSelection()?.toString() || ''; });
    $('comment').onclick = async () => {
      if (!(await editable())) return;
      const quote = mode === 'edit' ? body.slice(...selection) : selectedQuote;
      const input = document.createElement('textarea'); input.setAttribute('aria-label','批注内容'); input.maxLength = 10000;
      modal('添加批注', [...(quote ? [node(quote,'blockquote')] : []), input], [['添加', () => {
        if (!input.value.trim()) return; comments.push({id: crypto.randomUUID(), quote, text: input.value, created_at: new Date().toISOString()});
        commentList(); dirty(); close();
      }]]); input.focus();
    };
    const exported = () => body.replace(/<!--report-upload:[^>]+-->/g, '') + (comments.length ? '\n\n## 批注\n\n' + comments.map(c => (c.quote ? c.quote.split('\n').map(l => '> '+l).join('\n')+'\n\n' : '') + c.text).join('\n\n') : '');
    $('copy').onclick = async () => { try { await navigator.clipboard.writeText(exported()); message('已复制 Markdown'); } catch (e) { message(e.message); } };
    $('export').onclick = () => { const url = URL.createObjectURL(new Blob([exported()],{type:'text/markdown;charset=utf-8'})); const a = document.createElement('a'); a.href=url; a.download=options.filename(item); a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); };
    $('info').onclick = () => {
      if (!item) return;
      const data = options.info(item), nodes = data.map(([name,value]) => node(name+'：'+(value || '未知')));
      if (title && item?.mode !== 'readonly') { const input = document.createElement('input'); input.value=tags.join(', '); input.setAttribute('aria-label','标签'); nodes.push(input);
        modal('笔记信息', nodes, [['保存标签', ()=>{tags=input.value.split(/[,，]/).map(x=>x.trim()).filter(Boolean);dirty();close();}]]);
      } else modal('文档信息',nodes);
    };
    const context = {request, modal, close, node, flush, display, message, current:()=>item, content, setMode,
      clean:()=>sequence===saved&&!saveJob&&!uploads.size,
      refreshMetadata:data=>{if(item && data.revision===item.revision){item=data;state();}}};
    $('history').onclick = () => options.history(context).catch(e=>message(e.message));
    if ($('sources')) $('sources').onclick = () => options.sources(context).catch(e=>message(e.message));
    if ($('candidate')) $('candidate').onclick = () => options.candidate(context).catch(e=>message(e.message));
    if ($('regenerate')) $('regenerate').onclick = async () => { pendingActions++; try { await options.regenerate(context); } catch (e) { message(e.message); } finally { pendingActions--; } };
    if ($('save-as')) $('save-as').onclick = () => options.saveAs(context).catch(e=>message(e.message));
    if ($('confirm')) $('confirm').onclick = async () => {
      if (item.mode === 'formal') { await setMode('edit'); return; }
      if (uploads.size || busy || body.includes('<!--report-upload:')) return;
      busy=true;state();try { if (await flush()) await display(await options.confirm(item)); } catch(e){message(e.message);} finally{busy=false;state();}
    };
    const unsaved = () => sequence !== saved || uploads.size || saveJob || editJob || busy || pendingActions || composing || dialogDirty();
    window.addEventListener('beforeunload', e => { if (active() && !allowUnload && unsaved()) { e.preventDefault();e.returnValue=''; } }, {signal: lifecycle.signal});
    document.addEventListener('workbench:before-leave', e => {
      if (!owns(e) || !active() || !unsaved()) return;
      e.preventDefault();
      if (uploads.size) message('内容尚未保存；请等待图片上传完成，或移除未上传的截图。');
      else if (busy || pendingActions || editJob || composing || dialogDirty()) message('内容尚未保存，请先完成当前操作。');
      else {
        message('保存中，请稍候。');
        flush().then(ok => { if (active() && ok) message('已保存，请再次选择要打开的栏目。'); });
      }
    }, {signal: lifecycle.signal});
    document.addEventListener('workbench:leave', e => {
      if (owns(e)) { clearTimeout(timer); renderSequence++; if ($('dialog').open) close(); }
    }, {signal: lifecycle.signal});
    document.addEventListener('workbench:enter', e => {
      if (!owns(e) || !active()) return;
      height();
      if (sequence !== saved && !composing) timer = setTimeout(flush, 600);
      if (mode === 'preview' && item) render();
      if (deferredModal) { const args = deferredModal; deferredModal = null; modal(...args); }
    }, {signal: lifecycle.signal});
    document.addEventListener('workbench:dispose', e => {
      if (!owns(e)) return;
      clearTimeout(timer); renderSequence++; deferredModal = null; lifecycle.abort();
    }, {signal: lifecycle.signal});
    document.addEventListener('click', async e => {
      if (!active()) return;
      const a = e.target.closest('a[href]'); if (!a || e.defaultPrevented || e.ctrlKey || e.metaKey || a.target || a.hasAttribute('download') || a.origin !== location.origin || a.pathname === location.pathname) return;
      if (busy || pendingActions || editJob || composing || dialogDirty()) { e.preventDefault(); message('内容尚未保存，请先完成当前操作。'); return; }
      if (sequence === saved && !saveJob && !uploads.size) return;
      e.preventDefault(); if (!uploads.size && await flush()) location.assign(a.href);
      else modal('内容尚未保存', [node('输入已保留为本机恢复稿；尚未上传的截图无法在刷新后恢复。')], [['继续留在这里',close],['离开页面',()=>{recover();sequence=saved;uploads.clear();allowUnload=true;location.assign(a.href);}]]);
    }, {signal: lifecycle.signal});
    state();
    options.load().then(async data => {
      if (lifecycle.signal.aborted) return;
      // Freeze recovery candidates before rendering; another tab may start typing
      // while preview is awaiting HTTP. Never mistake that new edit for a crash.
      let stored=[];try{for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(key.startsWith(options.key+':'))stored.push({key,...JSON.parse(localStorage.getItem(key))});}}catch{}
      await display(data,true); ready=true;state();
      let drafts=stored.filter(d=>d.body!==body||JSON.stringify(d.comments||[])!==JSON.stringify(comments)||JSON.stringify(d.tags||[])!==JSON.stringify(tags)||(title&&d.title!==title.value));
      drafts.sort((a,b)=>b.updated_at-a.updated_at);
      if(drafts.length){const draft=drafts[0];modal('发现未保存的本机稿',[node('服务器正文没有被覆盖。恢复后仍需通过版本检查保存。')],[['恢复本机稿',async()=>{
        if(!(await editable()))return;body=draft.body;comments=draft.comments||[];tags=draft.tags||[];if(title)title.value=draft.title||'';source.value=remembered=body;selection=[body.length,body.length];resetNative();item.revision=draft.base_revision;commentList();dirty();close();await setMode('edit');
        localStorage.removeItem(draft.key);
      }],['忽略此稿',()=>{localStorage.removeItem(draft.key);close();}]]);}
    }).catch(e=>{message('打开失败：'+e.message);$('save-state').textContent='打开失败';});
    return context;
  }
  return {mount, request, node};
})();
