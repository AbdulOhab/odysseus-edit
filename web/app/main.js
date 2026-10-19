/**
 * Odysseus-Edit start page: open an image, start a blank canvas, resume a
 * draft. Hands the document to the upstream editor (js/galleryEditor.js)
 * and takes over again when the user presses Home.
 */

import { host, getCapabilities } from './host.js';
import { getModelChoices } from './ai-planner.js';
import { requestPersistence } from './db.js';
import uiModule from '../js/ui.js';

const editorModule = import('../js/galleryEditor.js');

const $ = id => document.getElementById(id);
const landing = $('oe-landing');
const container = $('gallery-editor-container');
const fileInput = $('oe-file-input');

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp', 'image/avif', 'image/svg+xml'];

// ── Switching between the start page and the editor ─────────────────────

function showEditorShell() {
  landing.hidden = true;
  document.body.classList.add('oe-editing');
}

function showLanding() {
  container.style.display = 'none';
  landing.hidden = false;
  document.body.classList.remove('oe-editing');
  renderDrafts();
}

async function openEditor(...args) {
  const editor = await editorModule;
  if (editor.isEditorOpen()) await closeEditor();
  showEditorShell();
  return editor.openEditor(...args);
}

async function closeEditor() {
  const editor = await editorModule;
  if (!editor.isEditorOpen()) return;
  try {
    await editor.flushDraft();
  } catch (err) {
    console.warn('[odysseus-edit] final draft save failed', err);
  }
  editor.closeEditor({ force: true, persist: false });
}

async function goHome() {
  await closeEditor();
  showLanding();
}

// ── Opening files ───────────────────────────────────────────────────────

function isImageFile(file) {
  return !!file && (IMAGE_TYPES.includes(file.type) || /\.(png|jpe?g|webp|gif|bmp|avif|svg)$/i.test(file.name || ''));
}

async function openImageFile(file, handle = null) {
  if (!isImageFile(file)) {
    uiModule.showToast(`${file?.name || 'That file'} is not an image this editor can open`, 4000);
    return;
  }
  try {
    const image = await host.images.remember({ blob: file, name: file.name || 'Image', handle });
    await openEditor(image.url, image.id, null, image.name);
  } catch (err) {
    console.error('[odysseus-edit] open failed', err);
    uiModule.showError(`Could not open the image: ${err?.message || err}`);
    showLanding();
  }
}

async function pickImage() {
  if (typeof window.showOpenFilePicker === 'function') {
    try {
      const [handle] = await window.showOpenFilePicker({
        types: [{ description: 'Images', accept: { 'image/*': ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.avif', '.svg'] } }],
        excludeAcceptAllOption: false,
        multiple: false,
      });
      if (handle) await openImageFile(await handle.getFile(), handle);
      return;
    } catch (err) {
      if (err?.name === 'AbortError') return;
      // Fall through to the plain input (e.g. blocked in an iframe).
    }
  }
  fileInput.value = '';
  fileInput.click();
}

fileInput.addEventListener('change', () => {
  const file = fileInput.files?.[0];
  if (file) openImageFile(file);
});
$('oe-open-btn').addEventListener('click', pickImage);

// Drag and drop anywhere on the start page.
let dragDepth = 0;
landing.addEventListener('dragenter', e => {
  if (!e.dataTransfer?.types?.includes('Files')) return;
  e.preventDefault();
  dragDepth += 1;
  landing.classList.add('oe-drag-over');
});
landing.addEventListener('dragover', e => {
  if (e.dataTransfer?.types?.includes('Files')) e.preventDefault();
});
landing.addEventListener('dragleave', () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) landing.classList.remove('oe-drag-over');
});
landing.addEventListener('drop', async e => {
  e.preventDefault();
  dragDepth = 0;
  landing.classList.remove('oe-drag-over');
  const item = [...(e.dataTransfer?.items || [])].find(i => i.kind === 'file');
  if (!item) return;
  // Chromium can hand over a file handle, which lets Save write back.
  let handle = null;
  const handlePromise = typeof item.getAsFileSystemHandle === 'function' ? item.getAsFileSystemHandle() : null;
  const file = item.getAsFile();
  try { handle = handlePromise ? await handlePromise : null; } catch { handle = null; }
  if (handle && handle.kind !== 'file') handle = null;
  if (file) openImageFile(file, handle);
});

// Paste an image while the start page is showing (the editor handles its own).
window.addEventListener('paste', e => {
  if (landing.hidden) return;
  const file = [...(e.clipboardData?.files || [])].find(isImageFile);
  if (file) {
    e.preventDefault();
    openImageFile(new File([file], file.name || 'Pasted image.png', { type: file.type }));
  }
});

// ── New canvas ──────────────────────────────────────────────────────────

$('oe-presets').addEventListener('click', e => {
  const btn = e.target.closest('.oe-preset');
  if (!btn) return;
  if (btn.dataset.custom) {
    // The editor shows its own size prompt when no size is given.
    openEditor(null, null, null, 'New canvas');
    return;
  }
  const w = Number(btn.dataset.w);
  const h = Number(btn.dataset.h);
  const name = btn.querySelector('strong')?.textContent || 'New canvas';
  openEditor(null, null, { w, h }, `${name} ${w}×${h}`);
});

// Cancelling the custom-size prompt closes the editor; come back here.
window.addEventListener('gallery-editor-setup-cancelled', showLanding);

// Upstream refuses to close the editor unless its gallery tab allows it;
// there is no tab here. The editor also closes itself when a draft or
// image fails to load, so return to the start page whenever it hides.
window.__galleryAllowCloseEditor = true;
new MutationObserver(() => {
  if (container.style.display === 'none' && landing.hidden) showLanding();
}).observe(container, { attributes: true, attributeFilter: ['style'] });

// ── Drafts ──────────────────────────────────────────────────────────────

function timeAgo(iso) {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return '';
  const s = Math.round((Date.now() - then) / 1000);
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} d ago`;
  return new Date(then).toLocaleDateString();
}

async function renderDrafts() {
  const list = $('oe-drafts');
  let drafts = [];
  try {
    drafts = await host.drafts.list();
  } catch (err) {
    list.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'oe-empty';
    empty.textContent = `Drafts are unavailable: ${err?.message || err}`;
    list.appendChild(empty);
    return;
  }
  list.innerHTML = '';
  if (!drafts.length) {
    const empty = document.createElement('div');
    empty.className = 'oe-empty';
    empty.textContent = 'No drafts yet. Anything you edit is saved here automatically.';
    list.appendChild(empty);
    return;
  }
  for (const draft of drafts) {
    const card = document.createElement('article');
    card.className = 'oe-draft';
    card.innerHTML = `
      <button type="button" class="oe-draft-open">
        <div class="oe-draft-thumb"></div>
        <div class="oe-draft-meta">
          <span class="oe-draft-name"></span>
          <span class="oe-draft-info"></span>
        </div>
      </button>
      <button type="button" class="oe-draft-delete" title="Delete draft" aria-label="Delete draft">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>
      </button>`;
    const name = draft.name || 'Untitled';
    card.querySelector('.oe-draft-name').textContent = name;
    card.querySelector('.oe-draft-name').title = name;
    const size = draft.width && draft.height ? `${draft.width}×${draft.height} · ` : '';
    card.querySelector('.oe-draft-info').textContent = `${size}${timeAgo(draft.updated_at)}`;
    if (draft.thumbnail) {
      const img = document.createElement('img');
      img.src = draft.thumbnail;
      img.alt = '';
      img.loading = 'lazy';
      card.querySelector('.oe-draft-thumb').appendChild(img);
    }
    card.querySelector('.oe-draft-open').addEventListener('click', () => {
      const preset = draft.width && draft.height ? { w: draft.width, h: draft.height } : null;
      openEditor(null, draft.source_image_id || null, preset, name, draft.id);
    });
    card.querySelector('.oe-draft-delete').addEventListener('click', async () => {
      const ok = await uiModule.styledConfirm(`Delete "${name}"? This cannot be undone.`, {
        title: 'Delete draft', confirmText: 'Delete', danger: true,
      });
      if (!ok) return;
      await host.drafts.remove(draft.id);
      renderDrafts();
    });
    list.appendChild(card);
  }
}

// ── Home button inside the editor ───────────────────────────────────────

document.addEventListener('click', e => {
  if (e.target.closest?.('#oe-home-btn')) {
    e.preventDefault();
    goHome();
  }
});

// Save promptly when the tab is hidden or closed.
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState !== 'hidden') return;
  const editor = await editorModule;
  if (editor.isEditorOpen()) editor.flushDraft().catch(() => {});
});

// ── AI status + theme ───────────────────────────────────────────────────

async function refreshAiStatus() {
  const status = $('oe-ai-status');
  const text = status.querySelector('.oe-status-text');
  const caps = await getCapabilities();
  document.documentElement.classList.toggle('oe-ai-offline', !caps);
  if (!caps) {
    status.dataset.state = 'offline';
    text.textContent = 'AI server offline';
    status.title = 'The editor works fully; AI tools need the optional server. Click for setup.';
    status.onclick = () => host.showSetupHelp('server');
    return;
  }
  const models = await getModelChoices();
  const imageModels = (caps.image_providers || []).length;
  const parts = [models.length ? `Quick Edit: ${models[0].model}` : 'No language model', imageModels ? `${imageModels} image model${imageModels > 1 ? 's' : ''}` : 'no image model'];
  status.dataset.state = models.length && imageModels ? 'online' : 'partial';
  text.textContent = `AI ready · ${parts.join(' · ')}`;
  status.title = 'AI server connected. Click for setup notes.';
  status.onclick = () => host.showSetupHelp(models.length ? 'image-model' : 'llm');
}

$('oe-theme-toggle').addEventListener('click', () => {
  const light = document.documentElement.classList.toggle('light');
  try { localStorage.setItem('odysseus-edit.theme', light ? 'light' : 'dark'); } catch {}
});

// ── Start ───────────────────────────────────────────────────────────────

async function start() {
  requestPersistence();
  refreshAiStatus();
  // Reopen the document that was open before a reload.
  const editor = await editorModule;
  const session = editor.getActiveEditorSession();
  if (session?.draftId) {
    const draft = await host.drafts.get(session.draftId).catch(() => null);
    if (draft) {
      const preset = draft.width && draft.height ? { w: draft.width, h: draft.height } : null;
      await openEditor(null, draft.source_image_id || null, preset, draft.name || 'Untitled', draft.id);
      return;
    }
  }
  showLanding();
}

start();
