window.addEventListener('error', (e) => {
  console.error('Uncaught error:', e.error || e.message);
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('Unhandled promise rejection:', e.reason);
});

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('startSessionBtn').addEventListener('click', () => {
    sessionStorage.setItem('pokeringIsCreator', '1');
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/create';
    document.body.appendChild(form);
    form.submit();
  });
});

(async () => {
  try {
    const res = await fetch('/version', { cache: 'no-store' });
    if (!res.ok) return;
    const { version, tooltipHtml } = await res.json();
    const el = document.getElementById('versionBadge');
    if (el) el.textContent = `v${version}`;
    const tooltip = document.getElementById('versionTooltip');
    if (tooltip && tooltipHtml) tooltip.innerHTML = tooltipHtml;
  } catch {}
})();
