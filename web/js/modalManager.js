/**
 * Odysseus-Edit replacement for odysseus/static/js/modalManager.js.
 *
 * Upstream is the workspace window manager (docking, tiling, z-order).
 * The editor only uses it to minimise floating panels (History, the
 * adjustment popups) into a dock and restore them later, so this shim keeps
 * the same API with a small chip dock: #minimized-dock > [data-modal-id].
 */

const _state = new Map();

function dockEl() {
  let dock = document.getElementById('minimized-dock');
  if (!dock) {
    dock = document.createElement('div');
    dock.id = 'minimized-dock';
    dock.className = 'oe-dock';
    dock.setAttribute('aria-label', 'Minimised panels');
    document.body.appendChild(dock);
  }
  return dock;
}

function chipFor(id) {
  return document.querySelector(`#minimized-dock [data-modal-id="${CSS.escape(id)}"]`);
}

function renderChip(id) {
  const entry = _state.get(id);
  if (!entry) return;
  let chip = chipFor(id);
  if (!chip) {
    chip = document.createElement('div');
    chip.className = 'oe-dock-chip';
    chip.dataset.modalId = id;
    chip.innerHTML = `
      <button type="button" class="oe-dock-restore"></button>
      <button type="button" class="oe-dock-close" aria-label="Close">&times;</button>`;
    chip.querySelector('.oe-dock-restore').addEventListener('click', () => restore(id));
    chip.querySelector('.oe-dock-close').addEventListener('click', () => close(id));
    dockEl().appendChild(chip);
  }
  const restoreBtn = chip.querySelector('.oe-dock-restore');
  restoreBtn.innerHTML = `${entry.icon || ''}<span></span>`;
  restoreBtn.querySelector('span').textContent = entry.label || id;
  restoreBtn.title = `Restore ${entry.label || 'panel'}`;
}

function removeChip(id) {
  chipFor(id)?.remove();
  const dock = document.getElementById('minimized-dock');
  if (dock && !dock.children.length) dock.remove();
}

export function register(id, { restoreFn, closeFn, label, icon } = {}) {
  _state.set(id, { restoreFn, closeFn, label, icon, isMinimized: false });
}

export function unregister(id) {
  _state.delete(id);
  removeChip(id);
}

export function isRegistered(id) { return _state.has(id); }
export function isMinimized(id) { return _state.get(id)?.isMinimized === true; }

export function minimize(id) {
  const entry = _state.get(id);
  if (!entry) return;
  entry.isMinimized = true;
  renderChip(id);
}

export function restore(id) {
  const entry = _state.get(id);
  if (!entry) return;
  entry.isMinimized = false;
  removeChip(id);
  try { entry.restoreFn?.(); } catch (err) { console.error('[modalManager] restore failed', err); }
}

export function toggle(id) {
  if (isMinimized(id)) restore(id);
  else minimize(id);
}

export function close(id) {
  const entry = _state.get(id);
  removeChip(id);
  if (!entry) return;
  try { entry.closeFn?.(); } catch (err) { console.error('[modalManager] close failed', err); }
  _state.delete(id);
}

// Upstream adds a minimise button to workspace modals; the editor's own
// panels already render theirs.
export function injectMinimizeButton() {}

export default { register, unregister, isRegistered, isMinimized, minimize, restore, toggle, close, injectMinimizeButton };
