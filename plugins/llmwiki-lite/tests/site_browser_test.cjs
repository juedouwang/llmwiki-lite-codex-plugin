/* Whole-site regression in an isolated temporary project, never user data. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:960}});
    const page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const origin=process.argv[2],base=`/project/${process.argv[3]}`;
    async function go(route){const r=await page.goto(origin+route);assert.equal(r.status(),200,route);assert.equal(await page.locator('main').count(),1);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`overflow: ${route} ${JSON.stringify(await page.evaluate(()=>Array.from(document.querySelectorAll("body *")).filter(e=>e.getBoundingClientRect().right>innerWidth&&!e.closest("#progress-timeline")).map(e=>({tag:e.tagName,id:e.id,cls:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right}))))}`);}
    await go(base+'/todos');
    // Assert the invariant rather than a fixed total: adding a project page adds one.
    const navItems=await page.locator('.console-nav-item').count();
    assert.ok(navItems>=5,'project console navigation entries missing');
    assert.equal(await page.locator('.console-nav-icon svg').count(),navItems,'every console nav item must carry exactly one local icon');
    assert.equal(await page.locator('.console-nav-icon').allTextContents().then(items=>items.join('')),'');
    assert.equal(await page.locator('.console-nav-icon svg').first().getAttribute('aria-hidden'),'true');
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-icons-todos.png')});
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:960});
      for(const route of ['/',base,`${base}/records`,`${base}/todos`,`${base}/literature`,`${base}/literature/read/references/demo-paper.pdf`,`${base}/literature/compare/references/demo-paper.pdf`,`${base}/page/experiment.md`,'/search','/settings'])await go(route);
      await go(base+'/records');
      await page.locator('.timeline-card').first().click();
      assert.equal(await page.locator('.reader-contents[open]').count(),0);
      assert.ok(!(await page.locator('main').innerText()).includes('记录时间：'));
      await page.locator('.reader-contents>summary').click();
      assert.ok((await page.locator('main').innerText()).includes('记录时间：'));
      if(width===390){
        await page.getByRole('button',{name:'打开导航菜单'}).click();
        assert.equal(await page.getByRole('button',{name:'打开导航菜单'}).getAttribute('aria-expanded'),'true');
        await page.keyboard.press('Shift+Tab');
        assert.equal(await page.evaluate(()=>document.activeElement.textContent.trim()),'设置');
        await page.keyboard.press('Escape');
        assert.equal(await page.getByRole('button',{name:'打开导航菜单'}).getAttribute('aria-expanded'),'false');
        assert.equal(await page.locator('#console-sidebar').evaluate(el=>el.inert),true);
      }
    }
    await page.setViewportSize({width:1440,height:960});
    await go(base);await page.getByLabel('筛选知识页').fill('不会匹配');
    assert.equal(await page.locator('[data-page-title]:visible').count(),0);
    assert.equal(await page.locator('.filter-empty').isVisible(),true);
    await page.getByLabel('筛选知识页').fill('低纹理');assert.equal(await page.locator('[data-page-title]:visible').count(),1);
    await go('/search');await page.getByLabel('搜索关键词').fill('低纹理');await page.getByRole('button',{name:'搜索',exact:true}).click();
    assert.ok(await page.locator('.search-result').count());
    await go('/settings#register-project');assert.equal(await page.locator('#register-project').getAttribute('open'),'');
    assert.equal(await page.locator('#register-project .settings[open]').count(),0);
    await go(base+'/todos');
    await page.locator('#progress-import').click();
    await page.locator('#progress-candidates input').first().check();
    await page.locator('#progress-import-form .primary').click();
    await page.locator('#progress-import-dialog').waitFor({state:'hidden'});
    await page.locator('#progress-unscheduled .progress-task').first().click();
    await page.locator('#progress-form [name=status]').selectOption('active');
    await page.locator('#progress-form [name=start]').fill('2026-09-18');
    await page.locator('#progress-form [name=end]').fill('2026-09-21');
    await page.locator('#progress-form [name=checkpoint]').fill('昨晚完成基线，尚未跑夜间数据');
    await page.locator('#progress-form [name=next_step]').fill('检查日志，然后补夜间样本');
    const sourceHref=await page.locator('#progress-source a').getAttribute('href');
    assert.equal((await page.request.get(origin+sourceHref)).status(),200);
    await page.locator('#progress-form [type=submit]').click();
    await page.locator('#progress-dialog').waitFor({state:'hidden'});
    await page.reload();await page.locator('#progress-resume').waitFor();
    assert.match(await page.locator('#progress-resume').innerText(),/昨晚完成基线/);
    await page.locator('#progress-add input').fill('<img src=x onerror=alert(1)>未排期');await page.locator('#progress-add input').press('Enter');
    await page.locator('#progress-unscheduled .progress-task').waitFor();
    assert.equal(await page.locator('#research-progress img').count(),0);
    await page.locator('#progress-unscheduled .progress-task').click();
    assert.equal(await page.locator('#progress-form [name=start]').inputValue(),'');
    const other=await page.context().newPage();await other.goto(origin+base+'/todos');await other.locator('#progress-unscheduled .progress-task').click();
    await other.locator('#progress-form [name=checkpoint]').fill('另一页面的进度');await other.locator('#progress-form [type=submit]').click();await other.locator('#progress-dialog').waitFor({state:'hidden'});
    await page.locator('#progress-form [name=next_step]').fill('本页的下一步');await page.locator('#progress-form [type=submit]').click();await page.locator('#progress-reload').waitFor();
    assert.equal(await page.locator('#progress-form [name=next_step]').inputValue(),'本页的下一步');
    await page.locator('#progress-reload').click();await page.waitForFunction(()=>document.querySelector('#progress-form [name=checkpoint]').value==='另一页面的进度');
    await page.locator('#progress-form [type=submit]').click();await page.locator('#progress-dialog').waitFor({state:'hidden'});await other.close();
    await page.locator('#progress-unscheduled .progress-inline-status').selectOption('done');
    await page.locator('#progress-done').waitFor();await page.locator('#progress-done summary').click();
    assert.equal(await page.locator('#progress-done .progress-task').count(),1);
    await page.locator('#progress-done .progress-inline-status').selectOption('planned');await page.locator('#progress-unscheduled .progress-task').waitFor();
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-progress-desktop.png')});
    await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('#progress-resume').waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-progress-mobile.png')});await page.setViewportSize({width:1440,height:960});
    // /literature is the strict catalogue (M-04): a PDF sitting in the project must not
    // list itself, and entries are added deliberately. The file-scanning library it
    // replaces stays reachable at /literature/old and keeps its own coverage below.
    await go(base+'/literature');
    assert.equal(await page.locator('#literature-list').count(),1);
    assert.ok(!(await page.locator('#literature-list').innerText()).includes('demo-paper.pdf'),'unregistered project PDF leaked into the catalogue');
    assert.equal(await page.locator('#add-literature').count(),1);
    await go(base+'/literature/old');
    assert.equal(await page.locator('.paper-notes[open],.paper-prompt[open],#literature-flow[open]').count(),0);
    await page.getByLabel('阅读状态').selectOption('unread');assert.equal(await page.locator('[data-literature-card]:visible').count(),0);await page.getByLabel('阅读状态').selectOption('all');
    await page.getByLabel('文献类型').selectOption({index:1});assert.equal(await page.locator('[data-literature-card]:visible').count(),1);
    await page.getByRole('link',{name:'添加文献',exact:true}).click();await page.locator('#literature-flow[open]').waitFor();await page.locator('#literature-flow>summary').click();
    const fav=page.locator('.fav-button').first();await fav.click();assert.equal(await fav.locator('svg').count(),1);assert.equal(await fav.getAttribute('aria-pressed'),'true');await page.reload();assert.equal(await page.locator('.fav-button').first().getAttribute('aria-pressed'),'true');
    await page.locator('input[type=search]').fill('no-match');assert.equal(await page.locator('[data-literature-card]:visible').count(),0);
    assert.equal(await page.locator('#literature-filter-empty').isVisible(),true);await page.locator('input[type=search]').fill('');
    for(const [route,name] of [[base+'/literature','literature'],[base+'/records','records'],[base,'knowledge'],['/settings','settings']]){
      await go(route);await page.screenshot({path:path.join(os.tmpdir(),`llmwiki-minimal-${name}.png`)});
    }
    await page.setViewportSize({width:390,height:844});await go(base+'/records');await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-minimal-mobile.png')});
    await page.getByRole('button',{name:'打开导航菜单'}).click();await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-icons-mobile.png')});
    assert.deepEqual(errors,[]);
    console.log('Site browser checks passed: 10 routes desktop/mobile, record details, navigation keyboard, search, filtering, settings disclosures, task timeline, continuation context, legacy import, optimistic conflicts, favorites.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
