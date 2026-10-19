/**
 * Photoshop-style editor chrome: a menu bar (File, Edit, Image, Layer,
 * Select, Filter, View, Help) over an options bar for the active tool.
 *
 * It replaces upstream's icon top bar visually but drives it: the legacy top
 * bar stays in the DOM, hidden, and every menu item clicks one of its buttons,
 * one of the editor's panel buttons, or sends the keyboard shortcut the
 * editor already handles. Menus that upstream builds (Image, Select, Filter,
 * View) are read from its hidden dropdowns each time they open, so their
 * items, checkmarks and disabled states stay in sync. The options bar mirrors
 * controls from the right panel in both directions.
 */

import { attachColorPicker } from '../js/colorPicker.js';
import { host } from './host.js';

const ADJUSTMENTS = [
  ['brightness-contrast', 'Brightness/Contrast…'],
  ['levels', 'Levels…'],
  ['curves', 'Curves…'],
  ['exposure', 'Exposure…'],
  ['vibrance', 'Vibrance…'],
  ['hue-saturation', 'Hue/Saturation…'],
  ['color-balance', 'Color Balance…'],
  ['black-white', 'Black & White…'],
  ['white-balance', 'White Balance…'],
  ['shadows-highlights', 'Shadows/Highlights…'],
  ['selective-color', 'Selective Color…'],
  ['gradient-map', 'Gradient Map…'],
];

const HINTS = {
  move: 'Drag to move the layer · Arrow keys nudge',
  hand: 'Drag to pan · Hold Space to pan with any tool',
  crop: 'Drag to choose the area · Enter applies',
  transform: 'Drag the handles · Enter applies · Esc cancels',
  pen: 'Click to add points · Enter closes the path',
  eyedropper: 'Click the canvas to pick a colour',
  sam: 'Click an object to select it (AI)',
  inpaint: 'Paint over the area to replace, then describe it (AI)',
  rembg: 'Removes the background into a new layer (AI)',
  sharpen: 'Sharpens the image into a new layer (AI)',
};

const SIZE = { kind: 'range', source: '#ge-brush-controls .ge-size-slider', label: 'Size', value: '#ge-brush-controls .ge-size-label' };
const COLOR = { kind: 'color', source: '#ge-brush-controls .ge-color-picker', label: 'Color' };
const brushLike = (prefix) => [
  { kind: 'range', source: `#ge-${prefix}-opacity`, label: 'Opacity' },
  { kind: 'range', source: `#ge-${prefix}-flow`, label: 'Flow' },
  { kind: 'range', source: `#ge-${prefix}-softness`, label: 'Softness' },
];

const TOOL_OPTIONS = {
  move: [
    { kind: 'number', source: '#ge-layer-x', label: 'X' },
    { kind: 'number', source: '#ge-layer-y', label: 'Y' },
    { kind: 'number', source: '#ge-layer-width', label: 'W' },
    { kind: 'number', source: '#ge-layer-height', label: 'H' },
    { kind: 'button', source: '#ge-selected-align', label: 'Align…' },
  ],
  transform: [
    { kind: 'number', source: '#ge-layer-x', label: 'X' },
    { kind: 'number', source: '#ge-layer-y', label: 'Y' },
    { kind: 'number', source: '#ge-layer-width', label: 'W' },
    { kind: 'number', source: '#ge-layer-height', label: 'H' },
  ],
  brush: [COLOR, SIZE, ...brushLike('brush'), { kind: 'select', source: '#ge-brush-blend', label: 'Mode' }],
  eraser: [SIZE, ...brushLike('eraser')],
  clone: [SIZE, { kind: 'select', source: '#ge-clone-sample-mode', label: 'Sample' }, ...brushLike('clone')],
  heal: [SIZE, { kind: 'select', source: '#ge-clone-sample-mode', label: 'Sample' }, ...brushLike('clone')],
  smudge: [SIZE, { kind: 'range', source: '#ge-smudge-strength', label: 'Strength' }, { kind: 'range', source: '#ge-brush-softness', label: 'Softness' }],
  dodge: [SIZE, ...brushLike('brush')],
  burn: [SIZE, ...brushLike('brush')],
  gradient: [
    { kind: 'select', source: '#ge-gradient-type', label: 'Type' },
    { kind: 'color', source: '#ge-gradient-start', label: 'Start' },
    { kind: 'color', source: '#ge-gradient-end', label: 'End' },
    { kind: 'range', source: '#ge-gradient-opacity', label: 'Opacity' },
  ],
  text: [
    { kind: 'select', source: '#ge-text-font', label: 'Font' },
    { kind: 'number', source: '#ge-text-size', label: 'Size' },
    { kind: 'color', source: '#ge-text-color', label: 'Color' },
    { kind: 'toggle', source: '#ge-text-bold', label: 'B', title: 'Bold' },
    { kind: 'toggle', source: '#ge-text-italic', label: 'I', title: 'Italic' },
  ],
  shape: [
    { kind: 'color', source: '#ge-shape-fill', label: 'Fill' },
    { kind: 'color', source: '#ge-shape-stroke', label: 'Stroke' },
    { kind: 'number', source: '#ge-shape-stroke-width', label: 'Width' },
    { kind: 'number', source: '#ge-shape-radius', label: 'Radius' },
  ],
  marquee: [
    { kind: 'select', source: '#ge-marquee-constraint', label: 'Style' },
    { kind: 'number', source: '#ge-marquee-width', label: 'W' },
    { kind: 'number', source: '#ge-marquee-height', label: 'H' },
  ],
  lasso: [
    { kind: 'range', source: '#ge-lasso-feather', label: 'Feather' },
    { kind: 'range', source: '#ge-lasso-grow', label: 'Grow' },
  ],
  wand: [
    { kind: 'range', source: '#ge-wand-tolerance', label: 'Tolerance' },
    { kind: 'range', source: '#ge-wand-feather', label: 'Feather' },
  ],
  eyedropper: [{ kind: 'select', source: '#ge-eyedropper-sample', label: 'Sample' }],
  inpaint: [{ kind: 'range', source: '#ge-inpaint-brush-slider', label: 'Brush' }],
};

const ICONS = {
  undo: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>',
  redo: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/></svg>',
  history: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/></svg>',
  compare: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/></svg>',
  keyboard: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>',
  home: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="m21 15-4.5-4.5L7 20"/></svg>',
};

// ── Small DOM helpers ─────────────────────────────────────────────────────

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function textOf(node) {
  return (node?.textContent || '').replace(/\s+/g, ' ').trim();
}

/** Send a keyboard shortcut the editor already handles. */
function sendKeys(key, { ctrl = false, shift = false, alt = false, code } = {}) {
  const event = new KeyboardEvent('keydown', {
    key, code: code || (key.length === 1 ? `Key${key.toUpperCase()}` : key),
    ctrlKey: ctrl, shiftKey: shift, altKey: alt, bubbles: true, cancelable: true,
  });
  document.body.dispatchEvent(event);
}

function clickId(id) {
  return () => document.getElementById(id)?.click();
}

const isMac = /Mac|iP(hone|ad)/.test(navigator.platform || '');
const mod = isMac ? '⌘' : 'Ctrl+';
const alt = isMac ? '⌥' : 'Alt+';
const shift = isMac ? '⇧' : 'Shift+';

// ── Menu bar ──────────────────────────────────────────────────────────────

/**
 * Item shapes: {label, shortcut, run, disabled, checked, submenu: [], render: fn}
 * plus {separator: true} and {header: 'text'}.
 */
function legacyMenuItems(menu) {
  // Read one of upstream's hidden dropdowns into menu items.
  const items = [];
  if (!menu) return items;
  for (const node of menu.children) {
    if (node.classList.contains('dropdown-section-divider')) {
      items.push({ separator: true });
    } else if (node.classList.contains('ge-filter-submenu-label') || node.classList.contains('dropdown-section-label')) {
      items.push({ header: textOf(node) });
    } else if (node.matches('button.dropdown-item-compact')) {
      const shortcut = textOf(node.querySelector('.dropdown-shortcut'));
      const labelNode = [...node.children].reverse().find(c => c.tagName === 'SPAN' && !c.classList.contains('dropdown-shortcut') && !c.classList.contains('dropdown-icon'));
      const isCheck = node.getAttribute('role') === 'menuitemcheckbox';
      items.push({
        label: textOf(labelNode || node).replace(shortcut, '').trim(),
        shortcut: shortcut.replace(/Ctrl\+/g, mod).replace(/Alt\+/g, alt).replace(/Shift\+/g, shift),
        disabled: node.disabled,
        checked: isCheck ? node.getAttribute('aria-checked') === 'true' || node.classList.contains('active') : undefined,
        run: () => node.click(),
      });
    }
  }
  return items;
}

function adjustmentItems(api) {
  return ADJUSTMENTS.map(([type, label]) => ({ label, run: () => api.addAdjustmentLayer(type) }));
}

function buildMenus({ api, legacy }) {
  const tool = (id) => () => document.querySelector(`.ge-tool-btn[data-tool="${id}"]`)?.click();
  return [
    {
      id: 'file', label: 'File', items: () => [
        { label: 'New…', shortcut: `${alt}${mod}N`, run: () => window.dispatchEvent(new CustomEvent('oe:command', { detail: 'new' })) },
        { label: 'Open…', shortcut: `${mod}O`, run: () => window.dispatchEvent(new CustomEvent('oe:command', { detail: 'open' })) },
        { label: 'Place', submenu: [
          { label: 'From file…', run: clickId('ge-import-file') },
          { label: 'From clipboard', run: clickId('ge-import-paste') },
          { label: 'Recent images…', run: clickId('ge-import-gallery') },
        ] },
        { separator: true },
        { label: 'Save', shortcut: `${mod}S`, run: clickId('ge-save') },
        { label: 'Save As…', shortcut: `${shift}${mod}S`, run: clickId('ge-export-gallery') },
        { label: 'Export Image…', run: clickId('ge-download') },
        { separator: true },
        { label: 'Save Project (.json)', run: clickId('ge-save-project') },
        { label: 'Load Project…', run: clickId('ge-load-project') },
        { separator: true },
        { label: 'Close', run: clickId('oe-home-btn') },
      ],
    },
    {
      id: 'edit', label: 'Edit', items: () => [
        { label: 'Undo', shortcut: `${mod}Z`, disabled: !!document.getElementById('ge-undo')?.disabled, run: clickId('ge-undo') },
        { label: 'Redo', shortcut: `${shift}${mod}Z`, disabled: !!document.getElementById('ge-redo')?.disabled, run: clickId('ge-redo') },
        { label: 'History', run: clickId('ge-history-btn') },
        { separator: true },
        { label: 'Cut', shortcut: `${mod}X`, run: () => sendKeys('x', { ctrl: true }) },
        { label: 'Copy', shortcut: `${mod}C`, run: () => sendKeys('c', { ctrl: true }) },
        { label: 'Paste', shortcut: `${mod}V`, run: clickId('ge-import-paste') },
        { separator: true },
        { label: 'Fill', shortcut: `${alt}⌫`, run: () => sendKeys('Backspace', { alt: true, code: 'Backspace' }) },
        { label: 'Free Transform', shortcut: `${alt}${mod}T`, run: () => sendKeys('t', { ctrl: true, alt: true }) },
        { label: 'Quick Mask', shortcut: 'Q', run: () => sendKeys('q') },
        { separator: true },
        { label: 'Show Before', run: clickId('ge-compare-btn') },
      ],
    },
    {
      id: 'image', label: 'Image', items: () => [
        { label: 'Adjustments', submenu: adjustmentItems(api) },
        { separator: true },
        ...legacyMenuItems(legacy('ge-image-menu')),
        { separator: true },
        { label: 'Crop', shortcut: 'C', run: tool('crop') },
      ],
    },
    {
      id: 'layer', label: 'Layer', items: () => [
        { label: 'New Layer', shortcut: `${alt}${mod}J`, run: () => sendKeys('j', { ctrl: true, alt: true }) },
        { label: 'Duplicate Layer', shortcut: `${mod}J`, run: () => sendKeys('j', { ctrl: true }) },
        { label: 'Delete Layer', run: clickId('ge-selected-delete') },
        { separator: true },
        { label: 'New Adjustment Layer', submenu: adjustmentItems(api) },
        { label: 'Group Layers', run: clickId('ge-group-selected') },
        { label: 'Clipping Mask', shortcut: `${alt}${mod}G`, run: () => sendKeys('g', { ctrl: true, alt: true }) },
        { separator: true },
        { label: 'Merge Down', run: clickId('ge-merge-down') },
        { label: 'Merge All', run: clickId('ge-merge-all') },
        { label: 'Flatten Copy', run: clickId('ge-flatten') },
        { separator: true },
        { label: 'Select All Layers', run: clickId('ge-select-all-layers') },
        { label: 'Show/Hide Selected', run: clickId('ge-selected-visibility') },
        { label: 'Lock/Unlock Selected', run: clickId('ge-selected-lock') },
        { label: 'Align & Distribute…', run: clickId('ge-selected-align') },
      ],
    },
    {
      id: 'select', label: 'Select', items: () => {
        api.renderSelectionMenu?.();
        const menu = legacy('ge-selection-menu');
        const items = legacyMenuItems(menu);
        items.push({ separator: true }, { label: 'Quick Mask', shortcut: 'Q', run: () => sendKeys('q') });
        const saveBtn = menu?.querySelector('[data-selection-action="save"]');
        const nameInput = document.getElementById('ge-selection-name');
        items.push({ separator: true }, { header: 'Saved selections' });
        items.push({
          render: () => {
            const row = el('form', 'oe-menu-form');
            const input = el('input', 'oe-menu-input');
            input.type = 'text';
            input.maxLength = 100;
            input.placeholder = 'Name this selection';
            input.disabled = !!saveBtn?.disabled;
            const button = el('button', 'oe-menu-mini-btn', 'Save');
            button.type = 'submit';
            button.disabled = !!saveBtn?.disabled;
            row.append(input, button);
            row.addEventListener('submit', (event) => {
              event.preventDefault();
              if (!saveBtn || saveBtn.disabled) return;
              if (nameInput) nameInput.value = input.value;
              saveBtn.click();
              closeAll();
            });
            return row;
          },
        });
        const rows = menu?.querySelectorAll('#ge-saved-selection-list .ge-saved-selection-row') || [];
        if (!rows.length) items.push({ label: 'None saved', disabled: true });
        for (const row of rows) {
          const load = row.querySelector('[data-selection-action="load"]');
          const del = row.querySelector('[data-selection-action="delete"]');
          items.push({ label: textOf(load), run: () => load?.click(), trailing: del ? { label: '×', title: `Delete ${textOf(load)}`, run: () => del.click() } : null });
        }
        return items;
      },
    },
    { id: 'filter', label: 'Filter', items: () => legacyMenuItems(legacy('ge-filter-menu')) },
    {
      id: 'view', label: 'View', items: () => {
        const menu = legacy('ge-view-menu');
        const items = [
          { label: 'Zoom In', run: clickId('ge-zoom-in') },
          { label: 'Zoom Out', run: clickId('ge-zoom-out') },
          { label: 'Fit / Actual Size', run: clickId('ge-zoom-100') },
          { separator: true },
        ];
        for (const item of legacyMenuItems(menu)) items.push(item);
        const grid = document.getElementById('ge-grid-size');
        if (grid) {
          // Upstream's grid size field sits between the checkboxes.
          const at = items.findIndex(i => i.label === 'Grid') + 1 || items.length;
          items.splice(at, 0, {
            render: () => {
              const row = el('label', 'oe-menu-form');
              row.append(el('span', 'oe-menu-form-label', 'Grid size'));
              const input = el('input', 'oe-menu-input oe-menu-input-num');
              input.type = 'number';
              input.min = grid.min; input.max = grid.max; input.step = grid.step;
              input.value = grid.value;
              input.addEventListener('change', () => {
                grid.value = input.value;
                grid.dispatchEvent(new Event('input', { bubbles: true }));
                grid.dispatchEvent(new Event('change', { bubbles: true }));
              });
              row.append(input, el('span', 'oe-menu-form-label', 'px'));
              return row;
            },
          });
        }
        return items;
      },
    },
    {
      id: 'help', label: 'Help', items: () => [
        { label: 'Keyboard Shortcuts', shortcut: '?', run: clickId('ge-shortcuts-btn') },
        { label: 'Quick Edit', run: () => {
          const box = document.getElementById('ge-ai-command');
          if (box?.classList.contains('ge-ai-command-collapsed')) document.getElementById('ge-ai-command-toggle')?.click();
          else document.getElementById('ge-ai-command-input')?.focus();
        } },
        { label: 'AI Setup…', run: () => host.showSetupHelp('llm') },
        { separator: true },
        { label: 'About Odysseus-Edit', run: showAbout },
      ],
    },
  ];
}

function showAbout() {
  const overlay = el('div', 'oe-dialog-overlay');
  overlay.innerHTML = `
    <div class="oe-dialog" role="dialog" aria-modal="true" aria-labelledby="oe-about-title">
      <h2 class="oe-dialog-title" id="oe-about-title">Odysseus-Edit</h2>
      <p class="oe-dialog-msg">A layered image editor for the browser, extracted from Odysseus.
Free software under the GNU AGPL v3 or later.</p>
      <p class="oe-dialog-msg"><a href="https://github.com/odysseus-dev/odysseus" target="_blank" rel="noopener">Odysseus project</a> · unofficial, not affiliated.</p>
      <div class="oe-dialog-actions"><button type="button" class="oe-btn oe-btn-primary">Close</button></div>
    </div>`;
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey, true); };
  const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
  overlay.addEventListener('click', (e) => { if (e.target === overlay || e.target.closest('.oe-btn')) close(); });
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(overlay);
  overlay.querySelector('.oe-btn').focus();
}

// The single open menu (and submenu) across the menu bar.
let openState = null;

// Menu keyboard handling. Registered at import time on window/capture so it
// runs before the editor's own window-capture Escape guard (galleryEditor.js
// imports this module before its body runs); while a menu is open, keys stay
// in the menu instead of reaching tool shortcuts.
let menuKeyHandler = null;
window.addEventListener('keydown', (e) => {
  if (!openState || !menuKeyHandler) return;
  menuKeyHandler(e);
  if (!e.target?.matches?.('input')) e.stopImmediatePropagation();
}, true);

function closeAll() {
  if (!openState) return;
  openState.panel.remove();
  openState.sub?.remove();
  openState.title.classList.remove('active');
  openState.title.setAttribute('aria-expanded', 'false');
  openState = null;
}

function placePanel(panel, anchorRect, { side = 'below' } = {}) {
  panel.style.visibility = 'hidden';
  document.body.appendChild(panel);
  const w = panel.offsetWidth;
  const h = panel.offsetHeight;
  let left;
  let top;
  if (side === 'below') {
    left = anchorRect.left;
    top = anchorRect.bottom + 2;
  } else {
    left = anchorRect.right - 2;
    top = anchorRect.top - 5;
    if (left + w > window.innerWidth - 4) left = anchorRect.left - w + 2;
  }
  left = Math.max(4, Math.min(left, window.innerWidth - w - 4));
  top = Math.max(4, Math.min(top, window.innerHeight - h - 4));
  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
  panel.style.visibility = '';
}

function renderPanel(items, { onSubmenu } = {}) {
  const panel = el('div', 'oe-menu');
  panel.setAttribute('role', 'menu');
  for (const item of items) {
    if (item.separator) {
      if (panel.lastElementChild && !panel.lastElementChild.classList.contains('oe-menu-sep')) {
        panel.appendChild(el('div', 'oe-menu-sep'));
      }
      continue;
    }
    if (item.header) {
      panel.appendChild(el('div', 'oe-menu-header', item.header));
      continue;
    }
    if (item.render) {
      panel.appendChild(item.render());
      continue;
    }
    const row = el('button', 'oe-menu-item');
    row.type = 'button';
    row.setAttribute('role', item.checked === undefined ? 'menuitem' : 'menuitemcheckbox');
    if (item.checked !== undefined) row.setAttribute('aria-checked', item.checked ? 'true' : 'false');
    row.disabled = !!item.disabled;
    row.append(el('span', 'oe-menu-check', item.checked ? '✓' : ''), el('span', 'oe-menu-label', item.label));
    if (item.submenu) {
      row.classList.add('oe-has-sub');
      row.setAttribute('aria-haspopup', 'menu');
      row.append(el('span', 'oe-menu-arrow', '▸'));
      row.addEventListener('pointerenter', () => onSubmenu?.(row, item.submenu));
      row.addEventListener('click', (e) => { e.stopPropagation(); onSubmenu?.(row, item.submenu, { focus: true }); });
    } else {
      row.append(el('span', 'oe-menu-shortcut', item.shortcut || ''));
      row.addEventListener('pointerenter', () => onSubmenu?.(null));
      row.addEventListener('click', () => {
        closeAll();
        item.run?.();
      });
    }
    if (item.trailing) {
      const wrap = el('div', 'oe-menu-row');
      const trailing = el('button', 'oe-menu-trailing', item.trailing.label);
      trailing.type = 'button';
      trailing.title = item.trailing.title || '';
      trailing.addEventListener('click', (e) => {
        e.stopPropagation();
        item.trailing.run();
        closeAll();
      });
      wrap.append(row, trailing);
      panel.appendChild(wrap);
    } else {
      panel.appendChild(row);
    }
  }
  if (panel.lastElementChild?.classList.contains('oe-menu-sep')) panel.lastElementChild.remove();
  return panel;
}

function focusableItems(panel) {
  return [...panel.querySelectorAll('.oe-menu-item:not(:disabled)')];
}

function openMenu(menu, title, { focusFirst = false } = {}) {
  closeAll();
  const panel = renderPanel(menu.items(), {
    onSubmenu: (row, submenu, opts = {}) => {
      openState?.sub?.remove();
      if (openState) openState.sub = null;
      panel.querySelectorAll('.oe-menu-item.sub-open').forEach(r => r.classList.remove('sub-open'));
      if (!row || !submenu) return;
      const sub = renderPanel(submenu);
      sub.classList.add('oe-submenu');
      row.classList.add('sub-open');
      placePanel(sub, row.getBoundingClientRect(), { side: 'right' });
      openState.sub = sub;
      if (opts.focus) focusableItems(sub)[0]?.focus();
    },
  });
  placePanel(panel, title.getBoundingClientRect());
  title.classList.add('active');
  title.setAttribute('aria-expanded', 'true');
  openState = { menu, title, panel, sub: null };
  if (focusFirst) focusableItems(panel)[0]?.focus();
}

function mountMenubar(menubar, { api, legacy }) {
  const menus = buildMenus({ api, legacy });
  const titles = [];
  for (const menu of menus) {
    const title = el('button', 'oe-menu-title', menu.label);
    title.type = 'button';
    title.setAttribute('aria-haspopup', 'menu');
    title.setAttribute('aria-expanded', 'false');
    title.dataset.menu = menu.id;
    title.addEventListener('click', (e) => {
      e.stopPropagation();
      // A hover just switched to this menu; the click that follows should
      // not immediately close it again.
      if (openState?.title === title && !openState.viaHover) closeAll();
      else if (openState?.title === title) openState.viaHover = false;
      else openMenu(menu, title);
    });
    title.addEventListener('pointerenter', () => {
      if (openState && openState.title !== title) {
        openMenu(menu, title);
        openState.viaHover = true;
      }
    });
    title.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMenu(menu, title, { focusFirst: true });
      }
    });
    titles.push({ title, menu });
    menubar.appendChild(title);
  }

  const onPointerDown = (e) => {
    if (!openState) return;
    if (openState.panel.contains(e.target) || openState.sub?.contains(e.target)) return;
    if (titles.some(t => t.title === e.target || t.title.contains(e.target))) return;
    closeAll();
  };
  const onKeyDown = (e) => {
    if (!openState) return;
    const inSub = openState.sub?.contains(document.activeElement);
    const panel = inSub ? openState.sub : openState.panel;
    const items = focusableItems(panel);
    const index = items.indexOf(document.activeElement);
    const step = (delta) => {
      const i = titles.findIndex(t => t.title === openState.title);
      const next = titles[(i + delta + titles.length) % titles.length];
      openMenu(next.menu, next.title, { focusFirst: true });
    };
    if (e.target.matches?.('input')) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeAll(); }
      return;
    }
    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        e.stopPropagation();
        if (inSub) { openState.sub.remove(); openState.sub = null; openState.panel.querySelector('.sub-open')?.focus(); }
        else { const title = openState.title; closeAll(); title.focus(); }
        break;
      case 'ArrowDown':
      case 'ArrowUp': {
        e.preventDefault();
        e.stopPropagation();
        if (!items.length) break;
        const delta = e.key === 'ArrowDown' ? 1 : -1;
        items[(index + delta + items.length) % items.length].focus();
        break;
      }
      case 'ArrowRight':
        e.preventDefault();
        e.stopPropagation();
        if (!inSub && document.activeElement?.classList.contains('oe-has-sub')) document.activeElement.click();
        else step(1);
        break;
      case 'ArrowLeft':
        e.preventDefault();
        e.stopPropagation();
        if (inSub) { openState.sub.remove(); openState.sub = null; openState.panel.querySelector('.sub-open')?.focus(); }
        else step(-1);
        break;
      default:
        break;
    }
  };
  document.addEventListener('pointerdown', onPointerDown, true);
  menuKeyHandler = onKeyDown;
  window.addEventListener('resize', closeAll);
  return () => {
    closeAll();
    document.removeEventListener('pointerdown', onPointerDown, true);
    if (menuKeyHandler === onKeyDown) menuKeyHandler = null;
    window.removeEventListener('resize', closeAll);
  };
}

// ── Options bar ───────────────────────────────────────────────────────────

function sourceValueText(source, spec, root) {
  if (spec.value) return textOf(root.querySelector(spec.value)) || source.value;
  const row = source.closest('.ge-control-row, .ge-eraser-row');
  const label = row?.querySelector('label span, .ge-slider-value');
  return textOf(label) || source.value;
}

function fire(source, ...types) {
  for (const type of types) source.dispatchEvent(new Event(type, { bubbles: true }));
}

/** Build one mirrored control. Returns {node, sync} or null if the source is missing. */
function mirrorControl(spec, root) {
  const source = root.querySelector(spec.source);
  if (!source) return null;
  // Modifier class (oe-opt--range) must not collide with the input's own class.
  const field = el('label', `oe-opt oe-opt--${spec.kind}`);
  if (spec.kind !== 'toggle' && spec.kind !== 'button') field.append(el('span', 'oe-opt-label', spec.label));
  let sync = () => {};

  if (spec.kind === 'range') {
    const input = el('input', 'oe-opt-range');
    input.type = 'range';
    input.min = source.min; input.max = source.max; input.step = source.step || 1;
    const value = el('span', 'oe-opt-value');
    input.addEventListener('input', () => {
      source.value = input.value;
      fire(source, 'input');
      value.textContent = sourceValueText(source, spec, root);
    });
    input.addEventListener('change', () => fire(source, 'change'));
    input.addEventListener('pointerup', () => input.blur());
    field.append(input, value);
    sync = () => { input.value = source.value; value.textContent = sourceValueText(source, spec, root); };
  } else if (spec.kind === 'number') {
    const input = el('input', 'oe-opt-number');
    input.type = 'number';
    if (source.min) input.min = source.min;
    if (source.max) input.max = source.max;
    input.step = source.step || 1;
    input.readOnly = source.readOnly;
    if (source.readOnly) input.tabIndex = -1;
    const commit = () => { source.value = input.value; fire(source, 'input', 'change'); };
    input.addEventListener('change', commit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { commit(); input.blur(); }
      if (e.key === 'Escape') { sync(); input.blur(); }
    });
    field.append(input);
    sync = () => { if (document.activeElement !== input) input.value = source.value; };
  } else if (spec.kind === 'select') {
    const select = el('select', 'oe-opt-select');
    for (const option of source.options) select.append(new Option(option.textContent, option.value));
    select.addEventListener('change', () => { source.value = select.value; fire(source, 'input', 'change'); });
    field.append(select);
    sync = () => {
      if (select.options.length !== source.options.length) {
        select.innerHTML = '';
        for (const option of source.options) select.append(new Option(option.textContent, option.value));
      }
      select.value = source.value;
      select.disabled = source.disabled;
    };
  } else if (spec.kind === 'color') {
    const input = el('input', 'ge-color-picker oe-opt-color');
    input.type = 'color';
    input.value = source.value;
    input.addEventListener('input', () => { source.value = input.value; fire(source, 'input'); });
    input.addEventListener('change', () => { source.value = input.value; fire(source, 'change'); });
    field.append(input);
    try { attachColorPicker(input); } catch (_) { /* native picker fallback */ }
    sync = () => { if (input.value !== source.value) input.value = source.value; };
  } else if (spec.kind === 'toggle' || spec.kind === 'button') {
    const button = el('button', `oe-opt-btn${spec.kind === 'toggle' ? ' oe-opt-toggle' : ''}`, spec.label);
    button.type = 'button';
    if (spec.title) button.title = spec.title;
    if (spec.label === 'B') button.style.fontWeight = '700';
    if (spec.label === 'I') button.style.fontStyle = 'italic';
    button.addEventListener('click', (e) => { e.preventDefault(); source.click(); requestAnimationFrame(sync); });
    field.append(button);
    sync = () => {
      button.disabled = source.disabled;
      if (spec.kind === 'toggle') {
        const on = source.classList.contains('active') || source.getAttribute('aria-pressed') === 'true';
        button.classList.toggle('active', on);
        button.setAttribute('aria-pressed', on ? 'true' : 'false');
      }
    };
  }
  source.addEventListener('input', () => sync());
  source.addEventListener('change', () => sync());
  sync();
  return { node: field, sync };
}

function mountOptionsBar(bar, { container, api }) {
  const left = el('div', 'oe-options-tool');
  const options = el('div', 'oe-options-controls');
  const right = el('div', 'oe-options-right');
  bar.append(left, options, right);

  // Move upstream's own buttons here (ids and listeners intact) rather than
  // proxying them: History and the shortcuts popover position themselves
  // next to their button, so it has to be visible.
  for (const [id, icon, label] of [
    ['ge-undo', ICONS.undo, 'Undo'],
    ['ge-redo', ICONS.redo, 'Redo'],
    ['ge-history-btn', ICONS.history, 'History'],
    ['ge-compare-btn', ICONS.compare, 'Show before'],
    ['ge-shortcuts-btn', ICONS.keyboard, 'Keyboard shortcuts (?)'],
  ]) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.className = 'oe-opt-icon';
    btn.innerHTML = icon;
    btn.title = label;
    btn.setAttribute('aria-label', label);
    right.appendChild(btn);
  }

  let mirrors = [];
  let currentTool = null;

  const render = () => {
    const toolId = api.getTool();
    currentTool = toolId;
    const toolBtn = container.querySelector(`.ge-tool-btn[data-tool="${toolId}"]`);
    left.innerHTML = '';
    const icon = el('span', 'oe-options-icon');
    icon.innerHTML = toolBtn?.querySelector('.ge-tool-icon')?.innerHTML || '';
    left.append(icon, el('span', 'oe-options-name', textOf(toolBtn?.querySelector('.ge-tool-label')) || toolId));
    options.innerHTML = '';
    mirrors = [];
    for (const spec of TOOL_OPTIONS[toolId] || []) {
      const mirror = mirrorControl(spec, container);
      if (!mirror) continue;
      mirrors.push(mirror);
      options.appendChild(mirror.node);
    }
    if (HINTS[toolId]) options.appendChild(el('span', 'oe-options-hint', HINTS[toolId]));
  };

  const syncAll = () => {
    if (api.getTool() !== currentTool) { render(); return; }
    for (const mirror of mirrors) mirror.sync();
  };

  let frame = 0;
  const scheduleSync = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; syncAll(); });
  };
  let pointerDown = false;
  const onDown = () => { pointerDown = true; };
  const onUp = () => { pointerDown = false; scheduleSync(); };
  const onMove = () => { if (pointerDown) scheduleSync(); };
  document.addEventListener('pointerdown', onDown, true);
  document.addEventListener('pointerup', onUp, true);
  document.addEventListener('pointermove', onMove, true);
  document.addEventListener('keyup', scheduleSync, true);

  // Tool switches show up as class changes on the toolbar buttons.
  const toolbar = container.querySelector('.ge-toolbar');
  const observer = new MutationObserver(scheduleSync);
  if (toolbar) observer.observe(toolbar, { subtree: true, attributes: true, attributeFilter: ['class'] });

  render();
  syncAll();
  return () => {
    observer.disconnect();
    document.removeEventListener('pointerdown', onDown, true);
    document.removeEventListener('pointerup', onUp, true);
    document.removeEventListener('pointermove', onMove, true);
    document.removeEventListener('keyup', scheduleSync, true);
  };
}

// ── Save status ───────────────────────────────────────────────────────────

const SAVE_TEXT = {
  saved: 'Saved', saving: 'Saving…', pending: 'Unsaved', error: 'Save failed',
};

function mountSaveStatus(slot) {
  const status = el('span', 'oe-save-status');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  slot.appendChild(status);
  const sync = () => {
    const src = document.getElementById('ge-draft-status');
    const state = src?.dataset.state || (textOf(src) === 'Saved' ? 'saved' : 'pending');
    status.dataset.state = state;
    status.textContent = SAVE_TEXT[state] || textOf(src) || 'Unsaved';
    status.title = 'Drafts save automatically in this browser';
  };
  const saveBtn = document.getElementById('ge-save-menu-btn');
  const observer = new MutationObserver(sync);
  if (saveBtn) observer.observe(saveBtn, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['data-state'] });
  sync();
  return () => observer.disconnect();
}

// ── Mount ─────────────────────────────────────────────────────────────────

let disposeCurrent = null;

/** Remove the chrome's listeners (the editor is closing). */
export function unmountEditorChrome() {
  disposeCurrent?.();
}

/**
 * Build the menu bar and options bar above the editor and hide upstream's
 * top bar (it keeps working underneath). Call after the editor DOM is built.
 *
 * api: {getTool(), addAdjustmentLayer(type), renderSelectionMenu(), getDocumentName()}
 */
export function mountEditorChrome({ container, legacyTopbar, api }) {
  disposeCurrent?.();
  if (!container || !legacyTopbar) return;
  const root = el('div', 'oe-topbar');
  const menubar = el('div', 'oe-menubar');
  menubar.setAttribute('role', 'menubar');
  const optionsbar = el('div', 'oe-optionsbar');
  root.append(menubar, optionsbar);
  legacyTopbar.before(root);
  legacyTopbar.classList.add('oe-legacy-topbar');

  // Home keeps its id: the start page listens for clicks on #oe-home-btn.
  const home = legacyTopbar.querySelector('#oe-home-btn') || el('button');
  home.className = 'oe-app-btn';
  home.id = 'oe-home-btn';
  home.type = 'button';
  home.innerHTML = ICONS.home;
  home.title = 'Odysseus-Edit · back to start (your work is kept as a draft)';
  home.setAttribute('aria-label', 'Home');
  menubar.appendChild(home);

  const legacy = (id) => document.getElementById(id);
  const cleanups = [mountMenubar(menubar, { api, legacy })];

  menubar.appendChild(el('span', 'oe-menubar-spacer'));
  const docName = el('span', 'oe-doc-name', api.getDocumentName?.() || '');
  docName.title = docName.textContent;
  menubar.appendChild(docName);
  cleanups.push(mountSaveStatus(menubar));
  cleanups.push(mountOptionsBar(optionsbar, { container, api }));

  disposeCurrent = () => {
    cleanups.forEach(fn => fn());
    disposeCurrent = null;
  };
}
