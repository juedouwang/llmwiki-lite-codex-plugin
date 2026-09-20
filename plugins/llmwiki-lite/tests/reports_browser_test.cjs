const {chromium} = require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
(async () => {
  const cfg = JSON.parse(process.argv[2]);
  fs.mkdirSync(cfg.evidence, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: process.env.LLMWIKI_BROWSER_CHANNEL || 'chrome'});
  const context = await browser.newContext({viewport: {width: 1360, height: 920}});
  const page = await context.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const api = `/api/reports/daily/2026-09-19`;
  const url = cfg.origin + `/reports/daily/2026-09-19`;
  async function saved() { await page.waitForFunction(() => document.querySelector('#report-save-state').textContent === '已保存'); }
  async function follow(target, link) {
    const url=new URL(await link.getAttribute('href'),target.url()).href;
    await link.click(); await target.waitForURL(url);
  }
  async function paste(text) {
    await page.evaluate(text => {
      const target = document.activeElement;
      const data = new DataTransfer(); data.setData('text/plain', text);
      target.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
    }, text);
  }
  try {
    // Regression: the registry selects B, while this tab explicitly browses A.
    const nav = await context.newPage();
    nav.on('pageerror', e => errors.push(e.message));
    async function expectProject(target, pid, name) {
      assert.equal(await target.locator('.console-project-label b').innerText(), name);
      for (const [label, suffix] of [['科研进度','/todos'], ['科研记录','/records'], ['知识库',''], ['文献','/literature'], ['代码','/code']]) {
        assert.equal(await target.locator('.console-navigation').getByRole('link',{name:label,exact:true}).getAttribute('href'), `/project/${pid}${suffix}`);
      }
    }
    await nav.goto(cfg.origin + '/reports');
    await expectProject(nav, cfg.other_pid, '第二项目');
    await nav.goto(cfg.origin + `/project/${cfg.pid}/records`);
    await follow(nav, nav.getByRole('link',{name:'日报与周报',exact:true}));
    assert.equal(new URL(nav.url()).searchParams.get('context'), cfg.pid);
    await expectProject(nav, cfg.pid, '报告交互验收');
    assert.equal(await nav.getByLabel('筛选参与项目').inputValue(), '');
    await follow(nav, nav.getByRole('link',{name:'周报',exact:true}));
    await expectProject(nav, cfg.pid, '报告交互验收');
    await Promise.all([nav.waitForURL(url=>url.searchParams.get('project')===cfg.other_pid), nav.getByLabel('筛选参与项目').selectOption(cfg.other_pid)]);
    await nav.locator('.report-search > summary').click();
    await nav.locator('input[aria-label="搜索报告"]').fill('2026-09-14');
    await Promise.all([nav.waitForURL(url=>url.searchParams.get('q')==='2026-09-14'), nav.getByRole('button',{name:'搜索',exact:true}).click()]);
    await expectProject(nav, cfg.pid, '报告交互验收');
    const filteredList = nav.url();
    await nav.locator('.report-row').click();
    await nav.locator('#report-save-state').getByText('已保存',{exact:true}).waitFor();
    await expectProject(nav, cfg.pid, '报告交互验收');
    assert.equal(await nav.locator('#research-report').getAttribute('data-api'), '/api/reports');
    await nav.reload();
    await expectProject(nav, cfg.pid, '报告交互验收');
    await follow(nav, nav.getByRole('link',{name:'返回列表',exact:true}));
    for (const [key, value] of new URL(filteredList).searchParams) assert.equal(new URL(nav.url()).searchParams.get(key), value);
    assert.equal(new URL(nav.url()).hash, '#report-row-0');
    await expectProject(nav, cfg.pid, '报告交互验收');
    // Explicit switching preserves report view/filter; it doesn't re-home a document.
    await nav.getByLabel('切换研究项目',{exact:true}).click();
    await follow(nav, nav.locator('.console-project-menu').getByRole('link',{name:'第二项目',exact:true}));
    await expectProject(nav, cfg.other_pid, '第二项目');
    assert.equal(new URL(nav.url()).searchParams.get('view'), 'weekly');
    assert.equal(await nav.getByLabel('筛选参与项目').inputValue(), cfg.other_pid);
    assert.equal(await nav.locator('input[aria-label="搜索报告"]').inputValue(), '2026-09-14');
    await follow(nav, nav.getByRole('link',{name:'科研记录',exact:true}));
    assert.equal(new URL(nav.url()).pathname, `/project/${cfg.other_pid}/records`);
    // A second tab cannot reset this tab or the global default.
    await page.goto(cfg.origin + `/reports?context=${cfg.pid}`);
    await expectProject(page, cfg.pid, '报告交互验收');
    await nav.goto(cfg.origin + `/reports?context=${cfg.other_pid}`);
    await page.reload();
    await expectProject(page, cfg.pid, '报告交互验收');
    await nav.reload();
    await expectProject(nav, cfg.other_pid, '第二项目');
    await nav.goto(cfg.origin + '/reports');
    await expectProject(nav, cfg.other_pid, '第二项目');
    // Paging, type switches and creation retain A independently of participant B.
    await nav.goto(cfg.origin + `/reports?context=${cfg.pid}&project=${cfg.other_pid}&q=2026-08`);
    await follow(nav, nav.getByRole('link',{name:'下一页',exact:true}));
    await expectProject(nav, cfg.pid, '报告交互验收');
    assert.equal(new URL(nav.url()).searchParams.get('offset'), '30');
    assert.equal(await nav.locator('.report-row').count(), 1);
    await follow(nav, nav.getByRole('link',{name:'周报',exact:true}));
    assert.equal(new URL(nav.url()).searchParams.get('offset'), null);
    await expectProject(nav, cfg.pid, '报告交互验收');
    await follow(nav, nav.getByRole('link',{name:'日报',exact:true}));
    await expectProject(nav, cfg.pid, '报告交互验收');
    await nav.getByRole('button',{name:'新建日报',exact:true}).click();
    await nav.locator('#report-date').fill('2026-09-17');
    await nav.locator('#report-create-submit').click();
    await nav.locator('#report-save-state').getByText('已保存',{exact:true}).waitFor();
    await expectProject(nav, cfg.pid, '报告交互验收');
    assert.equal(new URL(nav.url()).searchParams.get('context'), cfg.pid);
    assert.equal(await nav.locator('#research-report').getAttribute('data-api'), '/api/reports');
    const created = await (await context.request.get(cfg.origin + '/api/reports/daily/2026-09-17')).json();
    assert.equal(created.metadata.owner_project_id, null);
    await follow(nav, nav.getByRole('link',{name:'返回列表',exact:true}));
    await expectProject(nav, cfg.pid, '报告交互验收');
    assert.equal(await nav.getByLabel('筛选参与项目').inputValue(), cfg.other_pid);
    // Legacy deep links use their original owner only when context is absent.
    await nav.goto(cfg.origin + `/project/${cfg.pid}/reports/weekly/2026-09-14`);
    await expectProject(nav, cfg.pid, '报告交互验收');
    await nav.goto(cfg.origin + `/project/${cfg.pid}/reports/weekly/2026-09-14?context=${cfg.other_pid}`);
    await expectProject(nav, cfg.other_pid, '第二项目');
    assert.equal(await nav.locator('#research-report').getAttribute('data-api'), `/api/project/${cfg.pid}/reports`);
    await follow(nav, nav.getByRole('link',{name:'返回列表',exact:true}));
    await expectProject(nav, cfg.other_pid, '第二项目');
    await nav.goto(cfg.origin + '/reports?context=deleted-project');
    assert.equal(await nav.locator('.console-project-label b').innerText(), '选择项目');
    await follow(nav, nav.getByRole('link',{name:'周报',exact:true}));
    await nav.reload();
    assert.equal(await nav.locator('.console-project-label b').innerText(), '选择项目');
    await nav.getByLabel('切换研究项目',{exact:true}).click();
    await follow(nav, nav.locator('.console-project-menu').getByRole('link',{name:'报告交互验收',exact:true}));
    await expectProject(nav, cfg.pid, '报告交互验收');
    await nav.close();
    // One cross-project weekly document, not one copy per project. Legacy links keep their owner.
    await page.goto(cfg.origin + '/reports?view=weekly&status=formal');
    assert.equal(await page.locator('.report-row[href^="/reports/weekly/2026-09-14"]').count(),1);
    assert.equal(await page.locator('.report-row').count(),2);
    assert.deepEqual(await page.locator('.report-status').allTextContents(),['正式版','正式版']);
    assert.equal(await page.locator('.report-status.status-formal').count(),2);
    await Promise.all([page.waitForURL(url=>url.searchParams.get('project')===cfg.other_pid), page.getByLabel('筛选参与项目').selectOption(cfg.other_pid)]);
    assert.equal(await page.locator('.report-row').count(),1);
    await page.locator('.report-row').click(); await saved();
    const weeklyApi = cfg.origin + '/api/reports/weekly/2026-09-14';
    const formalWeek = await (await context.request.get(weeklyApi+'?version=1')).json();
    await page.locator('#report-confirm').click();
    await page.locator('#report-source').fill('两个项目的修订草稿'); await saved();
    const revisedWeek = await (await context.request.get(weeklyApi)).json();
    assert.equal(revisedWeek.mode,'draft'); assert.equal(revisedWeek.metadata.versions.length,1);
    assert.deepEqual([...revisedWeek.metadata.project_ids].sort(),[cfg.pid,cfg.other_pid].sort());
    assert.equal((await (await context.request.get(weeklyApi+'?version=1')).json()).body,formalWeek.body);
    await page.goto(cfg.origin + `/project/${cfg.pid}/reports/weekly/2026-09-14`); await saved();
    assert.equal(await page.locator('.console-navigation a[aria-current=page]').innerText(),'日报与周报');
    await page.locator('#report-preview').getByText('历史项目周报，不移动',{exact:true}).waitFor();
    assert.equal(await page.locator('#report-preview').innerText(),'历史项目周报，不移动');
    assert.equal(await page.locator('#research-report').getAttribute('data-api'),`/api/project/${cfg.pid}/reports`);
    await page.goto(cfg.origin + `/project/${cfg.pid}/records`);
    assert.equal(await page.locator('.report-tabs').count(), 0);
    await follow(page, page.getByRole('link', {name:'日报与周报', exact:true}));
    assert.equal(new URL(page.url()).pathname, '/reports');
    assert.equal(await page.locator('.console-navigation a[aria-current=page]').innerText(), '日报与周报');
    await follow(page, page.getByRole('link', {name:/日报 · 2026-09-19/}));
    await page.locator('#report-source').waitFor({state:'visible'});
    const heading = '### 1. 编辑器：允许引入成熟组件，不从零自研，';
    await paste(heading);
    await page.locator('#report-preview h3').waitFor();
    assert.equal(await page.locator('#report-preview h3').innerText(), heading.slice(4));
    assert.ok(await page.locator('#report-preview h3').evaluate(e => Number(getComputedStyle(e).fontWeight) >= 600));
    await saved();
    await page.screenshot({path:path.join(cfg.evidence, '01-markdown-paste.png'), fullPage:true});
    await page.locator('#report-edit').click();
    assert.equal(await page.locator('#report-source').inputValue(), heading);
    await page.locator('#report-source').press('Control+End');
    await paste('\n\n**结论**\n\n|方法|状态|\n|---|---|\n|A|待验证|\n\n```python\nx = 1\n```');
    assert.equal(await page.locator('#report-source').isVisible(), true);
    await page.locator('#report-preview-mode').click();
    await page.locator('#report-preview strong').waitFor();
    assert.equal(await page.locator('#report-preview table').count(), 1);
    assert.equal(await page.locator('#report-preview pre').count(), 1);
    await saved();
    // Image at the selection, while upload is delayed and typing continues.
    await page.locator('#report-edit').click();
    await page.locator('#report-source').fill('实验说明\n\n下一步');
    await page.locator('#report-source').evaluate(e => {e.setSelectionRange(6,6); e.dispatchEvent(new Event('select'));});
    let release; const gate = new Promise(r => release = r);
    await page.route('**/reports/upload', async route => {await gate; await route.continue();});
    await page.evaluate(png => {
      const bytes = Uint8Array.from(atob(png), c => c.charCodeAt(0));
      const data = new DataTransfer(); data.items.add(new File([bytes], 'shot.png', {type:'image/png'}));
      document.querySelector('#report-source').dispatchEvent(new ClipboardEvent('paste', {clipboardData:data,bubbles:true,cancelable:true}));
    }, cfg.png);
    await page.waitForFunction(() => document.querySelector('#report-source').value.includes('<!--report-upload:'));
    assert.equal(await page.locator('#report-confirm').isDisabled(), true);
    await page.locator('#report-source').press('Control+End');
    await page.locator('#report-source').pressSequentially(' ABC');
    release();
    await page.waitForFunction(() => document.querySelector('#report-source').value.includes('![截图]'));
    await page.unroute('**/reports/upload');
    const imageBody = await page.locator('#report-source').inputValue();
    assert.ok(imageBody.indexOf('实验说明') < imageBody.indexOf('![截图]'));
    assert.ok(imageBody.indexOf('![截图]') < imageBody.indexOf('下一步'));
    assert.ok(imageBody.endsWith(' ABC'));
    await saved();
    await page.locator('#report-preview-mode').click();
    await page.locator('#report-preview img').waitFor();
    assert.equal(await page.locator('#report-preview img').count(),1);
    await page.screenshot({path:path.join(cfg.evidence, '02-image-position.png'), fullPage:true});
    // Quoted human annotations travel with drafts and immutable formal versions.
    await page.locator('#report-edit').click();await page.locator('#report-source').evaluate(e=>{e.setSelectionRange(0,4);e.dispatchEvent(new Event('select'));});
    await page.locator('#report-comment').click();await page.getByLabel('批注内容').fill('人工批注：等待硬件验证。');
    await page.locator('#report-dialog-actions').getByRole('button',{name:'添加',exact:true}).click();await saved();
    assert.equal(await page.locator('#report-comments blockquote').innerText(),'实验说明');
    // Confirm, edit revision, preserve v1.
    await page.locator('#report-confirm').click();
    await page.waitForFunction(() => document.querySelector('#report-state').textContent === '正式版');
    await page.locator('#report-edit').click();
    await page.locator('#report-source').fill('第二版结论');
    await saved();
    const v1 = await (await context.request.get(cfg.origin + api + '?version=1')).json();
    assert.equal(v1.body, imageBody);assert.equal(v1.comments[0].text,'人工批注：等待硬件验证。');assert.equal(v1.comments[0].quote,'实验说明');
    await page.locator('#report-confirm').click();
    await page.waitForFunction(() => document.querySelector('#report-state').textContent === '正式版');
    const current = await (await context.request.get(cfg.origin + api)).json();
    assert.equal(current.metadata.versions.length,2);assert.deepEqual(current.comments,v1.comments);
    await page.reload();await page.locator('#report-comments').getByText('人工批注：等待硬件验证。',{exact:true}).waitFor();
    // Returning from a document preserves the URL's filters and selected row.
    const filtered = new URL('/reports', cfg.origin);
    filtered.search = new URLSearchParams({view:'daily', project:cfg.pid, status:'formal', q:'2026-09-19'});
    await page.goto(filtered.href);
    assert.equal(await page.locator('.report-row').count(), 1);
    await page.locator('.report-row').click(); await saved();
    await follow(page, page.getByRole('link', {name:'返回列表',exact:true}));
    const returned = new URL(page.url());
    for (const [name, value] of filtered.searchParams) assert.equal(returned.searchParams.get(name), value);
    assert.equal(returned.hash, '#report-row-0');
    assert.equal(await page.locator('.report-row').count(), 1);
    await page.goto(url + '?return=' + encodeURIComponent('https://invalid.example/reports'));
    await saved();
    assert.equal(new URL(await page.getByRole('link',{name:'返回列表',exact:true}).getAttribute('href'), cfg.origin).origin, cfg.origin);
    // Candidate does not replace confirmed text until explicit adoption.
    await page.goto(cfg.origin + `/reports/daily/2026-09-18`);
    await page.locator('#report-candidate').waitFor({state:'visible'});
    await page.locator('#report-preview').getByText('正式版：实测尚未完成。').waitFor();
    await page.locator('#report-candidate').click();
    await page.screenshot({path:path.join(cfg.evidence, '03-formal-candidate.png'), fullPage:true});
    await page.getByRole('button', {name:'确认采用新稿'}).click();
    await page.waitForFunction(() => document.querySelector('#report-state').textContent === '修订草稿');
    await page.waitForFunction(() => document.querySelector('#report-preview').textContent.includes('候选'));
    assert.match(await page.locator('#report-preview').innerText(), /候选/);
    const old = await (await context.request.get(cfg.origin + `/api/reports/daily/2026-09-18?version=1`)).json();
    assert.equal(old.body, '正式版：实测尚未完成。');
    // Undo/redo a whole paste, retain native textarea content.
    await page.locator('#report-edit').click();
    await page.locator('#report-source').press('Control+End');
    const before = await page.locator('#report-source').inputValue();
    await paste('\n补充内容');
    await page.waitForFunction(() => document.querySelector('#report-source').value.includes('补充内容'));
    await page.locator('#report-source').press('Control+z');
    assert.equal(await page.locator('#report-source').inputValue(), before);
    await page.locator('#report-source').press('Control+Shift+z');
    assert.equal(await page.locator('#report-source').inputValue(), before+'\n补充内容');
    await saved();
    // Real content conflict between two browser tabs: no silent overwrite.
    await page.goto(url);
    await page.locator('#report-edit').click();
    await page.locator('#report-source').waitFor({state:'visible'});
    const second = await context.newPage(); await second.goto(url);
    await second.locator('#report-edit').click();
    await second.locator('#report-source').waitFor({state:'visible'});
    await page.locator('#report-source').fill('标签页 A'); await saved();
    await second.locator('#report-source').fill('标签页 B');
    await second.getByRole('heading', {name:'正文发生冲突'}).waitFor();
    assert.equal(await second.locator('#report-source').inputValue(),'标签页 B');
    await second.getByRole('button', {name:'取消',exact:true}).click();
    await second.close({runBeforeUnload:false});
    // Narrow layout and unchanged old records entrance.
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.goto(cfg.origin + '/settings');
    await page.locator('#report-settings').evaluate(e => e.open = true);
    await page.waitForFunction(() => document.querySelector('#report-connection').textContent.includes('已暂停'));
    assert.equal(await page.locator('#report-daily-time').inputValue(), '');
    assert.equal(await page.locator('#report-owner').count(), 0);
    // Explicit capture consent in the temporary project, never real research data.
    assert.equal(await page.locator('#report-capture').isChecked(), false);
    await page.locator(`input[name=report-project][value="${cfg.pid}"]`).check();
    await page.locator('#report-capture').check();
    await page.getByRole('button', {name:'保存设置', exact:true}).click();
    await page.waitForFunction(() => document.querySelector('#report-settings-status').textContent.includes('已保存'));
    let settings = await (await context.request.get(cfg.origin + '/api/reports/settings')).json();
    assert.deepEqual(settings.capture_hosts, ['codex']);
    assert.match(settings.workflow_cli, /research_cycle\.py$/);
    assert.equal(settings.enabled, false); // Checking capture does not silently enable schedules.
    await page.locator('#report-capture').uncheck();
    await page.getByRole('button', {name:'保存设置', exact:true}).click();
    await page.waitForFunction(async () => (await (await fetch('/api/reports/settings')).json()).capture_hosts.length === 0);
    // A running Python process may still serve the previous HTML/API after source updates.
    // New static JS must keep that page usable until the user restarts it.
    await page.route(cfg.origin + '/settings', async route => {
      const response = await route.fetch();
      const html = (await response.text()).replace(/<label[^>]*><input[^>]*id="report-capture"[^>]*>[^<]*<\/label>/, '');
      await route.fulfill({response, body:html});
    });
    let legacyPayload;
    await page.route(cfg.origin + '/api/reports/settings', async route => {
      if (route.request().method() === 'POST') legacyPayload = route.request().postDataJSON();
      const response = await route.fetch(), value = await response.json();
      delete value.capture_hosts; delete value.workflow_cli; delete value.workflow_skill; delete value.report_scope;
      await route.fulfill({response, json:value});
    });
    await page.goto(cfg.origin + '/settings');
    await page.locator('#report-settings').evaluate(e => e.open = true);
    await page.waitForFunction(() => document.querySelector('#report-connection').textContent.includes('已暂停'));
    assert.equal(await page.locator('#report-capture').count(), 0);
    await page.getByRole('button', {name:'保存设置', exact:true}).click();
    await page.waitForFunction(() => document.querySelector('#report-settings-status').textContent.includes('旧版本'));
    assert.equal(legacyPayload, undefined);
    await page.locator('#report-copy-enable').click();
    await page.waitForFunction(() => document.querySelector('#report-settings-status').textContent.includes('旧版本'));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed:true, checks:['project context across navigation/filter/paging/create/two tabs/legacy/invalid links','multi-project weekly and legacy deep links','workspace reports navigation without project ownership','markdown paste h3/bold/table/code','cursor screenshot + concurrent typing','formal history and annotations','return preserves filters and row','candidate protection','undo redo','two-tab conflict','narrow layout','settings disabled by default','capture consent grant/revoke','previous-server compatibility'], screenshots:cfg.evidence}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
