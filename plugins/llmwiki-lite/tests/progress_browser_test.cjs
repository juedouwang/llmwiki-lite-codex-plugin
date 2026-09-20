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
    const summary=async(pid)=>await (await context.request.get(api(pid))).json();
    // Mirrors progress.js resumeTasks(): last human edit first, ties by task id.
    const resumeOrder=(tasks)=>tasks.filter(t=>['active','blocked'].includes(t.status))
      .sort((a,b)=>b.updated_at.localeCompare(a.updated_at)||a.id.localeCompare(b.id));
    const row=(title)=>page.locator('.progress-task-row').filter({hasText:title});
    const doneRow=(title)=>page.locator('#progress-done .progress-task-row').filter({hasText:title});
    const taskButtons=(title)=>page.locator('.progress-task').filter({hasText:title});
    async function openDone(){
      if(!(await page.locator('#progress-done').evaluate(el=>el.open)))await page.locator('#progress-done summary').click();
    }
    // The dialog's <details> keep their state between openings, so clicking a
    // summary twice would close what the assertion is about to read.
    async function expand(selector){
      if(!(await page.locator(selector).evaluate(el=>el.open)))await page.locator(`${selector} summary`).click();
    }
    async function todos(){await page.goto(origin+base+'/todos');await page.locator('#progress-resume').waitFor();}
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
     * Multi-project entry points: A's list row points at its rank-1 task, the home
     * page shows the top three, and B neither borrows A's context nor dresses a
     * not-started task up as "what you were doing". */
    const seeded=await summary(cfg.projectId);
    const expected=resumeOrder(seeded.tasks);
    assert.ok(expected.length>=4,`夹具需要至少四个进行中任务，实际 ${expected.length}`);
    const initialDone=seeded.tasks.filter(t=>t.status==='done').length;

    await page.goto(origin+'/');
    const rowA=page.locator('.project-row').filter({has:page.locator(`a.project-row-main[href="${base}/todos"]`)});
    assert.equal(await rowA.count(),1,'项目列表缺少 A 的行');
    assert.equal(await rowA.locator('.project-row-task').count(),1,'A 有进行中任务时应有一条最近任务入口');
    assert.equal((await rowA.locator('.project-row-task').innerText()).trim(),expected[0].title,'列表摘要必须指向排序第一的任务');
    assert.ok((await rowA.locator('.project-row-task').getAttribute('href')).endsWith(`${base}/todos#task-${expected[0].id}`),'列表摘要应直达该任务');
    // A nested anchor cannot exist in a parsed DOM: the parser hoists the inner <a> out.
    // The invariant therefore has to be checked against the served markup.
    const listHtml=await (await context.request.get(origin+'/')).text();
    const mainLink=/<a class="project-row-main"[\s\S]*?<\/a>/.exec(listHtml);
    assert.ok(mainLink,'项目列表缺少项目主链接');
    assert.ok(!/class="project-row-task"/.test(mainLink[0]),'项目列表不得出现嵌套链接');
    const rowB=page.locator('.project-row').filter({has:page.locator(`a.project-row-main[href="${other}/todos"]`)});
    assert.equal(await rowB.count(),1,'项目列表缺少 B 的行');
    assert.equal(await rowB.locator('.project-row-task').count(),0,'只有未开始任务的项目不应显示最近任务');

    await rowA.locator('.project-row-task').click();
    await page.locator(`#progress-dialog[open]`).waitFor();
    assert.equal(await page.locator('#progress-form [name=title]').inputValue(),expected[0].title,'点击列表摘要应打开对应任务');
    await page.locator('#progress-close').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});

    await page.goto(origin+base);
    await page.locator('.resume-block[data-project] .resume-item').first().waitFor();
    assert.equal(await page.locator('.resume-block .resume-item').count(),3,'首页继续上次最多三个任务');
    assert.deepEqual(
      (await page.locator('.resume-block .resume-title').allTextContents()).map(t=>t.trim()),
      expected.slice(0,3).map(t=>t.title),
      '首页继续上次的排序应为最后修改时间降序'
    );
    // The same block is rendered server-side so it survives a progress page that fails
    // to load; the client re-renders it from the summary call, so the served markup is
    // checked separately from the live DOM.
    const homeHtml=await (await context.request.get(origin+base)).text();
    assert.equal((homeHtml.match(/class="resume-item"/g)||[]).length,3,'服务端渲染的首页继续上次也应为三个任务');
    await page.goto(origin+other);
    await page.locator('.resume-block[data-project]').waitFor({state:'attached'});
    assert.equal(await page.locator('.resume-block .resume-item').count(),0,'B 没有进行中任务时不应渲染继续上次');
    assert.equal(await page.locator('.resume-block[data-project]').evaluate(el=>el.hidden),true,'B 的继续上次必须隐藏');
    const otherHtml=await (await context.request.get(origin+other)).text();
    assert.match(otherHtml,/class="resume-block"[^>]*hidden/,'B 的服务端输出就应是隐藏的空块');
    assert.ok(!otherHtml.includes('resume-item'),'B 的服务端输出不应含继续上次条目');
    // textContent, not innerText: hidden nodes and collapsed details are still a leak.
    const bodyB=await page.locator('main').evaluate(el=>el.textContent);
    assert.ok(!bodyB.includes('已跑完基线'),'B 不得借用 A 的上下文');
    assert.ok(!bodyB.includes(expected[0].title),'B 不得显示 A 的任务标题');

    /* ---------------------------------------------------------------- A-04 ---
     * Done / reopen / Done again: one task throughout, the two completion actions
     * stay distinguishable, and a reopened task is not counted as finished. */
    const cycle='夜间数据验证';
    await todos();
    assert.equal(await taskButtons(cycle).count(),1,'开始前应只有一个同名任务');
    await row(cycle).locator('.progress-inline-status').selectOption('done');
    await openDone();
    await doneRow(cycle).waitFor();
    assert.equal(await page.locator('#progress-done summary span').innerText(),String(initialDone+1),'完成一条后 Done 数量应加一');
    assert.equal(await taskButtons(cycle).count(),1,'完成后不得复制出第二个任务');
    // Anchor the negative on live content: an empty or dead resume block would
    // otherwise make "the completed task is gone" true for the wrong reason.
    const afterComplete=await summary(cfg.projectId);
    const stillShown=resumeOrder(afterComplete.tasks).slice(0,3).map(t=>t.title);
    assert.equal(stillShown.length,3,'继续上次仍应有内容');
    assert.deepEqual(
      (await page.locator('#progress-resume .progress-resume-item strong').allTextContents()).map(t=>t.trim()),
      stillShown,
      '完成后继续上次仍应列出其余进行中任务'
    );
    assert.ok(!(await page.locator('#progress-resume').innerText()).includes(cycle),'已完成任务必须离开继续上次');
    assert.equal(await page.locator('#progress-timeline .progress-task-row, #progress-unscheduled .progress-task-row').filter({hasText:cycle}).count(),0,'完成的任务不应留在未完成时间轴');

    await doneRow(cycle).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await expand('#progress-info');
    const firstInfo=await page.locator('#progress-info').innerText();
    const firstTime=(/完成时间：(.+)/.exec(firstInfo)||[])[1];
    assert.ok(firstTime,'已知完成时间必须显示具体时间');
    assert.ok(!firstInfo.includes('完成时间未记录'),'有真实完成时间时不得显示未记录');
    const firstHistory=await page.locator('#progress-history .progress-history-entry').allTextContents();
    assert.match(firstHistory[0],/已完成/,'最新修改记录应是完成动作');
    await page.locator('#progress-close').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    // Second-resolution rendering: keep the two completion times apart on purpose.
    await page.waitForTimeout(1100);

    await openDone();
    await doneRow(cycle).locator('.progress-inline-status').selectOption('active');
    await page.locator('#progress-resume .progress-resume-item').filter({hasText:cycle}).waitFor();
    assert.equal(await page.locator('#progress-done summary span').innerText(),String(initialDone),'重开后 Done 数量应回落');
    assert.equal(await doneRow(cycle).count(),0,'重开期间不得把它当作当前已完成事项');
    assert.equal(await taskButtons(cycle).count(),1,'重开不得复制任务');
    await row(cycle).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await expand('#progress-info');
    const reopened=await page.locator('#progress-info').innerText();
    assert.ok(!/完成时间：\d/.test(reopened),'重开后必须清除完成时间');
    assert.match(reopened,/未完成/,'重开后的任务不是已完成');
    await page.locator('#progress-close').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});

    await row(cycle).locator('.progress-inline-status').selectOption('done');
    await openDone();
    await doneRow(cycle).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await expand('#progress-info');
    const secondTime=(/完成时间：(.+)/.exec(await page.locator('#progress-info').innerText())||[])[1];
    assert.ok(secondTime,'第二次完成也必须显示真实完成时间');
    await expand('#progress-history');
    const history=await page.locator('#progress-history .progress-history-entry').allTextContents();
    const finished=history.map((text,index)=>({text,index})).filter(item=>/已完成/.test(item.text));
    const reopenedAt=history.findIndex(text=>/进行中/.test(text));
    assert.ok(finished.length>=2,`两次完成动作应各留一条记录，实际 ${finished.length}`);
    assert.ok(reopenedAt>finished[0].index&&reopenedAt<finished[1].index,'两次完成之间应有一条重开记录');
    const stamp=text=>(/完成时间：(.+)/.exec(text)||[])[1];
    assert.equal(stamp(finished[0].text),secondTime,'最新记录应与当前完成时间一致');
    assert.equal(stamp(finished[1].text),firstTime,'第一次完成的时间应保留在修改记录中');
    assert.notEqual(stamp(finished[0].text),stamp(finished[1].text),'两次完成时间应可区分');
    assert.equal(await page.locator('#progress-done summary span').innerText(),String(initialDone+1),'再次完成后 Done 数量应加一');
    assert.equal(await taskButtons(cycle).count(),1,'两轮完成重开后仍应只有一个任务');
    await page.locator('#progress-close').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});

    /* ---------------------------------------------------------------- A-05 ---
     * A done task from the old file has no completion time. It must keep saying so,
     * stay at the end of Done, and not gain a time from a later title edit. */
    const legacyTitle='旧完成事项（无完成时间）';
    await openDone();
    const doneTitles=(await page.locator('#progress-done .progress-task').allTextContents()).map(t=>t.trim());
    // Compare against the full expected order rather than the last row alone: the
    // legacy fixture is edited later than every completion, so an implementation
    // that sorted by last-edited instead of completion time would show up here.
    const doneNow=(await summary(cfg.projectId)).tasks.filter(t=>t.status==='done');
    const expectedDone=doneNow.filter(t=>t.completed_at)
      .sort((a,b)=>b.completed_at.localeCompare(a.completed_at)||a.id.localeCompare(b.id))
      .concat(doneNow.filter(t=>!t.completed_at).sort((a,b)=>a.id.localeCompare(b.id)))
      .map(t=>t.title.trim());
    assert.deepEqual(doneTitles,expectedDone,'已完成顺序应为已知完成时间优先、未知完成时间置末');
    assert.equal(doneTitles[doneTitles.length-1],legacyTitle,'没有完成时间的旧任务排在已知完成时间之后');
    await page.locator('#progress-done .progress-task-row').filter({hasText:legacyTitle}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await expand('#progress-info');
    const legacyInfo=await page.locator('#progress-info').innerText();
    assert.match(legacyInfo,/完成时间未记录/,'旧完成任务必须显示完成时间未记录');
    assert.ok(!/完成时间：\d/.test(legacyInfo),'不得用文件时间、迁移时间或当天日期冒充完成时间');
    await expand('#progress-history');
    assert.match(await page.locator('#progress-history').innerText(),/完成时间未记录/,'修改记录同样不得补造完成时间');
    await page.locator('#progress-form [name=title]').fill(legacyTitle+'（改名）');
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    await openDone();
    await page.locator('#progress-done .progress-task-row').filter({hasText:'（改名）'}).locator('.progress-task').click();
    await expand('#progress-info');
    assert.match(await page.locator('#progress-info').innerText(),/完成时间未记录/,'修改已完成任务的标题不得改变完成时间');
    await page.locator('#progress-close').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});

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
     * An explicit clear is a human change that incoming automatic content must not
     * refill; only the explicit 使用自动内容 action releases one field. */
    const owner='人工覆盖与自动版本';
    await todos();
    const ownerTask=(await summary(cfg.projectId)).tasks.find(t=>t.title===owner);
    assert.ok(ownerTask,'夹具缺少 A-08 用的任务');
    assert.equal(ownerTask.context_mode.next_step,'auto','夹具任务的下一步应由自动内容拥有');
    const autoV1=await mcpCall('llmwiki_progress_context_write',autoArgs(ownerTask,{checkpoint:'自动停点V1',next_step:'自动下一步V1'}));
    assert.equal(autoV1.payload.ok,true,`夹具自动版本应写入：${JSON.stringify(autoV1.payload)}`);
    await todos();
    await page.locator('#progress-unscheduled .progress-task-row').filter({hasText:owner}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    const ownerNext=page.locator('#progress-form [name=next_step]');
    assert.equal(await ownerNext.inputValue(),'自动下一步V1','未编辑字段应显示自动内容');
    assert.equal(await page.locator('#progress-form [name=next_step_mode]').inputValue(),'auto');
    assert.equal(await page.locator('.progress-use-auto[data-field=next_step]').isVisible(),false,'没有人工覆盖时不应提供使用自动内容');
    // Clearing must be a real edit: the manual flag is only set by an input event.
    await ownerNext.click();
    await ownerNext.press('Control+a');
    await ownerNext.press('Delete');
    assert.equal(await ownerNext.inputValue(),'');
    assert.equal(await page.locator('#progress-form [name=next_step_mode]').inputValue(),'manual','清空应把该字段标为人工');
    assert.equal(await page.locator('.progress-use-auto[data-field=next_step]').isVisible(),true,'存在人工覆盖时应提供使用自动内容');
    assert.equal(await page.locator('.progress-use-auto[data-field=checkpoint]').isVisible(),false,'另一个字段不应受影响');
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    const cleared=(await summary(cfg.projectId)).tasks.find(t=>t.title===owner);
    assert.equal(cleared.context_mode.next_step,'manual','显式清空应保存为人工覆盖');
    assert.equal(cleared.effective_context.next_step,'','清空后有效内容应保持为空');
    assert.equal(cleared.auto_context.next_step,'自动下一步V1','自动版本仍应保留');
    const autoV2=await mcpCall('llmwiki_progress_context_write',autoArgs(cleared,{checkpoint:'自动停点V2',next_step:'新的自动下一步'}));
    assert.equal(autoV2.payload.ok,true,`第二版自动内容应写入：${JSON.stringify(autoV2.payload)}`);
    await todos();
    await page.locator('#progress-unscheduled .progress-task-row').filter({hasText:owner}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await expand('#progress-auto');
    assert.match(await page.locator('#progress-auto').innerText(),/下一步：新的自动下一步/,'自动整理应保留最新自动版本');
    assert.equal(await page.locator('#progress-form [name=next_step]').inputValue(),'','自动更新到达时被清空的字段仍应为空');
    assert.equal(await page.locator('#progress-form [name=checkpoint]').inputValue(),'自动停点V2','未覆盖字段应读取新的自动内容');
    await page.locator('.progress-use-auto[data-field=next_step]').click();
    assert.equal(await page.locator('#progress-form [name=next_step]').inputValue(),'新的自动下一步','明确点击后才改用最新自动版本');
    assert.equal(await page.locator('#progress-form [name=next_step_mode]').inputValue(),'auto','点击应解除该字段的人工覆盖');
    assert.equal(await page.locator('#progress-form [name=checkpoint_mode]').inputValue(),'auto','另一个字段的模式不应改变');
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    const ownerAfter=(await summary(cfg.projectId)).tasks.find(t=>t.title===owner);
    assert.equal(ownerAfter.context_mode.next_step,'auto','保存后该字段应由自动内容拥有');
    assert.equal(ownerAfter.effective_context.next_step,'新的自动下一步','保存后应显示最新自动版本');

    /* ---------------------------------------------------------------- A-13 ---
     * A newer automatic version arrives while the user is typing. The read-only
     * display must move on; the open form, its text and the caret must not. */
    const autoTask='自动整理接入验证';
    await todos();
    await page.locator('#progress-unscheduled .progress-task-row').filter({hasText:autoTask}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    const nextStep=page.locator('#progress-form [name=next_step]');
    await expand('#progress-auto');
    assert.match(await page.locator('#progress-auto').innerText(),/自动整理尚未接通/,'没有自动结果时应如实说明尚未接通');
    await nextStep.click();
    await nextStep.pressSequentially('用户正在输入的下一步');
    const typed=await nextStep.inputValue();
    assert.equal(typed,'用户正在输入的下一步');
    assert.equal(await page.locator('#progress-form [name=next_step_mode]').inputValue(),'manual','手动输入应把该字段标为人工');
    const caret=await nextStep.evaluate(el=>el.selectionStart);

    const beforeInject=await summary(cfg.projectId);
    const autoTarget=beforeInject.tasks.find(t=>t.title===autoTask);
    assert.ok(autoTarget,'未找到用于自动接入的任务');
    assert.equal(autoTarget.context_revision,'','首次接入前不应有自动版本');
    const written=await mcpCall('llmwiki_progress_context_write',autoArgs(autoTarget));
    assert.ok(!written.rpc.error,`MCP 调用失败：${JSON.stringify(written.rpc.error||written.rpc)}`);
    assert.equal(written.isError,false,'合法接入不应返回错误');
    assert.equal(written.payload.ok,true,`自动接入被拒绝：${JSON.stringify(written.payload)}`);
    assert.equal(written.payload.changed,true,'首次接入应写入新版本');

    await page.waitForFunction(()=>{
      const body=document.querySelector('#progress-auto-body');
      return body&&!body.hidden&&body.innerText.includes('自动整理：夜间样本已补齐');
    },null,{timeout:20000});
    const resumeItem=page.locator('#progress-resume .progress-resume-item').filter({hasText:autoTask});
    await resumeItem.waitFor();
    assert.ok((await resumeItem.innerText()).includes('自动整理：夜间样本已补齐'),'未编辑的摘要显示应就地更新');
    assert.equal(await nextStep.inputValue(),typed,'后台刷新不得改写正在输入的表单');
    assert.equal(await nextStep.evaluate(el=>el===document.activeElement),true,'焦点必须留在正在输入的字段');
    assert.equal(await nextStep.evaluate(el=>el.selectionStart),caret,'光标位置不得被刷新移动');
    assert.equal(await page.locator('#progress-form [name=next_step_mode]').inputValue(),'manual','刷新不得把人工字段改回自动');
    const shownStamp=await page.evaluate(ts=>new Date(ts).toLocaleString('zh-CN'),written.payload.generated_at);
    assert.ok((await page.locator('#progress-auto').innerText()).includes(`生成时间：${shownStamp}`),'自动整理应显示服务端返回的生成时间，而不是任意时间');
    // A-07: the stored automatic version must point back at the record it came from.
    const sourceLink=page.locator('#progress-auto-body a');
    await sourceLink.waitFor();
    assert.equal((await context.request.get(origin+await sourceLink.getAttribute('href'))).status(),200,'自动版本的来源记录应可打开');
    // Saving the typed text must still work: a background automatic update may not
    // manufacture a human revision conflict.
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    const saved=(await summary(cfg.projectId)).tasks.find(t=>t.title===autoTask);
    assert.equal(saved.next_step,typed,'保存人工内容后不得丢失或出现伪冲突');
    assert.equal(saved.context_mode.next_step,'manual','保存后该字段应记为人工');
    await page.locator('#progress-resume .progress-resume-item').filter({hasText:autoTask}).click();
    await page.locator('#progress-dialog[open]').waitFor();
    assert.equal(await page.locator('#progress-form [name=next_step]').inputValue(),typed,'重新打开详情应读取到人工填写的内容');
    assert.equal(await page.locator('#progress-form [name=checkpoint]').inputValue(),'自动整理：夜间样本已补齐','未编辑字段应读取到新的自动版本');
    await expand('#progress-auto');
    assert.match(await page.locator('#progress-auto').innerText(),/自动整理：跑消融实验/,'自动整理应保留最新自动版本');
    await page.locator('#progress-close').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});

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
     * A slow or unavailable background source must not block any foreground action,
     * mask the page, open a terminal, or disable buttons. */
    await todos();
    assert.ok((await page.locator('#progress-resume').innerText()).includes('自动整理：夜间样本已补齐'),'旧上下文应仍可读');
    const stalled=[],pending=new Set(),timers=new Set();
    await page.route('**/api/project/**/progress*',route=>{
      const request=route.request();
      if(request.method()==='GET'&&request.url().includes('view=summary')){
        stalled.push(request.url());
        pending.add(route);
        timers.add(setTimeout(()=>{if(pending.delete(route))try{route.abort();}catch{}},30000));
        return;
      }
      route.continue();
    });
    const stallStart=Date.now();
    while(!stalled.length&&Date.now()-stallStart<10000)await page.waitForTimeout(200);
    assert.ok(stalled.length>0,'五秒轻量刷新未发起，延迟模拟无效');
    const pagesBefore=context.pages().length,dialogsBefore=dialogs.length,requestMark=requests.length;
    const started=Date.now();
    await page.locator('#progress-add input').fill('慢生成期间新增');
    await page.locator('#progress-add input').press('Enter');
    // The app itself abandons a stalled read after 3s, so a bound near that would also
    // pass for a foreground that really did wait for the background source.
    await page.locator('#progress-unscheduled .progress-task-row').filter({hasText:'慢生成期间新增'}).waitFor({timeout:2500});
    assert.ok(Date.now()-started<2500,'创建任务不应等待后台来源');
    assert.ok((await summary(cfg.projectId)).tasks.some(t=>t.title==='慢生成期间新增'),'后台读取被延迟时任务仍应正常落盘');
    await row('慢生成期间新增').locator('.progress-inline-status').selectOption('active');
    await page.locator('#progress-resume .progress-resume-item').filter({hasText:'慢生成期间新增'}).waitFor({timeout:6000});
    // Note material still loads on demand: the records view is not the stalled one.
    await row('慢生成期间新增').locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await page.locator('#progress-form [name=record_id] option').nth(1).waitFor({state:'attached',timeout:10000});
    await page.locator('#progress-close').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    assert.equal(await page.locator('dialog[open]').count(),0,'前台操作不得弹出遮罩或对话框');
    assert.equal(await page.locator('#progress-add button').isDisabled(),false,'添加按钮不得因后台来源被禁用');
    assert.equal(await row('慢生成期间新增').locator('.progress-inline-status').isDisabled(),false,'状态选择器不得因后台来源被禁用');
    assert.equal(await page.evaluate(()=>{
      const target=document.querySelector('#progress-add input');
      const box=target.getBoundingClientRect();
      const top=document.elementFromPoint(box.left+box.width/2,box.top+box.height/2);
      return target===top||target.contains(top);
    }),true,'页面被遮罩覆盖，前台操作实际不可用');
    assert.equal(context.pages().length,pagesBefore,'不得弹出终端或新窗口');
    assert.equal(dialogs.length,dialogsBefore,'不得弹出原生对话框');
    // The foreground may only touch the endpoints it actually needs. A call to a
    // generator, terminal or scheduler would show up here whatever it is called —
    // a pattern match on four English words would not have caught a rename.
    const unexpectedCalls=requests.slice(requestMark).filter(url=>{
      const route=url.slice(origin.length).split('#')[0];
      return !(/^\/static\//.test(route)
        || /^\/api\/project\/[^/]+\/progress(\?view=(summary|records))?$/.test(route)
        || route==='/'||route===base||route===base+'/todos'
        || route.startsWith('/favicon'));
    });
    assert.deepEqual(unexpectedCalls,[],'前台链路只应访问已知的进度与静态资源接口');
    // Returning to the project page still reads the previously stored context, because
    // the landing page renders it server-side rather than waiting for the slow read.
    await page.goto(origin+base);
    await page.locator('.resume-block .resume-item').first().waitFor();
    assert.ok((await page.locator('.resume-block').innerText()).includes('自动整理：夜间样本已补齐'),'返回项目页时旧上下文仍应可读');
    timers.forEach(clearTimeout);
    pending.forEach(route=>{try{route.abort();}catch{}});
    pending.clear();
    await page.unroute('**/api/project/**/progress*');
    await todos();

    /* ---------------------------------------------------------------- A-11 ---
     * Linking a note keeps the note and its screenshot byte-for-byte. Unlinking
     * keeps the task. A record that moves away is reported, never deleted. */
    const notePath=path.join(cfg.wikiRoot,cfg.noteRelPath),imagePath=path.join(cfg.wikiRoot,cfg.imageRelPath);
    const noteBefore=fs.readFileSync(notePath),imageBefore=fs.readFileSync(imagePath);
    assert.ok(fs.existsSync(imagePath),'夹具缺少截图资源');
    await page.locator('#progress-unscheduled .progress-task-row').filter({hasText:'消融实验整理'}).locator('.progress-task').click();
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

    await page.locator('#progress-unscheduled .progress-task-row').filter({hasText:'消融实验整理'}).locator('.progress-task').click();
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
    await page.locator('#progress-unscheduled .progress-task-row').filter({hasText:'消融实验整理'}).locator('.progress-task').click();
    await page.locator('#progress-dialog[open]').waitFor();
    await page.locator('#progress-source').getByText('来源不可用').waitFor({timeout:20000});
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

    // Only the deliberate, expected network noise is allowed through, matched exactly:
    // a substring test on "404"/"409" would also hide a real application error that
    // happened to quote a status code.
    const noise=[/^Failed to load resource: net::ERR_(ABORTED|FAILED|NO_BUFFER_SPACE)$/,
                 /^Failed to load resource: the server responded with a status of (404|409)\b/];
    const unexpected=errors.filter(e=>!noise.some(pattern=>pattern.test(e)));
    assert.deepEqual(unexpected,[],'不应出现未预期的页面错误');
    assert.deepEqual(dialogs,[],'不应出现未预期的原生对话框');
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-progress-acceptance.png'),fullPage:true});
    console.log('Progress acceptance checks passed: A-06 list/home entry points, A-04 done-reopen-done with history, A-05 legacy completion time, A-13 poll refresh without stealing input, A-12 slow source not blocking, A-11 note/link integrity, DOM-simulated hidden-tab polling (real window switch covered by separate opt-in harness).');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
