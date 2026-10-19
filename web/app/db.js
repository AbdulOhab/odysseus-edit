/**
 * Local persistence for Odysseus-Edit, backed by IndexedDB.
 *
 * Stores:
 *   draft_meta      {id, name, source_image_id, width, height, thumbnail,
 *                    created_at, updated_at}: the list view, no pixels
 *   draft_payloads  {id, payload}: the layered document the editor restores
 *   images          {id, name, type, size, blob, handle, added_at}: recently
 *                   opened images, used by "Import → Recent" and by Save to
 *                   write back to the original file
 *
 * Drafts are split in two stores so listing them never loads every layer
 * of every document into memory.
 */

const DB_NAME = 'odysseus-edit';
const DB_VERSION = 1;
const MAX_RECENT_IMAGES = 24;

let _dbPromise = null;

function openDb() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    if (!('indexedDB' in globalThis)) {
      reject(new Error('This browser has no IndexedDB, so drafts cannot be saved'));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('draft_meta')) {
        const meta = db.createObjectStore('draft_meta', { keyPath: 'id' });
        meta.createIndex('source_image_id', 'source_image_id');
        meta.createIndex('updated_at', 'updated_at');
      }
      if (!db.objectStoreNames.contains('draft_payloads')) {
        db.createObjectStore('draft_payloads', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('images')) {
        const images = db.createObjectStore('images', { keyPath: 'id' });
        images.createIndex('added_at', 'added_at');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('Could not open IndexedDB'));
    req.onblocked = () => reject(new Error('IndexedDB is blocked by another open tab'));
  });
  // A failed open should be retried on the next call rather than cached.
  _dbPromise.catch(() => { _dbPromise = null; });
  return _dbPromise;
}

function done(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error || new Error('Transaction aborted'));
    tx.onerror = () => reject(tx.error || new Error('Transaction failed'));
  });
}

function request(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export function newId(prefix = '') {
  const raw = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return prefix + raw;
}

// ── Drafts ────────────────────────────────────────────────────────────────

/**
 * Create or update a draft. `body` has the shape the editor builds:
 * {name, source_image_id, width, height, payload, thumbnail}.
 * Returns the stored summary (with id).
 */
export async function saveDraft(id, body) {
  const db = await openDb();
  const now = new Date().toISOString();
  const tx = db.transaction(['draft_meta', 'draft_payloads'], 'readwrite');
  const metaStore = tx.objectStore('draft_meta');
  const existing = id ? await request(metaStore.get(id)) : null;
  const draftId = existing ? existing.id : (id || newId('draft-'));
  const meta = {
    id: draftId,
    name: body.name || existing?.name || 'Untitled',
    source_image_id: body.source_image_id ?? existing?.source_image_id ?? null,
    width: body.width ?? existing?.width ?? null,
    height: body.height ?? existing?.height ?? null,
    thumbnail: body.thumbnail ?? existing?.thumbnail ?? null,
    created_at: existing?.created_at || now,
    updated_at: now,
  };
  metaStore.put(meta);
  if (body.payload) tx.objectStore('draft_payloads').put({ id: draftId, payload: body.payload });
  await done(tx);
  return meta;
}

export async function getDraft(id) {
  if (!id) return null;
  const db = await openDb();
  const tx = db.transaction(['draft_meta', 'draft_payloads'], 'readonly');
  const [meta, payloadRow] = await Promise.all([
    request(tx.objectStore('draft_meta').get(id)),
    request(tx.objectStore('draft_payloads').get(id)),
  ]);
  if (!meta) return null;
  return { ...meta, payload: payloadRow?.payload || null };
}

/** Draft summaries, most recently updated first. */
export async function listDrafts() {
  const db = await openDb();
  const tx = db.transaction('draft_meta', 'readonly');
  const all = await request(tx.objectStore('draft_meta').getAll());
  return all.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
}

export async function findDraftBySource(sourceImageId) {
  if (!sourceImageId) return null;
  const db = await openDb();
  const tx = db.transaction('draft_meta', 'readonly');
  const matches = await request(tx.objectStore('draft_meta').index('source_image_id').getAll(sourceImageId));
  if (!matches.length) return null;
  matches.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  return getDraft(matches[0].id);
}

export async function deleteDraft(id) {
  if (!id) return;
  const db = await openDb();
  const tx = db.transaction(['draft_meta', 'draft_payloads'], 'readwrite');
  tx.objectStore('draft_meta').delete(id);
  tx.objectStore('draft_payloads').delete(id);
  await done(tx);
}

export async function renameDraft(id, name) {
  const db = await openDb();
  const tx = db.transaction('draft_meta', 'readwrite');
  const store = tx.objectStore('draft_meta');
  const meta = await request(store.get(id));
  if (meta) store.put({ ...meta, name: String(name || 'Untitled') });
  await done(tx);
}

// ── Recent images ─────────────────────────────────────────────────────────

/**
 * Remember an opened image. `handle` is an optional FileSystemFileHandle
 * (Chromium) that lets Save write back to the original file.
 */
export async function addImage({ blob, name, handle = null }) {
  const db = await openDb();
  const record = {
    id: newId('img-'),
    name: name || 'Image',
    type: blob.type || 'image/png',
    size: blob.size,
    blob,
    handle,
    added_at: new Date().toISOString(),
  };
  const tx = db.transaction('images', 'readwrite');
  const store = tx.objectStore('images');
  try {
    store.put(record);
  } catch (err) {
    // Some browsers cannot structured-clone file handles; keep the image.
    if (!handle) throw err;
    record.handle = null;
    store.put(record);
  }
  await done(tx);
  pruneImages().catch(() => {});
  return record;
}

export async function getImage(id) {
  if (!id) return null;
  const db = await openDb();
  const tx = db.transaction('images', 'readonly');
  return (await request(tx.objectStore('images').get(id))) || null;
}

export async function listImages(limit = MAX_RECENT_IMAGES) {
  const db = await openDb();
  const tx = db.transaction('images', 'readonly');
  const all = await request(tx.objectStore('images').getAll());
  all.sort((a, b) => String(b.added_at).localeCompare(String(a.added_at)));
  return all.slice(0, limit);
}

async function pruneImages() {
  const db = await openDb();
  const tx = db.transaction('images', 'readwrite');
  const store = tx.objectStore('images');
  const keys = await request(store.index('added_at').getAllKeys());
  // getAllKeys on the index returns primary keys ordered by added_at.
  const excess = keys.length - MAX_RECENT_IMAGES;
  for (let i = 0; i < excess; i += 1) store.delete(keys[i]);
  await done(tx);
}

/** Ask the browser not to evict our data under storage pressure. */
export async function requestPersistence() {
  try {
    if (navigator.storage?.persisted && await navigator.storage.persisted()) return true;
    return !!(await navigator.storage?.persist?.());
  } catch {
    return false;
  }
}
