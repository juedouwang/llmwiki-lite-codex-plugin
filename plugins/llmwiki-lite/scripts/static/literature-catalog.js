(() => {
  'use strict';
  const root = document.querySelector('#literature');
  if (!root) return;
  const pid = root.dataset.project, base = `/project/${encodeURIComponent(pid)}/literature`, api = `/api/project/${encodeURIComponent(pid)}/literature/`;
  const key = `literature:${pid}`, message = root.querySelector('#literature-message');
  const uid = () => crypto.randomUUID().replaceAll('-', '');
  async function post(action, data) {
    const response = await fetch(api + action, {method:'POST', headers:{'Content-Type':'application/json','X-Literature-Request':'1'}, body:JSON.stringify(data)});
    const value = await response.json();
    if (!response.ok || !value.ok) throw new Error(value.error?.message || '部分操作失败；请检查选中的文件。');
    return value;
  }
  function remember() {sessionStorage.setItem(key, JSON.stringify({url:location.pathname+location.search, scroll:scrollY}));}
  root.querySelectorAll('[data-item]').forEach(a => a.addEventListener('click', remember));
  const back = root.querySelector('[data-back]');
  if (back) {
    try {const saved=JSON.parse(sessionStorage.getItem(key)); if(saved?.url.startsWith(base+'?') || saved?.url===base) back.href=saved.url;} catch {}
  } else {
    try {const saved=JSON.parse(sessionStorage.getItem(key)); if(saved?.url===location.pathname+location.search) requestAnimationFrame(()=>scrollTo(0,saved.scroll));} catch {}
  }
  function reveal(kind) {const f=root.querySelector(`[data-literature-form="${kind}"]`);f.hidden=false; f.elements.locator.focus();}
  root.querySelector('[data-add]')?.addEventListener('click',()=>reveal('add'));
  root.querySelector('[data-edit]')?.addEventListener('click',()=>reveal('edit'));
  if (new URLSearchParams(location.search).get('edit') === '1') reveal('edit');
  let searchGeneration=0;
  async function refresh(url=location.href, highlight) {
    const generation=++searchGeneration;
    const response=await fetch(url);
    if(!response.ok) throw new Error('列表加载失败，请重试。');
    const doc=new DOMParser().parseFromString(await response.text(),'text/html');
    if(generation!==searchGeneration) return;
    root.querySelector('#literature-results').replaceWith(doc.querySelector('#literature-results'));
    root.querySelectorAll('[data-item]').forEach(a=>a.addEventListener('click',remember));
    if(highlight) {const row=document.getElementById('lit-'+highlight);row?.classList.add('literature-highlight');row?.scrollIntoView({block:'nearest'});}
  }
  let timer;
  root.querySelector('#literature-search')?.addEventListener('input',event=>{
    if(event.isComposing) return;
    clearTimeout(timer); timer=setTimeout(async()=>{
      const url=new URL(base,location.origin);if(event.target.value) url.searchParams.set('q',event.target.value);
      history.replaceState(null,'',url);try {await refresh(url);}catch(e){message.textContent=e.message;}
    },180);
  });
  root.querySelectorAll('[data-literature-form]').forEach(form=>{
    let composing=false, busy=false, requestId=uid();
    form.addEventListener('compositionstart',()=>composing=true);
    form.addEventListener('compositionend',()=>composing=false);
    form.addEventListener('keydown',e=>{
      if(e.key==='Enter'&&(composing||e.isComposing||e.keyCode===229)) e.preventDefault();
      if(e.key==='Escape'&&!composing) {e.preventDefault();form.hidden=true;}
    });
    form.querySelector('[data-cancel]')?.addEventListener('click',()=>form.hidden=true);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||composing) return;
      busy=true;const button=form.querySelector('[type=submit]'), error=form.querySelector('[role=alert]'), focused=document.activeElement;
      button.disabled=true;error.textContent='';
      try {
        const kind=form.dataset.literatureForm, article=root.querySelector('[data-item-id]');let action, data;
        if(kind==='import') {action='migrate/apply';data={selected_paths:[...form.querySelectorAll(':checked')].map(x=>x.value)};}
        else {
          data={locator:form.elements.locator.value};
          if(kind==='add') {data.request_id=requestId;if(form.elements.title.value.trim())data.title=form.elements.title.value;action='add';}
          else {Object.assign(data,{title:form.elements.title.value,authors:form.elements.authors.value.split('\n').map(x=>x.trim()).filter(Boolean),year:form.elements.year.value?Number(form.elements.year.value):null,expected_item_revision:article.dataset.revision}); action=`item/${article.dataset.itemId}/update`;}
        }
        const result=await post(action,data);
        if(kind==='edit') {article.dataset.revision=result.item_revision;location.replace(location.pathname);return;}
        if(kind==='import') {location.href=base;return;}
        message.textContent=({created:'已收藏',restored:'已重新收录',updated:'已在本项目文献中',unchanged:'已在本项目文献中'})[result.action] + (result.warnings?.length?'；部分资料达到上限，未保存。':'');
        requestId=uid();form.reset();form.hidden=true;
        root.querySelector('#literature-search').value='';history.replaceState(null,'',base);await refresh(base,result.item_id);
      } catch(e) {error.textContent=e.message; (focused && form.contains(focused) && focused!==button ? focused : form.querySelector('input,textarea'))?.focus();}
      finally {busy=false;button.disabled=false;}
    });
  });
  root.querySelector('[data-remove]')?.addEventListener('click',async event=>{
    if(!confirm('仅从本项目文献清单移除，不删除原文或笔记。确定移除？')) return;
    const article=root.querySelector('[data-item-id]');event.target.disabled=true;
    try {await post(`item/${article.dataset.itemId}/delete`,{expected_item_revision:article.dataset.revision});location.href=back.href;}
    catch(e) {message.textContent=e.message;event.target.disabled=false;}
  });
  const details=root.querySelector('#literature-collection');
  async function status() {
    if(!details?.open)return;
    try {const response=await fetch(api+'collection');const value=await response.json();if(!response.ok)throw new Error(value.error?.message||'状态不可用');
      const labels={disabled:'文献自动收录已关闭',pending_connection:'文献收录待连接',idle:'暂无待处理材料',running:'正在处理',failed:'收录失败'};
      details.querySelector('[data-collection-status]').textContent=[labels[value.status]||value.status,value.last_checked_at?'最近检查：'+value.last_checked_at:'',value.last_updated_at?'最近更新：'+value.last_updated_at:'',...(value.gaps||[]),value.last_error||''].filter(Boolean).join(' · ');
      details.querySelector('[data-retry]').hidden=value.status!=='failed';
    } catch(e) {details.querySelector('[data-collection-status]').textContent=e.message;}
  }
  details?.addEventListener('toggle',status);window.addEventListener('focus',status);
  details?.querySelector('[data-retry]').addEventListener('click',async event=>{event.target.disabled=true;try{await post('collection/retry',{});await status();}catch(e){message.textContent=e.message;}finally{event.target.disabled=false;}});
})();
