document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('startSessionBtn').addEventListener('click', () => {
    window.location.href = '/create';
  });
});

(async () => {
  try {
    const res = await fetch('/version', { cache: 'no-store' });
    if (!res.ok) return;
    const { version, changes } = await res.json();
    const el = document.getElementById('versionBadge');
    if (el) el.textContent = `v${version}`;
    const tooltip = document.getElementById('versionTooltip');
    if (tooltip && changes && changes.length) {
      tooltip.innerHTML = `<h4>What's new in v${version}</h4><ul>${changes.map(c => `<li>${c}</li>`).join('')}</ul>`;
    }
  } catch {}
})();