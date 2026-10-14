"""Per-job Docker engine (Docker-in-Docker under Sysbox only; §7 of job_build_environments.md).

Topology per job:

* ``mucli-job-<job12>-engine`` runs ``dockerd`` under ``--runtime=sysbox-runc`` (never
  ``--privileged``) with its own storage volume ``mucli-job-<job12>-docker``;
* it runs with ``--network none``: no route out. Its API is a Unix socket in a private host
  directory bind-mounted at ``/run/mucli`` (``dockerd -H unix:///run/mucli/docker.sock``); the
  host talks HTTP over that socket, so nothing depends on reaching a container IP (which
  rootless Docker and Docker Desktop do not route). Image pulls go through the stage's policy
  proxy, which pulls host-side (registry must be on the job's egress allowlist) and loads the
  image into the engine;
* the job worktree is mounted at ``/workspace`` (path parity with the stage) and the shared
  dependency cache of its first docker binding at ``/cache``;
* CPU, memory and pids come from the job's sandbox rules.

Stage and suite containers never reach the engine directly: each gets a
:class:`~mu.jobs.sandbox.docker_proxy.DockerPolicyProxy` socket at ``/var/run/docker.sock``.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .environments import (LABEL_ROLE, SYSBOX_MESSAGE, Docker, EnvironmentBlocked, job12, job_labels,
                           sysbox_available)

ENGINE_PORT = 2375  # legacy (TCP engines); the engine API is now a Unix socket
SOCKET_IN_ENGINE = "/run/mucli/docker.sock"
SOCKET_NAME = "docker.sock"


def socket_dir(job_id: str) -> Path:
    """Host directory holding the engine's API socket.

    Short on purpose (Unix socket paths are limited to ~107 bytes): ``<runtime dir or temp
    dir>/mucli-<uid>/engine-<home>-<job12>``. The outer directory is 0700 and owned by this user,
    so nobody else can reach the socket; the inner one is world-writable so the engine's root
    (a subordinate uid under Sysbox) can create the socket there whether or not the kernel
    supports ID-mapped mounts."""
    from .environments import home_id
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    if not os.path.isdir(base):
        base = tempfile.gettempdir()
    return Path(base) / f"mucli-{os.getuid()}" / f"engine-{home_id()}-{job12(job_id)}"


def _private(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    import stat as _stat
    return _stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not (st.st_mode & 0o077)


def ensure_socket_dir(job_id: str) -> Path:
    path = socket_dir(job_id)
    outer = path.parent
    if not outer.exists():
        outer.mkdir(mode=0o700)
    if not _private(outer):
        raise RuntimeError(f"engine socket directory {outer} must be a 0700 directory owned by this user")
    path.mkdir(exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"engine socket directory {path} is not a directory")
    os.chmod(path, 0o777)  # reachable only through the private parent
    return path


def remove_socket_dir(job_id: str) -> None:
    path = socket_dir(job_id)
    if _private(path.parent) and path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)


class UnixHTTPConnection(http.client.HTTPConnection):
    """``http.client`` over a Unix socket (streaming uploads for ``/images/load``)."""

    def __init__(self, path: str, timeout: float = 60):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


def engine_request(path: str, method: str, target: str, *, timeout: float = 30) -> tuple:
    """One small API call over the engine socket: ``(status, body text)``."""
    conn = UnixHTTPConnection(path, timeout=timeout)
    try:
        conn.request(method, target, headers={"Host": "docker"})
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", "replace")
    finally:
        conn.close()


def engine_name(job_id: str) -> str:
    return f"mucli-job-{job12(job_id)}-engine"


def network_name(job_id: str) -> str:
    return f"mucli-job-{job12(job_id)}-net"


def volume_name(job_id: str) -> str:
    return f"mucli-job-{job12(job_id)}-docker"


def combined_gpus(states: Sequence[Dict[str, Any]]) -> str:
    """The engine gets the union of its docker bindings' GPU requests."""
    requests = [str(s.get("gpus") or "") for s in states if s.get("docker") and s.get("gpus")]
    if not requests:
        return ""
    if "all" in requests:
        return "all"
    ids: List[str] = []
    for request in requests:
        for item in request.split("=", 1)[-1].replace('"', "").split(","):
            if item and item not in ids:
                ids.append(item)
    return "device=" + ",".join(ids) if ids else ""


class JobEngine:
    def __init__(self, job: Any, states: Sequence[Dict[str, Any]], *, docker: Optional[Docker] = None,
                 settings: Optional[Dict[str, Any]] = None, service=None):
        self.job = job
        self.states = [s for s in states if s.get("docker")]
        self.docker = docker or Docker()
        self.settings = settings
        self.service = service
        self.name = engine_name(job.id)

    # ---------------------------------------------------------------- settings
    def _rule(self, path: str) -> Any:
        from ..job_settings import rule
        return rule(path, self.settings)

    def cache_dir(self) -> str:
        """The engine's /cache: the first docker binding's shared cache (path parity with that
        binding's implement and checks stages, so ``-v /cache/...`` means the same directory)."""
        from .policy import stage_cache_dir
        state = self.states[0] if self.states else {"name": "engine"}
        return stage_cache_dir(self.job, state, "checks", self.settings)

    def state_dir(self) -> Path:
        from ..core.artifacts import artifacts_for
        path = Path(artifacts_for(self.job).path("engine"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------------------------------------------------------------- lifecycle
    def ensure(self) -> str:
        """Start (or reuse) the job engine; returns the host path of its API socket."""
        from .cli import is_podman
        if is_podman(self.settings):
            from .environments import PODMAN_DIND_MESSAGE
            raise EnvironmentBlocked(PODMAN_DIND_MESSAGE)
        if not sysbox_available(self.docker):
            raise EnvironmentBlocked(SYSBOX_MESSAGE)
        running = self.docker.inspect("container", self.name, "{{.State.Running}}")
        fresh = False
        if running == "":
            self._create()
            fresh = True
        elif running != "true":
            ensure_socket_dir(self.job.id)  # a temp dir may have been cleaned since (reboot)
            self.docker.run("start", self.name, timeout=120)
        address = self.address()
        self._wait_ready(address)
        if fresh:
            self._preload(address)
        return address

    def _create(self) -> None:
        from .policy import SandboxPolicy, gpus_arg
        from .cli import bind_mount, selinux_enforcing
        labels = [arg for key, value in job_labels(self.job.id).items() for arg in ("--label", f"{key}={value}")]
        if not self.docker.exists("volume", volume_name(self.job.id)):
            self.docker.run("volume", "create", *labels, volume_name(self.job.id), timeout=60)
        policy = SandboxPolicy.for_stage("checks")
        image = str(self._rule("sandbox.docker_engine_image") or "docker:dind")
        if not self.docker.exists("image", image):
            # The engine image is infrastructure (like the runtime image): fetched once, never by a stage.
            self.docker.run("pull", image, timeout=1800)
        worktree = os.path.realpath(self.job.worktree)
        cache = self.cache_dir()
        sockets = str(ensure_socket_dir(self.job.id))
        selinux = selinux_enforcing()
        args = ["run", "-d", "--name", self.name, "--runtime", "sysbox-runc", *labels,
                "--label", f"{LABEL_ROLE}=job-engine", "--network", "none",
                "--cpus", str(policy.cpu), "--memory", policy.memory, "--pids-limit", "8192",
                "--mount", f"type=volume,src={volume_name(self.job.id)},dst=/var/lib/docker",
                *bind_mount(worktree, "/workspace", False, selinux=selinux),
                *(bind_mount(cache, "/cache", False, selinux=selinux) if cache else []),
                *bind_mount(sockets, "/run/mucli", False, selinux=selinux),
                "-e", "DOCKER_TLS_CERTDIR="]
        gpus = combined_gpus(self.states) if self._rule("sandbox.gpus_enabled") else ""
        if gpus:
            args += ["--gpus", gpus_arg(gpus), "--label", f"io.mucli.gpus={gpus}"]
        args += ["--entrypoint", "sh", image, "-c", engine_command()]
        self.docker.run(*args, timeout=300)
        self._event("job_engine_started", {"engine": self.name, "image": image, "gpus": gpus})

    def address(self) -> str:
        """Host path of the engine's API socket."""
        return str(socket_dir(self.job.id) / SOCKET_NAME)

    def _wait_ready(self, address: str, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                status, body = engine_request(address, "GET", "/_ping", timeout=3)
                if status == 200 and body.strip() == "OK":
                    return
                last = f"HTTP {status}"
            except OSError as exc:  # not created yet, refused, permission (chmod pending)
                last = str(exc)
            except http.client.HTTPException as exc:
                last = str(exc)
            time.sleep(1)
        logs = self.docker.run("logs", "--tail", "40", self.name, check=False, timeout=30)
        raise RuntimeError(f"job engine did not become ready ({last}): {((logs.stdout or '') + (logs.stderr or ''))[-800:]}")

    # ---------------------------------------------------------------- images
    def load(self, address: str, ref: str) -> None:
        """``docker save <ref>`` on the host → ``POST /images/load`` on the engine (streamed)."""
        proc = self.docker.popen(["save", ref], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            conn = UnixHTTPConnection(address, timeout=1800)

            def chunks():
                while True:
                    block = proc.stdout.read(1 << 20)
                    if not block:
                        return
                    yield block
            conn.request("POST", "/images/load?quiet=1", body=chunks(), encode_chunked=True,
                         headers={"Content-Type": "application/x-tar"})
            response = conn.getresponse()
            text = response.read().decode("utf-8", "replace")
            if response.status != 200 or '"error"' in text:
                raise RuntimeError(f"loading {ref} into the job engine failed: {text[-400:]}")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=30)

    def _preload(self, address: str) -> None:
        refs: List[str] = []
        for state in self.states:
            refs += [str(state.get("job_image") or "")] + list(state.get("preload_images") or [])
        for ref in dict.fromkeys(r for r in refs if r):
            if not self.docker.exists("image", ref):
                self._event("job_engine_preload_skipped", {"image": ref, "reason": "not present on the host"})
                continue
            self.load(address, ref)
            self._event("job_engine_preloaded", {"image": ref})

    def pull(self, address: str, ref: str) -> str:
        """Host-side pull for an allow-listed registry, then load into the engine."""
        existed = self.docker.exists("image", ref)
        if not existed:
            self.docker.run("pull", ref, timeout=1800)
            pulled = self.state_dir() / "pulled.json"
            items = json.loads(pulled.read_text()) if pulled.is_file() else []
            if ref not in items:
                pulled.write_text(json.dumps(items + [ref]))
        self.load(address, ref)
        return f"Pulled {ref} through the mucli host proxy"

    def storage_used(self, address: str) -> int:
        status, body = engine_request(address, "GET", "/system/df", timeout=30)
        if status != 200:
            raise RuntimeError(f"engine /system/df answered HTTP {status}")
        data = json.loads(body)
        total = int(data.get("LayersSize") or 0)
        total += sum(int((v.get("UsageData") or {}).get("Size") or 0) for v in data.get("Volumes") or [])
        total += sum(int(c.get("SizeRw") or 0) for c in data.get("Containers") or [])
        return total

    def storage_check(self, address: str):
        cap = int(self._rule("sandbox.docker_storage_gb") or 20) * 1024 ** 3

        def check() -> str:
            try:
                used = self.storage_used(address)
            except Exception:  # noqa: BLE001 - never block on a telemetry failure
                return ""
            if used >= cap:
                return (f"the job's Docker engine uses {used / 1024 ** 3:.1f} GB of its {cap // 1024 ** 3} GB storage cap; "
                        "prune images/volumes or raise sandbox.docker_storage_gb")
            return ""
        return check

    # ---------------------------------------------------------------- per-stage proxy
    def proxy(self, socket_path, *, egress: Sequence[str], gpus: str, readonly: bool = False):
        """Start a policy proxy for one stage/suite/broker container; caller closes it.

        Bind sources are judged against the engine's own mounts (worktree at /workspace, engine
        cache at /cache): those are the paths engine-created containers actually see. The engine
        itself mounts the worktree read-write (implement needs it), so ``readonly`` stages get a
        proxy that forces every worktree mount read-only."""
        from .docker_proxy import DockerPolicyProxy, ProxyContext
        address = self.ensure()
        ctx = ProxyContext(job_id=self.job.id, worktree=os.path.realpath(self.job.worktree), cache_dir=self.cache_dir(),
                           gpus=gpus, egress=tuple(egress), readonly=bool(readonly))
        return DockerPolicyProxy(socket_path, ("unix", address), ctx,
                                 on_event=lambda event: self._event("docker_api", event),
                                 pull=lambda ref: self.pull(address, ref),
                                 storage_check=self.storage_check(address)).start()

    def _event(self, kind: str, payload: Dict[str, Any]) -> None:
        if self.service is None:
            return
        try:
            reason = payload.get("reason") or ""
            self.service.store.append_event(self.job.id, kind, reason=str(reason)[:500], payload=payload)
        except Exception:  # noqa: BLE001
            pass


def engine_command(socket: str = SOCKET_IN_ENGINE) -> str:
    """dockerd on a Unix socket in the bind-mounted directory. Root inside the Sysbox container is
    not the host user, so once the socket exists it is opened to the host user; the directory
    itself is 0700 and owned by that user, which keeps everyone else out."""
    return (f"rm -f {socket}; dockerd --host=unix://{socket} --tls=false & pid=$!; "
            f"i=0; while [ $i -lt 600 ] && [ ! -S {socket} ]; do i=$((i+1)); sleep 0.2; done; "
            f"chmod 0666 {socket} 2>/dev/null; wait $pid")


def pulled_images(job: Any) -> List[str]:
    """Images the engine proxy pulled onto the host for this job (removed with the job)."""
    try:
        from ..core.artifacts import artifacts_for
        path = Path(artifacts_for(job).path("engine", "pulled.json"))
        return list(json.loads(path.read_text())) if path.is_file() else []
    except (OSError, ValueError):
        return []


# Stages that may never write the worktree. Plan and review get no engine at all (nothing
# they do needs Docker, and an engine container left by implement could still write it);
# checks get a read-only proxy.
NO_ENGINE_STAGES = ("plan", "review")
READONLY_ENGINE_STAGES = ("checks",)


def attach_docker(policy, job, state, states, relay_dir, *, service=None, settings=None, stage: str = ""):
    """For a docker binding: start the engine + a policy proxy in ``relay_dir``; returns (policy, proxy).

    ``stage`` picks the worktree access: none for plan/review, read-only for checks."""
    from dataclasses import replace
    if not state or not state.get("docker") or stage in NO_ENGINE_STAGES:
        return policy, None
    engine = JobEngine(job, states, settings=settings, service=service)
    socket_path = Path(relay_dir) / "docker.sock"
    proxy = engine.proxy(socket_path, egress=policy.egress_extra, gpus=policy.gpus,
                         readonly=stage in READONLY_ENGINE_STAGES)
    return replace(policy, docker_socket=str(socket_path)), proxy
