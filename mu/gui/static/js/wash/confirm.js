/* Wash destructive / plain confirm (G§9.17) — replaces window.confirm.
 *
 *   WashConfirm({ title, message, confirmLabel = 'OK', tone = 'primary' })
 *       → Promise<boolean>
 *
 * Renders a small .w-modal with the shared dialog stack (dialogs.js,
 * MuDialog): focus is trapped, Esc and the backdrop cancel, and focus
 * returns to the opener. A destructive confirm (tone: 'danger') names the
 * consequence in its button and gives Cancel the default focus.
 */
(function () {
    'use strict';
    var seq = 0;

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function WashConfirm(options) {
        var o = options || {};
        var danger = o.tone === 'danger';
        var id = 'w-confirm-' + (++seq);
        var backdrop = document.createElement('div');
        backdrop.className = 'w-modal w-confirm';
        backdrop.hidden = true;
        backdrop.innerHTML = ''
            + '<section class="w-modal__dialog" data-size="sm" role="alertdialog" aria-modal="true" aria-labelledby="' + id + '-title" aria-describedby="' + id + '-message">'
            + '<header class="w-modal__head"><h2 class="w-modal__title" id="' + id + '-title">' + esc(o.title || 'Are you sure?') + '</h2></header>'
            + '<div class="w-modal__body"><p id="' + id + '-message">' + esc(o.message || '') + '</p></div>'
            + '<footer class="w-modal__foot">'
            + '<button type="button" class="w-btn" data-variant="secondary" data-confirm-cancel>Cancel</button>'
            + '<button type="button" class="w-btn" data-variant="' + (danger ? 'danger' : 'primary') + '" data-confirm-ok>' + esc(o.confirmLabel || 'OK') + '</button>'
            + '</footer></section>';
        document.body.appendChild(backdrop);
        var cancel = backdrop.querySelector('[data-confirm-cancel]');
        var ok = backdrop.querySelector('[data-confirm-ok]');
        return new Promise(function (resolve) {
            var settled = false;
            function finish(value) {
                if (settled) return;
                settled = true;
                if (window.MuDialog) window.MuDialog.close(backdrop);
                backdrop.remove();
                resolve(value);
            }
            cancel.addEventListener('click', function () { finish(false); });
            ok.addEventListener('click', function () { finish(true); });
            backdrop.addEventListener('dialog-close', function () { finish(false); });
            backdrop.addEventListener('mousedown', function (event) { if (event.target === backdrop) finish(false); });
            var first = danger ? cancel : ok;
            if (window.MuDialog) {
                window.MuDialog.open(backdrop, { focus: first });
            } else {
                backdrop.hidden = false;
                first.focus();
            }
        });
    }

    window.WashConfirm = WashConfirm;
})();
