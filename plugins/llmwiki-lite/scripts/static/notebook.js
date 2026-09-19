/* Dependency-free, local-only notebook. The server owns Markdown rendering. */
(() => {
  'use strict';
  const root = document.querySelector('#notebook');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const project = root.dataset.project;
  let noteId = root.dataset.note;
  const base = `/api/project/${encodeURIComponent(project)}/notebook/`;
  const route = `/project/${encodeURIComponent(project)}`;
  const title = $('#nb-title'), tags = $('#nb-tags'), blocksEl = $('#nb-blocks');
  const status = $('#nb-status'), banner = $('#nb-banner');
  const labels = {markdown:'文本', heading:'标题', image:'图片', code:'代码', callout:'提示', divider:'分隔线'};
  let doc = {title:'', tags:[], blocks:[]}, revision = '', dirty = false, generation = 0;
  let saving = false, blocked = true, timer, pendingUploads = 0, activeBlock = null, fileTarget = null;
  const undo = [];
  const clone = x => JSON.parse(JSON.stringify(x));
  const id = () => crypto.randomUUID().replaceAll('-', '');
  let tabId;
  try {tabId=sessionStorage.getItem('llmwiki-notebook-tab')||id();sessionStorage.setItem('llmwiki-notebook-tab',tabId);} catch {tabId=id();}
  const draftPrefix = () => `llmwiki-notebook:${project}:${noteId}:`;
  const key = () => draftPrefix()+tabId;
  const element = (tag, cls, text) => {const e = document.createElement(tag); if(cls) e.className=cls; if(text !== undefined) e.textContent=text; return e;};
  const button = (text, fn, cls='') => {const e=element('button',cls,text);e.type='button';e.addEventListener('click',fn);return e;};
  function setStatus(text, error=false) {status.textContent=text;status.classList.toggle('nb-error',error);}
  function formatTime(value) {
    if(value===undefined)return '待首次保存';
    if(!value)return '此前未记录';
    const date=new Date(value);
    if(Number.isNaN(date.getTime()))return '时间无效';
    return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date)+' UTC+08:00';
  }
  function timeLabel(item) {return `创建：${formatTime(item.created_at)} · 最近修改：${formatTime(item.updated_at)}`;}
  function refreshTimes() {
    const info=$('#nb-info-times');
    if(info)info.textContent=timeLabel(doc);
  }
  function showInfo() {
    const content=$('#nb-dialog-content');
    const times=element('p','nb-info-times',timeLabel(doc));times.id='nb-info-times';
    content.replaceChildren(element('h2','','笔记信息'),times,
      element('p','nb-hint','北京时间（UTC+08:00），精确到秒。时间对应已保存版本；创建时间为首次成功保存时间。'));
    $('#nb-dialog').showModal();
  }
  function clearTimes(item) {delete item.created_at;delete item.updated_at;}
  function message(text, actions=[]) {
    banner.replaceChildren(element('span','',text));
    actions.forEach(([label, fn])=>banner.append(button(label,fn)));
    banner.hidden=false;
  }
  async function request(path, payload) {
    const response = await fetch(base+path, payload === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json','X-Notebook-Request':'1'}, body:JSON.stringify(payload)});
    let data;
    try {data=await response.json();} catch {throw new Error('服务未响应，请检查本地网页服务是否运行。');}
    if(!response.ok || !data.ok) {const e=new Error(data.error || '请求失败');e.code=response.status;throw e;}
    return data;
  }
  function remember() {
    try {localStorage.setItem(key(),JSON.stringify({document:doc, revision, savedAt:new Date().toISOString()}));}
    catch {message('浏览器草稿不可用或空间不足，请及时保存到 Wiki 或导出 Markdown。');}
  }
  function changed() {
    dirty=true;generation++;remember();clearTimeout(timer);
    setStatus(blocked?'需处理冲突 · 草稿已保留':'尚未保存…');
    if(!blocked) timer=setTimeout(save,900);
  }
  function checkpoint() {undo.push(clone(doc));if(undo.length>40)undo.shift();$('#nb-undo').disabled=false;}
  function resize(input) {input.style.height='auto'; input.style.height=Math.max(input.scrollHeight,52)+'px';}
  function focusBlock(bid) {
    activeBlock=bid;
    const el=blocksEl.querySelector(`[data-id="${bid}"]`);
    const input=el?.querySelector('.nb-input')||el?.querySelector('button:not(:disabled)');
    if(input) input.focus();
  }
  function addBlock(kind, index=doc.blocks.length) {
    if(blocked) return;
    checkpoint();const block={id:id(),type:kind,text:'',image:'',comments:[]};
    doc.blocks.splice(index,0,block);changed();render();focusBlock(block.id);
    if(kind==='image') fileTarget=block.id; // Creating a block must not open the file picker.
  }
  function insertBar(index) {
    const bar=element('div','nb-insert');
    const menu=element('div','nb-insert-menu');menu.hidden=true;
    const plus=button('＋',()=>{const open=menu.hidden;root.querySelectorAll('.nb-insert-menu').forEach(m=>m.hidden=true);menu.hidden=!open;plus.setAttribute('aria-expanded',String(open));},'nb-plus');
    plus.setAttribute('aria-label',`在第 ${index+1} 个位置添加块`);plus.setAttribute('aria-expanded','false');
    Object.entries(labels).forEach(([kind,label])=>menu.append(button(label,()=>addBlock(kind,index))));
    bar.append(plus,menu);return bar;
  }
  async function preview(block, area, previewEl, toggle) {
    if(!previewEl.hidden) {previewEl.hidden=true;area.hidden=false;toggle.textContent='预览';area.focus();return;}
    toggle.disabled=true;
    try {const data=await request('preview',{text:block.text});previewEl.innerHTML=data.html;
      previewEl.hidden=false;area.hidden=true;toggle.textContent='编辑';}
    catch(e) {message(e.message);} finally {toggle.disabled=false;}
  }
  function showImage(block) {
    const content=$('#nb-dialog-content');content.replaceChildren();
    const image=element('img','nb-lightbox');image.src=`${route}/asset/records/assets/${block.image}`;image.alt=block.text||'科研截图';
    content.append(image,element('p','',block.text));$('#nb-dialog').showModal();
  }
  function chooseImage(bid) {fileTarget=bid;$('#nb-file').click();}
  function render() {
    title.value=doc.title;tags.value=doc.tags.join('，');blocksEl.replaceChildren();
    $('#nb-empty').hidden=doc.blocks.length>0;
    doc.blocks.forEach((block,index)=>{
      blocksEl.append(insertBar(index));
      const cell=element('section',`nb-cell nb-${block.type === "image" ? "image-block" : block.type}`);cell.dataset.id=block.id;
      cell.addEventListener('focusin',()=>{activeBlock=block.id;});
      cell.addEventListener('pointerdown',()=>{activeBlock=block.id;});
      const head=element('div','nb-cell-head');
      head.append(element('span','nb-cell-number',String(index+1).padStart(2,'0')),element('span','nb-cell-kind',labels[block.type]));
      const tools=element('div','nb-cell-tools');
      function move(delta) {if(blocked)return;checkpoint();const other=index+delta;[doc.blocks[index],doc.blocks[other]]=[doc.blocks[other],doc.blocks[index]];changed();render();focusBlock(block.id);}
      const up=button('↑',()=>move(-1));up.title='上移';up.setAttribute('aria-label','上移块');up.disabled=index===0;
      const down=button('↓',()=>move(1));down.title='下移';down.setAttribute('aria-label','下移块');down.disabled=index===doc.blocks.length-1;
      tools.append(up,down,button('复制',()=>{if(blocked)return;checkpoint();const copy=clone(block);copy.id=id();clearTimes(copy);doc.blocks.splice(index+1,0,copy);changed();render();focusBlock(copy.id);}),
        button(`批注${block.comments.length?' · '+block.comments.length:''}`,()=>{if(blocked)return;checkpoint();block.comments.push('');changed();render();const inputs=blocksEl.querySelector(`[data-id="${block.id}"]`).querySelectorAll('.nb-comment textarea');inputs[inputs.length-1].focus();}),
        button('删除',()=>{if(blocked)return;checkpoint();doc.blocks.splice(index,1);changed();render();message('已删除一个块，支持撤销；已保存版本也可从历史恢复。',[['撤销',undoBlock]]);}));
      const more=element('details','action-menu nb-block-menu'),summary=element('summary','','⋯');summary.setAttribute('aria-label','块菜单');
      const actions=element('div','action-menu-items');Array.from(tools.children).filter(b=>!b.textContent.startsWith('批注')).forEach(b=>actions.append(b));
      more.append(summary,actions);tools.append(more);head.append(tools);cell.append(head);
      if(block.type==='divider') cell.append(element('hr'));
      else {
        if(block.type==='image') {
          if(block.image) {
            const image=element('img','nb-image');image.src=`${route}/asset/records/assets/${block.image}`;image.alt=block.text||'科研截图';image.loading='lazy';
            const zoom=button('',()=>showImage(block),'nb-image-zoom');zoom.setAttribute('aria-label','放大截图');zoom.append(image);cell.append(zoom);
            cell.append(button('替换图片',()=>chooseImage(block.id),'nb-replace'));
          } else cell.append(button('粘贴截图 · 拖入图片 · 点击选择',()=>chooseImage(block.id),'nb-image-drop'));
        }
        const area=element('textarea','nb-input');area.value=block.text;area.rows=block.type==='heading'?1:3;
        area.setAttribute('aria-label',`${labels[block.type]}块 ${index+1}`);
        area.placeholder=({markdown:'记录观察、想法或实验过程… 支持 **粗体**、列表、表格与链接',heading:'小节标题',code:'粘贴代码或实验日志（不执行）',image:'为截图添加说明、实验条件或来源…',callout:'关键发现、待验证假设或提醒…'})[block.type];
        area.disabled=blocked;
        area.addEventListener('input',()=>{block.text=area.value;resize(area);changed();});
        area.addEventListener('keydown',event=>{if(event.key==='Tab' && block.type==='code'){event.preventDefault();const start=area.selectionStart;area.setRangeText('  ',start,area.selectionEnd,'end');area.dispatchEvent(new Event('input'));}});
        const previewEl=element('div','nb-preview document');previewEl.hidden=true;
        if(block.type==='markdown') tools.prepend(button('预览',event=>preview(block,area,previewEl,event.currentTarget)));
        cell.append(area,previewEl);
      }
      if(block.comments.length) {
        const comments=element('aside','nb-comments');comments.append(element('span','nb-comment-label','批注 · 随内容块保存'));
        block.comments.forEach((text,i)=>{
          const row=element('div','nb-comment');const input=element('textarea');input.rows=1;input.value=text;input.placeholder='补充解读、疑问或下一步…';input.setAttribute('aria-label',`第 ${index+1} 块批注 ${i+1}`);input.disabled=blocked;
          input.addEventListener('input',()=>{block.comments[i]=input.value;resize(input);changed();});
          const remove=button('×',()=>{if(blocked)return;checkpoint();block.comments.splice(i,1);changed();render();});remove.setAttribute('aria-label','删除批注');row.append(input,remove);comments.append(row);
        });cell.append(comments);
      }
      blocksEl.append(cell);
    });
    const continueWriting=button('继续记录…',()=>{const last=doc.blocks.at(-1);if(last?.type==='markdown'&&!last.text.trim())focusBlock(last.id);else addBlock('markdown');},'nb-continue');
    continueWriting.disabled=blocked;
    $('#nb-bottom').replaceChildren(continueWriting,insertBar(doc.blocks.length));
    title.disabled=blocked;tags.disabled=blocked;refreshTimes();
    requestAnimationFrame(()=>root.querySelectorAll('textarea').forEach(resize));
  }
  function undoBlock() {if(blocked||!undo.length)return;doc=undo.pop();$('#nb-undo').disabled=!undo.length;changed();render();banner.hidden=true;}
  async function save() {
    clearTimeout(timer);
    if(blocked||saving||pendingUploads||!dirty)return;
    saving=true;const sentGeneration=generation;const sent=clone(doc);setStatus('正在保存…');
    try {
      const data=await request(noteId,{document:sent,revision});revision=data.revision;
      if(data.timestamps) {
        doc.created_at=data.timestamps.created_at;doc.updated_at=data.timestamps.updated_at;
        const times=new Map(data.timestamps.blocks.map(b=>[b.id,b]));
        doc.blocks.forEach(b=>{const t=times.get(b.id);if(t){b.created_at=t.created_at;b.updated_at=t.updated_at;}});
        refreshTimes();
      }
      history.replaceState(null,'',`${route}/notebook/${noteId}`);
      dirty=generation!==sentGeneration;
      if(dirty)remember();else {try{localStorage.removeItem(key());}catch{ /* server copy is safe */ }}
      setStatus(dirty?'尚有新改动…':'已保存');
    } catch(e) {
      remember();
      if(e.code===409) {
        blocked=true;render();
        message(e.message,[['保存为新笔记',saveCopy],['对比服务器版本',compareServer],['导出我的草稿',exportMarkdown]]);
      } else message(`保存失败：${e.message} 草稿仍保留在当前浏览器。`,[['重试保存',save],['导出草稿',exportMarkdown]]);
      setStatus('未保存到 Wiki',true);
    } finally {saving=false; if(dirty&&!blocked)timer=setTimeout(save,5000);}
  }
  async function saveCopy() {
    if(saving||pendingUploads)return;
    const oldKey=key();noteId=id();revision='';clearTimes(doc);doc.blocks.forEach(clearTimes);blocked=false;dirty=true;generation++;
    history.replaceState(null,'',`${route}/notebook/${noteId}`);remember();
    try{localStorage.removeItem(oldKey);}catch{ /* draft retained in current key */ }
    banner.hidden=true;render();await save();
  }
  async function compareServer() {
    try {
      const result=await request(noteId);const content=$('#nb-dialog-content');content.replaceChildren(element('h2','','服务器版本（不会覆盖草稿）'));
      const pre=element('pre','nb-version-text',toMarkdown(result.document));content.append(pre);$('#nb-dialog').showModal();
    } catch(e) {message(e.message,[['保存为新笔记',saveCopy],['导出我的草稿',exportMarkdown],['查看原 Markdown',()=>window.open(`${route}/page/records/manual/${noteId}.md`,'_blank','noopener')]]);}
  }
  function toMarkdown(document, withMetadata=false) {
    const lines=['# '+(document.title||'未命名科研笔记'),''];
    if(withMetadata)lines.unshift('---','type: research-notebook','recorded_at: '+JSON.stringify(document.created_at??null),'updated_at: '+JSON.stringify(document.updated_at??null),'---','');
    if(document.tags.length)lines.push('标签：'+document.tags.join('、'),'');
    document.blocks.forEach(b=>{
      if(b.type==='heading')lines.push('## '+b.text);
      else if(b.type==='code'){const longest=Math.max(2,...(b.text.match(/`+/g)||[]).map(x=>x.length));const fence='`'.repeat(longest+1);lines.push(fence+'\n'+b.text+'\n'+fence);}
      else if(b.type==='divider')lines.push('---');
      else if(b.type==='callout')lines.push(b.text.split('\n').map(l=>'> '+l).join('\n'));
      else if(b.type==='image'){if(b.image)lines.push(`![截图](../assets/${b.image})`);if(b.text)lines.push(b.text);}
      else lines.push(b.text);
      b.comments.forEach(c=>lines.push(c.split('\n').map((l,i)=>'> '+(i?'':'批注：')+l).join('\n')));lines.push('');
    });return lines.join('\n');
  }
  function exportMarkdown() {
    const blob=new Blob([toMarkdown(doc,true)],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob);
    const a=element('a');a.href=url;a.download=(doc.title||'科研笔记').replace(/[\\/:*?"<>|]/g,'_')+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    message('Markdown 已导出。图片仍保存在 Wiki 的 records/assets 目录；移到其他位置时请一并复制图片目录。');
  }
  let uploads=Promise.resolve();
  function uploadFiles(files, target=null, after=activeBlock, replace=false) {
    if(blocked)return Promise.resolve();
    pendingUploads++;setStatus('图片上传中…');
    uploads=uploads.then(()=>uploadBatch(files,target,after,replace)).finally(()=>{pendingUploads--;save();});
    return uploads;
  }
  async function uploadBatch(files, target, after, replace) {
    if(blocked)return;
    try {
      after=target || after;
      for(const file of files) {
        if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type))throw new Error('请选择 PNG、JPEG、GIF 或 WebP 图片。');
        if(file.size>10*1024*1024)throw new Error('单张图片不能超过 10 MB。');
        // Decode locally too: avoids accepting a renamed non-image as a screenshot.
        const bitmap=await createImageBitmap(file);
        if(bitmap.width*bitmap.height>80_000_000||bitmap.width>20000||bitmap.height>20000){bitmap.close();throw new Error('图片尺寸过大。');}bitmap.close();
        const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
        const result=await request('upload',{data:encoded});
        if(blocked)throw new Error('笔记已暂停编辑，请先处理保存冲突');
        checkpoint();let block=target?doc.blocks.find(b=>b.id===target&&((b.type==='image'&&(replace||!b.image))||(b.type==='markdown'&&!b.text.trim()&&!b.comments.length))):null;
        if(!block){block={id:id(),type:'image',text:'',image:'',comments:[]};const index=doc.blocks.findIndex(b=>b.id===after);doc.blocks.splice(index<0?doc.blocks.length:index+1,0,block);}
        block.type='image';block.image=result.image;after=block.id;activeBlock=block.id;target=null;changed();render();focusBlock(block.id);
      }
    } catch(e) {message(`上传失败：${e.message||'无法解码该图片'}。其他已上传图片不会丢失。`);}
  }
  async function showHistory() {
    try {
      const result=await request(noteId+'/history');const content=$('#nb-dialog-content');content.replaceChildren(element('h2','','版本历史'),element('p','nb-hint','选择一个版本预览。恢复会另存当前版本，不会删除历史。'));
      if(!result.versions.length)content.append(element('p','','还没有历史版本；每次更新已保存笔记时自动保留上一版。'));
      result.versions.forEach(v=>content.append(button(`${formatTime(v.updated_at)} · ${v.title}`,async()=>{
        try{const old=await request(noteId+'/history?revision='+v.revision);content.replaceChildren(element('h2','','历史版本预览'),element('pre','nb-version-text',toMarkdown(old.document)));
          content.append(button('恢复此版本',()=>{if(blocked||saving||pendingUploads){message('请先处理保存冲突或等待保存完成。');return;}checkpoint();doc=old.document;changed();render();$('#nb-dialog').close();save();},'primary'));}
        catch(e){message(e.message);}
      },'nb-history-item')));$('#nb-dialog').showModal();
    }catch(e){message(e.message);}
  }
  async function init() {
    history.replaceState(null,'',`${route}/notebook/${noteId}`);
    let draft, draftKey=key();
    try {
      const raw=localStorage.getItem(key());draft=raw?JSON.parse(raw):null;
      if(!draft) {
        const candidates=Object.keys(localStorage).filter(k=>k.startsWith(draftPrefix())).map(k=>({key:k,value:JSON.parse(localStorage.getItem(k))})).sort((a,b)=>String(b.value.savedAt).localeCompare(String(a.value.savedAt)));
        if(candidates.length){draft=candidates[0].value;draftKey=candidates[0].key;}
      }
    }catch{ /* continue with server */ }
    try {
      const data=await request(noteId);doc=data.document;revision=data.revision;
      if(!doc.blocks.length&&!data.exists)doc.blocks=[{id:id(),type:'markdown',text:'',image:'',comments:[]}];
      blocked=false;render();setStatus(data.exists?'已保存到本地 Wiki':'新笔记 · 输入后自动保存');
      if(draft?.document){blocked=true;render();message('发现未保存的浏览器草稿。请选择恢复或使用服务器版本。',[
        ['恢复草稿',()=>{try{localStorage.removeItem(draftKey);}catch{}doc=draft.document;blocked=draft.revision!==revision;revision=draft.revision;dirty=true;generation++;remember();render();
          if(blocked)message('草稿与服务器版本不同，已暂停自动保存。',[['保存为新笔记',saveCopy],['对比服务器版本',compareServer],['导出草稿',exportMarkdown]]);
          else{banner.hidden=true;changed();}}],
        ['使用服务器版本',()=>{try{localStorage.removeItem(draftKey);}catch{}blocked=false;banner.hidden=true;render();setStatus('已载入服务器版本');}]
      ]);}
    }catch(e){
      blocked=true;
      if(draft?.document){doc=draft.document;revision=draft.revision;dirty=true;render();message(e.message,[['保存草稿为新笔记',saveCopy],['导出草稿',exportMarkdown]]);}
      else{render();message(e.message,[['重试',()=>location.reload()],['查看原 Markdown',()=>window.open(`${route}/page/records/manual/${noteId}.md`,'_blank','noopener')]]);}
      setStatus('笔记未载入，已禁止覆盖',true);
    }
  }
  title.addEventListener('input',()=>{doc.title=title.value;changed();});
  tags.addEventListener('input',()=>{doc.tags=tags.value.split(/[,，]/).map(t=>t.trim()).filter(Boolean);changed();});
  $('#nb-save').addEventListener('click',save);$('#nb-undo').addEventListener('click',undoBlock);
  $('#nb-info').addEventListener('click',showInfo);$('#nb-export').addEventListener('click',exportMarkdown);$('#nb-history').addEventListener('click',showHistory);
  $('#nb-focus').addEventListener('click',()=>{document.body.classList.toggle('nb-focus-mode');$('#nb-focus').textContent=document.body.classList.contains('nb-focus-mode')?'退出专注':'专注模式';});
  $('#nb-dialog-close').addEventListener('click',()=>$('#nb-dialog').close());
  $('#nb-file').addEventListener('change',event=>{const files=Array.from(event.target.files);event.target.value='';uploadFiles(files,fileTarget,activeBlock,true);fileTarget=null;});
  function pasteTarget(event) {
    // Do not hijack search fields, metadata, annotations or open dialogs.
    if(document.querySelector('dialog[open]'))return false;
    const target=event.target instanceof Element?event.target:document.activeElement;
    if(target?.closest('#nb-tags, .nb-comment, [contenteditable="true"]'))return false;
    return !(target?.matches('input, textarea')&&!root.contains(target));
  }
  /* The title is a text field, not the note body. It lives inside root, so without
     this it looked like a body textarea: copying from a chat window or a document
     yields text + image, and the image won while the title silently lost the text.
     Image-only clipboards keep landing as a block, which is the deliberate case. */
  function titleTarget(event) {
    const target=event.target instanceof Element?event.target:document.activeElement;
    return !!target?.closest('#nb-title');
  }
  function emptyImageTarget() {
    const current=doc.blocks.find(b=>b.id===activeBlock);
    const only=doc.blocks.length===1?doc.blocks[0]:null;
    const block=current||only;
    return block&&((block.type==='image'&&!block.image)||(block.type==='markdown'&&!block.text.trim()&&!block.comments.length))?block.id:null;
  }
  document.addEventListener('paste',event=>{
    if(!pasteTarget(event))return;
    const clipboard=event.clipboardData;
    let files=Array.from(clipboard?.items||[]).filter(i=>i.kind==='file').map(i=>i.getAsFile()).filter(Boolean);
    if(!files.length)files=Array.from(clipboard?.files||[]);
    files=files.filter(f=>f.type.startsWith('image/'));
    if(files.length){
      // M-01 keeps the normal text paste in the title. Copying from a chat window or a
      // document gives text + image; without this the image won and the text was lost.
      if(titleTarget(event)&&clipboard?.getData('text/plain'))return;
      event.preventDefault();if(blocked){message('笔记尚未就绪或存在冲突，请处理后再粘贴。');return;}uploadFiles(files,emptyImageTarget());}
    else if(!blocked&&!doc.blocks.length&&clipboard?.getData('text/plain')&&!(event.target instanceof Element&&event.target.matches('input,textarea'))){
      event.preventDefault();addBlock('markdown');doc.blocks[0].text=clipboard.getData('text/plain');changed();render();focusBlock(doc.blocks[0].id);
    }
  });
  root.addEventListener('dragover',event=>{if(event.dataTransfer.types.includes('Files')){event.preventDefault();root.classList.add('nb-dragging');}});
  root.addEventListener('dragleave',event=>{if(!root.contains(event.relatedTarget))root.classList.remove('nb-dragging');});
  root.addEventListener('drop',event=>{root.classList.remove('nb-dragging');if(event.dataTransfer.files.length){event.preventDefault();const cell=event.target.closest('.nb-cell');if(cell)activeBlock=cell.dataset.id;uploadFiles(Array.from(event.dataTransfer.files),emptyImageTarget());}});
  /* A drop that misses the note used to hand the file to the browser, which replaced
     the editor with the image. Swallow file drops anywhere outside the note instead. */
  ['dragover','drop'].forEach(type=>document.addEventListener(type,event=>{
    if(event.dataTransfer?.types.includes('Files')&&!root.contains(event.target))event.preventDefault();
  }));
  function closeInserts() {root.querySelectorAll('.nb-insert-menu').forEach(m=>m.hidden=true);root.querySelectorAll('.nb-plus').forEach(b=>b.setAttribute('aria-expanded','false'));}
  document.addEventListener('click',event=>{if(!event.target.closest('.nb-insert'))closeInserts();});
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape')closeInserts();
    if(document.querySelector('dialog[open]'))return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();save();}
    if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&root.contains(event.target)){event.preventDefault();const index=doc.blocks.findIndex(b=>b.id===activeBlock);addBlock('markdown',index<0?doc.blocks.length:index+1);}
  });
  window.addEventListener('beforeunload',event=>{if(dirty||pendingUploads){remember();event.preventDefault();event.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&dirty){remember();save();}});
  init();
})();
