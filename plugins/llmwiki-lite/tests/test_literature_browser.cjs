/* Opt-in isolated browser regression; uses an already installed Playwright. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
(async()=>{
 const cfg=JSON.parse(process.argv[2]);fs.mkdirSync(cfg.evidence,{recursive:true});
 const browser=await chromium.launch({headless:true,channel:process.env.LLMWIKI_BROWSER_CHANNEL||'chrome'});
 const context=await browser.newContext({viewport:{width:1280,height:900}}), page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=cfg.origin+`/project/${cfg.pid}/literature`, api=`/api/project/${cfg.pid}/literature/`;
 const root=()=>page.locator('#literature');

 try{
  await page.goto(base);await page.getByText('还没有收藏文献。点击“添加文献”粘贴地址即可。').waitFor();
  await root().getByRole('button',{name:'添加文献',exact:true}).click();
  const add=page.locator('[data-literature-form=add]');
  assert.equal(await add.locator('[name=locator]').evaluate(e=>e===document.activeElement),true);
  await add.locator('[name=locator]').fill('javascript:alert(1)');await add.getByRole('button',{name:'收藏',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-literature-form=add] [role=alert]').textContent.length>0);
  assert.equal(await add.locator('[name=locator]').inputValue(),'javascript:alert(1)');
  await page.screenshot({path:path.join(cfg.evidence,'literature-error.png'),fullPage:true});
  await add.locator('[name=locator]').fill('10.1234/browser');
  await add.locator('[name=locator]').dispatchEvent('compositionstart');
  await add.locator('[name=locator]').press('Enter');
  assert.equal(await root().locator('[data-item]').count(),0);
  await add.locator('[name=locator]').dispatchEvent('compositionend');
  await add.locator('[name=locator]').press('Enter');
  await root().locator('[data-item]').waitFor();
  assert.equal(await root().locator('[data-item]').count(),1);
  await page.reload();assert.equal(await root().locator('[data-item]').count(),1);
  await page.locator('#literature-search').fill('browser');
  await page.waitForURL(/q=browser/);await page.waitForTimeout(250);
  await page.screenshot({path:path.join(cfg.evidence,'literature-list.png'),fullPage:true});
  await root().locator('[data-item]').click();await root().getByRole('heading',{name:'10.1234/browser',exact:true}).waitFor();
  const itemId=await root().locator('[data-item-id]').getAttribute('data-item-id');
  assert.equal(await root().getByRole('link',{name:'原地址',exact:true}).getAttribute('rel'),'noopener noreferrer');
  await root().getByRole('button',{name:'编辑资料',exact:true}).click();
  const edit=page.locator('[data-literature-form=edit]');
  await edit.locator('[name=title]').fill('人工论文标题');await edit.locator('[name=authors]').fill('作者甲\n作者乙');await edit.locator('[name=year]').fill('2026');
  await edit.getByRole('button',{name:'保存',exact:true}).click();await root().getByRole('heading',{name:'人工论文标题',exact:true}).waitFor();
  await root().getByText('时间与来源',{exact:true}).click();await root().getByText('本次明确收藏',{exact:true}).first().waitFor();
  await page.screenshot({path:path.join(cfg.evidence,'literature-detail.png'),fullPage:true});
  // Two actual browser pages: a new source makes the first editor stale.
  await root().getByRole('button',{name:'编辑资料',exact:true}).click();await edit.locator('[name=title]').fill('保留未保存输入');
  const second=await context.newPage();await second.goto(base);
  const duplicate=await second.evaluate(async api=>{const r=await fetch(api+'add',{method:'POST',headers:{'Content-Type':'application/json','X-Literature-Request':'1'},body:JSON.stringify({locator:'10.1234/browser',request_id:crypto.randomUUID().replaceAll('-','')})});return r.json();},api);
  assert.equal(duplicate.item_id,itemId);await second.close();
  await edit.getByRole('button',{name:'保存',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-literature-form=edit] [role=alert]').textContent.includes('已变化'));
  assert.equal(await edit.locator('[name=title]').inputValue(),'保留未保存输入');
  await page.screenshot({path:path.join(cfg.evidence,'literature-conflict.png'),fullPage:true});
  await root().getByRole('link',{name:'返回文献列表',exact:true}).click();assert.match(page.url(),/q=browser/);
  // Search identifiers, then title after returning; no input is lost.
  await page.locator('#literature-search').fill('人工论文');await page.waitForURL(/q=/);await page.waitForTimeout(350);
  await root().locator('[data-item]').click();await root().getByText('更多操作',{exact:true}).click();
  page.once('dialog',d=>d.accept());await root().getByRole('button',{name:'从文献清单移除',exact:true}).click();await page.waitForURL(/literature\?/);
  assert.equal(await root().locator('[data-item]').count(),0);
  await root().getByRole('button',{name:'添加文献',exact:true}).click();
  await add.locator('[name=locator]').fill('10.1234/browser');await add.locator('[name=locator]').press('Enter');
  await root().locator('[data-item]').waitFor();assert.ok((await root().locator('[data-item]').getAttribute('href')).includes(itemId));
  await page.goto(base);await root().getByRole('button',{name:'添加文献',exact:true}).click();
  await add.locator('[name=locator]').fill('cancel input');await add.locator('[name=locator]').press('Escape');assert.equal(await add.isVisible(),false);
  // Original and notes were created only inside the temporary fixture by the runner.
  await page.goto(cfg.origin+cfg.readingUrl);await root().getByRole('link',{name:'原文：paper.pdf',exact:true}).waitFor();
  const note=await root().getByRole('link',{name:'笔记：note.md',exact:true}).getAttribute('href');
  const read=await root().getByRole('link',{name:'原文：paper.pdf',exact:true}).getAttribute('href');
  const compare=await root().getByRole('link',{name:'对照阅读：note.md',exact:true}).getAttribute('href');
  for(const link of [note,read,compare]){const response=await page.goto(cfg.origin+link);assert.equal(response.status(),200);}
  await page.goto(base);await root().getByText('收录详情',{exact:true}).click();await page.getByText(/文献自动收录已关闭/).waitFor();
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,evidence:cfg.evidence,checked:['manual','IME','invalid-input','search-back','edit','two-window-conflict','remove','explicit-restore','read-note-compare','collection-status']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
