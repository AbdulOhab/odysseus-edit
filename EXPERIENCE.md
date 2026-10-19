# Experience log

Lessons, traps and decisions from building Odysseus-Edit. Read this before
changing code; add to it when something costs you time. Group by area.

## Extracting the editor from Odysseus

- **The editor was already modular enough to lift out.** `galleryEditor.js`
  plus `editor/` only import 7 files from the rest of Odysseus. Two of them are
  huge (`ui.js` 1,500 lines, `modalManager.js` 1,600 lines) but the editor uses
  three functions from one and four from the other, so small shims replace them
  and cut away theming, chat and the window manager.
- **First commit = pristine upstream.** Keeping the unmodified import as its own
  commit makes `git diff a36ad4d -- web/js` show every local change, which is
  what an upstream sync needs.
- **Mirror upstream paths** (`web/js/...` = odysseus `static/js/...`) so files
  can be diffed and re-synced one to one.
- **`?v=...` cache-busting queries** on imports were stripped. Without a build
  step, the server sends `Cache-Control: no-cache` instead.
- **AGPL section 5(a)** requires modified files to carry a notice saying so,
  with a date. Every modified upstream file has a "Modified for Odysseus-Edit"
  line in its header comment.

## CSS

- Upstream CSS is one big cascade split mechanically into numbered files; order
  matters (1,151 redeclarations, 1,886 `!important`). The editor needs rules
  from many of those files, not just `13-image-editor.css`.
- `tools/extract_css.py` keeps a rule when every class/id in one of its
  selectors appears in the editor's JS. That over-includes app areas whose
  names collide with JS identifiers (`msg`, `section`, `sidebar`), hence the
  `--deny` list in `tools/sync-upstream-css.sh`.
- **`:root.light button { background: #eaeaea }`** (specificity 0,2,1) comes
  along and greys every button in light mode. The editor keeps that look for
  fidelity; shell buttons in `app/app.css` use `html:root .oe-…` (also 0,2,1,
  later in the cascade) to win.
- `--accent`, `--fg-muted` and `--bg-secondary` are never defined upstream
  either; the editor falls back or inherits. Do not define them globally or the
  editor's look changes.

## Browser storage and files

- **Copy a `File` before storing it.** A `File` from a picker or a drop still
  points at the file on disk; after the file changes, Chromium refuses to read
  it and object URLs break. `host.rememberImage` stores
  `new Blob([await file.arrayBuffer()])`.
- Drafts are split into `draft_meta` and `draft_payloads` stores so the start
  page can list drafts without loading every layer of every document.
- File System Access API (`showSaveFilePicker`, handles) needs a fresh user
  activation. Open the picker first, encode the image after. Fallback is a
  plain download (Firefox, Safari).
- Upstream `closeEditor()` refuses to close unless `window.__galleryAllowCloseEditor`
  is set (it expected a gallery tab). The shell sets it and watches the
  container's `style` to return to the start page whenever the editor hides.

## Upstream quirks found

- The "Gallery" import button (now "Recent") lives in `#ge-import-section`,
  which only shows when `state.tool === 'import'`, and no toolbar button sets
  that tool. It is unreachable in upstream too.
- Quick Edit's "upscale" clicked `#ge-upscale-ai`, which no markup creates. AI
  upscale is now `runAiUpscale(scale, btn)` returned from `wireAIToolsMisc`.

## Testing with Playwright MCP

- Clicking a button that opens a file chooser puts the MCP session into a
  "modal state" and the rest of the script keeps running in the background,
  interfering with later steps. Use `page.setInputFiles('#oe-file-input', …)`
  on the hidden input instead.
- Session recovery reopens the last document on reload; clear
  `sessionStorage` and delete the `odysseus-edit` IndexedDB for a clean run.
- `style.cssText` serialises with spaces (`z-index: 10001`), so attribute
  selectors must include them.

## AI providers

- **Z.ai GLM Coding Plan keys are for Z.ai's supported coding tools only**
  (Z.ai FAQ: "strictly limited to use within officially supported tools and
  products"). Calls from this app may be rejected or billed to the account
  balance. Use a pay-as-you-go Z.ai API key here.
- A language model cannot edit pixels. In this app it plans edits: it returns
  actions (adjustment layers, rotate, AI tools) that the editor runs, so every
  step stays undoable. Inpainting and style edits need an image model
  (OpenAI Images or a diffusion server).
