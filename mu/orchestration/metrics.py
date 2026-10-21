"""Node history (charts): what CPU, memory, disk, load, network and disk I/O did over time.

Nodes running agent 1.3.0 or newer put a utilisation sample on their poll about every 20 seconds
(``node/collectors.py MetricsSampler``). The hub keeps it (``record``), stamped with the master's
clock; the scheduler rolls samples older than two days into 5-minute averages and keeps those for
30 days (``rollup``). ``series`` answers a chart: one node or many (a group, the fleet), over a
range, in at most ``MAX_POINTS`` buckets.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional

METRICS_SINCE = "1.3.0"
RAW_KEEP_S = 2 * 24 * 3600
ROLLUP_S = 300
ROLLUP_KEEP_S = 30 * 24 * 3600
ROLLUP_EVERY_S = 3600
MAX_POINTS = 240
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 24 * 3600, "30d": 30 * 24 * 3600}

# key: (label, unit, how many nodes combine: avg | max | sum)
FIELDS = {
    "cpu_pct": ("CPU", "percent", "avg"),
    "mem_pct": ("Memory", "percent", "avg"),
    "disk_pct": ("Disk (fullest)", "percent", "max"),
    "load1": ("Load", "number", "avg"),
    "net_rx_bps": ("Network in", "bytes_per_s", "sum"),
    "net_tx_bps": ("Network out", "bytes_per_s", "sum"),
    "disk_read_bps": ("Disk read", "bytes_per_s", "sum"),
    "disk_write_bps": ("Disk write", "bytes_per_s", "sum"),
}
_LIMITS = {"percent": (0.0, 100.0), "number": (0.0, 1e6), "bytes_per_s": (0.0, 1e12)}


def clean(sample: Any) -> Dict[str, float]:
    """Only known keys with finite numbers in range (a node is trusted with its numbers, not
    with the store)."""
    out: Dict[str, float] = {}
    if not isinstance(sample, Mapping):
        return out
    for key, (_label, unit, _how) in FIELDS.items():
        value = sample.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        lo, hi = _LIMITS[unit]
        out[key] = round(min(hi, max(lo, float(value))), 2)
    return out


def record(store, node_id: str, sample: Any) -> bool:
    values = clean(sample)
    if not values:
        return False
    store.add_metrics(node_id, store.now(), values)
    return True


def rollup(store, now: float) -> int:
    return store.rollup_metrics(now - RAW_KEEP_S, ROLLUP_S, now - ROLLUP_KEEP_S)


def _bucket_size(range_s: int) -> int:
    for size in (20, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800):
        if range_s / size <= MAX_POINTS:
            return size
    return 10800


def series(store, node_ids: Iterable[str], range_key: str = "6h", now: Optional[float] = None) -> Dict[str, Any]:
    """Chart data for one node or many: {range, bucket_s, points: [{t, <field>…}], fields}. Each
    node's samples average within a bucket; many nodes combine per field (avg, max or sum)."""
    ids = [str(n) for n in node_ids]
    range_s = RANGES.get(range_key) or RANGES["6h"]
    now = store.now() if now is None else now
    since = now - range_s
    bucket = max(_bucket_size(range_s), ROLLUP_S if range_s > RAW_KEEP_S else 0)
    rows = store.list_metrics(ids, since, now, 0)
    if since < now - RAW_KEEP_S:
        rows = store.list_metrics(ids, since, now - RAW_KEEP_S, ROLLUP_S) + rows
    per: Dict[float, Dict[str, Dict[str, List[float]]]] = {}
    for node_id, ts, data in rows:
        b = float(int(ts // bucket) * bucket)
        slot = per.setdefault(b, {}).setdefault(node_id, {})
        for key, value in data.items():
            if key in FIELDS and isinstance(value, (int, float)):
                slot.setdefault(key, []).append(float(value))
    points: List[Dict[str, Any]] = []
    for b in sorted(per):
        point: Dict[str, Any] = {"t": b}
        for key, (_label, _unit, how) in FIELDS.items():
            node_values = [sum(v[key]) / len(v[key]) for v in per[b].values() if v.get(key)]
            if not node_values:
                continue
            combined = max(node_values) if how == "max" else sum(node_values) if how == "sum" else sum(node_values) / len(node_values)
            point[key] = round(combined, 2)
        points.append(point)
    return {"range": range_key if range_key in RANGES else "6h", "ranges": list(RANGES), "bucket_s": bucket,
            "since": since, "until": now, "nodes": len(ids), "points": points,
            "fields": [{"key": k, "label": label, "unit": unit, "combine": how} for k, (label, unit, how) in FIELDS.items()]}


SPARK_FIELDS = ("cpu_pct", "mem_pct", "net_rx_bps", "net_tx_bps", "disk_read_bps", "disk_write_bps")
SPARK_POINTS = 48


def per_node(store, node_ids: Iterable[str], range_key: str = "6h", now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Small per-node series for the dashboard's health rows (one request for a whole group):
    {node_id: {points: [{t, cpu_pct, mem_pct, net_bps, io_bps}], latest: {…}, peak: {…}}} in at most
    SPARK_POINTS buckets. Network and disk I/O are in + out (read + write) together."""
    ids = [str(n) for n in node_ids]
    range_s = RANGES.get(range_key) or RANGES["6h"]
    now = store.now() if now is None else now
    since = now - range_s
    bucket = max(_bucket_size(range_s), int(range_s / SPARK_POINTS) or 1)
    rows = store.list_metrics(ids, max(since, now - RAW_KEEP_S), now, 0)
    if since < now - RAW_KEEP_S:
        rows = store.list_metrics(ids, since, now - RAW_KEEP_S, ROLLUP_S) + rows
    acc: Dict[str, Dict[float, Dict[str, List[float]]]] = {}
    for node_id, ts, data in rows:
        b = float(int(ts // bucket) * bucket)
        slot = acc.setdefault(node_id, {}).setdefault(b, {})
        for key in SPARK_FIELDS:
            if isinstance(data.get(key), (int, float)):
                slot.setdefault(key, []).append(float(data[key]))
    out: Dict[str, Dict[str, Any]] = {}
    for node_id in ids:
        points = []
        for b in sorted(acc.get(node_id, {})):
            v = {k: sum(vals) / len(vals) for k, vals in acc[node_id][b].items()}
            point: Dict[str, Any] = {"t": b}
            for name, keys in (("cpu_pct", ("cpu_pct",)), ("mem_pct", ("mem_pct",)),
                               ("net_bps", ("net_rx_bps", "net_tx_bps")), ("io_bps", ("disk_read_bps", "disk_write_bps"))):
                present = [v[k] for k in keys if k in v]
                if present:
                    point[name] = round(sum(present), 1)
            points.append(point)
        points = points[-SPARK_POINTS:]       # aligned buckets can straddle one extra: keep the newest
        latest = {k: next((p[k] for p in reversed(points) if k in p), None) for k in ("cpu_pct", "mem_pct", "net_bps", "io_bps")}
        peak = {k: max((p[k] for p in points if k in p), default=None) for k in ("cpu_pct", "mem_pct", "net_bps", "io_bps")}
        out[node_id] = {"points": points, "latest": latest, "peak": peak}
    return out


def reporting(nodes: Iterable[Any]) -> Dict[str, List[str]]:
    """Which nodes can report history (agent ≥ 1.3.0) and which need an agent update first."""
    from .node import version_tuple
    ready, outdated = [], []
    for n in nodes:
        (ready if version_tuple(n.agent_version or "") >= version_tuple(METRICS_SINCE) else outdated).append(n.name)
    return {"reporting": ready, "needs_update": outdated}


# ------------------------------------------------------------------ text (terminal sparklines)

SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: List[Optional[float]], width: int = 48, top: Optional[float] = None) -> str:
    """Values as block characters, resampled to ``width`` (gaps as spaces); ``top`` fixes the
    scale (100 for percentages), else the series' own maximum."""
    if not values:
        return ""
    if len(values) > width:
        step = len(values) / width
        values = [_mean(values[int(i * step):max(int(i * step) + 1, int((i + 1) * step))]) for i in range(width)]
    real = [v for v in values if v is not None]
    hi = top if top else (max(real) if real else 0)
    if not real or hi <= 0:
        return "".join(" " if v is None else SPARK[0] for v in values)
    return "".join(" " if v is None else SPARK[min(len(SPARK) - 1, int(v / hi * (len(SPARK) - 1) + 0.5))] for v in values)


def _mean(values: List[Optional[float]]) -> Optional[float]:
    real = [v for v in values if v is not None]
    return sum(real) / len(real) if real else None


def human(value: Optional[float], unit: str) -> str:
    if value is None:
        return "–"
    if unit == "percent":
        return f"{value:.0f}%"
    if unit == "bytes_per_s":
        for size, suffix in ((1 << 30, "GB/s"), (1 << 20, "MB/s"), (1 << 10, "kB/s")):
            if value >= size:
                return f"{value / size:.1f} {suffix}"
        return f"{value:.0f} B/s"
    return f"{value:.2f}"


def text_lines(data: Mapping[str, Any], width: int = 48) -> List[str]:
    """One line per field: label, sparkline, latest / average / maximum."""
    points = data.get("points") or []
    if not points:
        return []
    lines = []
    for field in data.get("fields") or []:
        key, unit = field["key"], field["unit"]
        values = [p.get(key) for p in points]
        real = [v for v in values if v is not None]
        if not real:
            continue
        line = sparkline(values, width, 100.0 if unit == "percent" else None)
        lines.append(f"{field['label']:<15} {line}  now {human(real[-1], unit)} · avg {human(sum(real) / len(real), unit)} · max {human(max(real), unit)}")
    return lines
