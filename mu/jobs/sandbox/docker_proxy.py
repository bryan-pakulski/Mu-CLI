"""Per-stage Docker API policy proxy for build environments with ``docker = true``.

The stage never talks to its job engine directly. Its ``/var/run/docker.sock`` is this proxy's
Unix socket; the proxy forwards to the job's Sysbox engine and enforces (§7):

* every container, network, volume and image build is labelled ``io.mucli.job=<id>``;
* bind mounts only from ``/workspace`` (the job worktree) and ``/cache``, judged by real path;
* no host devices, no host network/pid/ipc/uts/userns namespaces, no ``--volumes-from``
  foreign containers, no touching containers this job did not create;
* ``--privileged`` is allowed: it is confined to the Sysbox engine;
* GPUs only within the binding's request (``DeviceRequests``);
* pulls only from registries on the job's egress allowlist (performed host-side and loaded
  into the engine, which itself has no route out);
* a storage cap for the engine;
* read-only stages (``ProxyContext.readonly``: checks; plan and review get no engine at all)
  cannot write the worktree: ``/workspace`` binds, bind volumes and ``--volumes-from`` are
  forced read-only, and containers the stage did not create itself (e.g. left running by
  implement with a writable ``/workspace``) can only be inspected, stopped or removed.

Decisions are made by the pure :func:`decide`, so they are unit-testable without Docker.
"""
from __future__ import annotations

import asyncio
import json
import os
import posixpath
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlencode, urlsplit

LABEL_JOB = "io.mucli.job"
LABEL_SESSION = "io.mucli.proxy-session"   # containers created through one read-only proxy
_VERSION_RE = re.compile(r"^/v\d+(?:\.\d+)?(?=/)")
_CONTAINER_OP_RE = re.compile(r"^/containers/(?P<id>[^/]+)(?:/(?P<op>[a-z]+))?$")
_HOST_MODES = ("NetworkMode", "PidMode", "IpcMode", "UTSMode", "UsernsMode", "CgroupnsMode")
MAX_JSON_BODY = 8 * 1024 * 1024


@dataclass
class ProxyContext:
    job_id: str
    worktree: str
    cache_dir: str = ""
    gpus: str = ""                       # "" | "all" | "device=0,1"
    egress: Tuple[str, ...] = ()
    owned: Callable[[str], Optional[bool]] = lambda ref: True   # container id/name -> labelled for this job?
    # Read-only stage: the worktree must not be writable through the engine.
    readonly: bool = False
    session: str = ""                    # LABEL_SESSION value of containers this proxy created
    session_owned: Callable[[str], bool] = lambda ref: False     # created through this proxy?
    volume_device: Callable[[str], Optional[str]] = lambda name: None  # named bind volume -> engine path


@dataclass
class Decision:
    allow: bool
    reason: str = ""
    body: Optional[Dict[str, Any]] = None        # rewritten JSON body
    query: Optional[Dict[str, List[str]]] = None  # rewritten query string
    action: str = ""                              # audit action name ("create", "start", "pull", ...)
    pull: str = ""                                # image reference to pull host-side
    container: str = ""


def strip_version(path: str) -> str:
    return _VERSION_RE.sub("", path or "") or "/"


def _within(path: str, root: str) -> bool:
    root = root.rstrip("/") or "/"
    return path == root or path.startswith(root + "/")


def host_path(ctx: ProxyContext, engine_path: str) -> Optional[str]:
    """Map an engine-side bind source to the host path behind it (None = not allowed)."""
    path = posixpath.normpath("/" + str(engine_path or "").lstrip("/"))
    for mount, host in (("/workspace", ctx.worktree), ("/cache", ctx.cache_dir)):
        if host and _within(path, mount):
            rest = path[len(mount):].lstrip("/")
            target = os.path.realpath(os.path.join(host, rest)) if rest else os.path.realpath(host)
            real_root = os.path.realpath(host)
            return target if _within(target, real_root) else None
    return None


def _bind_reason(ctx: ProxyContext, source: str) -> str:
    if not source.startswith("/"):
        return ""  # a named volume of this engine
    if host_path(ctx, source) is None:
        return f"bind mount {source} is outside /workspace and /cache"
    return ""


# ``local``-driver options a volume may carry. Anything else (nfs/cifs/overlay/block devices, a
# third-party driver) could mount engine paths outside /workspace and /cache — notably
# /run/mucli, the engine's own API socket directory, which would bypass this proxy.
_VOLUME_OPTION_KEYS = ("type", "o", "device")
_BIND_FLAGS = ("bind", "rbind")


def volume_options_reason(ctx: ProxyContext, driver: Any, options: Any) -> str:
    """Why a volume definition (``/volumes/create`` or an inline ``Mounts[].VolumeOptions.DriverConfig``)
    is refused; ``""`` = allowed. Bind-style options are judged exactly like bind mounts."""
    driver = str(driver or "local")
    if driver != "local":
        return f"volume driver {driver} is not allowed (only the local driver)"
    if options in (None, {}):
        return ""
    if not isinstance(options, dict):
        return "invalid volume driver options"
    extra = sorted(str(k) for k in options if str(k) not in _VOLUME_OPTION_KEYS)
    if extra:
        return f"volume driver options {', '.join(extra)} are not allowed"
    kind = str(options.get("type") or "").strip().lower()
    flags = [f.strip().lower() for f in str(options.get("o") or "").split(",") if f.strip()]
    device = str(options.get("device") or "")
    if any(f in _BIND_FLAGS for f in flags):
        if kind not in ("", "none"):
            return f"bind volumes of type {kind} are not allowed"
        if not device.startswith("/"):
            return "bind volumes need an absolute device path"
        return _bind_reason(ctx, device)
    if kind == "tmpfs" and device in ("", "tmpfs"):
        return ""
    return "volume driver options are limited to bind volumes of /workspace or /cache and tmpfs"


def _volume_device_of(options: Any) -> str:
    """Engine path a (validated) bind-volume option set points at, else ``""``."""
    if not isinstance(options, dict):
        return ""
    flags = [f.strip().lower() for f in str(options.get("o") or "").split(",")]
    return str(options.get("device") or "") if any(f in _BIND_FLAGS for f in flags) else ""


def _inline_volume_options(mount: Dict[str, Any]) -> Tuple[Any, Any]:
    config = ((mount or {}).get("VolumeOptions") or {}).get("DriverConfig") or {}
    return (config.get("Name") if isinstance(config, dict) else None,
            config.get("Options") if isinstance(config, dict) else config)


def _in_worktree(ctx: ProxyContext, source: str) -> bool:
    """Does an engine-side path (or a named bind volume) reach the job worktree?"""
    source = str(source or "")
    if not source.startswith("/"):
        device = ctx.volume_device(source) if source else None
        if not device:
            return False
        source = device
    path = posixpath.normpath("/" + source.lstrip("/"))
    return _within(path, "/workspace")


def _readonly_bind(bind: str) -> str:
    """``src:dst[:opts]`` with ``rw`` replaced by ``ro``."""
    parts = str(bind).split(":")
    if len(parts) < 2:
        return bind
    opts = [o for o in (parts[2].split(",") if len(parts) > 2 else []) if o and o not in ("rw", "ro")]
    return ":".join(parts[:2] + [",".join(["ro", *opts])])


def _readonly_create(body: Dict[str, Any], ctx: ProxyContext) -> Dict[str, Any]:
    """Force every worktree-reaching mount of a read-only stage's container to read-only."""
    host = dict(body.get("HostConfig") or {})
    if host.get("Binds"):
        host["Binds"] = [_readonly_bind(b) if _in_worktree(ctx, str(b).split(":", 1)[0]) else b
                         for b in host["Binds"]]
    mounts = []
    for mount in host.get("Mounts") or []:
        mount = dict(mount or {})
        _driver, options = _inline_volume_options(mount)
        inline = _volume_device_of(options)
        if _in_worktree(ctx, str(mount.get("Source") or "")) or (inline and _in_worktree(ctx, inline)):
            mount["ReadOnly"] = True
        mounts.append(mount)
    if mounts:
        host["Mounts"] = mounts
    if host.get("VolumesFrom"):
        # Another container's mounts may include a writable /workspace (e.g. one implement started).
        host["VolumesFrom"] = [str(ref).split(":", 1)[0] + ":ro" for ref in host["VolumesFrom"]]
    labels = dict(body.get("Labels") or {})
    if ctx.session:
        labels[LABEL_SESSION] = ctx.session
    return {**body, "HostConfig": host, "Labels": labels}


# Operations a read-only stage may run on a container it did not create itself.
_FOREIGN_READONLY_OPS = ("", "json", "logs", "top", "stats", "changes", "wait", "stop", "kill")


def _gpu_ids(request: str) -> Optional[List[str]]:
    """None = all GPUs; [] = none; else the device ids."""
    if request == "all":
        return None
    if request.startswith("device="):
        return [d for d in request.split("=", 1)[1].replace('"', "").split(",") if d]
    return []


def device_requests_reason(ctx: ProxyContext, requests: Any) -> str:
    if not requests:
        return ""
    if not isinstance(requests, list):
        return "invalid DeviceRequests"
    allowed = _gpu_ids(ctx.gpus) if ctx.gpus else []
    if allowed == []:
        return "this build environment has no GPUs"
    for request in requests:
        if not isinstance(request, dict):
            return "invalid DeviceRequests"
        caps = [c for group in request.get("Capabilities") or [] for c in (group or [])]
        if str(request.get("Driver") or "nvidia") not in ("nvidia", "") or (caps and "gpu" not in caps):
            return "only NVIDIA GPU device requests are allowed"
        ids = [str(i) for i in request.get("DeviceIDs") or []]
        count = int(request.get("Count") or 0)
        if allowed is None:
            continue
        if count == -1 or (not ids and count > len(allowed)):
            return f"GPU request exceeds the build environment's GPUs ({','.join(allowed)})"
        if any(i not in allowed for i in ids):
            return f"GPUs {','.join(i for i in ids if i not in allowed)} are not in the build environment's request"
    return ""


def _labelled(body: Dict[str, Any], job_id: str, key: str = "Labels") -> Dict[str, Any]:
    labels = dict(body.get(key) or {})
    labels[LABEL_JOB] = job_id
    return {**body, key: labels}


def registry_of(ref: str) -> str:
    first = str(ref or "").split("/", 1)[0]
    if "/" in str(ref) and ("." in first or ":" in first or first == "localhost"):
        return first.split("@")[0].lower()
    return "docker.io"


def registry_allowed(ref: str, egress: Sequence[str]) -> bool:
    registry = registry_of(ref).split(":")[0]
    candidates = {registry} | ({"registry-1.docker.io", "index.docker.io"} if registry == "docker.io" else set())
    for domain in egress or ():
        domain = str(domain).lower()
        for host in candidates:
            if domain == host or (domain.startswith("*.") and host.endswith(domain[1:])):
                return True
    return False


def decide(method: str, raw_path: str, query: Dict[str, List[str]], body: Optional[Dict[str, Any]],
           ctx: ProxyContext) -> Decision:
    """Policy for one Docker API request (pure; ``ctx.owned`` answers container ownership)."""
    method = method.upper()
    path = strip_version(raw_path)
    if path.startswith("/plugins/") and method == "POST":
        return Decision(False, "Docker plugins need host privileges and are not allowed", action="plugin")
    if path == "/containers/create" and method == "POST":
        if not isinstance(body, dict):
            return Decision(False, "invalid container create request", action="create")
        host = dict(body.get("HostConfig") or {})
        for bind in host.get("Binds") or []:
            reason = _bind_reason(ctx, str(bind).split(":", 1)[0])
            if reason:
                return Decision(False, reason, action="create")
        for mount in host.get("Mounts") or []:
            kind = str((mount or {}).get("Type") or "volume")
            if kind == "bind":
                reason = _bind_reason(ctx, str(mount.get("Source") or ""))
                if reason or not str(mount.get("Source") or "").startswith("/"):
                    return Decision(False, reason or "bind mounts need an absolute source", action="create")
            elif kind == "volume":
                # An inline driver config creates the volume on the fly: the same rules as
                # /volumes/create, or ``type=none,o=bind,device=/run/mucli`` reaches the engine socket.
                driver, options = _inline_volume_options(mount)
                reason = volume_options_reason(ctx, driver, options)
                if reason:
                    return Decision(False, reason, action="create")
            elif kind != "tmpfs":
                return Decision(False, f"{kind} mounts are not allowed", action="create")
        if host.get("Devices"):
            return Decision(False, "host devices are not allowed", action="create")
        for key in _HOST_MODES:
            if str(host.get(key) or "").lower() == "host":
                return Decision(False, f"{key} host is not allowed", action="create")
        for ref in host.get("VolumesFrom") or []:
            owner = str(ref).split(":", 1)[0]
            if not ctx.owned(owner):
                return Decision(False, f"--volumes-from {owner}: container was not created by this job", action="create")
        reason = device_requests_reason(ctx, host.get("DeviceRequests"))
        if reason:
            return Decision(False, reason, action="create")
        if ctx.readonly:
            body = _readonly_create(body, ctx)
        return Decision(True, body=_labelled(body, ctx.job_id), action="create")
    if ctx.readonly and method == "POST" and (path.startswith("/swarm/") or path.startswith("/services/")):
        return Decision(False, "swarm services are not available to read-only stages", action="service")
    if path == "/networks/create" and method == "POST":
        if not isinstance(body, dict):
            return Decision(False, "invalid network create request", action="network")
        if str(body.get("Driver") or "") == "host":
            return Decision(False, "the host network driver is not allowed", action="network")
        return Decision(True, body=_labelled(body, ctx.job_id), action="network")
    if path == "/volumes/create" and method == "POST":
        if not isinstance(body, dict):
            return Decision(False, "invalid volume create request", action="volume")
        opts = body.get("DriverOpts") or {}
        reason = volume_options_reason(ctx, body.get("Driver"), opts)
        if reason:
            return Decision(False, reason, action="volume")
        device = _volume_device_of(opts)
        if ctx.readonly and device and _in_worktree(ctx, device):
            return Decision(False, "a read-only stage cannot create a volume of the worktree", action="volume")
        return Decision(True, body=_labelled(body, ctx.job_id), action="volume")
    if path == "/build" and method == "POST":
        if str((query.get("networkmode") or [""])[0]).lower() == "host":
            return Decision(False, "host networking for builds is not allowed", action="build")
        labels = {}
        try:
            labels = json.loads((query.get("labels") or ["{}"])[0]) or {}
        except ValueError:
            labels = {}
        labels[LABEL_JOB] = ctx.job_id
        return Decision(True, query={**query, "labels": [json.dumps(labels)]}, action="build")
    if path == "/images/create" and method == "POST":
        source = (query.get("fromSrc") or [""])[0]
        if source and source != "-":
            return Decision(False, "importing images from a URL is not allowed", action="pull")
        image = (query.get("fromImage") or [""])[0]
        if image and not source:
            tag = (query.get("tag") or [""])[0]
            ref = image if not tag else (f"{image}@{tag}" if tag.startswith("sha256:") else f"{image}:{tag}")
            if not registry_allowed(ref, ctx.egress):
                return Decision(False, f"registry {registry_of(ref)} is not on the job's egress allowlist "
                                       "(add it to Extra egress domains)", action="pull")
            return Decision(True, action="pull", pull=ref)
        return Decision(True, action="import")
    match = _CONTAINER_OP_RE.match(path)
    if match and match.group("id") not in ("create", "json", "prune"):
        ref, op = match.group("id"), match.group("op") or ""
        if ctx.owned(ref) is False:
            return Decision(False, f"container {ref} was not created by this job", action=op or method.lower(), container=ref)
        if op == "update" and isinstance(body, dict) and (body.get("Devices") or body.get("DeviceRequests")):
            return Decision(False, "devices cannot be added to a running container", action="update", container=ref)
        if ctx.readonly and not ctx.session_owned(ref) and method not in ("GET", "HEAD", "DELETE") \
                and op not in _FOREIGN_READONLY_OPS:
            return Decision(False, f"container {ref} was not created by this read-only stage; it may only be "
                                   "inspected, stopped or removed", action=op or method.lower(), container=ref)
        return Decision(True, action=op or ("remove" if method == "DELETE" else "inspect"), container=ref)
    return Decision(True, action="")


# ------------------------------------------------------------------ HTTP plumbing


def _parse_head(raw: bytes) -> Tuple[str, str, str, List[Tuple[str, str]]]:
    lines = raw.decode("latin-1").split("\r\n")
    method, target, version = (lines[0].split(" ", 2) + ["", ""])[:3]
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        key, _, value = line.partition(":")
        headers.append((key.strip(), value.strip()))
    return method, target, version or "HTTP/1.1", headers


def _header(headers: List[Tuple[str, str]], name: str) -> str:
    return next((v for k, v in headers if k.lower() == name.lower()), "")


def _render_head(method: str, target: str, version: str, headers: List[Tuple[str, str]]) -> bytes:
    lines = [f"{method} {target} {version}"] + [f"{k}: {v}" for k, v in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def _read_body(reader: asyncio.StreamReader, headers: List[Tuple[str, str]], limit: int) -> bytes:
    if "chunked" in _header(headers, "Transfer-Encoding").lower():
        out = bytearray()
        while True:
            size_line = await reader.readuntil(b"\r\n")
            size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                await reader.readuntil(b"\r\n")
                return bytes(out)
            out.extend(await reader.readexactly(size))
            await reader.readexactly(2)
            if len(out) > limit:
                raise ValueError("request body too large")
    length = int(_header(headers, "Content-Length") or 0)
    if length > limit:
        raise ValueError("request body too large")
    return await reader.readexactly(length) if length else b""


def _response(status: int, payload: Any, *, stream: bool = False) -> bytes:
    body = (payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    reason = {200: "OK", 403: "Forbidden", 400: "Bad Request", 502: "Bad Gateway", 507: "Insufficient Storage"}.get(status, "Error")
    head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n").encode()
    return head + body


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except (OSError, RuntimeError):
            pass


class DockerPolicyProxy:
    """Unix-socket Docker API proxy in front of one job engine (one instance per stage container)."""

    def __init__(self, socket_path, upstream: Tuple[str, int], ctx: ProxyContext, *,
                 on_event: Callable[[Dict[str, Any]], None] = lambda event: None,
                 pull: Optional[Callable[[str], str]] = None,
                 storage_check: Optional[Callable[[], str]] = None):
        self.path = Path(socket_path)
        self.upstream = upstream
        self.ctx = ctx
        self.on_event = on_event
        self.pull = pull
        self.storage_check = storage_check or (lambda: "")
        if ctx.readonly and not ctx.session:
            import secrets
            ctx.session = secrets.token_hex(8)
        self.ready = threading.Event()
        self.error: Optional[BaseException] = None
        self._owned_cache: Dict[str, bool] = {}
        self._session_cache: Dict[str, bool] = {}
        # The CLI sends attach and start concurrently; a start must not overtake its container's
        # attach (the daemon would drop a fast container's output), so starts wait for the attach
        # stream to be established.
        self._attaching: Dict[str, asyncio.Event] = {}
        # asyncio (3.10) keeps neither connection tasks nor stream readers strongly; a hijacked
        # attach whose client half-closed is then only a reference cycle and the GC would destroy
        # it mid-stream. Hold every task here until it finishes.
        self._tasks: set = set()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> "DockerPolicyProxy":
        self.thread = threading.Thread(target=self._run, name="mucli-docker-proxy", daemon=True)
        self.thread.start()
        if not self.ready.wait(10) or self.error:
            self.close()
            raise RuntimeError("Docker policy proxy failed to start") from self.error
        return self

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def setup():
            self.server = await asyncio.start_unix_server(self._client, path=str(self.path), limit=1 << 20)
            self.path.chmod(0o600)
        try:
            self.loop.run_until_complete(setup())
            self.ready.set()
            self.loop.run_forever()
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            self.ready.set()
        finally:
            if hasattr(self, "server"):
                self.server.close()
                self.loop.run_until_complete(self.server.wait_closed())
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def close(self) -> None:
        if hasattr(self, "loop") and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if hasattr(self, "thread"):
            self.thread.join(timeout=3)

    # ---------------------------------------------------------------- upstream helpers
    async def _open_upstream(self):
        """``(host, port)`` of the job engine, or ``("unix", path)`` for a socket engine."""
        if self.upstream[0] == "unix":
            return await asyncio.open_unix_connection(str(self.upstream[1]))
        return await asyncio.open_connection(*self.upstream)

    async def _upstream_json(self, method: str, path: str) -> Tuple[int, Any]:
        reader, writer = await self._open_upstream()
        try:
            writer.write(f"{method} {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            _m, status, _v, headers = _parse_head(head)  # status line parses as "HTTP/1.1 200 OK"
            code = int(status) if status.isdigit() else 0
            body = await _read_body(reader, headers, MAX_JSON_BODY) if (
                _header(headers, "Content-Length") or "chunked" in _header(headers, "Transfer-Encoding").lower()) \
                else await reader.read()
            try:
                return code, json.loads(body or b"null")
            except ValueError:
                return code, None
        finally:
            writer.close()

    async def _owned(self, ref: str) -> Optional[bool]:
        if ref in self._owned_cache:
            return self._owned_cache[ref]
        code, data = await self._upstream_json("GET", f"/containers/{ref}/json")
        if code == 404 or not isinstance(data, dict):
            return None  # unknown container: let the engine answer 404
        labels = dict((data.get("Config") or {}).get("Labels") or {})
        owned = labels.get(LABEL_JOB) == self.ctx.job_id
        mine = bool(self.ctx.session) and labels.get(LABEL_SESSION) == self.ctx.session
        for alias in (ref, data.get("Id"), str(data.get("Name") or "").lstrip("/")):
            if alias:
                self._owned_cache[alias] = owned
                self._session_cache[alias] = mine
        return owned

    async def _volume_device(self, name: str) -> Optional[str]:
        """Engine path behind a named ``local`` bind volume (None = an ordinary volume)."""
        if not name or name.startswith("/"):
            return None
        from urllib.parse import quote
        code, data = await self._upstream_json("GET", f"/volumes/{quote(name, safe='')}")
        if code != 200 or not isinstance(data, dict):
            return None
        options = dict(data.get("Options") or {})
        return _volume_device_of(options) or None

    def _emit(self, decision: Decision, method: str, path: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        if decision.allow and decision.action not in ("create", "start", "pull", "build", "network", "volume", "remove"):
            return
        event = {"action": decision.action or method.lower(), "method": method, "path": strip_version(path),
                 "allowed": decision.allow, "reason": decision.reason, "container": decision.container, **(extra or {})}
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 - audit must never break the proxy
            pass

    # ---------------------------------------------------------------- per connection
    def _hold(self, task: "asyncio.Future") -> "asyncio.Future":
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._hold(asyncio.current_task())
        try:
            await self._handle(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
            pass
        except Exception as exc:  # noqa: BLE001
            try:
                writer.write(_response(502, {"message": f"mucli Docker proxy error: {exc}"}))
                await writer.drain()
            except (OSError, RuntimeError):
                pass
        finally:
            try:
                writer.close()
            except (OSError, RuntimeError):
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        method, target, version, headers = _parse_head(head)
        parts = urlsplit(target)
        path, query = parts.path, parse_qs(parts.query, keep_blank_values=True)
        bare = strip_version(path)
        inspect_body = method.upper() == "POST" and (bare in ("/containers/create", "/networks/create", "/volumes/create")
                                                     or bare.endswith("/update"))
        body: Optional[bytes] = None
        payload = None
        if inspect_body:
            body = await _read_body(reader, headers, MAX_JSON_BODY)
            try:
                payload = json.loads(body or b"{}")
            except ValueError:
                payload = None

        match = _CONTAINER_OP_RE.match(bare)
        attached: Optional[asyncio.Event] = None
        if match and match.group("op") == "attach":
            attached = self._attaching.setdefault(match.group("id"), asyncio.Event())
        owned_answer: Optional[bool] = True
        if match and match.group("id") not in ("create", "json", "prune"):
            owned_answer = await self._owned(match.group("id"))
        volumes_from = [str(v).split(":", 1)[0] for v in ((payload or {}).get("HostConfig") or {}).get("VolumesFrom") or []] \
            if bare == "/containers/create" and isinstance(payload, dict) else []
        owners = {ref: await self._owned(ref) for ref in volumes_from}
        devices: Dict[str, Optional[str]] = {}
        if self.ctx.readonly and bare == "/containers/create" and isinstance(payload, dict):
            host_config = payload.get("HostConfig") or {}
            names = [str(b).split(":", 1)[0] for b in host_config.get("Binds") or []]
            names += [str((m or {}).get("Source") or "") for m in host_config.get("Mounts") or []
                      if str((m or {}).get("Type") or "volume") == "volume"]
            devices = {n: await self._volume_device(n) for n in names if n and not n.startswith("/")}
        session = dict(self._session_cache)
        ctx = ProxyContext(**{**self.ctx.__dict__, "owned": lambda ref: owners.get(ref, owned_answer),
                              "session_owned": lambda ref: session.get(ref, False),
                              "volume_device": lambda name: devices.get(name)})
        decision = decide(method, path, query, payload, ctx)
        if decision.allow and decision.action in ("create", "pull", "build", "import") and method.upper() == "POST":
            full = self.storage_check()
            if full:
                decision = Decision(False, full, action=decision.action)
        if not decision.allow:
            if attached is not None:
                attached.set()
            self._emit(decision, method, path)
            writer.write(_response(403, {"message": f"mucli policy: {decision.reason}"}))
            await writer.drain()
            return
        if match and match.group("op") == "start" and match.group("id") in self._attaching:
            try:
                await asyncio.wait_for(self._attaching[match.group("id")].wait(), 10)
            except asyncio.TimeoutError:
                pass
        if decision.pull and self.pull is not None:
            self._emit(decision, method, path, extra={"image": decision.pull})
            loop = asyncio.get_running_loop()
            try:
                message = await loop.run_in_executor(None, self.pull, decision.pull)
                lines = json.dumps({"status": message or f"Pulled {decision.pull} through the mucli host proxy"}) + "\n"
            except Exception as exc:  # noqa: BLE001
                lines = json.dumps({"errorDetail": {"message": str(exc)}, "error": str(exc)}) + "\n"
            writer.write(_response(200, lines.encode()))
            await writer.drain()
            return
        if decision.query is not None:
            target = parts._replace(query=urlencode(decision.query, doseq=True)).geturl()
        if not _header(headers, "Upgrade"):
            # One request per upstream connection; attach/exec upgrades keep their own headers.
            headers = [(k, v) for k, v in headers if k.lower() not in ("connection", "keep-alive")]
            headers.append(("Connection", "close"))
        if decision.body is not None:
            body = json.dumps(decision.body).encode()
        if body is not None:
            headers = [(k, v) for k, v in headers if k.lower() not in ("content-length", "transfer-encoding")]
            headers.append(("Content-Length", str(len(body))))
        try:
            upstream_reader, upstream_writer = await self._open_upstream()
        except OSError:
            if attached is not None:
                attached.set()
                self._attaching.pop(match.group("id"), None)
            raise
        try:
            upstream_writer.write(_render_head(method, target, version, headers))
            if body:
                upstream_writer.write(body)
            await upstream_writer.drain()
            self._emit(decision, method, path, extra={"image": str((decision.body or {}).get("Image") or "")})
            # Streaming bodies (builds, archive uploads) and hijacked attach/exec sessions flow both
            # ways; the exchange ends when the engine closes its side.
            if _header(headers, "Upgrade"):
                # Hijacked attach/exec: wait for the engine's 101 before relaying the client's
                # stream; it also releases the container's start held back in _attaching.
                first = await upstream_reader.read(65536)
                if attached is not None:
                    attached.set()
                if first:
                    writer.write(first)
                    await writer.drain()
            upload = self._hold(asyncio.ensure_future(_pipe(reader, upstream_writer)))
            await _pipe(upstream_reader, writer)
            upload.cancel()
        finally:
            if attached is not None:
                attached.set()
                self._attaching.pop(match.group("id"), None)
            upstream_writer.close()
