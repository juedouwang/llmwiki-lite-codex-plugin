/* Real temporary repositories/settings only; no user registry or installed plugin. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
(async()=>{
  const [origin,pid]=process.argv.slice(2);
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  try{
    const context=await browser.newContext({viewport:{width:1440,height:960}});
    const page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const base=`/project/${pid}`;
    await page.goto(origin+base+'/code');
    await page.locator('.code-commit').filter({hasText:'修正标定参数读取'}).click();
    await page.locator('#code-expand-diff').waitFor();
    for(const selector of ['[data-workbench-nav="code"] svg','#code-branches summary svg','#code-merge svg','#code-create-from svg']){
      assert.equal(await page.locator(selector+' circle').count(),3,selector+' has all three nodes');
    }
    assert.ok((await page.locator('[data-workbench-nav="code"] svg path').getAttribute('d')).includes('M9 20a9 9 0 0 0 9-9'),'branch curves from the bottom circle to the right node');
    await page.locator('#code-commit-metadata summary').click();
    const response=await page.request.get(origin+`/api/project/${pid}/code/commit/`+(await page.locator('#code-commit-oid').innerText()));
    const commit=await response.json();
    assert.equal(await page.locator('#code-commit-repository').innerText(),commit.repository_path);
    assert.ok(path.isAbsolute(commit.repository_path));
    await page.locator('#code-commit-metadata summary').click();
    const before=await page.locator('.code-commit-diff .code-diff').innerText();
    const selected=await page.locator('.code-version-title').innerText();
    await page.locator('#code-expand-diff').click();
    assert.equal(await page.locator('#code-dialog[open]').count(),1);
    const large=await page.locator('#code-dialog').boundingBox(),small=await page.locator('#code-detail').boundingBox();
    assert.ok(large.width>small.width*2,'enlarged diff is substantially wider');
    assert.ok((await page.locator('.code-expanded-diff .code-diff').innerText()).includes('exposure_ms: 8.0'));
    assert.equal(await page.locator('#code-dialog-actions button').count(),1,'read-only dialog has no Git write action');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#code-dialog[open]').count(),0);
    assert.equal(await page.locator('.code-commit-diff .code-diff').innerText(),before);
    assert.equal(await page.locator('.code-version-title').innerText(),selected);
    assert.equal(await page.locator('#code-expand-diff').evaluate(e=>e===document.activeElement),true);
    // Isolate the layout invariant: a tall history must not push actions to its bottom.
    await page.locator('.code-history').evaluate(e=>e.style.minHeight='2800px');
    const placement=await page.evaluate(()=>{
      const d=document.querySelector('.code-commit-diff').getBoundingClientRect();
      const a=document.querySelector('.code-detail-actions').getBoundingClientRect();
      return {gap:a.top-d.bottom,bottom:a.bottom,historyBottom:document.querySelector('.code-history').getBoundingClientRect().bottom};
    });
    assert.ok(placement.gap>=0 && placement.gap<=26,JSON.stringify(placement));
    assert.ok(placement.bottom<placement.historyBottom-1000,JSON.stringify(placement));
    await page.locator('.code-history').evaluate(e=>e.style.minHeight='');
    const evidence=path.join(os.tmpdir(),'llmwiki-workbench-refinements');fs.mkdirSync(evidence,{recursive:true});
    await page.mouse.move(0,0);await page.screenshot({path:path.join(evidence,'code-detail.png'),fullPage:true});
    await page.locator('#code-expand-diff').click();
    await page.screenshot({path:path.join(evidence,'diff-expanded.png')});
    await page.locator('#code-dialog-close').click();
    console.log('PASS three-node reference icons / real local path / read-only enlarge + Escape + focus / actions follow diff under tall history');

    await page.locator('.console-sidebar-brand').click();
    await page.waitForURL(origin+'/projects');
    await page.locator('.project-drag-handle').first().waitFor();
    const ids=()=>page.locator('.project-row').evaluateAll(rows=>rows.map(e=>e.dataset.projectId));
    const original=await ids();assert.equal(original.length,2);
    const currentAgent=(await (await page.request.get(origin+'/api/projects/preferences')).json()).current_project_id;
    let documents=0;page.on('request',r=>{if(r.isNavigationRequest()&&r.frame()===page.mainFrame())documents++;});
    const last=page.locator('.project-drag-handle').last(),firstRow=page.locator('.project-row').first();
    const start=await last.boundingBox(),end=await firstRow.boundingBox();
    await page.mouse.move(start.x+start.width/2,start.y+start.height/2);await page.mouse.down();
    await page.mouse.move(end.x+20,end.y+2,{steps:12});await page.mouse.up();
    await page.waitForFunction(()=>document.querySelector('#project-preferences-status')?.textContent==='已保存');
    assert.deepEqual(await ids(),[...original].reverse());
    assert.equal(documents,0,'drag never navigates into a project');
    const reordered=await ids();
    await page.reload();assert.deepEqual(await ids(),reordered);
    await page.locator('.project-drag-handle').first().focus();await page.keyboard.press('ArrowDown');
    await page.waitForFunction(()=>document.querySelector('#project-preferences-status')?.textContent==='已保存');
    assert.deepEqual(await ids(),original);
    console.log('PASS pointer folder drag / keyboard order / persisted reload / no accidental project opening');

    const wanted=original[1];
    await page.locator(`.project-row[data-project-id="${wanted}"] .project-default`).click();
    await page.waitForFunction(id=>document.querySelector('#project-manager')?.dataset.defaultProject===id,wanted);
    await page.goto(origin+'/');await page.waitForURL(origin+`/project/${wanted}/todos`);
    await page.locator('.console-sidebar-brand').click();await page.waitForURL(origin+'/projects');
    assert.equal(await page.locator(`.project-row[data-project-id="${wanted}"] .project-default`).getAttribute('aria-pressed'),'true');
    const fresh=await context.newPage();await fresh.goto(origin+'/');
    assert.equal(new URL(fresh.url()).pathname,`/project/${wanted}/todos`);await fresh.close();
    await page.locator(`.project-row[data-project-id="${wanted}"] .project-default`).click();
    await page.waitForFunction(()=>document.querySelector('#project-manager')?.dataset.defaultProject==='');
    await page.goto(origin+'/');assert.equal(new URL(page.url()).pathname,`/project/${original[0]}/todos`);
    assert.equal((await (await page.request.get(origin+'/api/projects/preferences')).json()).current_project_id,currentAgent);
    console.log('PASS explicit startup default / new browser page / brand overview / cleared default uses first / agent selection unchanged');

    await page.goto(origin+base+'/code');await page.locator('.code-version-title').waitFor();
    await page.evaluate(()=>window.savedCodeRoot=document.getElementById('code-app'));
    await page.locator('.console-sidebar-brand').click();await page.waitForURL(origin+'/projects');
    await page.locator('.project-drag-handle').last().focus();await page.keyboard.press('ArrowUp');
    await page.waitForFunction(()=>document.querySelector('#project-preferences-status')?.textContent==='已保存');
    const expected=await ids();
    await page.locator('.console-project-switcher summary').click();
    await page.locator(`.console-project-menu a[data-project-id="${pid}"]`).click();
    await page.waitForURL(origin+base+'/todos');
    await page.locator('[data-workbench-nav="code"]').click();await page.waitForURL(origin+base+'/code');
    assert.equal(await page.evaluate(()=>document.getElementById('code-app')===window.savedCodeRoot),true);
    assert.deepEqual(await page.locator('.console-project-menu a[data-project-id]').evaluateAll(links=>links.map(e=>e.dataset.projectId)),expected);
    await page.locator('.console-sidebar-brand').click();await page.waitForURL(origin+'/projects');
    const beforeFailure=await ids();
    await page.route('**/api/projects/preferences',route=>route.request().method()==='POST'?route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({ok:false,error:'模拟保存失败'})}):route.continue());
    await page.locator('.project-drag-handle').last().focus();await page.keyboard.press('ArrowUp');
    await page.waitForFunction(()=>document.querySelector('#project-preferences-status')?.textContent.startsWith('未保存：'));
    assert.deepEqual(await ids(),beforeFailure,'failed save restores the original order');
    await page.unroute('**/api/projects/preferences');
    console.log('PASS retained Git DOM and sidebar order / failed preferences save visibly rolls back');
    await page.locator('.project-default').first().click();
    await page.waitForFunction(()=>document.querySelector('#project-preferences-status')?.textContent==='已保存');
    await page.evaluate(()=>document.activeElement?.blur());
    await page.mouse.move(0,0);await page.screenshot({path:path.join(evidence,'project-order.png'),fullPage:true});
    for(const width of [320,580,1440]){
      await page.setViewportSize({width,height:960});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'projects do not overflow '+width);
    }
    await page.goto(origin+base+'/code');await page.locator('#code-expand-diff').waitFor();
    for(const width of [320,580,1440]){
      await page.setViewportSize({width,height:960});
      await page.locator('#code-expand-diff').click();
      const box=await page.locator('#code-dialog').boundingBox();
      assert.ok(box.x>=0 && box.x+box.width<=width+1,'expanded diff stays in viewport '+width);
      await page.keyboard.press('Escape');
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify({placement,errors,checks:5},null,2));
    console.log('PASS mobile/desktop project list and expanded diff / no page errors. Evidence: '+evidence);
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
