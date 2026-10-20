// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.test.cjs
// Settings → Appearance → Text size (route_theme.js MuTextScale): the preference is stored,
// clamped to the design range and applied as --mu-scale on <html>, which tokens.css multiplies
// type, spacing and control sizes by.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');
const tokens = JSON.parse(readFileSync(resolve(root, 'mu/design/tokens.json'), 'utf8'));
const range = tokens.density.text_scale;

async function page(stored) {
    const dom = new JSDOM(`<!doctype html><input type="range" data-text-scale-range><output data-text-scale-value></output><button data-text-scale-reset>Reset</button>`,
        { runScripts: 'outside-only', url: 'http://127.0.0.1/' });
    if (stored != null) dom.window.localStorage.setItem('mucli-text-scale', String(stored));
    dom.window.eval(readFileSync(resolve(root, 'mu/gui/static/js/route_theme.js'), 'utf8'));
    if (dom.window.document.readyState === 'loading') await new Promise(done => dom.window.document.addEventListener('DOMContentLoaded', done));
    return dom.window;
}
const scaleOf = w => w.document.documentElement.style.getPropertyValue('--mu-scale');

test('the module uses the design range from tokens.json', async () => {
    const w = await page();
    assert.deepEqual([w.MuTextScale.MIN, w.MuTextScale.MAX, w.MuTextScale.STEP, w.MuTextScale.DEFAULT],
        [range.min, range.max, range.step, range.default]);
});

test('a stored size applies on load; the default leaves the design untouched', async () => {
    const w = await page(90);
    assert.equal(scaleOf(w), '0.9');
    assert.equal(w.document.querySelector('[data-text-scale-value]').textContent, '90%');
    assert.equal(scaleOf(await page()), '');
});

test('moving the slider stores and applies the size, clamped to the range and step', async () => {
    const w = await page();
    const input = w.document.querySelector('[data-text-scale-range]');
    input.value = '87';
    input.dispatchEvent(new w.Event('input', { bubbles: true }));
    assert.equal(w.localStorage.getItem('mucli-text-scale'), '85');
    assert.equal(scaleOf(w), '0.85');
    assert.equal(w.MuTextScale.set(400), range.max);
    assert.equal(scaleOf(w), String(range.max / 100));
});

test('Reset returns to the design size', async () => {
    const w = await page(110);
    w.document.querySelector('[data-text-scale-reset]').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    assert.equal(w.localStorage.getItem('mucli-text-scale'), '100');
    assert.equal(scaleOf(w), '');
});
