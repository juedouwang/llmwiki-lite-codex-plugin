/* Prototype-derived real records, destructive-action cancellation and persistent appearance. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
(async()=>{
 const [origin,pid,prototype]=process.argv.slice(2),base='/project/'+pid;
 const evidence=path.join(os.tmpdir(),'llmwiki-workbench-alignment');fs.mkdirSync(evidence,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:960},colorScheme:'light'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  async function post(url,payload){return page.request.post(origin+url,{headers:{Origin:origin,'X-Notebook-Request':'1'},data:payload});}
  const id=crypto.randomBytes(16).toString('hex');
  const document={format:'markdown',title:'低纹理区域跟踪 · 第一次对比',body:'### 实验记录\n\n**固定参数**，完成基线对照。\n\n```python\nprint("实验完成")\n```',tags:[],comments:[{id:'comment1',quote:'固定参数',text:'复核第二组数据。',created_at:null}]};
  const saved=await post(`/api/project/${pid}/notebook/${id}`,{revision:'',document});assert.equal(saved.status(),200);
  const original=await saved.json();
  await page.goto(origin+base+'/records');
  await page.locator('[data-delete-note="'+id+'"]').waitFor();
  assert.equal(await page.locator('.timeline-day').count(),0);
  assert.equal(await page.locator('.filter-bar:visible').count(),0);
  assert.equal(await page.locator('.page-header .primary').innerText(),'新建笔记');
  assert.equal(await page.locator('.page-header .primary svg').count(),1);
  assert.equal(await page.locator('.rw-list-row').filter({hasText:document.title}).locator('.rw-list-meta').innerText().then(s=>s.includes('1 条批注')),true);
  assert.equal(await page.locator('.rw-list-row').filter({hasText:'实验设计与阶段结论'}).locator('[data-delete-note]').count(),0);
  // Same-source layout check, not just an assertion against our own stylesheet.
  if(prototype){
    const ref=await context.newPage();await ref.setContent(fs.readFileSync(prototype,'utf8'));
    await ref.addStyleTag({content:'html{zoom:1.25}body{margin:0}#research-workbench .rw-app{border:0!important;border-radius:0!important;min-height:calc(100dvh / 1.25)!important}'});
    await ref.addScriptTag({path:path.resolve(path.dirname(require.resolve('lucide')),'../umd/lucide.min.js')});
    await ref.locator('#rw-nav [data-view="notes"]').click();
    async function measure(tab,selector){return tab.locator(selector).first().evaluate(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return{x:r.x,y:r.y,width:r.width,height:r.height,padding:s.padding,fontSize:s.fontSize,lineHeight:s.lineHeight,gap:s.gap,backgroundColor:s.backgroundColor,borderRadius:s.borderRadius};});}
    const pairs=[['.page-header','.rw-toolbar',['x','y','width','height']],['.page-header .primary','.rw-toolbar .rw-primary',['x','y','width','height','padding','fontSize','gap','borderRadius']],['.records-timeline','.rw-list',['x','y','width']],['.rw-list-row','.rw-list-row',['x','y','width','height','padding','gap']],['.rw-title-button','.rw-title-button',['x','y','fontSize','lineHeight']],['.rw-list-meta','.rw-list-meta',['x','y','fontSize','lineHeight','gap']],['.workbench-footer','.rw-footer',['x','y','width','height','fontSize']]];
    const comparisons=[];
    for(const [live,approved,keys] of pairs){const actual=await measure(page,live),expected=await measure(ref,approved);const differences=keys.filter(k=>typeof actual[k]==='number'?Math.abs(actual[k]-expected[k])>1:actual[k]!==expected[k]);comparisons.push({live,approved,actual,expected,differences});}
    fs.writeFileSync(path.join(evidence,'notes-comparison.json'),JSON.stringify(comparisons,null,2));
    await ref.screenshot({path:path.join(evidence,'approved-notes.png'),fullPage:true});await ref.close();
    await page.screenshot({path:path.join(evidence,'actual-notes.png'),fullPage:true});
    assert.deepEqual(comparisons.filter(r=>r.differences.length).map(r=>({selector:r.live,differences:r.differences,actual:r.actual,expected:r.expected})),[],'records must match prototype geometry');
  }
  await page.locator('[aria-label="搜索与筛选记录"]').click();await page.getByLabel('搜索记录',{exact:true}).fill('低纹理');
  await page.locator('.records-filter button').click();await page.waitForURL(/q=/);assert.equal(await page.locator('.rw-list-row').count(),1);
  await page.goto(origin+base+'/records');
  const deleteButton=page.locator('[data-delete-note="'+id+'"]');
  await deleteButton.click();await page.locator('#record-delete-dialog [value=cancel]').click();
  assert.equal((await (await page.request.get(origin+`/api/project/${pid}/notebook/${id}`)).json()).exists,true);
  await deleteButton.click();await page.locator('[data-confirm-delete]').click();await page.locator('#record-delete-dialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('[data-delete-note="'+id+'"]').count(),0);
  await page.locator('[data-undo-delete]').click();await deleteButton.waitFor();
  assert.equal((await (await page.request.get(origin+`/api/project/${pid}/notebook/${id}`)).json()).revision,original.revision);
  // External edit after list load must reject removal and leave the original row visible.
  const changed=await post(`/api/project/${pid}/notebook/${id}`,{revision:original.revision,document:{...document,body:'外部更新'}});assert.equal(changed.status(),200);
  await deleteButton.click();await page.locator('[data-confirm-delete]').click();await page.locator('[data-delete-error]').filter({hasText:'其他窗口'}).waitFor();
  assert.equal(await deleteButton.count(),1);await page.locator('#record-delete-dialog [value=cancel]').click();
  console.log('PASS notes prototype geometry / compact search / real comments / cancel / delete / undo / stale revision');
  await page.goto(origin+'/reports?context='+pid);
  await page.locator('#report-new').click();await page.locator('#report-date').fill('2026-09-20');await page.locator('#report-create-submit').click();
  await page.locator('#report-save-state').filter({hasText:'已保存'}).waitFor();
  await page.goto(origin+base+'/literature');await page.locator('[data-add]').click();
  await page.locator('[data-literature-form=add] [name=locator]').fill('https://arxiv.org/abs/2304.08069');
  await page.locator('[data-literature-form=add] [name=title]').fill('Segment Anything');await page.locator('[data-literature-form=add] [type=submit]').click();
  await page.locator('.literature-list [data-item]').filter({hasText:'Segment Anything'}).waitFor();
  // The XSS regression has already run; make the disposable task fixture readable for screenshots.
  let progress=await (await page.request.get(origin+`/api/project/${pid}/progress`)).json();
  for(const task of progress.tasks.filter(t=>t.title.includes('<img'))){
    const clean=await post(`/api/project/${pid}/progress`,{revision:progress.revision,action:'update',id:task.id,task:{title:'整理实验数据'}});
    assert.equal(clean.status(),200);progress=await clean.json();
  }
  // Toggle appearance through the actual sidebar; navigation replaces the sidebar DOM.
  async function theme(value){await page.locator('.theme-menu summary').click();await page.locator('.theme-options [data-theme-choice='+value+']').click();}
  await theme('dark');assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  await page.reload();assert.equal(await page.locator('html').getAttribute('data-resolved-theme'),'dark');
  const routes=[['records',base+'/records'],['reports','/reports?context='+pid],['knowledge',base],['literature',base+'/literature'],['progress',base+'/todos'],['code',base+'/code']];
  for(const [name,route] of routes){
    await page.goto(origin+route);
    assert.equal(await page.locator('html').getAttribute('data-resolved-theme'),'dark');
    assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(33, 33, 33)');
    if(name==='reports'){assert.equal(await page.locator('.report-row').count(),1);assert.equal(await page.locator('.report-status.status-draft').innerText(),'草稿');assert.equal(await page.locator('.report-status').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(51, 45, 63)');const boxes=await page.locator('.rw-filter-row select').evaluateAll(items=>items.map(e=>e.getBoundingClientRect().y));assert.ok(boxes.every(y=>Math.abs(y-boxes[0])<1),'desktop filters stay in one row');}
    if(name==='knowledge'){assert.equal(await page.locator('#knowledge-updates').evaluate(e=>{const s=getComputedStyle(e);return s.borderTopStyle==='solid'&&parseFloat(s.borderTopWidth)>=0.8&&parseFloat(s.borderTopWidth)<=1;}),true,'outline remains visible after 125% device-pixel rounding');assert.equal(await page.locator('.rw-page-list li').count(),1);assert.equal(await page.locator('.rw-knowledge-document .document h1').innerText(),'低纹理配准实验');}
    if(name==='literature'){assert.equal(await page.locator('.literature-list .rw-list-row').count(),1);assert.equal(await page.locator('.page-header .primary').evaluate(e=>e.getBoundingClientRect().height),41.25);}
    if(name==='code')await page.locator('.code-version-title').waitFor();
    if(name==='progress'){
      await page.locator('.progress-day').first().waitFor();
      // This iteration explicitly replaces the old dual-column/status prototype.
      // Enforce the new geometry and semantic contract even without a prototype file.
      assert.equal(await page.locator('.progress-day').count(),7);
      assert.equal(await page.locator('#progress-todo-tab').getAttribute('aria-pressed'),'true');
      assert.equal(await page.locator('#progress-done-tab').getAttribute('aria-pressed'),'false');
      assert.equal(await page.locator('#progress-done').isHidden(),true);
      assert.deepEqual(await page.locator('#progress-priority-filter option').evaluateAll(items=>items.map(el=>el.value)),['','high','medium','low']);
      assert.equal(await page.locator('#progress-form [name=checkpoint], #progress-form [name=next_step], #progress-form [name=start], #progress-form [name=end], #progress-form [name=status], .progress-inline-status').count(),0);
      assert.equal(await page.locator('#progress-description').count(),1);
      assert.equal(await page.locator('#progress-form input[type=date]').count(),1);
      assert.equal(await page.locator('#progress-form [name=ddl]').evaluate(el=>el.required),false);
      const taskRow=page.locator('#progress-todo .progress-task-row').first();await taskRow.hover();
      const taskBox=await taskRow.locator('.progress-task').boundingBox(),menuBox=await taskRow.locator('.progress-inline-priority').boundingBox(),toggleBox=await taskRow.locator('.progress-done-toggle').boundingBox();
      assert.ok(menuBox.x>=taskBox.x+taskBox.width-1,'priority selector must not intercept the task title');
      assert.ok(toggleBox.x+toggleBox.width<=taskBox.x+1,'explicit completion control must not intercept the task title');
      assert.ok(Math.abs(toggleBox.width-toggleBox.height)<1,'completion control remains circular at 125% zoom');
      const priorityStyle=await taskRow.locator('.progress-inline-priority').evaluate(el=>{const s=getComputedStyle(el);return{color:s.color,background:s.backgroundColor,opacity:s.opacity};});
      assert.notEqual(priorityStyle.color,'rgba(0, 0, 0, 0)');assert.notEqual(priorityStyle.background,'rgba(0, 0, 0, 0)');
      assert.notEqual(priorityStyle.color,priorityStyle.background);assert.equal(priorityStyle.opacity,'1','priority text remains visible');
      const priorityValues=await page.locator('#progress-todo .progress-inline-priority').evaluateAll(items=>items.map(el=>el.value));
      assert.deepEqual(priorityValues,[...priorityValues].sort((a,b)=>['high','medium','low'].indexOf(a)-['high','medium','low'].indexOf(b)),'Todo rows are priority ordered');
      const resume=page.locator('#progress-resume .progress-resume-item');assert.ok(await resume.count()>0);
      for(const item of await resume.all()){
        assert.equal(await item.locator('.resume-title').count(),1);
        assert.equal(await item.locator('p').count(),1,'one unified description, never checkpoint/next-step columns');
        assert.equal(await item.locator('.progress-description-excerpt').count(),1);
      }
      const days=await page.locator('.progress-day').evaluateAll(items=>items.map(el=>{const r=el.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width};}));
      assert.ok(days.every(day=>Math.abs(day.y-days[0].y)<1&&Math.abs(day.width-days[0].width)<1),'seven equal-width deadline columns');
      for(let i=1;i<days.length;i++)assert.ok(days[i].x>=days[i-1].x+days[i-1].width-1,'deadline columns do not overlap');
      const contract={taskBox,menuBox,toggleBox,priorityStyle,priorityValues,days};
      fs.writeFileSync(path.join(evidence,'progress-layout-contract.json'),JSON.stringify(contract,null,2));
      await taskRow.locator('.progress-task').click();await page.locator('#progress-dialog').waitFor();
      const descriptionBox=await page.locator('#progress-description').boundingBox(),ddlBox=await page.locator('#progress-form [name=ddl]').boundingBox(),priorityBox=await page.locator('#progress-form [name=priority]').boundingBox();
      assert.ok(descriptionBox.y>=Math.max(ddlBox.y+ddlBox.height,priorityBox.y+priorityBox.height),'single description below DDL/priority options');
      assert.ok(ddlBox.x>=priorityBox.x+priorityBox.width-1,'DDL and priority inputs do not overlap');
      assert.equal(await page.locator('#progress-description').isEditable(),true);
      await page.locator('#progress-close').click();await page.mouse.move(0,0);
      console.log('PASS progress layout: unified description / Todo+Done / optional DDL / visible priority controls / seven deadline columns');
    }
    await page.screenshot({path:path.join(evidence,name+'-dark.png'),fullPage:true});
    await theme('light');await page.screenshot({path:path.join(evidence,name+'-light.png'),fullPage:true});await theme('dark');
  }
  await theme('system');await page.emulateMedia({colorScheme:'light'});assert.equal(await page.locator('html').getAttribute('data-resolved-theme'),'light');
  await page.emulateMedia({colorScheme:'dark'});await page.waitForFunction(()=>document.documentElement.dataset.resolvedTheme==='dark');
  await theme('light');await page.emulateMedia({colorScheme:'dark'});assert.equal(await page.locator('html').getAttribute('data-resolved-theme'),'light');
  await page.locator('[data-workbench-nav=records]').click();await page.locator('#research-records').waitFor();await theme('dark');
  await page.locator('[data-workbench-nav=code]').click();await page.locator('.code-version-title').waitFor();assert.equal(await page.locator('.theme-options [data-theme-choice=dark]').getAttribute('aria-pressed'),'true');
  await page.goto(origin+base+'/todos');await page.locator('#progress-new').click();await page.locator('#progress-form [name=title]').fill('体验验证任务');await page.locator('#progress-description').fill('统一描述体验验证');await page.locator('#progress-form [name=priority]').selectOption('medium');assert.equal(await page.locator('#progress-form [name=ddl]').inputValue(),'');await page.locator('#progress-form [type=submit]').click();await page.locator('#progress-dialog').waitFor({state:'hidden'});await page.locator('#progress-todo .progress-task').filter({hasText:'体验验证任务'}).click();
  await page.locator('#progress-dialog').waitFor();assert.equal(await page.locator('#progress-dialog').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(33, 33, 33)');await page.locator('#progress-close').click();
  await page.goto(origin+'/reports?context='+pid);const daily=await page.locator('#report-new').evaluate(el=>({height:el.getBoundingClientRect().height,padding:getComputedStyle(el).padding}));await page.locator('#report-new').click();await page.locator('#report-create').waitFor();await page.locator('#report-create [value=cancel]').click();
  await page.goto(origin+'/reports?context='+pid+'&view=weekly');assert.equal(await page.locator('#report-new').innerText(),'新建周报');
  assert.deepEqual(await page.locator('#report-new').evaluate(el=>({height:el.getBoundingClientRect().height,padding:getComputedStyle(el).padding})),daily);
  await page.setViewportSize({width:390,height:844});
  for(const [name,route] of routes){await page.goto(origin+route);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),name+' narrow overflow');}
  await page.goto(origin+base+'/records');await page.screenshot({path:path.join(evidence,'notes-mobile-dark.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS 3 appearance modes / refresh / live system changes / all 6 columns light + dark / cached navigation / dialogs / unified actions / mobile');
  console.log('Screenshots: '+evidence);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
