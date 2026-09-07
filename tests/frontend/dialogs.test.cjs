// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/frontend/*.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = resolve(__dirname, '../..');
const script = name => readFileSync(resolve(root, 'mu/gui/static', name), 'utf8');
const settle = window => new Promise(resolve => window.requestAnimationFrame(() => window.requestAnimationFrame(resolve)));

test('stacked dialogs keep focus, close one at a time, and return to their trigger', async () => {
    const errors = [];
    const console = new VirtualConsole();
    console.on('jsdomError', error => errors.push(error));
    const dom = new JSDOM(`<!doctype html><div x-data="{outer: false, inner: false}">
      <button id="launch" @click="outer = true">Settings</button>
      <div x-show="outer" x-dialog="outer" @dialog-close="outer = false">
        <button id="browse" @click="inner = true">Browse</button><button id="last">Last</button>
      </div>
      <template x-teleport="body"><div x-show="inner" x-dialog="inner" @dialog-close="inner = false">
        <button id="pick">Select folder</button>
      </div></template>
    </div>`, { runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: console });
    const { window } = dom;
    // jsdom has no layout engine. Supply only the visibility signal needed
    // for focusability; Alpine still owns the real x-show/teleport lifecycle.
    window.HTMLElement.prototype.getClientRects = function () {
        for (let el = this; el; el = el.parentElement) {
            if (window.getComputedStyle(el).display === 'none') return [];
        }
        return this.isConnected ? [{ width: 100, height: 30 }] : [];
    };
    window.eval(script('js/dialogs.js'));
    window.eval(script('vendor/alpine.min.js'));
    const doc = window.document;
    const key = (name, shiftKey = false) => doc.activeElement.dispatchEvent(new window.KeyboardEvent('keydown', { key: name, shiftKey, bubbles: true, cancelable: true }));
    try {
        await settle(window);
        doc.getElementById('launch').focus();
        doc.getElementById('launch').click();
        await settle(window);
        assert.equal(doc.activeElement.id, 'browse');
        key('Tab', true);
        assert.equal(doc.activeElement.id, 'last');
        key('Tab');
        assert.equal(doc.activeElement.id, 'browse');
        doc.getElementById('browse').click();
        await settle(window);
        assert.equal(doc.activeElement.id, 'pick');
        key('Escape');
        await settle(window);
        assert.equal(doc.activeElement.id, 'browse');
        assert(doc.documentElement.classList.contains('has-dialog'));
        key('Escape');
        await settle(window);
        assert.equal(doc.activeElement.id, 'launch');
        assert(!doc.documentElement.classList.contains('has-dialog'));
        assert.deepEqual(errors, []);
    } finally { window.close(); }
});

test('a failed math render does not prevent the next response from rendering', async () => {
    const dom = new JSDOM('<p id="math">$x$</p>', { runScripts: 'outside-only' });
    const { window } = dom;
    // Let the empty document finish loading before registering app bootstrap.
    await new Promise(resolve => window.addEventListener('load', resolve));
    let attempts = 0;
    window.MathJax = { typesetPromise: async () => { if (++attempts === 1) throw new Error('bad expression'); } };
    window.console.warn = () => {};
    window.eval(script('js/app.js'));
    try {
        const elements = [window.document.getElementById('math')];
        await window.typesetMath(elements);
        await window.typesetMath(elements);
        assert.equal(attempts, 2);
        elements[0].remove();
        await window.typesetMath(elements);
        assert.equal(attempts, 2, 'detached history must not be typeset');
    } finally { window.close(); }
});
