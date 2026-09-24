/* Apply appearance before paint; delegated controls survive retained-page navigation. */
(() => {
  'use strict';
  const key = 'workbench.theme', root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)');
  // Easter egg: "vapor" stays out of every menu until found (five quick taps on the avatar, or the
  // Konami code). Its key remembers the everyday theme to return to, and its presence means "found".
  const themes = ['light', 'dark', 'system', 'vapor'], eggKey = 'workbench.vapor';
  const konami = 'ArrowUp,ArrowUp,ArrowDown,ArrowDown,ArrowLeft,ArrowRight,ArrowLeft,ArrowRight,b,a';
  const sunset = '<svg class="ui-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 17h18M6 20.5h12M7 14a5 5 0 0 1 10 0"/><path d="M12 4v2.5M5.3 7.3l1.6 1.6M18.7 7.3l-1.6 1.6"/></svg>';
  const read = name => { try { return localStorage.getItem(name); } catch (_) { return null; } };
  const write = (name, value) => { try { localStorage.setItem(name, value); } catch (_) {} };
  let choice = themes.includes(read(key)) ? read(key) : 'system';
  let taps = 0, lastTap = 0, keys = [], toastTimer = 0;
  function vaporAssets() {
    if (choice === 'vapor' && !document.querySelector('link[data-vapor]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet'; link.href = '/static/vapor.css'; link.dataset.vapor = '';
      link.setAttribute('blocking', 'render');  // no flash of the everyday theme on load
      document.head.append(link);
    }
    if (read(eggKey) === null) return;
    document.querySelectorAll('.theme-options, .theme-settings').forEach(group => {
      if (group.querySelector('[data-theme-choice=vapor]')) return;
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.themeChoice = 'vapor'; button.innerHTML = sunset + '<span>蒸汽波</span>';
      group.append(button);
    });
  }
  function apply() {
    const scheme = choice === 'vapor' ? 'dark' : choice;
    vaporAssets();
    root.dataset.theme = choice;
    root.dataset.resolvedTheme = scheme === 'system' ? (media.matches ? 'dark' : 'light') : scheme;
    root.style.colorScheme = scheme === 'system' ? 'light dark' : scheme;
    document.querySelectorAll('[data-theme-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === choice)));
  }
  function choose(value) {
    if (value === 'vapor' && choice !== 'vapor') write(eggKey, choice);
    choice = value; write(key, value); apply();
  }
  function toast(text) {
    let note = document.querySelector('.vapor-toast');
    if (!note) { note = document.createElement('div'); note.className = 'vapor-toast'; note.setAttribute('role', 'status'); document.body.append(note); }
    note.textContent = text; note.classList.add('is-shown');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => note.classList.remove('is-shown'), 2400);
  }
  function toggleVapor() {
    const back = read(eggKey);
    choose(choice === 'vapor' ? (themes.includes(back) && back !== 'vapor' ? back : 'system') : 'vapor');
    toast(choice === 'vapor' ? 'ＡＥＳＴＨＥＴＩＣ · 蒸汽波已开启' : '已回到日常外观');
  }
  apply();
  document.addEventListener('DOMContentLoaded', apply);
  document.addEventListener('workbench:enter', apply);
  media.addEventListener('change', apply);
  window.addEventListener('storage', event => {
    if (event.key === key) { choice = themes.includes(event.newValue) ? event.newValue : 'system'; apply(); }
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-theme-choice]');
    if (button) {
      choose(button.dataset.themeChoice);
      const menu = button.closest('.theme-menu');
      if (menu) { menu.open = false; menu.querySelector('summary').focus(); }
    }
    const avatar = event.target.closest('.console-avatar');
    if (avatar) {
      const now = Date.now(); taps = now - lastTap < 600 ? taps + 1 : 1; lastTap = now;
      if (taps >= 3 && !calm.matches) avatar.animate([{transform: 'scale(1)'}, {transform: 'scale(1.18)'}, {transform: 'scale(1)'}], 180);
      if (taps === 5) { taps = 0; toggleVapor(); }
    }
    document.querySelectorAll('.theme-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') document.querySelectorAll('.theme-menu[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
    if (event.target instanceof Element && event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable=false])')) return;
    keys = [...keys, event.key.length === 1 ? event.key.toLowerCase() : event.key].slice(-10);
    if (keys.join() === konami) { keys = []; toggleVapor(); }
  });
})();
