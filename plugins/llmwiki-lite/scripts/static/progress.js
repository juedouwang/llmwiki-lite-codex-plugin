/* Project-local explicit tasks. Shared editor request/Markdown preview APIs only. */
(()=>{
  'use strict';
  const page=document.getElementById('main-content');if(!page)return;
  const root=page.querySelector('#research-progress'),home=page.querySelector('.resume-block[data-project]');
  const project=(root||home)?.dataset.project;if(!project)return;
  const lifecycle=new AbortController(),active=()=>!lifecycle.signal.aborted&&page.isConnected;
  const owns=e=>e.detail?.root===page;
  const base=`/api/project/${encodeURIComponent(project)}`,endpoint=base+'/progress';
  const $=s=>root?.querySelector(s),form=$('#progress-form'),dialog=$('#progress-dialog');
  const input=name=>form.elements.namedItem(name);
  const fields=['title','description','priority','ddl','record_id'];
  const fieldLabels={title:'任务名称',description:'任务描述',priority:'优先级',ddl:'截止日',record_id:'关联笔记'};
  const priorities={high:'高优先级',medium:'中优先级',low:'低优先级'},rank={high:0,medium:1,low:2};
  const node=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const button=(text,run,cls='')=>{const b=node('button',cls,text);b.type='button';b.addEventListener('click',run);return b;};
  const iso=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const today=()=>iso(new Date()),from=s=>new Date(s+'T12:00:00');
  const add=(s,n)=>{const d=from(s);d.setDate(d.getDate()+n);return iso(d);};
  const delta=(a,b)=>Math.round((from(a)-from(b))/86400000);
  const shortDate=s=>`${Number(s.slice(5,7))}/${Number(s.slice(8))}`;
  const recordUrl=id=>`/project/${encodeURIComponent(project)}/records/`+id.replace(/^records\//,'').split('/').map(encodeURIComponent).join('/');
  const description=t=>t.description??[t.effective_context?.checkpoint??t.checkpoint,t.effective_context?.next_step??t.next_step].filter(Boolean).join('\n\n');
  const ddl=t=>t.ddl??(t.end||t.start||'');
  const pending=t=>t.status!=='done'&&(t.review_state==='pending'||t.status==='pending');
  const review=t=>t.status==='done'?'已完成':pending(t)?'待你验收':'待完成';
  const metadata=t=>Object.fromEntries(['parent_id','scheduled_date','review_state','kind'].filter(key=>Object.hasOwn(t,key)).map(key=>[key,t[key]]));
  let taskMetadata={};
  const sorted=list=>[...list].sort((a,b)=>(rank[a.priority]??1)-(rank[b.priority]??1)||(ddl(a)||'9999-12-31').localeCompare(ddl(b)||'9999-12-31')||a.id.localeCompare(b.id));
  let tasks=[],revision='',records=[],candidates=[],recordsLoaded=false,ready=false,busy=false;
  let editing=null,baseline=null,dialogRevision='',session=0,descriptionTouched=false,conflict=false;
  let view='todo',start=today(),days=7,timer=null,inflight=false,epoch=0,previewTimer=null,previewSequence=0;
  const uploads=new Map();
  function notice(text){const n=$('#progress-message');if(n){n.textContent=text;n.hidden=!text;}}
  function error(text){const n=$('#progress-error');if(n){n.textContent=text;n.hidden=!text;}}
  async function request(url,payload){
    // Use the shared editor's bounded same-origin JSON API, not another editor implementation.
    if(window.ResearchDocument?.request)return window.ResearchDocument.request(url,payload);
    const response=await fetch(url,payload===undefined?{cache:'no-store',signal:lifecycle.signal}:{method:'POST',headers:{'Content-Type':'application/json','X-Notebook-Request':'1'},body:JSON.stringify(payload),signal:lifecycle.signal});
    const result=await response.json();if(!response.ok||result.ok===false){const e=new Error(result.error?.message||result.error||'请求失败');e.status=response.status;throw e;}return result;
  }
  function apply(data){tasks=data.tasks;revision=data.revision;ready=true;if(data.warning)notice(data.warning);render();}
  async function load(){const generation=epoch,data=await request(endpoint+'?view=summary');if(active()&&generation===epoch&&!busy)apply(data);}
  async function persist(payload,baseRevision=revision){
    if(busy||!ready)throw new Error('请等待当前操作完成。');busy=true;epoch++;
    try{const result=await request(endpoint,{...payload,revision:baseRevision});if(active())apply(result);return result;}
    finally{busy=false;}
  }
  async function ensureRecords(){
    if(recordsLoaded)return;
    const data=await request(endpoint+'?view=records');if(!active())return;
    records=data.records||[];candidates=data.candidates||[];recordsLoaded=true;
  }
  function fillRecords(value){
    const select=input('record_id'),list=[{id:'',title:'不关联'},...records];
    if(value&&!list.some(t=>t.id===value))list.push({id:value,title:'来源不可用 · '+value});
    select.replaceChildren(...list.map(t=>{const o=node('option','',t.title);o.value=t.id;return o;}));select.value=value;
    const box=$('#progress-source');box.replaceChildren();
    if(value){if(recordsLoaded&&!records.some(t=>t.id===value))box.append(node('p','meta','来源不可用（保留原关联）'));
      else{const a=node('a','','打开关联笔记');a.href=recordUrl(value);box.append(a);}}
  }
  function values(){return Object.fromEntries(fields.map(k=>[k,input(k).value]));}
  function hasChanges(){return !!(dialog?.open&&baseline&&fields.some(k=>input(k).value!==baseline[k]));}
  function pendingUploads(){return [...uploads.values()].some(u=>u.state==='pending');}
  function unsaved(){return busy||hasChanges()||uploads.size>0||!!($('#progress-import-dialog')?.open&&$('#progress-candidates :checked'));}
  function renderResume(container){
    if(!container)return;
    const list=tasks.filter(t=>t.status!=='done'&&(t.resume_eligible||['active','blocked'].includes(t.status)))
      .sort((a,b)=>(rank[a.priority]??1)-(rank[b.priority]??1)||b.updated_at.localeCompare(a.updated_at)||a.id.localeCompare(b.id)).slice(0,3);container.hidden=!list.length;
    container.replaceChildren(node('h2','','继续上次'));
    const ul=node('ul','resume-list');
    list.forEach(t=>{const li=node('li','progress-resume-item');
      if(root)li.append(button(t.title,()=>openTask(t),'resume-title'));
      else{const a=node('a','resume-title',t.title);a.href=`/project/${encodeURIComponent(project)}/todos#task-${t.id}`;li.append(a);}
      li.append(node('p','progress-description-excerpt',description(t)||'暂无描述'));ul.append(li);
    });container.append(ul);
  }
  function taskRow(task){
    const row=node('div','progress-task-row priority-'+(task.priority||'medium'));row.dataset.id=task.id;
    const done=task.status==='done';
    const toggle=button(done?'✓':'',()=>mutate(task,done?'restore':'complete'),'progress-done-toggle'+(done?' is-done':''));
    toggle.setAttribute('aria-label',(done?'恢复 ':'完成 ')+task.title);toggle.title=done?'恢复到 Todo':pending(task)?'验收并标记完成':'标记为完成';
    const text=node('div','progress-task-text'),title=button(task.title,()=>openTask(task),'progress-task');
    title.dataset.id=task.id;text.append(title);
    if(description(task))text.append(node('p','progress-description-excerpt',description(task)));
    const dailyMeta=[];
    if(task.parent_id)dailyMeta.push('来源：'+(tasks.find(t=>t.id===task.parent_id)?.title||task.parent_title||'原任务不可用'));
    if(task.scheduled_date)dailyMeta.push('每日安排 '+task.scheduled_date);
    if(pending(task))dailyMeta.push('待你验收');
    if(dailyMeta.length)text.append(node('p','progress-daily-meta'+(pending(task)?' is-pending':''),dailyMeta.join(' · ')));
    const date=node('span','progress-ddl'+(!done&&ddl(task)&&ddl(task)<today()?' is-overdue':''),ddl(task)?'DDL '+ddl(task):'未排期');
    const select=node('select','progress-inline-priority');select.setAttribute('aria-label',task.title+'的优先级');
    for(const [value,label] of Object.entries(priorities)){const o=node('option','',label);o.value=value;select.append(o);}select.value=task.priority||'medium';
    select.addEventListener('change',async()=>{select.disabled=true;try{await persist({action:'update',id:task.id,task:{priority:select.value}});}catch(e){notice(e.message);select.value=task.priority||'medium';if(e.status===409)await load().catch(()=>{});}finally{select.disabled=false;}});
    row.append(toggle,text,date,select);return row;
  }
  async function mutate(task,action){
    if(dialog?.open&&(hasChanges()||uploads.size)){error('请先保存当前填写，再完成或恢复任务。');return;}
    try{await persist({action,id:task.id},dialog?.open?dialogRevision:revision);if(dialog?.open)closeTask(true);}
    catch(e){if(dialog?.open){error(e.message);if(e.status===409)setConflict();}else notice(e.message);if(e.status===409&&!dialog?.open)await load().catch(()=>{});}
  }
  function render(){
    renderResume(home);if(!root)return;renderResume($('#progress-resume'));
    const priority=$('#progress-priority-filter').value,filter=t=>!priority||(t.priority||'medium')===priority;
    const todo=sorted(tasks.filter(t=>t.status!=='done'&&filter(t))),done=sorted(tasks.filter(t=>t.status==='done'&&filter(t)));
    for(const [name,list] of [['todo',todo],['done',done]]){
      const section=$('#progress-'+name);section.hidden=view!==name;
      section.querySelector('div').replaceChildren(...(list.length?list.map(taskRow):[node('p','progress-empty',name==='todo'?'没有待办任务；可以新建，日期可留空。':'还没有已完成任务。')]));
      const tab=$('#progress-'+name+'-tab');tab.setAttribute('aria-pressed',String(view===name));tab.querySelector('span').textContent=String(list.length);
    }
    $('#progress-schedule').hidden=view==='done';
    const current=today(),end=add(start,days-1),timeline=$('#progress-timeline');timeline.replaceChildren();
    $('#progress-range-label').textContent=`${start} — ${end}`;
    const grid=node('div','progress-grid');grid.style.setProperty('--days',days);
    const header=node('div','progress-grid-row progress-grid-head');header.append(node('span','progress-name','任务'));
    for(let i=0;i<days;i++){const date=add(start,i);header.append(node('span','progress-day'+(date===current?' is-today':''),['日','一','二','三','四','五','六'][from(date).getDay()]+' '+Number(date.slice(8))));}grid.append(header);
    // Show the remaining span, not a stored start date. Overdue work stays visible today.
    // Daily children are the same task IDs, shown on their explicit day (never moved by viewing).
    const scheduled=todo.filter(t=>t.scheduled_date||ddl(t)).map(task=>task.scheduled_date?
      {task,due:task.scheduled_date,from:task.scheduled_date,to:task.scheduled_date,daily:true}:
      {task,due:ddl(task),from:current,to:ddl(task)<current?current:ddl(task)});
    for(const item of scheduled.filter(t=>t.to>=start&&t.from<=end)){
      const {task,due}=item,overdue=due<current;
      const row=node('div','progress-grid-row'),name=button(task.title,()=>openTask(task),'progress-calendar-title');name.title=task.title;row.append(name);
      const track=node('div','progress-track'),bar=button(item.daily?shortDate(due)+' 每日安排'+(pending(task)?' · 待你验收':''):overdue?'已逾期':due===current?'今天截止':shortDate(due)+' 截止',()=>openTask(task),'progress-bar priority-'+(task.priority||'medium'));
      bar.style.gridColumn=`${Math.max(0,delta(item.from,start))+1} / ${Math.min(days-1,delta(item.to,start))+2}`;
      bar.classList.toggle('is-overdue',overdue);bar.classList.toggle('is-clipped-start',item.from<start);bar.classList.toggle('is-clipped-end',item.to>end);
      bar.dataset.taskId=task.id;
      bar.title=task.title+' · '+(item.daily?'每日安排 '+due+' · '+review(task):overdue?'截止 '+due+'，已逾期 '+delta(current,due)+' 天':current+' — '+due)+' · '+priorities[task.priority||'medium'];
      bar.setAttribute('aria-label',bar.title);track.append(bar);row.append(track);grid.append(row);
    }
    timeline.append(grid);if(grid.children.length===1)timeline.append(node('p','progress-empty','这段时间没有已排期任务。未排期任务仍保留在 Todo。'));
    const outside=scheduled.filter(t=>t.to<start||t.from>end);
    if(outside.length)timeline.append(button(`${outside.length} 项任务在此范围外 · 回到今天`,()=>{start=today();render();},'progress-outside'));
  }
  function dailyInfo(task){
    const box=$('#progress-task-metadata');box.replaceChildren();
    const children=tasks.filter(t=>task.id&&t.parent_id===task.id).sort((a,b)=>(a.scheduled_date||'').localeCompare(b.scheduled_date||'')||a.id.localeCompare(b.id));
    const hasDelivery=Boolean(task.delivery_summary||task.completion_record||task.rejection_record||task.acceptance_record);
    box.hidden=!(task.parent_id||task.scheduled_date||pending(task)||task.review_state==='accepted'||hasDelivery);
    if(!box.hidden){
      box.append(node('p','','任务归属：当前项目 · '+(task.parent_id?'每日子任务':'独立待办')));
      if(task.scheduled_date){const line=node('p','','每日日期：'+task.scheduled_date+' · '),link=node('a','','查看当天待办');link.href='/daily?'+new URLSearchParams({date:task.scheduled_date,context:project});line.append(link);box.append(line);}
      if(task.parent_id){const parent=tasks.find(t=>t.id===task.parent_id),line=node('p','','来源任务：'),link=node('a','',parent?.title||task.parent_title||'查看来源任务');link.href=`/project/${encodeURIComponent(project)}/todos?task=${encodeURIComponent(task.parent_id)}`;line.append(link);box.append(line);}
      box.append(node('p',pending(task)?'is-pending':'','验收状态：'+review(task)));
      if(task.delivery_summary)box.append(node('p','progress-delivery','助手交付：'+task.delivery_summary));
      if(task.rejection_reason)box.append(node('p','progress-delivery','退回意见：'+task.rejection_reason));
      for(const [label,record] of [['交付记录',task.completion_record],['退回记录',task.rejection_record],['验收记录',task.acceptance_record]]){
        if(!record?.id)continue;const line=node('p','progress-delivery',label+'：'),link=node('a','',record.title||record.id);link.href=recordUrl(record.id);line.append(link);box.append(line);
      }
    }
    const detail=$('#progress-daily-arrangements');detail.hidden=!children.length;
    detail.querySelector('div').replaceChildren(...children.map(child=>{
      const row=node('div','progress-daily-child'),open=button(child.title,()=>{if(unsaved()){error('请先保存或取消当前修改。');return;}openTask(child);});
      open.dataset.taskId=child.id;row.append(node('span','',child.scheduled_date||'未安排日期'),open,node('span',pending(child)?'is-pending':'',review(child)));return row;
    }));
  }
  function legacyInfo(task){
    const detail=$('#progress-legacy'),keys=['checkpoint','next_step','start','end','status','context_mode','effective_context','auto_context','assistant_context'];
    const content=Object.fromEntries(keys.filter(k=>task[k]!==undefined).map(k=>[k,task[k]]));
    detail.hidden=!task.id;detail.querySelector('pre').textContent=JSON.stringify(content,null,2);
    const history=$('#progress-history');history.hidden=!task.id;history.open=false;
    history.querySelector('div').replaceChildren(...[...(task.history||[])].reverse().map(h=>{
      const n=node('div','progress-history-entry');n.append(node('p','meta',(h.at||'')+' · '+(h.status==='done'?'Done':'Todo')),node('strong','',h.title),node('p','',description(h)),node('p','meta',(priorities[h.priority]||'中优先级')+' · DDL '+(ddl(h)||'未排期')));return n;
    }));
  }
  function clearUploads(){for(const u of uploads.values())if(u.url)URL.revokeObjectURL(u.url);uploads.clear();renderUploads();}
  function openTask(task=null){
    if(!form||!active())return;if(dialog.open){if(unsaved())return;closeTask(true);}
    session++;clearUploads();editing=task?.id||null;descriptionTouched=false;conflict=false;dialogRevision=revision;error('');
    taskMetadata=metadata(task||{});dailyInfo(task||{});
    baseline={title:task?.title||'',description:task?description(task):'',priority:task?.priority||'medium',ddl:task?ddl(task):'',record_id:task?.record_id||''};
    fields.forEach(k=>{if(k!=='record_id')input(k).value=baseline[k];});fillRecords(baseline.record_id);
    $('#progress-dialog-title').textContent=editing?'编辑任务':'新建任务';$('#progress-delete').hidden=!editing;$('#progress-complete').hidden=!editing;
    $('#progress-complete').textContent=task?.status==='done'?'恢复到 Todo':task&&pending(task)?'验收并完成':'完成任务';$('#progress-reload').hidden=true;
    form.querySelector('[type=submit]').disabled=false;legacyInfo(task||{});dialog.showModal();input('title').focus();updatePreview();
    const token=session;ensureRecords().then(()=>{if(active()&&token===session&&dialog.open)fillRecords(input('record_id').value);}).catch(e=>error(e.message));
  }
  function closeTask(force=false){
    if(!force&&(busy||pendingUploads())){error('请等待保存或截图上传完成。');return;}
    if(!force&&(hasChanges()||uploads.size)&&!confirm('尚未保存，放弃这次修改吗？'))return;
    session++;previewSequence++;clearTimeout(previewTimer);clearUploads();dialog.close();editing=null;baseline=null;
  }
  function setConflict(){conflict=true;$('#progress-reload').hidden=false;form.querySelector('[type=submit]').disabled=true;}
  async function save(event){
    event.preventDefault();if(conflict||busy)return;
    if(uploads.size){error('请等待截图上传完成；失败的截图请重试或移除后保存。');return;}
    const submit=form.querySelector('[type=submit]');submit.disabled=true;error('');
    fields.forEach(k=>{input(k).disabled=true;});
    const shortcuts=[...form.querySelectorAll('[data-ddl]')];shortcuts.forEach(b=>{b.disabled=true;});
    form.setAttribute('aria-busy','true');
    const current=values(),patch=Object.fromEntries(fields.filter(k=>!editing||current[k]!==baseline[k]||(k==='description'&&descriptionTouched)).map(k=>[k,current[k]]));
    try{await persist({action:editing?'update':'create',...(editing?{id:editing}:{}),task:{...taskMetadata,...patch}},dialogRevision);closeTask(true);}
    catch(e){error(e.message);if(e.status===409)setConflict();}
    finally{submit.disabled=conflict;fields.forEach(k=>{input(k).disabled=false;});shortcuts.forEach(b=>{b.disabled=false;});form.removeAttribute('aria-busy');}
  }
  async function reloadDraft(){
    if(busy||pendingUploads())return;
    const token=session,current=values(),old={...baseline};
    try{await load();if(token!==session||!dialog.open)return;
      if(!editing){dialogRevision=revision;conflict=false;$('#progress-reload').hidden=true;form.querySelector('[type=submit]').disabled=false;error('已读取最新列表，新任务填写仍保留。');return;}
      const latest=tasks.find(t=>t.id===editing);if(!latest)throw new Error('任务已被删除；当前填写保留，请复制后新建任务。');
      const next={title:latest.title,description:description(latest),priority:latest.priority||'medium',ddl:ddl(latest),record_id:latest.record_id||''},collisions=[];
      fields.forEach(k=>{if(current[k]===old[k])current[k]=next[k];else if(next[k]!==old[k]&&current[k]!==next[k])collisions.push(`${fieldLabels[k]}（服务器）：${next[k]||'空'}`);});
      baseline=next;fields.forEach(k=>{if(k!=='record_id')input(k).value=current[k];});fillRecords(current.record_id);dialogRevision=revision;conflict=false;$('#progress-reload').hidden=true;form.querySelector('[type=submit]').disabled=false;
      taskMetadata=metadata(latest);dailyInfo(latest);legacyInfo(latest);updatePreview();error(collisions.length?'双方修改了相同字段，当前填写保留，请核对后保存。\n'+collisions.join('\n'):'已合并服务器更新，当前填写保留，请检查后保存。');
    }catch(e){error(e.message);}
  }
  async function updatePreview(){
    if(!dialog?.open)return;
    const sequence=++previewSequence,token=session,text=input('description').value,box=$('#progress-preview');
    if(!text){box.replaceChildren(node('p','meta','暂无描述'));return;}
    try{const data=await request(base+'/notebook/preview',{text});if(!active()||token!==session||sequence!==previewSequence)return;
      // The renderer escapes Markdown HTML; additionally allow images only from this project.
      const template=document.createElement('template');template.innerHTML=data.html;
      const prefix=`/project/${encodeURIComponent(project)}/asset/`;
      template.content.querySelectorAll('img').forEach(img=>{
        const url=new URL(img.getAttribute('src'),location.origin);
        if(url.origin!==location.origin||!url.pathname.startsWith(prefix)){img.replaceWith(node('span','meta','[图片不可用：仅显示当前项目图片]'));return;}
        img.loading='lazy';img.referrerPolicy='no-referrer';
      });box.replaceChildren(template.content);
    }catch(e){if(token===session&&sequence===previewSequence){box.replaceChildren(node('p','meta','预览暂不可用，描述仍保留。'),node('pre','',text));}}
  }
  function changedDescription(){descriptionTouched=true;previewSequence++;clearTimeout(previewTimer);previewTimer=setTimeout(updatePreview,200);}
  function renderUploads(){
    const box=$('#progress-uploads');if(!box)return;box.replaceChildren();
    for(const [key,u] of uploads){const row=node('div','progress-upload'),img=node('img');img.src=u.url;img.alt='待上传截图';row.append(img,node('span','',u.state==='pending'?'截图上传中…':u.error));
      if(u.state==='failed')row.append(button('重试',()=>upload(key)),button('移除此截图',()=>{URL.revokeObjectURL(u.url);uploads.delete(key);renderUploads();}));box.append(row);}
  }
  async function upload(key){
    const u=uploads.get(key);if(!u||u.state==='pending')return;
    u.state='pending';renderUploads();const token=session;
    try{
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('截图读取失败。'));reader.readAsDataURL(u.file);});
      const result=await request(base+'/notebook/upload',{data});if(!active()||token!==session||!uploads.has(key))return;
      if(!/^[a-f0-9]{64}\.(png|jpg|gif|webp)$/.test(result.image))throw new Error('图片接口返回了无效文件名。');
      const text=input('description'),markdown=`\n![](../assets/${result.image})\n`;
      if(text.value.length+markdown.length>text.maxLength)throw new Error('描述已达长度上限，请缩短后重试。');
      // Do not overwrite text entered while the upload was in flight.
      const position=Math.min(u.position,text.value.length),at=text.value===u.original?position:text.value.length;
      text.setRangeText(markdown,at,at,'end');URL.revokeObjectURL(u.url);uploads.delete(key);renderUploads();changedDescription();updatePreview();
    }catch(e){if(token===session&&uploads.has(key)){u.state='failed';u.error='截图上传失败：'+e.message;renderUploads();}}
  }
  function paste(event){
    const files=[...(event.clipboardData?.items||[])].filter(i=>i.kind==='file'&&i.type.startsWith('image/')).map(i=>i.getAsFile()).filter(Boolean);
    if(!files.length)return;event.preventDefault();if(busy){error('请等待保存完成再粘贴截图。');return;}
    for(const file of files){
      if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)||file.size>10*1024*1024){error('截图须为 PNG、JPEG、GIF 或 WebP，且不超过 10 MB。');continue;}
      const key=crypto.randomUUID();uploads.set(key,{file,url:URL.createObjectURL(file),state:'queued',position:input('description').selectionStart,original:input('description').value});upload(key);
    }
  }
  function stopTimer(){if(timer){clearInterval(timer);timer=null;}}
  async function refresh(){if(inflight||busy||document.hidden||!active())return;inflight=true;try{await load();}catch(e){if(active())notice('科研进度刷新失败：'+e.message);}finally{inflight=false;}}
  function startTimer(){if(!timer&&active()&&!document.hidden)timer=setInterval(refresh,5000);}
  function hashTask(){if(!root||!active()||dialog.open)return;const id=new URLSearchParams(location.search).get('task')||location.hash.match(/^#task-([a-f0-9]{32})$/)?.[1];if(!/^[a-f0-9]{32}$/.test(id||''))return;const task=tasks.find(t=>t.id===id);if(task)openTask(task);}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopTimer();else if(active()){refresh();startTimer();}},{signal:lifecycle.signal});
  document.addEventListener('workbench:before-leave',event=>{if(owns(event)&&active()&&unsaved()){event.preventDefault();if(dialog?.open)error('尚未保存，请先保存或取消当前修改。');else notice('请等待当前操作完成。');}},{signal:lifecycle.signal});
  document.addEventListener('workbench:leave',event=>{if(owns(event)){stopTimer();if(dialog?.open)closeTask(true);$('#progress-import-dialog')?.close();}},{signal:lifecycle.signal});
  document.addEventListener('workbench:enter',event=>{if(owns(event)&&active()){refresh().then(hashTask);startTimer();}},{signal:lifecycle.signal});
  document.addEventListener('workbench:dispose',event=>{if(owns(event)){stopTimer();clearTimeout(previewTimer);previewSequence++;session++;clearUploads();lifecycle.abort();}},{signal:lifecycle.signal});
  window.addEventListener('beforeunload',event=>{if(active()&&unsaved()){event.preventDefault();event.returnValue='';}},{signal:lifecycle.signal});
  window.addEventListener('hashchange',hashTask,{signal:lifecycle.signal});
  if(root){
    $('#progress-new').addEventListener('click',()=>openTask());form.addEventListener('submit',save);
    $('#progress-close').addEventListener('click',()=>closeTask());dialog.addEventListener('cancel',event=>{event.preventDefault();closeTask();});
    input('description').addEventListener('input',changedDescription);input('description').addEventListener('paste',paste);
    input('record_id').addEventListener('change',()=>fillRecords(input('record_id').value));
    root.querySelectorAll('[data-ddl]').forEach(b=>b.addEventListener('click',()=>{input('ddl').value=b.dataset.ddl===''?'':add(today(),Number(b.dataset.ddl));}));
    $('#progress-reload').addEventListener('click',reloadDraft);
    $('#progress-complete').addEventListener('click',()=>{const task=tasks.find(t=>t.id===editing);if(task)mutate(task,task.status==='done'?'restore':'complete');});
    $('#progress-delete').addEventListener('click',async()=>{
      if(busy||pendingUploads()){error('请等待当前操作完成。');return;}
      if(!editing||!confirm('确定删除此任务？仅删除任务，不删除关联笔记或图片。当前未保存的填写将被放弃。'))return;
      try{await persist({action:'delete',id:editing},dialogRevision);closeTask(true);}catch(e){error(e.message);if(e.status===409)setConflict();}
    });
    for(const name of ['todo','done'])$('#progress-'+name+'-tab').addEventListener('click',()=>{view=name;render();});
    $('#progress-priority-filter').addEventListener('change',render);
    $('#progress-prev').addEventListener('click',()=>{start=add(start,-days);render();});$('#progress-next').addEventListener('click',()=>{start=add(start,days);render();});$('#progress-today').addEventListener('click',()=>{start=today();render();});$('#progress-days').addEventListener('change',e=>{days=Number(e.target.value);render();});
    $('#progress-import').addEventListener('click',async()=>{try{await ensureRecords();if(!active())return;
      const box=$('#progress-candidates');box.replaceChildren();
      candidates.filter(c=>!tasks.some(t=>t.id===c.id)).forEach(c=>{const label=node('label','progress-candidate'),check=node('input');check.type='checkbox';check.value=c.id;label.append(check,node('span','',c.title));box.append(label);});
      if(!box.children.length)box.append(node('p','meta','没有可导入的旧待办。'));$('#progress-import-error').hidden=true;$('#progress-import-completed').checked=false;$('#progress-import-dialog').showModal();
    }catch(e){notice(e.message);}});
    $('#progress-import-close').addEventListener('click',()=>$('#progress-import-dialog').close());
    $('#progress-import-form').addEventListener('submit',async event=>{event.preventDefault();const ids=[...$('#progress-candidates').querySelectorAll(':checked')].map(n=>n.value);if(!ids.length)return;
      const completed=$('#progress-import-completed').checked?candidates.filter(c=>{try{return localStorage.getItem('llmwiki-todo-'+c.legacy_key)==='1';}catch{return false;}}).map(c=>c.id):[];
      const submit=event.target.querySelector('[type=submit],.primary');submit.disabled=true;
      try{await persist({action:'import',ids,completed});$('#progress-import-dialog').close();}
      catch(e){const box=$('#progress-import-error');box.textContent=e.message;box.hidden=false;if(e.status===409)await load().catch(()=>{});}finally{submit.disabled=false;}
    });
  }
  load().then(()=>{hashTask();startTimer();}).catch(e=>notice('科研进度未载入：'+e.message+'。不会覆盖原文件。'));
})();
