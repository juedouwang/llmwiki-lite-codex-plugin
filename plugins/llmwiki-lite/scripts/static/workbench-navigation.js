/* Keep already-read column DOM in memory. Git writes never use this display cache. */
(() => {
  'use strict';
  const main = () => document.getElementById('main-content');
  if (!main() || !document.querySelector('.console-shell')) return;
  const entries = new Map(), pending = new Map();
  const key = value => { const u = new URL(value, location.href); return u.pathname + u.search; };
  const allowed = u => u.origin === location.origin && (u.pathname === '/projects' || u.pathname === '/reports' || /^\/project\/[^/]+(?:\/(?:todos|records|literature|code))?$/.test(u.pathname));
  const signal = (name, entry, extra = {}, cancelable = false) => document.dispatchEvent(new CustomEvent('workbench:' + name, {cancelable, detail: {root: entry.main, ...extra}}));
  const styles = new Set([...document.head.querySelectorAll('link[rel="stylesheet"]')].map(n => n.href));
  let installing = null;
  let current = key(location.href), serial = 0, index = Number(history.state?.workbenchIndex || 0), restoringHistory = false;
  function capture(doc, url, initialized = false) {
    return {url, main: doc.querySelector('#main-content'), sidebar: doc.querySelector('#console-sidebar')?.cloneNode(true),
      topbar: doc.querySelector('.console-topbar')?.cloneNode(true), title: doc.title,
      page: doc.body.dataset.workbenchPage || '', project: doc.body.dataset.workbenchProject || '',
      html: doc.querySelector('#main-content')?.innerHTML, initialized, scroll: 0, checked: Date.now()};
  }
  const initial = capture(document, current, true);
  entries.set(current, initial);
  history.replaceState({...history.state, workbenchIndex: index}, '', location.href);
  function installStyles(entry) {
    entry.main.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
      if (!styles.has(link.href)) { styles.add(link.href); document.head.append(link.cloneNode()); }
      link.remove();
    });
  }
  installStyles(initial);
  async function read(url, fresh = false) {
    if (!fresh && entries.has(url)) return entries.get(url);
    if (pending.has(url)) return pending.get(url);
    const task = (async () => {
      const response = await fetch(url, {headers: {'X-Workbench-Navigation': '1'}, cache: 'no-cache'});
      if (!response.ok || key(response.url) !== url || !response.headers.get('content-type')?.includes('text/html')) throw new Error('页面读取失败');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const entry = capture(doc, url);
      if (!entry.main || !entry.sidebar || !entry.topbar) throw new Error('页面不支持局部导航');
      if (!fresh) {
        // Bound speculative HTML only; visited pages retain their exact controls and state.
        const speculative = [...entries.values()].filter(e => !e.initialized);
        if (speculative.length >= 8) entries.delete(speculative[0].url);
        entries.set(url, entry);
      }
      return entry;
    })().finally(() => pending.delete(url));
    pending.set(url, task);
    return task;
  }
  async function initialize(entry) {
    if (entry.initialized) return;
    entry.initialized = true;
    for (const inert of [...entry.main.querySelectorAll('script')]) {
      // Only the local server's own page adapters execute, once per retained page.
      if (!inert.src || new URL(inert.src).origin !== location.origin || !new URL(inert.src).pathname.startsWith('/static/')) { inert.remove(); continue; }
      const script = document.createElement('script'); script.src = inert.src; script.async = false;
      await new Promise((resolve, reject) => {
        script.onload = resolve; script.onerror = () => reject(new Error('页面交互未载入，请刷新重试'));
        inert.replaceWith(script);
      });
    }
  }
  function allowedToLeave(entry) { return signal('before-leave', entry, {}, true); }
  function mount(entry, restored) {
    const prior = entries.get(current);
    if (prior) { prior.scroll = scrollY; signal('leave', prior); }
    window.closeLightbox?.(); window.toggleConsoleSidebar?.(false);
    document.querySelector('#console-sidebar').replaceWith(entry.sidebar.cloneNode(true));
    document.querySelector('.console-topbar').replaceWith(entry.topbar.cloneNode(true));
    main().replaceWith(entry.main);
    current = entry.url; document.title = entry.title;
    document.body.dataset.workbenchPage = entry.page; document.body.dataset.workbenchProject = entry.project;
    installStyles(entry);
    window.scrollTo(0, entry.scroll);
    signal('enter', entry, {restored});
  }
  function busy(entry) {
    return entry.main.querySelector('dialog[open], input:focus, textarea:focus, select:focus, [contenteditable="true"]:focus') ||
      [...entry.main.querySelectorAll('input:not([type=hidden]),textarea')].some(n => n.type === 'checkbox' || n.type === 'radio' ? n.checked !== n.defaultChecked : n.value !== n.defaultValue);
  }
  async function revalidate(entry) {
    // Git/progress adapters refresh their own real data without rebuilding their DOM.
    if (entry.main.querySelector('#code-app,#research-progress') || Date.now() - entry.checked < 15000) return;
    entry.checked = Date.now();
    try {
      const fresh = await read(entry.url, true);
      if (fresh.html === entry.html || installing || current !== entry.url || busy(entry) || !allowedToLeave(entry)) return;
      fresh.scroll = scrollY;
      signal('leave', entry); signal('dispose', entry);
      entries.set(entry.url, fresh);
      main().replaceWith(fresh.main); installStyles(fresh);
      installing = initialize(fresh); try { await installing; } finally { installing = null; }
      signal('enter', fresh, {restored: false});
      window.scrollTo(0, fresh.scroll);
    } catch { /* A failed background read never blanks previously read content. */ }
  }
  async function navigate(href, pop = false) {
    const u = new URL(href, location.href), url = key(u), prior = entries.get(current);
    if (url === current) { if (u.hash) document.getElementById(decodeURIComponent(u.hash.slice(1)))?.scrollIntoView(); return true; }
    if (prior && !allowedToLeave(prior)) return false;
    const token = ++serial;
    let entry = entries.get(url);
    try {
      if (!entry) entry = await read(url);
      if (installing) await installing;
      if (token !== serial) return true;
      if (prior && !allowedToLeave(prior)) return false;
      const restored = entry.initialized;
      if (!pop) { index += 1; history.pushState({workbenchIndex: index}, '', u.pathname + u.search + u.hash); }
      mount(entry, restored);
      if (!restored) { installing = initialize(entry); try { await installing; } finally { installing = null; } }
      // A restored page has already painted its real cached content before refresh starts.
      if (restored) void revalidate(entry);
      if (u.hash) document.getElementById(decodeURIComponent(u.hash.slice(1)))?.scrollIntoView();
      return true;
    } catch (error) {
      if (token === serial) {
        if (current !== url) location.assign(href);
        else { const message = document.createElement('p'); message.setAttribute('role','alert'); message.textContent = error.message; entry.main.prepend(message); }
      }
      return true;
    }
  }
  document.addEventListener('workbench:project-preferences', event => {
    // Update every retained sidebar so cached column returns cannot restore the old order.
    const ids = event.detail.project_order || [];
    function reorder(sidebar) {
      const menu = sidebar?.querySelector('.console-project-menu');
      if (!menu) return;
      const links = new Map([...menu.querySelectorAll('a[data-project-id]')].map(a => [a.dataset.projectId, a]));
      for (const id of [...ids].reverse()) if (links.has(id)) menu.prepend(links.get(id));
    }
    reorder(document.querySelector('#console-sidebar'));
    for (const entry of entries.values()) reorder(entry.sidebar);
  });
  // Window bubbling lets existing editor save/leave handlers consume the click first.
  window.addEventListener('click', event => {
    const a = event.target.closest?.('a[href]');
    if (!a || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || a.target || a.hasAttribute('download')) return;
    const u = new URL(a.href, location.href);
    if (!allowed(u) || key(u) === current) return;
    event.preventDefault(); void navigate(u.href);
  });
  window.addEventListener('popstate', async event => {
    if (restoringHistory) { restoringHistory = false; return; }
    const next = Number(event.state?.workbenchIndex);
    if (!Number.isFinite(next) || !allowed(new URL(location.href)) && !entries.has(key(location.href))) { location.reload(); return; }
    const previous = index;
    if (await navigate(location.href, true)) index = next;
    else { restoringHistory = true; history.go(previous - next); }
  });
  // Intent prefetch reads HTML only: never launches Git writes, remote checks or agents.
  let intent;
  function prefetch(event) {
    const a = event.target.closest?.('.console-sidebar a[href]');
    if (!a) return;
    const u = new URL(a.href, location.href);
    clearTimeout(intent);
    if (allowed(u) && !entries.has(key(u))) intent = setTimeout(() => { void read(key(u)).catch(() => {}); }, 70);
  }
  document.addEventListener('pointerover', prefetch);
  document.addEventListener('focusin', prefetch);
})();
