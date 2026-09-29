/* Appearance presets use the real picker, persist, and remain usable on narrow screens. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  try {
    const page=await (await browser.newContext({viewport:{width:1440,height:900}})).newPage();
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    const origin=process.argv[2],base=`/project/${process.argv[3]}`;
    const presets=['claude','codex','notion','linear','glass','bento','brutalist','swiss','retro','collage','anima','cyberpunk','geek'];
    await page.goto(origin+'/settings');
    assert.deepEqual(await page.locator('.theme-preset-grid [data-theme-choice]').evaluateAll(buttons=>buttons.map(button=>button.dataset.themeChoice)),presets);
    assert.deepEqual(await page.locator('.theme-options [data-theme-choice]').evaluateAll(buttons=>buttons.map(button=>button.dataset.themeChoice)),['light','dark','system',...presets]);
    assert.equal(await page.locator('[data-theme-choice=vapor]').count(),0);
    const palettes=new Set();
    for(const preset of presets){
      await page.locator(`.theme-preset-grid [data-theme-choice=${preset}]`).click();
      const state=await page.evaluate(()=>({
        theme:document.documentElement.dataset.theme,
        stored:localStorage.getItem('workbench.theme'),
        scheme:getComputedStyle(document.documentElement).colorScheme,
        paper:getComputedStyle(document.documentElement).getPropertyValue('--rw-paper').trim(),
        solid:getComputedStyle(document.documentElement).getPropertyValue('--rw-solid').trim()
      }));
      assert.equal(state.theme,preset);
      assert.equal(state.stored,preset);
      assert.equal(state.scheme,['linear','retro','anima','cyberpunk','geek'].includes(preset)?'dark':'light');
      assert.equal(await page.locator(`.theme-preset-grid [data-theme-choice=${preset}]`).getAttribute('aria-pressed'),'true');
      palettes.add(`${state.paper}|${state.solid}`);
    }
    assert.equal(palettes.size,presets.length,'each preset should have a distinct base palette');
    await page.reload();
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),presets.at(-1));
    await page.locator('.theme-menu summary').click();
    await page.locator('.theme-options [data-theme-choice=claude]').click();
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'claude');
    await page.goto(origin+base+'/records');
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'claude');
    for(const width of [375,320]){
      await page.setViewportSize({width,height:700});
      await page.goto(origin+'/settings');
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`settings overflow at ${width}`);
      await page.getByRole('button',{name:'打开导航菜单'}).click();
      await page.locator('.theme-menu summary').click();
      const menu=await page.locator('.theme-options').boundingBox();
      assert.ok(menu.x>=-1 && menu.x+menu.width<=width+1 && menu.y>=-1 && menu.y+menu.height<=701,
        `appearance menu overflow at ${width}: ${JSON.stringify(menu)}`);
      const mobileChoice=width===320?'collage':'codex';
      await page.locator(`.theme-options [data-theme-choice=${mobileChoice}]`).click();
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),mobileChoice);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`chosen style overflow at ${width}`);
    }
    for(const preset of presets){
      await page.goto(origin+'/settings');
      await page.locator(`.theme-preset-grid [data-theme-choice=${preset}]`).click();
      for(const route of ['/projects',base+'/records',base+'/todos']){
        await page.goto(origin+route);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),
          `${preset} overflows ${route} at 320px`);
      }
    }
    assert.deepEqual(errors,[]);
    console.log('Appearance presets: picker, distinct palettes, persistence, navigation and mobile routes PASS');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
