/* A-13: real foreground/background tabs in a disposable headed browser.
 * No visibility overrides, synthetic visibilitychange events, or personal profile.
 * This deliberately opens a visible window; it is not part of unattended smoke.
 */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT||'playwright');
const assert=require('node:assert/strict');
const {performance}=require('node:perf_hooks');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(condition, timeout=10000) {
  const end=performance.now()+timeout;
  while(!condition()) {
    assert.ok(performance.now()<end,'Timed out waiting for a summary request/response');
    await sleep(25);
  }
}
(async()=>{
  const cfg=JSON.parse(process.argv[2]);
  // Attach only to the disposable Chrome started by this test's Python runner.
  // noDefaults prevents Playwright from enabling focus/visibility emulation.
  const browser=await chromium.connectOverCDP(cfg.browserEndpoint,{noDefaults:true});
  const browserSession=await browser.newBrowserCDPSession();
  try {
    const context=browser.contexts()[0];
    const page=context.pages()[0]||await context.newPage();
    const session=await context.newCDPSession(page);
    const windowInfo=await session.send('Browser.getWindowForTarget');
    const polls=[], responses=[], errors=[];
    const endpoint=`/api/project/${cfg.projectId}/progress?view=summary`;
    page.on('request',request=>{if(request.url().endsWith(endpoint))polls.push(performance.now());});
    page.on('response',response=>{if(response.url().endsWith(endpoint))responses.push(response.status());});
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{
      window.visibilityEvidence=[];
      document.addEventListener('visibilitychange',event=>{
        window.visibilityEvidence.push({hidden:document.hidden,trusted:event.isTrusted});
      });
    });
    await page.goto(`${cfg.origin}/project/${cfg.projectId}/todos#task-${cfg.taskId}`);
    await page.bringToFront();
    await page.waitForFunction(()=>!document.hidden&&document.hasFocus(),null,{timeout:10000});
    await page.locator('#progress-dialog[open]').waitFor();
    const input=page.locator('#progress-form [name=next_step]');
    await input.click();
    await input.fill('这句还没写完，明天继续');
    await input.focus();
    const state=()=>input.evaluate(el=>({text:el.value,start:el.selectionStart,end:el.selectionEnd,focused:el===document.activeElement}));
    const expected=await state();
    // Three consecutive foreground intervals, around 15 seconds in total.
    const first=polls.length-1;
    await until(()=>polls.length>=first+4,22000);
    const intervals=polls.slice(first,first+4).slice(1).map((t,i)=>Math.round(t-polls[first+i]));
    assert.ok(intervals.every(ms=>ms>=4000&&ms<=6500),`Unexpected foreground intervals: ${intervals}`);
    const foregroundEditor=await state();
    if(JSON.stringify(foregroundEditor)!==JSON.stringify(expected))console.log('FOCUS_DIAGNOSTIC '+JSON.stringify(await page.evaluate(()=>({hasFocus:document.hasFocus(),activeTag:document.activeElement?.tagName,activeName:document.activeElement?.getAttribute('name'),hidden:document.hidden}))));
    assert.deepEqual(foregroundEditor,expected,'Polling changed unsaved input, caret, or focus');
    assert.equal(await page.evaluate(()=>document.hidden),false);

    // Opening and activating another real tab changes native document visibility.
    const other=await context.newPage();
    await other.setContent('<title>A-13 切换测试</title><p>正在观察后台科研进度页 30 秒，测试结束后自动关闭。</p>');
    const otherSession=await context.newCDPSession(other);
    const otherWindowInfo=await otherSession.send('Browser.getWindowForTarget');
    assert.equal(otherWindowInfo.windowId,windowInfo.windowId,'Test requires two tabs in the same browser window');
    await other.bringToFront();
    await page.waitForFunction(()=>document.hidden,null,{polling:100,timeout:10000});
    const beforeHidden=polls.length;
    const hiddenStart=performance.now();
    let samples=0;
    while(performance.now()-hiddenStart<30000) {
      await sleep(1000);
      const native=await page.evaluate(()=>({
        hidden:document.hidden, state:document.visibilityState,
        overridden:Object.hasOwn(document,'hidden')||Object.hasOwn(document,'visibilityState'),
      }));
      assert.deepEqual(native,{hidden:true,state:'hidden',overridden:false});
      assert.equal(polls.length,beforeHidden,'A hidden tab sent a new summary request');
      samples++;
    }
    const hiddenDuration=Math.round(performance.now()-hiddenStart);
    const returnedAt=performance.now();
    await page.bringToFront();
    await until(()=>polls.length>beforeHidden,1500);
    const wakeMs=Math.round(polls[beforeHidden]-returnedAt);
    assert.ok(wakeMs<1500,`Resume took ${wakeMs} ms`);
    await page.waitForFunction(()=>!document.hidden,null,{timeout:3000});
    assert.deepEqual(await state(),expected,'Tab switching or refresh changed the unsaved editor');
    await until(()=>polls.length>beforeHidden+1,7000);
    const resumedInterval=Math.round(polls[beforeHidden+1]-polls[beforeHidden]);
    assert.ok(resumedInterval>=4000&&resumedInterval<=6500,`Bad resumed interval: ${resumedInterval}`);
    await until(()=>responses.length===polls.length);
    assert.ok(responses.every(status=>status===200),`Summary responses: ${responses}`);
    assert.deepEqual(errors,[]);
    const events=await page.evaluate(()=>window.visibilityEvidence);
    assert.ok(events.some(event=>event.hidden&&event.trusted),'No native hidden event');
    assert.ok(events.some(event=>!event.hidden&&event.trusted),'No native visible event');
    console.log('REAL_VISIBILITY_RESULT '+JSON.stringify({
      browser:browser.version(), foregroundIntervalsMs:intervals,
      hiddenDurationMs:hiddenDuration, nativeHiddenSamples:samples, hiddenNewRequests:0,
      resumeRequestMs:wakeMs,resumedIntervalMs:resumedInterval,
      unsavedInputAndCaretPreserved:true,nativeEvents:events,
    }));
  } finally {
    await browserSession.send('Browser.close').catch(()=>{});
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
