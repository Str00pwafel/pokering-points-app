import { USERNAME_MAX_LEN, isValidUsername } from './utils.js';

export const FOCUSABLE_SELECTOR =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function trapFocus(container) {
  const prevActive = document.activeElement;
  const getFocusable = () =>
    Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
      (el) => el.offsetParent !== null
    );

  const initial = getFocusable();
  if (initial.length) initial[0].focus();

  function onKeyDown(e) {
    if (e.key !== 'Tab') return;
    const els = getFocusable();
    if (!els.length) return;
    const first = els[0];
    const last = els[els.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !container.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }
  container.addEventListener('keydown', onKeyDown);
  return () => {
    container.removeEventListener('keydown', onKeyDown);
    if (prevActive && typeof prevActive.focus === 'function') prevActive.focus();
  };
}

/**
 * Show the shared modal.
 *
 * @param {string} message — modal body (textContent unless allowHtml)
 * @param {Function} [onConfirm] — called on confirm; in yesNoMode called with true/false
 * @param {object} [options]
 * @param {boolean} [options.withInput] — show a username text input
 * @param {boolean} [options.yesNoMode] — Yes/No buttons, onConfirm(boolean)
 * @param {boolean} [options.hideCancel] — hide the cancel button (and disable Escape)
 * @param {string}  [options.prefill] — initial input value
 * @param {boolean} [options.allowHtml] — render message as HTML (trusted static strings only)
 * @param {boolean} [options.withSpectateToggle] — show the "join as spectator" toggle
 */
export function showModal(message, onConfirm, options = {}) {
  const {
    withInput = false,
    yesNoMode = false,
    hideCancel = false,
    prefill = '',
    allowHtml = false,
    withSpectateToggle = false,
  } = options;
  const backdrop = document.getElementById('modalBackdrop');
  const messageEl = document.getElementById('modalMessage');
  const errorEl = document.getElementById('modalError');

  // cloneNode strips listeners accumulated when modals stack (e.g. joinFailed fires during promptUsername)
  for (const id of ['modalConfirm', 'modalCancel']) {
    const el = document.getElementById(id);
    el.replaceWith(el.cloneNode(true));
  }
  const confirmBtn = document.getElementById('modalConfirm');
  const cancelBtn = document.getElementById('modalCancel');

  if (allowHtml) {
    messageEl.innerHTML = message;
  } else {
    messageEl.textContent = message;
  }
  if (withInput) {
    messageEl.appendChild(document.createElement('br'));
    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'modalInput';
    input.maxLength = USERNAME_MAX_LEN;
    input.value = prefill;
    messageEl.appendChild(input);

    if (withSpectateToggle) {
      const toggleRow = document.createElement('div');
      toggleRow.className = 'toggle-row';
      toggleRow.style.marginTop = '12px';
      const label = document.createElement('span');
      label.textContent = 'Join as spectator';
      const switchLabel = document.createElement('label');
      switchLabel.className = 'toggle-switch';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = 'modalSpectateToggle';
      const slider = document.createElement('span');
      slider.className = 'toggle-slider';
      switchLabel.appendChild(checkbox);
      switchLabel.appendChild(slider);
      toggleRow.appendChild(label);
      toggleRow.appendChild(switchLabel);
      messageEl.appendChild(toggleRow);
    }
  }
  errorEl.textContent = '';
  cancelBtn.style.display = hideCancel ? 'none' : '';

  if (yesNoMode) {
    confirmBtn.textContent = 'Yes';
    cancelBtn.textContent = 'No';
  } else {
    confirmBtn.textContent = 'Confirm';
    cancelBtn.textContent = 'Cancel';
  }

  backdrop.classList.remove('hidden');
  const releaseFocus = trapFocus(document.getElementById('modalContent'));
  const inputEl = document.getElementById('modalInput');
  if (inputEl) inputEl.focus();

  function onEscape(e) {
    if (e.key !== 'Escape') return;
    if (hideCancel) return;
    e.preventDefault();
    cancelHandler();
  }
  document.addEventListener('keydown', onEscape);

  function cleanup() {
    backdrop.classList.add('hidden');
    confirmBtn.removeEventListener('click', confirmHandler);
    cancelBtn.removeEventListener('click', cancelHandler);
    document.removeEventListener('keydown', onEscape);
    releaseFocus();
  }

  function confirmHandler() {
    const err = document.getElementById('modalError');
    if (withInput) {
      const inp = document.getElementById('modalInput');
      if (!inp || !isValidUsername(inp.value.trim())) {
        err.textContent = "Name: letters, digits, spaces, - _ ' (max 30).";
        return;
      }
    }
    err.textContent = '';
    if (onConfirm) {
      yesNoMode ? onConfirm(true) : onConfirm();
    }
    cleanup();
  }

  function cancelHandler() {
    if (onConfirm && yesNoMode) onConfirm(false);
    cleanup();
  }

  confirmBtn.addEventListener('click', confirmHandler);
  cancelBtn.addEventListener('click', cancelHandler);
}
