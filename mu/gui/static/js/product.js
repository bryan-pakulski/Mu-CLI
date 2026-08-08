/* MuCLI Product UI — presentation-only enhancements.
 * No backend/store semantics live here. This file intentionally confines
 * itself to static copy, focus ergonomics and product-level shortcuts.
 */
(function () {
    function setText(selector, value) {
        const node = document.querySelector(selector);
        if (node) node.textContent = value;
    }

    function polishWelcomeCopy() {
        setText('.welcome-kicker', 'AI workspace');
        setText('.welcome-hero h1', 'What are you working on?');
        setText('.welcome-hero .lede', 'Start a new workspace or pick up where you left off.');

        const entries = Array.from(document.querySelectorAll('.welcome-entry'));
        const copy = [
            ['Continue a session', 'Open recent work and keep going'],
            ['Start something new', 'Create a chat, workspace, or isolated environment'],
            ['Environments', 'Manage isolated development environments'],
        ];
        entries.slice(0, copy.length).forEach((entry, index) => {
            const strong = entry.querySelector('.welcome-entry-copy strong');
            const small = entry.querySelector('.welcome-entry-copy small');
            if (strong) strong.textContent = copy[index][0];
            // Preserve Alpine-owned dynamic counters on the first/third card.
            if (small && !small.hasAttribute('x-text')) small.textContent = copy[index][1];
        });

        const footnote = document.querySelector('.welcome-footnote > span');
        if (footnote) footnote.textContent = 'Your model, workspace, and execution environment stay under your control.';
    }

    function focusComposer() {
        const input = document.querySelector('.composer textarea');
        if (!input) return false;
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        return true;
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.documentElement.classList.add('mucli-product-ui');
        polishWelcomeCopy();
    });

    document.addEventListener('keydown', (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
            const active = document.activeElement;
            const isEditable = active && (
                active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable
            );
            if (!isEditable && focusComposer()) event.preventDefault();
        }
    });
})();
