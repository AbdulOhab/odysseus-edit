/**
 * Quick Edit planner client.
 *
 * Sends a plain-language request plus a summary of the document to the
 * server's /api/ai/command, which asks the configured language model
 * (Z.ai GLM, Anthropic, OpenAI-compatible, ...) for a list of edit actions.
 * The editor then runs those actions itself, so the model never touches
 * pixels and every step stays undoable.
 */

const STORAGE_KEY = 'odysseus-edit.quick-edit-model';

let _providersPromise = null;

/**
 * Resolves to {default: {provider, model}, providers: [{id, name, models,
 * vision, ready, reason}]} or null when the AI server is not running.
 */
export function getPlannerProviders({ refresh = false } = {}) {
  if (_providersPromise && !refresh) return _providersPromise;
  _providersPromise = (async () => {
    try {
      const res = await fetch('/api/ai/providers', { headers: { Accept: 'application/json' } });
      if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) return null;
      return await res.json();
    } catch {
      return null;
    }
  })();
  return _providersPromise;
}

/** Model choices as {value: "provider::model", label, provider, model, vision}. */
export async function getModelChoices() {
  const info = await getPlannerProviders();
  if (!info) return [];
  const choices = [];
  for (const provider of info.providers || []) {
    if (!provider.ready) continue;
    for (const model of provider.models || []) {
      choices.push({
        value: `${provider.id}::${model}`,
        label: `${model} · ${provider.name || provider.id}`,
        provider: provider.id,
        model,
        vision: !!provider.vision,
      });
    }
  }
  return choices;
}

/** The stored choice, falling back to the server default. '' means built-in commands only. */
export async function getSelectedChoice() {
  const choices = await getModelChoices();
  let stored = null;
  try { stored = localStorage.getItem(STORAGE_KEY); } catch {}
  if (stored === '') return null;
  const match = choices.find(c => c.value === stored);
  if (match) return match;
  const info = await getPlannerProviders();
  const def = info?.default;
  return choices.find(c => def && c.provider === def.provider && c.model === def.model) || choices[0] || null;
}

export function setSelectedChoice(value) {
  try { localStorage.setItem(STORAGE_KEY, value ?? ''); } catch {}
}

/**
 * Ask the server for a plan. Resolves to {message, actions, provider, model}.
 * Throws with the server's message on failure.
 */
export async function planEdit({ prompt, choice, context, preview, signal }) {
  const res = await fetch('/api/ai/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      prompt,
      provider: choice?.provider || null,
      model: choice?.model || null,
      context: context || {},
      image: preview || null,
    }),
    signal,
  });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map(d => d.msg).join('; ')
      : `AI request failed (${res.status})`;
    throw new Error(message);
  }
  return {
    message: String(data?.message || ''),
    actions: Array.isArray(data?.actions) ? data.actions : [],
    provider: data?.provider || choice?.provider || null,
    model: data?.model || choice?.model || null,
  };
}
