/* Settings → Devices (mu/gui/devices.py): pair a phone (QR + code) and revoke paired devices.
 * Only this computer can do either (the routes are loopback-only and CSRF-checked). */
(function () {
    'use strict';
    const box = document.querySelector('[data-devices]');
    if (!box) return;
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const list = box.querySelector('[data-device-list]');
    const pair = box.querySelector('[data-pair]');
    let csrf = null, timer = 0, expiresAt = 0;

    async function token() {
        if (csrf) return csrf;
        const res = await fetch('/api/csrf', { credentials: 'same-origin' });
        csrf = await res.json();
        return csrf;
    }
    async function call(method, path, body) {
        const t = method === 'GET' ? null : await token();
        const res = await fetch(path, {
            method, credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', ...(t ? { [t.header]: t.token } : {}) },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        return data;
    }
    const when = ts => (ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'never');

    async function refresh() {
        let devices = [];
        try { devices = (await call('GET', '/api/devices')).devices || []; }
        catch (err) { list.innerHTML = `<li class="w-row"><span class="w-row__main"><span class="w-row__sub">${esc(err.message)}</span></span></li>`; return []; }
        list.innerHTML = devices.length ? devices.map(d => `<li class="w-row">
                <span class="w-row__main"><span class="w-row__title">${esc(d.name)}</span>
                <span class="w-row__sub">Last seen ${esc(when(d.last_seen))}${d.address ? ` · ${esc(d.address)}` : ''} · paired ${esc(when(d.created_at))}</span></span>
                <span class="w-row__actions"><button type="button" class="w-textbtn" data-tone="danger" data-revoke="${esc(d.id)}" data-name="${esc(d.name)}">Revoke</button></span></li>`).join('')
            : '<li class="w-row"><span class="w-row__main"><span class="w-row__sub">No paired devices yet.</span></span></li>';
        list.querySelectorAll('[data-revoke]').forEach(b => {
            b.onclick = async () => {
                if (!window.confirm(`Revoke ${b.dataset.name}? It's signed out at once and needs pairing again.`)) return;
                try { await call('DELETE', `/api/devices/${encodeURIComponent(b.dataset.revoke)}`); window.WashToast?.show(`${b.dataset.name} revoked`, 'ok'); }
                catch (err) { window.WashToast?.show(err.message, 'danger'); }
                refresh();
            };
        });
        return devices;
    }

    function tick() {
        const left = Math.max(0, Math.round(expiresAt - Date.now() / 1000));
        box.querySelector('[data-pair-expiry]').textContent = left
            ? `Valid for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}, one use.` : 'Expired. Start a new pairing.';
        if (!left) clearInterval(timer);
    }
    async function start() {
        let p;
        try { p = await call('POST', '/api/devices/pairing', {}); }
        catch (err) { window.WashToast?.show(err.message, 'danger'); return; }
        pair.hidden = false;
        box.querySelector('[data-pair-qr]').innerHTML = p.qr_svg || '';           // server-generated SVG (segno)
        box.querySelector('[data-pair-code]').textContent = p.display;
        box.querySelector('[data-pair-urls]').textContent = p.urls?.length ? `GUI address: ${p.urls.join(', ')}` : '';
        box.querySelector('[data-pair-loopback]').hidden = !p.listening_on_loopback;
        expiresAt = p.expires_at;
        clearInterval(timer);
        timer = setInterval(tick, 1000);
        tick();
        /* A paired phone shows up in the list. */
        const known = new Set((await refresh()).map(d => d.id));
        const poll = setInterval(async () => {
            if (pair.hidden || Date.now() / 1000 > expiresAt) { clearInterval(poll); return; }
            const now = await refresh();
            const added = now.find(d => !known.has(d.id));
            if (added) {
                pair.hidden = true; clearInterval(poll); clearInterval(timer);
                window.WashToast?.show(`${added.name} paired`, 'ok');
            }
        }, 3000);
    }
    /* A paired browser on another machine: it can't pair or revoke others (this computer only),
       so it sees which device it is and can sign itself out (back to the pairing page). */
    async function asRemote() {
        let me = null;
        try { me = await call('GET', '/api/devices/self'); } catch (_) { return false; }
        if (!me || !me.device) return false;
        box.querySelector('[data-pair-start]').hidden = true;
        list.innerHTML = `<li class="w-row"><span class="w-row__main"><span class="w-row__title">This browser: ${esc(me.device.name)}</span>
            <span class="w-row__sub">Paired ${esc(when(me.device.created_at))}. Pairing and revoking other devices happen on the computer running μcli.</span></span>
            <span class="w-row__actions"><button type="button" class="w-textbtn" data-tone="danger" data-sign-out>Sign out</button></span></li>`;
        list.querySelector('[data-sign-out]').onclick = async () => {
            if (!window.confirm('Sign this browser out? It needs a new pairing code to come back.')) return;
            try { await call('DELETE', '/api/devices/self'); (window.top || window).location.reload(); }   // the whole shell, not just this view
            catch (err) { window.WashToast?.show(err.message, 'danger'); }
        };
        return true;
    }

    box.querySelector('[data-pair-start]').onclick = start;
    box.querySelector('[data-pair-cancel]').onclick = async () => {
        pair.hidden = true; clearInterval(timer);
        try { await call('DELETE', '/api/devices/pairing'); } catch (_) { /* expired anyway */ }
    };
    asRemote().then(remote => { if (!remote) refresh(); });
})();
