# Work log

Newest entry first. Each entry: what changed, how it was verified, what is left.

## 2026-10-19 (end): process for following upstream Odysseus

**Done** (the user will not maintain this by hand; an agent should be able to
update it from a newer Odysseus checkout)
- `tools/upstream-sync.py`: vendor-branch sync (`upstream` branch +
  `git merge --no-commit`), `upstream.json`, CSS regeneration, and a report:
  conflicts, missing imports, `/api` paths the server lacks, missing shim
  functions, upstream backend diff to port. `--check` mode changes nothing.
- Baseline: `upstream` branch now = mirrored files @9712a54 (normalized),
  merged into main (no content change).
- `docs/UPSTREAM-SYNC.md`: the runbook for agents (conflict rules, report
  handling, backend mapping, verification, ledger of our changes).

**Verified**
- Simulated an upstream update in scratch clones (plain change, change on a
  line we modified, `?v=` bump, new file, deleted file, new external import,
  new shim call, new API, backend change). Result: one conflicting file with
  two hunks (imports, session key); `?v=` bump merged cleanly; the report
  listed the missing imports, the unserved `/api/image/sparkle`, the missing
  `ui.showNotice` and the backend diff.
- `--check` on the real repo: nothing to change.

## 2026-10-19 (late night): Photoshop-style header

**Done** (user chose "menu bar + options bar"; no Move tool changes)
- Menu bar: File, Edit, Image (with Adjustments), Layer, Select, Filter, View,
  Help; hover-switching, arrow-key navigation, Escape, submenus. Document
  name and draft status on the right; app icon goes home.
- Options bar: active tool + its main settings mirrored two-way from the right
  panel (brush/eraser/clone/heal/smudge/dodge/burn size, opacity, flow,
  softness, mode; gradient; text font/size/colour/bold/italic; shape;
  marquee; lasso; wand; eyedropper; move/transform X/Y/W/H); undo, redo,
  history, before/after, shortcuts on the right.
- File > New / Open (also Ctrl+O, Ctrl+Alt+N) go through the start page.
- Fixed upstream Escape handling for dialogs (export, confirm, shortcuts)
  and positioned History/shortcuts next to their (now visible) buttons.

**Verified** (Playwright, dark + light, 1440/1366/390 px)
- Every menu opens with the expected items; Escape closes; hover switches;
  New Layer, Duplicate, Undo x2, Levels adjustment, Grid toggle, Select
  All/Deselect state, Export dialog + Escape, About + Escape, rasterize
  prompt + Escape, History, Show Before, shortcuts popover.
- Options mirror: changing the bar's size updates the panel slider and `]`
  in the canvas updates the bar. No overlap at 390 px (bar scrolls).
- `npm test` 9 passed, server 155 passed.

**Left**
- Brush settings appear in both the options bar and the right panel; the
  panel copy could be trimmed if the user prefers.

## 2026-10-19 (night, 2): scrollbar on the right of the canvas

**Found** The collapsed Quick Edit pill hung 16px below the canvas area
(`bottom: -16px`), making the area scrollable by 16px whenever the pointer was
not over the canvas; overlay scrollbars appeared near the right edge.

**Done** Collapsed pill stays inside the edge (`bottom: 4px`), hover still
lifts it.

**Verified** At fit zoom `scrollHeight === clientHeight` with the pointer over
the canvas and over the right panel (no scrollbar gutter). Zoomed in, the
mouse wheel still scrolls the canvas.

## 2026-10-19 (night): Quick Edit look and replies

**Done** (from the user's screenshots: "no need for toasts, the design broke")
- Removed the Quick Edit toasts; errors stay in the status line, with an
  inline "How to enable" link when the AI server is missing.
- Light theme: Odysseus' `:root.light button` rule turned the send and Save
  buttons grey and boxed the icon buttons. The CSS sync now drops that
  selector (`tools/extract_css.py --drop-selector`), and the landing-page
  workaround for it is gone.
- Model picker sits after "DOCUMENT" with the same divider and reads as a
  label, so its caret no longer sits next to the collapse chevron.
- Planner prompt lists the manual tools; for "remove the cuts at the bottom"
  GLM now answers (in Bengali) to use Healing Brush or Clone Stamp.

**Verified**
- Light and dark screenshots of Quick Edit and the top bar: red send/Save
  buttons, transparent icon buttons. 155 server tests pass, ruff clean.
- One real glm-5.3 call with the user's prompt: guidance reply, HTTP 200.

**Left**
- The user restarts their server to pick up the planner prompt change.

## 2026-10-19 (evening): "glm-5.3-flash does nothing"

**Found**
- The user's server, config and key were fine: `/api/ai/command` with
  glm-5.3-flash returned plans in ~5 s and the browser added the layers.
- After two quick calls, Z.ai answered every request with 429
  `[1302] Rate limit reached for requests`. The editor showed this only in the
  Quick Edit status line, so it looked like nothing happened.

**Done**
- `server/app/llm.py`: retry 429/503/529 twice with backoff (Retry-After
  honoured); clearer rate-limit message. Tests for retry, recovery and no
  retry on auth errors (154 passed).
- Quick Edit shows a toast for planner errors and for replies with no edit.

**Verified**
- Patched server on a spare port: Bengali prompt with glm-5.3-flash returned a
  Bengali message and two actions (brightness, vibrance), HTTP 200.

**Left**
- The user must restart their server (`Ctrl+C`, `uv run odysseus-edit`) to
  get the retry.

## 2026-10-19 (later): AI server and multi-vendor Quick Edit

**Done**
- `server/` (FastAPI, uv, Python 3.12): serves `web/` with `no-cache`, plus
  the image AI endpoints ported from Odysseus (inpaint, harmonize, style
  transfer, sharpen, denoise, upscale, SAM masks, background removal, face
  enhance). Image vendors: OpenAI-compatible Images API and self-hosted
  diffusion servers. Browser-chosen endpoints must match a configured provider
  (no SSRF); keys never leave the server. Host + Origin checks, 50 MB body cap.
  Heavy ML packages are optional extras.
- Multi-vendor language models (`server/app/llm.py`): Anthropic Messages
  (Anthropic, Z.ai GLM via `https://api.z.ai/api/anthropic`, bearer or
  x-api-key auth) and OpenAI-compatible Chat Completions (OpenAI, OpenRouter,
  Ollama...).
- Quick Edit planner (`server/app/planner.py`, `/api/ai/providers`,
  `/api/ai/command`): prompt lists only the AI tools this install has; the
  reply is validated and clamped; unknown actions are dropped.
- Frontend: Quick Edit model picker, style transfer sends its model choice,
  AI errors show the server's detail, "Remove" recognises any OpenAI-style
  image provider, setup help names the right extras.

**Verified**
- `cd server && uv run pytest`: 152 passed, 3 skipped (real-model tests
  need `ODYSSEUS_EDIT_TEST_MODELS=1`); ruff clean. `npm test`: 9 passed.
- End to end with a mock Anthropic-format server standing in for GLM:
  start page shows "AI ready · Quick Edit: glm-5.3"; "give it a warm sunset
  mood" → server → mock (Bearer key, anthropic-version, model glm-5.3,
  document context) → three adjustment layers (Sunset warmth, Vibrance,
  Shadows/Highlights), an invented "teleport" action dropped; Sharpen ran on
  the server and added a layer. No page errors.

**Left**
- Not tested against the real Z.ai API (no key in this environment).
- Real rembg / Real-ESRGAN / SAM runs (weights download) not exercised.
- Image-model features (inpaint, style edit) tested only with mocked vendors.

## 2026-10-19: Project created, editor extracted

**Done**
- Created the repository. The first commit imports the editor from Odysseus
  `dev` @ `9712a54` unmodified (NOTICE.md lists the files).
- Frontend runs standalone (`web/`, no build step):
  - shims for upstream `ui.js` (toast, error, confirm) and `modalManager.js`
    (minimised-panel dock);
  - `web/app/host.js` adapter: drafts and recent images in IndexedDB, Save to
    the original file through the File System Access API with a download
    fallback, Save As, AI capability check, setup help dialogs;
  - start page: open/drop/paste an image, canvas presets, custom size, recent
    drafts with thumbnails, session recovery after reload, light/dark theme;
  - Home button in the editor top bar returns to the start page after
    flushing the draft;
  - AI tools hidden while the AI server is not running;
  - Quick Edit runs a shared action vocabulary: built-in commands (rotate,
    flip, warmer, cooler, brighter, darker, contrast, vivid, black and white)
    work without AI; with a language model configured, `/api/ai/command`
    plans the actions.
- CSS: `tools/extract_css.py` pulls the editor's rules out of Odysseus' app
  stylesheets into `web/css/upstream-pre.css` / `upstream-post.css`.
- Multi-vendor provider config (`config/providers.example.toml`): Anthropic
  Messages (Anthropic, Z.ai GLM at `https://api.z.ai/api/anthropic`), OpenAI
  compatible chat (OpenAI, OpenRouter, Ollama), OpenAI Images, local diffusion.

**Verified** (Playwright, Chromium, static server, no AI server)
- Topbar Import now opens a File / Clipboard / Recent menu (upstream's
  Gallery picker was unreachable); Recent imports a layer, the menu closes on
  an outside click.
- Open image → brush → Quick Edit "warmer" adds a White Balance layer →
  autosave "Saved" → Home → draft card with thumbnail → resume.
- Export dialog downloads; Ctrl+S and Save fall back to a download without a
  file handle; Recent images picker imports a layer; Custom size → Esc
  returns to the start page; reload reopens the open draft.
- No page errors; only expected 404s for `/api/*` without the server.

**Left**
- (done in the next entry) AI server and Quick Edit planner.
