// Run with: node --test web/tests
import test from 'node:test';
import assert from 'node:assert/strict';
import { quickEditAdjustment, matchQuickEditCommand } from '../app/quick-edit.js';

test('brightness and contrast map from slider units to multipliers', () => {
  const spec = quickEditAdjustment({ type: 'brightness_contrast', brightness: 20, contrast: -10 });
  assert.equal(spec.type, 'brightness-contrast');
  assert.ok(Math.abs(spec.params.brightness - 1.2) < 1e-9);
  assert.ok(Math.abs(spec.params.contrast - 0.9) < 1e-9);
});

test('values are clamped and junk becomes neutral', () => {
  const wb = quickEditAdjustment({ type: 'white_balance', temperature: 900, tint: 'pink' });
  assert.deepEqual(wb, { type: 'white-balance', params: { temperature: 100, tint: 0 } });
  const exp = quickEditAdjustment({ type: 'exposure', ev: -40 });
  assert.equal(exp.params.exposure, -5);
});

test('levels keeps white above black', () => {
  const spec = quickEditAdjustment({ type: 'levels', black: 250, white: 10, gamma: 50 });
  assert.equal(spec.params.inBlack, 250);
  assert.equal(spec.params.inWhite, 252);
  assert.equal(spec.params.gamma, 9.9);
});

test('curves gain endpoints, are sorted and deduplicated', () => {
  const spec = quickEditAdjustment({ type: 'curves', points: [[192, 208], [64, 48], [64, 50], 'x'] });
  assert.deepEqual(spec.params.points.rgb, [[0, 0], [64, 48], [192, 208], [255, 255]]);
});

test('color balance accepts per-tone objects or a single tone', () => {
  const multi = quickEditAdjustment({ type: 'color_balance', highlights: { r: 10, b: -300 } });
  assert.deepEqual(multi.params, { highlights: { r: 10, g: 0, b: -100 } });
  const single = quickEditAdjustment({ type: 'color_balance', tone: 'shadows', b: 15 });
  assert.deepEqual(single.params, { shadows: { r: 0, g: 0, b: 15 } });
});

test('gradient map rejects non-hex colours', () => {
  const spec = quickEditAdjustment({ type: 'gradient_map', shadows: 'red', highlights: '#FFEEDD' });
  assert.equal(spec.params.shadows, '#000000');
  assert.equal(spec.params.highlights, '#FFEEDD');
});

test('non-adjustment actions are not adjustments', () => {
  assert.equal(quickEditAdjustment({ type: 'rotate', degrees: 90 }), null);
  assert.equal(quickEditAdjustment(null), null);
});

test('strict matching only takes whole short commands', () => {
  const m = (text) => matchQuickEditCommand(text.toLowerCase(), text, { strict: true });
  assert.deepEqual(m('Rotate left'), [{ type: 'rotate', degrees: 270 }]);
  assert.deepEqual(m('warmer'), [{ type: 'white_balance', temperature: 25 }]);
  assert.deepEqual(m('upscale 4x'), [{ type: 'upscale', scale: 4 }]);
  assert.deepEqual(m('style: oil painting'), [{ type: 'style_edit', prompt: 'oil painting', strength: 0.55 }]);
  assert.equal(m('make it warmer and brighter'), null);
});

test('loose matching keeps upstream keyword rules and falls back to a style edit', () => {
  const m = (text) => matchQuickEditCommand(text.toLowerCase(), text, { strict: false });
  assert.deepEqual(m('please remove the background'), [{ type: 'remove_background' }]);
  assert.deepEqual(m('turn left'), [{ type: 'rotate', degrees: 270 }]);
  assert.deepEqual(m('a subtle watercolor look'), [{ type: 'style_edit', prompt: 'a subtle watercolor look', strength: 0.35 }]);
});
