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
    const origin=process.argv[2],pid=process.argv[3],base=`/project/${pid}`;
    async function go(route){const r=await page.goto(origin+route);assert.equal(r.status(),200,route);assert.equal(await page.locator('main').count(),1);assert.equal(await page.locator('.console-sidebar-brand').innerText(),'野人工作台');assert.ok((await page.title()).endsWith(' · 野人工作台'));assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).zoom),'1.25');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`overflow: ${route} ${JSON.stringify(await page.evaluate(()=>Array.from(document.querySelectorAll("body *")).filter(e=>e.getBoundingClientRect().right>innerWidth&&!e.closest("#progress-timeline")).map(e=>({tag:e.tagName,id:e.id,cls:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right}))))}`);}
    await go(base+'/todos');
    assert.ok(Math.abs((await page.locator('.console-sidebar').boundingBox()).width-207.5)<1,'166px prototype rail renders at 125%');
    assert.equal(await page.locator('.console-project-label small').innerText(),'当前项目');
    await page.waitForFunction(()=>document.querySelectorAll('.progress-day').length===7);
    // The approved workbench has exactly six project sections; tools stay in the footer.
    const navItems=await page.locator('.console-nav-item').count();
    assert.deepEqual(await page.locator('.console-navigation .console-nav-item').allTextContents(), ['科研进度','科研记录','日报与周报','知识库','文献','代码']);
    assert.equal(await page.locator('.console-navigation a[href="'+base+'/code"] svg circle').count(),2,'Git icon matches approved two-endpoint prototype');
    for (const [route, active] of [[base+'/todos','科研进度'],[base+'/records','科研记录'],['/reports','日报与周报'],[base,'知识库'],[base+'/literature','文献'],[base+'/code','代码']]) {
      await go(route);
      assert.equal(await page.locator('.console-navigation a[aria-current=page]').innerText(),active);
    }
    await go(base+'/records');
    const links = await page.locator('.console-project-menu a[href^="/project/"]').evaluateAll(items => items.map(a => a.getAttribute('href')));
    assert.ok(links.length>=2 && links.every(href=>href.endsWith('/records')),'switching clears details and retains the section');
    await page.getByLabel('切换研究项目').click();
    const targetProject=page.locator('.console-project-menu a:not(.is-current)[href^="/project/"]').first();
    const targetHref=await targetProject.getAttribute('href');
    const targetName=(await targetProject.innerText()).trim();
    await Promise.all([page.waitForURL(origin+targetHref),targetProject.click()]);
    assert.ok(new URL(page.url()).pathname.endsWith('/records') && !new URL(page.url()).pathname.startsWith(base+'/'));
    const targetBase=targetHref.replace(/\/records$/, '');
    await page.waitForFunction(expected=>document.body.dataset.workbenchProject===expected,targetBase.split('/').pop());
    assert.equal(await page.locator('.console-project-label b').innerText(),targetName);
    assert.deepEqual(await page.locator('.console-navigation .console-nav-item').evaluateAll(items=>items.map(a=>a.getAttribute('href'))),
      [targetBase+'/todos',targetBase+'/records','/reports?context='+targetBase.split('/').pop(),targetBase,targetBase+'/literature',targetBase+'/code']);
    await go(base+'/todos');
    // Approved prototype footer: local avatar/label and icon-only accessible settings;
    // project overview remains on the brand, not a second footer navigation row.
    assert.equal(await page.locator('.console-sidebar-brand').getAttribute('href'),'/projects?context='+pid);
    assert.equal(await page.locator('.console-sidebar-footer .console-avatar').innerText(),'野');
    assert.equal(await page.locator('.console-sidebar-footer .console-profile-label').innerText(),'本地');
    assert.equal(await page.locator('.console-sidebar-footer a').count(),1);
    assert.equal(await page.locator('.console-sidebar-footer').getByRole('link',{name:'设置',exact:true}).getAttribute('href'),'/settings?context='+pid);
    assert.equal(await page.locator('.console-nav-icon svg').count(),navItems,'every console nav item must carry exactly one local icon');
    assert.equal(await page.locator('.console-nav-icon').allTextContents().then(items=>items.join('')),'');
    assert.equal(await page.locator('.console-nav-icon svg').first().getAttribute('aria-hidden'),'true');
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-icons-todos.png')});
    for(const width of [1440,1280,1024,736,580,390,375,320]){
      await page.setViewportSize({width,height:960});
      for(const route of ['/projects',base,`${base}/records`,`${base}/todos`,`${base}/literature`,`${base}/literature/read/references/demo-paper.pdf`,`${base}/literature/compare/references/demo-paper.pdf`,`${base}/page/experiment.md`,'/search','/settings'])await go(route);
      await go(base+'/records');
      const recordLink=page.locator('.timeline-card').first();
      const recordHref=await recordLink.getAttribute('href');
      await Promise.all([page.waitForURL(origin+recordHref),recordLink.click()]);
      await page.locator('.reader-contents').waitFor();
      assert.equal(await page.locator('.reader-contents[open]').count(),0);
      assert.ok(!(await page.locator('main').innerText()).includes('记录时间：'));
      await page.locator('.reader-contents>summary').click();
      assert.ok((await page.locator('main').innerText()).includes('记录时间：'));
      if(width<=775){
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
    await go(base);await page.locator('.knowledge-search > summary').click();await page.getByLabel('筛选知识页').fill('不会匹配');
    assert.equal(await page.locator('[data-page-title]:visible').count(),0);
    assert.equal(await page.locator('.filter-empty').isVisible(),true);
    await page.getByLabel('筛选知识页').fill('低纹理');assert.equal(await page.locator('[data-page-title]:visible').count(),1);
    await go('/search');await page.getByLabel('搜索关键词').fill('低纹理');await page.getByRole('button',{name:'搜索',exact:true}).click();
    assert.ok(await page.locator('.search-result').count());
    await go('/settings#register-project');assert.equal(await page.locator('#register-project').getAttribute('open'),'');
    assert.equal(await page.locator('#register-project .settings[open]').count(),0);
    await go(base+'/todos');
    await page.locator('#progress-import').click();
    await page.locator('#progress-candidates input').first().waitFor();
    for (const width of [1024,580,320]) {
      await page.setViewportSize({width,height:640});
      const box=await page.locator('#progress-import-dialog').boundingBox();
      assert.ok(box.x>=-1 && box.x+box.width<=width+1 && box.y>=-1 && box.y+box.height<=641,'scaled task dialog stays within viewport');
    }
    await page.setViewportSize({width:1440,height:960});
    await page.locator('#progress-candidates input').first().check();
    await page.locator('#progress-import-form .primary').click();
    await page.locator('#progress-import-dialog').waitFor({state:'hidden'});
    // Unified task description, optional single DDL and priority: never pick an in-progress state.
    const progressAPI=origin+base.replace('/project/','/api/project/')+'/progress';
    async function progressSummary(){const response=await page.request.get(progressAPI+'?view=summary');assert.equal(response.status(),200);return response.json();}
    async function saveProgress(tab=page){await tab.locator('#progress-form [type=submit]').click();await tab.locator('#progress-dialog').waitFor({state:'hidden'});}
    // fill() works for the description textarea and the shared editor's editable surface.
    const descriptionValue=tab=>tab.locator('#progress-description').evaluate(el=>el.isContentEditable?el.innerText:el.value);
    const imported=(await progressSummary()).tasks[0];
    await page.locator('#progress-todo .progress-task').first().click();
    assert.equal(await page.locator('#progress-form [name=checkpoint], #progress-form [name=next_step], #progress-form [name=start], #progress-form [name=end], #progress-form [name=status]').count(),0);
    assert.equal(await page.locator('#progress-description:visible').count(),1);
    assert.equal(await page.locator('#progress-form input[type=date]').count(),1);
    assert.equal(await page.locator('#progress-form [name=ddl]').evaluate(el=>el.required),false);
    await page.locator('#progress-form [name=priority]').selectOption('high');
    await page.locator('#progress-form [data-ddl="0"]').click();
    const taskDDL=await page.locator('#progress-form [name=ddl]').inputValue();
    assert.match(taskDDL,/^\d{4}-\d{2}-\d{2}$/);
    const taskDescription='昨晚完成基线，尚未跑夜间数据\n\n下一步：**检查日志**，然后补夜间样本';
    await page.locator('#progress-description').fill(taskDescription);
    await page.locator('#progress-preview strong').filter({hasText:'检查日志'}).waitFor();
    await page.locator('#progress-legacy summary').click();
    assert.match(await page.locator('#progress-legacy pre').innerText(),/checkpoint[\s\S]*next_step/);
    assert.equal(await page.locator('#progress-legacy input, #progress-legacy textarea, #progress-legacy [contenteditable=true]').count(),0);
    const sourceHref=await page.locator('#progress-source a').getAttribute('href');
    assert.equal((await page.request.get(origin+sourceHref)).status(),200);
    await saveProgress();
    const edited=(await progressSummary()).tasks.find(task=>task.id===imported.id);
    assert.equal(edited.description,taskDescription);assert.equal(edited.priority,'high');assert.equal(edited.ddl,taskDDL);
    for(const key of ['status','checkpoint','next_step','start','end','record_id','assistant_context'])assert.deepEqual(edited[key],imported[key],key+' must survive unified editing');
    assert.equal(edited.completed_at,null,'editing a deadline or description must not complete a task');
    // Inspect server HTML, not only hydrated DOM: no first-paint checkpoint/next-step double column.
    const progressHTML=await (await page.request.get(origin+base+'/todos')).text();
    const resumeHTML=progressHTML.match(/<section id="progress-resume"[\s\S]*?<\/section>/)?.[0];
    assert.ok(resumeHTML);assert.ok(resumeHTML.includes(taskDescription));
    assert.equal((resumeHTML.match(/<p(?:\s[^>]*)?>/g)||[]).length,1,'one SSR description for the imported task');
    await page.reload();await page.locator('#progress-resume').waitFor();
    assert.match(await page.locator('#progress-resume').innerText(),/昨晚完成基线[\s\S]*检查日志/);
    const unscheduledTitle='<img src=x onerror=alert(1)>未排期';
    await page.locator('#progress-new').click();
    await page.locator('#progress-form [name=title]').fill(unscheduledTitle);
    await page.locator('#progress-form [name=priority]').selectOption('low');
    await page.locator('#progress-description').fill('本页初始任务描述');
    assert.equal(await page.locator('#progress-form [name=ddl]').inputValue(),'');
    await saveProgress();
    const unscheduled=(await progressSummary()).tasks.find(task=>task.title===unscheduledTitle);
    assert.ok(unscheduled);assert.equal(unscheduled.ddl,'');assert.equal(unscheduled.status,'planned');assert.equal(unscheduled.resume_eligible,true);
    const taskRow=tab=>tab.locator('#progress-todo .progress-task-row[data-id="'+unscheduled.id+'"]');
    await taskRow(page).waitFor();assert.equal(await page.locator('#research-progress img').count(),0);
    assert.equal(await page.locator('#progress-todo .progress-task').first().getAttribute('data-id'),imported.id,'high priority sorts before low priority');
    await page.locator('#progress-priority-filter').selectOption('low');
    assert.equal(await page.locator('#progress-todo .progress-task-row').count(),1);
    await page.locator('#progress-priority-filter').selectOption('');
    await taskRow(page).locator('.progress-task').click();
    assert.equal(await page.locator('#progress-form [name=ddl]').inputValue(),'');
    const other=await page.context().newPage();await other.goto(origin+base+'/todos');await taskRow(other).locator('.progress-task').click();
    await other.locator('#progress-description').fill('另一页面的进度');
    await other.locator('#progress-form [name=ddl]').fill(taskDDL);await saveProgress(other);
    await page.locator('#progress-description').fill('本页的下一步');
    await page.locator('#progress-form [name=priority]').selectOption('medium');
    const conflict=page.waitForResponse(response=>response.url()===progressAPI&&response.request().method()==='POST');
    await page.locator('#progress-form [type=submit]').click();assert.equal((await conflict).status(),409);
    await page.locator('#progress-reload').waitFor();
    assert.equal(await descriptionValue(page),'本页的下一步');
    assert.equal(await page.locator('#progress-form [type=submit]').isDisabled(),true);
    await page.locator('#progress-reload').click();await page.locator('#progress-reload').waitFor({state:'hidden'});
    assert.equal(await descriptionValue(page),'本页的下一步','same-field conflict preserves the local description');
    assert.match(await page.locator('#progress-error').innerText(),/双方修改了相同字段[\s\S]*任务描述（服务器）：另一页面的进度/);
    assert.equal(await page.locator('#progress-form [name=ddl]').inputValue(),taskDDL,'unmodified local field takes the server update');
    assert.equal(await page.locator('#progress-form [name=priority]').inputValue(),'medium','local priority edit survives reload');
    await saveProgress();await other.close();
    const merged=(await progressSummary()).tasks.find(task=>task.id===unscheduled.id);
    assert.equal(merged.description,'本页的下一步');assert.equal(merged.priority,'medium');assert.equal(merged.ddl,taskDDL);assert.equal(merged.status,'planned');
    await taskRow(page).locator('.progress-done-toggle').click();await taskRow(page).waitFor({state:'detached'});
    await page.locator('#progress-done-tab').click();
    const doneRow=page.locator('#progress-done .progress-task-row[data-id="'+unscheduled.id+'"]');await doneRow.waitFor();
    assert.equal(await page.locator('#progress-done .progress-task').count(),1);
    const completed=(await progressSummary()).tasks.find(task=>task.id===unscheduled.id);
    assert.equal(completed.status,'done');assert.ok(completed.completed_at);assert.equal(completed.resume_eligible,false);
    await doneRow.locator('.progress-task').click();await page.locator('#progress-history summary').click();
    assert.match(await page.locator('#progress-history').innerText(),/Done/);
    assert.equal(await page.locator('#progress-complete').innerText(),'恢复到 Todo');await page.locator('#progress-close').click();
    await doneRow.locator('.progress-done-toggle').click();await doneRow.waitFor({state:'detached'});
    await page.locator('#progress-todo-tab').click();await taskRow(page).waitFor();
    const restored=(await progressSummary()).tasks.find(task=>task.id===unscheduled.id);
    assert.equal(restored.status,'planned');assert.equal(restored.completed_at,null);assert.equal(restored.description,'本页的下一步');
    await go('/projects');
    assert.equal(await page.locator('.project-row a a').count(),0);
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-progress-desktop.png')});
    // The mobile check below belongs to the progress page, so come back to it:
    // reloading "/" would look for #progress-resume on the project list.
    await go(base+'/todos');
    await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('#progress-resume').waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));
    await page.screenshot({path:path.join(os.tmpdir(),'llmwiki-progress-mobile.png')});await page.setViewportSize({width:1440,height:960});
    // /literature is the strict catalogue (M-04): a PDF sitting in the project must not
    // list itself, and entries are added deliberately. The file-scanning library it
    // replaces stays reachable at /literature/old and keeps its own coverage below.
    await go(base+'/literature');
    assert.equal(await page.locator('#literature-results').count(),1);
    assert.ok(!(await page.locator('#literature-results').innerText()).includes('demo-paper.pdf'),'unregistered project PDF leaked into the catalogue');
    assert.equal(await page.locator('[data-add]').count(),1);
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
