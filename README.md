# Odysseus-Edit

A layered image editor that runs in your browser, with optional AI tools and
plain-language editing through the language model of your choice.

Odysseus-Edit is an unofficial, standalone extraction of the image editor in
[Odysseus](https://github.com/odysseus-dev/odysseus), a self-hosted AI
workspace. It is not affiliated with the Odysseus project.

## Features

- **Layers:** groups, masks, clipping, blend modes, opacity, layer styles,
  non-destructive adjustment layers.
- **Tools:** move, crop, transform, text, shapes, brush, gradient, eraser,
  eyedropper, smudge, clone, healing, dodge, burn.
- **Selections:** marquee, lasso, magic wand, pen, quick mask, saved selections.
- **Adjustments:** levels, curves, hue/saturation, white balance, exposure,
  vibrance, shadows/highlights, colour balance, black & white, gradient map,
  blur and more.
- **Quick Edit:** type what you want ("warmer and a bit brighter", "make it
  moody"). Built-in commands work offline; with a language model configured,
  the model plans the edit and the editor applies it as ordinary, undoable
  layers.
- **AI tools** (optional server): background removal, inpainting, upscaling,
  denoise, sharpen, face restoration, style edits.
- **Files:** open, drop or paste images; autosaved drafts in the browser;
  save back to the original file (Chromium) or download; PNG, JPEG and WebP
  export; layered project files (.json).

## Quick start

### Editor only

No install needed. Serve the `web/` folder with any static server:

```bash
python3 -m http.server 8000 --directory web
# open http://127.0.0.1:8000
```

AI tools stay hidden until the AI server is running.

### Editor with AI

Requires [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env                                   # add your API keys
cp config/providers.example.toml config/providers.toml # enable providers
cd server
uv run odysseus-edit                                   # http://127.0.0.1:7020
```

The server serves the editor and the AI endpoints on one port. Sharpen and a
basic face enhance work out of the box; the other local AI tools need optional
packages:

| Tool | Install (inside `server/`) |
|---|---|
| Background removal | `uv sync --extra rembg` |
| Smart masks (SAM, text prompts) | `uv sync --extra sam` (PyTorch, several GB) |
| Denoise, AI upscale, face restore | `uv pip install --no-build-isolation basicsr realesrgan gfpgan` after `--extra sam` |

Model weights download on first use. Background removal uses the Apache-2.0
`u2net` model by default (`ODYSSEUS_EDIT_REMBG_MODEL` overrides it).

## AI providers

`config/providers.toml` lists the providers you can use. Keys stay on the
server: each provider names an environment variable, and you set the key in
`.env`.

| kind | Wire format | Examples |
|---|---|---|
| `anthropic` | Anthropic Messages | Anthropic Claude, Z.ai GLM (`https://api.z.ai/api/anthropic`) |
| `openai` | OpenAI Chat Completions | OpenAI, OpenRouter, Ollama, LM Studio, vLLM |
| `openai-images` | OpenAI Images API | OpenAI image models |
| `diffusion` | Odysseus Cookbook diffusion server | self-hosted Stable Diffusion / Flux |

Language models (`chat`) power Quick Edit. They plan edits; they do not paint
pixels. Inpainting and style edits need an image model (`image-edit`).

### Z.ai GLM

```toml
[[providers]]
id = "zai"
name = "Z.ai GLM"
kind = "anthropic"
base_url = "https://api.z.ai/api/anthropic"
api_key_env = "ZAI_API_KEY"
auth = "bearer"
models = ["glm-5.3", "glm-5.3-flash"]
capabilities = ["chat"]
enabled = true
```

Use a pay-as-you-go Z.ai API key. Z.ai's FAQ limits the **GLM Coding Plan** to
Z.ai's supported coding tools, so a Coding Plan key may be rejected or billed
to your account balance when used here.

## Development

```bash
npm test                       # frontend unit tests (node --test)
cd server && uv run pytest     # server tests
```

- `web/js/` mirrors upstream `odysseus/static/js/`; `web/app/` is new code.
  The editor reaches storage, files and the server only through
  `web/app/host.js`.
- **Updating from Odysseus:** `python3 tools/upstream-sync.py /path/to/odysseus`
  merges upstream changes through the `upstream` vendor branch. The full
  procedure, written for coding agents, is in
  [docs/UPSTREAM-SYNC.md](docs/UPSTREAM-SYNC.md). When pushing, push both
  branches: `git push origin main upstream`.
- [CLAUDE.md](CLAUDE.md) has notes for coding agents, [EXPERIENCE.md](EXPERIENCE.md)
  records lessons learned, [WORKLOG.md](WORKLOG.md) records each session.

## Security

The server binds to `127.0.0.1` and has no login. Do not expose it to a
network without putting authentication in front of it: anyone who can reach it
can spend your API credits. It only accepts requests addressed to an allowed
host name (`ODYSSEUS_EDIT_ALLOWED_HOSTS`) and rejects cross-origin writes, which
blocks DNS-rebinding and drive-by requests from other websites. The browser can
only pick providers listed in `config/providers.toml`; it never sends URLs or
keys the server would call.

## License

GNU Affero General Public License v3.0 or later, the same as Odysseus. See
[LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). If you run a modified version
as a network service, you must offer its source to its users. Fira Code is
under the SIL Open Font License ([licenses/](licenses/)).
