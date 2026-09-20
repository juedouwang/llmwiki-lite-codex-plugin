const {chromium} = require(process.env.LLMWIKI_PLAYWRIGHT || 'playwright');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const cfg = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const git = (repo, ...args) => execFileSync('git', args, {cwd: repo.root, encoding: 'utf8', windowsHide: true}).trim();
(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.LLMWIKI_BROWSER_CHANNEL || 'chrome'});
  const page = await browser.newPage({viewport: {width: 1360, height: 940}});
  const errors = [], requests = [], completed = [], details = new Map(), diffs = new Map(), samples = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.url().includes('/code/')) requests.push({url: request.url(), at: Date.now(), method: request.method()});
  });
  page.on('response', async response => {
    if (!response.url().includes('/code/')) return;
    completed.push({url: response.url(), at: Date.now()});
    try {
      const data = await response.json();
      if (response.url().includes('/code/commit/') && data.ok) details.set(response.url(), data);
      if (response.url().includes('/code/diff?') && data.ok) diffs.set(response.url(), data);
    } catch (_) { /* A deliberately aborted fixture request is not a page error. */ }
  });
  const prefix = project => `${cfg.origin}/api/project/${project.pid}/code`;
  const count = (project, part) => requests.filter(item => item.url.startsWith(prefix(project) + '/' + part)).length;
  const ready = () => page.waitForFunction(() => document.querySelector('#code-app')?.dataset.snapshotState === 'current');
  const detailURL = (project, oid) => prefix(project) + '/commit/' + oid;
  function hasDetail(project, oid, includeDiff) {
    const data = details.get(detailURL(project, oid));
    if (!data) return false;
    if (!includeDiff || !data.files.length) return true;
    return diffs.has(prefix(project) + '/diff?' + new URLSearchParams({kind:'commit', oid, file_id:data.files[0].file_id}));
  }
  async function waitDetails(project, oids, includeDiff = false) {
    const deadline = Date.now() + 30000;
    while (!oids.every(oid => hasDetail(project, oid, includeDiff))) {
      assert.ok(Date.now() < deadline, 'bounded prefetch must finish');
      await sleep(50);
    }
    // Response observers run before the page consumes and memoizes the JSON.
    await sleep(50);
  }
  async function cachedClick(oid, includeDiff = true) {
    const result = await page.evaluate(async oid => {
      const before = performance.now();
      document.querySelector(`.code-commit[data-oid="${oid}"]`).click();
      const actual = document.querySelector('#code-commit-oid')?.textContent;
      const title = document.querySelector('#code-detail .code-version-title')?.textContent;
      const loading = document.querySelector('#code-detail').textContent.includes('读取版本…');
      const diffReady = document.querySelector('#code-detail .code-diff-box')?.getAttribute('aria-busy') === 'false';
      const diffText = document.querySelector('#code-detail .code-diff')?.textContent;
      const synchronousMs = performance.now() - before;
      // The second animation frame is after one frame was available for painting.
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return {actual, title, loading, diffReady, diffText, synchronousMs, paintMs: performance.now() - before};
    }, oid);
    assert.equal(result.actual, oid, 'real cached detail must be present in the click task');
    assert.ok(result.title);
    assert.equal(result.loading, false, 'a loading placeholder is not cached content');
    if (includeDiff) {
      assert.equal(result.diffReady, true, 'the first real file diff is synchronously restored too');
      assert.ok(result.diffText?.includes('experiment') || result.diffText?.includes('external fixture'));
    }
    assert.ok(result.paintMs < 100, `cached detail paint opportunity ${result.paintMs}ms`);
    samples.push(result);
  }
  const [first, second] = cfg.projects;
  try {
    await page.goto(`${cfg.origin}/project/${first.pid}/code`);
    await ready();
    await page.waitForSelector('#code-create-from');
    await waitDetails(first, cfg.commits.slice(0, 6), true);
    assert.equal(count(first, 'commit/'), 6, 'prefetch is bounded to six visible commits');
    assert.equal(await page.locator('#code-commit-metadata').evaluate(node => node.open), false);
    const initialDone = Math.max(...completed.filter(item =>
      item.url === prefix(first) + '/status' || item.url.includes('/graph?') ||
      item.url === detailURL(first, cfg.commits[0])).map(item => item.at));
    const prefetched = requests.filter(item => item.url.includes('/commit/') && !item.url.endsWith(cfg.commits[0]));
    assert.ok(prefetched.every(item => item.at >= initialDone), 'prefetch must not compete with initial reads');
    for (let i = 1; i < prefetched.length; i++) {
      const previousDone = completed.find(item => item.url === prefetched[i - 1].url);
      assert.ok(prefetched[i].at >= previousDone.at, 'prefetch is serial');
    }
    for (const oid of [...cfg.commits.slice(0, 6), ...cfg.commits.slice(0, 6).reverse()]) await cachedClick(oid);
    assert.equal(count(first, 'commit/'), 6, 'cached clicks cannot create duplicate GETs');
    await page.locator('#code-close-detail').click();
    await cachedClick(cfg.commits[0]);
    assert.equal(count(first, 'commit/'), 6, 'closing does not discard an immutable detail');
    console.log('PASS bounded serial prefetch / immutable cache / metadata collapsed / actual-content <100ms');

    // Keep a cold real API response in flight and select a cached node instead.
    const slowOid = cfg.commits[7];
    let slowStarted;
    const started = new Promise(resolve => { slowStarted = resolve; });
    await page.route(detailURL(first, slowOid), async route => {
      slowStarted();
      const response = await route.fetch();
      await sleep(250);
      await route.fulfill({response});
    });
    await page.locator(`.code-commit[data-oid="${slowOid}"]`).click();
    await started;
    await page.locator(`.code-commit[data-oid="${slowOid}"]`).click();
    await cachedClick(cfg.commits[1]);
    await waitDetails(first, [slowOid]);
    assert.equal(await page.locator('#code-commit-oid').textContent(), cfg.commits[1], 'late detail must not replace latest selection');
    assert.equal(count(first, 'commit/' + slowOid), 1, 'concurrent selection shares one read');
    await cachedClick(slowOid, false);
    await waitDetails(first, [slowOid], true);
    await page.unroute(detailURL(first, slowOid));
    console.log('PASS in-flight deduplication / late response isolation / cold read becomes warm');

    const beforeStatus = count(first, 'status'), beforeGraph = count(first, 'graph'), beforeCommit = count(first, 'commit/');
    await page.locator('#code-refresh').click();
    await ready();
    assert.equal(count(first, 'status'), beforeStatus + 1);
    assert.equal(count(first, 'graph'), beforeGraph + 1);
    assert.equal(count(first, 'commit/'), beforeCommit, 'explicit refresh re-reads mutable state, not immutable OIDs');
    assert.equal(await page.locator('#code-commit-oid').textContent(), slowOid);

    // This is a test-owned temporary repository, never a registered research repo.
    fs.writeFileSync(path.join(first.root, 'result.txt'), 'external fixture update\n');
    git(first, 'add', 'result.txt'); git(first, 'commit', '-m', '外部新增的真实版本');
    const newHead = git(first, 'rev-parse', 'HEAD');
    await page.locator('#code-refresh').click();
    await ready();
    assert.equal(await page.locator(`.code-commit[data-oid="${newHead}"]`).count(), 1);
    assert.equal(await page.locator('#code-commit-oid').textContent(), slowOid, 'fresh graph preserves selected immutable detail');
    await waitDetails(first, [newHead], true);
    await cachedClick(newHead);
    console.log('PASS fresh status/graph / external commit visible / selected detail preserved');

    await page.route(prefix(first) + '/status', route => route.fulfill({status: 503, contentType: 'application/json',
      body: JSON.stringify({ok: false, message: 'fixture read unavailable'})}));
    await page.locator('#code-refresh').click();
    await page.waitForFunction(() => document.querySelector('#code-app').dataset.snapshotState === 'stale');
    assert.equal(await page.locator('#code-commit-oid').textContent(), newHead);
    assert.equal(await page.locator('#code-merge').isDisabled(), true, 'a failed current read cannot authorize writes');
    assert.ok((await page.locator('#code-feedback').textContent()).includes('上次读取'));
    await page.unroute(prefix(first) + '/status');
    await page.locator('#code-refresh').click(); await ready();
    console.log('PASS failed refresh keeps display with stale label and disables writes');

    // Exercise BFCache lifecycle while the cached Git main is detached, as it
    // is when returning to a non-Git column before revisiting Git. These are
    // synthetic lifecycle events, not a claim of browser BFCache eligibility.
    const beforeResume = {status: count(first, 'status'), graph: count(first, 'graph'), commit: count(first, 'commit/')};
    await page.evaluate(() => {
      const main = document.querySelector('#code-app').closest('main');
      document.dispatchEvent(new CustomEvent('workbench:leave', {detail: {root: main}}));
      const marker = document.createComment('cached Git main');
      main.replaceWith(marker);
      window.detachedGitFixture = {main, marker};
      window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
      window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await sleep(250);
    assert.equal(await page.locator('#code-app').count(), 0, 'Git root is genuinely detached');
    assert.equal(count(first, 'status'), beforeResume.status, 'BFCache/focus cannot refresh a detached root');
    assert.equal(count(first, 'graph'), beforeResume.graph);
    await page.evaluate(() => {
      const {main, marker} = window.detachedGitFixture;
      marker.replaceWith(main);
      document.dispatchEvent(new CustomEvent('workbench:enter', {detail: {root: main, restored: true}}));
      delete window.detachedGitFixture;
    });
    assert.equal(await page.locator('#code-commit-oid').textContent(), newHead, 'enter must retain selected content');
    await page.waitForFunction(() => document.querySelector('#code-app').dataset.snapshotState === 'current', null, {timeout: 5000});
    assert.equal(count(first, 'status'), beforeResume.status + 1, 'reattached Git must resume fresh reads');
    assert.equal(count(first, 'graph'), beforeResume.graph + 1);
    assert.equal(count(first, 'commit/'), beforeResume.commit, 'BFCache restore retains immutable details');
    await cachedClick(newHead);
    console.log('PASS detached BFCache lifecycle / enter resumes fresh reads / cached content retained');

    // Shared commit hashes must not leak the first project's file bindings.
    await page.goto(`${cfg.origin}/project/${second.pid}/code`);
    await ready(); await waitDetails(second, cfg.commits.slice(0, 6), true);
    assert.equal(count(second, 'commit/'), 6);
    assert.notEqual(details.get(detailURL(first, cfg.commits[0])).files[0].file_id,
      details.get(detailURL(second, cfg.commits[0])).files[0].file_id);
    await cachedClick(cfg.commits[0]);
    console.log('PASS project-scoped OID cache and repository-bound file IDs');
    assert.deepEqual(errors, []);
    assert.equal(requests.filter(item => item.method !== 'GET').length, 0, 'display caching never writes or syncs');
    console.log(JSON.stringify({samples: samples.length,
      maxSynchronousMs: Math.max(...samples.map(item => item.synchronousMs)),
      maxPaintOpportunityMs: Math.max(...samples.map(item => item.paintMs)),
      errors}, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
