/* /design gallery: chat component states and the canned conversation replay.
 *
 * 1. Fills every [data-g-toolcards] / [data-g-diff] block through the real
 *    renderers (renderToolCards, WashDiff.renderDiffHTML).
 * 2. Replays a canned SSE sequence through app.js routeEvent() into the live
 *    fragments/chat.html in the "Chat" section: user message, streaming
 *    assistant text with a code block, thinking, tool calls running → done,
 *    grouped reads, a proposed diff, an approval prompt, a YOLO auto-approval
 *    breadcrumb and a toast. app.js is loaded in replay-only mode
 *    (window.MUCLI_GALLERY_REPLAY), so nothing talks to a session.
 */
(function () {
    'use strict';

    var ORIGINAL = [
        'class AuthMiddleware:',
        '    def __call__(self, request):',
        '        token = request.headers.get("Authorization")',
        '        if not token:',
        '            raise Unauthorized()',
        '        return self.app(request)',
        '',
    ].join('\n');
    var MODIFIED = [
        'class AuthMiddleware:',
        '    def __call__(self, request):',
        '        token = await self._ensure_fresh(request)',
        '        if not token:',
        '            raise Unauthorized("missing or expired token")',
        '        return self.app(request)',
        '',
    ].join('\n');

    function sampleEvents(now) {
        return {
            states: [
                { id: 'g1', kind: 'tool_call', name: 'read_file', args: { path: 'src/auth/middleware.py' }, at: now - 9000 },
                { id: 'g2', kind: 'tool_result', name: 'read_file', rawText: 'class AuthMiddleware: …', at: now - 8800 },
                { id: 'g3', kind: 'tool_call', name: 'read_file', args: { path: 'src/auth/refresh.py' }, at: now - 8000 },
                { id: 'g4', kind: 'tool_result', name: 'read_file', rawText: 'def refresh_token(req): …', at: now - 7900 },
                { id: 'g5', kind: 'tool_call', name: 'read_file', args: { path: 'src/auth/tokens.py', start_line: 1, end_line: 120 }, at: now - 7000 },
                { id: 'g6', kind: 'tool_result', name: 'read_file', rawText: 'TOKENS = {}', at: now - 6900 },
                { id: 'g7', kind: 'tool_call', name: 'search_for_string', args: { query: 'refresh_token(', path: 'src/' }, at: now - 5000 },
                { id: 'g8', kind: 'tool_result', name: 'search_for_string', rawText: 'src/auth/refresh.py:12', at: now - 4800 },
                { id: 'g9', kind: 'tool_call', name: 'write_file', args: { path: 'src/server/routes.py' }, at: now - 4000 },
                { id: 'g10', kind: 'tool_result', name: 'write_file', rawText: '{"error_code": "plan_mode_blocked"}', planBlocked: true, at: now - 3900 },
                { id: 'g11', kind: 'tool_call', name: 'web_search', args: { query: 'jwt refresh rotation' }, at: now - 3000 },
                { id: 'g12', kind: 'tool_result', name: 'web_search', rawText: 'Error: network unreachable', at: now - 2600 },
                { id: 'g13', kind: 'tool_call', name: 'apply_patch', args: { path: 'src/auth/middleware.py' }, at: now - 1500 },
                { id: 'g14', kind: 'tool_result', name: 'apply_patch', rawText: 'Applied 1 hunk', at: now - 300 },
                { id: 'g15', kind: 'tool_call', name: 'bash', args: { command: 'pytest tests/auth -q' }, at: now - 4100 },
            ],
            expanded: [
                { id: 'gx1', kind: 'tool_call', name: 'bash', args: { command: 'pytest tests/auth -q', timeout: 120 }, at: now - 4100, open: true },
                { id: 'gx2', kind: 'tool_result', name: 'bash', rawText: '....F.\nFAILED tests/auth/test_mw.py::test_expired - assert 401 == 200\n1 failed, 5 passed in 0.42s', cacheKey: 'c-7f2a', at: now - 3600 },
                { id: 'gx3', kind: 'info', text: 'Context compacted: 12 tool results folded into the result store.', at: now - 3000 },
            ],
        };
    }

    function fillStatic() {
        if (!window.WashToolCard || !window.WashDiff) return;
        var now = Date.now();
        var events = sampleEvents(now);
        document.querySelectorAll('[data-g-toolcards]').forEach(function (node) {
            var list = events[node.getAttribute('data-g-toolcards')] || [];
            node.innerHTML = window.renderToolCards(list.map(function (e) { return Object.assign({}, e); }), { running: true, now: now });
        });
        var unified = window.WashDiff.diffContents(ORIGINAL, MODIFIED, { filename: 'src/auth/middleware.py' });
        document.querySelectorAll('[data-g-diff]').forEach(function (node) {
            var kind = node.getAttribute('data-g-diff');
            node.innerHTML = window.WashDiff.renderDiffHTML(unified, {
                mode: kind === 'split' ? 'split' : 'unified',
                collapsed: kind === 'collapsed',
                maxLines: kind === 'approval' ? 12 : 0,
            });
        });
    }

    function replay() {
        if (!window.Alpine || typeof window.routeEvent !== 'function') return;
        var S = 'gallery';
        var chat = window.Alpine.store('chat');
        chat.focus(S);
        window.Alpine.store('mode').realMode = 'feature';
        window.Alpine.store('inspector').currentModel = 'claude-opus-5-5';
        var ev = function (o) { window.routeEvent(Object.assign({ session_name: S }, o)); };
        chat.addUser('Move token refresh into the auth middleware and add tests.', S);
        chat._slot(S).busy = true;
        ev({ kind: 'assistant_start', turn_id: 'ga1' });
        ev({ kind: 'assistant_delta', turn_id: 'ga1', text: "I'll read the current middleware and the refresh helper first, then propose a patch that moves `refresh_token()` behind `AuthMiddleware`.\n\n```python\ndef refresh_token(req):\n    return req.headers.get('Authorization')\n```\n" });
        ev({ kind: 'assistant_end', turn_id: 'ga1' });
        ev({ kind: 'thinking_delta', text: 'The refresh helper is called from three places; moving it into the middleware means the routes can drop their own checks.' });
        ['src/auth/middleware.py', 'src/auth/refresh.py', 'src/auth/tokens.py', 'tests/auth/test_mw.py'].forEach(function (p) {
            ev({ kind: 'tool_call', tool_name: 'read_file', tool_args: { path: p } });
            ev({ kind: 'tool_result', tool_name: 'read_file', text: 'class AuthMiddleware:\n    pass\n' });
        });
        ev({ kind: 'tool_call', tool_name: 'search_for_string', tool_args: { query: 'refresh_token(', path: 'src/' } });
        ev({ kind: 'tool_result', tool_name: 'search_for_string', text: 'src/auth/refresh.py:12' });
        ev({ kind: 'tool_call', tool_name: 'bash', tool_args: { command: 'pytest tests/auth -q' } });
        ev({ kind: 'diff', filename: 'src/auth/middleware.py', original: ORIGINAL, new: MODIFIED });
        ev({ kind: 'prompt_resolved', id: 'auto-g1', auto: true, tool_name: 'bash', target: 'ruff check src/auth' });
        ev({ kind: 'prompt', id: 'gp1', prompt: { shape: 'tool_approval', tool_name: 'write_file', tool_args: { path: 'src/auth/middleware.py', content: MODIFIED }, session_name: S } });
        window.Alpine.store('toast').show('Patch applied to 1 file', 'success', 600000);
        // Leave the transcript at the top so the screenshot shows the story.
        setTimeout(function () {
            var history = document.querySelector('#g-chat .chat-history');
            if (history) history.scrollTop = 0;
        }, 300);
    }

    document.addEventListener('alpine:initialized', function () {
        fillStatic();
        setTimeout(replay, 50);
    });
})();
