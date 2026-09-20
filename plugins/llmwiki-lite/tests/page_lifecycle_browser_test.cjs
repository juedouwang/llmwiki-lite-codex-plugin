/* Cache contract only: real isolated pages, synthetic detach/reattach, no shell dependency. */
const {chromium} = require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
(async () => {
  const cfg = JSON.parse(process.argv[2]);
  const browser = await chromium.launch({headless:true,channel:process.env.LLMWIKI_BROWSER_CHANNEL || 'chrome'});
  const context = await browser.newContext(), errors=[];
  // The shell is tested separately. These scripts must honour its event contract alone.
  await context.route('**/static/workbench-navigation.js', route => route.fulfill({contentType:'text/javascript',body:''}));
  await context.addInitScript(() => {
    window.lifecycleIntervals = new Set();
    const start=window.setInterval, stop=window.clearInterval;
    window.setInterval=(...args)=>{const id=start(...args);lifecycleIntervals.add(id);return id;};
    window.clearInterval=id=>{lifecycleIntervals.delete(id);stop(id);};
  });
  const open = async path => {
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.goto(cfg.origin+path);return page;
  };
  const allowed = page => page.evaluate(()=>document.dispatchEvent(new CustomEvent('workbench:before-leave',{cancelable:true,detail:{root:document.getElementById('main-content')}})));
  const detach = page => page.evaluate(()=>{
    window.cachedMain=document.getElementById('main-content');
    document.dispatchEvent(new CustomEvent('workbench:leave',{detail:{root:cachedMain}}));
    const next=document.createElement('main');next.id='main-content';next.innerHTML='<p id="nb-save-state">other page</p>';
    cachedMain.replaceWith(next);
  });
  const enter = page => page.evaluate(()=>{
    document.getElementById('main-content').replaceWith(cachedMain);
    document.dispatchEvent(new CustomEvent('workbench:enter',{detail:{root:cachedMain,restored:true}}));
  });
  const dispose = page => page.evaluate(()=>document.dispatchEvent(new CustomEvent('workbench:dispose',{detail:{root:document.getElementById('main-content')}})));
  const intervalCount = page => page.evaluate(()=>lifecycleIntervals.size);
  try {
    const notebook=await open(`/project/${cfg.pid}/records`);
    await notebook.locator('a[href$="/notebook"]').first().click();
    await notebook.locator('#nb-source').waitFor({state:'visible'});
    await notebook.waitForFunction(()=>!document.querySelector('#nb-source').readOnly);
    let release, requested;
    const received=new Promise(resolve=>requested=resolve), held=new Promise(resolve=>release=resolve);
    await notebook.route('**/api/project/*/notebook/*',async route=>{
      if(route.request().method()!=='POST')return route.continue();
      requested();await held;await route.continue();
    });
    await notebook.locator('#nb-source').fill('页面切换中的未保存正文');
    assert.equal(await allowed(notebook),false,'dirty notebook must synchronously veto navigation');
    await received;
    await detach(notebook);
    await notebook.evaluate(()=>history.replaceState({navigation:'sentinel'},'', '/reports'));
    release();
    await notebook.waitForFunction(()=>cachedMain.querySelector('#nb-save-state').textContent==='已保存');
    assert.equal(new URL(notebook.url()).pathname,'/reports','late notebook save must not rewrite another page URL');
    assert.equal(await notebook.locator('#nb-save-state').innerText(),'other page','late save must stay scoped to its own main');
    assert.equal(await notebook.evaluate(()=>{
      const data=new DataTransfer();data.items.add(new File(['x'],'test.png',{type:'image/png'}));
      const event=new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true});document.dispatchEvent(event);return event.defaultPrevented;
    }),false,'detached editor must not swallow another page drop');
    await enter(notebook);
    assert.equal(await notebook.locator('#nb-source').inputValue(),'页面切换中的未保存正文');
    await notebook.locator('#nb-comment').click();
    await notebook.getByLabel('批注内容').fill('尚未提交的批注');
    assert.equal(await allowed(notebook),false,'comment dialog draft must veto navigation');
    await notebook.locator('#nb-dialog-close').click();
    await dispose(notebook);await notebook.close();

    const progress=await open(`/project/${cfg.pid}/todos`);
    await progress.waitForFunction(()=>lifecycleIntervals.size>0);
    await progress.locator('#progress-new').click();
    await progress.locator('#progress-add input').fill('尚未添加的任务');
    assert.equal(await allowed(progress),false);
    await progress.locator('#progress-add input').fill('');
    assert.equal(await allowed(progress),true);
    await detach(progress);assert.equal(await intervalCount(progress),0,'leave stops progress polling');
    await progress.evaluate(()=>{window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));});
    assert.equal(await intervalCount(progress),0,'inactive global events cannot restart polling');
    await enter(progress);assert.equal(await intervalCount(progress),1);
    await enter(progress);assert.equal(await intervalCount(progress),1,'enter must not duplicate intervals');
    await dispose(progress);assert.equal(await intervalCount(progress),0);await progress.close();

    const report=await open('/reports/daily/2026-09-19');
    await report.waitForFunction(()=>!document.querySelector('#report-edit').disabled);
    assert.equal(await intervalCount(report),1);
    await detach(report);assert.equal(await intervalCount(report),0);
    await enter(report);assert.equal(await intervalCount(report),1);
    await dispose(report);assert.equal(await intervalCount(report),0);await report.close();

    const list=await open(`/reports?context=${cfg.pid}`);
    await list.locator('#report-new').click();assert.equal(await allowed(list),false);
    await list.evaluate(()=>document.querySelector('#report-create').close());assert.equal(await allowed(list),true);
    await dispose(list);await list.close();

    const literature=await open(`/project/${cfg.pid}/literature`);
    await literature.locator('[data-add]').click();
    await literature.locator('[data-literature-form="add"] [name=locator]').fill('https://example.test/paper');
    assert.equal(await allowed(literature),false);
    await literature.locator('[data-literature-form="add"] [data-cancel]').click();assert.equal(await allowed(literature),true);
    await detach(literature);await enter(literature);await dispose(literature);await literature.close();

    const knowledge=await open(`/project/${cfg.pid}`);
    await knowledge.waitForFunction(()=>document.querySelector('#km-status')?.textContent.includes('最近检查'));
    let calls=0;knowledge.on('request',req=>{if(req.url().includes('/knowledge-maintenance'))calls++;});
    await detach(knowledge);const before=calls;
    await knowledge.evaluate(()=>{window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));});
    await knowledge.waitForTimeout(60);assert.equal(calls,before,'detached knowledge does not refresh on global events');
    await enter(knowledge);await knowledge.waitForTimeout(60);assert.ok(calls>before,'reattached knowledge refreshes');
    await dispose(knowledge);await knowledge.close();
    assert.deepEqual(errors,[]);
    console.log('PASS: 6 page lifecycle groups (notebook/editor, progress, report editor, report list, literature, knowledge)');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
