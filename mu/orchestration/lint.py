"""Static checks for tool versions (plan §4 step 4), run on the master, never on a node.

``lint(manifest, files)`` combines manifest validation, a banned-pattern lint (pipe-to-shell,
``eval`` of parameters, ``rm -rf /``, writes outside declared paths, shell re-interpretation of
parameters), a secret scan, and shellcheck / ruff when they are installed. Errors block
submitting a draft for review; warnings are shown to the reviewer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Mapping, Optional

from .manifest import FILE_NAME, normalize

MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


def _finding(severity: str, rule: str, message: str, file: str = "", line: int = 0) -> Dict[str, Any]:
    return {"severity": severity, "rule": rule, "message": message, "file": file, "line": line}


_BASH_RULES = (
    ("error", "pipe-to-shell", re.compile(r"\b(curl|wget)\b[^|#\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"),
     "downloads piped into a shell run unreviewed code"),
    ("error", "eval", re.compile(r"(^|[;&|\s])eval\s"), "eval re-interprets data as code"),
    ("error", "rm-root", re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*(--no-preserve-root\s+)?[\"']?/\*?[\"']?(\s|$|;)"),
     "removes the root filesystem"),
    ("error", "shell-reinterpretation", re.compile(r"\b(ba|z|da)?sh\s+-c\s+[\"']?[^\n]*\$\{?MUCLI_P_"),
     "passes a parameter to a nested shell, which re-parses it as code"),
    ("error", "source-param", re.compile(r"(^|[;&|\s])(source|\.)\s+[\"']?\$\{?MUCLI_P_"),
     "sources a file named by a parameter"),
    ("warning", "rm-rf-variable", re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+[\"']?\$\{?[A-Za-z_]+\}?(/|[\"']?\s|$)"),
     "rm -rf on a variable; guard it with ${VAR:?} so an empty value cannot expand to /"),
    ("warning", "sudo", re.compile(r"(^|[;&|\s])sudo\s"), "tools run with their declared privilege; sudo is not available"),
)
_MUTATING_COMMANDS = re.compile(
    r"\b(systemctl\s+(restart|stop|start|reload|enable|disable|mask)|apt(-get)?\s+(install|remove|upgrade|purge)|"
    r"dnf\s+(install|remove|upgrade)|yum\s+(install|remove)|rm\s|mv\s|chmod\s|chown\s|kill\s|pkill\s|"
    r"useradd|userdel|truncate\s|shutdown|reboot)")
_PY_RULES = (
    ("error", "eval", re.compile(r"(?<![\w.])(eval|exec)\s*\("), "eval/exec re-interprets data as code"),
    ("error", "os-system", re.compile(r"\bos\.(system|popen)\s*\("), "os.system/os.popen run a shell"),
    ("error", "shell-true", re.compile(r"shell\s*=\s*True"), "subprocess with shell=True re-parses arguments"),
    ("warning", "pickle", re.compile(r"\bpickle\.loads?\s*\("), "pickle can execute code"),
    ("warning", "dunder-import", re.compile(r"__import__\s*\("), "dynamic imports are hard to review"),
)
_REDIRECT = re.compile(r"(?:>>?|\btee\s+(?:-a\s+)?)\s*[\"']?(/[^\s\"';|&)]+)")
_ALWAYS_WRITABLE = ("/dev/null", "/dev/stderr", "/dev/stdout", "/tmp/", "/var/tmp/")


def _in_double_quotes(line: str, index: int) -> bool:
    quotes, single = 0, False
    for ch in line[:index]:
        if ch == "'" and quotes % 2 == 0:
            single = not single
        elif ch == '"' and not single:
            quotes += 1
    return quotes % 2 == 1 or single


def _bash_findings(name: str, text: str, manifest: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    writes = list((manifest.get("paths") or {}).get("write") or [])
    read_only = manifest.get("effect") == "read_only"
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#!") else ""
        if not code.strip():
            continue
        for severity, rule, pattern, message in _BASH_RULES:
            if pattern.search(code):
                out.append(_finding(severity, rule, message, name, n))
        for match in re.finditer(r"\$\{?MUCLI_P_[A-Z0-9_]+\}?", code):
            if not _in_double_quotes(code, match.start()) and not code[:match.start()].rstrip().endswith("[["):
                out.append(_finding("warning", "unquoted-param",
                                    f"{match.group(0)} is unquoted; quote parameters so they are never word-split",
                                    name, n))
        for match in _REDIRECT.finditer(code):
            path = match.group(1)
            if path.startswith(_ALWAYS_WRITABLE):
                continue
            if any(path == w or path.startswith(w.rstrip("/") + "/") for w in writes):
                continue
            out.append(_finding("error" if read_only else "warning", "undeclared-write",
                                f"writes to {path}, which is not under paths.write", name, n))
        if read_only and _MUTATING_COMMANDS.search(code):
            out.append(_finding("warning", "read-only-mutates",
                                "this read-only tool appears to run a mutating command", name, n))
    return out


def _py_findings(name: str, text: str, manifest: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for severity, rule, pattern, message in _PY_RULES:
            if pattern.search(code):
                out.append(_finding(severity, rule, message, name, n))
    try:
        compile(text, name, "exec")
    except SyntaxError as exc:
        out.append(_finding("error", "syntax", f"syntax error: {exc.msg}", name, exc.lineno or 0))
    return out


def _shellcheck(files: Mapping[str, str]) -> Optional[List[Dict[str, Any]]]:
    exe = shutil.which("shellcheck")
    if not exe or not files:
        return None
    out: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, text in files.items():
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            paths.append(path)
        try:
            proc = subprocess.run([exe, "-f", "json", "-s", "bash", *paths], capture_output=True, text=True,
                                  timeout=60)
            items = json.loads(proc.stdout or "[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    for item in items:
        level = item.get("level")
        out.append(_finding("error" if level == "error" else "warning" if level == "warning" else "info",
                            f"SC{item.get('code')}", str(item.get("message") or ""),
                            os.path.basename(str(item.get("file") or "")), int(item.get("line") or 0)))
    return out


def _ruff(files: Mapping[str, str]) -> Optional[List[Dict[str, Any]]]:
    exe = shutil.which("ruff")
    if not exe or not files:
        return None
    out: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        try:
            proc = subprocess.run([exe, "check", "--no-cache", "--isolated", "--output-format", "json",
                                   "--select", "E9,F,S102,S307,S602,S605", tmp],
                                  capture_output=True, text=True, timeout=60, cwd=tmp)
            items = json.loads(proc.stdout or "[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    for item in items:
        code = str(item.get("code") or "")
        severity = "error" if code.startswith(("E9", "S")) else "warning"
        out.append(_finding(severity, f"ruff:{code}", str(item.get("message") or ""),
                            os.path.basename(str(item.get("filename") or "")),
                            int((item.get("location") or {}).get("row") or 0)))
    return out


def lint(manifest_raw: Mapping[str, Any], files: Mapping[str, bytes]) -> Dict[str, Any]:
    from mu.security.secret_paths import redact_secrets

    findings: List[Dict[str, Any]] = []
    manifest, errors = normalize(manifest_raw)
    findings += [_finding("error", "manifest", e) for e in errors]
    impl = manifest.get("implementation") or {}
    runtime = impl.get("runtime")
    total = 0
    texts: Dict[str, str] = {}
    for name, data in files.items():
        if not FILE_NAME.match(name):
            findings.append(_finding("error", "file-name", f"unsafe file name {name!r}", name))
            continue
        total += len(data)
        if len(data) > MAX_FILE_BYTES:
            findings.append(_finding("error", "file-size", "file is larger than 256 KiB", name))
        try:
            texts[name] = data.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(_finding("error", "binary", "tool files must be UTF-8 text", name))
    if total > MAX_TOTAL_BYTES:
        findings.append(_finding("error", "size", "tool files exceed 1 MiB in total"))
    if runtime != "builtin" and impl.get("entry") and impl.get("entry") not in files:
        findings.append(_finding("error", "entry", f"entry {impl.get('entry')!r} is not among the files"))
    bash_files = {n: t for n, t in texts.items() if n.endswith(".sh") or (runtime == "bash" and n == impl.get("entry"))}
    py_files = {n: t for n, t in texts.items() if n.endswith(".py")}
    for name, text in texts.items():
        if name in bash_files:
            findings += _bash_findings(name, text, manifest)
        if name in py_files:
            findings += _py_findings(name, text, manifest)
        _, hits = redact_secrets(text)
        if hits:
            findings.append(_finding("error", "secret", "file contains what looks like a credential; "
                                     "declare it under secrets: instead", name))
    entry_text = texts.get(str(impl.get("entry") or ""), "")
    if entry_text:
        if impl.get("check_mode") and "MUCLI_CHECK" not in entry_text:
            findings.append(_finding("warning", "check-mode-unused",
                                     "check_mode is declared but the entry never reads MUCLI_CHECK",
                                     str(impl.get("entry"))))
        if manifest.get("outputs") and "MUCLI_RESULT" not in entry_text:
            findings.append(_finding("warning", "result-unused",
                                     "outputs are declared but the entry never writes $MUCLI_RESULT",
                                     str(impl.get("entry"))))
    for name, sub in ((manifest.get("inputs") or {}).get("properties") or {}).items():
        if isinstance(sub, dict) and sub.get("type") == "string" and not any(
                k in sub for k in ("enum", "pattern", "maxLength", "const")):
            findings.append(_finding("warning", "unconstrained-string",
                                     f"parameter {name} is an unconstrained string; add a pattern, enum or maxLength"))
    if (manifest.get("inputs") or {}).get("additionalProperties", True) is not False:
        findings.append(_finding("warning", "open-inputs", "inputs should set additionalProperties: false"))
    if manifest.get("effect") == "destructive" and not manifest.get("rollback"):
        findings.append(_finding("info", "no-rollback", "destructive tool without a rollback tool"))
    if manifest.get("effect") != "read_only" and not impl.get("check_mode"):
        findings.append(_finding("info", "no-check-mode", "changing tool without a check (dry-run) mode"))
    analyzers = {}
    sc = _shellcheck(bash_files)
    analyzers["shellcheck"] = "ran" if sc is not None else ("not installed" if bash_files else "not needed")
    findings += sc or []
    rf = _ruff(py_files)
    analyzers["ruff"] = "ran" if rf is not None else ("not installed" if py_files else "not needed")
    findings += rf or []
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")}
    return {"ok": counts["error"] == 0, "counts": counts, "findings": findings, "analyzers": analyzers}
