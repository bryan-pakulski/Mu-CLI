(() => {
    'use strict';

    function currentTheme() {
        return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    }

    function apply(theme) {
        const value = theme === 'light' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', value);
        document.querySelectorAll('[data-route-theme]').forEach(button => {
            button.setAttribute('aria-label', value === 'dark' ? 'Switch to light appearance' : 'Switch to dark appearance');
            button.title = value === 'dark' ? 'Switch to light appearance' : 'Switch to dark appearance';
            button.querySelectorAll('[data-theme-icon="dark"]').forEach(icon => { icon.hidden = value !== 'dark'; });
            button.querySelectorAll('[data-theme-icon="light"]').forEach(icon => { icon.hidden = value !== 'light'; });
        });
    }

    function toggle() {
        const next = currentTheme() === 'dark' ? 'light' : 'dark';
        try { localStorage.setItem('mucli-theme', next); } catch (_) {}
        apply(next);
    }

    function init() {
        document.querySelectorAll('[data-route-theme]').forEach(button => {
            button.addEventListener('click', toggle);
        });
        apply(currentTheme());
    }

    window.addEventListener('storage', event => {
        if (event.key === 'mucli-theme' && (event.newValue === 'light' || event.newValue === 'dark')) {
            apply(event.newValue);
        }
    });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
