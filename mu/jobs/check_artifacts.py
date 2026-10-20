"""Artifacts of a checks run: every file a suite produced, kept so it can be browsed in mucli.

Checks discard what a suite leaves in the worktree (``checks.discard_byproducts``) so the next
suite and the dirty-tree gate see a clean tree. Before that happens, ``collect`` copies each file
the suite wrote — untracked *or ignored*: test reports, JUnit/coverage XML, logs, screenshots,
packet captures, archives — into ``checks/run-<n>/artifacts/<label>/<path>`` and records it in
``checks/run-<n>/artifacts.json``. Tracked files are never collected (a change there is a source
edit, and stays for the dirty-tree gate). Dependency and cache directories are skipped, symlinks
are never followed, and per-file / per-run size and count caps keep a noisy suite bounded.

``run_listing`` adds the suite logs and JUnit files the checks stage already writes, and
``build_preview`` turns one file into what the web viewer shows (text, JUnit cases, XML, JSON, CSV,
packet summaries, archive listings, a hex dump). Artifacts come from code under test: nothing
here executes them, XML with entity declarations is never parsed, and the HTTP route serves
them as downloads or sandboxed documents only.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import socket
import struct
import tarfile
import time
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Set

SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".nox", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".hypothesis", ".gradle", ".cargo", ".npm", ".yarn", ".pnpm-store", ".cache",
    "site-packages", ".eggs", ".terraform", "bower_components",
})
SKIP_SUFFIXES = (".pyc", ".pyo", ".o", ".obj", ".d", ".swp")
MAX_FILES_PER_SUITE = 500
MAX_FILE_BYTES = 64 * 2**20
MAX_RUN_BYTES = 512 * 2**20
MTIME_SLACK_S = 2.0

PREVIEW_TEXT_BYTES = 2 * 2**20
PREVIEW_PARSE_BYTES = 8 * 2**20
MAX_PACKETS = 5000
MAX_ARCHIVE_ENTRIES = 2000
MAX_CSV_ROWS = 1000

KINDS = ("log", "text", "junit", "xml", "json", "csv", "html", "image", "pcap", "archive", "binary")


def tracked_files(worktree: str) -> Set[str]:
    from .checks import git
    try:
        return {p for p in git(worktree, "ls-files", "-z").split("\0") if p}
    except Exception:
        return set()


def _walk(worktree: str) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(worktree, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            yield os.path.join(dirpath, name)


def collect(worktree: str, art, run_no: int, label: str, *, since: float, tracked: Set[str],
            suites: Optional[List[str]] = None, budget: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Copy what the suite(s) wrote since ``since`` into the run's artifacts; returns
    ``{"entries": [...], "skipped": [...]}`` (also appended to ``artifacts.json``)."""
    budget = budget if budget is not None else {"bytes": 0}
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    root = os.path.realpath(worktree)
    for full in _walk(root):
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        # A worktree's .git is a file (a gitlink), not a directory: never an artifact.
        if rel in tracked or rel.endswith(SKIP_SUFFIXES) or os.path.basename(rel) == ".git":
            continue
        try:
            st = os.lstat(full)
        except OSError:
            continue
        if not _is_regular(st.st_mode) or st.st_mtime < since - MTIME_SLACK_S:
            continue
        if len(entries) >= MAX_FILES_PER_SUITE:
            skipped.append({"name": rel, "reason": f"more than {MAX_FILES_PER_SUITE} files"})
            continue
        if st.st_size > MAX_FILE_BYTES:
            skipped.append({"name": rel, "bytes": st.st_size, "reason": "larger than 64 MB"})
            continue
        if budget["bytes"] + st.st_size > MAX_RUN_BYTES:
            skipped.append({"name": rel, "bytes": st.st_size, "reason": "the run's 512 MB artifact budget is used up"})
            continue
        stored = f"artifacts/{label}/{rel}"
        kind = classify(rel, _head(full))
        try:
            target = art.path("checks", f"run-{run_no}", *stored.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            redacted = _copy(full, target, kind)
        except (OSError, ValueError) as exc:
            skipped.append({"name": rel, "reason": f"couldn't copy: {exc}"[:200]})
            continue
        budget["bytes"] += st.st_size
        entry = {"path": stored, "name": rel, "label": label, "suites": list(suites or []),
                 "bytes": os.path.getsize(target), "kind": kind, "mtime": st.st_mtime}
        if redacted:
            entry["redacted"] = redacted
        entries.append(entry)
    _append_manifest(art, run_no, entries, skipped)
    return {"entries": entries, "skipped": skipped}


TEXT_KINDS = ("log", "text", "json", "csv", "xml", "junit", "html")


def _copy(src: str, dst: str, kind: str) -> int:
    """Copy one artifact (never through a symlink). Text is secret-redacted on the way in, as
    evidence is (``mu.security.secret_paths.redact_secrets``), line by line so a large log stays
    bounded in memory; returns how many secrets were redacted."""
    if kind not in TEXT_KINDS:
        shutil.copyfile(src, dst, follow_symlinks=False)
        return 0
    from mu.security.secret_paths import redact_secrets
    count = 0
    fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8", errors="replace", newline="") as fin, \
            open(dst, "w", encoding="utf-8", newline="") as fout:
        batch: List[str] = []
        for line in fin:
            batch.append(line)
            if len(batch) >= 2000:
                text, n = redact_secrets("".join(batch))
                fout.write(text)
                count += n
                batch = []
        if batch:
            text, n = redact_secrets("".join(batch))
            fout.write(text)
            count += n
    return count


def _is_regular(mode: int) -> bool:
    import stat
    return stat.S_ISREG(mode)


def _append_manifest(art, run_no: int, entries, skipped) -> None:
    rel = f"checks/run-{run_no}/artifacts.json"
    current = art.read_json(rel) if hasattr(art, "read_json") else None
    current = current if isinstance(current, dict) else {"entries": [], "skipped": []}
    current["entries"] = list(current.get("entries") or []) + entries
    current["skipped"] = list(current.get("skipped") or []) + skipped
    current["updated_at"] = time.time()
    art.write_json(rel, current)


def _head(path: str, n: int = 4096) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


# --------------------------------------------------------------------- classify

PCAP_MAGIC = (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"
IMAGE_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"))
TEXT_SUFFIXES = (".txt", ".out", ".err", ".md", ".rst", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".diff", ".patch",
                 ".css", ".js", ".py", ".sh", ".sql", ".har")
ARCHIVE_SUFFIXES = (".zip", ".jar", ".whl", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz", ".gz")


def classify(name: str, head: bytes) -> str:
    low = name.lower()
    if head[:4] in PCAP_MAGIC or head[:4] == PCAPNG_MAGIC or low.endswith((".pcap", ".pcapng", ".cap")):
        return "pcap"
    if any(head.startswith(m) for m, _ in IMAGE_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") \
            or low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return "image"
    if low.endswith(ARCHIVE_SUFFIXES) or head[:4] == b"PK\x03\x04" or head[:2] == b"\x1f\x8b":
        return "archive"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if low.endswith(".xml") or text.startswith(b"<?xml"):
        return "junit" if (b"<testsuite" in head or b"<testsuites" in head) else "xml"
    if low.endswith((".html", ".htm")):
        return "html"
    if low.endswith((".json", ".jsonl", ".ndjson")):
        return "json"
    if low.endswith((".csv", ".tsv")):
        return "csv"
    if low.endswith(".log"):
        return "log"
    if low.endswith(TEXT_SUFFIXES) or _looks_textual(head):
        return "text"
    return "binary"


def _looks_textual(head: bytes) -> bool:
    if not head:
        return True
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:      # a multibyte char cut at the end is fine
            return False
    control = sum(1 for b in head if b < 9 or 13 < b < 32)
    return control <= len(head) // 100


def media_type(name: str, kind: str) -> str:
    low = name.lower()
    if kind == "image":
        for suffix, mt in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".gif", "image/gif"),
                           (".webp", "image/webp"), (".svg", "image/svg+xml")):
            if low.endswith(suffix):
                return mt
    if low.endswith(".css"):
        return "text/css"
    if kind == "html":
        return "text/html; charset=utf-8"
    if kind in ("log", "text", "json", "csv", "xml", "junit"):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


# --------------------------------------------------------------------- listing

def run_listing(art, run_no: int) -> Dict[str, Any]:
    """Everything browsable for one checks run: the suite logs and JUnit files, then the
    collected artifacts (grouped by label in the client)."""
    base = art.path("checks", f"run-{run_no}")
    if not os.path.isdir(base):
        raise FileNotFoundError(f"checks run {run_no} not found")
    summary = art.read_json(f"checks/run-{run_no}/summary.json") or {}
    suites = summary.get("suites") or []
    files: List[Dict[str, Any]] = []
    for index, suite in enumerate(suites, 1):
        name = str(suite.get("suite") or f"suite {index}")
        for rel, kind in ((f"suite-{index}.log", "log"), (f"suite-{index}.xml", "junit")):
            full = os.path.join(base, rel)
            if os.path.isfile(full):
                files.append({"path": rel, "name": f"{name} — {'output' if kind == 'log' else 'JUnit report'}",
                              "label": "suite output", "suites": [name], "bytes": os.path.getsize(full), "kind": kind})
    manifest = art.read_json(f"checks/run-{run_no}/artifacts.json") or {}
    collected = [e for e in manifest.get("entries") or [] if os.path.isfile(os.path.join(base, e.get("path", "")))]
    return {"run": run_no, "files": files + collected, "skipped": list(manifest.get("skipped") or []),
            "collected": len(collected), "bytes": sum(int(e.get("bytes") or 0) for e in collected)}


def resolve(art, run_no: int, rel: str) -> Optional[Dict[str, Any]]:
    """A file of the run, by its path in the listing (nothing outside the run's folder)."""
    listing = run_listing(art, run_no)
    entry = next((e for e in listing["files"] if e["path"] == rel), None)
    if entry is None:
        # Files beside a listed HTML report (its CSS and images) under the same artifacts dir.
        if not rel.startswith("artifacts/") or not any(e["kind"] == "html" and e["path"].split("/")[1] == rel.split("/")[1]
                                                       for e in listing["files"] if e["path"].startswith("artifacts/")):
            return None
        entry = {"path": rel, "name": rel.split("/", 2)[-1], "kind": None}
    try:
        full = art.path("checks", f"run-{run_no}", *rel.split("/"))
    except ValueError:
        return None
    if not os.path.isfile(full) or os.path.islink(full):
        return None
    entry = dict(entry, full=full)
    if not entry.get("kind"):
        entry["kind"] = classify(rel, _head(full))
    return entry


# --------------------------------------------------------------------- preview

def build_preview(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What the viewer shows for one file."""
    full, kind = entry["full"], entry["kind"]
    size = os.path.getsize(full)
    out: Dict[str, Any] = {"path": entry["path"], "name": entry.get("name"), "kind": kind, "bytes": size}
    try:
        if kind in ("log", "text", "html"):
            out.update(_text(full, size))
        elif kind == "json":
            out.update(_json(full, size))
        elif kind == "csv":
            out.update(_csv(full, size, delimiter="\t" if full.lower().endswith(".tsv") else ","))
        elif kind in ("xml", "junit"):
            out.update(_xml(full, size, junit=kind == "junit"))
        elif kind == "pcap":
            out.update(read_pcap(full))
        elif kind == "archive":
            out.update(_archive(full))
        elif kind == "binary":
            out["hex"] = _hex(full)
    except Exception as exc:                      # a malformed artifact never breaks the viewer
        out["error"] = f"couldn't read it as {kind}: {exc}"[:300]
        out.setdefault("hex", _hex(full))
    return out


def _read(full: str, limit: int) -> bytes:
    with open(full, "rb") as fh:
        return fh.read(limit)


def _text(full: str, size: int) -> Dict[str, Any]:
    if size <= PREVIEW_TEXT_BYTES:
        return {"text": _read(full, size).decode("utf-8", "replace"), "truncated": False}
    half = PREVIEW_TEXT_BYTES // 2
    with open(full, "rb") as fh:
        head = fh.read(half)
        fh.seek(size - half)
        tail = fh.read(half)
    gap = size - 2 * half
    return {"text": head.decode("utf-8", "replace") + f"\n\n… {gap:,} bytes not shown — download the file for all of it …\n\n"
            + tail.decode("utf-8", "replace"), "truncated": True}


def _json(full: str, size: int) -> Dict[str, Any]:
    if size > PREVIEW_PARSE_BYTES or full.lower().endswith((".jsonl", ".ndjson")):
        return _text(full, size)
    raw = _read(full, size).decode("utf-8", "replace")
    try:
        return {"text": json.dumps(json.loads(raw), indent=2, ensure_ascii=False), "truncated": False, "pretty": True}
    except ValueError:
        return {"text": raw, "truncated": False}


def _csv(full: str, size: int, delimiter: str) -> Dict[str, Any]:
    raw = _read(full, min(size, PREVIEW_PARSE_BYTES)).decode("utf-8", "replace")
    rows = []
    for row in csv.reader(io.StringIO(raw), delimiter=delimiter):
        rows.append(row[:64])
        if len(rows) > MAX_CSV_ROWS:
            break
    return {"rows": rows[:MAX_CSV_ROWS + 1], "truncated": len(rows) > MAX_CSV_ROWS or size > PREVIEW_PARSE_BYTES}


def _xml(full: str, size: int, *, junit: bool) -> Dict[str, Any]:
    raw = _read(full, min(size, PREVIEW_PARSE_BYTES))
    out = _text(full, size)
    # Never parse documents that declare entities (expansion bombs) or that are cut short.
    if b"<!ENTITY" in raw or b"<!DOCTYPE" in raw or size > PREVIEW_PARSE_BYTES:
        out["note"] = "shown as text (too large, or it declares a DOCTYPE)"
        return out
    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)
    if junit:
        out["junit"] = _junit(root)
    try:
        ET.indent(root)                                # 3.9+
        out["text"] = ET.tostring(root, encoding="unicode")
        out["pretty"] = True
    except AttributeError:
        pass
    return out


def _junit(root) -> Dict[str, Any]:
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    out = []
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites[:200]:
        cases = []
        for case in suite.findall("testcase")[:5000]:
            status, message, detail = "passed", "", ""
            for tag in ("failure", "error", "skipped"):
                node = case.find(tag)
                if node is not None:
                    status = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                    message = (node.get("message") or "")[:500]
                    detail = (node.text or "")[:8000]
                    break
            out_text = (case.findtext("system-out") or "")[:4000]
            cases.append({"classname": case.get("classname") or "", "name": case.get("name") or "",
                          "time": _num(case.get("time")), "status": status, "message": message, "detail": detail,
                          "stdout": out_text})
        counts = {"tests": len(cases), "failures": sum(c["status"] == "failed" for c in cases),
                  "errors": sum(c["status"] == "error" for c in cases), "skipped": sum(c["status"] == "skipped" for c in cases)}
        for k in totals:
            totals[k] += counts[k]
        out.append({"name": suite.get("name") or "", "time": _num(suite.get("time")), **counts, "cases": cases})
    return {"suites": out, "totals": totals}


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _archive(full: str) -> Dict[str, Any]:
    entries = []
    if zipfile.is_zipfile(full):
        with zipfile.ZipFile(full) as zf:
            for info in zf.infolist()[:MAX_ARCHIVE_ENTRIES + 1]:
                entries.append({"name": info.filename, "bytes": info.file_size, "dir": info.is_dir()})
        return {"archive": "zip", "entries": entries[:MAX_ARCHIVE_ENTRIES], "truncated": len(entries) > MAX_ARCHIVE_ENTRIES}
    if tarfile.is_tarfile(full):
        with tarfile.open(full) as tf:
            for member in tf:
                entries.append({"name": member.name, "bytes": member.size, "dir": member.isdir()})
                if len(entries) > MAX_ARCHIVE_ENTRIES:
                    break
        return {"archive": "tar", "entries": entries[:MAX_ARCHIVE_ENTRIES], "truncated": len(entries) > MAX_ARCHIVE_ENTRIES}
    if full.lower().endswith(".gz"):
        import gzip
        with gzip.open(full, "rb") as fh:
            data = fh.read(PREVIEW_TEXT_BYTES)
        if _looks_textual(data[:4096]):
            return {"archive": "gzip", "text": data.decode("utf-8", "replace"), "truncated": len(data) >= PREVIEW_TEXT_BYTES}
        return {"archive": "gzip", "hex": _hex_bytes(data[:4096])}
    return {"hex": _hex(full)}


def _hex(full: str, n: int = 4096) -> str:
    return _hex_bytes(_read(full, n))


def _hex_bytes(data: bytes) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hexpart:<47}  {text}")
    return "\n".join(lines)


# --------------------------------------------------------------------- packet captures

ETHERTYPES = {0x0800: "IPv4", 0x86DD: "IPv6", 0x0806: "ARP", 0x8100: "VLAN"}
IP_PROTOS = {1: "ICMP", 6: "TCP", 17: "UDP", 58: "ICMPv6", 2: "IGMP", 47: "GRE", 50: "ESP", 132: "SCTP"}
WELL_KNOWN = {53: "DNS", 80: "HTTP", 443: "TLS", 22: "SSH", 25: "SMTP", 123: "NTP", 67: "DHCP", 68: "DHCP",
              5353: "mDNS", 8080: "HTTP", 3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis"}


def read_pcap(full: str) -> Dict[str, Any]:
    """A packet table from a pcap or pcapng file (standard library only): time, source,
    destination, protocol, length and a short summary per packet; plus conversations."""
    with open(full, "rb") as fh:
        data = fh.read(PREVIEW_PARSE_BYTES * 4)
    packets: List[Dict[str, Any]] = []
    if data[:4] == PCAPNG_MAGIC:
        frames = _pcapng_frames(data)
        fmt = "pcapng"
    else:
        frames = _pcap_frames(data)
        fmt = "pcap"
    start = None
    total = 0
    for ts, linktype, frame, orig_len in frames:
        total += 1
        if len(packets) >= MAX_PACKETS:
            continue
        start = ts if start is None else start
        info = _decode(linktype, frame)
        info.update(no=total, time=round(ts - start, 6), length=orig_len)
        packets.append(info)
    talks: Dict[tuple, Dict[str, Any]] = {}
    protos: Dict[str, int] = {}
    for p in packets:
        protos[p["proto"]] = protos.get(p["proto"], 0) + 1
        if p.get("src") and p.get("dst"):
            key = tuple(sorted([p["src"], p["dst"]])) + (p["proto"],)
            t = talks.setdefault(key, {"a": key[0], "b": key[1], "proto": key[2], "packets": 0, "bytes": 0})
            t["packets"] += 1
            t["bytes"] += p["length"]
    return {"format": fmt, "packets": packets, "total": total, "truncated": total > len(packets) or len(data) >= PREVIEW_PARSE_BYTES * 4,
            "protocols": dict(sorted(protos.items(), key=lambda kv: -kv[1])),
            "conversations": sorted(talks.values(), key=lambda t: -t["bytes"])[:50]}


def _pcap_frames(data: bytes):
    magic = data[:4]
    if magic not in PCAP_MAGIC or len(data) < 24:
        raise ValueError("not a pcap file")
    endian = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    nano = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
    linktype = struct.unpack(endian + "I", data[20:24])[0] & 0x0FFFFFFF
    off = 24
    while off + 16 <= len(data):
        sec, frac, incl, orig = struct.unpack(endian + "IIII", data[off:off + 16])
        off += 16
        if incl > 262144 or off + incl > len(data):
            break
        yield sec + frac / (1e9 if nano else 1e6), linktype, data[off:off + incl], orig
        off += incl


def _pcapng_frames(data: bytes):
    off = 0
    endian = "<"
    interfaces: List[tuple] = []          # (linktype, ts resolution divisor)
    while off + 12 <= len(data):
        btype = struct.unpack(endian + "I", data[off:off + 4])[0]
        if btype == 0x0A0D0D0A:
            bom = data[off + 8:off + 12]
            endian = "<" if bom == b"\x4d\x3c\x2b\x1a" else ">"
            interfaces = []
        blen = struct.unpack(endian + "I", data[off + 4:off + 8])[0]
        if blen < 12 or off + blen > len(data):
            break
        body = data[off + 8:off + blen - 4]
        if btype == 1 and len(body) >= 8:                       # interface description
            linktype = struct.unpack(endian + "H", body[0:2])[0]
            interfaces.append((linktype, _if_tsresol(body[8:], endian)))
        elif btype == 6 and len(body) >= 20:                    # enhanced packet
            iface, hi, lo, cap, orig = struct.unpack(endian + "IIIII", body[:20])
            linktype, div = interfaces[iface] if iface < len(interfaces) else (1, 1e6)
            yield ((hi << 32) | lo) / div, linktype, body[20:20 + cap], orig
        elif btype == 3 and len(body) >= 4:                     # simple packet
            orig = struct.unpack(endian + "I", body[:4])[0]
            linktype = interfaces[0][0] if interfaces else 1
            yield 0.0, linktype, body[4:4 + min(orig, len(body) - 4)], orig
        off += blen


def _if_tsresol(options: bytes, endian: str) -> float:
    off = 0
    while off + 4 <= len(options):
        code, length = struct.unpack(endian + "HH", options[off:off + 4])
        if code == 0:
            break
        if code == 9 and length >= 1:
            v = options[off + 4]
            return float(2 ** (v & 0x7F)) if v & 0x80 else float(10 ** v)
        off += 4 + ((length + 3) & ~3)
    return 1e6


def _decode(linktype: int, frame: bytes) -> Dict[str, Any]:
    out: Dict[str, Any] = {"src": "", "dst": "", "proto": "?", "info": ""}
    try:
        if linktype == 1:                                       # Ethernet
            if len(frame) < 14:
                return out
            etype, payload = struct.unpack("!H", frame[12:14])[0], frame[14:]
            while etype == 0x8100 and len(payload) >= 4:        # 802.1Q
                etype, payload = struct.unpack("!H", payload[2:4])[0], payload[4:]
            return _l3(etype, payload, out)
        if linktype in (101, 12, 14):                           # raw IP
            return _l3(0x86DD if frame[:1] and frame[0] >> 4 == 6 else 0x0800, frame, out)
        if linktype == 113 and len(frame) >= 16:                # Linux cooked (SLL)
            return _l3(struct.unpack("!H", frame[14:16])[0], frame[16:], out)
        if linktype == 276 and len(frame) >= 20:                # SLL2
            return _l3(struct.unpack("!H", frame[0:2])[0], frame[20:], out)
        if linktype == 0 and len(frame) >= 4:                   # BSD loopback
            family = struct.unpack("<I", frame[:4])[0]
            return _l3(0x86DD if family in (24, 28, 30) else 0x0800, frame[4:], out)
    except (struct.error, IndexError, ValueError):
        pass
    out["info"] = f"link type {linktype}"
    return out


def _l3(etype: int, p: bytes, out: Dict[str, Any]) -> Dict[str, Any]:
    if etype == 0x0800 and len(p) >= 20:
        ihl = (p[0] & 0x0F) * 4
        proto = p[9]
        out["src"], out["dst"] = socket.inet_ntoa(p[12:16]), socket.inet_ntoa(p[16:20])
        return _l4(proto, p[ihl:], out)
    if etype == 0x86DD and len(p) >= 40:
        out["src"], out["dst"] = socket.inet_ntop(socket.AF_INET6, p[8:24]), socket.inet_ntop(socket.AF_INET6, p[24:40])
        return _l4(p[6], p[40:], out)
    if etype == 0x0806 and len(p) >= 28:
        op = struct.unpack("!H", p[6:8])[0]
        spa, tpa = socket.inet_ntoa(p[14:18]), socket.inet_ntoa(p[24:28])
        out.update(proto="ARP", src=spa, dst=tpa, info=f"who has {tpa}? tell {spa}" if op == 1 else f"{spa} is at {p[8:14].hex(':')}")
        return out
    out["proto"] = ETHERTYPES.get(etype, f"0x{etype:04x}")
    return out


def _l4(proto: int, p: bytes, out: Dict[str, Any]) -> Dict[str, Any]:
    name = IP_PROTOS.get(proto, str(proto))
    out["proto"] = name
    if proto in (6, 17) and len(p) >= 8:
        sport, dport = struct.unpack("!HH", p[:4])
        out["src"] += f":{sport}" if ":" not in out["src"] else f".{sport}"
        out["dst"] += f":{dport}" if ":" not in out["dst"] else f".{dport}"
        app = WELL_KNOWN.get(dport) or WELL_KNOWN.get(sport)
        if proto == 6 and len(p) >= 20:
            flags = p[13]
            names = [n for bit, n in ((0x02, "SYN"), (0x10, "ACK"), (0x01, "FIN"), (0x04, "RST"), (0x08, "PSH")) if flags & bit]
            data = p[(p[12] >> 4) * 4:]
            out["info"] = f"{sport} → {dport} [{', '.join(names)}]" + (f" len={len(data)}" if data else "")
            first = data[:200].split(b"\r\n", 1)[0]
            if first[:5] in (b"GET /", b"POST ", b"PUT /", b"HEAD ", b"HTTP/", b"DELET", b"PATCH", b"OPTIO"):
                out["proto"], out["info"] = "HTTP", first.decode("latin-1")[:160]
            elif data[:1] == b"\x16" and data[1:2] == b"\x03":
                out["proto"] = "TLS"
                out["info"] += " handshake"
            elif app:
                out["proto"] = app if data else name
        else:
            data = p[8:]
            out["info"] = f"{sport} → {dport} len={len(data)}"
            if app in ("DNS", "mDNS") and len(data) > 12:
                qname = _dns_name(data, 12)
                out["proto"] = app
                out["info"] = ("response " if data[2] & 0x80 else "query ") + (qname or "")
            elif app:
                out["proto"] = app
    elif proto in (1, 58) and len(p) >= 2:
        out["info"] = {(1, 8): "echo request", (1, 0): "echo reply", (58, 128): "echo request", (58, 129): "echo reply"}.get(
            (proto, p[0]), f"type {p[0]} code {p[1]}")
    return out


def _dns_name(data: bytes, off: int) -> str:
    labels = []
    for _ in range(64):
        if off >= len(data):
            break
        n = data[off]
        if n == 0 or n & 0xC0:
            break
        labels.append(data[off + 1:off + 1 + n].decode("latin-1"))
        off += 1 + n
    return ".".join(labels)
