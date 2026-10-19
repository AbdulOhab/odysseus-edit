/**
 * Odysseus-Edit replacement for odysseus/static/js/ui.js.
 *
 * The editor only uses showToast, showError and styledConfirm from the
 * upstream module (1,500 lines that also pull in theming, chat and the
 * window manager). This shim keeps those three call signatures.
 */

let toastEl = null;
let hideTimer = null;

function ensureToast() {
  if (toastEl && toastEl.isConnected) return toastEl;
  toastEl = document.createElement('div');
  toastEl.className = 'oe-toast';
  toastEl.setAttribute('role', 'status');
  toastEl.setAttribute('aria-live', 'polite');
  document.body.appendChild(toastEl);
  return toastEl;
}

/**
 * showToast(msg, durationMs) or showToast(msg, {duration, action, onAction, error}).
 * Upstream's default duration is 1.2s for plain toasts and 5s with options.
 */
export function showToast(msg, durationOrOpts) {
  const el = ensureToast();
  let duration = 1200;
  let actionLabel = null;
  let onAction = null;
  let isError = false;
  if (typeof durationOrOpts === 'number') {
    duration = durationOrOpts;
  } else if (durationOrOpts && typeof durationOrOpts === 'object') {
    duration = durationOrOpts.duration || 5000;
    actionLabel = durationOrOpts.action || null;
    onAction = durationOrOpts.onAction || null;
    isError = !!durationOrOpts.error;
  }
  // Short toasts are hard to read when they carry a sentence.
  const text = String(msg ?? '');
  if (duration < 2500 && text.length > 40) duration = 2500;

  el.textContent = '';
  el.classList.toggle('oe-toast-error', isError);
  const span = document.createElement('span');
  span.textContent = text;
  el.appendChild(span);
  if (actionLabel && typeof onAction === 'function') {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'oe-toast-action';
    btn.textContent = actionLabel;
    btn.addEventListener('click', () => {
      el.classList.remove('oe-toast-show');
      onAction();
    });
    el.appendChild(btn);
  }
  el.classList.add('oe-toast-show');
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => el.classList.remove('oe-toast-show'), duration);
}

export function showError(msg) {
  showToast(msg, { duration: 6000, error: true });
}

/**
 * Promise-based confirm dialog. Resolves true (confirm), false (cancel or
 * Escape) or 'alternate' when alternateText is given and that button is used.
 */
export function styledConfirm(message, {
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  alternateText = '',
  title = 'Confirm',
  danger = false,
} = {}) {
  return new Promise(resolve => {
    const previouslyFocused = document.activeElement;
    // Upstream code drives this dialog by id (e.g. Enter/Escape on the
    // rasterize prompt clicks #styled-confirm-ok / -cancel), so keep its ids.
    document.getElementById('styled-confirm-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'oe-dialog-overlay';
    overlay.id = 'styled-confirm-overlay';
    overlay.innerHTML = `
      <div class="oe-dialog" role="dialog" aria-modal="true" aria-labelledby="styled-confirm-title" aria-describedby="styled-confirm-msg">
        <h2 class="oe-dialog-title" id="styled-confirm-title"></h2>
        <p class="oe-dialog-msg" id="styled-confirm-msg"></p>
        <div class="oe-dialog-actions">
          <button type="button" class="oe-btn" id="styled-confirm-cancel" data-act="cancel"></button>
          <button type="button" class="oe-btn" id="styled-confirm-alt" data-act="alternate" hidden></button>
          <button type="button" class="oe-btn oe-btn-primary" id="styled-confirm-ok" data-act="ok"></button>
        </div>
      </div>`;
    overlay.querySelector('#styled-confirm-title').textContent = title;
    overlay.querySelector('#styled-confirm-msg').textContent = String(message ?? '');
    const okBtn = overlay.querySelector('[data-act="ok"]');
    const cancelBtn = overlay.querySelector('[data-act="cancel"]');
    const altBtn = overlay.querySelector('[data-act="alternate"]');
    okBtn.textContent = confirmText;
    okBtn.classList.toggle('oe-btn-danger', !!danger);
    cancelBtn.textContent = cancelText;
    if (alternateText) {
      altBtn.hidden = false;
      altBtn.textContent = alternateText;
    }

    const finish = result => {
      document.removeEventListener('keydown', onKey, true);
      overlay.remove();
      if (previouslyFocused?.isConnected) previouslyFocused.focus?.({ preventScroll: true });
      resolve(result);
    };
    const onKey = e => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        finish(false);
      } else if (e.key === 'Enter' && document.activeElement === okBtn) {
        e.preventDefault();
        finish(true);
      }
    };
    okBtn.addEventListener('click', () => finish(true));
    cancelBtn.addEventListener('click', () => finish(false));
    altBtn.addEventListener('click', () => finish('alternate'));
    overlay.addEventListener('click', e => { if (e.target === overlay) finish(false); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(overlay);
    (danger ? cancelBtn : okBtn).focus();
  });
}

export default { showToast, showError, styledConfirm };
