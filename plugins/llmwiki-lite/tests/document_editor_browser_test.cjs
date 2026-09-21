/* No real records, dependencies or visible terminals are created by this suite. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const cfg=JSON.parse(process.argv[2]);
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext({viewport:{width:1280,height:960},permissions:['clipboard-read','clipboard-write']}), page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(12000);
 const base=`${cfg.origin}/project/${cfg.pid}`, noteId='b'.repeat(32), api=`${cfg.origin}/api/project/${cfg.pid}/notebook/${noteId}`;
 const body=prefix=>page.locator(`#${prefix}-source`).inputValue();
 const live=prefix=>page.locator(`#${prefix}-live`);
 const saved=prefix=>page.waitForFunction(p=>document.getElementById(p+'-save-state').textContent==='已保存'&&!document.getElementById(p+'-uploads').children.length,prefix);
 const read=async url=>(await context.request.get(url)).json();
 async function paste(prefix,{text,png}={}){
   await live(prefix).waitFor({state:'visible'});await live(prefix).focus();const previous=await body(prefix);
   await page.evaluate(async({text,png})=>{
     if(png){const blob=new Blob([Uint8Array.from(atob(png),c=>c.charCodeAt(0))],{type:'image/png'});await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);}
     else await navigator.clipboard.writeText(text);
   },{text,png});
   await live(prefix).press('Control+v');
   await page.waitForFunction(({prefix,previous})=>document.getElementById(prefix+'-source').value!==previous,{prefix,previous});
 }
 async function imageCount(prefix,n){await page.waitForFunction(({prefix,n})=>{const images=[...document.querySelectorAll('#'+prefix+'-live img')];return images.length===n&&images.every(i=>i.complete&&i.naturalWidth>0);},{prefix,n});}
 try{
   cfg.png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=400;c.height=140;const g=c.getContext('2d');g.fillStyle='#edf5f1';g.fillRect(0,0,400,140);g.strokeStyle='#377857';g.lineWidth=3;g.beginPath();for(let x=12;x<390;x++)g.lineTo(x,70+32*Math.sin(x/35));g.stroke();return c.toDataURL('image/png').split(',')[1];});
   // Empty notebook: no redundant save/code/comment buttons; paste stays editing.
   await page.goto(base+'/notebook/'+noteId);await live('nb').waitFor({state:'visible'});
   assert.equal(await page.locator('#nb-save,#nb-code,#nb-comment').count(),0);
   await paste('nb',{text:'前文\n\n```python\nx = 1\n```\n\n后文'});await saved('nb');
   assert.equal(await body('nb'),'前文\n\n```python\nx = 1\n```\n\n后文');
   // Cache an actual rendered records list: optimistic titles must arrive before
   // the delayed save, through the same workbench lifecycle used by navigation.
   await page.evaluate(async url=>{const html=await (await fetch(url)).text();window.editorTestList=new DOMParser().parseFromString(html,'text/html').querySelector('#main-content');document.dispatchEvent(new CustomEvent('workbench:leave',{detail:{root:window.editorTestList}}));},base+'/records');
   let titleRelease;const titleGate=new Promise(r=>titleRelease=r);
   await page.route(api,async route=>{if(route.request().method()==='POST')await titleGate;await route.continue();});
   await page.locator('#nb-title').fill('即时标题');assert.equal(await page.title(),'即时标题');
   assert.ok(await page.evaluate(()=>window.editorTestList.textContent.includes('即时标题')));
   titleRelease();
   await saved('nb');await page.unroute(api);assert.equal((await read(api)).document.title,'即时标题');
   await page.evaluate(()=>{document.dispatchEvent(new CustomEvent('workbench:dispose',{detail:{root:window.editorTestList}}));delete window.editorTestList;});
   await live('nb').press('Control+End');await paste('nb',{png:cfg.png});await imageCount('nb',1);await saved('nb');
   assert.match(await body('nb'),/!\[\]\(\.\.\/assets\/[a-f0-9]{64}\.png\)/);
   assert.ok(!(await body('nb')).includes('截图'));assert.equal(await live('nb').locator('figcaption').count(),0);
   assert.equal(await live('nb').evaluate(e=>document.activeElement===e),true);
   await paste('nb',{png:cfg.png});await imageCount('nb',2);await saved('nb');
   const beforeText=await body('nb');await live('nb').pressSequentially('hello');
   for(let i=0;i<5;i++)await live('nb').press('Control+z');assert.equal(await body('nb'),beforeText);
   for(let i=0;i<5;i++)await live('nb').press('Control+Shift+z');assert.equal(await body('nb'),beforeText+'hello');
   await live('nb').press('Enter');await live('nb').pressSequentially('tail');assert.ok((await body('nb')).endsWith('hello\ntail'));
   await saved('nb');
   // A delayed image resolves at its original cursor while typing continues.
   let release;let gate=new Promise(r=>release=r);
   await page.route('**/notebook/upload',async route=>{await gate;await route.continue();});
   await live('nb').press('Control+Home');await live('nb').press('ArrowRight');await paste('nb',{png:cfg.png});
   await page.waitForFunction(()=>document.querySelector('#nb-source').value.includes('<!--report-upload:'));
   await live('nb').pressSequentially('HERE');release();await imageCount('nb',3);
   await live('nb').pressSequentially('AFTER');assert.match(await body('nb'),/^前\n!\[\]\([^)]*\)\nHEREAFTER文/);
   await page.unroute('**/notebook/upload');await saved('nb');
   // Real Chromium composition via CDP, including an image completing mid-IME.
   gate=new Promise(r=>release=r);await page.route('**/notebook/upload',async route=>{await gate;await route.continue();});
   await live('nb').press('Control+End');await paste('nb',{png:cfg.png});
   const cdp=await context.newCDPSession(page);
   await live('nb').evaluate(e=>{e.dataset.compositions='0';e.addEventListener('compositionstart',()=>e.dataset.compositions=String(Number(e.dataset.compositions)+1));});
   await cdp.send('Input.imeSetComposition',{text:'zhong',selectionStart:5,selectionEnd:5});
   release();await page.waitForFunction(()=>!document.querySelector('#nb-uploads').children.length);
   assert.ok((await body('nb')).includes('<!--report-upload:'),'image replacement must wait for composition');
   await cdp.send('Input.imeSetComposition',{text:'中文科研',selectionStart:4,selectionEnd:4});
   await cdp.send('Input.insertText',{text:'中文科研'});await imageCount('nb',4);
   await live('nb').pressSequentially(' END');assert.ok((await body('nb')).endsWith('中文科研 END'));
   assert.equal(await live('nb').getAttribute('data-compositions'),'1');assert.ok(!(await body('nb')).includes('zhong'));
   await page.unroute('**/notebook/upload');await saved('nb');
   // Undo before upload completion, then redo: no dangling upload token.
   gate=new Promise(r=>release=r);await page.route('**/notebook/upload',async route=>{await gate;await route.continue();});
   const beforeImage=await body('nb');await paste('nb',{png:cfg.png});await live('nb').press('Control+z');
   assert.equal(await body('nb'),beforeImage);release();await page.waitForFunction(()=>!document.querySelector('#nb-uploads').children.length);
   await live('nb').press('Control+Shift+z');await imageCount('nb',5);assert.ok(!(await body('nb')).includes('<!--report-upload:'));
   await page.unroute('**/notebook/upload');await saved('nb');
   // Failed upload and failed save are recoverable independently.
   await page.route('**/notebook/upload',route=>route.abort());await paste('nb',{png:cfg.png});
   await page.getByRole('button',{name:'重试',exact:true}).waitFor();await page.unroute('**/notebook/upload');
   await page.getByRole('button',{name:'重试',exact:true}).click();await imageCount('nb',6);await saved('nb');
   await page.route(api,route=>route.request().method()==='POST'?route.abort():route.continue());
   await page.locator('#nb-title').fill('失败仍即时显示');assert.equal(await page.title(),'失败仍即时显示');
   await page.locator('#nb-retry').waitFor({state:'visible'});
   const beforeLeave=page.url();page.once('dialog',dialog=>dialog.dismiss());await page.reload({timeout:1500}).catch(()=>{});
   assert.equal(page.url(),beforeLeave);assert.equal(await page.locator('#nb-title').inputValue(),'失败仍即时显示');
   await page.unroute(api);await page.locator('#nb-retry').click();await saved('nb');
   const persisted=await body('nb');await page.reload();await saved('nb');assert.equal(await page.title(),'失败仍即时显示');
   await page.locator('#nb-edit').click();await imageCount('nb',6);assert.equal(await body('nb'),persisted);
   await page.locator('#nb-preview-mode').click();assert.equal(await page.locator('#nb-preview figcaption').count(),0);
   await page.screenshot({path:path.join(cfg.evidence,'notebook-preview.png')});
   await page.goto(base+'/records');assert.match(await page.locator('#research-records').innerText(),/失败仍即时显示/);
   // Old blocks/comments stay byte-identical on open; explicit edit migrates losslessly.
   const original=fs.readFileSync(cfg.legacyPath);
   await page.goto(base+'/notebook/'+cfg.legacyId);await saved('nb');assert.deepEqual(fs.readFileSync(cfg.legacyPath),original);
   assert.match(await page.locator('#nb-comments').innerText(),/原有批注不能丢/);
   await page.locator('#nb-edit').click();await imageCount('nb',1);const legacy=await body('nb');
   await live('nb').press('Control+End');await paste('nb',{text:'\n新增科研观察'});await saved('nb');
   assert.equal(await body('nb'),legacy+'\n新增科研观察');
   const migrated=await read(`${cfg.origin}/api/project/${cfg.pid}/notebook/${cfg.legacyId}?format=markdown`);
   assert.equal(migrated.document.comments.length,2);assert.match(migrated.document.body,/人工图片说明/);
   await page.reload();await saved('nb');assert.match(await page.locator('#nb-comments').innerText(),/图片批注/);
   // Both report adapters use the same live editor/clipboard and durable titles.
   for(const [kind,date] of [['daily','2026-09-19'],['weekly','2026-09-14']]){
     const reportAPI=`${cfg.origin}/api/reports/${kind}/${date}`;
     await page.goto(`${cfg.origin}/reports/${kind}/${date}?context=${cfg.pid}`);await live('report').waitFor({state:'visible'});
     assert.equal(await page.locator('#report-save,#report-code,#report-comment').count(),0);
     await paste('report',{text:'## 临时报告\n\n```python\nprint(1)\n```\n'});
     await paste('report',{png:cfg.png});await imageCount('report',1);await saved('report');
     await page.locator('#report-title').fill(kind+' 人工标题');assert.equal(await page.title(),kind+' 人工标题');await saved('report');
     assert.equal((await read(reportAPI)).title,kind+' 人工标题');
     await page.locator('#report-confirm').click();await page.waitForFunction(()=>document.querySelector('#report-confirm').textContent==='修改正式版');
     await page.reload();await saved('report');assert.equal(await page.locator('#report-title').inputValue(),kind+' 人工标题');
     await page.locator('#report-edit').click();await imageCount('report',1);
     await live('report').press('Control+End');await paste('report',{text:'\n修订不丢图'});await saved('report');
     assert.match((await read(reportAPI)).body,/修订不丢图/);
     await page.screenshot({path:path.join(cfg.evidence,kind+'-live.png')});
     await page.goto(`${cfg.origin}/reports?view=${kind}&context=${cfg.pid}`);
     assert.match(await page.locator('#research-reports-list').innerText(),new RegExp(kind+' 人工标题'));
   }
   assert.deepEqual(errors,[]);console.log('PASS: shared live Markdown, native Ctrl+V, images/caret/IME, undo/redo, retry/unload, durable titles, legacy comments, daily+weekly. Evidence: '+cfg.evidence);
 }catch(error){console.error(error);console.error('URL:',page.url());console.error('Editor:',await page.locator('.rw-editor-live').evaluateAll(es=>es.map(e=>({html:e.innerHTML,text:e.innerText}))).catch(()=>[]));await page.screenshot({path:path.join(cfg.evidence,'failure.png')}).catch(()=>{});process.exitCode=1;}
 finally{await context.close();await browser.close();}
})();
