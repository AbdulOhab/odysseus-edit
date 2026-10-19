# Experience

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
  that tool. It was unreachable upstream; the topbar Import button now opens a
  File / Clipboard / Recent menu instead.
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
- `python3 -m http.server` sends no cache headers, so Chromium heuristically
  caches CSS/JS and a plain reload can show stale styles. Reload with the cache
  bypassed (or use the project server, which sends `Cache-Control: no-cache`).

## AI providers

- **Z.ai GLM Coding Plan keys are for Z.ai's supported coding tools only**
  (Z.ai FAQ: "strictly limited to use within officially supported tools and
  products"). Calls from this app may be rejected or billed to the account
  balance. Use a pay-as-you-go Z.ai API key here.
- A language model cannot edit pixels. In this app it plans edits: it returns
  actions (adjustment layers, rotate, AI tools) that the editor runs, so every
  step stays undoable. Inpainting and style edits need an image model
  (OpenAI Images or a diffusion server).

## Server (Python, uv)

- **Never put `realesrgan`/`gfpgan` in a pyproject extra.** `basicsr` ships only
  an sdist whose `setup.py` imports torch; `uv lock` resolves every extra, so
  the lock hangs or fails even for the base install. Install them by hand with
  `--no-build-isolation`.
- **rembg calls `sys.exit(1)` at import when onnxruntime is missing.** Probe
  `find_spec("onnxruntime")` first; a SystemExit can take the server down.
- **rembg's default model changed to bria-rmbg (CC BY-NC 4.0).** We pin
  `u2net` (Apache-2.0). Upstream's transformers fallback (briaai/RMBG-1.4) has
  Bria's own licence and runs remote code (`trust_remote_code=True`).
- Upstream decoded masks with `.convert("L")`, ignoring alpha; masks are now
  weighted by alpha. Upstream denoise `strength` never had an effect
  (RealESRGANer ignores `dni_weight` with one model path).
- **`request.json()` ignores Content-Type**, so a cross-site `text/plain` form
  POST could spend the user's API keys. `app/middleware.py` checks Host
  (DNS rebinding) and Origin (cross-site writes).
- `uv sync --extra sam` pulls CUDA PyTorch on Linux (several GB).
- FastAPI dependencies are `param = Depends(...)` defaults; ruff's B008 is
  disabled for that in `server/pyproject.toml`.
- **Stopping background servers:** `pkill -f <pattern>` can match and kill the
  shell running it. Find the PID from the port instead:
  `ss -ltnpH "sport = :7021"`.

## Quick Edit planner

- One action vocabulary in two places: `web/app/quick-edit.js` (browser,
  maps to adjustment layers) and `server/app/planner.py` (prompt + validation).
  Change both together, and their tests.
- The planner asks for plain JSON instead of vendor tool-calling: every vendor
  (Anthropic, GLM, OpenAI, Ollama) can do it, and `llm.extract_json_object`
  tolerates code fences, `<think>` blocks and surrounding prose.
- Thinking models return extra content blocks (`type: "thinking"`); only
  `text` blocks are read.
- Newer OpenAI reasoning models reject `max_tokens`/`temperature`; the client
  retries once with `max_completion_tokens` and without temperature.
- The canvas preview is sent only to providers with the `vision` capability.
- For end-to-end tests without a real key, a 40-line mock of `/v1/messages`
  (Anthropic format) is enough; point a provider's `base_url` at it.
