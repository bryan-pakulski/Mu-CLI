/* MuCLI web shell reliability glue.
 *
 * Keeps session hydration authoritative after the session focus is known,
 * preserves live transcripts across reconnects, and adds lightweight wall-clock
 * timing for completed model turns without changing backend/API semantics.
 */
(function () {
    function installConversationStylesheet() {
        if (document.getElementById('mucli-conversation-css')) return;
        const link = document.createElement('link');
        link.id = 'mucli-conversation-css';
        link.rel = 'stylesheet';
        link.href = '/static/css/conversation.css';
        document.head.appendChild(link);
    }

    installConversationStylesheet();

    function hasLiveTranscript(slot) {
        return (slot.turns || []).some(turn => (
            (turn.role === 'assistant' && turn.streaming)
            || (turn.role === 'trace' && turn.running && (turn.events || []).length > 0)
            || (turn.role === 'subagent_panel' && turn.running)
        ));
    }

    function lastFinalAssistant(slot) {
        for (let index = slot.turns.length - 1; index >= 0; index -= 1) {
            const turn = slot.turns[index];
            if (turn.role === 'user') break;
            if (turn.role === 'assistant' && !turn.streaming && String(turn.text || '').trim()) {
                return turn;
            }
        }
        return null;
    }

    function workedFingerprintMap(slot) {
        const map = new Map();
        let userText = '';
        for (const turn of slot.turns || []) {
            if (turn.role === 'user') userText = String(turn.text || '');
            if (turn.role === 'assistant' && turn.workedMs && !turn.streaming) {
                map.set(`${userText}\u0000${String(turn.text || '')}`, Number(turn.workedMs));
            }
        }
        return map;
    }

    function restoreWorkedFingerprints(slot, map) {
        if (!map || !map.size) return;
        let userText = '';
        for (const turn of slot.turns || []) {
            if (turn.role === 'user') userText = String(turn.text || '');
            if (turn.role === 'assistant' && !turn.streaming) {
                const value = map.get(`${userText}\u0000${String(turn.text || '')}`);
                if (value) turn.workedMs = value;
            }
        }
    }

    function formatWorkedDuration(ms) {
        const totalSeconds = Math.max(0, Math.round(Number(ms || 0) / 1000));
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return minutes > 0
            ? `Worked for ${minutes}m ${seconds}s`
            : `Worked for ${seconds}s`;
    }

    function decorateWorkedDurations(chat, name) {
        const target = name || chat.currentName;
        if (!target || target !== chat.currentName) return;
        requestAnimationFrame(() => requestAnimationFrame(() => {
            if (target !== chat.currentName) return;
            const slot = chat._slot(target);
            const assistants = (slot.turns || []).filter(turn => turn.role === 'assistant' && !turn.streaming);
            const nodes = Array.from(document.querySelectorAll('.chat-history > .turn-wrap > .msg.assistant'));
            assistants.forEach((turn, index) => {
                const node = nodes[index];
                if (!node) return;
                let crumb = node.querySelector(':scope > .turn-worked-breadcrumb');
                if (!turn.workedMs) {
                    if (crumb) crumb.remove();
                    return;
                }
                if (!crumb) {
                    crumb = document.createElement('div');
                    crumb.className = 'turn-worked-breadcrumb';
                    node.appendChild(crumb);
                }
                crumb.textContent = formatWorkedDuration(turn.workedMs);
            });
        }));
    }

    document.addEventListener('alpine:init', () => {
        queueMicrotask(() => {
            const sessions = Alpine.store('sessions');
            const chat = Alpine.store('chat');
            const layout = Alpine.store('layout');
            if (!sessions || !chat || sessions.__webShellHydrationInstalled) return;
            sessions.__webShellHydrationInstalled = true;

            /*
             * The core bootstrap historically calls chat.loadHistory() before
             * sessions.load(). Suppress that provisional unscoped request: it can
             * hydrate an empty/default slot and then make the real session look
             * permanently empty. The sessions wrapper below performs the first
             * authoritative hydration after /api/sessions has resolved focus.
             */
            const coreLoadHistory = chat.loadHistory.bind(chat);
            chat.loadHistory = async function (name, options = {}) {
                const target = name || this.currentName || sessions.current || null;
                if (!target) return;

                const slot = this._slot(target);
                const worked = workedFingerprintMap(slot);
                const wasBusy = !!slot.busy;

                // A page refresh during a running turn starts with only an empty
                // busy-trace placeholder. It is safe (and important) to hydrate the
                // durable transcript beneath that placeholder. Once real live deltas
                // exist, never replace them mid-turn.
                if (wasBusy && hasLiveTranscript(slot)) {
                    slot.pendingReload = true;
                    return;
                }

                const result = await coreLoadHistory(target, {
                    ...options,
                    force: true,
                });

                const hydrated = this._slot(target);
                restoreWorkedFingerprints(hydrated, worked);
                if (hydrated.historyHydrated) hydrated.__authoritativeHydrated = true;

                if (wasBusy) {
                    hydrated.busy = true;
                    this._ensureBusyTrace(hydrated);
                }
                decorateWorkedDurations(this, target);
                return result;
            };

            const originalLoad = sessions.load.bind(sessions);
            sessions.load = async function (...args) {
                const result = await originalLoad(...args);
                const current = this.current || chat.currentName;
                if (current) {
                    if (chat.currentName !== current) chat.focus(current);
                    const slot = chat._slot(current);
                    if (!slot.__authoritativeHydrated) {
                        await chat.loadHistory(current, { force: true });
                    }
                    decorateWorkedDurations(chat, current);
                }
                return result;
            };

            const originalSwitchTo = sessions.switchTo.bind(sessions);
            sessions.switchTo = async function (name) {
                const result = await originalSwitchTo(name);
                if (this.current !== name) return result;
                const slot = chat._slot(name);
                slot.__authoritativeHydrated = false;
                await chat.loadHistory(name, { force: true });
                decorateWorkedDurations(chat, name);
                return result;
            };

            const originalFocus = chat.focus.bind(chat);
            chat.focus = function (name) {
                const result = originalFocus(name);
                decorateWorkedDurations(this, name);
                return result;
            };

            const originalSend = chat.send.bind(chat);
            chat.send = async function (text) {
                const name = this.currentName;
                const slot = this._slot(name);
                const trimmed = String(text || '').trim();
                const track = !!name && !this.isSlashCommand(trimmed) && this.canSend(trimmed);
                if (track && !slot.busy) slot.__turnStartedAt = Date.now();
                const result = await originalSend(text);
                if (track && !slot.busy) delete slot.__turnStartedAt;
                return result;
            };

            const originalAddCommandResult = chat.addCommandResult.bind(chat);
            chat.addCommandResult = function (result, name) {
                this._slot(name).__skipWorkedFinish = true;
                return originalAddCommandResult(result, name);
            };

            const originalFinishTurn = chat.finishTurn.bind(chat);
            chat.finishTurn = function (name) {
                const slot = this._slot(name);
                const startedAt = Number(slot.__turnStartedAt || 0);
                const skipWorked = !!slot.__skipWorkedFinish;
                slot.__skipWorkedFinish = false;

                const result = originalFinishTurn(name);

                if (startedAt && !skipWorked) {
                    const finalResponse = lastFinalAssistant(slot);
                    if (finalResponse) {
                        finalResponse.workedMs = Math.max(0, Date.now() - startedAt);
                        delete slot.__turnStartedAt;
                    } else if (!slot.busy) {
                        // Terminal error/interruption with no completed assistant
                        // response is not a fully completed turn.
                        delete slot.__turnStartedAt;
                    }
                }
                decorateWorkedDurations(this, name);
                return result;
            };

            // The RHS now uses compact selectors. Keep a useful desktop default
            // without over-allocating space to navigation chrome.
            if (layout && window.innerWidth >= 1100 && Number(layout.panelWidth || 0) < 500) {
                layout.panelWidth = 520;
            }
        });
    });
})();
