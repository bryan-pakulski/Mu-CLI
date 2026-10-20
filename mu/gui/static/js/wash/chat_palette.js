/* Chat page (/) commands for the Wash command palette (palette.js).
 *
 * Registers live sources — sessions, workspace views, actions and slash
 * commands — read from the Alpine stores each time the palette draws.
 * Choosing a slash command puts it in the composer rather than running it.
 */
(function () {
    'use strict';
    if (!window.WashPalette) return;

    function store(name) {
        try { return window.Alpine ? window.Alpine.store(name) : null; } catch (e) { return null; }
    }

    function focusComposer(value) {
        var input = document.querySelector('.composer textarea');
        if (!input) return;
        if (typeof value === 'string') {
            input.value = value;
            input.dispatchEvent(new Event('input'));
        }
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
    }

    WashPalette.register('chat:sessions', function () {
        var sessions = store('sessions');
        var chat = store('chat');
        if (!sessions || !Array.isArray(sessions.list)) return [];
        return sessions.list.map(function (s) {
            var current = chat && s.name === chat.currentName;
            return {
                id: 'session:' + s.name,
                group: 'Sessions',
                label: s.name,
                hint: current ? 'Current' : (s.session_type || ''),
                icon: s.session_type === 'container' ? 'box' : (s.session_type === 'chat' ? 'message-circle' : 'folder'),
                enabled: !current,
                run: function () { sessions.switchTo(s.name); },
            };
        });
    }, { priority: 10 });

    WashPalette.register('chat:views', function () {
        var mode = store('mode');
        var layout = store('layout');
        if (!mode) return [];
        var all = (mode.modes || []).concat(mode.views || []);
        var seen = {};
        return all.filter(function (item) {
            if (!item || item.external || item.disabled || seen[item.name]) return false;
            seen[item.name] = true;
            return (mode.panelModes || []).indexOf(item.name) >= 0;
        }).map(function (item) {
            return {
                id: 'view:' + item.name,
                group: 'Navigate',
                label: 'Show ' + (item.display_name || item.name) + ' panel',
                icon: 'panel-right',
                keywords: 'panel stage view ' + item.name,
                run: function () { mode.setView(item.name); if (layout) layout.setPanel(true); },
            };
        });
    }, { priority: 20 });

    WashPalette.register('chat:actions', function () {
        var items = [
            { id: 'act:compose', label: 'Focus the composer', icon: 'pencil', key: '/', run: function () { focusComposer(); } },
            { id: 'act:thread', label: 'New thread', icon: 'git-branch', run: function () { var t = store('threads'); if (t) t.create(); } },
            { id: 'act:settings', label: 'Open settings', icon: 'settings', run: function () { var i = store('inspector'); if (i) i.openDrawer(); } },
            { id: 'act:new-session', label: 'New session…', icon: 'plus', run: function () { var s = store('sessions'); if (s) s.deactivate(); } },
        ];
        var chat = store('chat');
        if (chat && chat.busy) items.unshift({ id: 'act:stop', label: 'Stop generating', icon: 'square', key: 'Esc', run: function () { chat.interrupt(); } });
        var prompts = store('prompts');
        if (prompts && prompts.visibleQueue && prompts.visibleQueue.length) {
            items.unshift({ id: 'act:review', label: 'Review pending approval', icon: 'shield', run: function () { prompts.focusCard(); } });
        }
        var plan = store('planMode');
        if (plan) {
            items.push(plan.active
                ? { id: 'act:plan-off', label: 'Turn plan mode off', icon: 'unlock', run: function () { plan.turnOff({ confirmFirst: true }); } }
                : { id: 'act:plan-on', label: 'Turn plan mode on (read-only)', icon: 'lock', run: function () { plan.set(true); } });
        }
        var yolo = store('yolo');
        if (yolo && yolo.active) {
            items.push({ id: 'act:yolo-off', label: 'Turn auto-approve off', icon: 'shield-check', run: function () { yolo.toggle(); } });
        }
        return items.map(function (item) { item.group = 'Actions'; return item; });
    }, { priority: 30 });

    WashPalette.register('chat:commands', function () {
        var cc = store('cmdComplete');
        if (!cc || !Array.isArray(cc.commands)) return [];
        return cc.commands.map(function (c) {
            return {
                id: 'cmd:' + c.name,
                group: 'Commands',
                label: c.name,
                hint: c.help || '',
                keywords: (c.aliases || []).join(' '),
                icon: 'command',
                run: function () { focusComposer(c.name + ' '); },
            };
        });
    }, { priority: 40 });
})();
