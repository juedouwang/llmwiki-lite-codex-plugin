/* Explicit, local tasks. No inferred percentages and no background agent process. */
(()=>{
  'use strict';
  const progressRoot=document.querySelector('#research-progress');
  const homeResume=document.querySelector('.resume-block[data-project]');
  const projectId=(progressRoot||homeResume||{}).dataset&& (progressRoot||homeResume).dataset.project;
  if(!projectId)return;
  const endpoint=`/api/project/${encodeURIComponent(projectId)}/progress`;
  const labels={planned:'未开始',active:'进行中',blocked:'卡住了',done:'已完成'};
  const fieldLabels={title:'任务名称',status:'状态',start:'开始日期',end:'结束日期',checkpoint:'上次做到哪',next_step:'下一步',record_id:'关联笔记'};
  const fields=Object.keys(fieldLabels);
  const emptyText='暂无记录';
  let tasks=[],candidates=[],revision='',ready=false,busy=false,editing=null,baseline=null,conflict=false,linkTargets=[],recordsLoaded=false,inflight=false,timer=null,pollError='';
  const $=q=>progressRoot?progressRoot.querySelector(q):null;
  const form=$('#progress-form'), dialog=$('#progress-dialog');
  const el=(tag,cls,text)=>{const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x;};
  const button=(text,fn,cls='')=>{const b=el('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;};
  const iso=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const from=s=>new Date(s+'T12:00:00');
  const add=(s,n)=>{const d=from(s);d.setDate(d.getDate()+n);return iso(d);};
  const delta=(a,b)=>Math.round((Date.UTC(...a.split('-').map((v,i)=>i===1?Number(v)-1:Number(v)))-Date.UTC(...b.split('-').map((v,i)=>i===1?Number(v)-1:Number(v))))/86400000);
  const today=()=>iso(new Date());
  const weekStart=()=>add(today(),-((from(today()).getDay()+6)%7));
  let start=weekStart(),days=7;
  function notice(text){if(!progressRoot)return;const node=$('#progress-message');if(!node)return;node.textContent=text;node.hidden=!text;}
  function error(text){if(!$('#progress-error'))return;$('#progress-error').textContent=text;$('#progress-error').hidden=!text;}
  function preview(text){const value=text||emptyText;return value;}
  function effective(task,field){const context=task.effective_context||{};if(context[field]!==undefined)return context[field];return task[field]||'';}
  function modeOf(task,field){return (task.context_mode&&task.context_mode[field])|| (task[field]?'manual':'auto');}
  function resumeTasks(){return tasks.filter(t=>['active','blocked'].includes(t.status)).sort((a,b)=>b.updated_at.localeCompare(a.updated_at)||a.id.localeCompare(b.id));}
  function doneTasks(){
    const done=tasks.filter(t=>t.status==='done');
    const known=done.filter(t=>t.completed_at).sort((a,b)=>b.completed_at.localeCompare(a.completed_at)||a.id.localeCompare(b.id));
    const unknown=done.filter(t=>!t.completed_at).sort((a,b)=>a.id.localeCompare(b.id));
    return known.concat(unknown);
  }
  async function parse(response){
    const result=await response.json();if(!response.ok||!result.ok){const e=new Error(result.error||'请求失败');e.code=response.status;throw e;}return result;
  }
  async function request(payload){
    const response=await fetch(endpoint,payload?{method:'POST',headers:{'Content-Type':'application/json','X-Notebook-Request':'1'},body:JSON.stringify({...payload,revision})}:{cache:'no-store'});
    return parse(response);
  }
  async function requestView(view, extra){
    const response=await fetch(`${endpoint}?view=${view}`,{cache:'no-store',...extra});
    return parse(response);
  }
  const recordUrl=id=>`/project/${encodeURIComponent(projectId)}/records/${id.replace(/^records\//,'').split('/').map(encodeURIComponent).join('/')}`;
  function renderSource(recordId, missing){
    const source=$('#progress-source');if(!source)return;source.replaceChildren();
    if(!recordId)return;
    if(missing){source.append(el('p','meta','来源不可用'));return;}
    const link=el('a','','打开关联笔记');link.href=recordUrl(recordId);source.append(link);
  }
  function fillTargets(current){
    const select=form&&form.elements.namedItem('record_id');if(!select)return;
    const items=[{id:'',title:'不关联'},...linkTargets];
    if(current&&!linkTargets.some(t=>t.id===current))items.push({id:current,title:`找不到的笔记 · ${current}`});
    const selected=select.value||current||'';
    select.replaceChildren(...items.map(t=>{const o=el('option','',t.title);o.value=t.id;return o;}));
    select.value=items.some(t=>t.id===selected)?selected:(current||'');
  }
  async function ensureRecords(){
    if(recordsLoaded)return;
    const data=await requestView('records');
    candidates=data.candidates||[];linkTargets=data.records||[];recordsLoaded=true;
    if($('#progress-import'))$('#progress-import').hidden=!candidates.length;
  }
  function applyTasks(data, preserveEdit){
    tasks=data.tasks||[];
    if(!preserveEdit)revision=data.revision;
    if(data.warning)pollError=data.warning;
    else if(!preserveEdit)pollError='';
  }
  function fillResume(node, items){
    node.hidden=!items.length;
    if(!items.length){node.replaceChildren();return;}
    const heading=el('h2','','继续上次');
    if(node.classList.contains('resume-block')){
      const list=el('ul','resume-list');
      items.slice(0,3).forEach(task=>{
        const item=el('li','resume-item');
        const title=el('a','resume-title',task.title);title.href=`/project/${encodeURIComponent(projectId)}/todos#task-${task.id}`;
        item.append(title, el('p','meta','上次做到哪：'+preview(effective(task,'checkpoint'))), el('p','meta','下一步：'+preview(effective(task,'next_step'))));
        if(task.record_id){const note=el('a','resume-note','打开关联笔记');note.href=recordUrl(task.record_id);item.append(note);}
        list.append(item);
      });
      node.replaceChildren(heading, list);
      return;
    }
    node.replaceChildren(heading);
    items.slice(0,3).forEach(task=>{
      const b=button('',()=>openTask(task),'progress-resume-item');b.dataset.id=task.id;
      b.append(el('strong','',task.title),el('span','',preview(effective(task,'checkpoint'))),el('span','meta','下一步：'+preview(effective(task,'next_step'))));
      if(task.record_id){const note=el('a','resume-note','打开关联笔记');note.href=recordUrl(task.record_id);note.addEventListener('click',event=>event.stopPropagation());b.append(note);}
      node.append(b);
    });
  }
  function renderHome(){
    if(!homeResume)return;
    const items=resumeTasks();
    homeResume.hidden=!items.length;
    if(!items.length){homeResume.replaceChildren();return;}
    fillResume(homeResume, items);
  }
  function updateAutoPanel(task){
    const status=$('#progress-auto-status'), body=$('#progress-auto-body');
    if(!status)return;
    const auto=task.auto_context;
    document.querySelectorAll('.progress-use-auto').forEach(btn=>{
      const field=btn.dataset.field;
      btn.hidden=!(auto&&modeOf(task,field)==='manual');
    });
    if(!auto){status.textContent='自动整理尚未接通';status.hidden=false;if(body)body.hidden=true;return;}
    status.hidden=true;if(body){body.hidden=false;body.replaceChildren();
      body.append(el('p','meta','生成时间：'+new Date(auto.generated_at).toLocaleString('zh-CN')));
      if(auto.source_record_id){const link=el('a','','打开来源记录');link.href=recordUrl(auto.source_record_id);body.append(link);}
      body.append(el('p','','上次做到哪：'+(auto.checkpoint||emptyText)));
      body.append(el('p','','下一步：'+(auto.next_step||emptyText)));
    }
  }
  function renderInfo(task){
    const box=$('#progress-info div');if(!box)return;
    box.replaceChildren(
      el('p','meta','创建时间：'+new Date(task.created_at).toLocaleString('zh-CN')),
      el('p','meta','最近修改：'+new Date(task.updated_at).toLocaleString('zh-CN')),
      el('p','meta','完成时间：'+(task.completed_at?new Date(task.completed_at).toLocaleString('zh-CN'):(task.status==='done'?'完成时间未记录':'未完成'))),
    );
  }
  async function load(){
    const data=await requestView('summary');
    applyTasks(data,false);ready=true;render();renderHome();await openFromHash();
  }
  async function openFromHash(){
    if(!progressRoot)return;
    const match=location.hash.match(/^#task-([a-f0-9]{32})$/);if(!match)return;
    const task=tasks.find(t=>t.id===match[1]);if(task)openTask(task);
  }
  async function persist(payload){if(busy||!ready)throw new Error('请等待当前操作完成。');busy=true;
    try{
      const data=await request(payload);
      applyTasks(data,false);
      if(data.candidates)candidates=data.candidates;
      else if(payload.action==='import')candidates=candidates.filter(c=>!tasks.some(t=>t.id===c.id));
      render();renderHome();notice(pollError);
    }
    finally{busy=false;}
  }
  function currentModes(){
    return {checkpoint:form.elements.namedItem('checkpoint_mode').value,next_step:form.elements.namedItem('next_step_mode').value};
  }
  function draft(){
    const values=Object.fromEntries(fields.map(k=>[k,form.elements.namedItem(k).value]));
    const modes=currentModes();
    ['checkpoint','next_step'].forEach(field=>{
      if(modes[field]==='auto'&&baseline)values[field]=baseline[field]||'';
    });
    values.context_mode=modes;
    return values;
  }
  function hasChanges(){
    if(!dialog||!dialog.open||!baseline)return false;
    const now=draft();
    return fields.some(k=>{
      if(k==='checkpoint'||k==='next_step')return now[k]!==(baseline[k]||'') || now.context_mode[k]!==modeOf(baseline,k);
      return now[k]!==(baseline[k]||'');
    });
  }
  function openTask(task){
    if(!form)return;
    editing=task.id;baseline=structuredClone(task);conflict=false;error('');if($('#progress-reload'))$('#progress-reload').hidden=true;
    fields.forEach(k=>{
      if(k==='record_id')return;
      form.elements.namedItem(k).value=(k==='checkpoint'||k==='next_step'?effective(task,k):task[k])||'';
    });
    baseline.checkpoint=form.elements.namedItem('checkpoint').value;
    baseline.next_step=form.elements.namedItem('next_step').value;
    form.elements.namedItem('checkpoint_mode').value=modeOf(task,'checkpoint');
    form.elements.namedItem('next_step_mode').value=modeOf(task,'next_step');
    fillTargets(task.record_id);renderSource(task.record_id, !!(task.record_id&&recordsLoaded&&!linkTargets.some(t=>t.id===task.record_id)));
    form.querySelector('[type=submit]').disabled=false;form.querySelector('[type=submit]').textContent='保存';
    const history=$('#progress-history');history.open=false;
    history.querySelector('div').replaceChildren(...[...(task.history||[])].reverse().map(h=>{
      const row=el('div','progress-history-entry');row.append(el('p','meta',new Date(h.at).toLocaleString('zh-CN')+' · '+labels[h.status]));
      row.append(el('p','',h.title));if(h.start)row.append(el('p','meta',`${h.start} — ${h.end}`));
      if(h.checkpoint)row.append(el('p','',h.checkpoint));if(h.next_step)row.append(el('p','',`下一步：${h.next_step}`));
      if(h.status==='done')row.append(el('p','meta',h.completed_at?('完成时间：'+new Date(h.completed_at).toLocaleString('zh-CN')):'完成时间未记录'));
      return row;
    }));
    renderInfo(task);updateAutoPanel(task);
    dialog.showModal();form.elements.namedItem('checkpoint').focus();
    ensureRecords().then(()=>{
      if(editing!==task.id)return;
      fillTargets(form.elements.namedItem('record_id').value||task.record_id);
      renderSource(form.elements.namedItem('record_id').value, !!(form.elements.namedItem('record_id').value&&!linkTargets.some(t=>t.id===form.elements.namedItem('record_id').value)));
    }).catch(e=>notice(e.message));
  }
  function closeTask(){if(busy)return;if(hasChanges()&&!confirm('尚未保存，放弃这次修改吗？'))return;dialog.close();}
  function taskButton(task){
    const row=el('div','progress-task-row'),status=el('select','progress-inline-status status-'+task.status);
    status.setAttribute('aria-label',task.title+'的状态');
    Object.entries(labels).forEach(([value,label])=>{const option=el('option','',label);option.value=value;status.append(option);});status.value=task.status;
    status.addEventListener('change',async()=>{const value=status.value;status.disabled=true;
      try{await persist({action:'update',id:task.id,task:{title:task.title,status:value,start:task.start,end:task.end,checkpoint:task.checkpoint,next_step:task.next_step,record_id:task.record_id||'',context_mode:task.context_mode}});}
      catch(e){notice(e.message);status.value=task.status;if(e.code===409)await load().catch(()=>{});}
      finally{status.disabled=false;}
    });
    const b=button(task.title,()=>openTask(task),'progress-task');b.dataset.id=task.id;
    const step=effective(task,'next_step');if(step)b.title=step;row.append(status,b);return row;
  }
  function render(){
    if(!progressRoot)return;
    if($('#progress-import'))$('#progress-import').hidden=recordsLoaded?!candidates.length:false;
    fillResume($('#progress-resume'), resumeTasks());
    const end=add(start,days-1),timeline=$('#progress-timeline');timeline.replaceChildren();
    $('#progress-range-label').textContent=`${start.slice(5).replace('-','/')} — ${end.slice(5).replace('-','/')}`;
    const grid=el('div','progress-grid');grid.style.setProperty('--days',days);
    const head=el('div','progress-grid-row progress-grid-head');head.append(el('span','progress-name','任务'));
    for(let i=0;i<days;i++){const d=add(start,i);head.append(el('span','progress-day'+(d===today()?' is-today':''),['日','一','二','三','四','五','六'][from(d).getDay()]+' '+Number(d.slice(8))));}grid.append(head);
    const scheduled=tasks.filter(t=>t.status!=='done'&&t.start);
    scheduled.filter(t=>t.end>=start&&t.start<=end).forEach(task=>{
      const row=el('div','progress-grid-row');row.append(taskButton(task));
      const track=el('div','progress-track');const bar=button('',()=>openTask(task),'progress-bar status-'+task.status);
      bar.style.gridColumn=`${Math.max(0,delta(task.start,start))+1} / ${Math.min(days-1,delta(task.end,start))+2}`;
      bar.textContent=effective(task,'next_step')||labels[task.status];bar.title=`${task.title} · ${task.start} — ${task.end}`;bar.setAttribute('aria-label',bar.title);track.append(bar);row.append(track);grid.append(row);
    });timeline.append(grid);
    if(grid.children.length===1)timeline.append(el('p','progress-empty','这段时间没有已排期的任务。'));
    const outside=scheduled.filter(t=>t.end<start||t.start>end);
    if(outside.length)timeline.append(button(`${outside.length} 项任务在此范围外 · 查看最近一项`,()=>{start=add(outside.sort((a,b)=>Math.abs(delta(a.start,today()))-Math.abs(delta(b.start,today())))[0].start,-1);render();},'progress-outside'));
    const unscheduled=tasks.filter(t=>t.status!=='done'&&!t.start),bucket=$('#progress-unscheduled > div');
    bucket.replaceChildren(...unscheduled.map(taskButton));if(!unscheduled.length)bucket.append(el('p','meta',tasks.length?'没有未排期任务':'先添加一个任务，不必先想好日期。'));
    const done=doneTasks();$('#progress-done').hidden=!done.length;$('#progress-done summary span').textContent=String(done.length);$('#progress-done > div').replaceChildren(...done.map(taskButton));
    if(editing){const current=tasks.find(t=>t.id===editing);if(current&&dialog.open){updateAutoPanel(current);renderInfo(current);}}
    if(pollError)notice(pollError);
  }
  async function refreshSummary(){
    if(inflight||document.hidden)return;
    inflight=true;
    const ac=new AbortController();
    const timeout=setTimeout(()=>ac.abort(),3000);
    try{
      const data=await requestView('summary',{signal:ac.signal});
      applyTasks(data, !!(dialog&&dialog.open));
      render();renderHome();
    }catch(e){
      if(e.name!=='AbortError')notice(e.message||pollError);
    }finally{clearTimeout(timeout);inflight=false;}
  }
  function stopTimer(){if(timer){clearInterval(timer);timer=null;}}
  function startTimer(){if(document.hidden||timer)return;timer=setInterval(refreshSummary,5000);}
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden)stopTimer();
    else{refreshSummary();startTimer();}
  });
  if(progressRoot){
    $('#progress-add').addEventListener('submit',async event=>{
      event.preventDefault();const input=event.target.elements.title,submit=event.target.querySelector('button');const title=input.value.trim();if(!title)return;submit.disabled=true;
      try{await persist({action:'create',task:{title,status:'planned'}});input.value='';input.focus();}
      catch(e){notice(e.message);if(e.code===409)await load().catch(()=>{});}
      finally{submit.disabled=false;}
    });
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(conflict)return;const submit=form.querySelector('[type=submit]');submit.disabled=true;error('');
      try{await persist({action:'update',id:editing,task:draft()});dialog.close();baseline=null;}
      catch(e){error(e.message);if(e.code===409){conflict=true;$('#progress-reload').hidden=false;}}
      finally{submit.disabled=conflict;}
    });
    ['checkpoint','next_step'].forEach(field=>{
      form.elements.namedItem(field).addEventListener('input',()=>{form.elements.namedItem(field+'_mode').value='manual';const current=tasks.find(t=>t.id===editing);if(current)updateAutoPanel({...current,context_mode:{...currentModes()}});});
    });
    document.querySelectorAll('.progress-use-auto').forEach(btn=>btn.addEventListener('click',()=>{
      const field=btn.dataset.field;const current=tasks.find(t=>t.id===editing);if(!current||!current.auto_context)return;
      form.elements.namedItem(field).value=current.auto_context[field]||'';
      form.elements.namedItem(field+'_mode').value='auto';
      updateAutoPanel({...current,context_mode:currentModes()});
    }));
    $('#progress-reload').addEventListener('click',async()=>{
      try{const current=draft();await load();const latest=tasks.find(t=>t.id===editing);if(!latest)throw new Error('任务已不存在，请复制当前内容后重新添加。');
        const conflicts=[];fields.forEach(k=>{
          const latestValue=k==='checkpoint'||k==='next_step'?effective(latest,k):latest[k];
          const baseValue=k==='checkpoint'||k==='next_step'?effective(baseline,k):baseline[k];
          if(current[k]===baseValue)form.elements.namedItem(k).value=latestValue||'';
          else if(latestValue!==baseValue&&latestValue!==current[k])conflicts.push(`${fieldLabels[k]}（服务器）：${latestValue||'空'}`);
        });
        form.elements.namedItem('checkpoint_mode').value=modeOf(latest,'checkpoint');
        form.elements.namedItem('next_step_mode').value=modeOf(latest,'next_step');
        baseline=structuredClone(latest);conflict=false;$('#progress-reload').hidden=true;
        error(conflicts.length?'以下字段双方都修改过，请核对后保存。当前填写仍保留。\n'+conflicts.join('\n'):'已合并其他字段的更新，当前填写保留，请检查后保存。');
        form.querySelector('[type=submit]').disabled=false;form.querySelector('[type=submit]').textContent='确认并保存';
      }catch(e){error(e.message);}
    });
    $('#progress-close').addEventListener('click',closeTask);dialog.addEventListener('cancel',event=>{event.preventDefault();closeTask();});
    form.elements.namedItem('record_id').addEventListener('change',event=>renderSource(event.target.value, recordsLoaded&&event.target.value&&!linkTargets.some(t=>t.id===event.target.value)));
    window.addEventListener('beforeunload',event=>{if(hasChanges()||busy){event.preventDefault();event.returnValue='';}});
    $('#progress-prev').addEventListener('click',()=>{start=add(start,-days);render();});$('#progress-next').addEventListener('click',()=>{start=add(start,days);render();});$('#progress-today').addEventListener('click',()=>{start=weekStart();render();});$('#progress-days').addEventListener('change',event=>{days=Number(event.target.value);render();});
    $('#progress-import').addEventListener('click',async()=>{
      $('#progress-import-error').hidden=true;
      try{
        await ensureRecords();
        const container=$('#progress-candidates');container.replaceChildren();
        if(!candidates.length)container.append(el('p','meta','没有可导入的旧待办。'));
        candidates.forEach(c=>{const row=el('label','progress-candidate'),input=el('input');input.type='checkbox';input.value=c.id;row.append(input,el('span','',c.title));container.append(row);});
        $('#progress-import-dialog').showModal();
      }catch(e){notice(e.message);}
    });
    $('#progress-import-close').addEventListener('click',()=>$('#progress-import-dialog').close());
    $('#progress-import-form').addEventListener('submit',async event=>{
      event.preventDefault();const selected=Array.from($('#progress-candidates').querySelectorAll(':checked')).map(n=>n.value);if(!selected.length)return;
      const completed=candidates.filter(c=>{try{return localStorage.getItem('llmwiki-todo-'+c.legacy_key)==='1';}catch{return false;}}).map(c=>c.id);
      const submit=event.target.querySelector('.primary');submit.disabled=true;
      try{await persist({action:'import',ids:selected,completed});$('#progress-import-dialog').close();}
      catch(e){$('#progress-import-error').textContent=e.message;$('#progress-import-error').hidden=false;if(e.code===409)await load().catch(()=>{});}
      finally{submit.disabled=false;}
    });
  }
  load().then(startTimer).catch(e=>notice(`科研进度未载入：${e.message}。请刷新重试；不会覆盖原文件。`));
})();
