/* Real local HTTP/browser regression for the continuous notebook, no user data. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  try{
    const context=await browser.newContext({viewport:{width:1280,height:960}}),page=await context.newPage(),errors=[];
    page.on('pageerror',e=>errors.push(e.message));let pickers=0;page.on('filechooser',()=>pickers++);
    const origin=process.argv[2],pid=process.argv[3],base=`${origin}/project/${pid}`;
    const saved=async p=>p.waitForFunction(()=>document.querySelector('#nb-save-state').textContent==='已保存');
    async function paste(p,{text='',html='',images=0,png='',selector}={}){
      return p.evaluate(({text,html,images,png,selector})=>{
        const data=new DataTransfer();if(text)data.setData('text/plain',text);if(html)data.setData('text/html',html);
        for(let i=0;i<images;i++)data.items.add(new File([Uint8Array.from(atob(png),c=>c.charCodeAt(0))],`shot${i}.png`,{type:'image/png'}));
        const target=selector?document.querySelector(selector):document.activeElement;target.focus();
        const e=new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true});target.dispatchEvent(e);return e.defaultPrevented;
      },{text,html,images,png,selector});
    }
    await page.goto(base+'/records');await page.getByRole('link',{name:'新建笔记',exact:true}).click();
    await page.locator('#nb-source').waitFor({state:'visible'});
    assert.equal(await page.locator('#nb-source').evaluate(e=>e===document.activeElement),true);
    assert.equal(await page.locator('input[type=file],.nb-plus,.nb-cell').count(),0);
    await paste(page,{text:'### 1. 编辑器\n\n**观察**：代码完成不等于硬件验证完成。\n\n```python\nx=1\n```'});
    await page.locator('#nb-preview h3').waitFor();assert.equal(await page.locator('#nb-preview strong').innerText(),'观察');
    await page.locator('#nb-title').fill('低纹理配准 · 第一轮实验');await saved(page);
    const url=page.url(),nid=url.split('/').pop(),api=`${origin}/api/project/${pid}/notebook/${nid}`;
    const initial=await (await context.request.get(api+'?format=markdown')).json();
    assert.equal(initial.document.format,'markdown');assert.equal(initial.document.comments.length,0);
    assert.ok(!initial.document.body.includes(initial.document.created_at));
    const png=(await page.locator('.rw-editor-body').screenshot()).toString('base64');
    // Repeated Ctrl+V into preview retains focus and inserts both occurrences.
    await page.locator('#nb-preview').focus();await paste(page,{images:1,png});
    await page.waitForFunction(()=>document.querySelectorAll('#nb-preview img').length===1);
    assert.equal(await page.locator('#nb-preview').evaluate(e=>document.activeElement===e),true);
    await paste(page,{images:1,png});await page.waitForFunction(()=>document.querySelectorAll('#nb-preview img').length===2);await saved(page);
    assert.equal(pickers,0);
    // Title text+image remains a native text paste; pure image routes to body.
    assert.equal(await paste(page,{selector:'#nb-title',text:'标题文字',images:1,png}),false);
    assert.equal(await page.locator('#nb-title').inputValue(),'低纹理配准 · 第一轮实验');
    await paste(page,{selector:'#nb-title',images:1,png});await page.waitForFunction(()=>document.querySelectorAll('#nb-preview img').length===3);await saved(page);
    // Quoted annotations are separate, not a timestamp paragraph in the body.
    await page.locator('#nb-edit').click();await page.locator('#nb-source').evaluate(e=>{e.setSelectionRange(0,9);e.dispatchEvent(new Event('select'));});
    await page.locator('#nb-comment').click();await page.getByLabel('批注内容').fill('补充夜间场景，暂不作为最终结论。');
    await page.locator('#nb-dialog-actions').getByRole('button',{name:'添加',exact:true}).click();await saved(page);
    const annotated=await (await context.request.get(api)).json();
    assert.equal(annotated.document.comments[0].text,'补充夜间场景，暂不作为最终结论。');
    assert.ok(annotated.document.comments[0].quote);assert.ok(!annotated.document.body.includes('暂不作为最终结论'));
    await page.getByLabel('文档更多操作').click();await page.locator('#nb-info').click();
    assert.ok((await page.locator('#nb-dialog-content').innerText()).includes('创建时间'));
    await page.getByLabel('标签',{exact:true}).fill('实验, 待验证');await page.getByRole('button',{name:'保存标签',exact:true}).click();await saved(page);
    const downloadEvent=page.waitForEvent('download');await page.locator('#nb-export').click();const download=await downloadEvent;
    const exported=fs.readFileSync(await download.path(),'utf8');assert.match(exported,/## 批注/);assert.ok(!exported.includes('llmwiki-notebook-v2'));
    await page.reload();await page.locator('#nb-preview img').first().waitFor();assert.equal(await page.locator('#nb-preview img').count(),3);
    assert.match(await page.locator('#nb-comments').innerText(),/补充夜间场景/);
    // Native text and injected-paste undo/redo preserve text around each transaction.
    await page.locator('#nb-edit').click();await page.locator('#nb-source').press('Control+End');
    const before=await page.locator('#nb-source').inputValue();await paste(page,{text:'\n补充记录'});await page.locator('#nb-source').press('Control+z');
    assert.equal(await page.locator('#nb-source').inputValue(),before);await page.locator('#nb-source').press('Control+Shift+z');
    assert.equal(await page.locator('#nb-source').inputValue(),before+'\n补充记录');await saved(page);
    // Undo an image while its HTTP request is in flight; redo resolves the same upload.
    let release;const gate=new Promise(r=>release=r);await page.route('**/notebook/upload',async route=>{await gate;await route.continue();});
    await paste(page,{images:1,png});await page.waitForFunction(()=>document.querySelector('#nb-source').value.includes('<!--report-upload:'));
    await page.locator('#nb-source').press('Control+z');release();await page.waitForFunction(()=>!document.querySelector('#nb-uploads').textContent.includes('上传中'));
    await page.locator('#nb-source').press('Control+Shift+z');assert.ok(!(await page.locator('#nb-source').inputValue()).includes('<!--report-upload:'));
    assert.equal((await page.locator('#nb-source').inputValue()).match(/!\[截图\]/g).length,4);await page.unroute('**/notebook/upload');await saved(page);
    // Upload failure retains a retry at the original position; never opens a picker.
    await page.route('**/notebook/upload',route=>route.abort());await paste(page,{images:1,png});await page.getByRole('button',{name:'重试',exact:true}).waitFor();
    await page.unroute('**/notebook/upload');await page.getByRole('button',{name:'重试',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#nb-uploads').children.length===0);await saved(page);
    assert.equal((await page.locator('#nb-source').inputValue()).match(/!\[截图\]/g).length,5);assert.equal(pickers,0);
    for(const image of (await page.locator('#nb-source').inputValue()).matchAll(/!\[截图\]\(([^)]+)\)/g)) assert.match(image[1], /^\.\.\/assets\/[a-f0-9]{64}\.png$/);
    // Ordinary typing uses the textarea's native undo/redo, not whole-document resets.
    const beforeTyping=await page.locator('#nb-source').inputValue();await page.locator('#nb-source').press('Control+End');
    await page.locator('#nb-source').pressSequentially(' native typing');
    let nativeSteps=0;while(await page.locator('#nb-source').inputValue()!==beforeTyping){assert.ok(nativeSteps++<30,'native undo must reach the insertion boundary');await page.locator('#nb-source').press('Control+z');assert.ok((await page.locator('#nb-source').inputValue()).startsWith(beforeTyping),'native undo must not remove an earlier screenshot');}
    for(let i=0;i<nativeSteps;i++)await page.locator('#nb-source').press('Control+Shift+z');assert.equal(await page.locator('#nb-source').inputValue(),beforeTyping+' native typing');await saved(page);
    // History is reachable; current notebook identity and image URLs survive refresh.
    await page.getByLabel('文档更多操作').click();await page.locator('#nb-history').click();await page.locator('#nb-dialog-actions button').first().waitFor();
    await page.locator('#nb-dialog-actions button').first().click();await page.getByRole('button',{name:'恢复为当前笔记'}).waitFor();await page.locator('#nb-dialog-close').click();
    // Two-tab conflict keeps both drafts; loading the remote version is explicit.
    const other=await context.newPage();await other.goto(url);await other.locator('#nb-preview').waitFor();await other.waitForFunction(()=>document.querySelector('#nb-save-state').textContent==='已保存');
    await other.locator('#nb-title').fill('另一页面的标题');await saved(other);await page.locator('#nb-title').fill('本页未合并标题');await page.locator('#nb-save').click();
    await page.getByRole('heading',{name:'正文发生冲突'}).waitFor();assert.equal(await page.locator('#nb-title').inputValue(),'本页未合并标题');
    assert.equal((await (await context.request.get(api)).json()).document.title,'另一页面的标题');
    await page.getByRole('button',{name:'加载服务器稿',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#nb-title').value==='另一页面的标题');await page.locator('#nb-dialog').waitFor({state:'hidden'});await other.close();
    // Offline recovery never silently overwrites the disk version.
    await page.route('**/api/**',route=>route.abort());await page.locator('#nb-title').fill('离线恢复标题');await page.locator('#nb-save').click();
    await page.waitForFunction(()=>document.querySelector('#nb-save-state').textContent==='保存失败');await page.unroute('**/api/**');
    page.on('dialog',dialog=>dialog.accept());await page.reload();await page.getByRole('button',{name:'恢复本机稿',exact:true}).click();await saved(page);
    assert.equal(await page.locator('#nb-title').inputValue(),'离线恢复标题');
    // Inert HTML-only clipboard becomes text, never a live element or script.
    await page.locator('#nb-edit').click();await page.locator('#nb-source').press('Control+End');
    await paste(page,{html:'<b>HTML剪贴板</b><img src=x onerror="window.injected=1"><script>window.injected=1</script>'});await saved(page);
    assert.equal(await page.evaluate(()=>window.injected),undefined);assert.match(await page.locator('#nb-source').inputValue(),/HTML剪贴板/);
    // Multiple files in one paste are one undoable insertion, even when their
    // asynchronous uploads complete at different moments.
    await page.locator('#nb-source').press('Control+End');const beforeMulti=await page.locator('#nb-source').inputValue();
    await paste(page,{images:2,png});await page.waitForFunction(()=>document.querySelector('#nb-uploads').children.length===0);await saved(page);
    assert.equal((await page.locator('#nb-source').inputValue()).match(/!\[截图\]/g).length,7);
    await page.locator('#nb-source').press('Control+z');assert.equal(await page.locator('#nb-source').inputValue(),beforeMulti);
    await page.locator('#nb-source').press('Control+Shift+z');assert.equal((await page.locator('#nb-source').inputValue()).match(/!\[截图\]/g).length,7);await saved(page);
    // Dropping outside the document must never navigate the browser to the file.
    const didPrevent=await page.evaluate(png=>{const d=new DataTransfer();d.items.add(new File([Uint8Array.from(atob(png),c=>c.charCodeAt(0))],'outside.png',{type:'image/png'}));const e=new DragEvent('drop',{dataTransfer:d,bubbles:true,cancelable:true});document.body.dispatchEvent(e);return e.defaultPrevented;},png);
    assert.equal(didPrevent,true);assert.equal(page.url(),url);
    // Composition postpones saves until the whole word is committed.
    await saved(page);let writes=0;const countWrite=r=>{if(r.url()===api&&r.method()==='POST')writes++;};page.on('request',countWrite);
    await page.locator('#nb-source').evaluate(e=>{e.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));e.value+='\n中文组合输入';e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertCompositionText',isComposing:true}));});
    await page.waitForTimeout(800);assert.equal(writes,0);await page.locator('#nb-source').evaluate(e=>e.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'中文组合输入'})));await saved(page);assert.equal(writes,1);page.off('request',countWrite);
    // Switching modes doesn't write a new version or move the source cursor.
    await page.locator('#nb-source').evaluate(e=>{e.setSelectionRange(2,7);e.dispatchEvent(new Event('select'));});
    const stable=(await (await context.request.get(api)).json()).revision;await page.locator('#nb-preview-mode').click();await page.locator('#nb-edit').click();
    assert.deepEqual(await page.locator('#nb-source').evaluate(e=>[e.selectionStart,e.selectionEnd]),[2,7]);assert.equal((await (await context.request.get(api)).json()).revision,stable);
    for(const scheme of ['light','dark']){await page.emulateMedia({colorScheme:scheme});for(const width of [1280,1024,736,580,375,320]){
      await page.setViewportSize({width,height:960});await page.waitForFunction(()=>{const e=document.querySelector('#nb-source');return e.scrollHeight<=e.clientHeight+2;});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`overflow ${width} ${scheme}`);
      if(width===1280||width===375)await page.screenshot({path:path.join(os.tmpdir(),`llmwiki-continuous-${scheme}-${width}.png`),fullPage:true});
    }}
    assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:['continuous body','Markdown default preview','repeated clipboard screenshots','title clipboard priority','quoted comments','timestamps in info','export','persistence','paste and native typing undo/redo','multi-image paste transaction','IME save delay','mode selection stability','drop navigation protection','in-flight upload undo/redo','upload retry','history','two-tab conflict','offline recovery','HTML sanitization','six widths / light and dark','no filechooser']}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
