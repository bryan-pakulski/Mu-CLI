/* MuCLI web shell reliability glue.
 *
 * Keeps session hydration authoritative after the session focus is known.
 * The legacy app bootstrap intentionally starts several stores in parallel;
 * this layer removes the history/focus race without duplicating API logic.
 */
(function () {
    document.addEventListener('alpine:init', () => {
        queueMicrotask(() => {
            const sessions = Alpine.store('sessions');
            const chat = Alpine.store('chat');
            const layout = Alpine.store('layout');
            if (!sessions || !chat || sessions.__webShellHydrationInstalled) return;
            sessions.__webShellHydrationInstalled = true;

            const originalLoad = sessions.load.bind(sessions);
            sessions.load = async function (...args) {
                const result = await originalLoad(...args);
                const current = this.current || chat.currentName;
                if (current) {
                    if (chat.currentName !== current) chat.focus(current);
                    const slot = chat._slot(current);
                    if (!slot.historyHydrated && !slot.busy) {
                        await chat.loadHistory(current, { force: true });
                    }
                }
                return result;
            };

            const originalSwitchTo = sessions.switchTo.bind(sessions);
            sessions.switchTo = async function (name) {
                const result = await originalSwitchTo(name);
                if (this.current !== name) return result;
                const slot = chat._slot(name);
                if (slot.busy) {
                    if (!slot.historyHydrated) slot.pendingReload = true;
                    return result;
                }
                await chat.loadHistory(name, { force: true });
                return result;
            };

            // The panel now carries its own navigation rail; give it enough
            // room for that rail plus useful content on desktop-sized views.
            if (layout && window.innerWidth >= 1100 && Number(layout.panelWidth || 0) < 500) {
                layout.panelWidth = 520;
            }
        });
    });
})();
