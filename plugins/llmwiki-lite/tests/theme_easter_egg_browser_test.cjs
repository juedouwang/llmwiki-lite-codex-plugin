/* Hidden vaporwave appearance: invisible until found, reversible, never triggered from text fields. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  try {
    const page=await (await browser.newContext({viewport:{width:1440,height:900}})).newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const origin=process.argv[2],base=`/project/${process.argv[3]}`;
    const theme=()=>page.evaluate(()=>document.documentElement.dataset.theme);
    const konami=['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
    const type=async()=>{for(const key of konami)await page.keyboard.press(key);};
    await page.goto(origin+base+'/records');  // explicit project: independent of the landing default
    assert.equal(await page.locator('[data-theme-choice=vapor]').count(),0,'no trace in menus before discovery');
    assert.equal(await page.locator('link[data-vapor]').count(),0,'stylesheet is not loaded for everyday themes');
    for(let i=0;i<4;i++)await page.locator('.console-avatar').click();
    assert.equal(await theme(),'system','four taps are not enough');
    await page.locator('.console-avatar').click();
    assert.equal(await theme(),'vapor');assert.equal(await page.locator('link[data-vapor]').count(),1);
    assert.match(await page.locator('.vapor-toast').innerText(),/蒸汽波已开启/);
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'dark');
    await page.reload();assert.equal(await theme(),'vapor','persists across reloads');
    await page.locator('[data-workbench-nav=todos]').click();await page.locator('#research-progress').waitFor();
    assert.equal(await page.locator('.theme-options [data-theme-choice=vapor]').count(),1,'found option survives in-app navigation');
    assert.equal(await page.locator('.theme-options .theme-choice-group:last-child [data-theme-choice=vapor]').count(),1,'vapor belongs with the other styles');
    assert.ok(await page.locator('.theme-options [data-theme-choice]').evaluateAll(buttons=>buttons.every(button=>getComputedStyle(button).justifyContent==='flex-start')),'menu choices align at the leading edge');
    await page.locator('.theme-menu summary').click();await page.locator('.theme-options [data-theme-choice=light]').click();
    assert.equal(await theme(),'light');assert.equal(await page.locator('.theme-options [data-theme-choice=vapor]').count(),1);
    await page.goto(origin+'/settings');
    assert.equal(await page.locator('.theme-preset-grid [data-theme-choice=vapor]').count(),1,'vapor is a matching style card');
    assert.equal(await page.locator('.theme-preset-grid [data-theme-choice]').count(),14);
    const positions=await page.locator('.theme-preset-grid [data-theme-choice]').evaluateAll(buttons=>buttons.map(button=>({
      choice:button.dataset.themeChoice,swatchX:button.querySelector('.theme-swatch').getBoundingClientRect().x,
      alignment:getComputedStyle(button).justifyContent
    })));
    assert.ok(positions.every(item=>item.alignment==='flex-start'),'all style cards align at the leading edge');
    assert.ok(Math.abs(positions[1].swatchX-positions.at(-1).swatchX)<1,'vapor card swatch aligns with the second column');
    await page.locator('.theme-preset-grid [data-theme-choice=vapor]').click();assert.equal(await theme(),'vapor');
    await page.locator('.theme-mode-grid [data-theme-choice=light]').click();assert.equal(await theme(),'light');
    await page.goto(origin+base+'/todos');
    await page.locator('main h1').click();await type();assert.equal(await theme(),'vapor');
    await type();assert.equal(await theme(),'light','returns to the remembered everyday theme');
    await page.goto(origin+'/search');await page.locator('#q').click();await type();
    assert.equal(await theme(),'light','typing in a field never flips the theme');
    assert.deepEqual(errors,[]);
    console.log('Theme easter egg: discovery, unified picker alignment, persistence, navigation and field guard PASS');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
