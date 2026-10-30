// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.test.cjs
// Settings → Appearance → Scenery (route_theme.js MuScenery): a watercolour pack from tokens.json
// scenery is stored and applied as html[data-scenery]; Classic (the default) removes it; unknown
// values read as Classic; the choices are unavailable while a flat palette is chosen.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');
const tokens = JSON.parse(readFileSync(resolve(root, 'mu/design/tokens.json'), 'utf8'));
const PACKS = Object.keys(tokens.scenery).filter(k => !k.startsWith('_') && k !== 'classic');

async function page({ stored = null, palette = null } = {}) {
    const choices = ['classic', ...PACKS].map(n => `<button data-scenery-choice="${n}">${n}</button>`).join('');
    const dom = new JSDOM(`<!doctype html><div data-scenery-setting>${choices}</div>`,
        { runScripts: 'outside-only', url: 'http://127.0.0.1/' });
    const w = dom.window;
    w.MU_SCENERY = PACKS;                       // published by the pre-paint bootstrap
    if (stored != null) w.localStorage.setItem('mucli-scenery', stored);
    if (palette) w.localStorage.setItem('mucli-palette', palette);   // MuPalette applies it on load
    w.eval(readFileSync(resolve(root, 'mu/gui/static/js/route_theme.js'), 'utf8'));
    if (w.document.readyState === 'loading') await new Promise(done => w.document.addEventListener('DOMContentLoaded', done));
    return w;
}
const scenery = w => w.document.documentElement.getAttribute('data-scenery');
const pressed = w => [...w.document.querySelectorAll('[data-scenery-choice][aria-pressed="true"]')].map(b => b.dataset.sceneryChoice);

test('the packs are the ones tokens.json lists', async () => {
    const w = await page();
    assert.deepEqual(w.MuScenery.packs(), PACKS);
    assert.ok(PACKS.includes('desert') && PACKS.includes('space'));
});

test('a stored pack applies on load; nothing stored is Classic', async () => {
    const w = await page({ stored: 'forest' });
    assert.equal(scenery(w), 'forest');
    assert.deepEqual(pressed(w), ['forest']);
    const plain = await page();
    assert.equal(scenery(plain), null);
    assert.deepEqual(pressed(plain), ['classic']);
});

test('choosing a pack stores and applies it; Classic removes it and tells the shell', async () => {
    const w = await page();
    const events = [];
    w.addEventListener('mucli-scenery-change', e => events.push(e.detail.scenery));
    w.document.querySelector('[data-scenery-choice="ocean"]').click();
    assert.equal(scenery(w), 'ocean');
    assert.equal(w.localStorage.getItem('mucli-scenery'), 'ocean');
    w.document.querySelector('[data-scenery-choice="classic"]').click();
    assert.equal(scenery(w), null);
    assert.equal(w.localStorage.getItem('mucli-scenery'), 'classic');
    assert.deepEqual(events, ['ocean', 'classic']);
});

test('an unknown stored value reads as Classic', async () => {
    const w = await page({ stored: 'volcano' });
    assert.equal(scenery(w), null);
    assert.equal(w.MuScenery.preference(), 'classic');
});

test('with a flat palette the choices are disabled and clicks change nothing', async () => {
    const w = await page({ palette: 'darcula' });
    const desert = w.document.querySelector('[data-scenery-choice="desert"]');
    assert.ok(desert.disabled);
    desert.click();
    assert.equal(scenery(w), null);
});
