(() => {
  'use strict';
  const root = document.querySelector('#knowledge-maintenance');
  if (!root) return;
  const $ = id => root.querySelector('#km-' + id);
  const page = root.closest('#main-content') || root, lifecycle = new AbortController();
  const updates = page.querySelector('#knowledge-updates');
  const active = () => !lifecycle.signal.aborted && root.isConnected;
  const owns = event => event.detail?.root === page;
  const api = '/api/project/' + encodeURIComponent(root.dataset.project) + '/knowledge-maintenance';
  let timer, pending = false, current, origin, busy = false, listScope = 'pending';
  const labels = {pending:'待确认', applied:'已采用', rejected:'已保留原文', stale:'需要重检'};
  async function request(url, payload) {
    const res = await fetch(url, payload ? {method:'POST', headers:{'Content-Type':'application/json','X-Notebook-Request':'1'},body:JSON.stringify(payload)} : {});
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error?.message || data.error || '读取失败，请重试。');
    return data;
  }
  const format = value => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}) : '尚无';
  async function refresh() {
    clearTimeout(timer);
    if (!active() || document.hidden || pending) return;
    pending = true;
    try {
      const data = await request(api);
      let count = data.pending_count;
      if (root.dataset.page && count) count = (await request(api+'/proposals?limit=1&page_path='+encodeURIComponent(root.dataset.page))).total;
      $('pending').hidden = count === 0;
      $('pending').textContent = '待确认更新 · ' + count;
      if (updates) updates.querySelector('span').textContent = count ? '查看更新 · ' + count : '查看更新';
      $('status').textContent = `${data.executor_status}。最近检查：${format(data.last_checked_at)}；实际更新：${format(data.last_updated_at)}。待检查 ${data.remaining} 项。${data.gaps.join(' ')} ${data.last_error || ''}`;
      if (active() && !document.hidden && (data.status === 'running' || data.pending_count)) timer = setTimeout(refresh,15000);
    } catch (e) { $('error').textContent = e.message; }
    finally { pending = false; }
  }
  async function showList(scope='pending', offset=0) {
    try {
      listScope = scope;
      const data = await request(api + '/proposals?' + new URLSearchParams({scope,offset,limit:20,...(root.dataset.page ? {page_path:root.dataset.page} : {})}));
      const box = $('list');
      box.replaceChildren(); box.hidden = false;
      const heading = document.createElement('p'); heading.textContent = scope === 'pending' ? '待确认更新' : '更新历史'; box.append(heading);
      if (!data.items.length) box.append(document.createTextNode('暂无记录。'));
      data.items.forEach(item => {
        const button = document.createElement('button'); button.className = 'km-item';
        button.textContent = `${item.page_path} · ${labels[item.status] || item.status} — ${item.reason}`;
        button.onclick = () => open(item.id,button); box.append(button);
      });
      if (offset) {const b=document.createElement('button');b.textContent='上一页';b.onclick=()=>showList(scope,Math.max(0,offset-20));box.append(b);}
      if (data.has_more) {const b=document.createElement('button');b.textContent='下一页';b.onclick=()=>showList(scope,offset+20);box.append(b);}
    } catch (e) {$('error').textContent=e.message;}
  }
  async function open(id,button) {
    try {
      current = await request(api+'/proposals/'+id); origin=button;
      $('title').textContent=current.page_path;
      // Only server-sanitized Markdown HTML is used here. Diff/evidence always textContent.
      $('base').innerHTML=current.base_html; $('proposed').innerHTML=current.proposed_html;
      $('diff').textContent=current.diff; $('reason').textContent=current.reason;
      $('evidence').textContent=current.evidence_refs.map(r=>r.locator+'\n'+r.excerpt).join('\n\n');
      $('conflict').textContent=current.effective_status === 'stale' ? '原文或依据已变化，等待重新检查。' : labels[current.effective_status];
      $('accept').disabled=current.effective_status!=='pending';
      $('keep').disabled=!['pending','stale'].includes(current.status);
      if (active()) $('dialog').showModal();
    } catch(e) {$('error').textContent=e.message;}
  }
  async function decide(action) {
    if (!current || busy) return;
    busy=true; $('accept').disabled=true; $('keep').disabled=true;
    try {
      await request(api+'/proposals/'+current.id,{action,expected_base_sha256:current.base_sha256,expected_proposal_sha256:current.proposal_sha256});
      $('dialog').close(); await showList(listScope); await refresh();
    } catch(e) {
      $('conflict').textContent=e.message;
      // Refresh only the version status, never discard the visible comparison.
      try { const fresh=await request(api+'/proposals/'+current.id); current.effective_status=fresh.effective_status;current.status=fresh.status; } catch {}
    } finally {busy=false; $('accept').disabled=current.effective_status!=='pending';$('keep').disabled=!['pending','stale'].includes(current.status);}
  }
  if (updates) updates.onclick=async()=>{await showList();if(active())$('list').scrollIntoView({block:'nearest'});};
  const info = page.querySelector('#knowledge-page-info');
  const details = page.querySelector('#knowledge-page-details');
  if (info && details) details.onclick=()=>{info.open=true;info.scrollIntoView({block:'nearest'});};
  $('pending').onclick=()=>showList(); $('history').onclick=()=>showList('history');
  $('close').onclick=()=>$('dialog').close();
  $('dialog').addEventListener('close',()=>{if(!active())return;if(origin?.isConnected)origin.focus();else $('history').focus();});
  $('accept').onclick=()=>decide('accept'); $('keep').onclick=()=>decide('keep');
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(active()&&!document.hidden)refresh();},{signal:lifecycle.signal});
  window.addEventListener('focus',refresh,{signal:lifecycle.signal});
  document.addEventListener('workbench:before-leave',event=>{
    if(owns(event)&&active()&&busy){event.preventDefault();$('conflict').textContent='请等待当前操作完成。';}
  },{signal:lifecycle.signal});
  window.addEventListener('beforeunload',event=>{if(active()&&busy){event.preventDefault();event.returnValue='';}},{signal:lifecycle.signal});
  document.addEventListener('workbench:leave',event=>{if(owns(event)){clearTimeout(timer);if($('dialog').open)$('dialog').close();}},{signal:lifecycle.signal});
  document.addEventListener('workbench:enter',event=>{if(owns(event)&&active())refresh();},{signal:lifecycle.signal});
  document.addEventListener('workbench:dispose',event=>{if(owns(event)){clearTimeout(timer);lifecycle.abort();}},{signal:lifecycle.signal});
  refresh();
})();
