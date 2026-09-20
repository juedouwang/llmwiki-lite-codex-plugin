/* Apply appearance before paint; delegated controls survive retained-page navigation. */
(() => {
  'use strict';
  const key = 'workbench.theme', root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let choice = 'system';
  try { const saved = localStorage.getItem(key); if (['light', 'dark', 'system'].includes(saved)) choice = saved; } catch (_) {}
  function apply() {
    root.dataset.theme = choice;
    root.dataset.resolvedTheme = choice === 'system' ? (media.matches ? 'dark' : 'light') : choice;
    root.style.colorScheme = choice === 'system' ? 'light dark' : choice;
    document.querySelectorAll('[data-theme-choice]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === choice)));
  }
  apply();
  document.addEventListener('DOMContentLoaded', apply);
  document.addEventListener('workbench:enter', apply);
  media.addEventListener('change', apply);
  window.addEventListener('storage', event => {
    if (event.key === key) { choice = ['light', 'dark', 'system'].includes(event.newValue) ? event.newValue : 'system'; apply(); }
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-theme-choice]');
    if (button) {
      choice = button.dataset.themeChoice;
      try { localStorage.setItem(key, choice); } catch (_) {}
      apply();
      const menu = button.closest('.theme-menu');
      if (menu) { menu.open = false; menu.querySelector('summary').focus(); }
    }
    document.querySelectorAll('.theme-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') document.querySelectorAll('.theme-menu[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); });
  });
})();
