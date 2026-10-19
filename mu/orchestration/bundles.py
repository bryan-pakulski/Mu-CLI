"""Import / export (plan §7): signed bundles and a config-as-code directory.

Layout (identical for ``*.mucli-orch.tar.zst`` / ``.tar.gz`` and ``--dir``)::

    manifest.json                     format, created_by/at, counts, bundle digest
    tools/<id>/<version>/tool.yaml    + the tool's files
    runbooks/<id>/<version>.yaml
    policies/access.yaml              access policy rules
    policies/defaults.yaml            rollout defaults
    groups.yaml                       label selectors (never machines)
    schedules.yaml                    exported disabled
    SIGNATURE                         Ed25519 over the bundle digest (JSON: key_id, public_key, signature)

The bundle digest is SHA-256 over the sorted ``(path, sha256)`` list of every other file, so
any added, removed or edited file breaks it. Never exported: secret values (manifests carry
names only), node credentials and identities, keys, audit internals.

Import shows a diff (new / identical / conflict per version). Imported versions arrive
``in_review`` unless the bundle is signed by a key the operator marked trusted, in which case
they are published directly (still linted and audited). Version numbers are never
overwritten: a conflicting version becomes the next free number, and pinned references inside
the bundle (rollbacks, preconditions, runbook steps) are rewritten to match.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from . import access, catalog, manifest as mf, runbook as rb_mod
from .models import RunbookVersion, ToolVersion
from .node.canonical import canonical, digest, sha256_hex
from .node.envelope import b64d, bundle_payload, key_id
from .signing import verify_with

FORMAT = "mucli-orch-bundle"
FORMAT_VERSION = 1
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_FILES = 5000


class BundleError(ValueError):
    pass


def _dump(data: Any) -> bytes:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100).encode("utf-8")


def _tree_digest(files: Mapping[str, bytes]) -> str:
    entries = sorted((path, sha256_hex(data)) for path, data in files.items()
                     if path not in ("manifest.json", "SIGNATURE"))
    return "sha256:" + sha256_hex(canonical(entries))


def _content_key(manifest: Mapping[str, Any], blobs_or_files: Mapping[str, Any]) -> str:
    m = {k: v for k, v in manifest.items() if k != "version"}
    files = {n: (v if isinstance(v, str) else sha256_hex(v)) for n, v in blobs_or_files.items()}
    return digest({"manifest": m, "files": files})


# --------------------------------------------------------------------- export

def collect(orch, *, include_drafts: bool = False, include_policies: bool = True,
            include_inventory: bool = False) -> Dict[str, bytes]:
    store = orch.store
    files: Dict[str, bytes] = {}
    statuses = ["published", "deprecated"] + (["draft", "testing", "in_review"] if include_drafts else [])
    tools = store.list_versions(statuses=statuses)
    for tv in tools:
        base = f"tools/{tv.tool_id}/{tv.version}"
        files[f"{base}/tool.yaml"] = _dump(tv.manifest)
        for name, data in catalog.files_of(store, tv).items():
            files[f"{base}/{name}"] = data
    rbs = store.list_runbook_versions(statuses=["published", "deprecated"] + (["draft", "in_review"] if include_drafts else []))
    for rb in rbs:
        spec = {k: v for k, v in rb.spec.items() if k != "effect"}
        files[f"runbooks/{rb.runbook_id}/{rb.version}.yaml"] = _dump(spec)
    if include_policies:
        files["policies/access.yaml"] = _dump({"rules": _rules(orch)})
        files["policies/defaults.yaml"] = _dump(orch.defaults())
        files["groups.yaml"] = _dump({"groups": [{"name": g["name"], "selector": g["selector"],
                                                  "description": g["description"]} for g in store.list_groups()]})
        files["schedules.yaml"] = _dump({"schedules": [
            {"name": s["name"], "kind": s["kind"], "spec": s["spec"], "target": s["target"]}
            for s in store.list_schedules()]})
    if include_inventory:
        inventory: Dict[str, Dict[str, int]] = {}
        for node in store.list_nodes(statuses=["active"]):
            for key, value in node.labels.items():
                inventory.setdefault(key, {})
                inventory[key][value] = inventory[key].get(value, 0) + 1
        files["inventory.yaml"] = _dump({"note": "anonymised label counts; no node identities",
                                         "labels": inventory})
    tree = _tree_digest(files)
    files["manifest.json"] = json.dumps({
        "format": FORMAT, "format_version": FORMAT_VERSION, "created_at": time.time(),
        "created_by": orch.keys.signing_key_id, "digest": tree,
        "counts": {"tools": len(tools), "runbooks": len(rbs)},
    }, indent=2, sort_keys=True).encode("utf-8")
    files["SIGNATURE"] = json.dumps(orch.keys.sign_bundle(tree), indent=2).encode("utf-8")
    return files


def _rules(orch) -> List[Dict[str, Any]]:
    from .runs import effective_rules
    return effective_rules(orch.store)


def _zstd():
    try:
        import zstandard  # type: ignore
        return zstandard
    except ImportError:
        return None


def export_bundle(orch, path: str, **kwargs) -> Dict[str, Any]:
    files = collect(orch, **kwargs)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(files[name]))
    data = raw.getvalue()
    zstd = _zstd()
    if path.endswith(".zst") and zstd is None:
        path = path[:-4] + ".gz"
    if path.endswith(".zst"):
        data = zstd.ZstdCompressor(level=10).compress(data)
    else:
        import gzip
        data = gzip.compress(data, mtime=0)
    with open(path, "wb") as fh:
        fh.write(data)
    orch.store.audit("operator", "bundle.export", os.path.basename(path), {"digest": json.loads(files["manifest.json"])["digest"]})
    return {"path": path, "bytes": len(data), "manifest": json.loads(files["manifest.json"])}


def export_dir(orch, directory: str, **kwargs) -> Dict[str, Any]:
    files = collect(orch, **kwargs)
    for name, data in files.items():
        target = os.path.join(directory, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
    orch.store.audit("operator", "bundle.export_dir", directory, {"digest": json.loads(files["manifest.json"])["digest"]})
    return {"path": directory, "files": len(files), "manifest": json.loads(files["manifest.json"])}


# --------------------------------------------------------------------- read

def read_bundle_bytes(data: bytes) -> Dict[str, bytes]:
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError("bundle is larger than 64 MiB")
    if data[:4] == b"\x28\xb5\x2f\xfd":
        zstd = _zstd()
        if zstd is None:
            raise BundleError("this bundle is zstd-compressed; install the 'zstandard' package to read it")
        data = zstd.ZstdDecompressor().decompress(data, max_output_size=MAX_BUNDLE_BYTES * 4)
    elif data[:2] == b"\x1f\x8b":
        import gzip
        data = gzip.decompress(data)
    files: Dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = os.path.normpath(member.name)
            if name.startswith(("/", "..")) or "/../" in f"/{name}/":
                raise BundleError(f"unsafe path in bundle: {member.name}")
            if len(files) >= MAX_FILES:
                raise BundleError("too many files in bundle")
            extracted = tar.extractfile(member)
            files[name] = extracted.read() if extracted else b""
    return files


def read_dir(directory: str) -> Dict[str, bytes]:
    files: Dict[str, bytes] = {}
    root = os.path.abspath(directory)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path):
                raise BundleError(f"symlinks are not allowed in a bundle: {rel}")
            with open(path, "rb") as fh:
                files[rel.replace(os.sep, "/")] = fh.read()
            if len(files) > MAX_FILES:
                raise BundleError("too many files")
    return files


def read_source(path: str = "", data: Optional[bytes] = None) -> Dict[str, bytes]:
    if data is not None:
        if isinstance(data, str):
            import base64
            data = base64.b64decode(data)
        return read_bundle_bytes(data)
    if not path:
        raise BundleError("give a bundle path or directory")
    if os.path.isdir(path):
        return read_dir(path)
    with open(path, "rb") as fh:
        return read_bundle_bytes(fh.read())


# --------------------------------------------------------------------- verify / parse

def verify(orch, files: Mapping[str, bytes]) -> Dict[str, Any]:
    """Integrity (digest tree) and trust (signature by a trusted key)."""
    out: Dict[str, Any] = {"signed": False, "trusted": False, "key_id": "", "intact": False, "problems": []}
    if "manifest.json" not in files:
        # A hand-written config-as-code directory: allowed, unsigned and untrusted.
        out["problems"].append("no manifest.json: treated as an unsigned directory")
        out["intact"] = True
        return out
    try:
        meta = json.loads(files["manifest.json"])
    except ValueError:
        raise BundleError("manifest.json is not valid JSON")
    if meta.get("format") != FORMAT:
        raise BundleError("not a mucli orchestration bundle")
    if int(meta.get("format_version") or 0) > FORMAT_VERSION:
        raise BundleError("bundle format is newer than this mucli")
    computed = _tree_digest(files)
    out["intact"] = computed == meta.get("digest")
    if not out["intact"]:
        out["problems"].append("bundle contents do not match its digest (modified after export)")
    out["digest"] = computed
    if "SIGNATURE" in files:
        try:
            sig = json.loads(files["SIGNATURE"])
            public = b64d(sig["public_key"])
            out["key_id"] = key_id(public)
            out["signed"] = verify_with(public, bundle_payload(str(meta.get("digest"))), sig["signature"])
        except (ValueError, KeyError, TypeError):
            out["problems"].append("SIGNATURE is malformed")
        if not out["signed"]:
            out["problems"].append("signature does not verify")
        trusted = {k["key_id"]: k["public_key"] for k in orch.store.trusted_keys()}
        out["trusted"] = bool(out["signed"] and out["intact"] and out["key_id"] in trusted
                              and trusted[out["key_id"]] == sig.get("public_key"))
    return out


def parse(files: Mapping[str, bytes]) -> Dict[str, Any]:
    tools: Dict[Tuple[str, int], Dict[str, Any]] = {}
    runbooks: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for path, data in files.items():
        parts = path.split("/")
        if parts[0] == "tools" and len(parts) == 4:
            try:
                key = (parts[1], int(parts[2]))
            except ValueError:
                raise BundleError(f"bad tool path {path}")
            entry = tools.setdefault(key, {"manifest": None, "files": {}})
            if parts[3] == "tool.yaml":
                entry["manifest"] = yaml.safe_load(data) or {}
            else:
                entry["files"][parts[3]] = data
        elif parts[0] == "runbooks" and len(parts) == 3 and parts[2].endswith(".yaml"):
            try:
                key = (parts[1], int(parts[2][:-5]))
            except ValueError:
                raise BundleError(f"bad runbook path {path}")
            runbooks[key] = yaml.safe_load(data) or {}
    for key, entry in tools.items():
        if entry["manifest"] is None:
            raise BundleError(f"tools/{key[0]}/{key[1]} has no tool.yaml")
        if entry["manifest"].get("id") not in (None, key[0]):
            raise BundleError(f"tools/{key[0]}/{key[1]}/tool.yaml declares a different id")
    out = {"tools": tools, "runbooks": runbooks}
    for name in ("policies/access.yaml", "policies/defaults.yaml", "groups.yaml", "schedules.yaml"):
        if name in files:
            out[name] = yaml.safe_load(files[name]) or {}
    return out


# --------------------------------------------------------------------- lint (CI)

def lint_source(path: str) -> Dict[str, Any]:
    """``mucli orch lint ./ops``: validate every tool and runbook without a master."""
    from . import lint as lint_mod
    files = read_source(path)
    parsed = parse(files)
    report: Dict[str, Any] = {"tools": {}, "runbooks": {}, "ok": True}
    known = {f"{k[0]}@{k[1]}": v for k, v in parsed["tools"].items()}
    for (tool_id, version), entry in sorted(parsed["tools"].items()):
        raw = dict(entry["manifest"], id=tool_id, version=version)
        result = lint_mod.lint(raw, entry["files"])
        report["tools"][f"{tool_id}@{version}"] = result
        report["ok"] = report["ok"] and result["ok"]

    def resolve(tool_id: str, version: int):
        entry = known.get(f"{tool_id}@{version}")
        if entry is None:
            return None
        manifest, _ = mf.normalize(dict(entry["manifest"], id=tool_id), version=version)
        return ToolVersion(tool_id=tool_id, version=version, status="published", manifest=manifest)

    for (rid, version), spec in sorted(parsed["runbooks"].items()):
        _spec, errors = rb_mod.normalize(dict(spec, id=rid), version=version, resolve_tool=resolve)
        report["runbooks"][f"{rid}@{version}"] = {"ok": not errors, "errors": errors}
        report["ok"] = report["ok"] and not errors
    if "policies/access.yaml" in parsed:
        problems = access.validate_rules((parsed["policies/access.yaml"] or {}).get("rules"))
        report["access_policy"] = {"ok": not problems, "errors": problems}
        report["ok"] = report["ok"] and not problems
    return report


# --------------------------------------------------------------------- import

def _deps(manifest: Mapping[str, Any]) -> List[str]:
    return [r for r in [manifest.get("rollback")] + list(manifest.get("preconditions") or []) if r]


def _dependency_order(tools: Mapping[Tuple[str, int], Dict[str, Any]]) -> List[Tuple[str, int]]:
    """Bundle tool versions with referenced versions (rollbacks, preconditions) first."""
    remaining = sorted(tools)
    ordered: List[Tuple[str, int]] = []
    while remaining:
        pending_refs = {f"{t}@{v}" for t, v in remaining}
        ready = [k for k in remaining
                 if not any(r in pending_refs and r != f"{k[0]}@{k[1]}" for r in _deps(tools[k]["manifest"]))]
        if not ready:          # a reference cycle: keep the remaining order
            ordered += remaining
            break
        ordered += ready
        remaining = [k for k in remaining if k not in ready]
    return ordered


def _rewrite_refs(raw: Dict[str, Any], mapping: Mapping[str, str]) -> Dict[str, Any]:
    raw = dict(raw)
    if raw.get("rollback"):
        raw["rollback"] = mapping.get(raw["rollback"], raw["rollback"])
    raw["preconditions"] = [mapping.get(r, r) for r in raw.get("preconditions") or []]
    return raw


def plan_import(orch, files: Mapping[str, bytes]) -> Dict[str, Any]:
    """Diff a bundle against the catalog and decide each version's local number.

    Versions are resolved in dependency order, rewriting pinned references through the mapping
    built so far, so a re-import of an already imported bundle is recognised as identical.
    """
    store = orch.store
    trust = verify(orch, files)
    parsed = parse(files)
    diff: Dict[str, Any] = {"tools": [], "runbooks": [], "policies": {}, "groups": [], "schedules": []}
    mapping: Dict[str, str] = {}
    prepared: Dict[str, Dict[str, Any]] = {}
    next_free: Dict[str, int] = {}
    for tool_id, version in _dependency_order(parsed["tools"]):
        entry = parsed["tools"][(tool_id, version)]
        raw = _rewrite_refs(dict(entry["manifest"], id=tool_id), mapping)
        raw.pop("version", None)
        normalized, errors = mf.normalize(raw, version=version)
        key = _content_key(normalized, entry["files"])
        locals_ = store.list_versions(tool_id)
        match = next((v for v in locals_ if _content_key(v.manifest, catalog.files_of(store, v)) == key), None)
        if match is not None:
            status, number = "identical", match.version
        else:
            free = next_free.get(tool_id, max((v.version for v in locals_), default=0) + 1)
            existing = store.get_version(tool_id, version)
            number = version if existing is None and version >= free else free
            status = "new" if existing is None else "conflict"
            next_free[tool_id] = number + 1
        ref = f"{tool_id}@{version}"
        mapping[ref] = f"{tool_id}@{number}"
        prepared[ref] = raw
        diff["tools"].append({"ref": ref, "tool_id": tool_id, "version": version, "status": status,
                              "local_version": number, "errors": errors, "effect": normalized.get("effect"),
                              "title": normalized.get("title")})
    for (rid, version), spec in sorted(parsed["runbooks"].items()):
        existing = store.get_runbook_version(rid, version)
        steps = [dict(s, tool=mapping.get(str(s.get("tool") or ""), s.get("tool"))) for s in spec.get("steps") or []]
        candidate = {k: v for k, v in dict(spec, id=rid, steps=steps).items() if k not in ("effect", "version")}
        same = any({k: v for k, v in rb.spec.items() if k not in ("effect", "version")} ==
                   {k: v for k, v in rb_mod.normalize(candidate, version=rb.version)[0].items()
                    if k not in ("effect", "version")} for rb in store.list_runbook_versions(rid))
        diff["runbooks"].append({"ref": f"{rid}@{version}", "runbook_id": rid, "version": version,
                                 "status": "identical" if same else ("new" if existing is None else "conflict")})
    access_doc = parsed.get("policies/access.yaml")
    if access_doc is not None:
        rules = access_doc.get("rules")
        diff["policies"]["access"] = {"changed": rules != _rules(orch), "errors": access.validate_rules(rules),
                                      "rules": rules}
    groups = {g["name"]: g for g in store.list_groups()}
    for group in (parsed.get("groups.yaml") or {}).get("groups") or []:
        current = groups.get(group.get("name"))
        diff["groups"].append({"name": group.get("name"), "selector": group.get("selector"),
                               "status": "new" if current is None else
                               ("identical" if current["selector"] == group.get("selector") else "changed")})
    for item in (parsed.get("schedules.yaml") or {}).get("schedules") or []:
        diff["schedules"].append({"name": item.get("name"), "kind": item.get("kind")})
    return {"trust": trust, "diff": diff, "parsed": parsed, "mapping": mapping, "prepared": prepared}


def import_bundle(orch, *, path: str = "", data: Optional[bytes] = None, actor: str = "operator",
                  apply: bool = True, apply_policies: bool = False) -> Dict[str, Any]:
    if str(actor).startswith("ai"):
        raise BundleError("only the operator imports bundles")
    files = read_source(path, data)
    plan = plan_import(orch, files)
    trust = plan["trust"]
    result = {"trust": trust, "diff": plan["diff"], "applied": False}
    if not apply:
        return result
    if not trust["intact"]:
        raise BundleError("refusing to import: " + "; ".join(trust["problems"]))
    store = orch.store
    parsed = plan["parsed"]
    publish = bool(trust["trusted"])
    mapping: Dict[str, str] = dict(plan["mapping"])
    imported_tools: List[str] = []

    def rewrite(ref: str) -> str:
        return mapping.get(ref, ref)

    # The diff is already in dependency order: publishing can verify each reference exists.
    for item in plan["diff"]["tools"]:
        if item["status"] == "identical":
            continue
        tool_id, version = item["tool_id"], item["version"]
        tv = _insert_version(store, tool_id, item["local_version"], plan["prepared"][item["ref"]],
                             parsed["tools"][(tool_id, version)]["files"], actor, origin_ref=item["ref"])
        mapping[item["ref"]] = tv.ref
        # Versions that fail lint stay drafts: the operator sees why in the Studio.
        if catalog.run_lint(store, tv.tool_id, tv.version, actor=actor)["ok"]:
            catalog.submit_for_review(store, tv.tool_id, tv.version, actor=actor, headline="Imported from a bundle",
                                      notes=f"bundle key {trust.get('key_id') or 'unsigned'}")
        tv = store.get_version(tv.tool_id, tv.version)
        if publish and tv.status == "in_review":
            try:
                catalog.publish(store, orch.keys, tv.tool_id, tv.version, actor=actor, typed=tv.tool_id)
            except catalog.CatalogError as exc:
                store.audit(actor, "bundle.publish_skipped", tv.ref, {"reason": str(exc)})
        imported_tools.append(tv.ref)
    imported_runbooks: List[str] = []
    for item in plan["diff"]["runbooks"]:
        if item["status"] == "identical":
            continue
        spec = dict(parsed["runbooks"][(item["runbook_id"], item["version"])], id=item["runbook_id"])
        spec.pop("version", None)
        spec["steps"] = [dict(s, tool=rewrite(str(s.get("tool") or ""))) for s in spec.get("steps") or []]
        with store.tx() as conn:
            number = store.next_runbook_version(item["runbook_id"], conn)
            if store.get_runbook_version(item["runbook_id"], item["version"]) is None and item["version"] >= number:
                number = item["version"]
            normalized, _errors = rb_mod.normalize(spec, version=number,
                                                   resolve_tool=lambda t, v: store.get_version(t, v))
            store.insert_runbook_version(RunbookVersion(runbook_id=item["runbook_id"], version=number, status="draft",
                                                        spec=normalized, digest=digest(normalized), created_by=actor,
                                                        origin="import"), conn)
            store.audit(actor, "runbook.import", f"{item['runbook_id']}@{number}", {"from": item["ref"]}, conn=conn)
        rb = store.get_runbook_version(item["runbook_id"], number)
        if not catalog.runbook_problems(store, rb, for_review=True):
            rb = catalog.set_runbook_status(store, rb.runbook_id, rb.version, "in_review", actor=actor)
            if publish:
                catalog.set_runbook_status(store, rb.runbook_id, rb.version, "published", actor=actor)
        imported_runbooks.append(rb.ref)
    for group in (parsed.get("groups.yaml") or {}).get("groups") or []:
        try:
            from . import selectors
            selectors.parse(str(group.get("selector") or ""))
            store.put_group(str(group["name"]), str(group["selector"]), str(group.get("description") or ""))
        except Exception as exc:  # noqa: BLE001 - one bad group must not abort the import
            store.audit(actor, "bundle.group_skipped", str(group.get("name")), {"reason": str(exc)})
    for item in (parsed.get("schedules.yaml") or {}).get("schedules") or []:
        try:
            target = dict(item.get("target") or {})
            if target.get("target"):
                target["target"] = rewrite(str(target["target"]))
            orch.put_schedule({"name": item["name"], "kind": item["kind"], "spec": item.get("spec") or {},
                               "target": target, "enabled": False}, actor=actor)
        except Exception as exc:  # noqa: BLE001
            store.audit(actor, "bundle.schedule_skipped", str(item.get("name")), {"reason": str(exc)})
    policy_applied = False
    access_doc = parsed.get("policies/access.yaml")
    if apply_policies and access_doc is not None and not access.validate_rules(access_doc.get("rules")):
        with store.tx() as conn:
            version = store.save_access_rules(access_doc["rules"], actor, conn)
            store.audit(actor, "policy.import", f"access@{version}", {"rules": access_doc["rules"]}, conn=conn)
        policy_applied = True
    store.audit(actor, "bundle.import", trust.get("digest", ""), {
        "tools": imported_tools, "runbooks": imported_runbooks, "trusted": trust["trusted"],
        "key_id": trust.get("key_id"), "published": publish, "policy_applied": policy_applied})
    store.change("tool", "", ["catalog"])
    result.update(applied=True, tools=imported_tools, runbooks=imported_runbooks, mapping=mapping,
                  published=publish, policy_applied=policy_applied)
    return result


def _insert_version(store, tool_id: str, version: int, raw: Dict[str, Any], files: Mapping[str, bytes],
                    actor: str, *, origin_ref: str = "") -> ToolVersion:
    blobs = {name: store.put_blob(data) for name, data in files.items() if mf.FILE_NAME.match(name)}
    with store.tx() as conn:
        number = store.next_version_number(tool_id, conn)
        if store.get_version(tool_id, version) is None and version >= number:
            number = version
        normalized, errors = mf.normalize(raw, version=number)
        now = store.now()
        tv = ToolVersion(tool_id=tool_id, version=number, status="draft", manifest=normalized,
                         manifest_digest=mf.digest_of(normalized), blobs=blobs, created_at=now, updated_at=now,
                         created_by=actor, stage="review", origin="import",
                         intent=f"Imported from a bundle ({origin_ref or tool_id})")
        store.insert_version(tv, conn)
        store.audit(actor, "tool.import", tv.ref, {"from": origin_ref, "digest": tv.manifest_digest,
                                                    "errors": errors[:5]}, conn=conn)
    return store.get_version(tool_id, number)
