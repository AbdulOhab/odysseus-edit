/**
 * Image-import wiring — covers all four entry points that drop an
 * image as a new layer:
 *
 *   #ge-import-topbar    topbar "+ Import" button
 *   #ge-import-file      File button in the Import section
 *   #ge-import-paste     Clipboard button (uses async clipboard API)
 *   #ge-import-gallery   Recent-images picker — lists images opened earlier
 *                        (host.images) and shows a thumbnail grid overlay
 *
 * Plus the shared `handleImportedImage(img)` sink — scales to canvas,
 * centres, creates a new layer, switches to Move tool, hides the
 * import section, refreshes the panel. Returned so the drag-and-drop
 * + paste paths (wired in editor/clipboard-and-drop.js) can use the
 * same sink.
 *
 * @param {{
 *   container:        HTMLElement,
 *   saveState:        (label?: string) => void,
 *   createLayer:      (name, w, h) => object,
 *   composite:        () => void,
 *   renderLayerPanel: () => void,
 *   uiModule:         object,
 * }} deps
 *
 * @returns {{ handleImportedImage: (img: HTMLImageElement) => void }}
 *
 * Modified for Odysseus-Edit (2026-10-19): the Gallery picker lists recent
 * images from ../../app/host.js instead of /api/gallery/library, and the
 * topbar Import button opens a File / Clipboard / Recent menu. See NOTICE.md.
 */
import { state } from './state.js';
import { host } from '../../app/host.js';
import {
  createPlacedData,
  initialPlacedMatrix,
  renderPlacedLayer,
} from './placed-layer.js';

export function wireImport({ container, saveState, createLayer, composite, renderLayerPanel, uiModule }) {
  // Hidden <input type="file"> the topbar + File buttons both click.
  const importFileInput = document.createElement('input');
  importFileInput.type = 'file';
  importFileInput.accept = 'image/*';
  importFileInput.style.display = 'none';
  container.appendChild(importFileInput);

  function handleImportedImage(img, sourceName = 'Imported image') {
    if (!state.editorOpen) return;
    saveState('Import image');
    // Scale down if larger than canvas.
    let w = img.naturalWidth || img.width;
    let h = img.naturalHeight || img.height;
    if (w > state.imgWidth || h > state.imgHeight) {
      const scale = Math.min(state.imgWidth / w, state.imgHeight / h);
      w = Math.round(w * scale);
      h = Math.round(h * scale);
    }
    const source = document.createElement('canvas');
    source.width = img.naturalWidth || img.width;
    source.height = img.naturalHeight || img.height;
    source.getContext('2d').drawImage(img, 0, 0);
    const layer = createLayer('Imported', w, h);
    // Centre on the canvas.
    const ox = Math.round((state.imgWidth - w) / 2);
    const oy = Math.round((state.imgHeight - h) / 2);
    layer.kind = 'placed';
    layer.placed = createPlacedData(
      source,
      initialPlacedMatrix(source.width, source.height, { x: ox, y: oy, width: w, height: h }),
      sourceName,
    );
    const rendered = renderPlacedLayer(layer);
    state.layerOffsets.set(layer.id, rendered.offset);
    state.layers.push(layer);
    state.activeLayerId = layer.id;
    // Switch to move tool so the imported layer is immediately
    // repositionable.
    state.tool = 'move';
    const tb = container.querySelector('.ge-toolbar');
    if (tb) tb.querySelectorAll('.ge-tool-btn').forEach(b => b.classList.toggle('active', b.dataset.tool === 'move'));
    // Hide the import section now that the import is done.
    const importSec = document.getElementById('ge-import-section');
    if (importSec) importSec.style.display = 'none';
    composite();
    renderLayerPanel();
    if (uiModule) uiModule.showToast('Image imported — drag to position');
  }

  importFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const img = new Image();
      img.onload = () => handleImportedImage(img, file.name || 'Imported image');
      img.src = ev.target.result;
    };
    reader.readAsDataURL(file);
    importFileInput.value = '';
  });

  // Topbar Import opens a small menu (File / Clipboard / Recent). Upstream
  // went straight to the file picker, which left Recent unreachable.
  {
    const importBtn = document.getElementById('ge-import-topbar');
    if (importBtn) {
      const menu = document.createElement('div');
      menu.className = 'ge-save-menu dropdown oe-import-menu';
      menu.id = 'oe-import-menu';
      menu.hidden = true;
      menu.setAttribute('role', 'menu');
      const icons = {
        file: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
        paste: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>',
        recent: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
      };
      const items = [
        ['file', 'From file…', () => importFileInput.click()],
        ['paste', 'From clipboard', () => document.getElementById('ge-import-paste')?.click()],
        ['recent', 'Recent images…', () => document.getElementById('ge-import-gallery')?.click()],
      ];
      for (const [key, label, run] of items) {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'dropdown-item-compact';
        item.setAttribute('role', 'menuitem');
        item.innerHTML = `<span class="dropdown-icon">${icons[key]}</span><span></span>`;
        item.lastElementChild.textContent = label;
        item.addEventListener('click', () => { setOpen(false); run(); });
        menu.appendChild(item);
      }
      importBtn.insertAdjacentElement('afterend', menu);
      function setOpen(open) {
        menu.hidden = !open;
        importBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (!open) return;
        const r = importBtn.getBoundingClientRect();
        menu.style.top = `${r.bottom + 2}px`;
        menu.style.right = `${Math.max(8, window.innerWidth - r.right)}px`;
        menu.style.left = 'auto';
        menu.querySelector('button')?.focus();
      }
      importBtn.setAttribute('aria-haspopup', 'menu');
      importBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setOpen(menu.hidden);
      });
      menu.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); importBtn.focus(); }
      });
      const onOutside = (e) => {
        // The editor rebuilds its DOM on every open; drop stale listeners.
        if (!menu.isConnected) { document.removeEventListener('pointerdown', onOutside, true); return; }
        if (!menu.hidden && !menu.contains(e.target) && !importBtn.contains(e.target)) setOpen(false);
      };
      document.addEventListener('pointerdown', onOutside, true);
    }
  }
  document.getElementById('ge-import-file')?.addEventListener('click', () => importFileInput.click());

  document.getElementById('ge-import-paste')?.addEventListener('click', async () => {
    const pasteFromKeyboard = () => {
      document.getElementById('gallery-editor-container')?.focus();
      uiModule?.showToast('Press Ctrl+V to paste the image');
    };
    if (!navigator.clipboard?.read) {
      pasteFromKeyboard();
      return;
    }
    try {
      const clipItems = await navigator.clipboard.read();
      let blob = null;
      for (const item of clipItems) {
        const imgType = item.types.find(t => t.startsWith('image/'));
        if (imgType) { blob = await item.getType(imgType); break; }
      }
      if (!blob) { if (uiModule) uiModule.showToast('No image found in clipboard'); return; }
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => { handleImportedImage(img); URL.revokeObjectURL(url); };
      img.onerror = () => { URL.revokeObjectURL(url); if (uiModule) uiModule.showToast('Failed to load clipboard image'); };
      img.src = url;
    } catch (e) {
      pasteFromKeyboard();
    }
  });

  // Import from recent images (kept in IndexedDB by the host) and show a
  // thumbnail-grid picker overlay.
  document.getElementById('ge-import-gallery')?.addEventListener('click', async () => {
    try {
      const items = await host.images.list(50);
      if (!items.length) { if (uiModule) uiModule.showToast('No recent images yet. Open one from the start page first.', 4000); return; }

      // Picker overlay.
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;';
      const panel = document.createElement('div');
      panel.style.cssText = 'background:var(--panel,#1e1e1e);border-radius:12px;padding:16px;max-width:500px;max-height:70vh;overflow-y:auto;width:90%;';
      panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;"><span style="font-size:13px;font-weight:600;">Recent images</span><button id="ge-gallery-close" style="background:none;border:none;color:var(--fg);cursor:pointer;font-size:18px;">✕</button></div>';
      const grid = document.createElement('div');
      grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:6px;';
      for (const item of items) {
        const thumb = document.createElement('img');
        thumb.src = item.url;
        thumb.style.cssText = 'width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;cursor:pointer;border:2px solid transparent;transition:border-color 0.15s;';
        thumb.addEventListener('mouseenter', () => { thumb.style.borderColor = 'var(--accent,#61afef)'; });
        thumb.addEventListener('mouseleave', () => { thumb.style.borderColor = 'transparent'; });
        thumb.addEventListener('click', () => {
          overlay.remove();
          const img = new Image();
          img.crossOrigin = 'anonymous';
          img.onload = () => handleImportedImage(img, item.name || 'Recent image');
          img.onerror = () => { if (uiModule) uiModule.showToast('Failed to load image'); };
          img.src = item.url;
        });
        grid.appendChild(thumb);
      }
      panel.appendChild(grid);
      overlay.appendChild(panel);
      document.body.appendChild(overlay);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
      panel.querySelector('#ge-gallery-close').addEventListener('click', () => overlay.remove());
    } catch (e) {
      if (uiModule) uiModule.showToast('Failed to load recent images: ' + e.message);
    }
  });

  return { handleImportedImage };
}
