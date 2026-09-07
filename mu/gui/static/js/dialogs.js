// Shared modal focus management. x-dialog follows the same condition as
// x-show; a dialog-close event lets the owning component decide how to close.
(function () {
    const stack = [];
    let nextDialogId = 0;
    const focusable = 'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
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

    document.addEventListener('alpine:init', () => {
        Alpine.directive('dialog', (el, { expression }, { evaluateLater, effect, cleanup }) => {
            const readOpen = evaluateLater(expression);
            let entry;
            let frame = 0;
            const surface = el.querySelector('[role="dialog"]') || el;
            surface.setAttribute('role', 'dialog');
            surface.setAttribute('aria-modal', 'true');
            if (!surface.hasAttribute('aria-label') && !surface.hasAttribute('aria-labelledby')) {
                const title = surface.querySelector('.modal-title,.fb-title,.confirm-message,h2');
                if (title) {
                    if (!title.id) title.id = `mucli-dialog-title-${++nextDialogId}`;
                    surface.setAttribute('aria-labelledby', title.id);
                }
            }
            el.setAttribute('tabindex', '-1');

            const close = () => {
                if (!entry) return;
                cancelAnimationFrame(frame);
                const wasTop = top() === entry;
                const previous = entry.previous;
                stack.splice(stack.indexOf(entry), 1);
                entry = null;
                document.documentElement.classList.toggle('has-dialog', stack.length > 0);
                if (wasTop) {
                    if (previous && visible(previous) && (!top() || top().el.contains(previous))) previous.focus({ preventScroll: true });
                    else if (top()) focus(top().el);
                }
            };

            effect(() => readOpen(open => {
                if (!open) { close(); return; }
                if (entry) return;
                entry = { el, previous: document.activeElement };
                stack.push(entry);
                document.documentElement.classList.add('has-dialog');
                frame = requestAnimationFrame(() => {
                    if (top() === entry) focus(el);
                });
            }));
            cleanup(close);
        });
    });
})();
