"""Host-side lifecycle and message bridge for container sessions."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Callable

import httpx
import utils.config as _config

from .builder import build_container, container_slug
from .docker_cli import CommandRunner, ContainerRuntimeError, OutputCallback
from .network import teardown_network
from .ref import ContainerRef
from .registry import ContainerRegistry
from .templates import ContainerTemplate, TemplateRegistry
from .runner import attach_session_folder, detach_session_folder, mount_folder


class ContainerSupervisor:
    def __init__(
        self,
        *,
        registry: ContainerRegistry | None = None,
        runner: CommandRunner | None = None,
        request_timeout: float = 15.0,
        template_registry: TemplateRegistry | None = None,
    ):
        self.registry = registry or ContainerRegistry()
        self.runner = runner or CommandRunner()
        self.request_timeout = request_timeout
        self.template_registry = template_registry or TemplateRegistry()

    def resolve(self, name: str) -> ContainerRef | None:
        """Resolve a managed name with or without the ``mucli-`` prefix."""
        value = str(name or "").strip()
        if not value:
            return None
        ref = self.registry.get(value)
        if ref is None and not value.startswith("mucli-"):
            ref = self.registry.get(f"mucli-{value}")
        return ref

    def list_environments(self, *, refresh: bool = True) -> list[ContainerRef]:
        refs = self.registry.list_containers()
        if not refresh:
            return refs
        for ref in refs:
            try:
                exists, running = self._container_state(ref.name)
                ref.status = "running" if running else ("stopped" if exists else "missing")
                self.registry.upsert(ref)
            except Exception:
                pass
        return refs

    def create_environment(
        self,
        *,
        container_name: str,
        dockerfile: str | None = None,
        template_name: str | None = None,
        mounts: list[dict] | None = None,
        egress_allow: list[str] | None = None,
        egress_deny: list[str] | None = None,
        supervisor_url: str = "",
        source_path: str | None = None,
        start: bool = True,
        progress: Callable[[str, str], None] | None = None,
        output: OutputCallback | None = None,
    ) -> ContainerRef:
        """Create a managed environment without creating or attaching a session."""
        existing = self.resolve(container_name)
        if existing is not None:
            exists, _running = self._container_state(existing.name)
            if exists:
                raise ContainerRuntimeError(f"managed container already exists: {existing.name}")
            self._discard_stale_registration(existing)

        template = None
        if template_name:
            template = self.template_registry.get(template_name)
            if template is None:
                raise ContainerRuntimeError(f"container template not found: {template_name}")

        return build_container(
            container_name,
            dockerfile,
            base_image=template.image if template else None,
            template_name=template.name if template else None,
            mounts=mounts,
            egress_allow=(egress_allow if egress_allow is not None else (template.egress_allow if template else None)),
            egress_deny=(egress_deny if egress_deny is not None else (template.egress_deny if template else None)),
            mucli_source_path=source_path,
            supervisor_url=supervisor_url,
            session_name=None,
            registry=self.registry,
            runner=self.runner,
            start=start,
            progress=progress,
            output=output,
        )

    def create(
        self,
        *,
        container_name: str,
        session_name: str,
        dockerfile: str | None = None,
        template_name: str | None = None,
        mounts: list[dict] | None = None,
        egress_allow: list[str] | None = None,
        egress_deny: list[str] | None = None,
        supervisor_url: str,
        source_path: str | None = None,
        progress: Callable[[str, str], None] | None = None,
        output: OutputCallback | None = None,
    ) -> ContainerRef:
        def report(stage: str, message: str) -> None:
            if progress is not None:
                progress(stage, message)

        report("resolving_container", "Checking for an existing managed container…")
        existing = self.resolve(container_name)
        if existing is not None:
            exists, _running = self._container_state(existing.name)
            if existing.status == "error" or not exists:
                report(
                    "recovering_container",
                    "Removing stale container metadata and rebuilding the worker…",
                )
                self._discard_stale_registration(existing)
                existing = None
        if existing is None:
            template = self.template_registry.get(template_name) if template_name else None
            if template_name and template is None:
                raise ContainerRuntimeError(f"container template not found: {template_name}")
            ref = build_container(
                container_name,
                dockerfile,
                base_image=template.image if template else None,
                template_name=template.name if template else None,
                mounts=mounts,
                egress_allow=egress_allow,
                egress_deny=egress_deny,
                mucli_source_path=source_path,
                supervisor_url=supervisor_url,
                session_name=session_name,
                registry=self.registry,
                runner=self.runner,
                progress=progress,
                output=output,
            )
        else:
            report("reusing_container", "Reusing the existing managed container…")
            ref = existing
            self.ensure_running(ref)
            if session_name not in ref.attached_sessions:
                report("attaching_session", "Attaching the session data to the worker…")
                ref = attach_session_folder(
                    ref,
                    session_name,
                    registry=self.registry,
                    runner=self.runner,
                )
        attached = self.registry.attach_session(ref.name, session_name)
        report("container_ready", "Container is ready; loading the session…")
        return attached

    def container_for_session(self, session_name: str) -> ContainerRef | None:
        for ref in self.registry.list_containers():
            if session_name in ref.attached_sessions:
                return ref
        return None

    def _container_state(self, name: str) -> tuple[bool, bool]:
        """Return ``(exists, running)`` for a Docker container name."""
        docker = self.runner.require("docker")
        inspect = self.runner.run(
            [docker, "inspect", "-f", "{{.State.Running}}", name],
            check=False,
        )
        if inspect.returncode != 0:
            return False, False
        return True, inspect.stdout.strip().lower() == "true"

    def _discard_stale_registration(self, ref: ContainerRef) -> None:
        """Remove registry/network state for a worker missing from Docker.

        Persistent volumes are deliberately retained. A replacement worker uses
        the same volume names, so user-installed packages and workspace data are
        not discarded during recovery.
        """
        docker = self.runner.require("docker")
        self.runner.run([docker, "rm", "-f", ref.name], check=False)

        subnet = ref.network_subnet
        network_inspect = self.runner.run(
            [
                docker,
                "network",
                "inspect",
                ref.network_name,
                "--format",
                "{{range .IPAM.Config}}{{.Subnet}}{{end}}",
            ],
            check=False,
        )
        if network_inspect.returncode == 0 and network_inspect.stdout.strip():
            subnet = network_inspect.stdout.strip()
        if ref.network_name:
            teardown_network(ref.network_name, subnet, runner=self.runner)
        self.registry.remove(ref.name, force=True)

    def ensure_running(self, ref: ContainerRef) -> ContainerRef:
        exists, running = self._container_state(ref.name)
        if not exists:
            raise ContainerRuntimeError(
                f"managed container is missing from Docker: {ref.name}; "
                "reload the session to rebuild it"
            )
        if not running:
            self.runner.run([self.runner.require("docker"), "start", ref.name])
        ref.status = "running"
        return self.registry.upsert(ref)

    def worker_url(self, ref: ContainerRef) -> str:
        docker = self.runner.require("docker")
        result = self.runner.run(
            [
                docker,
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                ref.name,
            ]
        )
        host = result.stdout.strip()
        if not host:
            raise ContainerRuntimeError(f"worker container has no network address: {ref.name}")
        return f"http://{host}:{ref.worker_port}"

    def _post(self, ref: ContainerRef, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_running(ref)
        response = httpx.post(
            f"{self.worker_url(ref)}{path}",
            json=payload,
            headers={"X-MuCLI-Worker-Token": ref.worker_token},
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {"ok": True}

    def send(
        self,
        session_name: str,
        text: str,
        *,
        provider: str,
        model: str,
        agent_mode: str = "default",
        system_instruction: str = "You are a helpful assistant.",
    ) -> dict[str, Any]:
        ref = self.container_for_session(session_name)
        if ref is None:
            raise ContainerRuntimeError(f"no container attached to session {session_name!r}")
        return self._post(
            ref,
            "/send",
            {
                "session_name": session_name,
                "text": text,
                "provider": provider,
                "model": model,
                "agent_mode": agent_mode,
                "system_instruction": system_instruction,
            },
        )

    def send_sync(
        self,
        session_name: str,
        text: str,
        *,
        provider: str,
        model: str,
        agent_mode: str = "default",
        system_instruction: str = "You are a helpful assistant.",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run one worker turn synchronously for terminal clients.

        Browser/mobile clients use the event callback bridge. A standalone TUI
        has no FastAPI callback server, so this endpoint returns the final
        assistant text and compact turn result directly.
        """
        ref = self.container_for_session(session_name)
        if ref is None:
            raise ContainerRuntimeError(f"no container attached to session {session_name!r}")
        self.ensure_running(ref)
        response = httpx.post(
            f"{self.worker_url(ref)}/send-sync",
            json={
                "session_name": session_name,
                "text": text,
                "provider": provider,
                "model": model,
                "agent_mode": agent_mode,
                "system_instruction": system_instruction,
            },
            headers={"X-MuCLI-Worker-Token": ref.worker_token},
            timeout=timeout or None,
        )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {"ok": True}

    def interrupt(self, session_name: str) -> dict[str, Any]:
        ref = self.container_for_session(session_name)
        if ref is None:
            return {"ok": False, "detail": "No container attached."}
        return self._post(ref, "/interrupt", {"session_name": session_name})

    def add_mount(
        self, session_name: str, host_path: str, container_path: str, mode: str = "rw"
    ) -> ContainerRef:
        ref = self.container_for_session(session_name)
        if ref is None:
            raise ContainerRuntimeError(f"no container attached to session {session_name!r}")
        return mount_folder(
            ref,
            host_path,
            container_path,
            mode,
            registry=self.registry,
            runner=self.runner,
        )

    def detach(self, session_name: str, *, stop_if_idle: bool = True) -> ContainerRef | None:
        ref = self.container_for_session(session_name)
        if ref is None:
            return None
        ref = detach_session_folder(
            ref,
            session_name,
            registry=self.registry,
            runner=self.runner,
            recreate_if_empty=not stop_if_idle,
        )
        if stop_if_idle and not ref.attached_sessions:
            self.runner.run([self.runner.require("docker"), "stop", "-t", "20", ref.name], check=False)
            ref.status = "stopped"
            self.registry.upsert(ref)
        return ref

    def remove(self, container_name: str, *, force: bool = False) -> bool:
        ref = self.resolve(container_name)
        if ref is None:
            return False
        if ref.attached_sessions and not force:
            raise RuntimeError("detach all sessions before removing the container")
        docker = self.runner.require("docker")
        self.runner.run([docker, "rm", "-f", ref.name], check=False)
        teardown_network(ref.network_name, ref.network_subnet, runner=self.runner)
        if ref.workspace_volume:
            self.runner.run([docker, "volume", "rm", ref.workspace_volume], check=False)
        if ref.root_volume:
            self.runner.run([docker, "volume", "rm", ref.root_volume], check=False)
        return self.registry.remove(ref.name, force=force)

    def start(self, container_name: str) -> ContainerRef:
        ref = self.resolve(container_name)
        if ref is None:
            raise ContainerRuntimeError(f"managed container not found: {container_name}")
        return self.ensure_running(ref)

    def stop(self, container_name: str) -> ContainerRef:
        ref = self.resolve(container_name)
        if ref is None:
            raise ContainerRuntimeError(f"managed container not found: {container_name}")
        self.runner.run([self.runner.require("docker"), "stop", "-t", "20", ref.name], check=False)
        ref.status = "stopped"
        return self.registry.upsert(ref)

    def restart(self, container_name: str) -> ContainerRef:
        ref = self.resolve(container_name)
        if ref is None:
            raise ContainerRuntimeError(f"managed container not found: {container_name}")
        self.runner.run([self.runner.require("docker"), "restart", "-t", "20", ref.name])
        ref.status = "running"
        return self.registry.upsert(ref)

    def attach_session(self, container_name: str, session_name: str) -> ContainerRef:
        ref = self.resolve(container_name)
        if ref is None:
            raise ContainerRuntimeError(f"managed container not found: {container_name}")
        self.ensure_running(ref)
        session_file = os.path.join(
            os.path.expanduser(_config.HISTORY_DIR),
            "sessions",
            session_name,
            "session.json",
        )
        if not os.path.isfile(session_file):
            raise ContainerRuntimeError(f"saved session not found: {session_name}")
        if session_name not in ref.attached_sessions:
            ref = attach_session_folder(
                ref, session_name, registry=self.registry, runner=self.runner
            )
        return self.registry.attach_session(ref.name, session_name)

    def configuration(self, container_name: str) -> dict[str, Any]:
        ref = self.resolve(container_name)
        if ref is None:
            raise ContainerRuntimeError(f"managed container not found: {container_name}")
        dockerfile = None
        if not ref.template_name:
            path = os.path.join(self.registry.root, ref.name, "Dockerfile")
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    dockerfile = handle.read()
            except OSError:
                dockerfile = None
        return {
            "container_name": ref.name,
            "dockerfile": dockerfile,
            "template_name": ref.template_name or None,
            "mounts": [item.to_dict() for item in ref.mounts],
            "egress_allow": list(ref.egress_allow),
            "egress_deny": list(ref.egress_deny),
        }

    def detach_session(
        self, container_name: str, session_name: str, *, stop_if_idle: bool = False
    ) -> ContainerRef:
        requested = self.resolve(container_name)
        attached = self.container_for_session(session_name)
        if requested is None:
            raise ContainerRuntimeError(f"managed container not found: {container_name}")
        if attached is None or attached.name != requested.name:
            raise ContainerRuntimeError(
                f"session {session_name!r} is not attached to {requested.name}"
            )
        result = self.detach(session_name, stop_if_idle=stop_if_idle)
        if result is None:
            raise ContainerRuntimeError(f"session is not attached: {session_name}")
        return result

    def snapshot(
        self, container_name: str, template_name: str, *, description: str = ""
    ) -> ContainerTemplate:
        ref = self.resolve(container_name)
        if ref is None:
            raise ContainerRuntimeError(f"managed container not found: {container_name}")
        exists, _running = self._container_state(ref.name)
        if not exists:
            raise ContainerRuntimeError(f"Docker container is missing: {ref.name}")
        slug = container_slug(template_name)
        previous = self.template_registry.get(slug)
        image = f"mucli/template-{slug}:{int(time.time())}"
        docker = self.runner.require("docker")
        scrub_keys = (
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
            "GOOGLE_API_KEY", "OLLAMA_API_KEY", "OLLAMA_HOST",
            "MUCLI_WORKER_TOKEN", "MUCLI_SUPERVISOR_URL",
            "MUCLI_CONTAINER_NAME", "MUCLI_EGRESS_ALLOW",
            "MUCLI_EGRESS_DENY", "MUCLI_WORKSPACES",
        )
        commit_command = [docker, "commit"]
        for key in scrub_keys:
            commit_command.extend(["--change", f"ENV {key}="])
        commit_command.extend([ref.name, image])
        self.runner.run(commit_command)
        if previous is not None and previous.image != image:
            self.runner.run([docker, "image", "rm", previous.image], check=False)
        template = ContainerTemplate(
            name=slug,
            image=image,
            source_container=ref.name,
            description=str(description or ""),
            dockerfile_hash=ref.dockerfile_hash,
            egress_allow=list(ref.egress_allow),
            egress_deny=list(ref.egress_deny),
        )
        return self.template_registry.upsert(template)

    def remove_template(self, template_name: str, *, remove_image: bool = True) -> bool:
        template = self.template_registry.get(template_name)
        if template is None:
            return False
        if remove_image:
            self.runner.run(
                [self.runner.require("docker"), "image", "rm", template.image], check=False
            )
        return self.template_registry.remove(template.name)

    def interactive_shell(self, container_name: str, *, shell: str = "/bin/bash") -> int:
        """Attach the current terminal to a managed Docker environment."""
        ref = self.start(container_name)
        docker = self.runner.require("docker")
        command = [docker, "exec", "-it", ref.name, shell]
        result = subprocess.run(command, check=False)
        if result.returncode == 126 and shell != "/bin/sh":
            result = subprocess.run([docker, "exec", "-it", ref.name, "/bin/sh"], check=False)
        return int(result.returncode)

    def validate_token(self, container_name: str, token: str) -> bool:
        ref = self.registry.get(container_name)
        return bool(ref and token and __import__("hmac").compare_digest(ref.worker_token, token))

    def shutdown(self) -> None:
        # Containers are intentionally not removed.  Stop only idle workers.
        for ref in self.registry.list_containers():
            if not ref.attached_sessions and not ref.standalone and ref.status == "running":
                self.runner.run([self.runner.require("docker"), "stop", "-t", "20", ref.name], check=False)
                ref.status = "stopped"
                self.registry.upsert(ref)
