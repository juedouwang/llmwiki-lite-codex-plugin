/* Explicit, local tasks. No inferred percentages and no background agent process. */
(()=>{
  'use strict';
  const root=document.querySelector('#research-progress');if(!root)return;
  const $=q=>root.querySelector(q), form=$('#progress-form'), dialog=$('#progress-dialog');
  const endpoint=`/api/project/${encodeURIComponent(root.dataset.project)}/progress`;
  const labels={planned:'未开始',active:'进行中',blocked:'卡住了',done:'已完成'};
  const fieldLabels={title:'任务名称',status:'状态',start:'开始日期',end:'结束日期',checkpoint:'上次做到哪',next_step:'下一步'};
  const fields=Object.keys(fieldLabels);
  let tasks=[],candidates=[],revision='',ready=false,busy=false,editing=null,baseline=null,conflict=false;
  const el=(tag,cls,text)=>{const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x;};
  const button=(text,fn,cls='')=>{const b=el('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;};
  const iso=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const from=s=>new Date(s+'T12:00:00');
  const add=(s,n)=>{const d=from(s);d.setDate(d.getDate()+n);return iso(d);};
  const delta=(a,b)=>Math.round((Date.UTC(...a.split('-').map((v,i)=>i===1?Number(v)-1:Number(v)))-Date.UTC(...b.split('-').map((v,i)=>i===1?Number(v)-1:Number(v))))/86400000);
  const today=()=>iso(new Date());
  let start=add(today(),-3),days=14;
  function notice(text){const node=$('#progress-message');node.textContent=text;node.hidden=!text;}
  function error(text){$('#progress-error').textContent=text;$('#progress-error').hidden=!text;}
  async function request(payload){
    const response=await fetch(endpoint,payload?{method:'POST',headers:{'Content-Type':'application/json','X-Notebook-Request':'1'},body:JSON.stringify({...payload,revision})}:{cache:'no-store'});
    const result=await response.json();if(!response.ok||!result.ok){const e=new Error(result.error||'请求失败');e.code=response.status;throw e;}return result;
  }
  async function load(){const data=await request();tasks=data.tasks;candidates=data.candidates;revision=data.revision;ready=true;render();}
  async function persist(payload){if(busy||!ready)throw new Error('请等待当前操作完成。');busy=true;
    try{const data=await request(payload);tasks=data.tasks;revision=data.revision;candidates=candidates.filter(c=>!tasks.some(t=>t.id===c.id));render();notice('');}
    finally{busy=false;}
  }
  function draft(){return Object.fromEntries(fields.map(k=>[k,form.elements.namedItem(k).value]));}
  function hasChanges(){return dialog.open&&baseline&&fields.some(k=>draft()[k]!==baseline[k]);}
  function openTask(task){
    editing=task.id;baseline=structuredClone(task);conflict=false;error('');$('#progress-reload').hidden=true;
    fields.forEach(k=>form.elements.namedItem(k).value=task[k]);form.querySelector('[type=submit]').disabled=false;form.querySelector('[type=submit]').textContent='保存';
    const source=$('#progress-source');source.replaceChildren();
    if(task.record_id){const link=el('a','','关联科研记录');link.href=`/project/${encodeURIComponent(root.dataset.project)}/records/${task.record_id.replace(/^records\//,'').split('/').map(encodeURIComponent).join('/')}`;source.append(link);}
    const history=$('#progress-history');history.open=false;
    history.querySelector('div').replaceChildren(...[...(task.history||[])].reverse().map(h=>{
      const row=el('div','progress-history-entry');row.append(el('p','meta',new Date(h.at).toLocaleString('zh-CN')+' · '+labels[h.status]));
      row.append(el('p','',h.title));if(h.start)row.append(el('p','meta',`${h.start} — ${h.end}`));
      if(h.checkpoint)row.append(el('p','',h.checkpoint));if(h.next_step)row.append(el('p','',`下一步：${h.next_step}`));return row;
    }));
    dialog.showModal();form.elements.namedItem('checkpoint').focus();
  }
  function closeTask(){if(busy)return;if(hasChanges()&&!confirm('尚未保存，放弃这次修改吗？'))return;dialog.close();}
  function taskButton(task){
    const row=el('div','progress-task-row'),status=el('select','progress-inline-status status-'+task.status);
    status.setAttribute('aria-label',task.title+'的状态');
    Object.entries(labels).forEach(([value,label])=>{const option=el('option','',label);option.value=value;status.append(option);});status.value=task.status;
    status.addEventListener('change',async()=>{const value=status.value;status.disabled=true;
      try{await persist({action:'update',id:task.id,task:{...task,status:value}});}
      catch(e){notice(e.message);status.value=task.status;if(e.code===409)await load().catch(()=>{});}
      finally{status.disabled=false;}
    });
    const b=button(task.title,()=>openTask(task),'progress-task');b.dataset.id=task.id;
    if(task.next_step)b.title=task.next_step;row.append(status,b);return row;
  }

  function render(){
    $('#progress-import').hidden=!candidates.length;
    const active=tasks.filter(t=>['active','blocked'].includes(t.status)).sort((a,b)=>b.updated_at.localeCompare(a.updated_at));
    const resume=$('#progress-resume');resume.hidden=!active.length;resume.replaceChildren(el('h2','','继续上次'));
    active.slice(0,3).forEach(task=>{const b=button('',()=>openTask(task),'progress-resume-item');b.dataset.id=task.id;b.append(el('strong','',task.title),el('span','',task.checkpoint||'还没记录停在哪，点击补充'),el('span','meta',task.next_step?'下一步：'+task.next_step:'下次从这里继续'));resume.append(b);});
    const end=add(start,days-1),timeline=$('#progress-timeline');timeline.replaceChildren();
    $('#progress-range-label').textContent=`${start.slice(5).replace('-','/')} — ${end.slice(5).replace('-','/')}`;
    const grid=el('div','progress-grid');grid.style.setProperty('--days',days);
    const head=el('div','progress-grid-row progress-grid-head');head.append(el('span','progress-name','任务'));
    for(let i=0;i<days;i++){const d=add(start,i);head.append(el('span','progress-day'+(d===today()?' is-today':''),d.slice(8)));}grid.append(head);
    const scheduled=tasks.filter(t=>t.status!=='done'&&t.start);
    scheduled.filter(t=>t.end>=start&&t.start<=end).forEach(task=>{
      const row=el('div','progress-grid-row');row.append(taskButton(task));
      const track=el('div','progress-track');const bar=button('',()=>openTask(task),'progress-bar status-'+task.status);
      bar.style.gridColumn=`${Math.max(0,delta(task.start,start))+1} / ${Math.min(days-1,delta(task.end,start))+2}`;
      bar.textContent=task.next_step||labels[task.status];bar.title=`${task.title} · ${task.start} — ${task.end}`;bar.setAttribute('aria-label',bar.title);track.append(bar);row.append(track);grid.append(row);
    });timeline.append(grid);
    if(grid.children.length===1)timeline.append(el('p','progress-empty','这段时间没有已排期的任务。'));
    const outside=scheduled.filter(t=>t.end<start||t.start>end);
    if(outside.length)timeline.append(button(`${outside.length} 项任务在此范围外 · 查看最近一项`,()=>{start=add(outside.sort((a,b)=>Math.abs(delta(a.start,today()))-Math.abs(delta(b.start,today())))[0].start,-1);render();},'progress-outside'));
    const unscheduled=tasks.filter(t=>t.status!=='done'&&!t.start),bucket=$('#progress-unscheduled > div');
    bucket.replaceChildren(...unscheduled.map(taskButton));if(!unscheduled.length)bucket.append(el('p','meta',tasks.length?'没有未排期任务':'先添加一个任务，不必先想好日期。'));
    const done=tasks.filter(t=>t.status==='done');$('#progress-done').hidden=!done.length;$('#progress-done summary span').textContent=String(done.length);$('#progress-done > div').replaceChildren(...done.map(taskButton));
  }
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
  $('#progress-reload').addEventListener('click',async()=>{
    try{const current=draft();await load();const latest=tasks.find(t=>t.id===editing);if(!latest)throw new Error('任务已不存在，请复制当前内容后重新添加。');
      const conflicts=[];fields.forEach(k=>{if(current[k]===baseline[k])form.elements.namedItem(k).value=latest[k];else if(latest[k]!==baseline[k]&&latest[k]!==current[k])conflicts.push(`${fieldLabels[k]}（服务器）：${latest[k]||'空'}`);});
      baseline=structuredClone(latest);conflict=false;$('#progress-reload').hidden=true;
      error(conflicts.length?'以下字段双方都修改过，请核对后保存。当前填写仍保留。\n'+conflicts.join('\n'):'已合并其他字段的更新，当前填写保留，请检查后保存。');
      form.querySelector('[type=submit]').disabled=false;form.querySelector('[type=submit]').textContent='确认并保存';
    }catch(e){error(e.message);}
  });
  $('#progress-close').addEventListener('click',closeTask);dialog.addEventListener('cancel',event=>{event.preventDefault();closeTask();});
  window.addEventListener('beforeunload',event=>{if(hasChanges()||busy){event.preventDefault();event.returnValue='';}});
  $('#progress-prev').addEventListener('click',()=>{start=add(start,-days);render();});$('#progress-next').addEventListener('click',()=>{start=add(start,days);render();});$('#progress-today').addEventListener('click',()=>{start=add(today(),-3);render();});$('#progress-days').addEventListener('change',event=>{days=Number(event.target.value);render();});
  $('#progress-import').addEventListener('click',()=>{
    const container=$('#progress-candidates');container.replaceChildren();$('#progress-import-error').hidden=true;
    candidates.forEach(c=>{const row=el('label','progress-candidate'),input=el('input');input.type='checkbox';input.value=c.id;row.append(input,el('span','',c.title));container.append(row);});$('#progress-import-dialog').showModal();
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
  load().catch(e=>notice(`科研进度未载入：${e.message}。请刷新重试；不会覆盖原文件。`));
})();
