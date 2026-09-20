/* Real project APIs only. Display snapshots never authorize writes or remote sync. */
(() => {
  'use strict';
  const root = document.getElementById('code-app');
  if (!root || root.dataset.codeInitialized) return;
  root.dataset.codeInitialized = 'true';
  // A retained page may be detached while another project has the same element IDs.
  const $ = id => root.querySelector(`#code-${id}`);
  const endpoint = root.dataset.api;
  const projectId = root.dataset.projectId;
  const PAGE_SIZE = 100;
  const ROW_HEIGHT = 68;
  const DETAIL_CACHE_SIZE = 48;
  const PREFETCH_COUNT = 6;
  const state = {
    status: null, graph: null, nodes: [], selected: null, detail: null,
    detailOpen: true, modal: null, writing: false, refresh: 0, graphRequest: 0,
    detailRequest: 0, mergeRequest: 0, alive: true, active: true, lifecycle: 0, merge: null,
    conflictId: null, drafts: new Map(), mergePending: false, statusError: false,
  };
  const reads = new Map();
  // These maps belong to this project root, not to whichever project is visible.
  // Only immutable OID details are memoized; status/graph/previews always use the API.
  const detailCache = new Map();
  const detailReads = new Map();
  const diffCache = new Map(), diffReads = new Map();
  const globalListeners = new AbortController();
  const listen = (target, name, handler) => target.addEventListener(name, handler, {signal: globalListeners.signal});
  let prefetchTimer = null, prefetchRun = null;
  const fileKey = f => JSON.stringify([f.old_path || '', f.path]);
  const short = oid => String(oid || '').slice(0, 10);
  const text = value => value === null || value === undefined ? '' : String(value);
  const list = value => Array.isArray(value) ? value : [];
  const samePage = () => state.alive && state.active && root.isConnected && root.dataset.projectId === projectId;

  function el(tag, value, className) {
    const node = document.createElement(tag);
    if (value !== undefined && value !== null) node.textContent = String(value);
    if (className) node.className = className;
    return node;
  }
  function button(label, handler, id, className) {
    const node = el('button', label, className);
    node.type = 'button';
    if (id) node.id = `code-${id}`;
    if (handler) node.addEventListener('click', handler);
    return node;
  }
  function field(label, input) {
    const node = el('label', undefined, 'code-field');
    node.append(el('span', label), input);
    return node;
  }
  function input(id, value = '', type = 'text') {
    const node = el('input'); node.id = `code-${id}`; node.type = type; node.value = value;
    node.autocomplete = 'off'; return node;
  }
  function feedback(message, error = false, target = $('feedback')) {
    target.textContent = message; target.dataset.error = String(error);
  }
  function fail(message, code = 'invalid_response') {
    return Object.assign(new Error(message), {code});
  }
  function errorText(error) {
    const messages = {
      state_changed: '内容已变化，请重新查看并确认。原有输入已保留。',
      preview_expired: '预览已过期，请重新查看并确认。原有输入已保留。',
      snapshot_expired: '版本记录已变化，请刷新后再加载，未提交输入已保留。',
      repo_busy: '仓库正被其他操作使用，请稍后重新查看并确认。',
      dirty_worktree: '先保存当前修改，再重新发起此操作。',
      identity_required: '缺少作者身份，请填写名称和邮箱，仅用于当前仓库。',
      auth_required: '无法非交互认证，请在原 Git 工具完成认证后重试。',
      result_uncertain: '上传结果待核对，请检查远端；不会自动重试。',
      unsupported_conflict: '此冲突暂不支持网页解决，可取消本次合并或在原工具处理。',
      no_remote: '尚未配置远端，请先在现有 Git 工具中配置。',
    };
    return messages[error.code] || error.message || '操作未完成，请刷新实际状态后重试。';
  }
  async function api(path, {method = 'GET', body, key} = {}) {
    if (!samePage()) throw fail('页面已切换。', 'page_changed');
    const lifecycle = state.lifecycle;
    const controller = new AbortController();
    if (key) { reads.get(key)?.abort(); reads.set(key, controller); }
    try {
      const response = await fetch(endpoint + path, {
        method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: method === 'POST'
          ? {'Content-Type': 'application/json', 'X-Notebook-Request': '1', Accept: 'application/json'}
          : {Accept: 'application/json'},
        ...(body === undefined ? {} : {body: JSON.stringify(body)}),
      });
      let data;
      try { data = await response.json(); }
      catch (_) { throw fail(`服务器未返回有效 JSON（HTTP ${response.status}），请检查接口接入。`); }
      if (!samePage() || lifecycle !== state.lifecycle) throw fail('页面已切换。', 'page_changed');
      if (!response.ok || data?.ok === false) {
        const error = fail(data?.message || `请求未完成（HTTP ${response.status}）。`, data?.code || 'request_failed');
        error.data = data; throw error;
      }
      const emptyMerge = method === 'GET' && path === '/merge' && data === null;
      if (!emptyMerge && (!data || typeof data !== 'object' || data.ok !== true)) {
        throw fail('接口缺少有效的 ok 字段，未确认任何操作成功。');
      }
      return data;
    } catch (error) {
      if (error instanceof TypeError) throw fail(method === 'POST'
        ? '连接中断，操作结果待核对；请刷新实际状态，不会自动重试。'
        : '无法读取仓库，请检查本地服务后重试。', method === 'POST' ? 'result_uncertain' : 'network_error');
      throw error;
    } finally {
      if (key && reads.get(key) === controller) reads.delete(key);
    }
  }
  const post = (path, body) => api(path, {method: 'POST', body});
  const query = params => '?' + new URLSearchParams(params).toString();
  const ignored = error => error.name === 'AbortError' || error.code === 'page_changed';
  const files = () => list(state.status?.files);
  const hasHead = () => Boolean(state.status?.head?.oid);
  const writable = () => state.status?.capabilities?.write === true;

  function writeReason(action) {
    if (!state.status) return '尚未读取仓库状态。';
    if (state.statusError) return '当前状态未能核对，请刷新后再操作。';
    if (!writable()) return text(state.status.capabilities?.reason) || '当前仓库只读。';
    if (state.writing) return '当前写操作尚未结束。';
    if (state.status.ongoing) return '当前有未完成的 Git 操作，请先处理或取消。';
    if (state.status.head?.detached && !['create_branch', 'switch_branch', 'fetch'].includes(action)) {
      return 'HEAD 处于游离状态，请先创建并切换至本地分支。';
    }
    if (!hasHead() && !['save', 'fetch'].includes(action)) return '仓库尚无版本，请先保存首个版本。';
    return '';
  }
  function guard(action) {
    const reason = writeReason(action);
    if (reason) { feedback(reason, true); return false; }
    if (['switch_branch', 'merge', 'restore', 'pull_apply'].includes(action) && files().length) {
      const m = openModal('先保存当前修改', 'blocked');
      m.body.append(el('p', `还有 ${files().length} 个文件未保存。不会暂存备份、丢弃或带着修改切换。`, 'code-summary'));
      m.actions.append(button('查看并保存', () => { closeModal(); openAction('save', {}); }, 'dirty-save', 'code-primary'));
      return false;
    }
    return true;
  }
  function renderStatus() {
    const s = state.status;
    if (!s) return;
    const head = s.head || {};
    $('branch-label').textContent = head.detached ? `游离 HEAD · ${short(head.oid)}` : head.branch || '尚无分支';
    const reasons = [];
    if (!writable()) reasons.push(text(s.capabilities?.reason) || '当前仓库仅可读取。');
    if (head.detached) reasons.push('HEAD 游离：可以查看或创建分支，切至本地分支后才能保存、合并、恢复与上传。');
    if (s.ongoing?.external) reasons.push('检测到外部 Git 操作，请在原工具处理后刷新。网页不会接管或取消。');
    $('capability').hidden = !reasons.length; $('capability').textContent = reasons.join('\n');
    $('change-title').textContent = !writable() ? '当前仓库仅支持浏览' : files().length ? `${files().length} 个文件有改动` : '所有修改已保存';
    $('change-note').textContent = !writable() ? '仅展示版本历史，未执行工作区过滤器检查。' : s.ongoing ? '有未完成的 Git 操作，其他写操作暂停。'
      : files().length ? '尚未保存为版本 · 按完整文件保存，不自动上传'
        : head.unborn ? '工作区干净 · 尚无提交' : '工作区干净';
    $('save').hidden = !files().length;
    $('save').disabled = Boolean(writeReason('save')) || !files().length;
    $('merge').disabled = Boolean(writeReason('merge'));
    $('push').disabled = Boolean(writeReason('push'));
    $('pull').disabled = Boolean(writeReason('fetch'));
    for (const name of ['save', 'merge', 'push', 'pull']) {
      $(name).title = writeReason(name === 'pull' ? 'fetch' : name);
    }
    const upstream = s.upstream;
    const count = $('push-count');
    count.hidden = !upstream || !Number.isInteger(upstream.ahead);
    count.textContent = count.hidden ? '' : String(upstream.ahead);
    count.title = '基于本地远端缓存的待上传版本数';
    const remote = list(s.remotes).find(item => item.remote_id === upstream?.remote_id);
    $('cache').textContent = upstream
      ? `${remote?.name || '跟踪远端'}/${upstream.branch} · 缓存${s.last_fetch_at ? `检查于 ${formatTime(s.last_fetch_at)}` : '尚未检查'}`
      : '尚无跟踪目标 · 不自动检查远端';
    const menu = $('branch-menu');
    const menuSignature = JSON.stringify([s.branches, head, state.writing, state.statusError, s.capabilities, s.ongoing]);
    if (menu.dataset.signature !== menuSignature) {
      menu.dataset.signature = menuSignature; menu.replaceChildren();
      for (const branch of list(s.branches)) {
        const control = button(branch.name + (branch.current ? ' · 当前' : ''), () => {
          $('branches').open = false;
          if (!branch.current) openAction('switch_branch', {branch: branch.name});
        });
        control.dataset.branch = branch.name;
        control.setAttribute('aria-current', String(Boolean(branch.current)));
        control.disabled = branch.current || Boolean(writeReason('switch_branch'));
        menu.append(control);
      }
      const create = button('＋ 新建分支（不切换）', () => {
        $('branches').open = false; openAction('create_branch', {target_oid: state.status.head.oid});
      }, 'new-branch');
      create.disabled = Boolean(writeReason('create_branch')); menu.append(create);
    }
    if (state.modal) updateConfirm(state.modal);
    for (const [id, action] of [['create-from', 'create_branch'], ['restore', 'restore']]) {
      if ($(id)) { $(id).disabled = Boolean(writeReason(action)); $(id).title = writeReason(action); }
    }
  }
  function graphTime(value) {
    const date = new Date(typeof value === 'number' ? value * (value < 1e12 ? 1000 : 1) : value);
    if (Number.isNaN(date.getTime())) return '时间未提供';
    const today = new Date(), yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
    if (date.toDateString() === today.toDateString()) return compactTime(date);
    if (date.toDateString() === yesterday.toDateString()) return '昨天';
    return date.toLocaleDateString('zh-CN', {year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric', month: 'numeric', day: 'numeric'});
  }
  function compactTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '时间未提供' : date.toLocaleTimeString('zh-CN',
      {hour12: false, hour: '2-digit', minute: '2-digit'});
  }
  function formatTime(value) {
    if (!value) return '时间未提供';
    const date = new Date(typeof value === 'number' ? value * (value < 1e12 ? 1000 : 1) : value);
    return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString('zh-CN', {hour12: false});
  }

  function refreshBusy(busy) {
    const control = $('refresh');
    control.disabled = busy;
    control.setAttribute('aria-busy', String(busy));
    control.setAttribute('aria-label', busy ? '正在刷新版本记录' : '刷新版本记录');
    control.title = busy ? '正在刷新版本记录' : '刷新版本记录';
    // Keep the server-rendered SVG intact, including when a retained page leaves.
  }
  async function refresh() {
    if (!samePage()) return false;
    clearTimeout(prefetchTimer);
    const serial = ++state.refresh;
    root.dataset.snapshotState = 'checking';
    const showingSnapshot = Boolean(state.status && !state.writing && !state.modal);
    if (showingSnapshot) feedback('显示上次读取结果，正在后台核对本地仓库…');
    refreshBusy(true);
    const statusJob = (async () => {
      try {
        const s = await api('/status', {key: 'status'});
        if (serial !== state.refresh) return;
        if (!s?.head || !Array.isArray(s.files) || !s.capabilities) throw fail('状态接口字段不完整。');
        state.status = s; state.statusError = false; renderStatus();
        if (!state.selected && s.head.oid && state.detailOpen) selectCommit(s.head.oid);
        if (s.ongoing) await refreshMerge();
        else if (!state.mergePending) {
          state.merge = null; $('conflicts').hidden = true; $('workspace').hidden = false;
        }
        return true;
      } catch (error) {
        if (!ignored(error) && serial === state.refresh) {
          state.statusError = true;
          feedback((state.status ? '显示上次读取结果；当前状态未能核对。' : '') + errorText(error), true);
          if (state.status) renderStatus();
          else {
            $('change-title').textContent = '无法读取工作区';
            $('change-note').textContent = errorText(error);
            for (const id of ['save', 'merge', 'pull', 'push']) $(id).disabled = true;
            $('branch-menu').replaceChildren();
            $('branch-label').textContent = '状态不可用';
          }
          if (state.modal) updateConfirm(state.modal);
        }
      }
    })();
    const [statusRead, graphRead] = await Promise.all([statusJob, loadGraph(false)]);
    const fresh = serial === state.refresh && statusRead === true && graphRead === true;
    if (serial === state.refresh && samePage()) {
      refreshBusy(false);
      root.dataset.snapshotState = fresh ? 'current' : 'stale';
      if (fresh && showingSnapshot) feedback('本地状态已核对 · 操作仍需预览确认。');
      schedulePrefetch();
    }
    return fresh;
  }
  async function loadGraph(append) {
    const serial = ++state.graphRequest;
    const prior = state.graph;
    if (append && (!prior?.has_more || !prior.snapshot_id)) return;
    const page = append ? prior.page + 1 : 0;
    const params = {page: String(page), page_size: String(PAGE_SIZE)};
    if (append) params.snapshot_id = prior.snapshot_id;
    $('graph-more').disabled = true; $('graph-more').textContent = '读取中…';
    try {
      const graph = await api('/graph' + query(params), {key: 'graph'});
      if (serial !== state.graphRequest) return;
      if (!Array.isArray(graph?.nodes) || !graph.snapshot_id) throw fail('版本图接口字段不完整。');
      if (append && graph.snapshot_id !== prior.snapshot_id) throw fail('版本图快照已变化。', 'snapshot_expired');
      if (!append && state.graph?.snapshot_id === graph.snapshot_id && state.nodes.length > graph.nodes.length) {
        // Keep already loaded pages from the very same snapshot on focus refresh.
        state.graph = {...state.graph, branches: graph.branches, refs: graph.refs};
      } else {
        state.nodes = append ? [...state.nodes, ...graph.nodes] : graph.nodes;
        state.graph = {...graph, page};
      }
      // Stable snapshots retain the existing rows, scroll position and focus.
      if (append || !prior || prior.snapshot_id !== graph.snapshot_id ||
          JSON.stringify([prior.branches, prior.refs, prior.head_oid]) !==
          JSON.stringify([state.graph.branches, state.graph.refs, state.graph.head_oid])) renderGraph();
      if (!state.selected && state.detailOpen && graph.head_oid) selectCommit(graph.head_oid);
      // A changed branch/HEAD cannot change an already-read commit OID. Do not
      // blank and re-fetch the user's selected detail when the graph refreshes.
      return true;
    } catch (error) {
      if (!ignored(error) && serial === state.graphRequest) {
        feedback(['snapshot_expired', 'state_changed'].includes(error.code)
          ? '版本记录已变化，请刷新后再加载，未提交输入已保留。' : errorText(error), true);
        if (!state.nodes.length) { $('graph-empty').hidden = false; $('graph-empty').textContent = errorText(error); }
        if (error.code === 'snapshot_expired' || error.code === 'state_changed') {
          if (state.graph) state.graph.has_more = false;
          $('graph-more').hidden = true;
        }
      }
    } finally {
      if (serial === state.graphRequest) { $('graph-more').disabled = false; $('graph-more').textContent = '加载更早版本'; }
    }
  }
  function svgElement(tag, attributes) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }
  function renderGraph() {
    const graph = $('graph'); graph.replaceChildren();
    $('graph-empty').hidden = Boolean(state.nodes.length);
    $('graph-empty').textContent = '仓库尚无版本，选择工作区文件即可保存首个版本。';
    $('graph-more').hidden = !state.graph?.has_more;
    if (!state.nodes.length) return;
    const position = new Map();
    const lane = value => Number.isInteger(value) && value >= 0 ? value : 0;
    const boundary = new Map(list(state.graph.boundary_parents).map(parent => [parent.oid, parent.lane]));
    let maxLane = Math.max(0, ...[...boundary.values()].map(lane));
    state.nodes.forEach((node, index) => {
      maxLane = Math.max(maxLane, lane(node.lane), ...list(node.parent_lanes).map(lane));
      position.set(node.oid, {x: 20 + lane(node.lane) * 25, y: index * ROW_HEIGHT + ROW_HEIGHT / 2});
    });
    const width = Math.max(66, 40 + maxLane * 25);
    const height = state.nodes.length * ROW_HEIGHT;
    graph.style.setProperty('--code-graph-width', `${width}px`);
    const svg = svgElement('svg', {width, height, viewBox: `0 0 ${width} ${height}`, 'aria-hidden': 'true', class: 'code-graph-svg'});
    const headLane = lane(state.nodes.find(n => n.oid === state.graph.head_oid)?.lane);
    const color = n => lane(n) === headLane ? 'var(--code-lane-0)' : `var(--code-lane-${1 + lane(n) % 3})`;
    for (const node of state.nodes) {
      const start = position.get(node.oid);
      list(node.parents).forEach((oid, index) => {
        const parentLane = node.parent_lanes?.[index] ?? boundary.get(oid);
        if (!position.has(oid) && !Number.isInteger(parentLane)) return; // Never guess an unloaded parent's lane.
        const end = position.get(oid) || {x: 20 + lane(parentLane) * 25, y: height};
        const distance = end.y - start.y;
        svg.append(svgElement('path', {
          d: start.x === end.x ? `M ${start.x} ${start.y} L ${end.x} ${end.y}`
            : `M ${start.x} ${start.y} C ${start.x} ${start.y + distance * .6} ${end.x} ${start.y + distance * .4} ${end.x} ${end.y}`,
          fill: 'none', stroke: color(node.lane), 'stroke-width': 1.7,
          ...(!position.has(oid) ? {'stroke-dasharray': '4 4'} : {}),
          'data-parent': oid, 'data-child': node.oid,
        }));
      });
    }
    const fragment = document.createDocumentFragment();
    for (const node of state.nodes) {
      const point = position.get(node.oid);
      svg.append(svgElement('circle', {
        cx: point.x, cy: point.y, r: 8, fill: 'var(--panel)', stroke: color(node.lane), 'stroke-width': 1,
        'data-selected-oid': node.oid, style: state.detailOpen && state.selected === node.oid ? '' : 'display:none',
      }));
      svg.append(svgElement('circle', {cx: point.x, cy: point.y, r: 4.5, fill: color(node.lane), stroke: 'var(--panel)', 'stroke-width': 1.5}));
      const row = button(null, () => selectCommit(node.oid), null, 'code-commit');
      row.dataset.oid = node.oid; row.setAttribute('aria-pressed', String(state.detailOpen && state.selected === node.oid));
      row.setAttribute('aria-label', `查看版本：${node.subject || '无标题版本'} · ${short(node.oid)}`);
      const copy = el('span', undefined, 'code-commit-copy');
      const title = el('span', node.subject || '无标题版本', 'code-commit-title'); title.title = node.subject || '';
      const meta = el('span', undefined, 'code-commit-meta');
      for (const branch of list(state.graph.branches).filter(b => b.oid === node.oid)) {
        const label = branch.name + (branch.current ? ' · 当前' : branch.remote ? ' · 远端缓存' : '');
        const tag = el('span', label, `code-tag${branch.current ? ' code-tag-current' : branch.remote ? ' code-tag-remote' : ''}`);
        tag.title = label; meta.append(tag);
      }
      for (const tag of list(node.tags)) meta.append(el('span', `标签 ${text(tag)}`, 'code-tag'));
      meta.append(el('span', node.short_oid || short(node.oid), 'code-hash'));
      copy.append(title, meta);
      const stamp = node.committed_at_iso || node.committed_at;
      const time = el('span', graphTime(stamp), 'code-commit-time'); time.title = formatTime(stamp);
      row.append(copy, time); fragment.append(row);
    }
    graph.append(svg, fragment);
  }
  function cachedDetail(oid) {
    const value = detailCache.get(oid);
    if (value) { detailCache.delete(oid); detailCache.set(oid, value); }
    return value;
  }
  function readDetail(oid) {
    const cached = cachedDetail(oid);
    if (cached) return Promise.resolve(cached);
    if (detailReads.has(oid)) return detailReads.get(oid);
    const request = (async () => {
      const detail = await api('/commit/' + encodeURIComponent(oid), {key: `commit:${oid}`});
      if (detail?.oid !== oid || !Array.isArray(detail.files)) throw fail('版本详情与所选版本不一致。');
      detailCache.set(oid, detail);
      while (detailCache.size > DETAIL_CACHE_SIZE) detailCache.delete(detailCache.keys().next().value);
      return detail;
    })();
    detailReads.set(oid, request);
    const forget = () => { if (detailReads.get(oid) === request) detailReads.delete(oid); };
    request.then(forget, forget);
    return request;
  }
  function schedulePrefetch() {
    clearTimeout(prefetchTimer);
    if (!samePage() || document.hidden || state.writing || state.mergePending ||
        state.modal || !state.status || !state.graph || !state.detail ||
        reads.has('status') || reads.has('graph') || diffReads.size || prefetchRun) return;
    // Let both initial reads and the selected detail finish first. One local GET
    // at a time, at most six visible versions; never fetch/push a Git remote.
    prefetchTimer = setTimeout(async () => {
      const run = {}; prefetchRun = run;
      try {
        for (const node of state.nodes.slice(0, PREFETCH_COUNT)) {
          if (prefetchRun !== run || !samePage() || document.hidden || state.writing || state.mergePending ||
              state.modal || reads.has('status') || reads.has('graph') || detailReads.size || diffReads.size) break;
          try {
            const detail = await readDetail(node.oid);
            const first = detail.files[0];
            if (first && samePage() && prefetchRun === run && !state.writing && !state.modal &&
                !reads.has('status') && !reads.has('graph')) {
              await readCommitDiff({kind: 'commit', oid: detail.oid, file_id: first.file_id});
            }
          } catch (_) { /* Optional prefetch never replaces visible content or errors. */ }
        }
      } finally { if (prefetchRun === run) prefetchRun = null; }
    }, 200);
  }
  async function selectCommit(oid) {
    const serial = ++state.detailRequest;
    state.selected = oid; state.detailOpen = true;
    $('workspace').classList.remove('code-no-detail'); $('detail').hidden = false;
    renderGraphSelection();
    const cached = cachedDetail(oid);
    if (cached) {
      // Render in this click's task, without awaiting any network or timer.
      state.detail = cached; renderDetail(cached); schedulePrefetch(); return;
    }
    state.detail = null;
    $('detail').replaceChildren(detailHeading(), el('p', '读取版本…', 'code-muted'));
    try {
      const detail = await readDetail(oid);
      if (!samePage() || serial !== state.detailRequest || !state.detailOpen) return;
      state.detail = detail; renderDetail(detail);
    } catch (error) {
      if (!ignored(error) && serial === state.detailRequest) {
        state.detail = null;
        $('detail').replaceChildren(detailHeading(), el('p', errorText(error), 'code-notice'));
        if (state.status?.head?.oid && state.status.head.oid !== oid) {
          $('detail').append(button('查看当前 HEAD', () => selectCommit(state.status.head.oid), 'view-head', 'code-outline'));
        }
      }
    } finally { schedulePrefetch(); }
  }
  function renderGraphSelection() {
    for (const circle of $('graph').querySelectorAll('[data-selected-oid]')) {
      circle.style.display = state.detailOpen && state.selected === circle.dataset.selectedOid ? '' : 'none';
    }
    for (const row of $('graph').querySelectorAll('[data-oid]')) {
      row.setAttribute('aria-pressed', String(state.detailOpen && state.selected === row.dataset.oid));
    }
  }
  function detailHeading() {
    const heading = el('div', undefined, 'code-detail-heading'); heading.append(el('span', '版本详情'));
    const close = button('×', () => {
      state.detailOpen = false; ++state.detailRequest;
      $('detail').hidden = true; $('workspace').classList.add('code-no-detail'); renderGraphSelection();
      [...$('graph').querySelectorAll('[data-oid]')].find(row => row.dataset.oid === state.selected)?.focus();
    }, 'close-detail');
    const closeIcon = $('dialog-close').querySelector('svg');
    if (closeIcon) close.replaceChildren(closeIcon.cloneNode(true));
    close.setAttribute('aria-label', '关闭版本详情'); heading.append(close); return heading;
  }
  function renderDetail(detail) {
    const container = $('detail'); container.replaceChildren(detailHeading());
    container.append(el('h2', detail.subject || '无标题版本', 'code-version-title'));
    const metadata = el('details', undefined, 'code-metadata');
    metadata.id = 'code-commit-metadata'; metadata.open = false;
    const summary = el('summary', undefined, 'code-version-meta');
    summary.title = '查看完整版本信息';
    summary.setAttribute('aria-label', '查看完整版本信息');
    const timestamp = el('time', compactTime(detail.committed_at));
    timestamp.dateTime = detail.committed_at || ''; timestamp.title = formatTime(detail.committed_at);
    const byline = el('span', `${detail.author_name || '作者未提供'} · `); byline.append(timestamp);
    summary.append(el('span', detail.oid.slice(0, 7), 'code-hash'), byline);
    metadata.append(summary);
    const author = [detail.author_name, detail.author_email ? `<${detail.author_email}>` : ''].filter(Boolean).join(' ');
    const dl = el('dl');
    for (const [label, value, id] of [['本地仓库', detail.repository_path || '未提供', 'repository'], ['完整哈希', detail.oid, 'oid'], ['作者', author || '作者未提供', 'author'],
      ['提交时间', formatTime(detail.committed_at), 'time'], ['父版本', list(detail.parents).join('\n') || '首个版本（无父版本）', 'parents'],
      ['说明', detail.message, 'message'], ['比较基准', detail.parents?.length > 1
        ? '合并版本 · 差异默认相对第一父版本' : detail.parents?.length ? '差异相对父版本' : '首个版本 · 差异相对空树', 'base']]) {
      const valueNode = el('dd', value); valueNode.id = `code-commit-${id}`;
      dl.append(el('dt', label), valueNode);
    }
    metadata.append(dl); container.append(metadata);
    const diff = diffBox(); diff.classList.add('code-commit-diff');
    const label = el('div', undefined, 'code-files-label');
    label.append(el('span', '改动文件'), el('span', `${detail.files.length} 个`));
    container.append(label, fileList(detail.files,
      f => loadDiff(diff, {kind: 'commit', oid: detail.oid, file_id: f.file_id}, f), {compact: true}), diff);
    const actions = el('div', undefined, 'code-detail-actions');
    actions.append(button('从此创建分支', () => openAction('create_branch', {target_oid: detail.oid}), 'create-from'),
      button('恢复到此版本', () => openAction('restore', {target_oid: detail.oid}), 'restore', 'code-outline'));
    actions.querySelector('#code-create-from').prepend(detailActionIcon('branch'));
    actions.querySelector('#code-restore').prepend(detailActionIcon('restore'));
    container.append(actions); renderStatus();
    // The approved detail view opens the first real file, not an empty prompt.
    container.querySelector('.code-file')?.click();
  }
  function detailActionIcon(name) {
    const svg = svgElement('svg', {class: 'ui-icon', width: 20, height: 20, viewBox: '0 0 24 24',
      fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round',
      'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false'});
    if (name === 'branch') {
      svg.append(svgElement('path', {d: 'M6 7v10M9 20a9 9 0 0 0 9-9M18 16v6M15 19h6'}));
      for (const [cx, cy] of [[6, 4], [6, 20], [18, 8]]) svg.append(svgElement('circle', {cx, cy, r: 3}));
    } else if (name === 'file') {
      svg.append(svgElement('path', {d: 'M14 2H6a2 2 0 0 0-2 2v6m16 0V8l-6-6v6h6M20 14v6a2 2 0 0 1-2 2h-6M5 14l-3 3 3 3M9 14l3 3-3 3'}));
    } else svg.append(svgElement('path', {d: 'M3 10a9 9 0 1 1 2.8 8.5M3 4v6h6'}));
    return svg;
  }
  function fileStatus(file) {
    const raw = text(file.status);
    const labels = {M: '修改', A: '新增', D: '删除', R: '重命名', C: '复制', T: '类型变化', '??': '未跟踪', U: '冲突'};
    const kind = raw === '??' ? raw : raw.trim().charAt(0);
    let label = labels[kind] || raw || '变化';
    if (file.old_path && file.old_path !== file.path) label = '重命名';
    const index = file.index_status || (raw.length === 2 ? raw[0] : '');
    const working = file.worktree_status || (raw.length === 2 ? raw[1] : '');
    if (index && ![' ', '?', '.'].includes(index)) label += working && ![' ', '.', '?'].includes(working) ? ' · 部分暂存' : ' · 已暂存';
    return label;
  }
  function fileList(items, select, {selection, onChange, compact = false} = {}) {
    const wrap = el('div', undefined, 'code-preview-files');
    let shown = 0;
    const rows = el('div', undefined, 'code-file-list');
    const count = el('span', undefined, 'code-muted');
    let all;
    const sync = () => {
      count.textContent = selection ? `已选 ${selection.size} / ${items.length} 个文件` : `显示 ${shown} / ${items.length}`;
      if (all) { all.checked = items.length > 0 && selection.size === items.length; all.indeterminate = selection.size > 0 && selection.size < items.length; }
      for (const checkbox of rows.querySelectorAll('input[type=checkbox]')) checkbox.checked = selection.has(checkbox.value);
    };
    if (selection) {
      const controls = el('label', undefined, 'code-file-controls');
      all = input('select-all', '', 'checkbox');
      all.addEventListener('change', () => {
        selection.clear(); if (all.checked) items.forEach(f => selection.add(f.file_id)); sync(); onChange?.();
      });
      controls.append(all, el('span', '全选全部文件'), count); wrap.append(controls);
    }
    const more = button('显示更多文件', append, null, 'code-file-more');
    function append() {
      const batch = document.createDocumentFragment();
      for (const file of items.slice(shown, shown + PAGE_SIZE)) {
        const row = el('div', undefined, 'code-file-row');
        if (selection) {
          const box = el('input'); box.type = 'checkbox'; box.value = file.file_id;
          box.checked = selection.has(file.file_id); box.setAttribute('aria-label', `保存 ${file.path}`);
          box.addEventListener('change', () => { box.checked ? selection.add(file.file_id) : selection.delete(file.file_id); sync(); onChange?.(); });
          row.append(box);
        }
        const control = button(null, () => {
          for (const b of rows.querySelectorAll('.code-file')) b.setAttribute('aria-pressed', String(b === control));
          select?.(file);
        }, null, 'code-file');
        control.dataset.fileId = file.file_id;
        control.setAttribute('aria-pressed', 'false');
        const path = file.old_path && file.old_path !== file.path ? `${file.old_path} → ${file.path}` : file.path;
        if (compact) {
          control.classList.add('code-commit-file');
          control.title = `${path} · ${fileStatus(file)}`;
          control.append(detailActionIcon('file'), el('span', path, 'code-file-path'));
          if (file.binary) control.append(el('span', '二进制', 'code-file-status'));
          else if (Number.isInteger(file.additions) && Number.isInteger(file.deletions)) {
            control.append(el('b', `+${file.additions}`, 'code-added'), el('b', `−${file.deletions}`, 'code-removed'));
          }
        } else control.append(el('span', path), el('span', file.resolved === undefined ? fileStatus(file) : file.resolved ? '已解决' : fileStatus(file), 'code-file-status'));
        row.append(control); batch.append(row);
      }
      shown = Math.min(items.length, shown + PAGE_SIZE); rows.append(batch); more.hidden = shown >= items.length;
      more.textContent = `显示更多文件（${shown} / ${items.length}）`;
      if (selection) sync(); else count.textContent = `显示 ${shown} / ${items.length}`;
    }
    wrap.append(rows, more); if (!selection && !compact) wrap.append(count);
    if (!items.length) rows.append(el('p', '没有文件变化。', 'code-muted'));
    append(); return wrap;
  }
  function diffBox() {
    const box = el('div', undefined, 'code-diff-box'); box.append(el('p', '选择文件查看真实差异。', 'code-muted')); return box;
  }
  function readCommitDiff(params) {
    const key = JSON.stringify([params.oid, params.file_id]);
    if (diffCache.has(key)) {
      const data = diffCache.get(key); diffCache.delete(key); diffCache.set(key, data);
      return Promise.resolve(data);
    }
    if (diffReads.has(key)) return diffReads.get(key);
    const request = (async () => {
      const data = await api('/diff' + query(params), {key: `diff:${key}`});
      diffCache.set(key, data);
      while (diffCache.size > DETAIL_CACHE_SIZE) diffCache.delete(diffCache.keys().next().value);
      return data;
    })();
    diffReads.set(key, request);
    const forget = () => { if (diffReads.get(key) === request) diffReads.delete(key); };
    request.then(forget, forget);
    return request;
  }
  function renderDiff(box, data, file) {
    box.setAttribute('aria-busy', 'false');
    const compact = box.classList.contains('code-commit-diff');
    box.replaceChildren();
    if (!compact) box.append(el('p', file.path));
    if (compact && typeof data.text === 'string' && data.text && !data.binary && data.supported !== false) {
      const tools = el('div', undefined, 'code-diff-tools');
      const expand = button('放大', () => {
        const m = openModal(file.path + ' · 代码差异', 'diff');
        const large = diffBox(); large.classList.add('code-expanded-diff');
        renderDiff(large, data, file); m.body.append(large);
        m.cancel.textContent = '关闭';
      }, 'expand-diff');
      expand.title = '放大查看代码差异'; expand.setAttribute('aria-label', '放大查看代码差异');
      tools.append(expand); box.append(tools);
    }
    if (data.binary) box.append(el('p', '二进制文件，无法展示文本差异。', 'code-muted'));
    else if (data.supported === false) box.append(el('p', '暂不支持此编码或文件类型，无法展示文本差异。', 'code-muted'));
    if (data.truncated) box.append(el('p', '差异过大，以下内容已截断，不是完整差异。', 'code-notice'));
    if (typeof data.text === 'string' && !data.binary) {
      const pre = el('pre', undefined, 'code-diff'); pre.tabIndex = 0; pre.setAttribute('aria-label', `${file.path} 的差异`);
      const fragment = document.createDocumentFragment();
      let lines = data.text.split('\n');
      const patch = compact && lines.some(line => /^@@ /.test(line));
      if (patch) {
        // Inline replacements pair removed/added lines; keep every real line and hunk boundary.
        const paired = []; let inside = false;
        for (let i = 0; i < lines.length;) {
          if (/^@@ /.test(lines[i])) inside = true;
          if (!inside || !lines[i].startsWith('-')) { paired.push(lines[i++]); continue; }
          const removed = [], added = [];
          while (i < lines.length && lines[i].startsWith('-')) removed.push(lines[i++]);
          while (i < lines.length && lines[i].startsWith('+')) added.push(lines[i++]);
          for (let n = 0; n < Math.max(removed.length, added.length); n++) {
            if (n < removed.length) paired.push(removed[n]);
            if (n < added.length) paired.push(added[n]);
          }
        }
        lines = paired;
      }
      let hunk = false, hunkCount = 0;
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (i === lines.length - 1 && !line) continue;
        if (patch && /^@@ /.test(line)) {
          hunk = true;
          // Keep later range headers so separate hunks never appear contiguous.
          if (++hunkCount > 1) fragment.append(el('span', line, 'code-diff-line code-diff-range'));
          continue;
        }
        if (patch && !hunk) continue;
        const added = line.startsWith('+') && (patch || !line.startsWith('+++'));
        const removed = line.startsWith('-') && (patch || !line.startsWith('---'));
        fragment.append(el('span', line, 'code-diff-line' + (added ? ' code-diff-add' : removed ? ' code-diff-del' : '')));
      }
      pre.append(fragment); box.append(pre);
      if (!data.text && data.supported !== false) box.append(el('p', '没有文本差异。', 'code-muted'));
    }
  }
  async function loadDiff(box, params, file, modal = null) {
    box.pendingDiff = {params, file};
    const request = (box.request || 0) + 1; box.request = request;
    const immutable = params.kind === 'commit';
    const cached = immutable && diffCache.get(JSON.stringify([params.oid, params.file_id]));
    if (cached) { renderDiff(box, cached, file); return; }
    box.setAttribute('aria-busy', 'true');
    box.replaceChildren(el('p', `${file.path} · 读取差异…`, 'code-muted'));
    try {
      // Preview/worktree diffs remain fresh; only fixed commit-file pairs cache.
      const data = await (immutable ? readCommitDiff(params) : api('/diff' + query(params)));
      if (!box.isConnected || box.request !== request || (modal && state.modal !== modal)) return;
      renderDiff(box, data, file);
    } catch (error) {
      if (!ignored(error) && box.isConnected && box.request === request) {
        box.setAttribute('aria-busy', 'false');
        box.replaceChildren(el('p', errorText(error), 'code-notice'));
        if (modal && ['state_changed', 'preview_expired'].includes(error.code)) invalidatePreview(modal);
      }
    } finally { schedulePrefetch(); }
  }

  function openModal(title, kind) {
    if (state.modal?.pending) return state.modal;
    const dialog = $('dialog');
    dialog.classList.toggle('code-dialog-diff', kind === 'diff');
    const trigger = dialog.open ? state.modal?.trigger : document.activeElement;
    if (state.modal?.timer) clearTimeout(state.modal.timer);
    const m = {kind, trigger, body: $('dialog-body'), actions: $('dialog-actions'), pending: false,
      preview: null, timer: null, selection: new Set(), previousFiles: null, fetched: null};
    state.modal = m;
    $('dialog-title').textContent = title; $('dialog-close').disabled = false;
    dialog.setAttribute('aria-busy', 'false'); m.body.replaceChildren(); m.actions.replaceChildren();
    feedback('', false, $('dialog-feedback'));
    m.cancel = button('取消', closeModal, 'dialog-cancel'); m.actions.append(m.cancel);
    if (!dialog.open) dialog.showModal();
    queueMicrotask(() => { if (state.modal === m) $('dialog-close').focus(); });
    return m;
  }
  function closeModal() {
    const m = state.modal;
    if (!m) return;
    if (m.pending) { feedback('操作进行中，请等待实际结果后关闭。', false, $('dialog-feedback')); return; }
    if (m.timer) clearTimeout(m.timer);
    state.modal = null; $('dialog').close();
    if (m.trigger?.isConnected && !m.trigger.disabled) m.trigger.focus();
    else $('refresh').focus();
  }
  function modalFeedback(m, message, error = false) {
    if (state.modal === m) feedback(message, error, $('dialog-feedback'));
  }
  function invalidatePreview(m) {
    m.preview = null;
    if (m.timer) clearTimeout(m.timer);
    if (m.confirm) m.confirm.disabled = true;
    if (m.review) m.review.hidden = false;
  }
  function pending(m, active, label = '处理中…') {
    m.pending = active;
    $('dialog').setAttribute('aria-busy', String(active));
    m.cancel.disabled = active; $('dialog-close').disabled = active;
    if (m.confirm) { m.confirm.disabled = active; m.confirm.textContent = active ? label : m.confirmLabel; }
    if (m.review) m.review.disabled = active;
    for (const node of m.body.querySelectorAll('input,textarea,select')) {
      if (active) { node.dataset.wasDisabled = String(node.disabled); node.disabled = true; }
      else if ('wasDisabled' in node.dataset) { node.disabled = node.dataset.wasDisabled === 'true'; delete node.dataset.wasDisabled; }
    }
    updateConfirm(m);
  }
  function updateConfirm(m) {
    if (!m.confirm || state.modal !== m || m.kind === 'abort') return;
    let valid = Boolean(m.preview?.can_execute && m.preview?.preview_id) && !m.pending && !writeReason(m.action);
    if (['switch_branch', 'merge', 'restore', 'pull_apply'].includes(m.action) && files().length) valid = false;
    if (m.action === 'save') valid = valid && m.selection.size > 0 && m.message.value.trim().length > 0 && m.message.value.trim().length <= 4000;
    if (m.action === 'create_branch') valid = valid && Boolean(m.name.value.trim());
    if (m.action === 'push' && m.pushDetailsMissing) valid = false;
    if (m.action === 'pull_apply' && (files().length || !hasHead() || state.status?.head?.detached)) valid = false;
    m.confirm.disabled = !valid;
  }
  function addIdentity(m) {
    const section = el('details', undefined, 'code-identity'); section.hidden = true;
    section.append(el('summary', '补填作者身份（仅当前仓库）'));
    const body = el('div');
    const name = input('identity-name'); const email = input('identity-email', '', 'email');
    name.autocomplete = 'name'; email.autocomplete = 'email';
    body.append(el('p', '仅用户确认后写入当前仓库本地配置，不改全局身份；不会自动重放保存操作。'),
      field('作者名称', name), field('作者邮箱', email));
    const save = button('确认仅用于当前仓库', async () => {
      if (!name.value.trim() || !email.value.trim() || !email.checkValidity()) {
        modalFeedback(m, '请填写名称和有效邮箱。', true); return;
      }
      if (m.pending || state.writing) return;
      pending(m, true); state.writing = true; renderStatus(); save.disabled = true;
      modalFeedback(m, '正在设置当前仓库身份…');
      try {
        await post('/identity', {name: name.value.trim(), email: email.value.trim(), scope: 'local'});
        invalidatePreview(m);
        modalFeedback(m, '当前仓库身份已设置。原说明与文件选择已保留，请重新查看后再次确认操作。');
        section.open = false;
      } catch (error) { if (!ignored(error)) modalFeedback(m, errorText(error), true); }
      finally {
        state.writing = false; save.disabled = false;
        if (state.modal === m) pending(m, false);
        renderStatus(); await refresh();
      }
    }, 'identity-save', 'code-outline');
    body.append(save); section.append(body); m.body.append(section); m.identity = section;
  }
  function showIdentity(m) {
    if (!m.identity) addIdentity(m);
    m.identity.hidden = false; m.identity.open = true;
  }
  const actionTitles = {
    save: '保存到本地', create_branch: '从此创建分支', switch_branch: '切换分支',
    merge: '合并分支', restore: '恢复到此版本', push: '确认上传', pull_apply: '更新当前分支',
  };
  function actionNote(action, params, targetBranch) {
    switch (action) {
      case 'save': return '本次仅保存在本地。将保存选中文件当前全部修改，包括部分暂存文件的未暂存内容；未选文件不夹带。';
      case 'create_branch': return `从 ${params.target_oid} 创建分支。只新增分支，不切换文件，不自动上传。`;
      case 'switch_branch': return `从 ${targetBranch || '游离 HEAD'} 切换到 ${params.branch}。不会丢弃修改或自动备份。`;
      case 'merge': return `将 ${params.source_branch || '所选分支'} 合并到 ${targetBranch}。源分支保留，不自动上传。`;
      case 'restore': return `当前分支 ${targetBranch} 的全部受跟踪文件将恢复为 ${params.target_oid} 的树，并新增一个版本。历史保留，不自动上传；不是撤销单个提交。`;
      case 'push': return `仅普通上传当前分支 ${targetBranch}，不上传其他分支、标签或未提交文件。基于本地远端缓存，最终以远端接收结果为准。`;
      case 'pull_apply': return `把刚检查的远端版本合入当前分支 ${targetBranch}。仅更新当前分支，分叉时普通合并，不执行 rebase。`;
      default: return '';
    }
  }
  function openAction(action, params) {
    if (!guard(action)) return;
    const m = openModal(actionTitles[action], action);
    m.action = action; m.params = {...params}; m.branch = state.status.head.branch;
    m.note = el('p', actionNote(action, params, m.branch), 'code-summary'); m.body.append(m.note);
    if (action === 'save') {
      m.message = el('textarea'); m.message.id = 'code-save-message'; m.message.maxLength = 4000;
      m.message.rows = 3; m.message.addEventListener('input', () => updateConfirm(m));
      m.body.append(field('版本说明（1–4000 字符）', m.message));
    }
    if (action === 'create_branch') {
      m.name = input('branch-name'); m.name.addEventListener('input', () => updateConfirm(m));
      m.body.append(field('新分支名称', m.name));
    }
    if (action === 'merge') {
      const select = el('select'); select.id = 'code-source-branch'; select.append(new Option('选择要合入的本地分支', ''));
      for (const branch of list(state.status.branches).filter(b => !b.current)) select.append(new Option(branch.name, branch.name));
      select.addEventListener('change', () => {
        m.params.source_branch = select.value; invalidatePreview(m);
        m.note.textContent = actionNote(action, m.params, m.branch);
        m.previewArea.replaceChildren(); modalFeedback(m, '目标已更改，请查看合并影响后再确认。');
      }); m.body.append(field(`合入当前分支 ${m.branch}`, select));
    }
    m.previewArea = el('div', undefined, 'code-preview'); m.body.append(m.previewArea);
    addIdentity(m);
    m.review = button('重新查看影响', () => preparePreview(m), 'preview-refresh', 'code-outline');
    m.confirmLabel = action === 'create_branch' ? '创建分支（不切换）' : actionTitles[action];
    m.confirm = button(m.confirmLabel, () => executePreview(m), 'confirm', 'code-primary'); m.confirm.disabled = true;
    m.actions.append(m.review, m.confirm);
    if (action !== 'merge') preparePreview(m);
    else { m.review.textContent = '查看合并影响'; modalFeedback(m, '先选择源分支，再查看合并方向和影响。'); }
  }
  const summaryKeys = {
    action: '操作', branch: '分支', source_branch: '源分支', target_branch: '目标分支', remote: '远端',
    remote_name: '远端', remote_id: '远端标识', remote_url: '远端地址', target_oid: '目标版本', source_oid: '源版本',
    head_before: '操作前 HEAD', head_after: '操作后 HEAD', head: '当前 HEAD', message: '说明',
    file_count: '影响文件数', files_count: '影响文件数', ahead: '待上传版本', behind: '落后版本',
    commits: '相关版本', outgoing_commits: '待上传版本', incoming_commits: '远端版本',
    new_branch: '将新建远端分支', creates_remote_branch: '将新建远端分支', last_fetch_at: '上次检查远端',
    cached: '基于缓存', cache_current: '缓存是否最新', target_exists: '远端目标是否已存在',
    warning: '提示', warnings: '提示', relation: '版本关系', subject: '版本标题', oid: '版本哈希',
  };
  function readableSummary(value, depth = 0) {
    if (value === null || value === undefined) return '';
    if (typeof value !== 'object') return typeof value === 'boolean' ? (value ? '是' : '否') : String(value);
    if (Array.isArray(value)) return value.map(item => readableSummary(item, depth + 1)).join('\n');
    return Object.entries(value).map(([key, item]) => `${'  '.repeat(Math.min(depth, 3))}${summaryKeys[key] || key}：${/url/i.test(key) ? safeRemoteURL(text(item)) : readableSummary(item, depth + 1)}`).join('\n');
  }
  function renderSummary(host, summary, modal) {
    if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
      host.append(el('div', readableSummary(summary), 'code-summary')); return;
    }
    const commitKeys = ['outgoing_commits', 'incoming_commits', 'commits'];
    const metadata = Object.fromEntries(Object.entries(summary).filter(([key]) => !commitKeys.includes(key)));
    host.append(el('div', readableSummary(metadata), 'code-summary'));
    for (const key of commitKeys) {
      if (!Array.isArray(summary[key])) continue;
      const commits = summary[key];
      const section = el('div', undefined, 'code-preview-files');
      section.append(el('h3', `${summaryKeys[key]} · ${commits.length}`));
      const rows = el('div', undefined, 'code-file-list');
      const detail = el('div', undefined, 'code-preview');
      let shown = 0;
      const more = button('显示更多版本', append, null, 'code-summary-more');
      function append() {
        for (const commit of commits.slice(shown, shown + PAGE_SIZE)) {
          const row = button(`${short(commit.oid)} · ${commit.subject || '无标题版本'}`,
            () => showRemoteCommit(detail, commit.oid, modal), null, 'code-file');
          row.dataset.previewOid = commit.oid; rows.append(row);
        }
        shown = Math.min(shown + PAGE_SIZE, commits.length);
        more.hidden = shown >= commits.length; more.textContent = `显示更多版本（${shown} / ${commits.length}）`;
      }
      if (!commits.length) rows.append(el('p', '没有待处理版本。', 'code-muted'));
      append(); section.append(rows, more, detail); host.append(section);
    }
  }
  async function showRemoteCommit(host, oid, modal) {
    const request = (host.request || 0) + 1; host.request = request;
    host.replaceChildren(el('p', '读取相关版本…', 'code-muted'));
    try {
      const commit = await api('/commit/' + encodeURIComponent(oid));
      if (!host.isConnected || host.request !== request || state.modal !== modal) return;
      if (commit?.oid !== oid || !Array.isArray(commit.files)) throw fail('相关版本详情与目标不一致。');
      const diff = diffBox();
      host.replaceChildren(el('h3', commit.subject || '无标题版本'),
        el('p', `${commit.oid} · ${commit.author_name || ''} · ${formatTime(commit.committed_at)}`, 'code-metadata'),
        el('p', commit.message, 'code-summary'),
        el('p', commit.parents?.length > 1 ? '差异相对第一父版本' : commit.parents?.length ? '差异相对父版本' : '差异相对空树', 'code-muted'),
        fileList(commit.files, f => loadDiff(diff, {kind: 'commit', oid, file_id: f.file_id}, f, modal)), diff);
    } catch (error) {
      if (!ignored(error) && host.isConnected && host.request === request) host.replaceChildren(el('p', errorText(error), 'code-notice'));
    }
  }
  async function preparePreview(m) {
    if (state.modal !== m || m.pending || state.writing) return;
    if (m.action === 'merge' && !m.params.source_branch) { modalFeedback(m, '请选择源分支。', true); return; }
    if (m.action === 'pull_apply' && !m.fetched) { modalFeedback(m, '请先明确检查远端更新。', true); return; }
    invalidatePreview(m); pending(m, true, '读取影响…'); modalFeedback(m, '正在读取并固定本次影响…');
    m.review.textContent = '重新查看影响';
    try {
      const preview = await post('/preview', {action: m.action, params: m.params});
      if (state.modal !== m) return;
      if (!preview?.preview_id || !Array.isArray(preview.files) || typeof preview.can_execute !== 'boolean') {
        throw fail('预览接口字段不完整，不允许执行。');
      }
      if (m.action === 'push') {
        const summary = preview.summary;
        const complete = summary && typeof summary === 'object' && Array.isArray(summary.outgoing_commits)
          && Object.prototype.hasOwnProperty.call(summary, 'creates_remote_branch');
        m.pushDetailsMissing = !complete;
      }
      if (m.action === 'save') {
        const selectedKeys = new Set(list(m.previousFiles).filter(f => m.selection.has(f.file_id)).map(fileKey));
        const first = m.previousFiles === null;
        m.selection = new Set(preview.files.filter(f => first || selectedKeys.has(fileKey(f))).map(f => f.file_id));
        m.previousFiles = preview.files;
      }
      m.preview = preview;
      m.previewArea.replaceChildren();
      if (preview.summary !== undefined) renderSummary(m.previewArea, preview.summary, m);
      m.previewArea.append(el('h3', `影响文件 · ${preview.files.length}`));
      const diff = diffBox();
      const canDiff = ['save', 'restore'].includes(m.action);
      const grid = el('div', undefined, canDiff ? 'code-save-grid' : 'code-preview-files');
      grid.append(fileList(preview.files, f => {
        if (canDiff && m.preview?.preview_id === preview.preview_id) {
          loadDiff(diff, {kind: m.action === 'save' ? 'worktree' : 'restore', preview_id: preview.preview_id, file_id: f.file_id}, f, m);
        }
      }, m.action === 'save' ? {selection: m.selection, onChange: () => updateConfirm(m)} : {}));
      if (canDiff) grid.append(diff); m.previewArea.append(grid);
      if (!preview.can_execute) {
        const reason = text(preview.reason) || '此预览不允许执行。'; modalFeedback(m, reason, true);
        if (/identity|身份|邮箱|作者/.test(reason)) showIdentity(m);
      } else if (m.pushDetailsMissing) {
        modalFeedback(m, '上传预览缺少待上传提交列表或目标新建状态，暂不能安全确认；请补齐接口后重新查看。', true);
      } else modalFeedback(m, '请核对以上影响，再明确确认。');
      if (preview.expires_at) {
        const raw = preview.expires_at;
        const expiry = typeof raw === 'number' ? raw * (raw < 1e12 ? 1000 : 1) : Date.parse(raw);
        if (Number.isFinite(expiry)) m.timer = setTimeout(() => {
          if (state.modal === m) { invalidatePreview(m); modalFeedback(m, '预览已过期，请重新查看。输入仍保留。', true); }
        }, Math.min(Math.max(expiry - Date.now(), 0), 2147483647));
      }
    } catch (error) {
      if (!ignored(error)) {
        modalFeedback(m, errorText(error), true);
        if (error.code === 'identity_required') showIdentity(m);
        invalidatePreview(m);
      }
    } finally {
      if (state.modal === m) { pending(m, false); m.review.hidden = false; updateConfirm(m); }
    }
  }
  async function executePreview(m) {
    if (state.modal !== m || m.pending || state.writing || !m.preview?.can_execute || m.confirm.disabled) return;
    const body = {preview_id: m.preview.preview_id};
    if (m.action === 'save') { body.file_ids = [...m.selection]; body.message = m.message.value.trim(); }
    if (m.action === 'create_branch') body.name = m.name.value.trim();
    invalidatePreview(m); pending(m, true, '执行中…');
    state.writing = true; renderStatus(); modalFeedback(m, '执行中，正在等待仓库真实结果…');
    let completed = false;
    try {
      const result = await post('/execute', body);
      if (!['done', 'no_change', 'conflicts', 'partial'].includes(result?.outcome)) throw fail('执行接口未返回明确结果，请刷新仓库核对。', 'result_uncertain');
      const message = resultMessage(result);
      if (result.outcome === 'partial') {
        modalFeedback(m, message, true); feedback(message, true);
        if (/identity|身份|邮箱|作者/.test(text(result.message))) showIdentity(m);
      } else {
        completed = true; feedback(message);
        if (result.outcome === 'conflicts') await refreshMerge();
      }
    } catch (error) {
      if (!ignored(error)) {
        modalFeedback(m, errorText(error), true); feedback(errorText(error), true);
        if (error.code === 'identity_required') showIdentity(m);
        if (m.action === 'save') m.previewArea.append(el('p', '保存未确认成功；部分选中文件可能已进入暂存区，请查看刷新后的实际状态。未自动回退任何修改。', 'code-notice'));
      }
    } finally {
      await refreshAfterWrite(m, completed);
    }
  }
  async function refreshAfterWrite(modal, completed) {
    // The receipt alone is not a UI-ready state: keep the write lock and modal
    // until both status and graph have settled, so the next action sees fresh files/HEAD.
    let refreshed = false;
    if (completed && modal && state.modal === modal) {
      modalFeedback(modal, '操作已返回完成，正在刷新仓库状态与版本记录…');
      if (modal.confirm) modal.confirm.textContent = '刷新状态中…';
    }
    try {
      refreshed = await refresh();
    } finally {
      state.writing = false;
      if (modal && state.modal === modal) {
        pending(modal, false);
        if (completed && !refreshed) modalFeedback(modal, '操作已返回完成，但状态或版本记录未能完整刷新。请刷新核对；不会重复执行，原输入已保留。', true);
      }
      renderStatus(); updateMergeButtons();
      if (completed && refreshed && modal && state.modal === modal) closeModal();
      schedulePrefetch();
    }
    return refreshed;
  }
  function resultMessage(result) {
    if (result.outcome === 'partial') return (result.action === 'restore'
      ? '文件已恢复，但版本尚未保存。请查看当前修改，修复原因后保存。'
      : result.action === 'push' ? '上传结果待核对，不会自动重试。' : '操作仅部分完成，请核对实际状态。')
      + (result.message ? ` ${result.message}` : '');
    if (result.outcome === 'conflicts') return '出现合并冲突，请逐文件确认最终内容，或取消本次合并。';
    if (result.message) return result.action === 'create_branch' ? `${result.message} 已创建，仍在当前分支。` : result.message;
    if (result.outcome === 'no_change') return '内容已包含或相同，无需变更，未创建额外版本。';
    if (result.action === 'create_branch') return '分支已创建，仍在当前分支；没有自动切换或上传。';
    if (result.action === 'save') return `已保存为本地版本 ${short(result.commit_oid || result.head_after)}，未自动上传。`;
    if (result.action === 'restore') return '已恢复并新增本地版本，原有历史保留，未自动上传。';
    if (result.action === 'push') return '指定分支已上传，未上传其他分支、标签或未提交文件。';
    return '操作已完成，正在读取实际仓库状态。';
  }
  function safeRemoteURL(raw) {
    if (!raw) return '地址未提供';
    try {
      const url = new URL(raw);
      return `${url.protocol}//${url.host}${url.pathname}${url.search || url.hash ? '（查询参数已隐藏）' : ''}`;
    } catch (_) {
      return raw.replace(/^[^/@\s]+@(?=[^/]+:)/, '').replace(/\/\/[^/]*@/, '//').split(/[?#]/)[0];
    }
  }
  function openRemote(kind) {
    if (!guard(kind === 'pull' ? 'fetch' : 'push')) return;
    const remotes = list(state.status.remotes);
    const m = openModal(kind === 'pull' ? '拉取：先检查，再更新代码' : '上传当前分支', kind);
    m.branch = state.status.head.branch; m.action = kind === 'push' ? 'push' : 'pull_apply';
    if (!remotes.length) {
      m.body.append(el('p', '尚未配置远端，请先在现有 Git 工具中配置。本地保存、分支、合并和恢复仍可使用。', 'code-summary')); return;
    }
    const remote = el('select'); remote.id = 'code-remote'; remote.append(new Option('请选择已有远端', ''));
    remotes.forEach(r => remote.append(new Option(r.name, r.remote_id)));
    const branch = input('remote-branch'); branch.setAttribute('list', 'code-remote-branches');
    const branchOptions = el('datalist'); branchOptions.id = 'code-remote-branches';
    const address = el('p', '', 'code-muted');
    const upstream = state.status.upstream;
    if (upstream && remotes.some(r => r.remote_id === upstream.remote_id)) { remote.value = upstream.remote_id; branch.value = upstream.branch; }
    const target = () => ({remote_id: remote.value, branch: branch.value.trim()});
    const describe = () => {
      const chosen = remotes.find(r => r.remote_id === remote.value);
      address.textContent = chosen ? `${chosen.name} · ${safeRemoteURL(chosen.url)} · 分支列表仅来自本地缓存` : '明确选择远端和分支前不会访问网络。';
      branchOptions.replaceChildren(...list(chosen?.branches).map(name => new Option(name, name)));
    };
    const resetTarget = () => {
      invalidatePreview(m); m.fetched = null; m.previewArea.replaceChildren(); fetchedInfo.replaceChildren();
      m.params = {}; check.disabled = false; m.review.hidden = true;
      modalFeedback(m, '目标已更改，请重新检查或查看确认，不会自动联网。'); describe();
    };
    remote.addEventListener('change', () => { branch.value = ''; resetTarget(); }); branch.addEventListener('input', resetTarget);
    m.body.append(field('已有远端', remote), address, field(kind === 'pull' ? '远端分支' : '目标远端分支（可新建）', branch), branchOptions);
    m.note = el('p', kind === 'pull' ? '检查远端仅更新缓存，不改 HEAD、索引或工作区。更新代码需要再次确认。'
      : actionNote('push', {}, m.branch), 'code-summary'); m.body.append(m.note);
    const fetchedInfo = el('div', undefined, 'code-preview'); m.body.append(fetchedInfo);
    m.previewArea = el('div', undefined, 'code-preview'); m.body.append(m.previewArea); addIdentity(m);
    const check = button(kind === 'pull' ? '检查远端更新' : '查看上传确认', async () => {
      if (m.pending || state.writing) return;
      const params = target();
      if (!params.remote_id || !params.branch) { modalFeedback(m, '请明确选择已有远端并填写目标分支。', true); return; }
      if (kind === 'push') {
        m.params = {remote_id: params.remote_id, target_branch: params.branch};
        m.note.textContent = `${actionNote('push', {}, m.branch)}\n目标：${remotes.find(r => r.remote_id === params.remote_id).name}/${params.branch}`;
        await preparePreview(m); return;
      }
      invalidatePreview(m); m.fetched = null; pending(m, true, '检查中…');
      state.writing = true; renderStatus(); check.disabled = true;
      modalFeedback(m, '正在检查所选远端，本地代码保持不变…');
      let checked = false;
      try {
        const data = await post('/fetch', params);
        if (data?.remote_id !== params.remote_id || data.branch !== params.branch || !data.fetched_oid) throw fail('远端检查结果与目标不一致，不能更新代码。');
        m.fetched = data;
        m.params = {...params, fetched_oid: data.fetched_oid};
        const relations = {equal: '已最新', ahead: '本地领先，远端没有待合入版本', behind: '可快进', diverged: '已分叉，将进行普通合并'};
        fetchedInfo.replaceChildren(el('p', `${relations[data.relation] || '检查完成'}\n远端版本：${data.fetched_oid}\n本地领先 ${data.ahead}，落后 ${data.behind}\n检查时间：${formatTime(data.last_fetch_at)}`, 'code-summary'));
        const remoteDetail = el('div', undefined, 'code-preview');
        fetchedInfo.append(button('查看检查到的远端版本', () => showRemoteCommit(remoteDetail, data.fetched_oid, m), 'fetched-commit'), remoteDetail);
        m.confirmLabel = data.relation === 'diverged' ? '合并远端更新' : '更新当前分支';
        m.confirm.textContent = m.confirmLabel;
        modalFeedback(m, '远端缓存已检查，本地代码尚未更新。'); checked = true;
      } catch (error) {
        if (!ignored(error)) modalFeedback(m, `${errorText(error)} 远端信息仍按旧缓存显示，不能视为已同步。`, true);
      } finally {
        state.writing = false; if (state.modal === m) pending(m, false); check.disabled = false;
        renderStatus(); await refresh();
      }
      if (checked && state.modal === m) {
        if (files().length) {
          fetchedInfo.append(el('p', '先保存当前修改，再重新发起更新；检查远端没有改变本地代码。', 'code-notice'),
            button('查看并保存', () => { closeModal(); openAction('save', {}); }, 'pull-save', 'code-outline'));
        }
        if (!hasHead() || state.status?.head?.detached) {
          modalFeedback(m, '当前没有可更新的本地分支；仅完成远端检查。', true);
        } else await preparePreview(m);
      }
    }, kind === 'pull' ? 'fetch' : 'push-preview', 'code-outline');
    m.review = button('重新查看影响', async () => {
      if (kind === 'push') {
        const params = target();
        if (!params.remote_id || !params.branch) { modalFeedback(m, '请先填写完整目标。', true); return; }
        m.params = {remote_id: params.remote_id, target_branch: params.branch};
      }
      await preparePreview(m);
    }, 'preview-refresh', 'code-outline'); m.review.hidden = true;
    m.confirmLabel = kind === 'pull' ? '更新当前分支' : '确认上传';
    m.confirm = button(m.confirmLabel, () => executePreview(m), 'confirm', 'code-primary'); m.confirm.disabled = true;
    m.actions.append(check, m.review, m.confirm); describe();
  }

  function mergeState(data) {
    if (!data || !data.operation_id && !data.external) return null;
    return data;
  }
  async function refreshMerge() {
    if (state.mergePending) return;
    const serial = ++state.mergeRequest;
    try {
      const data = await api('/merge', {key: 'merge'});
      if (serial !== state.mergeRequest || state.mergePending) return;
      state.merge = mergeState(data);
      if (!state.merge && state.status?.ongoing) {
        $('workspace').hidden = true; $('conflicts').hidden = false;
        $('conflicts').replaceChildren(el('p', '存在未完成操作，但无法取得网页持有的合并状态。请在原工具核对后刷新；不会接管或强制取消。', 'code-notice'));
        return;
      }
      renderMerge();
    } catch (error) {
      if (!ignored(error) && serial === state.mergeRequest) {
        feedback(errorText(error), true);
        $('workspace').hidden = true; $('conflicts').hidden = false;
        if (!state.merge) $('conflicts').replaceChildren(el('p', errorText(error), 'code-notice'));
      }
    }
  }
  function draftFor(file) {
    const key = `${state.merge.operation_id}:${file.file_id}`;
    let draft = state.drafts.get(key);
    if (!draft) {
      draft = {content: text(file.content), revision: file.revision, dirty: false, resolution: 'manual', stale: false};
      state.drafts.set(key, draft);
    } else if (draft.revision !== file.revision) {
      if (draft.dirty) draft.stale = true;
      else Object.assign(draft, {content: text(file.content), revision: file.revision, resolution: 'manual', stale: false});
    }
    return draft;
  }
  function mergeHasDrafts() {
    if (!state.merge) return false;
    return list(state.merge.files).some(f => state.drafts.get(`${state.merge.operation_id}:${f.file_id}`)?.dirty);
  }
  function mergeWritable() {
    return Boolean(writable() && !state.statusError && state.merge && !state.merge.external && state.merge.operation_id
      && state.merge.expected_revision !== undefined && !state.writing && !state.mergePending);
  }
  function renderMerge() {
    const merge = state.merge; const host = $('conflicts');
    host.hidden = !merge; $('workspace').hidden = Boolean(merge);
    if (!merge) return;
    if (merge.external || state.status?.ongoing?.external) {
      host.replaceChildren(el('h2', '外部 Git 操作'), el('p', '请在原工具处理后刷新。网页不接管、解决或取消外部合并。', 'code-notice'));
      host.dataset.operation = 'external'; return;
    }
    if (host.dataset.operation !== merge.operation_id || !$('merge-editor')) {
      host.dataset.operation = merge.operation_id;
      const heading = el('div', undefined, 'code-conflict-heading');
      const title = el('h2'); title.id = 'code-merge-title';
      const count = el('span', '', 'code-muted'); count.id = 'code-resolved-count';
      const complete = button('完成合并', completeMerge, 'merge-complete', 'code-primary');
      const abort = button('取消本次合并', confirmAbortMerge, 'merge-abort', 'code-outline code-danger');
      heading.append(title, count, complete, abort);
      const layout = el('div', undefined, 'code-conflict-layout');
      const fileArea = el('div', undefined, 'code-conflict-files'); fileArea.id = 'code-merge-files';
      const editor = el('div', undefined, 'code-conflict-editor'); editor.id = 'code-merge-editor';
      layout.append(fileArea, editor);
      const result = el('p', '', 'code-conflict-result'); result.id = 'code-merge-feedback'; result.setAttribute('role', 'status'); result.setAttribute('aria-live', 'polite');
      host.replaceChildren(heading, layout, result);
      state.conflictId = null;
    }
    $('merge-title').textContent = `合并冲突 · ${merge.source_branch || '源分支'} → ${merge.target_branch || '当前分支'}`;
    const items = list(merge.files);
    items.forEach(draftFor);
    $('resolved-count').textContent = `已解决 ${items.filter(f => f.resolved).length} / ${items.length}`;
    const area = $('merge-files');
    const signature = JSON.stringify(items.map(f => [f.file_id, f.path, f.status, f.resolved]));
    if (area.dataset.signature !== signature) {
      area.dataset.signature = signature;
      area.replaceChildren(fileList(items, file => {
        if (state.mergePending) return;
        state.conflictId = file.file_id; renderConflictFile(true);
      }));
    }
    if (!items.some(f => f.file_id === state.conflictId)) state.conflictId = items[0]?.file_id || null;
    renderConflictFile(false); updateMergeButtons();
  }
  function conflictSelection() {
    return list(state.merge?.files).find(f => f.file_id === state.conflictId);
  }
  function renderConflictFile(force) {
    const file = conflictSelection(); const editor = $('merge-editor');
    if (!file) { editor.replaceChildren(el('p', '没有未解决文件；核对后可完成或取消合并。', 'code-muted')); return; }
    const draft = draftFor(file);
    for (const b of $('merge-files').querySelectorAll('[data-file-id]')) b.setAttribute('aria-pressed', String(b.dataset.fileId === file.file_id));
    if (force || editor.dataset.fileId !== file.file_id || editor.dataset.supported !== String(file.supported)) {
      editor.dataset.fileId = file.file_id; editor.dataset.supported = String(file.supported);
      editor.replaceChildren(el('h3', file.path));
      if (file.supported !== true) {
        editor.append(el('p', `${fileStatus(file)}：此冲突不支持网页文本解决（仅支持双方修改的 UTF-8 普通文件，单文件不超过 1 MiB）。可以取消本次合并，或在原 Git 工具中处理。`, 'code-notice'));
        return;
      }
      const columns = el('div', undefined, 'code-conflict-columns');
      const current = el('pre', '', 'code-diff'); current.id = 'code-conflict-current'; current.tabIndex = 0;
      current.setAttribute('aria-label', '当前分支完整内容');
      const incoming = el('pre', '', 'code-diff'); incoming.id = 'code-conflict-incoming'; incoming.tabIndex = 0;
      incoming.setAttribute('aria-label', '合入分支完整内容');
      const final = el('textarea'); final.id = 'code-conflict-content'; final.spellcheck = false; final.wrap = 'off';
      final.setAttribute('aria-label', '最终内容');
      const choose = (resolution, value) => {
        if (!mergeWritable()) return;
        draft.content = text(value); draft.resolution = resolution; draft.dirty = true;
        final.value = draft.content; updateMergeButtons(); final.focus();
      };
      final.addEventListener('input', () => {
        // Textareas normalize CRLF; retain the original file's newline style for manual results.
        const reference = text(file.content || file.current);
        draft.content = /\r\n/.test(reference) ? final.value.replace(/\r?\n/g, '\r\n') : final.value;
        draft.resolution = 'manual'; draft.dirty = true; updateMergeButtons();
      });
      for (const [label, content, control] of [
        ['当前分支内容', current, button('采用当前整份文件', () => choose('current', conflictSelection()?.current), 'use-current', 'code-outline')],
        ['合入分支内容', incoming, button('采用合入整份文件', () => choose('incoming', conflictSelection()?.incoming), 'use-incoming', 'code-outline')],
        ['可编辑最终内容', final, el('small', '仅填入编辑框，明确标记后才写入文件；不自动保存。')],
      ]) {
        const column = el('div', undefined, 'code-conflict-column'); column.append(el('h3', label), content, control); columns.append(column);
      }
      const warning = el('p', '', 'code-conflict-warning'); warning.id = 'code-conflict-warning';
      const actions = el('div', undefined, 'code-conflict-buttons');
      const review = button('已核对更新，保留草稿重新确认', () => {
        const latest = conflictSelection();
        if (!latest || state.mergePending) return;
        draft.revision = latest.revision; draft.stale = false; draft.dirty = true; updateMergeButtons();
        feedback('已采用当前文件版本进行下一次复核；草稿未写入，请再次标记此文件已解决。', false, $('merge-feedback'));
      }, 'conflict-review', 'code-outline'); review.hidden = true;
      actions.append(review, button('标记此文件已解决', resolveConflict, 'resolve', 'code-primary'));
      editor.append(columns, warning, actions);
    }
    if (file.supported === true) {
      $('conflict-current').textContent = text(file.current); $('conflict-incoming').textContent = text(file.incoming);
      const final = $('conflict-content');
      if (force || final.dataset.fileId !== file.file_id || (!draft.dirty && document.activeElement !== final)) final.value = draft.content;
      final.dataset.fileId = file.file_id;
      $('conflict-warning').textContent = draft.stale ? '文件在外部发生变化。当前/合入内容已刷新，但最终内容草稿保留；请核对后明确采用新的复核版本。' : '';
      $('conflict-review').hidden = !draft.stale;
    }
  }
  function updateMergeButtons() {
    if (!$('merge-complete') || !state.merge) return;
    const can = mergeWritable(); const items = list(state.merge.files);
    $('merge-complete').disabled = !can || items.some(f => !f.resolved) || mergeHasDrafts();
    $('merge-abort').disabled = !can;
    const file = conflictSelection(); const draft = file ? draftFor(file) : null;
    if ($('resolve')) {
      $('resolve').disabled = !can || !file?.supported || draft?.stale || Boolean(file?.resolved && !draft?.dirty);
      $('resolve').textContent = state.mergePending ? '处理中…' : file?.resolved && !draft?.dirty ? '此文件已解决' : '标记此文件已解决';
      for (const id of ['use-current', 'use-incoming', 'conflict-review', 'conflict-content']) if ($(id)) $(id).disabled = !can;
    }
    if (file?.supported && $('conflict-warning') && !draft.stale) {
      $('conflict-warning').textContent = draft.dirty ? '最终内容尚未写入，请明确标记此文件已解决。' : '';
    }
  }
  async function resolveConflict() {
    const file = conflictSelection();
    if (!file?.supported || !mergeWritable()) return;
    const draft = draftFor(file);
    if (draft.stale) return;
    if (new TextEncoder().encode(draft.content).length > 1024 * 1024) {
      feedback('最终内容超过 1 MiB，不能在网页提交此冲突。', true, $('merge-feedback')); return;
    }
    const payload = {operation_id: state.merge.operation_id, file_id: file.file_id,
      expected_revision: draft.revision, resolution: draft.resolution};
    if (draft.resolution === 'manual') payload.content = draft.content;
    if (payload.expected_revision === undefined) {
      feedback('缺少文件 revision，不能安全标记解决，请刷新核对接口。', true, $('merge-feedback')); return;
    }
    state.mergePending = true; state.writing = true; updateMergeButtons(); renderStatus();
    feedback('正在保存并复核该文件…', false, $('merge-feedback'));
    try {
      const result = await post('/merge/resolve', payload);
      draft.dirty = false;
      if (result?.outcome) await handleMergeResult(result);
      else {
        const updated = mergeState(result);
        if (!updated || !Array.isArray(updated.files)) throw fail('冲突接口未返回更新状态，请刷新核对。', 'result_uncertain');
        state.merge = updated; renderMerge();
        const fresh = updated.files.find(f => f.file_id === file.file_id);
        if (!fresh?.resolved) throw fail('服务器未确认该文件已解决，请核对当前状态。');
        feedback('此文件已标记解决；尚未完成整个合并。', false, $('merge-feedback'));
      }
    } catch (error) {
      draft.dirty = true;
      if (error.code === 'state_changed') draft.stale = true;
      if (!ignored(error)) feedback(errorText(error), true, $('merge-feedback'));
    } finally {
      state.writing = false; state.mergePending = false; renderStatus();
      if (state.merge) await refreshMerge(); else await refresh();
      updateMergeButtons();
    }
  }
  async function handleMergeResult(result) {
    if (!['done', 'no_change', 'partial', 'conflicts'].includes(result?.outcome)) throw fail('无法确认合并结果，请刷新实际状态。', 'result_uncertain');
    feedback(resultMessage(result), result.outcome === 'partial');
    if (['done', 'no_change'].includes(result.outcome)) {
      const operation = state.merge?.operation_id;
      for (const key of state.drafts.keys()) if (key.startsWith(`${operation}:`)) state.drafts.delete(key);
      state.merge = null; renderMerge();
    }
  }
  async function completeMerge() {
    if (!mergeWritable() || $('merge-complete').disabled) return;
    await performMerge('complete', {operation_id: state.merge.operation_id, expected_revision: state.merge.expected_revision});
  }
  function confirmAbortMerge() {
    if (!mergeWritable()) return;
    const payload = {operation_id: state.merge.operation_id, expected_revision: state.merge.expected_revision};
    const m = openModal('取消本次合并？', 'abort');
    m.body.append(el('p', '将放弃本次冲突处理结果和未保存的冲突草稿，恢复合并前的 HEAD、索引与受跟踪文件。不会删除额外出现的未跟踪或忽略文件；外部修改会先复核。', 'code-summary'));
    m.review = button('重新查看取消影响', async () => {
      if (m.pending || state.writing) return;
      pending(m, true);
      await refreshMerge();
      if (state.merge?.operation_id === payload.operation_id && !state.merge.external) {
        payload.expected_revision = state.merge.expected_revision;
        modalFeedback(m, '已重新读取当前合并状态，请再次确认是否放弃本次冲突处理。');
      } else modalFeedback(m, '原合并操作已改变，请关闭此窗口后核对当前状态。', true);
      if (state.modal === m) { pending(m, false); m.confirm.disabled = state.merge?.operation_id !== payload.operation_id; }
    }, 'abort-review', 'code-outline');
    m.confirmLabel = '确认取消本次合并';
    m.confirm = button(m.confirmLabel, () => performMerge('abort', payload, m), 'abort-confirm', 'code-outline code-danger');
    m.actions.append(m.review, m.confirm);
  }
  async function performMerge(action, payload, modal = null) {
    if (!mergeWritable()) return;
    if (modal) pending(modal, true);
    state.mergePending = true; state.writing = true; updateMergeButtons(); renderStatus();
    feedback(action === 'abort' ? '正在取消本次合并…' : '正在复核并完成合并…', false, $('merge-feedback'));
    let completed = false;
    try {
      const result = await post(`/merge/${action}`, payload);
      if (result?.outcome) await handleMergeResult(result);
      else {
        const updated = mergeState(result);
        if (!updated) throw fail('服务器未返回合并状态或明确结果，请刷新核对。', 'result_uncertain');
        state.merge = updated; renderMerge();
        feedback('已读取服务器返回的合并状态，请核对是否仍需处理。');
      }
      completed = ['done', 'no_change'].includes(result?.outcome);
    } catch (error) {
      if (!ignored(error)) {
        feedback(errorText(error), true, $('merge-feedback') || $('feedback'));
        if (modal) modalFeedback(modal, errorText(error), true);
        if (error.code === 'identity_required') {
          const m = modal || openModal('补填当前仓库作者身份', 'identity');
          showIdentity(m); modalFeedback(m, '设置身份后，请关闭此窗口，再次明确点击“完成合并”。');
        }
      }
    } finally {
      state.mergePending = false;
      await refreshAfterWrite(modal, completed);
    }
  }

  $('dialog-close').addEventListener('click', closeModal);
  $('dialog').addEventListener('cancel', event => { event.preventDefault(); closeModal(); });
  $('dialog').addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const focusable = [...$('dialog').querySelectorAll('button,input,textarea,select,summary,[tabindex="0"]')]
      .filter(node => !node.disabled && !node.closest('[hidden]') && node.getClientRects().length);
    if (!focusable.length) { event.preventDefault(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  $('branches').addEventListener('keydown', event => {
    if (event.key === 'Escape') { $('branches').open = false; $('branches').querySelector('summary').focus(); }
  });
  const handlers = {
    save: () => openAction('save', {}), merge: () => openAction('merge', {}),
    pull: () => openRemote('pull'), push: () => openRemote('push'), refresh,
    'graph-more': () => loadGraph(true),
  };
  root.addEventListener('click', event => {
    const control = event.target.closest('[data-action]');
    if (control && !control.disabled && handlers[control.dataset.action]) handlers[control.dataset.action]();
  });
  let focusRefresh = null;
  function refreshOnFocus() {
    if (document.hidden || !samePage() || state.writing || state.mergePending) return;
    clearTimeout(focusRefresh);
    focusRefresh = setTimeout(() => {
      // A focus event must not abort an in-flight read. Explicit/write refreshes still re-read.
      if (samePage() && !document.hidden && !state.writing && !state.mergePending &&
          !reads.has('status') && !reads.has('graph')) refresh();
    }, 120);
  }
  listen(window, 'focus', refreshOnFocus);
  listen(document, 'visibilitychange', () => { if (!document.hidden) refreshOnFocus(); });
  function hasUnsavedWork() {
    if (state.writing || state.mergePending || state.modal?.pending || mergeHasDrafts()) return true;
    if (!state.modal) return false;
    return [...$('dialog-body').querySelectorAll('input,textarea,select')].some(node => {
      if (node.type === 'checkbox' || node.type === 'radio') return node.checked;
      return Boolean(node.value?.trim());
    });
  }
  function resumeDetailRead() {
    if (!state.detailOpen) return;
    if (state.selected && !state.detail) { selectCommit(state.selected); return; }
    const box = $('detail').querySelector('.code-diff-box[aria-busy="true"]');
    if (box?.pendingDiff) loadDiff(box, box.pendingDiff.params, box.pendingDiff.file);
  }
  function ownsNavigation(event) {
    const main = event.detail?.root;
    return main === root || Boolean(main?.contains(root));
  }
  function suspendReads() {
    state.active = false; ++state.lifecycle;
    ++state.refresh; ++state.graphRequest; ++state.detailRequest; ++state.mergeRequest;
    reads.forEach(controller => controller.abort()); reads.clear(); detailReads.clear(); diffReads.clear();
    clearTimeout(focusRefresh); clearTimeout(prefetchTimer); prefetchRun = null;
    if (state.modal?.timer) clearTimeout(state.modal.timer);
    refreshBusy(false);
    $('graph-more').disabled = false; $('graph-more').textContent = '加载更早版本';
    root.dataset.snapshotState = 'stale';
  }
  listen(document, 'workbench:before-leave', event => {
    if (!ownsNavigation(event) || !hasUnsavedWork()) return;
    event.preventDefault();
    feedback(state.writing || state.mergePending || state.modal?.pending
      ? 'Git 操作尚未结束，请等待实际结果后再离开。'
      : '请先完成或明确取消当前 Git 输入，再离开此栏目。', true,
      state.modal ? $('dialog-feedback') : $('feedback'));
  });
  listen(document, 'workbench:leave', event => {
    if (!ownsNavigation(event)) return;
    if (state.modal && !hasUnsavedWork()) closeModal();
    suspendReads();
  });
  listen(document, 'workbench:enter', event => {
    if (!ownsNavigation(event)) return;
    state.active = true;
    if (!samePage()) return;
    // DOM and immutable details are already visible; validate mutable data behind
    // them. Never treat a restored display snapshot as a write preview.
    if (event.detail.restored) {
      resumeDetailRead();
      refreshOnFocus();
    }
  });
  listen(document, 'workbench:dispose', event => {
    if (!ownsNavigation(event)) return;
    state.alive = false; suspendReads(); globalListeners.abort(); detailCache.clear(); diffCache.clear();
  });
  listen(window, 'beforeunload', event => {
    if (samePage() && hasUnsavedWork()) { event.preventDefault(); event.returnValue = ''; }
  });
  // BFCache suspends the whole document, including detached cached roots.
  // Only workbench:dispose permanently kills a root and removes its listeners.
  listen(window, 'pagehide', suspendReads);
  listen(window, 'pageshow', event => {
    if (!event.persisted) return;
    state.alive = true;
    if (!root.isConnected) return;
    state.active = true;
    resumeDetailRead();
    refresh();
  });
  refresh();
})();
