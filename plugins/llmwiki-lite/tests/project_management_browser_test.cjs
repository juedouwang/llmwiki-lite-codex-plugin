const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path'),fs=require('node:fs'),os=require('node:os');
(async()=>{
 const [origin,a,z]=process.argv.slice(2);
 const browser=await chromium.launch({headless:true,channel:process.env.LLMWIKI_BROWSER_CHANNEL||'chrome'});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`${origin}/project/${z}/records`);
  const selected=()=>page.getAttribute('body','data-workbench-project');
  await page.locator('.console-project-switcher summary').click();
  assert.equal(await page.locator('.console-project-menu a').count(),2);
  await page.locator('.console-topbar').click();
  assert.equal(await page.locator('.console-project-switcher').getAttribute('open'),null);
  await page.locator('.console-project-switcher summary').click();await page.keyboard.press('Escape');
  assert.equal(await page.locator('.console-project-switcher').getAttribute('open'),null);
  await page.locator('[data-workbench-nav="settings"]').click();
  await page.waitForURL('**/settings?*');assert.equal(await selected(),z);
  await page.locator('.console-sidebar-brand').click();await page.waitForURL('**/projects?*');
  assert.equal(await selected(),z);
  const row=page.locator(`.project-row[data-project-id="${z}"]`);
  await row.click({button:'right'});
  await page.locator('[data-project-action="rename"]').click();
  await page.locator('#project-new-name').fill('重命名后的工作台');
  await page.locator('#project-name-form button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('#project-preferences-status').textContent==='名称已更新');
  assert.equal(await row.locator('.row-title').innerText(),'重命名后的工作台');
  assert.equal(await page.locator('.console-project-label b').innerText(),'重命名后的工作台');
  await page.locator('[data-workbench-nav="records"]').click();await page.waitForURL(`**/project/${z}/records`);
  assert.equal(await page.locator('.console-project-label b').innerText(),'重命名后的工作台');
  await page.locator('.console-sidebar-brand').click();await page.waitForURL('**/projects?*');
  assert.equal(await row.locator('.row-title').innerText(),'重命名后的工作台');
  // Default preference changes do not steal the current project.
  await page.locator(`.project-row[data-project-id="${a}"] .project-default`).click();
  await page.waitForFunction(()=>document.querySelector('#project-preferences-status').textContent==='已保存');
  await page.goto(origin+'/');assert.ok(page.url().endsWith(`/project/${z}/todos`));
  const freshContext=await browser.newContext();const fresh=await freshContext.newPage();await fresh.goto(origin+'/');
  assert.ok(fresh.url().endsWith(`/project/${a}/todos`));await freshContext.close();
  await page.locator('.console-sidebar-brand').click();await page.waitForURL('**/projects?*');
  // Failed rename retains old title and an actionable dialog.
  await row.locator('.project-more').click();await page.locator('[data-project-action="rename"]').click();
  await page.locator('#project-new-name').fill('不会保存');
  await page.route('**/api/projects/*/rename',route=>route.fulfill({status:500,contentType:'application/json',body:'{"ok":false,"error":"模拟保存失败"}'}));
  await page.locator('#project-name-form button[type="submit"]').click();
  await page.locator('#project-name-dialog .project-dialog-status').filter({hasText:'模拟保存失败'}).waitFor();
  assert.equal(await row.locator('.row-title').innerText(),'重命名后的工作台');
  await page.locator('#project-name-dialog [data-project-cancel]').click();await page.unroute('**/api/projects/*/rename');
  // Keyboard invokes the same menu. Non-current removal keeps current context.
  const other=page.locator(`.project-row[data-project-id="${a}"]`);
  await other.locator('.project-more').focus();await page.keyboard.press('Shift+F10');
  await page.locator('[data-project-action="remove"]').click();
  await page.locator('#project-remove-dialog [data-project-cancel]').click();
  assert.equal(await other.count(),1);
  await other.locator('.project-more').click();await page.locator('[data-project-action="remove"]').click();
  await page.locator('#project-remove-confirm').click();
  await page.waitForFunction(id=>!document.querySelector(`.project-row[data-project-id="${id}"]`),a);
  assert.equal(await selected(),z);assert.equal(await page.locator('.console-project-menu a').count(),1);
  const out=path.join(os.tmpdir(),'llmwiki-task-refinements');fs.mkdirSync(out,{recursive:true});
  await row.locator('.project-more').click();await page.screenshot({path:path.join(out,'project-menu.png')});
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:420,height:900});
  await row.locator('.project-more').click();
  const menu=await page.locator('#project-context-menu').boundingBox();assert.ok(menu.x>=0&&menu.x+menu.width<=421);
  assert.deepEqual(errors,[]);
  console.log('PASS project context / outside & Escape dismissal / rename immediate & persisted / keyboard menu / delete cancel & confirm / failure recovery / mobile geometry');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
