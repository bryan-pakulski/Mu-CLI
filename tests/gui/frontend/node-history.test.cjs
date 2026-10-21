// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.test.cjs
// Node history charts (static/js/orchestration/history.js): the chart model scales, breaks the
// line at gaps, humanises rates and summarises each chart in words for screen readers.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');

function model() {
    const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only', url: 'http://127.0.0.1:30311/orchestration' });
    dom.window.eval(readFileSync(resolve(root, 'mu/gui/static/js/orchestration/history.js'), 'utf8'));
    return dom.window.OrchHistory;
}
const chart = (H, id) => H.CHARTS.find(c => c.id === id);

test('percentages sit on 0–100; other units on a round scale above the peak', () => {
    const H = model();
    assert.equal(H.niceMax(37, 'percent'), 100);
    assert.equal(H.niceMax(3.4, 'number'), 5);
    assert.equal(H.niceMax(1500, 'bytes_per_s'), 2000);
    assert.equal(H.niceMax(0, 'bytes_per_s'), 1);
});

test('a missing value or a long silence breaks the line', () => {
    const H = model();
    const pts = [{ t: 0, cpu_pct: 1 }, { t: 20, cpu_pct: 2 }, { t: 40 }, { t: 60, cpu_pct: 4 }, { t: 80, cpu_pct: 5 }, { t: 400, cpu_pct: 9 }];
    const runs = H.segments(pts, 'cpu_pct', 20);
    assert.equal(JSON.stringify(runs), JSON.stringify([[[0, 1], [20, 2]], [[60, 4], [80, 5]], [[400, 9]]]));
});

test('rates read as people say them, and each chart has a sentence', () => {
    const H = model();
    assert.equal(H.human(512, 'bytes_per_s'), '512 B/s');
    assert.equal(H.human(1536, 'bytes_per_s'), '1.5 kB/s');
    assert.equal(H.human(3.2 * 1048576, 'bytes_per_s'), '3.2 MB/s');
    assert.equal(H.human(42.4, 'percent'), '42%');
    assert.equal(H.human(null, 'percent'), '—');
    const data = { since: 0, until: 100, bucket_s: 20, points: [{ t: 0, cpu_pct: 10, net_rx_bps: 2048, net_tx_bps: 1024 }, { t: 20, cpu_pct: 40, net_rx_bps: 4096, net_tx_bps: 1024 }] };
    const cpu = H.chartModel(chart(H, 'cpu'), data);
    assert.equal(H.summary(chart(H, 'cpu'), cpu, '6h'), 'CPU over the last 6 hours: now 40%, average 25%, peak 40%.');
    const net = H.chartModel(chart(H, 'net'), data);
    assert.equal(net.max, 5000);
    assert.equal(H.summary(chart(H, 'net'), net, '1h'), 'Network over the last hour: in now 4.0 kB/s, peak 4.0 kB/s; out now 1.0 kB/s, peak 1.0 kB/s.');
    const io = H.chartModel(chart(H, 'io'), data);
    assert.ok(io.empty);
    assert.equal(H.summary(chart(H, 'io'), io, '24h'), 'Disk I/O over the last 24 hours: no samples.');
});

test('the readout finds the nearest bucket with a value', () => {
    const H = model();
    const pts = [{ t: 0, cpu_pct: 1 }, { t: 20 }, { t: 40, cpu_pct: 3 }];
    assert.equal(H.nearestIndex(pts, ['cpu_pct'], 18), 0);
    assert.equal(H.nearestIndex(pts, ['cpu_pct'], 31), 2);
    assert.equal(H.nearestIndex(pts, ['mem_pct'], 0), -1);
});

// Per-node sparklines in the dashboard's Health rows: hot CPU / memory read hot on their own
// 0–100 scale, network and disk I/O compare across the scope, and the list sorts busiest first.
test('each node gets sparklines: absolute CPU and memory, shared network scale, hot values toned', () => {
    const H = model();
    const data = { since: 0, until: 4800, range: '6h' };
    const perNode = {
        hot: { points: [{ t: 0, cpu_pct: 80, mem_pct: 50, net_bps: 1000 }, { t: 100, cpu_pct: 96, mem_pct: 91, net_bps: 4000 }],
               latest: { cpu_pct: 96, mem_pct: 91, net_bps: 4000, io_bps: null }, peak: { cpu_pct: 96, mem_pct: 91, net_bps: 4000, io_bps: null } },
        calm: { points: [{ t: 0, cpu_pct: 5, mem_pct: 20, net_bps: 100 }], latest: { cpu_pct: 5, mem_pct: 20, net_bps: 100, io_bps: null },
                peak: { cpu_pct: 5, mem_pct: 20, net_bps: 100, io_bps: null } },
        old: { points: [], latest: { cpu_pct: null, mem_pct: null, net_bps: null, io_bps: null }, peak: { cpu_pct: null, mem_pct: null, net_bps: null, io_bps: null } },
    };
    const scales = H.sparkScales(perNode);
    assert.equal(scales.cpu_pct, 100);
    assert.equal(scales.net_bps, 5000);                                    // the busiest node sets the shared scale
    const hot = H.nodeSparks(perNode.hot, scales, data);
    const byKey = Object.fromEntries(hot.map(s => [s.key, s]));
    assert.equal(byKey.cpu_pct.tone, 'danger');
    assert.equal(byKey.mem_pct.tone, 'attention');
    assert.equal(byKey.cpu_pct.text, 'CPU 96%');
    assert.equal(byKey.net_bps.text, 'Network 3.9 kB/s');
    assert.ok(byKey.cpu_pct.d.startsWith('M'));
    assert.equal(byKey.io_bps.d, '');                                       // no samples, no line
    const calm = H.nodeSparks(perNode.calm, scales, data);
    assert.equal(calm[0].tone, '');
    assert.ok(/h1\.5$/.test(calm[0].d), 'a single sample is a tick, not nothing');
    assert.equal(H.sparkSummary('hot-1', hot, '6h'), 'hot-1, last 6 hours: cpu now 96%, memory now 91%, network now 3.9 kB/s.');
    assert.equal(H.sparkSummary('old-1', H.nodeSparks(perNode.old, scales, data), '6h'), 'old-1: no history.');
});

test('the Health list sorts busiest first by the chosen figure, nodes without one last', () => {
    const H = model();
    const perNode = { a: { latest: { cpu_pct: 10, net_bps: 900 } }, b: { latest: { cpu_pct: 70, net_bps: 5 } }, c: { latest: { cpu_pct: null } } };
    assert.deepEqual([...H.sortNodes(['c', 'a', 'b'], perNode, 'health')], ['c', 'a', 'b']);   // health order as given
    assert.deepEqual([...H.sortNodes(['c', 'a', 'b'], perNode, 'cpu_pct')], ['b', 'a', 'c']);
    assert.deepEqual([...H.sortNodes(['c', 'a', 'b'], perNode, 'net_bps')], ['a', 'b', 'c']);
});
