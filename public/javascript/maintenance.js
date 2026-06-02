const REFRESH_MS = 60_000;

function formatMaintenanceTime(startsAt, timezone) {
  const date = new Date(startsAt);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: timezone || 'Europe/Amsterdam',
    timeZoneName: 'short',
  }).format(date);
}

function ensureBanner() {
  let banner = document.getElementById('maintenanceBanner');
  if (banner) return banner;

  banner = document.createElement('div');
  banner.id = 'maintenanceBanner';
  banner.className = 'maintenance-banner hidden';
  banner.setAttribute('role', 'status');
  banner.setAttribute('aria-live', 'polite');
  document.body.prepend(banner);
  return banner;
}

async function refreshMaintenanceBanner() {
  const banner = ensureBanner();
  try {
    const res = await fetch('/maintenance', { cache: 'no-store' });
    if (!res.ok) {
      banner.classList.add('hidden');
      return;
    }
    const data = await res.json();
    if (!data?.enabled || !data.startsAt) {
      banner.classList.add('hidden');
      return;
    }

    const formattedTime = formatMaintenanceTime(data.startsAt, data.timezone);
    banner.textContent =
      data.message ||
      `Scheduled restart/deploy at ${formattedTime || data.startsAt}. Please avoid starting long rounds near that time.`;
    banner.classList.remove('hidden');
  } catch (err) {
    console.error('Maintenance banner error:', err);
    banner.classList.add('hidden');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  refreshMaintenanceBanner();
  window.setInterval(refreshMaintenanceBanner, REFRESH_MS);
});
