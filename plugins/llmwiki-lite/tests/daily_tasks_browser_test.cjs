/* Real UI/HTTP state; every project and task is a disposable fixture. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const cfg=JSON.parse(process.argv[2]);
 const browser=await chromium.launch({headless:true,channel:process.env.LLMWIKI_BROWSER_CHANNEL||'chrome'});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1050}});
  const page=await context.newPage(), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept());
  const daily=`/daily?date=${cfg.today}&context=${cfg.pid}`;
  const api=async()=>await (await context.request.get(cfg.origin+`/api/daily-tasks?date=${cfg.today}`)).json();
  await page.goto(cfg.origin+daily);
  await page.waitForFunction(()=>!document.querySelector('#daily-new').disabled);
  assert.deepEqual(await page.locator('.console-navigation .console-nav-item').allTextContents(),['每日待办','日报与周报','科研进度','科研记录','知识库','文献','代码']);
  assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).zoom),'1.25');
  assert.match(await page.locator('#daily-todo').innerText(),/准备样例/);
  assert.match(await page.locator('#daily-todo').innerText(),/跨项目任务/);
  assert.match(await page.locator('#daily-todo').innerText(),/待你验收/);
  assert.match(await page.locator('#daily-todo .daily-duration').innerText(),/60 分钟/);
  await page.screenshot({path:path.join(cfg.evidence,'daily-desktop.png'),fullPage:true});
  await page.locator('#daily-new').click();
  await page.locator('#daily-title').fill('临时联系设备');
  await page.locator('#daily-description').fill('确认明天可使用的相机。');
  await page.locator('#daily-estimated-minutes').fill('20');
  await page.locator('#daily-save').click();
  await page.waitForFunction(()=>document.querySelector('#daily-todo').textContent.includes('临时联系设备'));
  const created=(await api()).tasks.find(t=>t.title==='临时联系设备');
  assert.ok(created);
  assert.equal(created.project_id,'__workspace__');
  assert.equal(created.estimated_minutes,20);
  await page.getByRole('button',{name:'临时联系设备',exact:true}).click();
  await page.locator('#daily-title').fill('临时联系设备（已沟通）');
  assert.equal(await page.locator('#daily-estimated-minutes').inputValue(),'20');
  await page.locator('#daily-estimated-minutes').fill('');
  await page.locator('#daily-save').click();
  await page.waitForFunction(()=>document.querySelector('#daily-todo').textContent.includes('临时联系设备（已沟通）'));
  assert.equal((await api()).tasks.find(t=>t.id===created.id).title,'临时联系设备（已沟通）');
  assert.equal((await api()).tasks.find(t=>t.id===created.id).estimated_minutes,null);
  await page.getByRole('button',{name:'准备样例',exact:true}).click();
  assert.match(await page.locator('#daily-delivery-info').innerText(),/样例准备完毕/);
  const recordLink = await page.locator('#daily-delivery-info a').first().getAttribute('href');
  assert.match(recordLink, /\/records\//);
  const recordResponse = await page.request.get(cfg.origin + recordLink);
  assert.equal(recordResponse.status(), 200);
  assert.match(await recordResponse.text(), /样例准备完毕/);
  await page.locator('#daily-accept').click();
  await page.waitForFunction(()=>document.querySelector('#daily-completed').textContent.includes('准备样例'));
  const done=(await api()).completed.find(t=>t.id===cfg.child);
  assert.equal(done.review_state,'accepted');
  assert.ok(done.completion_record_id);
  // Deletion affects the single workspace item, not any project or receipt.
  await page.getByRole('button',{name:'临时联系设备（已沟通）',exact:true}).click();
  await page.locator('#daily-delete').click();
  await page.waitForFunction(()=>!document.querySelector('#daily-todo').textContent.includes('临时联系设备（已沟通）'));
  assert.ok(!(await api()).tasks.some(t=>t.id===created.id));
  await page.locator('#daily-overdue summary').click();
  await page.getByRole('button',{name:'安排到这天',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#daily-todo').textContent.includes('昨日未完成'));
  await page.locator('#daily-date').fill(cfg.tomorrow);
  await page.waitForFunction(()=>document.querySelector('#daily-todo').textContent.includes('运行对比'));
  await page.goto(cfg.origin+`/project/${cfg.pid}/todos?task=${cfg.parent}`);
  await page.waitForFunction(()=>document.querySelector('#progress-dialog').open);
  assert.match(await page.locator('#progress-dialog').innerText(),/准备样例/);
  await page.locator('#progress-close').click();
  await page.goto(cfg.origin+`/project/${cfg.pid}/todos?task=${cfg.child}`);
  await page.waitForFunction(()=>document.querySelector('#progress-dialog').open);
  assert.equal(await page.locator('#progress-form [name=title]').inputValue(),'准备样例');
  assert.match(await page.locator('#progress-task-metadata').innerText(),/样例准备完毕/);
  assert.equal(await page.locator('#progress-task-metadata a[href*="records"]').count(),2);
  await page.locator('#progress-close').click();
  await page.locator('[data-workbench-nav=daily]').click();
  await page.waitForSelector('#daily-tasks');
  await page.waitForFunction(()=>!document.querySelector('#daily-new').disabled);
  // Local navigation should preserve the existing columns and their route identities.
  for(const [key,selector] of [['records','.records-surface'],['reports','#research-reports-list'],['todos','#research-progress']]) {
    await page.locator(`[data-workbench-nav=${key}]`).click();
    if(key==='records') await page.waitForFunction(()=>document.body.dataset.workbenchPage==='records');
    else await page.waitForSelector(selector);
  }
  await page.locator('[data-workbench-nav=daily]').click();
  await page.waitForSelector('#daily-tasks');
  // Keep unsaved input when a user rejects navigation.
  await page.locator('#daily-new').click();
  await page.locator('#daily-title').fill('尚未保存');
  page.removeAllListeners('dialog');page.on('dialog',d=>d.dismiss());
  await page.locator('[data-workbench-nav=reports]').click();
  assert.equal(await page.locator('#daily-title').inputValue(),'尚未保存');
  page.removeAllListeners('dialog');page.on('dialog',d=>d.accept());
  await page.locator('#daily-cancel').click();
  for(const width of [736,320]) {
    await page.setViewportSize({width,height:1050});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`overflow at ${width}`);
    await page.screenshot({path:path.join(cfg.evidence,`daily-${width}.png`),fullPage:true});
  }
  await page.emulateMedia({colorScheme:'dark'});
  await page.evaluate(()=>{localStorage.setItem('workbench.theme','system');document.documentElement.dataset.theme='system';});
  await page.setViewportSize({width:1440,height:1050});
  await page.screenshot({path:path.join(cfg.evidence,'daily-dark.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('Daily browser: shared IDs, pending/accept, temporary task CRUD, reschedule, date, parent drilldown, SPA, unsaved guard, responsive PASS; evidence '+cfg.evidence);
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
