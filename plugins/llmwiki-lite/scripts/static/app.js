
function copyText(id, button) {
  const el = document.getElementById(id);
  if (!el) return;
  const clone = el.cloneNode(true);
  clone.querySelectorAll('button').forEach(item => item.remove());
  navigator.clipboard.writeText(clone.innerText.trim()).then(() => {
    const old = button.innerText;
    button.innerText = '已复制';
    setTimeout(() => button.innerText = old, 1200);
  });
}
function filterPages(input) {
  const q=input.value.trim().toLowerCase();
  const scope=input.closest('.knowledge-list')||input.closest('.sidebar')||input.closest('.panel')||document;
  let visible=0;
  scope.querySelectorAll('[data-page-title]').forEach(el=>{el.hidden=!el.dataset.pageTitle.includes(q);if(!el.hidden)visible++;});
  const empty=scope.querySelector('.filter-empty');if(empty)empty.hidden=visible!==0;
}
const literatureState = {status: 'all', kind: 'all', query: '', favorite: false};
function setLiteratureFilter(group, value, button) {
  literatureState[group] = value;
  document.querySelectorAll(`[data-filter-group="${group}"]`).forEach(el => el.classList.remove('is-active'));
  if (button) button.classList.add('is-active');
  applyLiteratureFilters();
}
function filterLiterature(input) {
  literatureState.query = input.value.trim().toLowerCase();
  applyLiteratureFilters();
}
function applyLiteratureFilters() {
  const cards = Array.from(document.querySelectorAll('[data-literature-card]'));
  let visible = 0;
  cards.forEach(card => {
    const statusOK = literatureState.status === 'all' || card.dataset.literatureStatus === literatureState.status;
    const kindOK = literatureState.kind === 'all' || card.dataset.literatureKind === literatureState.kind;
    const favOK = !literatureState.favorite || card.dataset.favorite === '1';
    const queryOK = !literatureState.query || card.dataset.pageTitle.includes(literatureState.query);
    const show = statusOK && kindOK && favOK && queryOK;
    card.hidden = !show;
    if (show) visible += 1;
  });
  const count = document.getElementById('literature-result-count');
  if (count) count.textContent = String(visible);
  const empty = document.getElementById('literature-filter-empty');
  if (empty) empty.classList.toggle('is-visible', cards.length > 0 && visible === 0);
}
function toggleFavorites(button) {
  literatureState.favorite = !literatureState.favorite;
  if(button){button.classList.toggle('is-active',literatureState.favorite);button.setAttribute('aria-pressed',String(literatureState.favorite));}
  applyLiteratureFilters();
}
function initFavorites() {
  const buttons = Array.from(document.querySelectorAll('.fav-button'));
  const setFav = (btn, on) => {
    btn.classList.toggle('is-fav', on);
    btn.setAttribute('aria-pressed', String(on));
    btn.setAttribute('aria-label', on ? '取消收藏' : '收藏文献');
    btn.title=on ? '取消收藏' : '收藏文献';
    const card = btn.closest('[data-literature-card]');
    if (card) card.dataset.favorite = on ? '1' : '0';
  };
  buttons.forEach(btn => {
    if (btn.dataset.favoriteBound) return;
    btn.dataset.favoriteBound = '1';
    const key = 'llmwiki-fav-' + btn.dataset.favPath;
    try {
      let value=localStorage.getItem(key);
      const path=btn.dataset.favPath;
      if(value===null&&path.includes(':')){value=localStorage.getItem('llmwiki-fav-'+path.slice(path.indexOf(':')+1));if(value!==null)localStorage.setItem(key,value);}
      setFav(btn,value==='1');
    } catch (_) {setFav(btn,false);}
    btn.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      const on = !btn.classList.contains('is-fav');
      setFav(btn, on);
      try { localStorage.setItem(key, on ? '1' : '0'); } catch (_) {}
      applyLiteratureFilters();
    });
  });
}
function toggleConsoleSidebar(force) {
  const sidebar = document.getElementById('console-sidebar');
  const overlay = document.getElementById('console-overlay');
  if (!sidebar || !overlay) return;
  const open = typeof force === 'boolean' ? force : !sidebar.classList.contains('is-open');
  sidebar.classList.toggle('is-open', open);
  overlay.classList.toggle('is-visible', open);
  document.body.classList.toggle('console-locked', open);
  document.querySelector('.console-menu-button')?.setAttribute('aria-expanded',String(open));
  if(open){sidebar.inert=false;sidebar.querySelector('a,button')?.focus();}
  else if(sidebar.contains(document.activeElement)&&innerWidth<=775)document.querySelector('.console-menu-button')?.focus();
  sidebar.inert=!open&&innerWidth<=775;
}
function initConsoleShell() {
  toggleConsoleSidebar(false);
  const overlay = document.getElementById('console-overlay');
  if (overlay && !overlay.dataset.bound) { overlay.dataset.bound = '1'; overlay.addEventListener('click', () => toggleConsoleSidebar(false)); }
  document.querySelectorAll('.console-sidebar a').forEach(link => {
    link.addEventListener('click', () => toggleConsoleSidebar(false));
  });
}
function initLiterature() {applyLiteratureFilters();}
let lightboxGroup = [];
let lightboxIndex = 0;
function openLightbox(src, caption) {
  const lb = document.getElementById('lightbox');
  if (!lb) return;
  lightboxGroup = Array.from(document.querySelectorAll('[data-lightbox-src]'));
  lightboxIndex = Math.max(0, lightboxGroup.findIndex(el => el.dataset.lightboxSrc === src));
  renderLightbox(src, caption);
  lb.hidden = false;
  document.body.classList.add('lightbox-open');
}
function renderLightbox(src, caption) {
  const img = document.getElementById('lightbox-image');
  const cap = document.getElementById('lightbox-caption');
  if (img) { img.src = src; img.alt = caption || ''; }
  if (cap) cap.textContent = caption || '';
}
function closeLightbox() {
  const lb = document.getElementById('lightbox');
  if (lb) lb.hidden = true;
  document.body.classList.remove('lightbox-open');
}
function stepLightbox(delta) {
  if (!lightboxGroup.length) return;
  lightboxIndex = (lightboxIndex + delta + lightboxGroup.length) % lightboxGroup.length;
  const el = lightboxGroup[lightboxIndex];
  if (el) renderLightbox(el.dataset.lightboxSrc, el.dataset.lightboxTitle || '');
}
function initLightbox() {
  const lb = document.getElementById('lightbox');
  if (!lb) return;
  document.addEventListener('click', function(e) {
    const trigger = e.target.closest('[data-lightbox-src]');
    if (trigger) {
      e.preventDefault();
      openLightbox(trigger.dataset.lightboxSrc, trigger.dataset.lightboxTitle || '');
    }
  });
  lb.addEventListener('click', function(e) {
    if (e.target.closest('.lightbox-nav') || e.target.closest('.lightbox-close')) return;
    closeLightbox();
  });
  const closeBtn = lb.querySelector('.lightbox-close');
  const prevBtn = lb.querySelector('.lightbox-prev');
  const nextBtn = lb.querySelector('.lightbox-next');
  if (closeBtn) closeBtn.addEventListener('click', closeLightbox);
  if (prevBtn) prevBtn.addEventListener('click', function() { stepLightbox(-1); });
  if (nextBtn) nextBtn.addEventListener('click', function() { stepLightbox(1); });
  document.addEventListener('keydown', function(e) {
    if (lb.hidden) return;
    if (e.key === 'Escape') closeLightbox();
    else if (e.key === 'ArrowLeft') stepLightbox(-1);
    else if (e.key === 'ArrowRight') stepLightbox(1);
  });
}
document.addEventListener('DOMContentLoaded', () => {
  initConsoleShell();
  initLiterature();
  initLightbox();
  initFavorites();
});
// Disclosures are reachable from links without keeping entire help sections open.
function openAnchorDetails() {
  let target;try {target=document.getElementById(decodeURIComponent(location.hash.slice(1)));}catch{return;}
  if(target?.tagName==='DETAILS'){target.open=true;target.scrollIntoView({block:'start'});}
}
window.addEventListener('hashchange',openAnchorDetails);
openAnchorDetails();
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){
    const wasOpen=document.querySelector('.console-sidebar.is-open');
    toggleConsoleSidebar(false);if(wasOpen)document.querySelector('.console-menu-button')?.focus();
    document.querySelectorAll('.action-menu[open]').forEach(menu=>menu.open=false);
  }
  if(event.key==='Tab'&&document.body.classList.contains('console-locked')){
    const items=Array.from(document.querySelectorAll('.console-sidebar a,.console-sidebar button,.console-sidebar summary')).filter(el=>el.getClientRects().length);
    const first=items[0],last=items[items.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }
});
document.addEventListener('click',event=>{
  document.querySelectorAll('.action-menu[open]').forEach(menu=>{if(!menu.contains(event.target)||event.target.closest('button'))menu.open=false;});
});
window.matchMedia('(min-width:776px)').addEventListener('change',()=>toggleConsoleSidebar(false));

// New column DOM needs shell bindings once; retained columns keep their controls.
const columnFilters = new WeakMap();
document.addEventListener('workbench:leave', event => columnFilters.set(event.detail.root, {...literatureState}));
document.addEventListener('workbench:enter', event => {
  initConsoleShell();
  if (event.detail.restored) Object.assign(literatureState, columnFilters.get(event.detail.root) || {status:'all',kind:'all',query:'',favorite:false});
  if (!event.detail.restored) {
    Object.assign(literatureState, {status:'all',kind:'all',query:'',favorite:false});
    initLiterature(); initFavorites();
  }
});
