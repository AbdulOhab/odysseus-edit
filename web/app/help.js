/**
 * "How do I enable this?" dialogs for AI features that need the optional
 * server, a local package or a configured provider.
 */

const TOPICS = {
  server: {
    title: 'Start the AI server',
    body: `
      <p>AI tools run on the optional Odysseus-Edit server. The editor itself works without it.</p>
      <pre><code>cd server
uv run odysseus-edit</code></pre>
      <p>Then open <code>http://127.0.0.1:7020</code>, which serves this editor with AI enabled.</p>`,
  },
  rembg: {
    title: 'Install local AI tools',
    body: `
      <p>Background removal uses <code>rembg</code>, an optional package of the server.</p>
      <pre><code>cd server
uv sync --extra rembg</code></pre>
      <p>Smart masks (SAM) need <code>--extra sam</code> (PyTorch, several GB). Denoise,
      AI upscale and face restore need Real-ESRGAN and GFPGAN, installed by hand:</p>
      <pre><code>uv pip install --no-build-isolation basicsr realesrgan gfpgan</code></pre>
      <p>Restart the server afterwards. The first run downloads the model weights.</p>`,
  },
  'image-model': {
    title: 'Set up an image model',
    body: `
      <p>Inpaint, harmonize and style edits need an image model. Enable one in
      <code>config/providers.toml</code> (copy it from <code>providers.example.toml</code>):</p>
      <ul>
        <li><code>openai-images</code>: OpenAI-compatible Images API</li>
        <li><code>local-diffusion</code>: a self-hosted diffusion server</li>
      </ul>
      <p>Put the API key in <code>.env</code> and restart the server.</p>`,
  },
  llm: {
    title: 'Set up a language model',
    body: `
      <p>Quick Edit understands plain-language requests through a language model.
      Enable a provider in <code>config/providers.toml</code>, for example Z.ai GLM, Anthropic,
      OpenAI or a local Ollama, and put its key in <code>.env</code>.</p>
      <p>Without one, Quick Edit still runs its built-in commands such as
      <em>rotate</em>, <em>flip</em> and <em>remove background</em>.</p>`,
  },
};

export function showSetupHelp(topic) {
  const info = TOPICS[topic] || TOPICS.server;
  const previouslyFocused = document.activeElement;
  const overlay = document.createElement('div');
  overlay.className = 'oe-dialog-overlay';
  overlay.innerHTML = `
    <div class="oe-dialog oe-help" role="dialog" aria-modal="true" aria-labelledby="oe-help-title">
      <h2 class="oe-dialog-title" id="oe-help-title"></h2>
      <div class="oe-help-body">${info.body}</div>
      <div class="oe-dialog-actions">
        <button type="button" class="oe-btn oe-btn-primary" data-act="close">Got it</button>
      </div>
    </div>`;
  overlay.querySelector('#oe-help-title').textContent = info.title;
  const close = () => {
    document.removeEventListener('keydown', onKey, true);
    overlay.remove();
    if (previouslyFocused?.isConnected) previouslyFocused.focus?.({ preventScroll: true });
  };
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
  };
  overlay.querySelector('[data-act="close"]').addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(overlay);
  overlay.querySelector('[data-act="close"]').focus();
}
