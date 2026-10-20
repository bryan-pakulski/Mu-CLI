"""Fleet results: one sortable table across nodes from a tool's declared output schema.

A tool's ``outputs`` schema (manifest) says what each execution returns. ``views()`` turns it
into tables:

* **summary** — one row per node; columns are the top-level scalar properties, plus a count
  (and a short preview) for each array of scalars (e.g. ``packages``).
* **one view per array of objects** — one row per item, with the node beside it (e.g.
  ``disks`` → every mount on every node, ``certs`` → every certificate on every node).

Tools without a declared schema get columns inferred from the outputs they actually returned.
Rows are built from executions: ``run_results`` for one run (per step), ``latest_results`` for
a tool across the fleet (each node's most recent successful execution of its newest versions).
Failed or missing nodes still appear in the summary with their state, so gaps are visible.

Each view names a *key metric* (a percentage → highest first, ``days`` → soonest first,
otherwise the first number → highest first) that the UI sorts and charts by default. CSV
export writes raw values and neutralises spreadsheet formulas: outputs come from nodes and
are untrusted (they are already secret-redacted on the node and again on the master).
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCALARS = ("string", "integer", "number", "boolean")
FINISHED = ("succeeded", "failed", "timed_out", "cancelled", "unreachable", "skipped", "refused")
PREVIEW_ITEMS = 5


def _types(spec: Mapping[str, Any]) -> List[str]:
    t = spec.get("type")
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return [t] if isinstance(t, str) else []


def _kind(spec: Mapping[str, Any]) -> str:
    """scalar type (string|integer|number|boolean), 'array' or 'object' ('' when unknown)."""
    types = [t for t in _types(spec) if t != "null"]
    if not types and "enum" in spec:
        return "string"
    if any(t in ("integer", "number") for t in types):
        return "number" if "number" in types else "integer"
    for t in ("boolean", "string", "array", "object"):
        if t in types:
            return t
    return ""


_WORDS = {"pct": "%", "percent": "%", "bytes": "", "id": "ID", "ip": "IP", "cpu": "CPU", "os": "OS",
          "url": "URL", "fs": "FS", "tls": "TLS", "dns": "DNS", "ssh": "SSH", "uid": "UID", "pid": "PID"}


def label_for(key: str, spec: Optional[Mapping[str, Any]] = None) -> str:
    """``used_pct`` → "Used %", ``total_bytes`` → "Total", ``days_left`` → "Days left"."""
    if spec and isinstance(spec.get("title"), str) and spec["title"].strip():
        return spec["title"].strip()
    words = [w for w in re.split(r"[_\-\s]+", str(key)) if w]
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if low in _WORDS:
            if _WORDS[low]:
                out.append(_WORDS[low])
            continue
        out.append(w.capitalize() if i == 0 else low)
    return " ".join(out) or str(key)


def format_for(key: str, kind: str) -> str:
    """How the UI shows a value: bytes | percent | days | seconds | number | text | bool."""
    k = str(key).lower()
    if kind in ("integer", "number"):
        if k.endswith("_bytes") or k == "bytes" or k.endswith("_size"):
            return "bytes"
        if k.endswith("_pct") or k.endswith("percent") or k == "pct":
            return "percent"
        if "days" in k:
            return "days"
        if k.endswith("_s") or k.endswith("_seconds") or k.endswith("_secs"):
            return "seconds"
        return "number"
    if kind == "boolean":
        return "bool"
    return "text"


def _column(key: str, spec: Mapping[str, Any], *, kind: Optional[str] = None, path: Sequence[str] = ()) -> Dict[str, Any]:
    kind = kind or _kind(spec) or "string"
    return {"key": key, "label": label_for(key, spec), "kind": kind, "format": format_for(key, kind),
            "path": list(path) or [key]}


# Columns that name a row lead the table (manifests are stored with sorted keys, so the
# declared order is lost): identity, then other text, then flags, then numbers with the
# key metric last, beside its meter.
_IDENTITY = ("name", "mount", "path", "host", "hostname", "subject", "unit", "service", "package", "id",
             "device", "interface", "port", "user", "title")


def _ordered(columns: List[Dict[str, Any]], metric: Optional[str]) -> List[Dict[str, Any]]:
    def rank(col: Mapping[str, Any]) -> Tuple[int, int]:
        key = str(col["key"]).split("__")[0].lower()
        if col["kind"] == "string" and key in _IDENTITY:
            return (0, _IDENTITY.index(key))
        if col["kind"] == "string" and not col.get("aggregate"):
            return (1, 0)
        if col["kind"] == "boolean":
            return (2, 0)
        if col["key"] == metric:
            return (5, 0)
        if col.get("aggregate") == "preview":
            return (4, 0)
        return (3, 0)
    return sorted(columns, key=rank)


def _key_metric(columns: Sequence[Mapping[str, Any]]) -> Tuple[Optional[str], str]:
    """(column key, 'desc'|'asc') to sort and chart by default."""
    for col in columns:
        if col["format"] == "percent":
            return col["key"], "desc"
    for col in columns:
        if col["format"] == "days":
            return col["key"], "asc"
    for col in columns:
        if col["kind"] in ("integer", "number") and not col.get("meta"):
            return col["key"], "desc"
    return None, "asc"


def _infer_schema(outputs: Iterable[Any]) -> Dict[str, Any]:
    """A best-effort schema from what executions returned (tools without declared outputs)."""
    props: Dict[str, Any] = {}
    for out in outputs:
        if not isinstance(out, Mapping):
            continue
        for key, value in out.items():
            if key in props:
                continue
            if isinstance(value, bool):
                props[key] = {"type": "boolean"}
            elif isinstance(value, (int, float)):
                props[key] = {"type": "number" if isinstance(value, float) else "integer"}
            elif isinstance(value, str):
                props[key] = {"type": "string"}
            elif isinstance(value, list):
                first = next((v for v in value if v is not None), None)
                if isinstance(first, Mapping):
                    props[key] = {"type": "array", "items": _infer_schema(value)}
                else:
                    props[key] = {"type": "array", "items": {"type": "string"}}
    return {"type": "object", "properties": props}


def views(schema: Optional[Mapping[str, Any]], outputs: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Table definitions for an output schema (inferred from ``outputs`` when absent)."""
    if not isinstance(schema, Mapping) or not (schema.get("properties") or {}):
        schema = _infer_schema(outputs)
    props = schema.get("properties") or {}
    summary_cols: List[Dict[str, Any]] = []
    item_views: List[Dict[str, Any]] = []
    for key, spec in props.items():
        if not isinstance(spec, Mapping):
            continue
        kind = _kind(spec)
        if kind in SCALARS or kind in ("integer", "number"):
            summary_cols.append(_column(key, spec, kind=kind))
        elif kind == "array":
            items = spec.get("items") if isinstance(spec.get("items"), Mapping) else {}
            if _kind(items) == "object" and (items.get("properties") or {}):
                cols = [_column(k, s, path=(k,)) for k, s in items["properties"].items()
                        if isinstance(s, Mapping) and _kind(s) in ("string", "integer", "number", "boolean")]
                if cols:
                    metric, order = _key_metric(cols)
                    item_views.append({"key": key, "label": label_for(key, spec), "per": "item", "source": key,
                                       "columns": _ordered(cols, metric), "metric": metric, "order": order})
                    summary_cols.append({"key": f"{key}__count", "label": label_for(key, spec), "kind": "integer",
                                         "format": "number", "path": [key], "aggregate": "count", "meta": True})
            else:
                summary_cols.append({"key": f"{key}__count", "label": label_for(key, spec), "kind": "integer",
                                     "format": "number", "path": [key], "aggregate": "count"})
                summary_cols.append({"key": f"{key}__items", "label": f"{label_for(key, spec)} (first {PREVIEW_ITEMS})",
                                     "kind": "string", "format": "text", "path": [key], "aggregate": "preview"})
    metric, order = _key_metric(summary_cols)
    summary = {"key": "summary", "label": "Per node", "per": "node", "source": None,
               "columns": _ordered(summary_cols, metric), "metric": metric, "order": order}
    return [summary] + item_views


def _value(output: Any, col: Mapping[str, Any]) -> Any:
    if not isinstance(output, Mapping):
        return None
    value = output.get(col["path"][0])
    aggregate = col.get("aggregate")
    if aggregate == "count":
        return len(value) if isinstance(value, list) else None
    if aggregate == "preview":
        if not isinstance(value, list):
            return None
        shown = ", ".join(str(v) for v in value[:PREVIEW_ITEMS])
        return shown + (f" … +{len(value) - PREVIEW_ITEMS}" if len(value) > PREVIEW_ITEMS else "")
    if isinstance(value, (dict, list)):
        return None
    return value


def _node_fields(node: Any, node_id: str) -> Dict[str, Any]:
    return {"node_id": node_id, "node": getattr(node, "name", None) or node_id,
            "labels": dict(getattr(node, "labels", {}) or {})}


def build(view: Mapping[str, Any], executions: Sequence[Any], nodes: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Rows for one view. Each row: node_id, node, labels, exec_id, state, finished_at, values{}."""
    rows: List[Dict[str, Any]] = []
    for ex in executions:
        base = dict(_node_fields(nodes.get(ex.node_id), ex.node_id), exec_id=ex.id, state=ex.state,
                    finished_at=ex.finished_at, version=getattr(ex, "version", 0), run_id=getattr(ex, "run_id", ""))
        if view["per"] == "node":
            rows.append(dict(base, values={c["key"]: _value(ex.output, c) for c in view["columns"]}))
            continue
        items = ex.output.get(view["source"]) if isinstance(ex.output, Mapping) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, Mapping):
                rows.append(dict(base, values={c["key"]: (None if isinstance(item.get(c["key"]), (dict, list))
                                                         else item.get(c["key"])) for c in view["columns"]}))
    metric, order = view.get("metric"), view.get("order", "asc")
    if metric:
        present = [r for r in rows if isinstance(r["values"].get(metric), (int, float))]
        missing = [r for r in rows if not isinstance(r["values"].get(metric), (int, float))]
        present.sort(key=lambda r: r["values"][metric], reverse=(order == "desc"))
        rows = present + missing
    return rows


def _tables(schema: Optional[Mapping[str, Any]], executions: Sequence[Any], nodes: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for view in views(schema, [ex.output for ex in executions]):
        rows = build(view, executions, nodes)
        if view["per"] == "item" and not rows:
            continue
        out.append(dict(view, rows=rows))
    return out


def _schema_for(store: Any, tool_id: str, version: int) -> Optional[Mapping[str, Any]]:
    tv = store.get_version(tool_id, version) if tool_id else None
    return (tv.manifest.get("outputs") if tv is not None else None) or None


def run_results(store: Any, run: Any) -> Dict[str, Any]:
    """Results of a run: one entry per step that ran, each with its tables."""
    executions = [ex for ex in store.list_executions(run_id=run.id) if not getattr(ex, "rollback_of", "")]
    nodes = {n.id: n for n in store.list_nodes()}
    steps: Dict[int, List[Any]] = {}
    for ex in executions:
        steps.setdefault(ex.step, []).append(ex)
    out = []
    for step in sorted(steps):
        group = steps[step]
        # The latest attempt per node (retries create new executions).
        latest: Dict[str, Any] = {}
        for ex in group:
            latest[ex.node_id] = ex
        group = list(latest.values())
        first = group[0]
        out.append({
            "step": step, "step_name": first.step_name, "tool": f"{first.tool_id}@{first.version}",
            "nodes": len(group), "with_output": sum(1 for ex in group if isinstance(ex.output, Mapping)),
            "finished": sum(1 for ex in group if ex.state in FINISHED),
            "tables": _tables(_schema_for(store, first.tool_id, first.version), group, nodes),
        })
    return {"run_id": run.id, "title": run.title, "steps": out}


def latest_results(store: Any, tool_id: str, *, limit: int = 2000) -> Dict[str, Any]:
    """Each node's most recent successful execution of ``tool_id`` (any version; the newest
    version's schema shapes the table, older outputs fill what they share)."""
    rows: Dict[str, Any] = {}
    for ex in store.list_executions(states=["succeeded"], limit=limit * 10):
        if ex.tool_id != tool_id or getattr(ex, "rollback_of", "") or getattr(ex, "check_mode", False):
            continue
        prev = rows.get(ex.node_id)
        if prev is None or (ex.finished_at or 0) >= (prev.finished_at or 0):
            rows[ex.node_id] = ex
    group = sorted(rows.values(), key=lambda e: e.finished_at or 0, reverse=True)[:limit]
    nodes = {n.id: n for n in store.list_nodes()}
    newest = max((ex.version for ex in group), default=0)
    tv = store.latest_version(tool_id)
    schema = _schema_for(store, tool_id, newest) or (tv.manifest.get("outputs") if tv else None)
    return {"tool_id": tool_id, "nodes": len(group),
            "oldest": min((ex.finished_at or 0 for ex in group), default=None),
            "tables": _tables(schema, group, nodes)}


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    # Negative numbers stay numbers; any other leading formula character is neutralised.
    if text.startswith(_FORMULA) and not isinstance(value, (int, float)):
        return "'" + text
    return text


def to_csv(table: Mapping[str, Any]) -> str:
    """CSV of one table: node, labels, state, then the table's columns (raw values)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    cols = table["columns"]
    writer.writerow(["node", "node_id", "labels", "state"] + [c["label"] for c in cols])
    for row in table["rows"]:
        labels = ",".join(f"{k}={v}" for k, v in sorted(row.get("labels", {}).items()))
        writer.writerow([_cell(row["node"]), _cell(row["node_id"]), _cell(labels), _cell(row["state"])]
                        + [_cell(row["values"].get(c["key"])) for c in cols])
    return buf.getvalue()
