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

export function showModal(
  message,
  onConfirm,
  withInput = false,
  yesNoMode = false,
  hideCancel = false,
  prefill = ''
) {
  const backdrop = document.getElementById('modalBackdrop');
  const messageEl = document.getElementById('modalMessage');
  const confirmBtn = document.getElementById('modalConfirm');
  const cancelBtn = document.getElementById('modalCancel');
  const errorEl = document.getElementById('modalError');

  messageEl.innerHTML = message;
  if (withInput) {
    messageEl.appendChild(document.createElement('br'));
    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'modalInput';
    input.maxLength = USERNAME_MAX_LEN;
    input.value = prefill;
    messageEl.appendChild(input);
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
