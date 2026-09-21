const {chromium} = require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const {execFileSync} = require('node:child_process');
const cfg = JSON.parse(process.argv[2]);
(async () => {
  const browser = await chromium.launch({headless:true,channel:process.env.LLMWIKI_BROWSER_CHANNEL || 'chrome'});
  const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors=[], samples=[];
  page.setDefaultTimeout(15000);
  const [a,b] = cfg.projects;
  let documents = 0;
  page.on('pageerror', e=>errors.push(e.message));
  page.on('request', r=>{if(r.isNavigationRequest() && r.frame()===page.mainFrame()) documents++;});
  fs.writeFileSync(path.join(cfg.evidence,'trace.log'),'start\n');
  const trace = message => fs.appendFileSync(path.join(cfg.evidence,'trace.log'),message+'\n');
  const nav = key => page.locator(`[data-workbench-nav="${key}"]`);
  const ready = async () => {
    await page.waitForFunction(()=>document.querySelector('#code-change-title')?.textContent && !document.querySelector('#code-change-title').textContent.includes('正在'));
    await page.waitForFunction(()=>document.querySelector('#code-refresh')?.disabled===false);
  };
  try {
    await page.goto(`${cfg.origin}/project/${a.pid}/code`); await ready();
    await page.evaluate(()=>{window.initialCode = document.getElementById('code-app'); window.navigationToken = 'retained';});
    for(const key of ['todos','records','reports','overview','literature','code']) {
      trace('NAV '+key); await nav(key).click();
      await page.waitForFunction(k=>document.querySelector(`[data-workbench-nav="${k}"]`)?.getAttribute('aria-current')==='page',key);
    }
    trace('loop complete'); await ready(); trace('ready');
    assert.equal(documents,1,'column navigation must not reload the document');
    assert(await page.evaluate(()=>window.initialCode===document.getElementById('code-app')),'keep the actual initialized Git DOM');
    // Hold all fresh Git reads. Cached column content must still paint, not just a spinner.
    await page.route('**/api/project/*/code/**',async route=>{await new Promise(r=>setTimeout(r,500));await route.continue().catch(()=>{});});
    for(const key of ['overview','code','records','code','reports','code']) {
      trace('MEASURE '+key);
      const result = await Promise.race([page.evaluate(async key => {
        const started=performance.now();document.querySelector(`[data-workbench-nav="${key}"]`).click();
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
        return {key,ms:performance.now()-started,active:document.querySelector(`[data-workbench-nav="${key}"]`)?.getAttribute('aria-current'),
          text:document.getElementById('main-content').innerText, retained:key!=='code'||window.initialCode===document.getElementById('code-app')};
      },key),new Promise((_,reject)=>setTimeout(()=>reject(new Error('paint timed out '+key)),3000))]);
      assert.equal(result.active,'page');assert(result.retained);assert(result.text.trim().length>8);
      if(key==='code') assert(result.text.includes(a.name+' 阶段'),'actual commit content must be displayed');
      assert(result.ms<100,`${key}: ${result.ms}ms`);samples.push({key,ms:result.ms});
    }
    console.log('PASS 六栏目同文档切换，Git真实DOM保留，慢后台请求不阻塞实际内容 <100ms');
    await page.unrouteAll({behavior:'ignoreErrors'}); trace('routes removed');
    await nav('records').click(); await page.goBack();
    await page.waitForURL(`**/project/${a.pid}/code`);await page.waitForSelector('#code-app');
    assert(await page.evaluate(()=>window.initialCode===document.getElementById('code-app')));
    await page.goForward();await page.waitForURL('**/records');
    assert.equal(documents,1);console.log('PASS 浏览器后退/前进恢复栏目');
    await nav('reports').click();
    await page.locator('.console-project-switcher summary').click();
    await page.locator('.console-project-menu a').filter({hasText:b.name}).click();
    await page.waitForURL(u=>u.searchParams.get('context')===b.pid);
    assert(new URL(page.url()).searchParams.get('context')===b.pid,'report context follows selected project');
    assert.equal(new URL(page.url()).pathname,'/reports');
    await nav('code').click();await page.waitForURL(`**/project/${b.pid}/code`);await ready();
    assert(await page.locator('#code-app').innerText().then(t=>t.includes(b.name+' 阶段')));
    assert(!await page.locator('#code-app').innerText().then(t=>t.includes(a.name+' 阶段')));
    await page.locator('.console-project-switcher summary').click();
    await page.locator('.console-project-menu a').filter({hasText:a.name}).click();
    await page.waitForURL(`**/project/${a.pid}/code`);await page.waitForSelector('#code-app');
    assert(await page.evaluate(()=>window.initialCode===document.getElementById('code-app')));
    console.log('PASS 项目隔离，日报不跳项目，返回Git保留已有真实数据');
    await nav('records').click();
    fs.writeFileSync(path.join(a.root,'research.txt'),'外部推进\n');
    const git=(...args)=>execFileSync('git',args,{cwd:a.root,encoding:'utf8',windowsHide:true}).trim();
    git('add','research.txt');git('commit','-m','外部新增阶段');
    await nav('code').click();
    await page.waitForFunction(()=>document.querySelector('#code-app')?.innerText.includes('外部新增阶段'));
    console.log('PASS 外部提交在回访后台刷新后显示');
    await page.screenshot({path:path.join(cfg.evidence,'navigation-code.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(cfg.evidence,'performance.json'),JSON.stringify({samples,documents,errors},null,2));
    console.log(JSON.stringify({samples,documents,errors}));
  } catch(error) { trace('FAIL '+error.stack); await page.screenshot({path:path.join(cfg.evidence,'failure.png')}); console.error('PAGE', await page.locator('main').innerText().catch(()=>''), errors); throw error; } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
