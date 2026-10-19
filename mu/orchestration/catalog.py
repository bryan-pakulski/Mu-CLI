"""Tool and runbook versions: drafts, lint, review, publish (plan §3.2, §3.3, §4).

A draft *is* the durable discovery job: its ``stage`` (intent → spec → discover → implement →
test → review → publish) drives the Studio stepper, its ``session_name`` links the discovery
chat, and it survives restarts because it is a row like any other.

Published versions are immutable and signed over ``(tool id, version, manifest digest, blob
digests)``. Drafts are mutable; editing a draft that has been tested moves it back to ``draft``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import lint as lint_mod
from . import manifest as mf
from . import runbook as rb_mod
from .models import STAGES, RunbookVersion, ToolVersion
from .node.canonical import digest
from .states import EDITABLE, check_runbook, check_tool


class CatalogError(ValueError):
    pass


def _files_to_blobs(store, files: Mapping[str, Any]) -> Dict[str, str]:
    blobs = {}
    for name, content in (files or {}).items():
        if not mf.FILE_NAME.match(str(name)):
            raise CatalogError(f"unsafe file name {name!r}")
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        if len(data) > lint_mod.MAX_FILE_BYTES:
            raise CatalogError(f"{name} is larger than 256 KiB")
        blobs[str(name)] = store.put_blob(data)
    return blobs


def files_of(store, tv: ToolVersion) -> Dict[str, bytes]:
    out = {}
    for name, sha in tv.blobs.items():
        data = store.get_blob(sha)
        if data is None:
            raise CatalogError(f"blob for {name} is missing or corrupt")
        out[name] = data
    return out


def text_files(store, tv: ToolVersion) -> Dict[str, str]:
    return {n: d.decode("utf-8", errors="replace") for n, d in files_of(store, tv).items()}


def _prepare(manifest_raw: Mapping[str, Any], version: int) -> Tuple[Dict[str, Any], List[str]]:
    raw = dict(manifest_raw or {})
    raw["version"] = version
    return mf.normalize(raw, version=version)


# --------------------------------------------------------------------- tools

def create_draft(store, *, actor: str, manifest: Optional[Mapping[str, Any]] = None,
                 files: Optional[Mapping[str, Any]] = None, tool_id: str = "", intent: str = "",
                 session_name: str = "", origin: str = "studio") -> ToolVersion:
    raw = dict(manifest or {})
    tool_id = str(raw.get("id") or tool_id or "").strip()
    if not mf.TOOL_ID.match(tool_id):
        raise CatalogError("tool id must be a dotted lowercase name like 'nginx.rotate-logs'")
    raw["id"] = tool_id
    blobs = _files_to_blobs(store, files or {})
    with store.tx() as conn:
        version = store.next_version_number(tool_id, conn)
        for row in conn.execute("SELECT status FROM tool_versions WHERE tool_id=? AND status IN"
                                " ('draft','testing','in_review')", (tool_id,)).fetchall():
            raise CatalogError(f"{tool_id} already has an open {row['status']} version; update that instead")
        normalized, errors = _prepare(raw, version)
        now = store.now()
        stage = "spec" if manifest else "intent"
        tv = ToolVersion(tool_id=tool_id, version=version, status="draft", manifest=normalized,
                         manifest_digest=mf.digest_of(normalized), blobs=blobs, created_at=now, updated_at=now,
                         created_by=actor, stage=stage, session_name=session_name, intent=intent, origin=origin,
                         # Not linted yet: never "ok" until run_lint has checked this content.
                         lint={"ok": False, "findings": [{"severity": "error", "rule": "manifest",
                                                          "message": e} for e in errors], "stale": True})
        store.insert_version(tv, conn)
        store.audit(actor, "tool.draft.create", tv.ref, {"digest": tv.manifest_digest, "intent": intent[:500]},
                    conn=conn)
        store.change("tool", tool_id, ["state", "catalog"], conn=conn)
    return store.get_version(tool_id, version)


def update_draft(store, tool_id: str, version: int, *, actor: str, manifest: Optional[Mapping[str, Any]] = None,
                 files: Optional[Mapping[str, Any]] = None, remove_files: Optional[List[str]] = None,
                 stage: str = "", intent: Optional[str] = None, session_name: Optional[str] = None) -> ToolVersion:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    if tv.status not in EDITABLE:
        raise CatalogError(f"{tv.ref} is {tv.status}; only drafts can be edited (create a new version)")
    fields: Dict[str, Any] = {}
    changed = False
    if manifest is not None:
        raw = dict(manifest)
        if raw.get("id") and raw["id"] != tool_id:
            raise CatalogError("a draft's tool id cannot change")
        raw["id"] = tool_id
        normalized, errors = _prepare(raw, version)
        fields.update(manifest=normalized, manifest_digest=mf.digest_of(normalized))
        changed = True
    if files is not None or remove_files:
        blobs = dict(tv.blobs)
        for name in remove_files or []:
            blobs.pop(str(name), None)
        blobs.update(_files_to_blobs(store, files or {}))
        fields["blobs"] = blobs
        changed = True
    if stage:
        if stage not in STAGES:
            raise CatalogError(f"stage must be one of {', '.join(STAGES)}")
        fields["stage"] = stage
    elif changed and tv.stage == "intent":
        fields["stage"] = "spec"
    if intent is not None:
        fields["intent"] = str(intent)[:5000]
    if session_name is not None:
        fields["session_name"] = str(session_name)
    if changed:
        fields["lint"] = dict(tv.lint or {}, stale=True)
    with store.tx() as conn:
        if changed and tv.status == "testing":
            check_tool("testing", "draft")
            fields["status"] = "draft"
        store.update_version(tool_id, version, conn, **fields)
        if changed:
            store.audit(actor, "tool.draft.update", tv.ref,
                        {"digest": fields.get("manifest_digest", tv.manifest_digest),
                         "files": sorted((fields.get("blobs") or tv.blobs).keys())}, conn=conn)
        store.change("tool", tool_id, ["state", "catalog"], conn=conn)
    return store.get_version(tool_id, version)


def run_lint(store, tool_id: str, version: int, *, actor: str) -> Dict[str, Any]:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    result = lint_mod.lint(tv.manifest, files_of(store, tv))
    result["digest"] = digest({"manifest": tv.manifest_digest, "blobs": tv.blobs})
    result["at"] = store.now()
    with store.tx() as conn:
        fields: Dict[str, Any] = {"lint": result}
        if tv.status in EDITABLE and STAGES.index(tv.stage if tv.stage in STAGES else "intent") < STAGES.index("implement"):
            fields["stage"] = "implement"
        store.update_version(tool_id, version, conn, **fields)
        store.audit(actor, "tool.lint", tv.ref, {"ok": result["ok"], "counts": result["counts"]}, conn=conn)
        store.change("tool", tool_id, ["catalog"], conn=conn)
    return result


def lint_current(tv: ToolVersion) -> bool:
    lint = tv.lint or {}
    return bool(lint.get("ok")) and not lint.get("stale") and \
        lint.get("digest") == digest({"manifest": tv.manifest_digest, "blobs": tv.blobs})


def submit_for_review(store, tool_id: str, version: int, *, actor: str, headline: str = "",
                      risks: Optional[List[str]] = None, notes: str = "") -> ToolVersion:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    if not lint_current(tv):
        lint = run_lint(store, tool_id, version, actor=actor)
        if not lint["ok"]:
            raise CatalogError(f"lint found {lint['counts']['error']} error(s); fix them before review")
        tv = store.get_version(tool_id, version)
    check_tool(tv.status, "in_review")
    review = dict(tv.review or {})
    review.update({"headline": str(headline or "")[:300], "risks": [str(r)[:300] for r in (risks or [])][:20],
                   "notes": str(notes or "")[:5000], "submitted_by": actor, "submitted_at": store.now()})
    with store.tx() as conn:
        store.update_version(tool_id, version, conn, status="in_review", stage="review", review=review)
        store.audit(actor, "tool.submit", tv.ref, {"digest": tv.manifest_digest, "headline": review["headline"]},
                    conn=conn)
        store.change("tool", tool_id, ["state", "catalog"], conn=conn)
    return store.get_version(tool_id, version)


def request_changes(store, tool_id: str, version: int, *, actor: str, notes: str = "") -> ToolVersion:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    check_tool(tv.status, "draft")
    review = dict(tv.review or {})
    review.setdefault("history", []).append({"at": store.now(), "by": actor, "decision": "changes_requested",
                                             "notes": str(notes or "")[:5000]})
    with store.tx() as conn:
        store.update_version(tool_id, version, conn, status="draft", stage="implement", review=review)
        store.audit(actor, "tool.request_changes", tv.ref, {"notes": notes[:500]}, conn=conn)
        store.change("tool", tool_id, ["state", "catalog"], conn=conn)
    return store.get_version(tool_id, version)


def publish(store, keys, tool_id: str, version: int, *, actor: str, typed: str = "") -> ToolVersion:
    if str(actor).startswith("ai"):
        raise CatalogError("only the operator can publish tools")
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    check_tool(tv.status, "published")
    normalized, errors = mf.normalize(tv.manifest, version=version)
    if errors:
        raise CatalogError("manifest is invalid: " + "; ".join(errors[:5]))
    if not lint_current(tv):
        lint = run_lint(store, tool_id, version, actor=actor)
        if not lint["ok"]:
            raise CatalogError("lint has errors; the version cannot be published")
    if tv.effect == "destructive" and typed.strip() != tool_id:
        raise CatalogError(f"destructive tools are published by typing the tool id ({tool_id})")
    for ref in [normalized.get("rollback")] + list(normalized.get("preconditions") or []):
        if not ref:
            continue
        parsed = mf.parse_ref(ref)
        other = store.get_version(*parsed) if parsed else None
        if other is None or other.status != "published":
            raise CatalogError(f"{ref} must be a published tool before {tv.ref} can reference it")
        if ref in normalized.get("preconditions") and other.effect != "read_only":
            raise CatalogError(f"precondition {ref} must be read-only")
    digest_ = mf.digest_of(normalized)
    signature = keys.sign_version(tool_id, version, digest_, tv.blobs)
    review = dict(tv.review or {})
    review.setdefault("history", []).append({"at": store.now(), "by": actor, "decision": "published"})
    with store.tx() as conn:
        store.update_version(tool_id, version, conn, status="published", stage="publish", manifest=normalized,
                             manifest_digest=digest_, signature=signature, signed_with="main",
                             key_id=keys.signing_key_id, published_at=store.now(), review=review)
        store.audit(actor, "tool.publish", tv.ref, {"digest": digest_, "blobs": tv.blobs,
                                                     "key_id": keys.signing_key_id}, conn=conn)
        store.change("tool", tool_id, ["state", "catalog"], conn=conn)
    return store.get_version(tool_id, version)


def set_lifecycle(store, tool_id: str, version: int, target: str, *, actor: str) -> ToolVersion:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    if tv.status == "deprecated" and target == "published" and not tv.signature:
        raise CatalogError("version has no signature")
    check_tool(tv.status, target)
    with store.tx() as conn:
        store.update_version(tool_id, version, conn, status=target)
        store.audit(actor, f"tool.{target}", tv.ref, {"from": tv.status}, conn=conn)
        store.change("tool", tool_id, ["state", "catalog"], conn=conn)
    return store.get_version(tool_id, version)


def new_version_from(store, tool_id: str, version: int, *, actor: str, session_name: str = "") -> ToolVersion:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    manifest = dict(tv.manifest)
    manifest.pop("version", None)
    return create_draft(store, actor=actor, manifest=manifest, files=files_of(store, tv), tool_id=tool_id,
                        intent=f"New version of {tv.ref}", session_name=session_name)


def delete_draft(store, tool_id: str, version: int, *, actor: str) -> None:
    tv = store.get_version(tool_id, version)
    if tv is None:
        raise CatalogError(f"unknown tool version {tool_id}@{version}")
    if tv.status not in ("draft", "testing", "in_review"):
        raise CatalogError("only unpublished versions can be discarded")
    with store.tx() as conn:
        conn.execute("DELETE FROM tool_versions WHERE tool_id=? AND version=?", (tool_id, version))
        left = conn.execute("SELECT COUNT(*) AS c FROM tool_versions WHERE tool_id=?", (tool_id,)).fetchone()["c"]
        if not left:
            conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
        store.audit(actor, "tool.draft.discard", tv.ref, {}, conn=conn)
        store.change("tool", tool_id, ["state", "catalog"], conn=conn)


def search(store, query: str = "", *, include_drafts: bool = False, limit: int = 50) -> List[ToolVersion]:
    """Latest relevant version per tool, ranked by a simple term match over id/title/summary."""
    terms = [t for t in str(query or "").lower().split() if t]
    statuses = None if include_drafts else ["published", "deprecated"]
    latest: Dict[str, ToolVersion] = {}
    for tv in store.list_versions(statuses=statuses):
        if tv.status == "retired":
            continue
        prior = latest.get(tv.tool_id)
        if prior is None or (tv.status == "published") or prior.status not in ("published",):
            latest[tv.tool_id] = tv
    scored = []
    for tv in latest.values():
        hay = " ".join([tv.tool_id, str(tv.manifest.get("title") or ""), str(tv.manifest.get("summary") or ""),
                        str(tv.manifest.get("category") or "")]).lower()
        score = sum(hay.count(t) for t in terms) if terms else 1
        if score:
            scored.append((score, tv))
    scored.sort(key=lambda s: (-s[0], s[1].tool_id))
    return [tv for _, tv in scored[:limit]]


# --------------------------------------------------------------------- runbooks

class DraftConflict(CatalogError):
    """The open draft changed since the editor loaded it (HTTP 409 with the current draft)."""

    def __init__(self, message: str, current: Optional[RunbookVersion]):
        super().__init__(message)
        self.current = current


def _resolver(store):
    return lambda tool_id, version: store.get_version(tool_id, version)


def _latest_published(store):
    cache: Dict[str, Optional[int]] = {}

    def latest(tool_id: str) -> Optional[int]:
        if tool_id not in cache:
            tv = store.latest_version(tool_id, published_only=True)
            cache[tool_id] = tv.version if tv else None
        return cache[tool_id]
    return latest


def runbook_digest(spec: Mapping[str, Any], source: str = "") -> str:
    """The digest the editor's ``base_digest`` and the runbook-test freeze compare. A draft whose
    YAML doesn't parse also covers the raw text, so editing broken YAML is still a change."""
    return digest(spec) if not source else digest({"spec": spec, "source": source})


def _open_draft(store, rid: str) -> Optional[RunbookVersion]:
    found = [v for v in store.list_runbook_versions(rid) if v.status in ("draft", "in_review")]
    return found[-1] if found else None


def save_runbook_draft(store, *, actor: str, spec: Optional[Mapping[str, Any]] = None, base_digest: str = "",
                       source: str = "", runbook_id: str = "") -> RunbookVersion:
    """Create the next draft version or update the open one.

    ``source`` is the operator's raw YAML when it doesn't parse: it is stored as is and the draft
    keeps its last valid ``spec`` (a draft can always be saved; it just can't be submitted). With
    ``base_digest`` the save is refused when the open draft changed since the editor loaded it."""
    raw = dict(spec or {})
    rid = str(raw.get("id") or runbook_id or "")
    if runbook_id and raw.get("id") and raw["id"] != runbook_id:
        raise CatalogError("a runbook's id cannot change; create a new runbook instead")
    if not mf.TOOL_ID.match(rid):
        raise CatalogError("runbook id must be a dotted lowercase name")
    if spec is None and not source:
        raise CatalogError("nothing to save")
    resolve, latest = _resolver(store), _latest_published(store)
    with store.tx() as conn:
        rows = conn.execute("SELECT * FROM runbook_versions WHERE runbook_id=? ORDER BY version",
                            (rid,)).fetchall()
        versions = [RunbookVersion.from_row(r) for r in rows]
        target = next((v for v in reversed(versions) if v.status in ("draft", "in_review")), None)
        if base_digest and (target is None or target.digest != base_digest):
            raise DraftConflict("the draft changed since you opened it; reload it (your edits are kept in the editor)",
                                target)
        if target is not None and target.status != "draft":
            raise CatalogError(f"{target.ref} is in review; request changes before editing")
        version = target.version if target is not None else (max((v.version for v in versions), default=0) + 1)
        if spec is None:
            # Unparseable YAML: keep the last valid spec (or the newest version's) beside the text.
            base = target or (versions[-1] if versions else None)
            normalized = dict(base.spec) if base else {"id": rid, "title": "", "steps": []}
            normalized["version"] = version
        else:
            normalized, _problems, _info = rb_mod.analyze(raw, version=version, resolve_tool=resolve,
                                                          latest_published=latest)
        new_digest = runbook_digest(normalized, source)
        payload = {"digest": new_digest, "parses": not source}
        if target is not None:
            store.update_runbook_version(rid, version, conn, spec=normalized, digest=new_digest, source=source)
            store.audit(actor, "runbook.draft.update", f"{rid}@{version}", payload, conn=conn)
        else:
            store.insert_runbook_version(RunbookVersion(runbook_id=rid, version=version, status="draft",
                                                        spec=normalized, digest=new_digest, created_by=actor,
                                                        source=source), conn)
            store.audit(actor, "runbook.draft.create", f"{rid}@{version}", payload, conn=conn)
        store.change("tool", rid, ["catalog"], conn=conn)
    return store.get_runbook_version(rid, version)


def runbook_problem_objects(store, rb: RunbookVersion, *, for_review: bool = False,
                            positions: Optional[Mapping[str, Tuple[int, int]]] = None) -> List[Dict[str, Any]]:
    """Structured problems of a stored version, including a YAML parse error when ``source`` is set."""
    problems: List[Dict[str, Any]] = []
    if rb.source:
        _data, err = rb_mod.parse_yaml(rb.source)
        problems.append(dict(rb_mod.problem("", (err or {}).get("message") or "the YAML is not a runbook mapping"),
                             line=(err or {}).get("line"), column=(err or {}).get("column")))
    _spec, found, _info = rb_mod.analyze(rb.spec, version=rb.version, resolve_tool=_resolver(store),
                                         allow_in_review=for_review, latest_published=_latest_published(store))
    for p in found:
        item = dict(p)
        if positions:
            pos = rb_mod.locate(p["path"], positions)
            if pos:
                item.update(line=pos[0], column=pos[1])
        problems.append(item)
    return problems


def runbook_problems(store, rb: RunbookVersion, *, for_review: bool = False) -> List[str]:
    """Blocking problems (errors) as ``"path: message"`` strings."""
    return [rb_mod.problem_text(p) for p in rb_mod.errors_of(runbook_problem_objects(store, rb, for_review=for_review))]


def runbook_action_list(store, rb: RunbookVersion, *, has_canary: bool = False):
    """The runbook's ActionVMs with problems and the open-draft rule filled in."""
    from . import actions as act
    open_ = _open_draft(store, rb.runbook_id)
    return act.runbook_actions(rb, runbook_problems(store, rb), runbook_problems(store, rb, for_review=True),
                               has_canary=has_canary,
                               open_draft=open_.ref if open_ is not None and open_.version != rb.version else "")


def validate_runbook(store, spec: Any, *, actor: str = "operator", preview: Optional[Mapping[str, Any]] = None,
                     rules=None) -> Dict[str, Any]:
    """The editor's checks without saving anything: normalized spec, problems with paths (and YAML
    line/column when ``spec`` is YAML text), computed effect, the access decision preview and,
    per step, the resolved tool. ``preview: {node_id, inputs}`` evaluates params and ``when:``
    for that node with the real evaluator."""
    from . import access, vm
    source = spec if isinstance(spec, str) else ""
    yaml_error = None
    positions: Dict[str, Tuple[int, int]] = {}
    if isinstance(spec, str):
        data, yaml_error = rb_mod.parse_yaml(spec)
        if yaml_error is None and not isinstance(data, Mapping):
            yaml_error = {"message": "the YAML must be a mapping with id, title, inputs and steps", "line": 1,
                          "column": 1}
        if yaml_error is not None:
            return {"ok": False, "parses": False, "yaml_error": yaml_error, "spec": None, "steps_info": [],
                    "problems": [dict(rb_mod.problem("", yaml_error["message"]), line=yaml_error.get("line"),
                                      column=yaml_error.get("column"))],
                    "effect": None, "decision": None, "preview": None, "yaml": source}
        positions = rb_mod.yaml_positions(spec)
        spec = data
    raw = dict(spec or {}) if isinstance(spec, Mapping) else spec
    normalized, problems, info = rb_mod.analyze(raw, resolve_tool=_resolver(store), allow_in_review=True,
                                                latest_published=_latest_published(store))
    for p in problems:
        pos = rb_mod.locate(p["path"], positions) if positions else None
        if pos:
            p.update(line=pos[0], column=pos[1])
    for step in info:
        if step.get("ok"):
            step["effect_style"] = vm.effect_style(step["effect"])
    effect = normalized.get("effect", "read_only")
    approval = "confirm" if any(s.get("approval") == "confirm" for s in info) else "none"
    if rules is None:
        from .runs import effective_rules
        rules = effective_rules(store)
    decision = access.decide(rules, actor, {"id": normalized.get("id"), "effect": effect, "category": "other",
                                            "approval": approval}, []).to_dict()
    out = {"ok": not rb_mod.errors_of(problems), "parses": True, "yaml_error": None, "spec": normalized,
           "yaml": to_yaml_spec(normalized), "problems": problems, "steps_info": info, "effect": effect,
           "effect_style": vm.effect_style(effect), "decision": decision,
           "digest": runbook_digest(normalized), "preview": None}
    if preview:
        node = store.get_node(str(preview.get("node_id") or "")) if preview.get("node_id") else None
        node_view = {"id": node.id, "name": node.name, "labels": node.labels, "facts": node.facts} if node else \
            {"id": "", "name": "", "labels": dict(preview.get("labels") or {}), "facts": {}}
        out["preview"] = rb_mod.preview(normalized, node=node_view, inputs=preview.get("inputs") or {})
    return out


def to_yaml_spec(spec: Mapping[str, Any]) -> str:
    return mf.to_yaml(spec)


def discard_runbook_draft(store, runbook_id: str, version: int, *, actor: str) -> None:
    rb = store.get_runbook_version(runbook_id, version)
    if rb is None:
        raise CatalogError(f"unknown runbook {runbook_id}@{version}")
    if rb.status not in ("draft", "in_review"):
        raise CatalogError("only drafts and versions in review can be discarded")
    with store.tx() as conn:
        conn.execute("DELETE FROM runbook_versions WHERE runbook_id=? AND version=?", (runbook_id, version))
        left = conn.execute("SELECT COUNT(*) AS c FROM runbook_versions WHERE runbook_id=?",
                            (runbook_id,)).fetchone()["c"]
        if not left:
            conn.execute("DELETE FROM runbooks WHERE id=?", (runbook_id,))
        store.audit(actor, "runbook.draft.discard", rb.ref, {"digest": rb.digest}, conn=conn)
        store.change("tool", runbook_id, ["catalog"], conn=conn)


def new_runbook_version(store, runbook_id: str, version: int, *, actor: str,
                        spec: Optional[Mapping[str, Any]] = None) -> RunbookVersion:
    """Copy a published or deprecated version (or ``spec``, e.g. with upgraded tools) into the next draft."""
    rb = store.get_runbook_version(runbook_id, version)
    if rb is None:
        raise CatalogError(f"unknown runbook {runbook_id}@{version}")
    if rb.status not in ("published", "deprecated"):
        raise CatalogError("new versions start from a published or deprecated version")
    open_ = _open_draft(store, runbook_id)
    if open_ is not None:
        raise CatalogError(f"{open_.ref} is already open as a {open_.status.replace('_', ' ')} version")
    body = dict(spec or rb.spec)
    body.pop("version", None)
    body["id"] = runbook_id
    out = save_runbook_draft(store, actor=actor, spec=body)
    store.audit(actor, "runbook.new_version", out.ref, {"from": rb.ref})
    return out


def upgrade_runbook_tools(store, spec: Mapping[str, Any], *, steps: Optional[List[Any]] = None,
                          actor: str = "operator") -> Dict[str, Any]:
    """Read-only: every step whose pinned tool has a newer published version, with the manifest
    diff and what changes (effect and access decision, parameters the new schema rejects, removed
    outputs that later steps reference, rollback added or removed). ``steps`` (indexes or names)
    selects which to apply to the returned spec; default all. Nothing is stored."""
    from . import access
    from .runs import effective_rules
    from .vm import _diff, effect_style
    raw = dict(spec or {})
    resolve, latest = _resolver(store), _latest_published(store)
    before, _p, before_info = rb_mod.analyze(raw, resolve_tool=resolve, latest_published=latest)
    selected = None if steps is None else {str(s) for s in steps}
    upgraded = dict(raw)
    upgraded_steps = [dict(s) for s in (raw.get("steps") or []) if isinstance(s, Mapping)]
    report = []
    for info in before_info:
        if not info.get("ok") or not info.get("latest") or info["latest"] <= info["version"]:
            continue
        i, name = info["index"], info["name"]
        old = store.get_version(info["tool_id"], info["version"])
        new = store.get_version(info["tool_id"], info["latest"])
        if old is None or new is None:
            continue
        chosen = selected is None or str(i) in selected or name in selected
        params = (upgraded_steps[i].get("params") or {}) if i < len(upgraded_steps) else {}
        rejected: List[Dict[str, str]] = []
        rb_mod._check_params(params, new.manifest.get("inputs") or {}, f"steps[{i}].params", new.ref, rejected)
        old_out = set(((old.manifest.get("outputs") or {}).get("properties") or {}).keys())
        new_out = set(((new.manifest.get("outputs") or {}).get("properties") or {}).keys())
        removed = sorted(old_out - new_out)
        referenced = []
        for later in upgraded_steps[i + 1:]:
            texts = [e for _path, e in rb_mod._templates(later.get("params") or {}, "")] + \
                    ([later["when"]] if later.get("when") else [])
            for expr in texts:
                try:
                    refs = rb_mod.references(expr)
                except rb_mod.ExprError:
                    continue
                for ref in refs:
                    if len(ref) > 3 and ref[:3] == ["steps", name, "outputs"] and ref[3] in removed:
                        referenced.append({"step": later.get("name"), "output": ref[3]})
        report.append({
            "index": i, "name": name, "from": old.ref, "to": new.ref, "selected": chosen,
            "diff": _diff(mf.to_yaml(old.manifest), mf.to_yaml(new.manifest), "tool.yaml"),
            "effect": {"from": old.effect, "to": new.effect, "changed": old.effect != new.effect,
                       "from_style": effect_style(old.effect), "to_style": effect_style(new.effect)},
            "rejected_params": rejected, "removed_outputs": removed, "removed_referenced": referenced,
            "rollback": {"from": old.manifest.get("rollback") or "", "to": new.manifest.get("rollback") or ""},
            "check_mode": {"from": bool((old.manifest.get("implementation") or {}).get("check_mode")),
                           "to": bool((new.manifest.get("implementation") or {}).get("check_mode"))},
        })
        if chosen and i < len(upgraded_steps):
            upgraded_steps[i]["tool"] = new.ref
    upgraded["steps"] = upgraded_steps
    after, problems, _after_info = rb_mod.analyze(upgraded, resolve_tool=resolve, latest_published=latest)
    rules = effective_rules(store)

    def decision(s: Mapping[str, Any]) -> Dict[str, Any]:
        return access.decide(rules, actor, {"id": s.get("id"), "effect": s.get("effect"), "category": "other"},
                             []).to_dict()
    return {"steps": report, "spec": upgraded,
            "effect": {"from": before.get("effect"), "to": after.get("effect"),
                       "changed": before.get("effect") != after.get("effect"),
                       "from_style": effect_style(before.get("effect", "read_only")),
                       "to_style": effect_style(after.get("effect", "read_only"))},
            "decision": {"from": decision(before), "to": decision(after)},
            "problems": problems}


def set_runbook_status(store, runbook_id: str, version: int, target: str, *, actor: str) -> RunbookVersion:
    rb = store.get_runbook_version(runbook_id, version)
    if rb is None:
        raise CatalogError(f"unknown runbook {runbook_id}@{version}")
    if target == "published" and str(actor).startswith("ai"):
        raise CatalogError("only the operator can publish runbooks")
    if target in ("in_review", "published"):
        if rb.source:
            raise CatalogError("the draft's YAML doesn't parse; fix it before submitting")
        problems = runbook_problems(store, rb, for_review=target == "in_review")
        if problems:
            raise CatalogError("runbook is invalid: " + "; ".join(problems[:5]))
    check_runbook(rb.status, target)
    fields: Dict[str, Any] = {"status": target}
    if target == "published":
        fields["published_at"] = store.now()
    with store.tx() as conn:
        store.update_runbook_version(runbook_id, version, conn, **fields)
        store.audit(actor, f"runbook.{target}", rb.ref, {"from": rb.status, "digest": rb.digest}, conn=conn)
        store.change("tool", runbook_id, ["catalog"], conn=conn)
    return store.get_runbook_version(runbook_id, version)
