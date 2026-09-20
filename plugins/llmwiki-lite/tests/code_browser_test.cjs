const {chromium} = require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path');
const {execFileSync} = require('node:child_process');
const cfg = JSON.parse(process.argv[2]);
// Product actions below use clicks only. Git calls here are read-only assertions.
function git(project, ...args) { return execFileSync('git', args, {cwd:project.root, encoding:'utf8', windowsHide:true}).trim(); }
function head(project) { return git(project,'rev-parse','HEAD'); }
function edit(project,name,text) { fs.writeFileSync(path.join(project.root,name),text); }
(async () => {
  const browser = await chromium.launch({headless:true,channel:process.env.LLMWIKI_BROWSER_CHANNEL || 'chrome'});
  const context = await browser.newContext({viewport:{width:1360,height:940}});
  const page = await context.newPage(), errors=[], passed=[], busySamples=[];
  page.on('pageerror',e=>errors.push(e.message));
  const done = name => { passed.push(name); console.log('PASS '+name); };
  async function ready() {
    await page.waitForFunction(()=>document.querySelector('#code-change-title')?.textContent && !document.querySelector('#code-change-title').textContent.includes('正在'));
    await page.waitForFunction(()=>document.querySelector('#code-refresh') && !document.querySelector('#code-refresh').disabled);
  }
  async function open(project) { await page.goto(`${cfg.origin}/project/${project.pid}/code`); await ready(); }
  async function refresh() { await page.locator('#code-refresh').click(); await ready(); }
  async function confirm() {
    await page.waitForFunction(()=>document.querySelector('#code-confirm') && !document.querySelector('#code-confirm').disabled);
    await page.evaluate(()=>{
      const control=document.querySelector('#code-confirm'), dialog=document.querySelector('#code-dialog');
      control.addEventListener('click',()=>{
        const start=performance.now();
        const record=()=>{ if(dialog.getAttribute('aria-busy')==='true') { document.documentElement.dataset.codeBusyMs=String(performance.now()-start); observer.disconnect(); } };
        const observer=new MutationObserver(record); observer.observe(dialog,{attributes:true,attributeFilter:['aria-busy']}); record();
      },{once:true,capture:true});
    });
    await page.locator('#code-confirm').click();
    busySamples.push(await page.evaluate(()=>Number(document.documentElement.dataset.codeBusyMs)));
    await page.waitForFunction(()=>!document.querySelector('#code-dialog').open);
    await ready();
  }
  async function save(message, files=null) {
    await page.locator('#code-save').click();
    await page.locator('#code-save-message').fill(message);
    await page.waitForSelector('#code-select-all');
    if (files) {
      await page.locator('#code-select-all').uncheck();
      for (const name of files) await page.getByRole('checkbox',{name:`保存 ${name}`,exact:true}).check();
    } else await page.locator('#code-select-all').check();
    await confirm();
  }
  async function selectNode(oid) {
    await page.locator(`.code-commit[data-oid="${oid}"]`).click();
    await page.waitForSelector('#code-create-from');
  }
  async function switchBranch(name) {
    await page.locator('#code-branches summary').click();
    await page.locator(`#code-branch-menu [data-branch="${name}"]`).click();
    await confirm();
  }
  async function merge(name) {
    await page.locator('#code-merge').click();
    await page.locator('#code-source-branch').selectOption(name);
    await page.locator('#code-preview-refresh').click();
    await confirm();
  }
  try {
    await open(cfg.local);
    assert.equal(await page.locator('#code-app').count(),1);
    assert.equal(await page.locator('body > aside').count(),0);
    await save('只保存选择的A',['a.txt']);
    assert.equal(git(cfg.local,'show','HEAD:a.txt'),'选择保存的 A');
    assert.equal(git(cfg.local,'show','HEAD:b.txt'),'初始B');
    assert.equal(git(cfg.local,'rev-parse',':b.txt'),cfg.local.b_index);
    assert.equal(fs.readFileSync(path.join(cfg.local.root,'c.txt'),'utf8'),'不选的 C\n');
    done('A10 部分文件保存保持未选索引与工作区');
    await save('保存剩余修改');
    const mainBase=head(cfg.local);
    await selectNode(mainBase);
    await page.locator('#code-create-from').click();
    await page.locator('#code-branch-name').fill('experiment');
    await page.locator('#code-preview-refresh').click();
    await confirm();
    assert.equal(git(cfg.local,'branch','--show-current'),'main');
    assert.equal(git(cfg.local,'rev-parse','experiment'),mainBase);
    await switchBranch('experiment');
    assert.equal(git(cfg.local,'branch','--show-current'),'experiment');
    edit(cfg.local,'experiment.txt','实验完成\n'); await refresh(); await save('实验阶段结果');
    const experiment=head(cfg.local);
    await switchBranch('main');
    edit(cfg.local,'main.txt','主线推进\n'); await refresh(); await save('主线工作');
    const beforeMerge=head(cfg.local);
    await merge('experiment');
    assert.deepEqual(git(cfg.local,'show','-s','--format=%P','HEAD').split(' '),[beforeMerge,experiment]);
    done('A13 A14 A15 网页建分支切换保存真实双父合并');
    const beforeRestore=head(cfg.local);
    await selectNode(cfg.local.initial); await page.locator('#code-restore').click(); await confirm();
    assert.equal(git(cfg.local,'show','-s','--format=%P','HEAD'),beforeRestore);
    assert.equal(git(cfg.local,'rev-parse','HEAD^{tree}'),git(cfg.local,'rev-parse',`${cfg.local.initial}^{tree}`));
    assert.ok(git(cfg.local,'rev-list','HEAD').includes(experiment));
    done('A20 A35 恢复新增版本且保留原历史完整主流程');
    // A stale confirmation must preserve edits, not silently refresh and execute.
    edit(cfg.local,'a.txt','预览版本\n'); await refresh();
    await page.locator('#code-save').click(); await page.locator('#code-save-message').fill('保留这段说明');
    await page.locator('#code-select-all').check();
    await page.waitForFunction(()=>!document.querySelector('#code-confirm').disabled);
    edit(cfg.local,'a.txt','确认前的外部新内容\n');
    const staleHead=head(cfg.local); await page.locator('#code-confirm').click();
    await page.waitForFunction(()=>document.querySelector('#code-dialog-feedback').textContent.includes('重新'));
    assert.equal(head(cfg.local),staleHead);
    assert.equal(await page.locator('#code-save-message').inputValue(),'保留这段说明');
    await page.locator('#code-preview-refresh').click(); await confirm();
    done('A29 过期预览拒绝写入且保留输入');
    // Explicit remote check changes only tracking cache until second confirmation.
    await open(cfg.remote); const remoteBefore=head(cfg.remote);
    await page.locator('#code-pull').click(); await page.locator('#code-fetch').click();
    await page.waitForFunction(()=>document.querySelector('#code-confirm') && !document.querySelector('#code-confirm').disabled);
    assert.equal(head(cfg.remote),remoteBefore);
    assert.equal(git(cfg.remote,'rev-parse','origin/main'),cfg.remote.incoming);
    await confirm(); assert.equal(head(cfg.remote),cfg.remote.incoming);
    edit(cfg.remote,'uploaded.txt','已提交的上传内容\n'); await refresh(); await save('准备上传的版本');
    edit(cfg.remote,'not-uploaded.txt','不应上传的内容\n'); await refresh();
    const beforePush=execFileSync('git',['--git-dir',cfg.remote.bare,'rev-parse','main'],{encoding:'utf8',windowsHide:true}).trim();
    await page.locator('#code-push').click(); await page.locator('#code-push-preview').click();
    await page.waitForFunction(()=>!document.querySelector('#code-confirm').disabled);
    assert.equal(execFileSync('git',['--git-dir',cfg.remote.bare,'rev-parse','main'],{encoding:'utf8',windowsHide:true}).trim(),beforePush);
    await confirm();
    assert.equal(execFileSync('git',['--git-dir',cfg.remote.bare,'rev-parse','main'],{encoding:'utf8',windowsHide:true}).trim(),head(cfg.remote));
    assert.ok(!git(cfg.remote,'ls-tree','-r','--name-only','HEAD').includes('not-uploaded.txt'));
    done('A25 A27 A36 检查后二次确认更新与仅上传已提交单分支');
    // Website owns both conflicts; first abort then re-merge and finish.
    await open(cfg.conflict); await merge('topic');
    await page.waitForSelector('#code-conflict-content');
    await page.locator('#code-conflict-content').fill('第一次草稿\n');
    await page.locator('#code-resolve').click();
    await page.waitForFunction(()=>document.querySelector('#code-resolve').textContent.includes('已解决'));
    await page.reload(); await ready();
    await page.waitForSelector('#code-conflict-content');
    assert.equal(await page.locator('#code-conflict-content').inputValue(),'第一次草稿\n');
    await page.locator('#code-merge-abort').click(); await page.locator('#code-abort-confirm').click();
    await page.waitForFunction(()=>!document.querySelector('#code-dialog').open);
    assert.equal(head(cfg.conflict),cfg.conflict.before);
    assert.equal(git(cfg.conflict,'status','--porcelain'),'');
    await merge('topic'); await page.waitForSelector('#code-conflict-content');
    await page.locator('#code-conflict-content').fill('手动合并后的科研内容\n'); await page.locator('#code-resolve').click();
    await page.waitForFunction(()=>document.querySelector('#code-resolve').textContent.includes('已解决'));
    await page.locator('#code-merge-files [data-file-id]').nth(1).click();
    await page.locator('#code-use-incoming').click(); await page.locator('#code-resolve').click();
    await page.waitForFunction(()=>!document.querySelector('#code-merge-complete').disabled);
    await page.locator('#code-merge-complete').click();
    await page.waitForFunction(()=>document.querySelector('#code-conflicts').hidden);
    assert.equal(git(cfg.conflict,'show','-s','--format=%P','HEAD').split(' ').length,2);
    assert.equal(fs.readFileSync(path.join(cfg.conflict.root,'conflict.txt'),'utf8'),'手动合并后的科研内容\n');
    assert.equal(fs.readFileSync(path.join(cfg.conflict.root,'second.txt'),'utf8'),'incoming\n');
    done('A17 A18 手动/整侧冲突解决刷新持久化取消及完成合并');
    // First commit identity is local and never automatically replays save.
    await open(cfg.identity); await page.locator('#code-save').click();
    await page.locator('#code-save-message').fill('保留首版本说明');
    await page.locator('#code-select-all').check();
    await page.locator('#code-confirm').click();
    await page.waitForSelector('#code-identity-name',{state:'visible'});
    await page.locator('#code-identity-name').fill('Local Researcher');
    await page.locator('#code-identity-email').fill('local@example.test');
    await page.locator('#code-identity-save').click();
    await page.waitForFunction(()=>document.querySelector('#code-dialog-feedback').textContent.includes('已设置'));
    assert.equal(await page.locator('#code-save-message').inputValue(),'保留首版本说明');
    assert.equal(git(cfg.identity,'config','--local','user.name'),'Local Researcher');
    await page.locator('#code-preview-refresh').click(); await confirm();
    assert.equal(git(cfg.identity,'log','-1','--format=%s'),'保留首版本说明');
    done('A03 A33 首版本补填本地身份后重新确认');
    await open(cfg.failure); await selectNode(cfg.failure.target);
    await page.locator('#code-restore').click();
    await page.waitForFunction(()=>!document.querySelector('#code-confirm').disabled);
    await page.locator('#code-confirm').click();
    await page.waitForFunction(()=>document.querySelector('#code-dialog-feedback').textContent.includes('文件已恢复，但版本尚未保存'));
    await ready();
    assert.equal(head(cfg.failure),cfg.failure.before);
    assert.equal(fs.readFileSync(path.join(cfg.failure.root,'result.txt'),'utf8'),'old result\n');
    await page.locator('#code-dialog-close').click();
    await save('修复原因后保存恢复结果');
    assert.equal(git(cfg.failure,'show','-s','--format=%P','HEAD'),cfg.failure.before);
    assert.equal(git(cfg.failure,'rev-parse','HEAD^{tree}'),git(cfg.failure,'rev-parse',cfg.failure.target+'^{tree}'));
    done('A22 恢复后提交故障明确反馈并从网页重新保存');
    await page.goto(`${cfg.origin}/project/${cfg.local.pid}/todos`);
    await page.locator(`a[href="/project/${cfg.local.pid}/code"]`).click(); await ready();
    assert.equal(await page.locator('#code-app').getAttribute('data-project-id'),cfg.local.pid);
    await page.locator('.console-project-switcher summary').click();
    await page.locator(`.console-project-menu a[href="/project/${cfg.remote.pid}/code"]`).click(); await ready();
    assert.equal(await page.locator('#code-app').getAttribute('data-project-id'),cfg.remote.pid);
    assert.equal(await page.locator(`.code-commit[data-oid="${cfg.local.initial}"]`).count(),0);
    await page.locator('.console-project-switcher summary').click();
    await page.locator(`.console-project-menu a[href="/project/${cfg.local.pid}/code"]`).click(); await ready();
    assert.equal(await page.locator('#code-app').getAttribute('data-project-id'),cfg.local.pid);
    done('A01 A37 从进度栏目进入代码并跨项目返回不串数据');
    await page.waitForSelector('#code-create-from');
    assert.equal(await page.locator('#code-detail .code-version-title').count(),1);
    const unchanged=head(cfg.local);
    await selectNode(cfg.local.initial);
    await page.locator('#code-detail [data-file-id]').first().click();
    await page.waitForFunction(()=>document.querySelector('#code-detail .code-diff-box')?.textContent.includes('初始'));
    assert.equal(head(cfg.local),unchanged);
    await page.locator('#code-close-detail').click();
    assert.equal(await page.locator('#code-detail').isVisible(),false);
    await selectNode(unchanged);
    await page.waitForSelector('#code-create-from');
    done('A08 节点详情与真实文件差异只读且可收起');
    for (const width of [320,580,1024]) {
      await page.setViewportSize({width,height:920});
      for (const theme of ['light','dark']) {
        await page.emulateMedia({colorScheme:theme,reducedMotion:'reduce'});
        await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),`page overflow ${width} ${theme}`);
        await page.screenshot({path:path.join(cfg.evidence,`code-${width}-${theme}.png`),fullPage:true});
      }
    }
    await page.setViewportSize({width:1360,height:940});
    await page.locator('#code-refresh').focus(); await page.keyboard.press('Tab');
    assert.ok(await page.evaluate(()=>document.activeElement!==document.body));
    done('A04 A05 A37 320/580/1024深浅色无全页溢出与键盘');
    const samples=[];
    for(let i=0;i<5;i++) { const start=performance.now(); await open(cfg.benchmark); await page.waitForFunction(()=>document.querySelectorAll('.code-commit').length===100); samples.push(performance.now()-start); await page.waitForSelector('#code-create-from'); }
    samples.sort((a,b)=>a-b);
    fs.writeFileSync(path.join(cfg.evidence,'performance.json'),JSON.stringify({samples,median:samples[2],busySamples,maxBusy:Math.max(...busySamples),commits:1000,files:1000},null,2));
    assert.ok(samples[2]<=2000,`首屏中位数 ${samples[2]}ms 超过2秒`);
    done(`A32 1000版本1000文件首屏五次中位${Math.round(samples[2])}ms`);
    assert.ok(busySamples.every(ms=>Number.isFinite(ms)&&ms<=100),`忙碌态 ${busySamples}`);
    await page.locator('#code-graph-more').click();
    await page.waitForFunction(()=>document.querySelectorAll('.code-commit').length===200);
    assert.equal(await page.locator('.code-commit').evaluateAll(nodes=>new Set(nodes.map(n=>n.dataset.oid)).size),200);
    done('A07 分页追加真实历史无重复');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(cfg.evidence,'result.json'),JSON.stringify({passed,errors},null,2));
  } catch(error) {
    await page.screenshot({path:path.join(cfg.evidence,'failure.png'),fullPage:true}).catch(()=>{});
    fs.writeFileSync(path.join(cfg.evidence,'failure.txt'),`${error.stack}\n\n${await page.locator('body').innerText()}`);
    throw error;
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
