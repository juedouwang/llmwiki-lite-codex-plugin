/* Continuous Markdown reports. No runtime dependencies or model calls. */
(() => {
  'use strict';
  const page = document.getElementById('main-content'); if (!page) return;
  const $ = id => page.querySelector('#' + id), lifecycle = new AbortController();
  const active = () => !lifecycle.signal.aborted && page.isConnected;
  const owns = event => event.detail?.root === page;
  let stop = () => {}, resume = () => {}, guard = () => false;
  document.addEventListener('workbench:before-leave', event => {
    if (owns(event) && active() && guard()) event.preventDefault();
  }, {signal: lifecycle.signal});
  window.addEventListener('beforeunload', event => {
    if (active() && guard()) { event.preventDefault(); event.returnValue = ''; }
  }, {signal: lifecycle.signal});
  document.addEventListener('workbench:leave', event => { if (owns(event)) stop(); }, {signal: lifecycle.signal});
  document.addEventListener('workbench:enter', event => { if (owns(event) && active()) resume(); }, {signal: lifecycle.signal});
  document.addEventListener('workbench:dispose', event => { if (owns(event)) { stop(); lifecycle.abort(); } }, {signal: lifecycle.signal});
  async function request(url, data) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const res = await fetch(url, data === undefined ? {signal: controller.signal} : {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Notebook-Request': '1'},
        body: JSON.stringify(data), signal: controller.signal
      });
      const result = await res.json();
      if (!res.ok || result.ok === false) {
        const error = new Error(result.error?.message || result.error || '请求失败');
        error.code = result.error?.code; error.status = res.status; throw error;
      }
      return result;
    } finally { clearTimeout(timeout); }
  }
  const configForm = $('report-settings-form');
  if (configForm) {
    let config, dirty = false, saving = false;
    configForm.addEventListener('input', () => { dirty = true; });
    configForm.addEventListener('change', () => { dirty = true; });
    guard = () => {
      if (!dirty && !saving) return false;
      $('report-settings-status').textContent = saving ? '保存中，请稍候。' : '设置尚未保存，请先保存当前修改。';
      return true;
    };
    function fill(value) {
      config = value; dirty = false;
      $('report-enabled').checked = value.enabled;
      if ($('report-capture')) $('report-capture').checked = (value.capture_hosts || []).includes('codex');
      $('knowledge-enabled').checked = value.knowledge_enabled;
      $('literature-enabled').checked = value.literature_enabled;
      $('report-daily-time').value = value.daily_time || '';
      $('report-weekly-time').value = value.weekly_time || '';
      $('report-weekday').value = value.weekly_weekday || '';
      if ($('report-owner')) $('report-owner').value = value.weekly_owner_project_id || '';
      $('report-start-date').value = value.start_date || '';
      configForm.querySelectorAll('[name=report-project]').forEach(e => { e.checked = value.project_ids.includes(e.value); });
      const runtime = value.runtime || {};
      $('report-connection').textContent = ({paused: '已暂停', pending: '待连接执行端', configured: '已配置宿主计划（不代表已成功执行）'})[value.connection] + ' · 最近成功：' + (runtime.last_success_at || '暂无') + (runtime.last_error ? ' · 最近错误：' + runtime.last_error : '') + (runtime.last_started_at && Date.now() - Date.parse(runtime.last_started_at) > 7200000 ? ' · 尚未收到最近检查' : '');
    }
    request('/api/reports/settings').then(fill).catch(e => { $('report-settings-status').textContent = e.message; });
    configForm.onsubmit = async event => {
      event.preventDefault(); if (!config || saving) return;
      if (config.report_scope !== 'workspace') {
        $('report-settings-status').textContent = '服务仍是旧版本，请重启本地网页服务；未保存设置，避免恢复旧的项目归档逻辑。';
        return;
      }
      saving = true;
      try {
        fill(await request('/api/reports/settings', {expected_revision: config.revision,
          enabled: $('report-enabled').checked,
          ...($('report-capture') ? {capture_hosts: $('report-capture').checked ? ['codex'] : []} : {}),
          knowledge_enabled: $('knowledge-enabled').checked, literature_enabled: $('literature-enabled').checked,
          project_ids: [...configForm.querySelectorAll('[name=report-project]:checked')].map(e => e.value),
          daily_time: $('report-daily-time').value || null, weekly_time: $('report-weekly-time').value || null,
          weekly_weekday: Number($('report-weekday').value) || null,
          start_date: $('report-start-date').value || null}));
        $('report-settings-status').textContent = '已保存。没有启动后台模型或终端。';
      } catch (e) { $('report-settings-status').textContent = e.message; }
      finally { saving = false; }
    };
    $('report-copy-enable').onclick = async () => {
      if (!config) return;
      if (!config.workflow_cli || !config.workflow_skill) {
        $('report-settings-status').textContent = '服务仍是旧版本，请重启本地网页服务后连接自动整理。';
        return;
      }
      try {
        const names = [...configForm.querySelectorAll('[name=report-project]')].filter(e => config.project_ids.includes(e.value)).map(e => e.parentElement.textContent.trim());
        await navigator.clipboard.writeText(`请启用科研共享计划。配置文件：${config.config_path}，配置版本：${config.revision}，项目：${names.join('、')}。先阅读 ${config.workflow_skill}，使用源码入口 ${config.workflow_cli}，先核验已保存的项目、时间及取材授权，使用官方工具创建或复用每天北京时间18:00跟进（或按已保存时刻配置），再以真实回执绑定同一配置。不要改缓存，不使用后台 exec。未完成首次实际运行前不能声称自动生成已验收。`);
        $('report-settings-status').textContent = '已复制连接指令，可交给当前宿主助手。';
      } catch (e) { $('report-settings-status').textContent = e.message; }
    };
    return;
  }
  const list = $('research-reports-list');
  if (list) {
    list.querySelectorAll('.report-filters select').forEach(select=>{select.onchange=()=>select.form.requestSubmit();});
    let creating = false;
    guard = () => {
      if (!creating && !$('report-create').open) return false;
      $('report-create-error').textContent = creating ? '创建中，请稍候。' : '请先完成或关闭当前创建窗口。';
      return true;
    };
    $('report-new').onclick = () => $('report-create').showModal();
    $('report-create-submit').onclick = async () => {
      if (creating || !active()) return;
      creating = true;
      const button = $('report-create-submit'); button.disabled = true;
      try {
        const ids = [...list.querySelectorAll('input[name=project]:checked')].map(e => e.value);
        const item = await request(list.dataset.api || `/api/project/${list.dataset.project}/reports`, {action: 'create',
          kind: list.dataset.kind, period_start: $('report-date').value,
          project_ids: ids});
        const target = new URL(item.url, location.href);
        if (list.hasAttribute('data-context')) target.searchParams.set('context', list.dataset.context);
        target.searchParams.set('return', location.pathname + location.search);
        if (active()) { creating = false; $('report-create').close(); location.assign(target.pathname + target.search); }
      } catch (error) { $('report-create-error').textContent = error.message; }
      finally { creating = false; button.disabled = false; }
    };
    return;
  }
  const root = $('research-report'); if (!root) return;
  // List links preserve filters and the selected row; direct deep links keep their default.
  try {
    const target = new URL(new URLSearchParams(location.search).get('return') || '', location.href);
    if (target.origin === location.origin && target.pathname === '/reports') {
      if (root.hasAttribute('data-context')) target.searchParams.set('context', root.dataset.context);
      root.querySelector('[aria-label="返回列表"]').href = target.pathname + target.search + target.hash;
    }
  } catch { /* Invalid navigation hints never affect the document. */ }
  const {project,kind,date} = root.dataset, base=root.dataset.api || `/api/project/${project}/reports`, api=`${base}/${kind}/${date}`;
  if (!window.ResearchDocument || !$('report-preview-mode')) {
    const notice=document.createElement('p');notice.setAttribute('role','alert');notice.textContent='网页服务仍是旧版本，请重启本地服务后再编辑。';root.prepend(notice);return;
  }
  const {mount,node} = window.ResearchDocument;
  const load=()=>request(api), action=(name,item,extra={})=>request(api,{action:name,expected_revision:item.revision,...extra});
  const fmt=t=>t ? new Date(t).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}) : '未知';
  let ctx;
  async function version(query, label, number) {
    const result=await request(api+query);
    ctx.modal(label,[node(result.body,'pre'),...result.comments.map(c=>node('批注：'+c.text))],number ? [['恢复为修订草稿',async()=>{
      if (!(await ctx.flush())) return;
      await ctx.display(await action('restore',ctx.current(),{version:number}));ctx.close();
    }]]:[]);
  }
  ctx=mount({root,prefix:'report',key:`llmwiki-report:${project}:${kind}:${date}`,load,
    save:(value,item)=>action('save',item,{body:value.body,comments:value.comments}),
    startEdit:item=>action('start_edit',item), confirm:item=>action('confirm',item),
    async preview(body){return (await request(base+'/preview',{kind,period_start:date,body})).html;},
    async upload(data){const path=base==='/api/reports'?base+'/upload':`/api/project/${project}/notebook/upload`;return '../../assets/'+(await request(path,{data})).image;},
    label:item=>item ? (item.mode==='formal'?'正式版':item.metadata.versions.length?'修订草稿':'草稿'):'',
    filename:()=>`${kind}-${date}.md`,
    info:item=>item ? [['范围',`${item.metadata.period_start} — ${item.metadata.period_end}`],['创建时间',fmt(item.metadata.created_at)],['修改时间',fmt(item.metadata.updated_at)],['参与项目',item.metadata.project_ids.join('、')],['正式版本',String(item.metadata.versions.length)]]:[],
    async sources(c){const latest=await load(), s=latest.selected_metadata||{};c.modal('材料与来源',[node(JSON.stringify({sources:s.sources||[],gaps:s.gaps||[]},null,2),'pre')]);},
    async history(c){const latest=await load();c.modal('版本历史',[node('只有确认时才产生正式版本，自动保存不增加正式历史。')],[...latest.metadata.versions.map(v=>['查看 v'+v.number,()=>version('?version='+v.number,'正式版 v'+v.number,v.number)]),...(latest.metadata.previous_draft?[['替换前草稿',()=>version('?view=previous','替换前草稿')]]:[])]);},
    async candidate(c){const result=await request(api+'?view=candidate');c.modal('比较整理结果',[node('当前稿','h3'),node(c.content().body,'pre'),node('新候选','h3'),node(result.body,'pre'),node('采用将保留替换前草稿和人工批注，不会自动确认为正式版。')],[['确认采用新稿',async()=>{
      if (!(await c.flush()))return;await c.display(await action('adopt_candidate',c.current(),{expected_candidate_sha256:result.body_sha256}));c.close();
    }]]);},
    async regenerate(c){if (!(await c.flush()))return;const result=await action('regenerate',c.current());c.refreshMetadata(result);c.message('整理请求已保存，等待已连接的执行端处理；不会启动后台终端。');}
  });
  async function poll(){
    const item=ctx.current();if(!active()||polling||document.hidden||!item||!ctx.clean()||!(item.metadata.generation.requested||item.metadata.generation.state==='running'))return;
    polling=true;try{const result=await load();if(!active()||!ctx.clean())return;if(result.revision!==ctx.current().revision)ctx.message('正文有更新，重新打开可查看；当前编辑不会被替换。');else ctx.refreshMetadata(result);}catch{/* Background checks never interrupt typing. */}finally{polling=false;}
  }
  let timer, polling=false;
  stop=()=>{clearInterval(timer);timer=null;};
  resume=()=>{if(!active()||document.hidden||timer)return;poll();timer=setInterval(poll,10000);};
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else resume();},{signal:lifecycle.signal});
  resume();
})();
