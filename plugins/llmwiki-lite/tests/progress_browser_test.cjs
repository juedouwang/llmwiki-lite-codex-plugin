/* Acceptance assertions for the research-progress change: A-04, A-05, A-06, A-11, A-12, A-13.
 *
 * Runs against an isolated temporary project seeded by run_notebook_browser.py.
 * Every fixture is disposable; no user data is read or written.
 *
 * Note on scope: this file proves the *receiving* interface for automatic context.
 * A context delivered through the MCP tool is a simulated producer, not evidence
 * that an unattended generator exists.
 */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');

(async()=>{
  const cfg=JSON.parse(process.argv[2]);
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    const page=await context.newPage();
    const errors=[],dialogs=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    page.on('dialog',d=>{dialogs.push(d.message());d.accept();});
    page.on('request',r=>requests.push(r.url()));
    const origin=cfg.origin,base=`/project/${cfg.projectId}`,other=`/project/${cfg.otherProjectId}`;
    const api=(pid)=>`${origin}/api/project/${pid}/progress?view=summary`;
    const summary=async(pid)=>{const response=await context.request.get(api(pid));assert.equal(response.status(),200);return response.json();};
    const rank={high:0,medium:1,low:2};
    // Old active/blocked tasks and explicitly edited new-model tasks, never Done.
    const resumeOrder=tasks=>tasks.filter(t=>t.status!=='done'&&(t.resume_eligible||['active','blocked'].includes(t.status)))
      .sort((a,b)=>(rank[a.priority]??1)-(rank[b.priority]??1)||b.updated_at.localeCompare(a.updated_at)||a.id.localeCompare(b.id));
    const row=title=>page.locator('#progress-todo .progress-task-row').filter({has:page.getByRole('button',{name:title,exact:true})});
    const doneRow=title=>page.locator('#progress-done .progress-task-row').filter({has:page.getByRole('button',{name:title,exact:true})});
    const taskButtons=title=>page.locator('.progress-task').filter({hasText:title});
    const description=page.locator('#progress-description');
    const savedTask=async title=>(await summary(cfg.projectId)).tasks.find(t=>t.title===title);
    const resumeTitles=()=>page.locator('#progress-resume .resume-title').allTextContents();
    async function openDone(){await page.locator('#progress-done-tab').click();await page.locator('#progress-done').waitFor();}
    async function expand(selector){if(!(await page.locator(selector).evaluate(el=>el.open)))await page.locator(`${selector} summary`).click();}
    async function todos(){await page.goto(origin+base+'/todos');await page.locator('.progress-day').first().waitFor();}
    async function save(){await page.locator('#progress-form [type=submit]').click();await page.locator('#progress-dialog').waitFor({state:'hidden'});}
    async function close(){await page.locator('#progress-close').click();await page.locator('#progress-dialog').waitFor({state:'hidden'});}
    async function open(title){await row(title).locator('.progress-task').click();await page.locator('#progress-dialog[open]').waitFor();}
    async function assertHistory(task){
      await expand('#progress-history');
      const entries=page.locator('#progress-history .progress-history-entry');
      const history=[...task.history].reverse();assert.equal(await entries.count(),history.length);
      for(let i=0;i<history.length;i++){
        assert.equal(await entries.nth(i).locator('p.meta').first().innerText(),history[i].at+' · '+(history[i].status==='done'?'Done':'Todo'));
        assert.equal(await entries.nth(i).locator('strong').innerText(),history[i].title);
      }
    }
    // Both protected projects must remain byte-for-byte untouched, including contexts.
    const snapshotFiles=root=>Object.fromEntries(['tasks.json','contexts.json'].map(name=>{const file=path.join(root,'.research-progress',name);return[name,fs.existsSync(file)?fs.readFileSync(file):null];}));
    const otherBefore=snapshotFiles(cfg.otherWikiRoot),legacyBefore=snapshotFiles(cfg.legacyWikiRoot);
    // Every automatic-context submission — legal, duplicate or illegal — goes through
    // the harness, which answers it with a real MCP tools/call over stdio.
    let callSeq=0;
    async function mcpCall(tool,args){
      const id=String(++callSeq).padStart(3,'0');
      const requestPath=path.join(cfg.injectDir,`${id}.request.json`);
      const resultPath=path.join(cfg.injectDir,`${id}.result.json`);
      fs.writeFileSync(requestPath,JSON.stringify({tool,arguments:args}),'utf8');
      const deadline=Date.now()+90000;
      while(!fs.existsSync(resultPath)){
        if(Date.now()>deadline)throw new Error(`等待 ${tool} 结果超时`);
        await page.waitForTimeout(100);
      }
      const rpc=JSON.parse(fs.readFileSync(resultPath,'utf8'));
      assert.equal(rpc.id,Number(id),`MCP 响应与请求不匹配（${id}）`);
      const content=rpc&&rpc.result&&rpc.result.content;
      const text=content&&content[0]&&content[0].text;
      return {rpc,isError:!!(rpc&&rpc.result&&rpc.result.isError),payload:text?JSON.parse(text):null};
    }
    const autoArgs=(task,extra)=>Object.assign({
      project_root:cfg.projectRoot,state_root:cfg.stateRoot,task_id:task.id,
      checkpoint:'自动整理：夜间样本已补齐',next_step:'自动整理：跑消融实验',
      source_record_id:cfg.recordRelPath,base_revision:task.context_revision,
    },extra||{});

    /* ---------------------------------------------------------------- A-06 ---
     * Project browser only selects context; continuation belongs in progress. */
    const seeded=await summary(cfg.projectId);
    const expected=resumeOrder(seeded.tasks);
    assert.ok(expected.length>=4,`夹具需要至少四个进行中任务，实际 ${expected.length}`);
    const initialDone=seeded.tasks.filter(t=>t.status==='done').length;

    await page.goto(origin+'/projects');
    const rowA=page.locator(`.project-row[data-project-id="${cfg.projectId}"]`);
    assert.equal(await rowA.count(),1,'项目列表缺少 A 的行');
    assert.equal(await page.locator('.project-row-task').count(),0,'项目浏览页不显示有时有、有时没有的任务副标题');
    await rowA.locator('.project-row-main').click();
    await page.waitForURL(origin+'/projects?context='+cfg.projectId);
    assert.equal(await page.locator('body').getAttribute('data-workbench-page'),'home');
    assert.equal(await page.locator('body').getAttribute('data-workbench-project'),cfg.projectId);
    const rowB=page.locator(`.project-row[data-project-id="${other.split('/').pop()}"]`);
    assert.equal(await rowB.count(),1,'项目列表缺少 B 的行');

    await todos();
    await page.locator('#progress-resume .progress-resume-item').first().waitFor();
    assert.equal(await page.locator('#progress-resume .progress-resume-item').count(),3,'科研进度继续上次最多三个任务');
    assert.deepEqual(
      (await resumeTitles()).map(t=>t.trim()),
      expected.slice(0,3).map(t=>t.title),
      '继续上次应按优先级、最近人工修改时间排序'
    );
    // Keep server-rendered continuation before the summary request, now on the
    // progress landing page. Knowledge stays dedicated to project understanding.
    const homeHtml=await (await context.request.get(origin+base+'/todos')).text();
    const ssr=await page.evaluate(html=>{
      const root=new DOMParser().parseFromString(html,'text/html').querySelector('#progress-resume');
      return [...root.querySelectorAll('li')].map(item=>({title:item.querySelector('a').textContent,descriptions:[...item.querySelectorAll('p')].map(p=>p.textContent)}));
    },homeHtml);
    assert.deepEqual(ssr,expected.slice(0,3).map(t=>({title:t.title,descriptions:[t.description]})),'首屏 SSR 已有单一统一描述，不依赖 JS 替换旧双栏');
    assert.equal(await page.locator('#progress-form [name=checkpoint], #progress-form [name=next_step], #progress-form [name=status], #progress-form [name=start], #progress-form [name=end]').count(),0);
    assert.equal(await page.locator('#progress-description').count(),1);
    assert.equal(await page.locator('#progress-form input[type=date]').count(),1);
    assert.equal(await page.locator('#progress-form [name=ddl]').evaluate(el=>el.required),false);
    await page.goto(origin+base);
    assert.equal(await page.locator('#progress-resume,.resume-block').count(),0,'知识库不重复显示任务进度');
    await page.goto(origin+other+'/todos');
    await page.locator('#progress-resume').waitFor({state:'attached'});
    assert.equal(await page.locator('#progress-resume .progress-resume-item').count(),0,'B 没有进行中任务时不应渲染继续上次');
    assert.equal(await page.locator('#progress-resume').evaluate(el=>el.hidden),true,'B 的继续上次必须隐藏');
    const otherHtml=await (await context.request.get(origin+other+'/todos')).text();
    assert.match(otherHtml,/<section id="progress-resume"[^>]*hidden/,'B 的服务端输出就应是隐藏的空块');
    assert.ok(!otherHtml.includes('class="progress-resume-item"'),'B 的服务端输出不应含继续上次条目');
    // textContent, not innerText: hidden nodes and collapsed details are still a leak.
    const bodyB=await page.locator('main').evaluate(el=>el.textContent);
    assert.ok(!bodyB.includes('已跑完基线'),'B 不得借用 A 的上下文');
    assert.ok(!bodyB.includes(expected[0].title),'B 不得显示 A 的任务标题');

    /* ---------------------------------------------------------------- A-04 ---
     * Explicit complete / restore / complete: UI history and persisted completion
     * timestamps agree. A deadline or priority edit must never imply completion. */
    const cycle='夜间数据验证';
    await todos();
    const cycleBefore=await savedTask(cycle);
    await open(cycle);
    assert.equal(await description.inputValue(),cycleBefore.checkpoint+'\n\n下一步：'+cycleBefore.next_step);
    assert.equal(await page.locator('#progress-form [name=ddl]').inputValue(),cycleBefore.end,'旧 end 只读映射 DDL');
    await page.locator('#progress-form [name=priority]').selectOption('high');
    await page.locator('#progress-form [data-ddl="0"]').click();
    const cycleDDL=await page.locator('#progress-form [name=ddl]').inputValue();assert.match(cycleDDL,/^\d{4}-\d{2}-\d{2}$/);
    await save();
    const beforeCycle=await savedTask(cycle);
    assert.equal(beforeCycle.status,'active');assert.equal(beforeCycle.completed_at,null);
    for(const key of ['checkpoint','next_step','start','end','context_mode'])assert.deepEqual(beforeCycle[key],cycleBefore[key],key+' must survive priority/DDL edits');
    assert.equal(beforeCycle.ddl,cycleDDL);assert.equal(beforeCycle.priority,'high');assert.equal(beforeCycle.description_source,'legacy');
    assert.equal(await page.locator('#progress-todo .progress-task').first().innerText(),cycle,'高优先级在前');
    await page.locator('#progress-priority-filter').selectOption('high');assert.equal(await page.locator('#progress-todo .progress-task-row').count(),1);
    await page.locator('#progress-priority-filter').selectOption('');
    assert.equal(await page.locator('#progress-timeline .progress-marker[title="'+cycle+' · DDL '+cycleDDL+'"]').count(),1);
    const firstBegin=Date.now();
    await row(cycle).locator('.progress-done-toggle').click();await row(cycle).waitFor({state:'detached'});
    await openDone();await doneRow(cycle).waitFor();
    assert.equal(await page.locator('#progress-done-tab span').innerText(),String(initialDone+1));
    assert.equal(await taskButtons(cycle).count(),1);
    const first=await savedTask(cycle),firstTime=first.completed_at;
    assert.equal(first.status,'done');assert.ok(Date.parse(firstTime)>=firstBegin-1000&&Date.parse(firstTime)<=Date.now());
    assert.deepEqual(await resumeTitles(),resumeOrder((await summary(cfg.projectId)).tasks).slice(0,3).map(t=>t.title));
    assert.ok(!(await page.locator('#progress-resume').innerText()).includes(cycle));
    assert.equal(await row(cycle).count(),0);assert.equal(await page.locator('#progress-timeline .progress-marker[title^="'+cycle+' ·"]').count(),0);
    await doneRow(cycle).locator('.progress-task').click();await assertHistory(first);
    assert.equal(await page.locator('#progress-complete').innerText(),'恢复到 Todo');await close();
    // The backend stamps seconds: separate the two deliberate completion actions.
    await page.waitForTimeout(1100);
    await doneRow(cycle).locator('.progress-done-toggle').click();await doneRow(cycle).waitFor({state:'detached'});
    await page.locator('#progress-todo-tab').click();await row(cycle).waitFor();
    const restored=await savedTask(cycle);assert.equal(restored.status,cycleBefore.status);assert.equal(restored.completed_at,null);
    assert.equal(await page.locator('#progress-done-tab span').innerText(),String(initialDone));
    assert.equal(await taskButtons(cycle).count(),1);assert.ok((await resumeTitles()).includes(cycle));
    await open(cycle);await assertHistory(restored);assert.equal(await page.locator('#progress-complete').innerText(),'完成任务');await close();
    const secondBegin=Date.now();await row(cycle).locator('.progress-done-toggle').click();await row(cycle).waitFor({state:'detached'});
    await openDone();await doneRow(cycle).locator('.progress-task').click();
    const second=await savedTask(cycle);assert.equal(second.status,'done');assert.notEqual(second.completed_at,firstTime);
    assert.ok(Date.parse(second.completed_at)>=secondBegin-1000&&Date.parse(second.completed_at)<=Date.now());
    const transitions=second.history.slice(beforeCycle.history.length);
    assert.deepEqual(transitions.map(h=>h.status),['done','active','done']);
    assert.deepEqual(transitions.map(h=>h.completed_at),[firstTime,null,second.completed_at]);
    assert.deepEqual(second.history.slice(0,beforeCycle.history.length),beforeCycle.history,'早期历史不能被重写');
    for(const key of ['checkpoint','next_step','start','end','context_mode'])assert.deepEqual(second[key],cycleBefore[key]);
    await assertHistory(second);await close();assert.equal(await taskButtons(cycle).count(),1);
    assert.equal(await page.locator('#progress-done-tab span').innerText(),String(initialDone+1));

    /* ---------------------------------------------------------------- A-05 ---
     * Old Done has no completed_at. Editing a title preserves legacy text/history
     * and never invents a completion date. Done uses this iteration's priority/DDL order. */
    const legacyTitle='旧完成事项（无完成时间）';
    const legacyTask=await savedTask(legacyTitle);
    assert.equal(legacyTask.completed_at,null);assert.ok(legacyTask.history.every(h=>h.completed_at==null));
    const doneNow=(await summary(cfg.projectId)).tasks.filter(t=>t.status==='done');
    const expectedDone=[...doneNow].sort((a,b)=>(rank[a.priority]??1)-(rank[b.priority]??1)||(a.ddl||'9999-12-31').localeCompare(b.ddl||'9999-12-31')||a.id.localeCompare(b.id)).map(t=>t.title);
    assert.deepEqual(await page.locator('#progress-done .progress-task').allTextContents(),expectedDone);
    await doneRow(legacyTitle).locator('.progress-task').click();
    assert.equal(await description.inputValue(),'旧文件里的手写内容');await assertHistory(legacyTask);
    await page.locator('#progress-form [name=title]').fill(legacyTitle+'（改名）');await save();
    const renamed=await savedTask(legacyTitle+'（改名）');
    assert.equal(renamed.id,legacyTask.id);assert.equal(renamed.completed_at,null);assert.equal(renamed.status,'done');
    assert.ok(renamed.history.every(h=>h.completed_at==null),'人工改名不能伪造任何完成时间');
    assert.deepEqual(renamed.history.slice(0,legacyTask.history.length),legacyTask.history);
    for(const key of ['checkpoint','next_step','start','end','context_mode','description'])assert.deepEqual(renamed[key],legacyTask[key]);
    await doneRow(renamed.title).locator('.progress-task').click();await assertHistory(renamed);await close();
    // Exercise, rather than merely snapshot, the separate read-only legacy project.
    await page.goto(origin+`/project/${cfg.legacyProjectId}/todos`);await page.locator('.progress-day').first().waitFor();await openDone();
    await page.locator('#progress-done .progress-task').click();
    const readOnly=(await summary(cfg.legacyProjectId)).tasks[0];assert.equal(readOnly.completed_at,null);await assertHistory(readOnly);await close();
    assert.deepEqual(snapshotFiles(cfg.legacyWikiRoot),legacyBefore,'只读打开列表、详情、历史不能迁移写回旧文件');

    /* ---------------------------------------------------------------- A-09 ---
     * Illegal automatic submissions are refused outright, and a refusal changes
     * nothing: not the task, not the stored result, not the page. */
    const boundary='提交边界与版本';
    await todos();
    const boundaryTask=(await summary(cfg.projectId)).tasks.find(t=>t.title===boundary);
    assert.ok(boundaryTask,'夹具缺少 A-09 用的任务');
    assert.equal(boundaryTask.context_revision,'','基准任务不应已有自动版本');
    assert.equal(boundaryTask.auto_context,null,'基准任务不应已有自动版本');
    const contextFile=path.join(cfg.wikiRoot,'.research-progress','contexts.json');
    const taskFile=path.join(cfg.wikiRoot,'.research-progress','tasks.json');
    const snapshots=()=>({contexts:fs.existsSync(contextFile)?fs.readFileSync(contextFile):null,tasks:fs.readFileSync(taskFile)});
    const frozen=snapshots();
    async function assertUnchanged(label){
      const now=snapshots();
      assert.deepEqual(now.tasks,frozen.tasks,`${label}：tasks.json 不应被改写`);
      assert.deepEqual(now.contexts,frozen.contexts,`${label}：contexts.json 不应被改写`);
      const task=(await summary(cfg.projectId)).tasks.find(t=>t.title===boundary);
      assert.equal(task.context_revision,'',`${label}：不应产生自动版本`);
      assert.equal(task.status,'active',`${label}：不应改动任务状态`);
      assert.equal(task.title,boundary,`${label}：不应改动任务标题`);
    }
    async function rejected(label,args,expected){
      const result=await mcpCall('llmwiki_progress_context_write',args);
      assert.equal(result.rpc.error,undefined,`${label}：越权拒绝不应是 JSON-RPC 协议错误`);
      assert.equal(result.isError,true,`${label}：应标记为错误`);
      assert.equal(result.payload.ok,false,`${label}：应明确拒绝`);
      assert.ok(result.payload.error,`${label}：应给出可读原因`);
      if(expected)assert.equal(result.payload.error,expected,`${label}：原因应可解释`);
      await assertUnchanged(label);
      return result.payload.error;
    }
    await rejected('携带改状态字段',autoArgs(boundaryTask,{status:'done'}),'Unknown argument(s): status');
    await rejected('携带多个未允许字段',autoArgs(boundaryTask,{status:'done',title:'x'}),'Unknown argument(s): status, title');
    // A missing argument must still be a refusal, not a silent default. The exact
    // text is deliberately not pinned here: the tool currently reports an internal
    // TypeError for this case instead of a validation message (reported separately).
    for(const key of ['task_id','checkpoint','next_step','source_record_id','base_revision','project_root']){
      const args=autoArgs(boundaryTask,{});
      delete args[key];
      const result=await mcpCall('llmwiki_progress_context_write',args);
      assert.equal(result.isError,true,`缺少 ${key} 应被拒绝`);
      assert.equal(result.payload.ok,false,`缺少 ${key} 应明确拒绝`);
      await assertUnchanged(`缺少 ${key}`);
    }
    for(const value of ['abc','A'.repeat(32),'a'.repeat(31),'a'.repeat(33),12345,null,'']){
      await rejected(`非法任务 ID ${String(value).slice(0,10)}`,autoArgs(boundaryTask,{task_id:value}),'任务 ID 无效。');
    }
    // A blank project must not fall back to the currently selected project: that is
    // how one project's result would land in an unrelated project.
    await rejected('项目为空字符串',autoArgs(boundaryTask,{project_root:''}),'请明确指定项目，不能用当前选中项目代替。');
    await rejected('项目为 null',autoArgs(boundaryTask,{project_root:null}),'请明确指定项目，不能用当前选中项目代替。');
    const foreignTasks=(await summary(cfg.otherProjectId)).tasks;
    assert.ok(foreignTasks[0],'项目 B 需要至少一个任务');
    await rejected('不存在的任务',autoArgs(boundaryTask,{task_id:'0'.repeat(32)}),'未找到任务。');
    await rejected('别的项目的任务',autoArgs(boundaryTask,{task_id:foreignTasks[0].id}),'未找到任务。');
    await rejected('来源记录不存在',autoArgs(boundaryTask,{source_record_id:`records/manual/${'0'.repeat(32)}.md`}));
    await rejected('来源为空',autoArgs(boundaryTask,{source_record_id:''}),'请提供已保存的科研记录来源。');
    assert.deepEqual((await summary(cfg.otherProjectId)).tasks,foreignTasks,'项目 B 不应被改动');

    /* ---------------------------------------------------------------- A-10 ---
     * The same result twice writes nothing; an older result cannot overwrite a
     * newer one, and the newer one stays readable. */
    const writeV1=await mcpCall('llmwiki_progress_context_write',autoArgs(boundaryTask,{checkpoint:'自动停点V1',next_step:'自动下一步V1'}));
    assert.equal(writeV1.payload.ok,true,`合法提交应成功：${JSON.stringify(writeV1.payload)}`);
    assert.equal(writeV1.payload.changed,true,'首次提交应写入新版本');
    assert.ok(writeV1.payload.context_revision,'写入响应应返回新版本号');
    assert.ok(writeV1.payload.generated_at,'写入响应应返回服务端生成时间');
    const afterV1=snapshots();
    const withV1=Object.assign({},boundaryTask,{context_revision:writeV1.payload.context_revision});
    const repeatV1=await mcpCall('llmwiki_progress_context_write',autoArgs(withV1,{checkpoint:'自动停点V1',next_step:'自动下一步V1'}));
    assert.equal(repeatV1.payload.ok,true,'重复提交应成功返回而不是报错');
    assert.equal(repeatV1.payload.changed,false,'重复提交不应写入');
    assert.equal(repeatV1.payload.context_revision,writeV1.payload.context_revision,'重复提交不应改变版本');
    assert.equal(repeatV1.payload.generated_at,writeV1.payload.generated_at,'重复提交不应刷新生成时间');
    assert.deepEqual(snapshots().contexts,afterV1.contexts,'重复提交不应改写文件');
    const staleV2=await mcpCall('llmwiki_progress_context_write',autoArgs(boundaryTask,{checkpoint:'旧结果',next_step:'旧结果'}));
    assert.equal(staleV2.isError,true,'旧版本提交应被拒绝');
    assert.equal(staleV2.payload.ok,false);
    assert.equal(staleV2.payload.error,'自动整理存在较新版本，已保留当前结果。','应报告版本冲突而非静默覆盖');
    assert.deepEqual(snapshots().contexts,afterV1.contexts,'旧版本提交不得覆盖较新结果');
    const writeV2=await mcpCall('llmwiki_progress_context_write',autoArgs(withV1,{checkpoint:'自动停点V2',next_step:'自动下一步V2'}));
    assert.equal(writeV2.payload.ok,true,`携带最新版本应可写入：${JSON.stringify(writeV2.payload)}`);
    assert.equal(writeV2.payload.changed,true,'新内容应写入');
    assert.notEqual(writeV2.payload.context_revision,writeV1.payload.context_revision,'不同内容应产生不同版本');
    const boundaryAfter=(await summary(cfg.projectId)).tasks.find(t=>t.title===boundary);
    assert.equal(boundaryAfter.context_revision,writeV2.payload.context_revision,'读到的应是较新版本');
    assert.equal(boundaryAfter.effective_context.checkpoint,'自动停点V2','较新结果必须保留');
    assert.equal(boundaryAfter.effective_context.next_step,'自动下一步V2','较新结果必须保留');

    /* ---------------------------------------------------------------- A-08 ---
     * A manual unified description (including explicit empty text) always wins over
     * MCP context. The old per-field ownership and automatic result remain intact. */
    const owner='人工覆盖与自动版本';await todos();
    const ownerTask=await savedTask(owner);assert.equal(ownerTask.context_mode.next_step,'auto');
    const autoV1=await mcpCall('llmwiki_progress_context_write',autoArgs(ownerTask,{checkpoint:'自动停点V1',next_step:'自动下一步V1'}));
    assert.equal(autoV1.payload.ok,true);await todos();await open(owner);
    assert.equal(await description.inputValue(),'自动停点V1\n\n下一步：自动下一步V1');
    await description.focus();await description.press('Control+a');await description.press('Delete');await save();
    const cleared=await savedTask(owner);assert.equal(cleared.description,'');assert.equal(cleared.description_source,'manual');
    assert.deepEqual(cleared.context_mode,ownerTask.context_mode,'统一描述不能重写旧字段所有权');
    const autoV2=await mcpCall('llmwiki_progress_context_write',autoArgs(cleared,{checkpoint:'自动停点V2',next_step:'新的自动下一步'}));
    assert.equal(autoV2.payload.ok,true);await todos();await open(owner);
    assert.equal(await description.inputValue(),'','新自动内容不能回填手动清空的描述');
    await expand('#progress-legacy');
    const ownerLegacy=JSON.parse(await page.locator('#progress-legacy pre').innerText());
    assert.equal(ownerLegacy.auto_context.next_step,'新的自动下一步');
    assert.equal(ownerLegacy.auto_context.generated_at,autoV2.payload.generated_at);
    assert.equal(ownerLegacy.auto_context.source_record_id,cfg.recordRelPath);
    assert.equal(await page.locator('#progress-legacy input, #progress-legacy textarea, #progress-legacy [contenteditable=true]').count(),0);
    const manualDescription='手写研究安排：保留自己的判断，不接受自动覆盖。';
    await description.fill(manualDescription);await save();
    const manual=await savedTask(owner);
    const autoV3=await mcpCall('llmwiki_progress_context_write',autoArgs(manual,{checkpoint:'自动停点V3',next_step:'自动下一步V3'}));
    assert.equal(autoV3.payload.ok,true);await todos();await open(owner);assert.equal(await description.inputValue(),manualDescription);await close();
    const ownerAfter=await savedTask(owner);assert.equal(ownerAfter.description,manualDescription);
    assert.equal(ownerAfter.auto_context.checkpoint,'自动停点V3');assert.equal(ownerAfter.context_revision,autoV3.payload.context_revision);
    for(const key of ['checkpoint','next_step','start','end','status','context_mode'])assert.deepEqual(ownerAfter[key],ownerTask[key]);
    // A legacy handwritten checkpoint/next_step is equally protected from MCP writes.
    const handwritten=await savedTask('消融实验整理');
    const injected=await mcpCall('llmwiki_progress_context_write',autoArgs(handwritten,{checkpoint:'不能覆盖旧停点',next_step:'不能覆盖旧下一步'}));
    assert.equal(injected.payload.ok,true);
    const retained=await savedTask(handwritten.title);
    for(const key of ['checkpoint','next_step','description','effective_context','context_mode','history','updated_at'])assert.deepEqual(retained[key],handwritten[key]);

    /* ---------------------------------------------------------------- A-13 ---
     * Polling updates the list/resume, not the open description, focus or caret.
     * An automatic result never increments the human task revision. */
    const autoTask='自动整理接入验证';await todos();await open(autoTask);
    await page.locator('#progress-form [name=priority]').selectOption('high');await save();
    await open(autoTask);assert.equal(await description.inputValue(),'');
    await description.focus();await description.pressSequentially('用户正在输入的统一描述');
    const typed=await description.inputValue(),caret=await description.evaluate(el=>[el.selectionStart,el.selectionEnd]);
    const autoTarget=await savedTask(autoTask);assert.equal(autoTarget.context_revision,'');
    const beforeAuto=snapshots();
    const written=await mcpCall('llmwiki_progress_context_write',autoArgs(autoTarget));
    assert.equal(written.rpc.error,undefined);assert.equal(written.isError,false);assert.equal(written.payload.ok,true);assert.equal(written.payload.changed,true);
    await page.waitForFunction(id=>document.querySelector('#progress-todo .progress-task-row[data-id="'+id+'"] .progress-description-excerpt')?.textContent.includes('自动整理：夜间样本已补齐'),autoTarget.id,{timeout:20000});
    const resumeItem=page.locator('#progress-resume .progress-resume-item').filter({hasText:autoTask});await resumeItem.waitFor();
    assert.match(await resumeItem.innerText(),/自动整理：夜间样本已补齐/);
    assert.deepEqual(snapshots().tasks,beforeAuto.tasks,'自动上下文更新不能改写人工任务文件');
    assert.equal(await description.inputValue(),typed);assert.equal(await description.evaluate(el=>el===document.activeElement),true);
    assert.deepEqual(await description.evaluate(el=>[el.selectionStart,el.selectionEnd]),caret);
    assert.equal(await page.locator('#progress-form [type=submit]').isEnabled(),true);await save();
    const saved=await savedTask(autoTask);assert.equal(saved.description,typed);assert.equal(saved.description_source,'manual');
    for(const key of ['checkpoint','next_step','context_mode'])assert.deepEqual(saved[key],autoTarget[key]);
    assert.equal(saved.auto_context.generated_at,written.payload.generated_at);assert.equal(saved.auto_context.source_record_id,cfg.recordRelPath);
    await open(autoTask);assert.equal(await description.inputValue(),typed);await expand('#progress-legacy');
    const liveContext=JSON.parse(await page.locator('#progress-legacy pre').innerText());assert.deepEqual(liveContext.auto_context,saved.auto_context);
    // Daily files contain separately addressable entries; keep the exact entry ID.
    assert.equal((await context.request.get(origin+base+'/'+saved.auto_context.source_record_id)).status(),200,'MCP 来源日档必须可打开');
    await page.locator('#progress-form [name=record_id] option[value="'+cfg.recordId+'"]').waitFor({state:'attached'});
    await page.locator('#progress-form [name=record_id]').selectOption(cfg.recordId);
    const recordLink=await page.locator('#progress-source a').getAttribute('href');assert.equal(recordLink,base+'/'+cfg.recordId.split('/').map(encodeURIComponent).join('/'));
    assert.equal((await context.request.get(origin+recordLink)).status(),200);await save();
    await open(autoTask);assert.equal(await page.locator('#progress-form [name=record_id]').inputValue(),cfg.recordId);await close();

    // A hidden tab must stop polling and read once immediately when it comes back.
    // This fast harness retains Playwright's default focus/visibility emulation.
    // Exercise the handler with a DOM override here; the separate opt-in
    // run_progress_visibility.py disables emulation and switches real headed tabs.
    await todos();
    const polls=()=>requests.filter(url=>url.includes('/progress?view=summary')).length;
    const setHidden=value=>page.evaluate(hidden=>{
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>hidden});
      Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>hidden?'hidden':'visible'});
      document.dispatchEvent(new Event('visibilitychange'));
    },value);
    await setHidden(true);
    assert.equal(await page.evaluate(()=>document.hidden),true,'visibility 覆盖未生效');
    await page.waitForTimeout(600);
    const polled=polls();
    await page.waitForTimeout(7000);
    assert.equal(polls(),polled,'隐藏标签页不得持续轮询');
    await setHidden(false);
    // Bound this far below the 5s poll period: merely restarting the interval must not
    // satisfy "reads once as soon as it is visible again".
    const wake=Date.now();
    while(polls()===polled&&Date.now()-wake<1500)await page.waitForTimeout(50);
    assert.ok(polls()>polled&&Date.now()-wake<1500,'重新可见时应立即读取一次，而不是等下一个轮询周期');

    /* ---------------------------------------------------------------- A-12 ---
     * A stalled summary cannot block foreground creation/editing or erase SSR.
     * No generator, terminal, background window or scheduling endpoint is called. */
    await todos();assert.ok((await page.locator('#progress-resume').innerText()).includes(typed));
    const stalled=[],pending=new Set(),timers=new Set();
    const stallRoute=route=>{
      const request=route.request();
      if(request.method()==='GET'&&request.url().includes('view=summary')){
        stalled.push(request.url());pending.add(route);
        timers.add(setTimeout(()=>{if(pending.delete(route))route.abort().catch(()=>{});},30000));return;
      }
      return route.continue();
    };
    await page.route('**/api/project/**/progress*',stallRoute);
    const stallStart=Date.now();while(!stalled.length&&Date.now()-stallStart<10000)await page.waitForTimeout(100);
    assert.ok(stalled.length>0,'后台轮询必须真的进入延迟状态');
    const pagesBefore=context.pages().length,dialogsBefore=dialogs.length,requestMark=requests.length,started=Date.now();
    await page.locator('#progress-new').click();await page.locator('#progress-form [name=title]').fill('慢生成期间新增');
    await page.locator('#progress-form [name=priority]').selectOption('low');
    assert.equal(await page.locator('#progress-form [name=ddl]').inputValue(),'');
    await page.locator('#progress-form [type=submit]').click();
    await row('慢生成期间新增').waitFor({timeout:2500});assert.ok(Date.now()-started<2500,'前台新增不能等待后台 summary');
    const slowTask=await savedTask('慢生成期间新增');assert.ok(slowTask);assert.equal(slowTask.status,'planned');assert.equal(slowTask.resume_eligible,true);
    assert.equal(slowTask.ddl,'');assert.equal(slowTask.priority,'low');
    await open('慢生成期间新增');await page.locator('#progress-form [name=record_id] option').nth(1).waitFor({state:'attached',timeout:10000});await close();
    assert.equal(await page.locator('dialog[open]').count(),0);assert.equal(await page.locator('#progress-new').isEnabled(),true);
    assert.equal(await row('慢生成期间新增').locator('.progress-inline-priority').isEnabled(),true);
    await page.locator('#progress-new').click();const titleInput=page.locator('#progress-form [name=title]');await titleInput.scrollIntoViewIfNeeded();
    assert.equal(await titleInput.evaluate(target=>{const box=target.getBoundingClientRect(),top=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);return target===top||target.contains(top);}),true);
    await close();assert.equal(context.pages().length,pagesBefore);assert.equal(dialogs.length,dialogsBefore);
    const unexpectedCalls=requests.slice(requestMark).filter(url=>{
      const route=url.slice(origin.length).split('#')[0];
      return !(/^\/static\//.test(route)||/^\/api\/project\/[^/]+\/progress(\?view=(summary|records))?$/.test(route)
        ||route==='/'||route===base||route===base+'/todos'||route.startsWith('/favicon'));
    });
    assert.deepEqual(unexpectedCalls,[],'新增空描述只应访问已知的进度/静态接口');
    await page.goto(origin+base+'/todos');await page.locator('#progress-resume li').first().waitFor();
    assert.ok((await page.locator('#progress-resume').innerText()).includes(typed),'summary 被阻塞时 SSR 仍显示保存的统一描述');
    timers.forEach(clearTimeout);await Promise.all([...pending].map(route=>route.abort().catch(()=>{})));pending.clear();
    await page.unroute('**/api/project/**/progress*',stallRoute);await todos();

    /* ---------------------------------------------------------------- A-11 ---
     * Linking a note keeps the note and its screenshot byte-for-byte. Unlinking
     * keeps the task. A record that moves away is reported, never deleted. */
    const notePath=path.join(cfg.wikiRoot,cfg.noteRelPath),imagePath=path.join(cfg.wikiRoot,cfg.imageRelPath);
    const noteBefore=fs.readFileSync(notePath),imageBefore=fs.readFileSync(imagePath);
    assert.ok(fs.existsSync(imagePath),'夹具缺少截图资源');
    await page.locator('#progress-todo .progress-task-row').filter({hasText:'消融实验整理'}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    const select=page.locator('#progress-form [name=record_id]');
    await select.locator(`option[value="${cfg.noteRelPath}"]`).waitFor({state:'attached',timeout:20000});
    await select.selectOption(cfg.noteRelPath);
    await page.locator('#progress-source a').waitFor();
    const noteHref=await page.locator('#progress-source a').getAttribute('href');
    assert.equal(noteHref,`${base}/${cfg.noteRelPath}`,'关联入口应指向所选的那一篇笔记');
    assert.equal((await context.request.get(origin+noteHref)).status(),200,'关联笔记应可打开');
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    assert.deepEqual(fs.readFileSync(notePath),noteBefore,'关联笔记不得改动笔记内容');
    assert.deepEqual(fs.readFileSync(imagePath),imageBefore,'关联笔记不得改动截图');

    await page.locator('#progress-todo .progress-task-row').filter({hasText:'消融实验整理'}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    // Positive control first: without it, "no note entry after unlinking" would also
    // hold if the link had never been stored at all.
    await page.locator(`#progress-form [name=record_id] option[value="${cfg.noteRelPath}"]`).waitFor({state:'attached',timeout:20000});
    assert.equal(await page.locator('#progress-form [name=record_id]').inputValue(),cfg.noteRelPath,'重新打开后应显示已保存的关联笔记');
    await page.locator('#progress-form [name=record_id]').selectOption('');
    assert.equal(await page.locator('#progress-source a').count(),0,'取消关联后不应再有笔记入口');
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    assert.equal(await taskButtons('消融实验整理').count(),1,'取消关联不得删除任务');
    assert.deepEqual(fs.readFileSync(notePath),noteBefore,'取消关联不得改动笔记内容');
    assert.deepEqual(fs.readFileSync(imagePath),imageBefore,'取消关联不得改动截图');
    const afterUnlink=await summary(cfg.projectId);
    assert.equal(afterUnlink.tasks.find(t=>t.title==='消融实验整理').record_id,'','取消关联应清空引用');

    await row('消融实验整理').locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await page.locator('#progress-form [name=record_id]').selectOption(cfg.noteRelPath);
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    fs.rmSync(notePath);
    await page.reload();
    await page.locator('#progress-resume').waitFor();
    await page.locator('#progress-todo .progress-task-row').filter({hasText:'消融实验整理'}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await page.locator('#progress-source').getByText('来源不可用（保留原关联）',{exact:true}).waitFor({timeout:20000});
    assert.equal(await page.locator('#progress-source a').count(),0,'来源消失后不应再提供打开入口');
    await page.locator('#progress-close').click();
    assert.equal(await taskButtons('消融实验整理').count(),1,'来源消失不得删除任务');
    assert.equal((await summary(cfg.projectId)).tasks.find(t=>t.title==='消融实验整理').record_id,cfg.noteRelPath,'来源消失不得擅自清空引用');
    assert.deepEqual(fs.readFileSync(imagePath),imageBefore,'来源消失不得改动截图');
    // Importing old candidates stays an explicit choice and never rewrites the daily file.
    const recordPath=path.join(cfg.wikiRoot,cfg.recordRelPath);
    const recordBefore=fs.readFileSync(recordPath);
    const tasksBefore=(await summary(cfg.projectId)).tasks.length;
    await page.locator('#progress-import').click();
    await page.locator('#progress-import-dialog[open]').waitFor();
    await page.locator('#progress-candidates input').first().waitFor();
    assert.equal((await summary(cfg.projectId)).tasks.length,tasksBefore,'仅打开导入对话框不得创建任务');
    assert.deepEqual(fs.readFileSync(recordPath),recordBefore,'导入候选不得改写科研日档');
    await page.locator('#progress-import-close').click();

    assert.deepEqual(snapshotFiles(cfg.otherWikiRoot),otherBefore,'其他项目任务和助手上下文不能被改写');
    assert.deepEqual(snapshotFiles(cfg.legacyWikiRoot),legacyBefore,'只读项目必须始终字节不变');

    // Only the deliberate, expected network noise is allowed through, matched exactly:
    // a substring test on "404"/"409" would also hide a real application error that
    // happened to quote a status code.
    const noise=[/^Failed to load resource: net::ERR_(ABORTED|FAILED|NO_BUFFER_SPACE)$/,
                 /^Failed to load resource: the server responded with a status of (404|409)\b/];
    const unexpected=errors.filter(e=>!noise.some(pattern=>pattern.test(e)));
    assert.deepEqual(unexpected,[],'不应出现未预期的页面错误');
    assert.deepEqual(dialogs,[],'不应出现未预期的原生对话框');
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-progress-acceptance.png'),fullPage:true});
    console.log('Progress acceptance passed: unified SSR/resume + priority/DDL; explicit Done/restore/timestamps/history; legacy lossless/read-only; MCP ownership/rejection/versioning; project isolation; polling/caret/visibility; stalled-source foreground; note/record/image integrity.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
