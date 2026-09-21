/* Read-only comparison with the approved HTML: same viewport, 125%, live navigation. */
const {chromium}=require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
(async()=>{
  const [origin,pid,prototype]=process.argv.slice(2);
  const prototypeHTML=fs.readFileSync(prototype,'utf8');
  const hash=text=>crypto.createHash('sha256').update(text).digest('hex');
  const evidence=path.join(os.tmpdir(),'llmwiki-site-prototype');
  fs.mkdirSync(evidence,{recursive:true});
  const browser=await chromium.launch({headless:true,...(process.env.LLMWIKI_BROWSER_CHANNEL?{channel:process.env.LLMWIKI_BROWSER_CHANNEL}:{})});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:960},colorScheme:'light'});
    const page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    let documents=0;page.on('request',r=>{if(r.isNavigationRequest()&&r.frame()===page.mainFrame())documents++;});
    await page.goto(`${origin}/project/${pid}/code`);
    await page.locator('.code-commit').first().waitFor();
    await page.locator('.code-version-title').waitFor();
    await page.waitForFunction(()=>document.querySelector('#code-refresh')?.disabled===false);
    await page.locator('.code-commit').filter({hasText:'修正标定参数读取'}).click();
    await page.waitForFunction(()=>document.querySelector('.code-version-title')?.textContent==='修正标定参数读取');
    await page.locator('#code-detail .code-diff').waitFor();
    assert.equal((await page.locator('#code-pull').innerText()).trim(),'pull');
    assert.equal((await page.locator('#code-push').innerText()).trim(),'push');
    assert.equal(await page.locator('[data-workbench-nav="code"] svg circle').count(),2);
    assert.equal(await page.locator('#code-branches > summary svg').first().locator('circle').count(),2);
    assert.equal(await page.locator('.code-version-meta').innerText(),(await page.locator('#code-commit-oid').textContent()).slice(0,7)+'\n刘亚宁 · 16:42');
    assert.equal(await page.locator('#code-detail .code-commit-file').count(),2);
    assert.deepEqual(await page.locator('#code-detail .code-added').allTextContents(),['+2','+8']);
    assert.deepEqual(await page.locator('#code-detail .code-removed').allTextContents(),['−2','−3']);
    assert.equal(await page.locator('#code-detail .code-preview-files > .code-muted, #code-detail > .code-muted, .code-commit-diff > p').count(),0);
    const patch=await page.locator('#code-detail .code-diff').innerText();
    assert(!patch.includes('diff --git')&&!patch.includes('@@')&&!patch.includes('index '));
    assert(patch.includes('-  exposure_ms: 6.0')&&patch.includes('+  exposure_ms: 8.0'));
    await page.locator('#code-commit-metadata summary').click();
    assert.equal(await page.locator('#code-commit-metadata').evaluate(n=>n.open),true);
    await page.locator('#code-commit-metadata summary').click();
    assert.equal(await page.locator('#code-commit-metadata[open], .code-legend[open]').count(),0,'metadata and graph legend stay collapsed by default');
    // Do not stub workbench-navigation.js: this catches initial-main CSS relocation bugs.
    assert.equal(await page.locator('head link[href="/static/code.css"]').count(),1);
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).zoom),'1.25');
    const ref=await context.newPage();
    await ref.setContent(prototypeHTML);
    // Only remove the embedding card frame and apply the approved application scale.
    // Prototype source stays untouched. No CSS is copied from the implementation.
    await ref.addStyleTag({content:'html{zoom:1.25}body{margin:0}#research-workbench .rw-app{border:0!important;border-radius:0!important;min-height:calc(100dvh / 1.25)!important}'});
    await ref.addScriptTag({path:path.resolve(path.dirname(require.resolve('lucide')),'../umd/lucide.min.js')});
    await ref.locator('#rw-nav [data-view="code"]').click();
    await ref.locator('.rg-commit').first().waitFor();
    // Compare the actual prototype SVG, not just its number of endpoints.
    // The opposite quarter-circle has the same two circles but is a different icon.
    async function glyph(tab,selector){
      return tab.locator(selector).first().evaluate(svg=>{
        const style=getComputedStyle(svg);
        return {
          viewBox:svg.getAttribute('viewBox'),
          width:style.width,height:style.height,fill:style.fill,
          strokeWidth:style.strokeWidth,linecap:style.strokeLinecap,linejoin:style.strokeLinejoin,
          nodes:[...svg.children].map(node=>({tag:node.localName,
            attrs:Object.fromEntries([...node.attributes].map(a=>[a.name,a.value]).sort())})),
        };
      });
    }
    const expectedGit=await glyph(ref,'#rw-nav [data-view="code"] svg');
    assert.equal(expectedGit.nodes[0].attrs.d,'M15 6a9 9 0 0 0-9 9V3','approved Lucide git-branch source');
    const gitIcons=[];
    for(const selector of ['[data-workbench-nav="code"] svg','#code-branches > summary svg']){
      const actual=await glyph(page,selector);
      assert.deepEqual(actual,expectedGit,`${selector}: exact prototype geometry and stroke`);
      gitIcons.push(selector);
    }
    await page.locator('#code-branches > summary').click();
    await page.locator('#code-branch-menu [data-branch] svg').first().waitFor();
    assert.deepEqual(await glyph(page,'#code-branch-menu [data-branch] svg'),expectedGit,'dynamic branch menu matches prototype');
    gitIcons.push('#code-branch-menu [data-branch] svg');
    await page.locator('#code-branches > summary').click();
    await page.locator('[data-workbench-nav="code"]').screenshot({path:path.join(evidence,'actual-git-icon.png')});
    await ref.locator('#rw-nav [data-view="code"]').screenshot({path:path.join(evidence,'prototype-git-icon.png')});
    const pairs=[
      ['.console-sidebar','.rw-side',['x','y','width','height','padding','gap','backgroundColor']],
      ['.console-project-switcher','.rw-project-switch',['x','y','width','height']],
      ['.console-navigation .console-nav-item','.rw-nav button',['x','y','width','height','padding','gap','fontSize']],
      ['.console-topbar','.rw-header',['x','y','width','height','padding']],
      ['.console-avatar','.rw-avatar',['width','height','backgroundColor','borderRadius']],
      ['.code-toolbar','.rg-toolbar',['y','height','gap']],
      ['.code-changes','.rg-changes',['x','y','width','height','padding','gap']],
      ['.code-history','.rg-history',['x','y','width','padding']],
      ['.code-detail','.rg-details',['x','y','width','padding']],
      ['.code-commit','.rg-commit',['x','y','width','height','padding']],
      ['.code-section-heading','.rg-section-header',['x','y','width','height','padding','gap','fontSize']],
      ['.code-detail-heading','.rg-detail-heading',['x','y','width','height','fontSize']],
      ['.code-version-title','.rg-version-title',['x','y','width','height','fontSize','fontWeight','lineHeight']],
      ['.code-version-meta','.rg-version-meta',['x','y','width','height','fontSize','gap']],
      ['.code-files-label','.rg-files-label',['x','y','width','height','fontSize','margin']],
      ['.code-commit-file','.rg-file-button',['x','y','width','height','padding','gap','fontSize']],
      ['.code-added','.rg-added',['fontSize','fontWeight','color']],
      ['.code-removed','.rg-removed',['fontSize','fontWeight','color']],
      ['.code-commit-diff .code-diff','.rg-diff',['x','width','height','padding','margin','fontSize','lineHeight','whiteSpace','borderRadius']],
      ['.code-diff-line','.rg-code-line',['padding','fontSize','lineHeight']],
      ['.code-detail-actions','.rg-detail-actions',['x','width','gap']],
      ['#code-restore','.rg-detail-actions .rg-outline',['width','height','fontSize','borderRadius']],
    ];
    async function measure(tab,selector){
      return tab.locator(selector).first().evaluate(el=>{
        const r=el.getBoundingClientRect(),s=getComputedStyle(el);
        return {x:r.x,y:r.y,width:r.width,height:r.height,padding:s.padding,gap:s.gap,fontSize:s.fontSize,backgroundColor:s.backgroundColor,borderRadius:s.borderRadius,margin:s.margin,lineHeight:s.lineHeight,fontWeight:s.fontWeight,color:s.color,whiteSpace:s.whiteSpace};
      });
    }
    const results=[];
    for(const [live,approved,keys] of pairs){
      const actual=await measure(page,live),expected=await measure(ref,approved);
      const differences=keys.filter(key=>typeof actual[key]==='number'?Math.abs(actual[key]-expected[key])>1:actual[key]!==expected[key]);
      results.push({live,approved,keys,actual,expected,differences});
    }
    await page.mouse.move(0,0); await ref.mouse.move(0,0);
    await page.screenshot({path:path.join(evidence,'actual-code.png'),fullPage:true});
    await ref.screenshot({path:path.join(evidence,'prototype-code.png'),fullPage:true});
    // Compare the shared shell after actual asynchronous column swaps, not page.goto.
    await page.evaluate(()=>{window.visualCodeDOM=document.getElementById('code-app');});
    const routes={todos:`/project/${pid}/todos`,records:`/project/${pid}/records`,reports:`/reports?context=${pid}`,overview:`/project/${pid}`,literature:`/project/${pid}/literature`,code:`/project/${pid}/code`};
    for(const [section,route] of Object.entries(routes)){
      const link=page.locator(`.console-navigation [data-workbench-nav="${section}"]`);
      await Promise.all([page.waitForURL(origin+route),link.click()]);
      await page.waitForFunction(key=>document.body.dataset.workbenchPage===key,section);
      assert.equal(await page.locator('.console-navigation [aria-current="page"]').getAttribute('data-workbench-nav'),section);
      assert.equal(await page.locator('main').count(),1);
      const rail=await measure(page,'.console-sidebar');
      assert.ok(Math.abs(rail.width-207.5)<1,`${section}: shared rail width`);
    }
    assert.equal(documents,1,'six-column comparison must keep a single document');
    assert(await page.evaluate(()=>window.visualCodeDOM===document.getElementById('code-app')),'live Git DOM remains cached');
    const responsive=[];
    for(const width of [1440,1024,736,390,320]){
      await page.setViewportSize({width,height:960});
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>document.documentElement.clientWidth+1);
      responsive.push({width,overflow});
      assert.equal(overflow,false,`Git horizontal overflow at ${width}`);
    }
    assert.deepEqual(errors,[]);
    assert.equal(hash(fs.readFileSync(prototype,'utf8')),hash(prototypeHTML),'approved source was not modified');
    const report={viewport:{width:1440,height:960},zoom:1.25,prototype,prototypeSha256:hash(prototypeHTML),gitIcons,results,responsive,documents,errors,
      limitations:['User amendment: the diff now has an expand control and actions follow the diff instead of the panel bottom; those changed offsets are checked in workbench_refinements_browser_test.cjs, not against the old prototype.','Reference uses the bundled Lucide UMD runtime, restoring the same icons as the embedded prototype without changing its HTML.','Real commit/detail contents and history height legitimately differ from demo data.']};
    fs.writeFileSync(path.join(evidence,'comparison.json'),JSON.stringify(report,null,2));
    const differences=results.filter(result=>result.differences.length);
    assert.deepEqual(differences,[],`prototype geometry mismatch; evidence: ${evidence}`);
    console.log(`PASS approved-prototype comparison: ${gitIcons.length} exact Git SVGs, ${results.length} geometry/style groups, six live column swaps, five Git widths. Evidence: ${evidence}`);
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
