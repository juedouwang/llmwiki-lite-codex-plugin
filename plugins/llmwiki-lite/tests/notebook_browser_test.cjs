/* Optional browser regression: LLMWIKI_PLAYWRIGHT points to an installed Playwright module. */
const { chromium } = require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const os = require('node:os'), path = require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true, ...(process.env.LLMWIKI_BROWSER_CHANNEL ? {channel:process.env.LLMWIKI_BROWSER_CHANNEL} : {})});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  const origin=process.argv[2],pid=process.argv[3];
  await page.goto(`${origin}/project/${pid}/records`);
  await page.getByRole('link',{name:'＋ 手动记录',exact:true}).click();
  await page.locator('#nb-title').fill('低纹理配准 · 第一轮实验');
  await page.locator('.nb-input').fill('## 实验观察\n\n**关键发现**：弱纹理场景下，增加几何约束后误差明显下降。\n\n- 基线误差：0.082\n- 改进误差：0.046\n- 待验证：跨场景泛化');
  await page.locator('#nb-tags').fill('配准，实验，待验证');
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  const url=page.url(),nid=url.split('/').pop();
  assert.equal(await page.locator('.nb-block-time, #nb-timestamps').count(),0);
  assert.ok(!(await page.locator('.nb-paper').innerText()).includes('最近修改：'));
  assert.equal(await page.locator('#nb-status').textContent(),'已保存');
  await page.getByLabel('笔记菜单').click();
  await page.getByRole('button',{name:'笔记信息',exact:true}).click();
  const firstTime=await page.locator('#nb-info-times').textContent();
  assert.match(firstTime,/创建：.*\d{2}:\d{2}:\d{2} UTC\+08:00/);
  assert.match(firstTime,/最近修改：/);
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  const downloadEvent=page.waitForEvent('download');
  await page.getByLabel('笔记菜单').click();
  await page.locator('#nb-export').click();
  const download=await downloadEvent;
  const exported=require('node:fs').readFileSync(await download.path(),'utf8');
  assert.ok(exported.startsWith('---\ntype: research-notebook\nrecorded_at:'));
  assert.ok(!exported.includes('创建：')&&!exported.includes('最近修改：'));
  const reader=await context.newPage();
  await reader.goto(`${origin}/project/${pid}/page/records/manual/${nid}.md`);
  assert.equal(await reader.locator('details.frontmatter[open]').count(),0);
  assert.ok(!(await reader.locator('article.document').innerText()).includes('最近修改：'));
  await reader.getByText('笔记信息',{exact:true}).click();
  assert.ok(await reader.locator('details.frontmatter[open]').count());
  await reader.close();
  await page.reload();await page.locator('#nb-title:not([disabled])').waitFor();
  await page.getByLabel('笔记菜单').click();
  await page.getByRole('button',{name:'笔记信息',exact:true}).click();
  assert.equal(await page.locator('#nb-info-times').textContent(),firstTime);
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'预览',exact:true}).click();
  await page.locator('.nb-preview strong').waitFor();
  assert.equal(await page.locator('.nb-preview strong').textContent(),'关键发现');
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  // Generate a real screenshot for upload rather than relying on a malformed fixture.
  const png=await page.locator('.nb-paper').screenshot();
  await page.locator('#nb-bottom .nb-plus').click();
  await page.locator('#nb-bottom').getByRole('button',{name:'图片',exact:true}).click();
  await page.locator('#nb-file').setInputFiles({name:'实验截图.png',mimeType:'image/png',buffer:png});
  await page.locator('.nb-image').waitFor();
  const imageCell=page.locator('.nb-image').locator('xpath=ancestor::section[1]');
  await imageCell.locator('.nb-input').fill('图 1 · 实验界面截图。记录当前参数，方便后续复现。');
  await imageCell.getByRole('button',{name:'批注',exact:true}).click();
  await imageCell.locator('.nb-comment textarea').fill('这组结果还需要增加夜间场景测试，暂不作为最终结论。');
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  await imageCell.getByRole('button',{name:'放大截图'}).click();
  await page.locator('#nb-dialog[open] .nb-lightbox').waitFor();
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  // Paste image from clipboard payload.
  await page.locator('#nb-title').focus();
  await page.evaluate(b64=>{
    const bytes=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));const dt=new DataTransfer();
    dt.items.add(new File([bytes],'clipboard.png',{type:'image/png'}));
    document.querySelector('#nb-title').dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:dt}));
  },png.toString('base64'));
  await page.waitForFunction(()=>document.querySelectorAll('.nb-image').length===2);
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  // Delete/recover without losing comments; then reload from disk.
  await page.locator('.nb-cell').last().getByLabel('块菜单').click();
  await page.locator('.nb-cell').last().getByRole('button',{name:'删除',exact:true}).click();
  assert.equal(await page.locator('.nb-image').count(),1);
  await page.locator('#nb-undo').click();assert.equal(await page.locator('.nb-image').count(),2);
  await page.locator('#nb-save').click();
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  await page.reload();await page.locator('.nb-image').first().waitFor();
  assert.equal(await page.locator('.nb-image').count(),2);
  assert.equal(await page.locator('.nb-comment textarea').first().inputValue(),'这组结果还需要增加夜间场景测试，暂不作为最终结论。');
  // Timeline links reopen the editable notebook, not a read-only document.
  await page.goto(`${origin}/project/${pid}/records`);
  await page.locator('.timeline-card').filter({hasText:'低纹理配准 · 第一轮实验'}).click();
  await page.locator('#nb-title:not([disabled])').waitFor();
  assert.equal(await page.locator('#nb-title').inputValue(),'低纹理配准 · 第一轮实验');
  // New text with notebook keyboard shortcut.
  await page.locator('.nb-input').last().focus();await page.keyboard.press('Control+Enter');
  assert.equal(await page.locator('.nb-cell').count(),4);
  await page.locator('.nb-input').last().fill('下一步：复现实验、补充消融，并保存配置文件。');
  await page.keyboard.press('Control+s');
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  await page.getByLabel('笔记菜单').click();
  await page.locator('#nb-history').click();await page.locator('.nb-history-item').first().waitFor();assert.ok(await page.locator('.nb-history-item').count()>0);
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  // Screenshots go to the system temp dir, never the repository root.
  await page.screenshot({path:process.env.LLMWIKI_SCREENSHOT || path.join(os.tmpdir(),'llmwiki-notebook-desktop.png'),fullPage:true});
  // Direct clipboard flow: no + menu, picker or blank spacer required.
  const pastePage=await context.newPage();await pastePage.goto(`${origin}/project/${pid}/notebook`);
  await pastePage.locator('#nb-title:not([disabled])').waitFor();
  let pickers=0;pastePage.on('filechooser',()=>pickers++);
  const pasteImages=async (count=1,selector='body')=>pastePage.evaluate(({b64,count,selector})=>{
    const bytes=Uint8Array.from(atob(b64),c=>c.charCodeAt(0)),data=new DataTransfer();
    for(let n=0;n<count;n++)data.items.add(new File([bytes],`image-${n}.png`,{type:'image/png'}));
    const target=document.querySelector(selector);target.focus();target.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data}));
  },{b64:png.toString('base64'),count,selector});
  // Focus on navigation/main content used to silently reject the screenshot.
  await pastePage.locator('#main-content').focus();await pasteImages();
  await pastePage.waitForFunction(()=>document.querySelectorAll('.nb-image').length===1);
  assert.equal(await pastePage.locator('.nb-cell').count(),1);assert.equal(pickers,0);
  await pastePage.locator('.nb-input').fill('截图直接作为第一块，不留下空白文本块');
  await pastePage.locator('#nb-bottom .nb-plus').click();await pastePage.locator('#nb-bottom').getByRole('button',{name:'图片',exact:true}).click();
  assert.equal(pickers,0);await pasteImages(2);
  await pastePage.waitForFunction(()=>document.querySelectorAll('.nb-image').length===3);
  assert.equal(await pastePage.locator('.nb-cell').count(),3);assert.equal(pickers,0);
  await pastePage.locator('.nb-continue').click();
  assert.equal(await pastePage.locator('.nb-input').last().evaluate(e=>e===document.activeElement),true);
  await pastePage.locator('.nb-input').last().fill('普通文字粘贴仍正常');
  await pastePage.locator('#nb-tags').focus();await pasteImages(1,'#nb-tags');
  assert.equal(await pastePage.locator('.nb-image').count(),3);
  // M-01: the title keeps the normal text paste. Copying from a chat window or a
  // document yields text + image; the image used to win and the title lost the text.
  const hijacked=await pastePage.evaluate(b64=>{
    const bytes=Uint8Array.from(atob(b64),c=>c.charCodeAt(0)),data=new DataTransfer();
    data.setData('text/plain','配准误差 0.046');
    data.items.add(new File([bytes],'rich.png',{type:'image/png'}));
    const target=document.querySelector('#nb-title');target.focus();
    const event=new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data});
    target.dispatchEvent(event);return event.defaultPrevented;
  },png.toString('base64'));
  assert.equal(hijacked,false,'a text+image paste into the title must not be hijacked');
  assert.equal(await pastePage.locator('.nb-image').count(),3,'a text+image paste into the title must not add an image block');
  assert.equal((await pastePage.locator('#nb-title').inputValue()).includes('rich.png'),false);
  // Drop targets the pointed-at empty text cell rather than the stale active cell.
  await pastePage.locator('.nb-input').last().fill('');
  await pastePage.locator('.nb-cell').first().locator('.nb-input').focus();
  await pastePage.evaluate(b64=>{const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob(b64),c=>c.charCodeAt(0))],'drop.png',{type:'image/png'}));document.querySelector('.nb-cell:last-child .nb-input').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));},png.toString('base64'));
  await pastePage.waitForFunction(()=>document.querySelectorAll('.nb-image').length===4);
  assert.equal(await pastePage.locator('.nb-cell').count(),4);
  await pastePage.waitForFunction(()=>document.querySelector('#nb-status').textContent==='已保存');
  await pastePage.reload();await pastePage.locator('.nb-image').first().waitFor();assert.equal(await pastePage.locator('.nb-image').count(),4);
  await pastePage.close();
  // Autosave race: second tab wins, first retains draft and cannot overwrite it.
  const other=await context.newPage();await other.goto(url);await other.locator('#nb-title').fill('另一页面的标题');
  await other.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  await page.locator('#nb-title').fill('本页未合并的标题');await page.locator('#nb-save').click();
  await page.getByRole('button',{name:'保存为新笔记',exact:true}).waitFor();
  assert.equal(await page.locator('#nb-title').isDisabled(),true);
  await page.getByRole('button',{name:'对比服务器版本',exact:true}).click();
  assert.ok((await page.locator('.nb-version-text').textContent()).includes('另一页面的标题'));
  await page.getByRole('button',{name:'关闭',exact:true}).click();
  await page.getByRole('button',{name:'保存为新笔记',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  assert.notEqual(page.url(),url);
  const original=await (await context.request.get(`${origin}/api/project/${pid}/notebook/${nid}`)).json();
  assert.equal(original.document.title,'另一页面的标题');
  // Offline draft recovery.
  await page.route('**/api/**',route=>route.abort());
  await page.locator('#nb-title').fill('离线草稿保留');await page.locator('#nb-save').click();
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent==='未保存到 Wiki');
  page.on('dialog',dialog=>dialog.accept());
  await page.unroute('**/api/**');await page.reload();
  await page.getByRole('button',{name:'恢复草稿',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#nb-status').textContent.startsWith('已保存'));
  assert.equal(await page.locator('#nb-title').inputValue(),'离线草稿保留');
  // Narrow viewport is usable without horizontal overflow.
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  const unexpected=errors.filter(e=>!e.includes('409')&&!e.includes('ERR_FAILED')&&!e.includes('404'));
  assert.deepEqual(unexpected,[]);
  await browser.close();console.log('Notebook browser checks passed: blocks / preview / upload / direct paste / multi-image paste / empty-block reuse / drop target / comments / undo / persistence / shortcuts / history / conflict / offline draft / mobile.');
})().catch(e=>{console.error(e);process.exit(1);});

