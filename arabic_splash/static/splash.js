(() => {
  const splash = document.getElementById('app-splash');
  if (!splash) return;
  const hide = () => { splash.classList.add('splash-done'); window.setTimeout(() => splash.remove(), 500); };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const start = () => window.setTimeout(hide, reduced ? 300 : 3100);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once:true });
  else start();
})();
