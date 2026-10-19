# Work log

Newest entry first. Each entry: what changed, how it was verified, what is left.

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
- Open image → brush → Quick Edit "warmer" adds a White Balance layer →
  autosave "Saved" → Home → draft card with thumbnail → resume.
- Export dialog downloads; Ctrl+S and Save fall back to a download without a
  file handle; Recent images picker imports a layer; Custom size → Esc
  returns to the start page; reload reopens the open draft.
- No page errors; only expected 404s for `/api/*` without the server.

**Left**
- AI server (`server/`) is being ported; Quick Edit planner endpoint next.
- Topbar Import should offer File / Clipboard / Recent (Recent is unreachable,
  as upstream's Gallery button was).
