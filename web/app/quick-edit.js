/**
 * Quick Edit action vocabulary, shared by the built-in commands and the
 * language-model planner (server/app/planner.py validates the same shapes).
 *
 * Pure functions, no editor state: galleryEditor.js runs the actions.
 * Numbers arrive in slider units (mostly -100..100) and are clamped here.
 */

export const QUICK_EDIT_LABELS = {
  rotate: 'Rotate', flip: 'Flip', remove_background: 'Remove background', upscale: 'Upscale',
  denoise: 'Denoise', sharpen: 'Sharpen', enhance_face: 'Enhance face', style_edit: 'Style edit',
};

export function quickEditNumber(value, min, max, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fallback;
}

function color(value, fallback) {
  return /^#[0-9a-f]{6}$/i.test(String(value || '')) ? String(value) : fallback;
}

function rgb(value) {
  return {
    r: quickEditNumber(value?.r, -100, 100, 0),
    g: quickEditNumber(value?.g, -100, 100, 0),
    b: quickEditNumber(value?.b, -100, 100, 0),
  };
}

/** Map an action to an adjustment-layer spec {type, params}, or null if it is not one. */
export function quickEditAdjustment(action) {
  const a = action || {};
  switch (a.type) {
    case 'brightness_contrast':
      return { type: 'brightness-contrast', params: {
        brightness: 1 + quickEditNumber(a.brightness, -100, 100, 0) / 100,
        contrast: 1 + quickEditNumber(a.contrast, -100, 100, 0) / 100,
      } };
    case 'exposure':
      return { type: 'exposure', params: { exposure: quickEditNumber(a.ev, -5, 5, 0), offset: 0, gamma: 1 } };
    case 'white_balance':
      return { type: 'white-balance', params: {
        temperature: quickEditNumber(a.temperature, -100, 100, 0),
        tint: quickEditNumber(a.tint, -100, 100, 0),
      } };
    case 'hue_saturation':
      return { type: 'hue-saturation', params: {
        hue: quickEditNumber(a.hue, -180, 180, 0),
        saturation: 1 + quickEditNumber(a.saturation, -100, 100, 0) / 100,
        lightness: quickEditNumber(a.lightness, -100, 100, 0),
      } };
    case 'vibrance':
      return { type: 'vibrance', params: { vibrance: quickEditNumber(a.amount, -100, 100, 0) } };
    case 'black_white':
      return { type: 'black-white', params: {} };
    case 'shadows_highlights':
      return { type: 'shadows-highlights', params: {
        shadows: quickEditNumber(a.shadows, -100, 100, 0),
        highlights: quickEditNumber(a.highlights, -100, 100, 0),
      } };
    case 'levels': {
      const black = Math.round(quickEditNumber(a.black, 0, 253, 0));
      const white = Math.round(quickEditNumber(a.white, black + 2, 255, 255));
      return { type: 'levels', params: {
        channel: 'rgb', inBlack: black, inWhite: white,
        gamma: quickEditNumber(a.gamma, 0.1, 9.9, 1), outBlack: 0, outWhite: 255,
      } };
    }
    case 'curves': {
      const raw = Array.isArray(a.points) ? a.points : [];
      const points = raw
        .filter(pt => Array.isArray(pt) && pt.length === 2)
        .map(([x, y]) => [Math.round(quickEditNumber(x, 0, 255, 0)), Math.round(quickEditNumber(y, 0, 255, 0))])
        .sort((m, n) => m[0] - n[0])
        .filter((pt, i, arr) => i === 0 || pt[0] !== arr[i - 1][0])
        .slice(0, 16);
      if (!points.length || points[0][0] !== 0) points.unshift([0, 0]);
      if (points[points.length - 1][0] !== 255) points.push([255, 255]);
      return { type: 'curves', params: { channel: 'rgb', points: { rgb: points } } };
    }
    case 'color_balance': {
      const params = {};
      for (const tone of ['shadows', 'midtones', 'highlights']) {
        if (a[tone] && typeof a[tone] === 'object') params[tone] = rgb(a[tone]);
      }
      if (!Object.keys(params).length) {
        const tone = ['shadows', 'midtones', 'highlights'].includes(a.tone) ? a.tone : 'midtones';
        params[tone] = rgb(a);
      }
      return { type: 'color-balance', params };
    }
    case 'gradient_map':
      return { type: 'gradient-map', params: {
        shadows: color(a.shadows, '#000000'),
        highlights: color(a.highlights, '#ffffff'),
        midpoint: quickEditNumber(a.midpoint, 0, 100, 50),
        reverse: a.reverse === true,
      } };
    default:
      return null;
  }
}

/**
 * Built-in command matching. strict: only whole, short commands (used even
 * when a language model is configured). Loose: upstream's keyword rules,
 * where anything unmatched becomes a full-image style edit.
 */
export function matchQuickEditCommand(p, prompt, { strict }) {
  const text = p.trim().replace(/\s+/g, ' ').replace(/[.!]+$/, '');
  if (strict) {
    if (/^rotate( 90| right| clockwise)?$/.test(text)) return [{ type: 'rotate', degrees: 90 }];
    if (/^rotate (left|ccw|counter ?clockwise|-90|270)$/.test(text)) return [{ type: 'rotate', degrees: 270 }];
    if (/^(rotate 180|upside down)$/.test(text)) return [{ type: 'rotate', degrees: 180 }];
    if (/^(flip|mirror)( horizontal(ly)?| h)?$/.test(text)) return [{ type: 'flip', axis: 'h' }];
    if (/^(flip|mirror) (vertical(ly)?|v)$/.test(text)) return [{ type: 'flip', axis: 'v' }];
    if (/^remove (the )?(bg|background)$/.test(text)) return [{ type: 'remove_background' }];
    const up = text.match(/^upscale( (2|4)x)?$/);
    if (up) return [{ type: 'upscale', scale: up[2] === '4' ? 4 : 2 }];
    if (text === 'denoise') return [{ type: 'denoise', strength: 0.55 }];
    if (text === 'sharpen') return [{ type: 'sharpen', amount: 65 }];
    if (text === 'enhance face') return [{ type: 'enhance_face' }];
    if (/^(black (and|&) white|b&w|gr[ae]yscale)$/.test(text)) return [{ type: 'black_white' }];
    if (/^warm(er)?$/.test(text)) return [{ type: 'white_balance', temperature: 25 }];
    if (/^cool(er)?$/.test(text)) return [{ type: 'white_balance', temperature: -25 }];
    if (/^bright(er|en)?$/.test(text)) return [{ type: 'brightness_contrast', brightness: 15 }];
    if (/^dark(er|en)?$/.test(text)) return [{ type: 'brightness_contrast', brightness: -15 }];
    if (/^more contrast$/.test(text)) return [{ type: 'brightness_contrast', contrast: 20 }];
    if (/^(vivid|vibrant|more vibrant)$/.test(text)) return [{ type: 'vibrance', amount: 35 }];
    const style = prompt.trim().match(/^style:\s*(.+)$/i);
    if (style) return [{ type: 'style_edit', prompt: style[1], strength: 0.55 }];
    return null;
  }
  if (/\brotate\b.*\b180\b|\bupside\s*down\b/.test(p)) return [{ type: 'rotate', degrees: 180 }];
  if (/\brotate\b.*\b(left|ccw|counter)\b|\bturn\s+left\b/.test(p)) return [{ type: 'rotate', degrees: 270 }];
  if (/\brotate\b|\bturn\s+right\b|\bclockwise\b/.test(p)) return [{ type: 'rotate', degrees: 90 }];
  if (/\bflip\b.*\b(vertical|v)\b|\bmirror\b.*\b(vertical|v)\b/.test(p)) return [{ type: 'flip', axis: 'v' }];
  if (/\bflip\b|\bmirror\b/.test(p)) return [{ type: 'flip', axis: 'h' }];
  if (/\b(remove|erase|cut\s*out|transparent)\b.*\b(bg|background)\b|\b(bg|background)\b.*\b(remove|erase|transparent)\b/.test(p)) {
    return [{ type: 'remove_background' }];
  }
  if (/\b(upscale|higher\s*res|increase\s*resolution|bigger|2x|4x)\b/.test(p)) {
    return [{ type: 'upscale', scale: /\b4x\b/.test(p) ? 4 : 2 }];
  }
  if (/\b(denoise|noise|grain|grainy|clean\s*up)\b/.test(p)) return [{ type: 'denoise', strength: 0.55 }];
  if (/\b(face|portrait|skin|selfie|restore)\b/.test(p)) return [{ type: 'enhance_face' }];
  if (/\b(sharpen|sharp|crisp|clearer|make it look better|enhance|improve|better)\b/.test(p)) {
    return [{ type: 'sharpen', amount: 65 }];
  }
  return [{ type: 'style_edit', prompt, strength: /\b(subtle|slight|small)\b/.test(p) ? 0.35 : 0.55 }];
}
