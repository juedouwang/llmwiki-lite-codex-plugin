/* User clicks drive real temporary worktrees; CLI below only verifies results. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path');
const {execFileSync}=require('node:child_process');
const cfg=JSON.parse(process.argv[2]);
const git=(project,...args)=>execFileSync('git',args,{cwd:project.root,encoding:'utf8',windowsHide:true}).trim();
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.LLMWIKI_BROWSER_EXECUTABLE,channel:process.env.LLMWIKI_BROWSER_CHANNEL || 'chrome'});
  const context=await browser.newContext({viewport:{width:1440,height:960}});
  const page=await context.newPage(), errors=[], dialogs=[], writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss();});
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/code/'))writes.push(r.url());});
  const ready=async()=>{
    await page.waitForFunction(()=>document.querySelector('#code-refresh')?.disabled===false);
    await page.waitForSelector('#code-create-from');
  };
  const open=async project=>{await page.goto(`${cfg.origin}/project/${project.pid}/code`);await ready();};
  const confirm=async()=>{
    await page.waitForFunction(()=>document.querySelector('#code-confirm')?.disabled===false);
    await page.locator('#code-confirm').click();
    await page.waitForFunction(()=>!document.querySelector('#code-dialog')?.open);
    await ready();
  };
  const save=async(message,selected)=>{
    await page.locator('#code-save').click();
    await page.locator('#code-save-message').fill(message);
    await page.locator('#code-select-all').uncheck();
    for(const name of selected)await page.getByRole('checkbox',{name:`保存 ${name}`,exact:true}).check();
    await confirm();
  };
  try{
    const primaryIndex=fs.readFileSync(cfg.primary_index);
    const linkedStaged=git(cfg.linked,'rev-parse',':b.txt');
    await open(cfg.linked);
    assert.equal(writes.length,0,'opening never auto-saves or fetches');
    assert.equal(await page.locator('#code-capability').isVisible(),false);
    assert.match(await page.locator('#code-change-note').innerText(),/仅操作当前工作树/);
    assert.equal(await page.locator('#code-save').isEnabled(),true);
    for(const selector of ['#code-branches summary svg','#code-merge svg','#code-create-from svg']){
      assert.equal(await page.locator(selector+' circle').count(),2,'approved glyph '+selector);
    }
    assert.deepEqual(await page.locator('#code-create-from svg path').evaluateAll(ns=>ns.map(n=>n.getAttribute('d'))),
      ['M6 3v12','M15 6a9 9 0 0 0-9 9','M18 15v6','M21 18h-6']);
    assert.equal(await page.locator('#code-detail .code-file svg').first().evaluate(n=>getComputedStyle(n).width),'16px');
    await page.locator('#code-branches summary').click();
    const occupied=page.locator('#code-branch-menu [data-branch="main"]');
    assert.equal(await occupied.isDisabled(),true);
    assert.match(await occupied.innerText(),/其他工作树使用中/);
    assert.ok((await occupied.getAttribute('title')).includes(cfg.primary.root));
    assert.equal(await page.locator('#code-branch-menu [data-branch="available"]').isEnabled(),true);
    assert.equal(await page.locator('#code-new-branch').isEnabled(),true);
    for(const width of [1440,580,320]){
      await page.setViewportSize({width,height:960});
      await page.screenshot({path:path.join(cfg.evidence,`linked-branches-${width}.png`),fullPage:true});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal page overflow '+width);
    }
    await page.setViewportSize({width:1440,height:960});
    await page.locator('#code-branches summary').click();
    console.log('PASS approved code icons / writable linked worktree / occupied branch inline / 320-1440px');

    await save('linked selected only',['a.txt']);
    assert.notEqual(git(cfg.linked,'rev-parse','HEAD'),cfg.initial);
    assert.equal(git(cfg.primary,'rev-parse','HEAD'),cfg.initial);
    assert.deepEqual(fs.readFileSync(cfg.primary_index),primaryIndex);
    assert.equal(git(cfg.linked,'show','HEAD:a.txt'),'linked selected');
    assert.equal(git(cfg.linked,'show','HEAD:b.txt'),'base b');
    assert.equal(git(cfg.linked,'rev-parse',':b.txt'),linkedStaged);
    assert.equal(fs.readFileSync(path.join(cfg.linked.root,'c.txt'),'utf8'),'linked unselected\n');
    console.log('PASS linked selected-save preserves primary dirty index and linked unselected staging');
    await save('save remaining linked files',['b.txt','c.txt']);
    assert.equal(git(cfg.linked,'status','--porcelain'),'');
    await page.locator('#code-branches summary').click();
    await page.locator('#code-new-branch').click();
    await page.locator('#code-branch-name').fill('browser-experiment');
    await confirm();
    assert.equal(git(cfg.primary,'rev-parse','refs/heads/browser-experiment'),git(cfg.linked,'rev-parse','HEAD'));
    assert.equal(git(cfg.linked,'branch','--show-current'),'experiment');
    await page.locator('#code-branches summary').click();
    await page.locator('#code-branch-menu [data-branch="available"]').click();
    await confirm();
    assert.equal(git(cfg.linked,'branch','--show-current'),'available');
    assert.equal(git(cfg.primary,'branch','--show-current'),'main');
    assert.deepEqual(fs.readFileSync(cfg.primary_index),primaryIndex);
    assert.equal(fs.readFileSync(path.join(cfg.primary.root,'a.txt'),'utf8'),'primary unsaved\n');
    console.log('PASS shared branch creation and current-worktree switch despite other dirty worktree');
    await open(cfg.primary);
    await page.locator('#code-branches summary').click();
    const nowOccupied=page.locator('#code-branch-menu [data-branch="available"]');
    assert.equal(await nowOccupied.isDisabled(),true);
    assert.ok((await nowOccupied.getAttribute('title')).includes(cfg.linked.root));
    assert.equal(await page.locator('#code-branch-menu [data-branch="experiment"]').isEnabled(),true);
    assert.deepEqual(errors,[]);
    assert.deepEqual(dialogs,[]);
    assert.ok(writes.every(url=>url.includes(`/project/${cfg.linked.pid}/code/`)),'writes remain project/worktree-bound');
    assert.ok(writes.every(url=>!url.endsWith('/fetch')),'no automatic remote check');
    console.log('PASS refreshed cross-worktree occupancy / no native dialogs / no cross-project writes');
  }catch(error){
    await page.screenshot({path:path.join(cfg.evidence,'failure.png'),fullPage:true}).catch(()=>{});
    throw error;
  }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
