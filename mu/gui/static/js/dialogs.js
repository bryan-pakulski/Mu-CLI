// Shared modal focus management — the only modal mechanism (Wash .w-modal /
// .w-drawer markup, see static/css/wash/components/modal.css).
//
// Two entry points share one stack, one focus trap and one Esc handler:
//
//   x-dialog="expr"            Alpine directive; follows the same condition as
//                              x-show on the backdrop element.
//   MuDialog.open(el, opts)    vanilla pages (/containers, /work, /models/costs)
//   MuDialog.close(el)         that toggle a backdrop's `hidden` themselves.
//       opts.focus: element or selector that receives focus on open
//       (destructive confirms pass their Cancel button, G§9.17), or false
//       when the caller places focus itself.
//
// The backdrop is the trap root; role="dialog" / aria-modal / aria-labelledby
// belong on the dialog surface inside it (.w-modal__dialog, .w-drawer__panel),
// which both entry points label from its title when no label is given. Esc
// and outside focus are handled here; a `dialog-close` event on the backdrop
// lets the owning component decide how to close.
(function () {
    const stack = [];
    let nextDialogId = 0;
    const focusable = 'button:not(:disabled),a[href],input:not(:disabled):not([type="hidden"]),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
    const top = () => stack[stack.length - 1];
    const visible = el => el.isConnected && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
    const targets = el => Array.from(el.querySelectorAll(focusable)).filter(node => visible(node) && !node.closest('[inert]'));
    const focus = el => (targets(el)[0] || el).focus({ preventScroll: true });

    document.addEventListener('keydown', event => {
        const entry = top();
        if (!entry) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopImmediatePropagation();
            entry.el.dispatchEvent(new CustomEvent('dialog-close'));
        } else if (event.key === 'Tab') {
            const items = targets(entry.el);
            const current = items.indexOf(document.activeElement);
            if (!items.length || current < 0 || (event.shiftKey ? current === 0 : current === items.length - 1)) {
                event.preventDefault();
                (items[event.shiftKey ? items.length - 1 : 0] || entry.el).focus({ preventScroll: true });
            }
        }
    }, true);

    document.addEventListener('focusin', event => {
        const entry = top();
        if (entry && !entry.el.contains(event.target)) focus(entry.el);
    });

    // Put the dialog semantics on the surface and label it from its title.
    function prepare(el) {
        const surface = el.querySelector('[role="dialog"], [role="alertdialog"]') || el.querySelector('.w-modal__dialog, .w-drawer__panel') || el;
        if (surface.getAttribute('role') !== 'alertdialog') surface.setAttribute('role', 'dialog');
        surface.setAttribute('aria-modal', 'true');
        if (!surface.hasAttribute('aria-label') && !surface.hasAttribute('aria-labelledby')) {
            const title = surface.querySelector('.w-modal__title,.w-drawer__title,.modal-title,.fb-title,.confirm-message,h2');
            if (title) {
                if (!title.id) title.id = `mucli-dialog-title-${++nextDialogId}`;
                surface.setAttribute('aria-labelledby', title.id);
            }
        }
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
        return surface;
    }

    function push(el, preferred) {
        const existing = stack.find(entry => entry.el === el);
        if (existing) return existing;
        const entry = { el, previous: document.activeElement, frame: 0 };
        stack.push(entry);
        document.documentElement.classList.add('has-dialog');
        if (preferred === false) return entry;
        entry.frame = requestAnimationFrame(() => {
            if (top() !== entry) return;
            const target = typeof preferred === 'string' ? el.querySelector(preferred) : preferred;
            if (target && visible(target)) target.focus({ preventScroll: true });
            else focus(el);
        });
        return entry;
    }

    function pop(entry) {
        if (!entry || stack.indexOf(entry) < 0) return;
        cancelAnimationFrame(entry.frame);
        const wasTop = top() === entry;
        const previous = entry.previous;
        stack.splice(stack.indexOf(entry), 1);
        document.documentElement.classList.toggle('has-dialog', stack.length > 0);
        if (wasTop) {
            if (previous && visible(previous) && (!top() || top().el.contains(previous))) previous.focus({ preventScroll: true });
            else if (top()) focus(top().el);
        }
    }

    window.MuDialog = {
        open(el, options = {}) {
            if (!el) return;
            prepare(el);
            el.hidden = false;
            push(el, options.focus);
        },
        close(el) {
            if (!el) return;
            el.hidden = true;
            pop(stack.find(entry => entry.el === el));
        },
        isOpen(el) { return stack.some(entry => entry.el === el); },
        prepare,
    };

    document.addEventListener('alpine:init', () => {
        Alpine.directive('dialog', (el, { expression }, { evaluateLater, effect, cleanup }) => {
            const readOpen = evaluateLater(expression);
            let entry;
            prepare(el);
            const close = () => { pop(entry); entry = null; };
            effect(() => readOpen(open => {
                if (!open) { close(); return; }
                if (entry) return;
                entry = push(el);
            }));
            cleanup(close);
        });
    });
})();
