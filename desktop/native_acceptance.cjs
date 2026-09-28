/* Connects to the actual desktop WebView2, not a separately launched browser.
 * The companion Python runner provides a disposable registry and project. */
const {chromium} = require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

(async () => {
  const browser = await chromium.connectOverCDP(cfg.debug);
  const context = browser.contexts()[0];
  const page = context.pages().find(p => p.url().startsWith(cfg.origin));
  assert.ok(page, 'The actual application WebView must be loaded');
  page.setDefaultTimeout(12000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], {origin:cfg.origin});
  const base = cfg.origin + '/project/' + cfg.pid;
  const noteId = 'd'.repeat(32);
  const evidence = cfg.evidence;
  fs.mkdirSync(evidence, {recursive:true});
  const saved = prefix => page.waitForFunction(p => document.querySelector('#'+p+'-save-state')?.textContent === '已保存', prefix);
  const snapshot = name => page.screenshot({path:path.join(evidence, name+'.png')});

  if (cfg.phase === 'restart') {
    assert.equal(await page.evaluate(() => localStorage.getItem('workbench.theme')), 'dark');
    await page.goto(base+'/notebook/'+noteId);
    await page.locator('#nb-edit').click();
    await page.waitForFunction(() => document.querySelector('#nb-live img')?.naturalWidth===600);
    assert.match(await page.locator('#nb-source').inputValue(), /CLOSE-SAVE-CHECK/);
    await saved('nb');
    await snapshot('reopened');
    console.log('PASS: packaged application restart preserves theme, note and screenshot.');
    process.exit(0);
  }

  // Exercise every existing column in the embedded renderer, with its bundled assets.
  for (const [name, url] of [
    ['daily', cfg.origin+'/daily?context='+cfg.pid],
    ['projects', cfg.origin+'/projects'],
    ['progress', base+'/todos'],
    ['records', base+'/records'],
    ['knowledge', base],
    ['literature', base+'/literature'],
    ['code', base+'/code'],
    ['reports', cfg.origin+'/reports'],
  ]) {
    const response = await page.goto(url);
    assert.equal(response.status(), 200, name);
    await page.locator('#main-content').waitFor();
    if (name === 'code') {
      await page.waitForFunction(() => document.body.innerText.includes('desktop test fixture'));
    }
    await snapshot(name);
  }

  await page.goto(cfg.origin+'/daily?context='+cfg.pid);
  await page.locator('#daily-new').click();
  await page.locator('#daily-title').fill('桌面新建的临时任务');
  await page.locator('#daily-description-live').fill('检查本地桌面与共享后端的实际联动。');
  await page.locator('#daily-save').click();
  await page.getByRole('button', {name:'桌面新建的临时任务', exact:true}).waitFor();
  await snapshot('daily-created');

  await page.goto(base+'/notebook/'+noteId);
  await page.locator('#nb-live').waitFor({state:'visible'});
  await page.locator('#nb-title').fill('桌面端真实粘贴验收');
  await page.locator('#nb-live').fill('# 研究记录\n\n桌面端使用相同的编辑器。\n\n```python\nprint("科研工作台")\n```\n');
  await saved('nb');
  await page.locator('#nb-live').focus();
  await page.locator('#nb-live').press('Control+End');
  await page.evaluate(async () => {
    const canvas = document.createElement('canvas'); canvas.width=600;canvas.height=200;
    const ctx=canvas.getContext('2d');ctx.fillStyle='#eaf1ec';ctx.fillRect(0,0,600,200);
    ctx.strokeStyle='#35674a';ctx.lineWidth=3;ctx.beginPath();
    for(let x=12;x<588;x++)ctx.lineTo(x,100+50*Math.sin(x/40));ctx.stroke();
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
  });
  await page.locator('#nb-live').press('Control+v');
  await page.waitForFunction(() => document.querySelector('#nb-live img')?.naturalWidth===600);
  await saved('nb');
  await page.locator('#nb-preview-mode').click();
  await page.locator('#nb-preview h1').waitFor();
  await snapshot('notebook-light');
  await page.evaluate(() => document.querySelector('[data-theme-choice="dark"]').click());
  await page.waitForFunction(() => document.documentElement.dataset.resolvedTheme==='dark');
  await snapshot('notebook-dark');

  const note = await (await context.request.get(cfg.origin+'/api/project/'+cfg.pid+'/notebook/'+noteId)).json();
  assert.match(note.document.body, /print\("科研工作台"\)/);
  assert.match(note.document.body, /!\[\]\(\.\.\/assets\//);
  await page.reload();
  await page.waitForFunction(() => document.documentElement.dataset.resolvedTheme==='dark');
  await page.locator('#nb-edit').click();
  await page.waitForFunction(() => document.querySelector('#nb-live img')?.naturalWidth===600);
  await saved('nb');

  // Leave a dirty autosaving edit for the runner's native WM_CLOSE test.
  await page.locator('#nb-live').press('Control+End');
  await page.locator('#nb-live').pressSequentially(' CLOSE-SAVE-CHECK');
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(evidence,'ui-result.json'), JSON.stringify({
    ok:true, renderer:'actual desktop WebView2', pages:8, clipboard:'native Ctrl+V PNG',
    createdTask:true, markdown:true, persistence:true, darkTheme:true, jsErrors:errors,
  },null,2));
  console.log('PASS: native WebView2, eight pages, task creation, real Ctrl+V, Markdown, reload, theme.');
  process.exit(0);
})().catch(error => { console.error(error); process.exit(1); });
