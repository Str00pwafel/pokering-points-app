window.addEventListener('error', (e) => {
  console.error('Uncaught error:', e.error || e.message);
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('Unhandled promise rejection:', e.reason);
});

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('startSessionBtn').addEventListener('click', () => {
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
    const { version, changelog } = await res.json();
    const el = document.getElementById('versionBadge');
    if (el) el.textContent = `v${version}`;
    const tooltip = document.getElementById('versionTooltip');
    if (tooltip && changelog) {
      tooltip.innerHTML = Object.entries(changelog)
        .map(([v, items]) => `<h4>v${v}</h4><ul>${items.map((c) => `<li>${c}</li>`).join('')}</ul>`)
        .join('');
    }
  } catch {}
})();
