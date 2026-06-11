export const CONTROL_CHARS_RE =
  // eslint-disable-next-line no-control-regex
  /[\x00-\x1F\x7F\u00AD\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF]/g;
// CONTROL_CHARS_RE and USERNAME_RE mirror _CONTROL_CHARS_RE / USERNAME_RE in
// app/config.py (which points back here) — keep both sides in sync.
export const USERNAME_RE = /^[\p{L}\p{N}\s\-'_]{1,30}$/u;
export const USERNAME_MAX_LEN = 30;

export function isValidUsername(name) {
  if (typeof name !== 'string') return false;
  const cleaned = name.replace(CONTROL_CHARS_RE, '').trim();
  return USERNAME_RE.test(cleaned);
}

export function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

export function postCreate() {
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = '/create';
  document.body.appendChild(form);
  form.submit();
}

export const DEFAULT_TITLE = 'Pokering Points';
export function setDocTitle(prefix) {
  document.title = prefix ? `${prefix} — ${DEFAULT_TITLE}` : DEFAULT_TITLE;
}
