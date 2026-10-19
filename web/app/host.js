/**
 * Host adapter: everything the editor needs from its surroundings.
 *
 * Inside Odysseus the editor talked to the workspace server (gallery,
 * server-side drafts, Cookbook). Odysseus-Edit is a standalone web app, so
 * the editor calls this module instead:
 *
 *   drafts       autosaved layered documents, kept in IndexedDB
 *   images       recently opened images (Import → Recent, Save to original)
 *   save*        write to the user's disk (File System Access API when the
 *                browser has it, otherwise a download)
 *   capabilities what the optional AI server offers, or null when it is not
 *                running (the page then hides AI tools)
 *   showSetupHelp  explains how to enable a missing AI feature
 */

import * as db from './db.js';
import { showSetupHelp } from './help.js';

const FORMAT_META = {
  png: { mime: 'image/png', ext: 'png', label: 'PNG image' },
  jpeg: { mime: 'image/jpeg', ext: 'jpg', label: 'JPEG image' },
  webp: { mime: 'image/webp', ext: 'webp', label: 'WebP image' },
};
const MIME_TO_FORMAT = { 'image/png': 'png', 'image/jpeg': 'jpeg', 'image/webp': 'webp' };

// ── Capabilities ──────────────────────────────────────────────────────────

let _capsPromise = null;

/** Resolves to the server's /api/capabilities JSON, or null without a server. */
export function getCapabilities({ refresh = false } = {}) {
  if (_capsPromise && !refresh) return _capsPromise;
  _capsPromise = (async () => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);
    try {
      const res = await fetch('/api/capabilities', { signal: ctrl.signal, headers: { Accept: 'application/json' } });
      if (!res.ok) return null;
      const type = res.headers.get('content-type') || '';
      if (!type.includes('json')) return null;
      return await res.json();
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  })();
  return _capsPromise;
}

// ── Saving to disk ────────────────────────────────────────────────────────

function hasSavePicker() {
  return typeof window.showSaveFilePicker === 'function';
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function writeToHandle(handle, blob) {
  const writable = await handle.createWritable();
  try {
    await writable.write(blob);
  } finally {
    await writable.close();
  }
}

/**
 * Save a new file. `encode(mime, quality)` produces the Blob; it runs after
 * the save picker so the picker still has the click's user activation.
 * Returns {mode: 'saved' | 'downloaded' | 'cancelled', blob, name}.
 */
export async function saveCopy({ filename = 'image', format = 'png', encode }) {
  const meta = FORMAT_META[format] || FORMAT_META.png;
  const name = `${filename}.${meta.ext}`;
  if (hasSavePicker()) {
    let handle = null;
    try {
      handle = await window.showSaveFilePicker({
        suggestedName: name,
        types: [{ description: meta.label, accept: { [meta.mime]: [`.${meta.ext}`] } }],
      });
    } catch (err) {
      if (err?.name === 'AbortError') return { mode: 'cancelled', blob: null, name };
      handle = null; // e.g. activation expired: fall back to a download
    }
    if (handle) {
      const blob = await encode(meta.mime);
      await writeToHandle(handle, blob);
      return { mode: 'saved', blob, name: handle.name || name };
    }
  }
  const blob = await encode(meta.mime);
  downloadBlob(blob, name);
  return { mode: 'downloaded', blob, name };
}

/**
 * Overwrite the file an image was opened from. Needs a stored file handle
 * and write permission; otherwise falls back to saveCopy so Ctrl+S always
 * produces a file. `encode(mime, quality)` must encode in the given format.
 */
export async function saveOverOriginal({ imageId, fallbackName = 'image', encode }) {
  const record = await db.getImage(imageId).catch(() => null);
  const handle = record?.handle;
  const format = MIME_TO_FORMAT[record?.type] || null;
  if (handle && format && typeof handle.createWritable === 'function') {
    let permission = 'denied';
    try {
      permission = await handle.queryPermission({ mode: 'readwrite' });
      if (permission !== 'granted') permission = await handle.requestPermission({ mode: 'readwrite' });
    } catch {
      permission = 'denied';
    }
    if (permission === 'granted') {
      const blob = await encode(FORMAT_META[format].mime, format === 'png' ? undefined : 0.92);
      await writeToHandle(handle, blob);
      return { mode: 'overwritten', blob, name: handle.name };
    }
  }
  const base = String(record?.name || fallbackName).replace(/\.[^.]+$/, '') || 'image';
  return saveCopy({ filename: base, format: format || 'png', encode });
}

// ── Recent images ─────────────────────────────────────────────────────────

const _imageUrls = new Map();

function urlForRecord(record) {
  if (!_imageUrls.has(record.id)) _imageUrls.set(record.id, URL.createObjectURL(record.blob));
  return _imageUrls.get(record.id);
}

export async function listRecentImages(limit) {
  const records = await db.listImages(limit);
  return records.map(r => ({ id: r.id, name: r.name, url: urlForRecord(r), size: r.size, type: r.type }));
}

export async function rememberImage({ blob, name, handle }) {
  // A File from a picker or drop still points at the file on disk; once
  // that file changes, Chromium refuses to read it. Keep our own copy.
  const copy = new Blob([await blob.arrayBuffer()], { type: blob.type || 'image/png' });
  const record = await db.addImage({ blob: copy, name, handle });
  return { id: record.id, name: record.name, url: urlForRecord(record) };
}

export const host = {
  drafts: {
    save: (id, body) => db.saveDraft(id, body),
    get: id => db.getDraft(id),
    list: () => db.listDrafts(),
    findBySource: imageId => db.findDraftBySource(imageId),
    remove: id => db.deleteDraft(id),
    rename: (id, name) => db.renameDraft(id, name),
  },
  images: {
    list: listRecentImages,
    remember: rememberImage,
  },
  saveCopy,
  saveOverOriginal,
  getCapabilities,
  showSetupHelp,
};

export default host;
