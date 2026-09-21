/* Isolated progress browser regressions. No package/browser downloads. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
  const cfg=JSON.parse(process.argv[2]);
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  const checks=[];
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1050},permissions:['clipboard-read','clipboard-write']});
    const page=await context.newPage();
    page.setDefaultTimeout(15000);
    const errors=[],uploads=[];page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.url().endsWith('/notebook/upload'))uploads.push(r.url());});
    page.on('dialog',d=>d.accept());
    const base=`${cfg.origin}/project/${cfg.projectId}`,api=`${cfg.origin}/api/project/${cfg.projectId}/progress`;
    const data=async()=>await(await context.request.get(api+'?view=summary')).json();
    const task=async title=>(await data()).tasks.find(t=>t.title===title);
    const row=title=>page.locator('#progress-todo .progress-task-row').filter({has:page.getByRole('button',{name:title,exact:true})});
    const form=page.locator('#progress-form'),description=form.locator('[name=description]');
    const save=async()=>{await form.getByRole('button',{name:'保存',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#progress-dialog').open);};
    const open=async title=>{await page.locator('#progress-todo-tab').click();await row(title).getByRole('button',{name:title,exact:true}).click();};
    const close=async()=>{await page.locator('#progress-close').click();await page.waitForFunction(()=>!document.querySelector('#progress-dialog').open);};
    const screenshot=async name=>page.screenshot({path:path.join(cfg.output,name),fullPage:true});
    const until=async predicate=>{const deadline=Date.now()+15000;while(Date.now()<deadline){if(await predicate())return;await page.waitForTimeout(60);}throw new Error('Timed out waiting for persisted task');};
    // Read-only migration uses a separate fixture; the Python harness verifies exact bytes.
    await page.goto(`${cfg.origin}/project/${cfg.legacyProjectId}/todos`);
    await page.locator('#progress-todo .progress-task-row').first().waitFor();
    await page.goto(base+'/todos');await row('高优先级未排期').waitFor();
    assert.equal(await form.locator('[name=checkpoint],[name=next_step],[name=start],[name=end],[name=status]').count(),0);
    assert.equal(await page.locator('#progress-todo .progress-task-row').count(),4);
    assert.equal(await page.locator('#progress-todo .progress-task').first().innerText(),'高优先级未排期');
    assert.equal(await page.locator('#progress-todo .progress-task').last().innerText(),'低优先级明天截止');
    await page.locator('#progress-priority-filter').selectOption('high');assert.equal(await page.locator('#progress-todo .progress-task-row').count(),1);
    await page.locator('#progress-priority-filter').selectOption('');
    checks.push('priority ordering/filter; one description; optional DDL');

    await page.locator('#progress-new').click();
    await form.locator('[name=title]').fill('完整新任务');await description.fill('## 研究计划\n先复现实验，再分析误差。');
    await form.locator('[name=priority]').selectOption('high');await form.getByRole('button',{name:'明天',exact:true}).click();
    const tomorrow=await form.locator('[name=ddl]').inputValue();assert.match(tomorrow,/^\d{4}-\d{2}-\d{2}$/);
    await save();let fresh=await task('完整新任务');assert.equal(fresh.ddl,tomorrow);assert.equal(fresh.status,'planned');assert.equal(fresh.start,'');assert.equal(fresh.end,'');
    await row('完整新任务').locator('.progress-inline-priority').selectOption('low');await until(async()=> (await task('完整新任务')).priority==='low');
    await open('完整新任务');await form.getByRole('button',{name:'清除日期',exact:true}).click();await save();assert.equal((await task('完整新任务')).ddl,'');
    await open('完整新任务');await form.locator('[name=priority]').selectOption('high');await form.getByRole('button',{name:'今天',exact:true}).click();await save();
    assert.equal(await page.locator('#progress-timeline .progress-marker[aria-label^="完整新任务"]').count(),1);
    checks.push('create/edit; quick dates; clear date; inline priority; deadline calendar');

    // Merge old fields read-only; metadata edits must not claim the automatic/manual description.
    await open('旧任务兼容');assert.match(await description.inputValue(),/上次完成基线[\s\S]*下一步验证/);
    await form.locator('[name=priority]').selectOption('high');await form.getByRole('button',{name:'清除日期',exact:true}).click();await save();
    let legacy=await task('旧任务兼容');assert.equal(legacy.status,'blocked');assert.equal(legacy.description_source,'legacy');assert.equal(legacy.ddl,'');
    assert.equal(legacy.start,'2026-09-01');assert.equal(legacy.end,'2026-09-20');
    await open('旧任务兼容');await description.fill('统一后的任务描述');await save();
    legacy=await task('旧任务兼容');assert.equal(legacy.description,'统一后的任务描述');assert.equal(legacy.checkpoint,'上次完成基线');
    checks.push('legacy lossless editing; explicit empty deadline; no inferred completion');

    // Real browser clipboard Ctrl+V uploads a PNG into the current project only.
    await open('完整新任务');await description.focus();
    await page.evaluate(async png=>{const bytes=Uint8Array.from(atob(png),c=>c.charCodeAt(0));await navigator.clipboard.write([new ClipboardItem({'image/png':new Blob([bytes],{type:'image/png'})})]);},cfg.png);
    await page.keyboard.press('Control+End');await page.keyboard.press('Control+V');
    await page.waitForFunction(()=>document.querySelector('#progress-description').value.includes('![](../assets/'));
    const image=page.locator('#progress-preview img');await image.waitFor();
    await page.waitForFunction(()=>[...document.querySelectorAll('#progress-preview img')].every(img=>img.complete&&img.naturalWidth>0));
    const imageUrl=await image.first().getAttribute('src');assert.ok(imageUrl.startsWith(`/project/${cfg.projectId}/asset/records/assets/`));
    await screenshot('progress-description-image-light.png');await save();
    const imageTask=await task('完整新任务');assert.match(imageTask.description,/!\[\]\(\.\.\/assets\/[a-f0-9]{64}\.png\)/);
    const otherImage=await context.request.get(cfg.origin+imageUrl.replace(`/project/${cfg.projectId}/`,`/project/${cfg.otherProjectId}/`));assert.equal(otherImage.status(),404);
    await page.reload();await row('完整新任务').waitFor();await open('完整新任务');await image.waitFor();await page.waitForFunction(()=>document.querySelector('#progress-preview img')?.naturalWidth>0);await close();
    checks.push('native Ctrl+V screenshot; rendered image after reload; project-isolated upload');

    // An upload failure retains the local screenshot; retry succeeds without losing concurrent typing.
    await open('完整新任务');
    const uploadRoute='**/api/project/*/notebook/upload';
    await page.route(uploadRoute,route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({ok:false,error:'临时上传失败'})}));
    await description.focus();await page.keyboard.press('Control+V');await page.locator('#progress-uploads').getByRole('button',{name:'重试',exact:true}).waitFor();
    await form.getByRole('button',{name:'保存',exact:true}).click();assert.equal(await page.locator('#progress-dialog').evaluate(n=>n.open),true);
    assert.match(await page.locator('#progress-error').innerText(),/重试或移除/);
    await description.fill((await description.inputValue())+'\n上传期间输入保留');
    await page.unroute(uploadRoute);await page.locator('#progress-uploads').getByRole('button',{name:'重试',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#progress-uploads').children.length);assert.match(await description.inputValue(),/上传期间输入保留/);await save();
    checks.push('failed screenshot retry; block incomplete save; retain draft text');

    // Completion and restoration are explicit. Blocked legacy status is restored, not overwritten.
    await row('旧任务兼容').getByRole('button',{name:'完成 旧任务兼容',exact:true}).click();await until(async()=> (await task('旧任务兼容')).status==='done');
    assert.ok((await task('旧任务兼容')).completed_at);await page.locator('#progress-done-tab').click();
    await page.locator('#progress-done').getByRole('button',{name:'恢复 旧任务兼容',exact:true}).click();await until(async()=> (await task('旧任务兼容')).status==='blocked');
    assert.equal((await task('旧任务兼容')).completed_at,null);await page.locator('#progress-todo-tab').click();
    checks.push('Todo/Done; explicit completion; original status restored');

    // Another tab changes description while current tab edits title: no silent overwrite.
    await open('完整新任务');await form.locator('[name=title]').fill('保留我的标题');
    let current=await data();const concurrent=current.tasks.find(t=>t.title==='完整新任务');
    let response=await context.request.post(api,{headers:{'X-Notebook-Request':'1','Origin':cfg.origin},data:{action:'update',revision:current.revision,id:concurrent.id,task:{description:'另一页面的新描述'}}});assert.equal(response.status(),200);
    await form.getByRole('button',{name:'保存',exact:true}).click();await page.locator('#progress-reload').waitFor();
    assert.equal(await form.locator('[name=title]').inputValue(),'保留我的标题');
    await page.locator('#progress-reload').click();await until(async()=>!(await form.getByRole('button',{name:'保存',exact:true}).isDisabled()));
    assert.equal(await description.inputValue(),'另一页面的新描述');await save();assert.equal((await task('保留我的标题')).description,'另一页面的新描述');
    // Same-field conflict preserves local text for explicit confirmation.
    await open('保留我的标题');await description.fill('本地描述需要保留');current=await data();
    response=await context.request.post(api,{headers:{'X-Notebook-Request':'1','Origin':cfg.origin},data:{action:'update',revision:current.revision,id:concurrent.id,task:{description:'服务器描述也变了'}}});assert.equal(response.status(),200);
    await form.getByRole('button',{name:'保存',exact:true}).click();await page.locator('#progress-reload').waitFor();await page.locator('#progress-reload').click();
    await page.waitForFunction(()=>document.querySelector('#progress-error').textContent.includes('双方修改'));
    assert.equal(await description.inputValue(),'本地描述需要保留');await save();
    checks.push('409 conflict; field merge; same-field draft preservation');

    // Polling and soft navigation cannot discard an unsaved task description.
    await open('保留我的标题');await description.fill('不可丢失的未保存草稿');
    const blocked=await page.evaluate(()=>{const e=new CustomEvent('workbench:before-leave',{cancelable:true,detail:{root:document.querySelector('#main-content')}});document.dispatchEvent(e);return e.defaultPrevented;});assert.equal(blocked,true);
    await page.waitForTimeout(5200);assert.equal(await description.inputValue(),'不可丢失的未保存草稿');await close();
    await open('保留我的标题');assert.equal(await description.inputValue(),'本地描述需要保留');await close();
    checks.push('unsaved navigation guard; polling preserves draft; cancel discards only local changes');

    // Delete is explicit, project-bound, and does not remove unrelated tasks.
    await open('保留我的标题');await page.locator('#progress-delete').click();await page.waitForFunction(()=>!document.querySelector('#progress-dialog').open);
    assert.equal(await task('保留我的标题'),undefined);assert.ok(await task('旧任务兼容'));
    await page.locator('#progress-new').click();await form.locator('[name=title]').fill('<img src=x onerror=alert(1)>');await description.fill('<script>alert(1)</script>');await save();
    assert.equal(await page.locator('#progress-todo .progress-task img').count(),0);
    await screenshot('progress-list-light.png');
    await page.emulateMedia({colorScheme:'dark'});await screenshot('progress-list-dark.png');
    await page.setViewportSize({width:480,height:900});await screenshot('progress-mobile.png');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);
    await page.locator('#progress-new').click();await screenshot('progress-dialog-mobile.png');await close();
    assert.ok(uploads.length>=3);assert.ok(uploads.every(url=>url===`${cfg.origin}/api/project/${cfg.projectId}/notebook/upload`));assert.deepEqual(errors,[]);
    checks.push('explicit delete; safe text rendering; light/dark/mobile layout; no page errors');
    console.log(JSON.stringify({ok:true,checks,uploads:uploads.length},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
