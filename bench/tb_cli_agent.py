"""Controlled Terminal-Bench adapter for OpenCode, Claude Code, and Pi.

Unlike Terminal-Bench's stock installed-agent adapters, this adapter copies a
pre-staged, checksum-pinned CLI into the task container. Installation,
configuration, and preflight happen before the execution clock starts.
"""

from __future__ import annotations

import json
import os
import shlex
import time
from pathlib import Path

from terminal_bench.agents.base_agent import AgentResult, BaseAgent
from terminal_bench.agents.failure_mode import FailureMode
from terminal_bench.terminal.models import TerminalCommand

from bench.tb_support import (
    external_harness_environment,
    file_sha256,
    make_container_logs_host_cleanable,
    normalize_ollama_cloud_model,
    pi_ollama_models_config,
    read_external_cli_usage,
)

_REPO = Path(__file__).resolve().parent.parent
_ARTIFACTS = _REPO / "bench" / "artifacts" / "agents"
_HARNESS_SPECS = {
    "opencode": {
        "version": "1.18.4",
        "artifact": _ARTIFACTS / "opencode" / "1.18.4" / "opencode",
        "sha256": "6ce6570e7db9a40e7bd3304ebdfff607920bde8cafd2eb5587bd7a26f89ba0b5",
    },
    "claude-code": {
        "version": "2.1.211",
        "artifact": _ARTIFACTS / "claude-code" / "2.1.211" / "claude",
        "sha256": "8272c8a474ac9ea1bc35f19b9f7c7e7dc4dc4eb6d5ad3e484b19335ac72446b2",
    },
    "pi": {
        "version": "0.85.1",
        "artifact": _ARTIFACTS / "pi" / "pi-0.85.1.tar.gz",
        "sha256": "ffa2a8214ef6f96fe6a5cdb1ddfb4165396dbcc352e92e42bf4d988986757bc6",
        "node_artifact": _ARTIFACTS
        / "node"
        / "22.23.2"
        / "node-v22.23.2-linux-x64.tar.gz",
        "node_sha256": "b294a556e639d64338823920e5866c21c02741742d2e1529ee1a225c1ec9252a",
    },
}


class ControlledCliAgent(BaseAgent):
    """Run one pinned external CLI against Ollama Cloud inside a TB task."""

    @staticmethod
    def name() -> str:
        return "controlled-cli"

    def __init__(
        self,
        model_name: str | None = None,
        harness: str = "",
        execution_timeout_sec: float | str | None = None,
        setup_timeout_sec: float | str | None = 180,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if harness not in _HARNESS_SPECS:
            choices = ", ".join(sorted(_HARNESS_SPECS))
            raise ValueError(f"harness must be one of: {choices}")
        self._harness = harness
        self._spec = _HARNESS_SPECS[harness]
        self._model_name = model_name or "ollama/glm-5.3-flash"
        self._ollama_model = normalize_ollama_cloud_model(self._model_name)
        self._execution_timeout_sec = self._positive_timeout(
            execution_timeout_sec, "execution_timeout_sec"
        )
        self._setup_timeout_sec = (
            self._positive_timeout(setup_timeout_sec, "setup_timeout_sec") or 180.0
        )
        self._version = str(self._spec["version"])

    @staticmethod
    def _positive_timeout(value, label: str) -> float | None:
        if value is None:
            return None
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a number") from exc
        if timeout <= 0:
            raise ValueError(f"{label} must be positive")
        return timeout

    def _check_setup_budget(self, started: float) -> None:
        if time.monotonic() - started > self._setup_timeout_sec:
            raise TimeoutError("external harness setup exceeded its allowance")

    def _verify_artifact(self, key: str, sha_key: str) -> Path:
        path = Path(self._spec[key])
        if not path.is_file():
            raise FileNotFoundError(f"missing {self._harness} artifact: {path}")
        if file_sha256(path) != self._spec[sha_key]:
            raise ValueError(f"{self._harness} artifact checksum changed: {path}")
        return path

    @staticmethod
    def _write_metrics(
        logging_dir: Path | None,
        harness: str,
        *,
        setup_seconds: float,
        execution_seconds: float,
        execution_timeout_seconds: float | None,
        completed: bool,
        error_type: str | None,
        error_phase: str | None,
    ) -> None:
        if logging_dir is None:
            return
        payload = {
            "schema": 1,
            "harness": harness,
            "setup_seconds": round(setup_seconds, 6),
            "execution_seconds": round(execution_seconds, 6),
            "execution_timeout_seconds": execution_timeout_seconds,
            "completed": completed,
            "timed_out": error_type == "TimeoutError" and error_phase == "execution",
            "setup_timed_out": error_type == "TimeoutError" and error_phase == "setup",
            "error_type": error_type,
            "error_phase": error_phase,
        }
        path = Path(logging_dir) / f"{harness}-execution.json"
        temporary = path.with_suffix(".json.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _failure(self, logging_dir, setup_started: float, error: Exception):
        self._write_metrics(
            logging_dir,
            self._harness,
            setup_seconds=time.monotonic() - setup_started,
            execution_seconds=0.0,
            execution_timeout_seconds=self._execution_timeout_sec,
            completed=False,
            error_type=type(error).__name__,
            error_phase="setup",
        )
        return AgentResult(failure_mode=FailureMode.AGENT_INSTALLATION_FAILED)

    def _copy_and_install(self, session, setup_started: float) -> None:
        artifact = self._verify_artifact("artifact", "sha256")
        if self._harness == "pi":
            node = self._verify_artifact("node_artifact", "node_sha256")
            session.copy_to_container(
                paths=node,
                container_dir="/tmp",
                container_filename="node-runtime.tar.gz",
            )
            session.copy_to_container(
                paths=artifact,
                container_dir="/tmp",
                container_filename="pi-agent.tar.gz",
            )
            result = session.container.exec_run(
                [
                    "sh",
                    "-c",
                    "mkdir -p /opt/node /opt/pi /logs/pi && "
                    "tar -xzf /tmp/node-runtime.tar.gz -C /opt/node "
                    "--strip-components=1 && "
                    "tar -xzf /tmp/pi-agent.tar.gz -C /opt/pi && "
                    "ln -sf /opt/pi/node_modules/.bin/pi /usr/local/bin/pi",
                ]
            )
        else:
            filename = "opencode" if self._harness == "opencode" else "claude"
            session.copy_to_container(
                paths=artifact,
                container_dir="/usr/local/bin",
                container_filename=filename,
            )
            result = session.container.exec_run(
                ["chmod", "755", f"/usr/local/bin/{filename}"]
            )
        if result.exit_code != 0:
            raise RuntimeError("failed to install the frozen harness artifact")
        self._check_setup_budget(setup_started)

    def _write_environment(self, session) -> None:
        env = external_harness_environment(
            self._harness,
            self._model_name,
            os.environ.get("OLLAMA_API_KEY", ""),
        )
        if self._harness == "pi":
            env["PATH"] = "/opt/node/bin:/usr/local/bin:/usr/bin:/bin"
            models = json.dumps(
                pi_ollama_models_config(self._model_name), separators=(",", ":")
            )
            settings = json.dumps(
                {"defaultModel": self._ollama_model, "defaultProvider": "ollama"},
                separators=(",", ":"),
            )
            result = session.container.exec_run(
                [
                    "sh",
                    "-c",
                    "mkdir -p /logs/pi && "
                    "printf '%s\\n' \"$TB_PI_MODELS\" > /logs/pi/models.json && "
                    "printf '%s\\n' \"$TB_PI_SETTINGS\" > /logs/pi/settings.json && "
                    "chmod 600 /logs/pi/models.json /logs/pi/settings.json",
                ],
                environment={"TB_PI_MODELS": models, "TB_PI_SETTINGS": settings},
            )
            if result.exit_code != 0:
                raise RuntimeError("failed to configure Pi")
        exports = "\n".join(
            f"export {key}={shlex.quote(value)}" for key, value in env.items()
        )
        result = session.container.exec_run(
            [
                "sh",
                "-c",
                "mkdir -p /installed-agent && "
                "printf '%s\\n' \"$TB_AGENT_ENV\" > /installed-agent/setup-env.sh && "
                "chmod 600 /installed-agent/setup-env.sh",
            ],
            environment={"TB_AGENT_ENV": exports},
        )
        if result.exit_code != 0:
            raise RuntimeError("failed to write harness environment")
        session.send_keys(
            ["source /installed-agent/setup-env.sh", "Enter"],
            block=True,
            max_timeout_sec=10,
        )

    def _preflight(self, session) -> None:
        if self._harness == "opencode":
            command = ["/usr/local/bin/opencode", "--version"]
            environment = None
        elif self._harness == "claude-code":
            command = ["/usr/local/bin/claude", "--version"]
            environment = None
        else:
            command = [
                "/opt/node/bin/node",
                "/opt/pi/node_modules/.bin/pi",
                "--version",
            ]
            environment = {"PI_CODING_AGENT_DIR": "/logs/pi"}
        result = session.container.exec_run(command, environment=environment)
        if result.exit_code != 0:
            raise RuntimeError(f"{self._harness} preflight failed")

    def _command(self, instruction: str) -> TerminalCommand:
        prompt = shlex.quote(self._render_instruction(instruction))
        if self._harness == "opencode":
            agent_command = (
                "opencode run --format json --model "
                f"ollama/{shlex.quote(self._ollama_model)} --auto {prompt}"
            )
        elif self._harness == "claude-code":
            allowed = "Bash Edit Write Read Glob Grep LS WebFetch NotebookEdit NotebookRead TodoRead TodoWrite Agent"
            agent_command = (
                "claude --verbose --output-format stream-json "
                f"--model {shlex.quote(self._ollama_model)} -p {prompt} "
                f"--allowedTools {allowed} --no-session-persistence"
            )
        else:
            agent_command = (
                "pi --provider ollama "
                f"--model {shlex.quote(self._ollama_model)} --mode json "
                f"--print --no-session --approve -- {prompt}"
            )
        command = (
            "cd /app && ( "
            f"{agent_command} > /logs/controlled-agent-output.jsonl 2>&1; "
            "agent_status=$?; printf '%s' \"$agent_status\" "
            '> /tmp/controlled-agent-status; exit "$agent_status" )'
        )
        return TerminalCommand(
            command=command,
            min_timeout_sec=0.0,
            max_timeout_sec=self._execution_timeout_sec or float("inf"),
            block=True,
            append_enter=True,
        )

    def _stop_agent(self, session) -> None:
        try:
            session.send_keys(["C-c"], block=False, min_timeout_sec=0.0)
        except Exception:
            pass
        patterns = {
            "opencode": "[/]usr/local/bin/opencode",
            "claude-code": "[/]usr/local/bin/claude",
            "pi": "[p]i-coding-agent|[/]opt/pi/node_modules/.bin/pi",
        }
        session.container.exec_run(
            [
                "sh",
                "-c",
                f"pkill -TERM -f {shlex.quote(patterns[self._harness])} 2>/dev/null || true",
            ]
        )
        time.sleep(0.25)
        session.container.exec_run(
            [
                "sh",
                "-c",
                f"pkill -KILL -f {shlex.quote(patterns[self._harness])} 2>/dev/null || true",
            ]
        )

    def _save_output(self, session, logging_dir: Path | None) -> str:
        if logging_dir is None:
            return ""
        try:
            result = session.container.exec_run(
                ["cat", "/logs/controlled-agent-output.jsonl"]
            )
            if result.exit_code == 0:
                output = result.output.decode(errors="replace")
            else:
                output = session.capture_pane(capture_entire=True)
            path = Path(logging_dir) / f"{self._harness}-output.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
            return output
        except OSError:
            return ""

    @staticmethod
    def _agent_exit_status(session) -> int | None:
        result = session.container.exec_run(
            ["sh", "-c", "cat /tmp/controlled-agent-status 2>/dev/null"]
        )
        if result.exit_code != 0:
            return None
        try:
            return int(result.output.decode().strip())
        except (AttributeError, TypeError, ValueError):
            return None

    def perform_task(self, instruction, session, logging_dir=None):
        setup_started = time.monotonic()
        try:
            self._copy_and_install(session, setup_started)
            self._write_environment(session)
            self._preflight(session)
            self._check_setup_budget(setup_started)
        except Exception as exc:
            make_container_logs_host_cleanable(session)
            return self._failure(logging_dir, setup_started, exc)

        setup_seconds = time.monotonic() - setup_started
        execution_started = time.monotonic()
        completed = False
        error_type = None
        failure_mode = FailureMode.NONE
        execution_seconds = 0.0
        try:
            session.send_command(self._command(instruction))
            exit_status = self._agent_exit_status(session)
            completed = exit_status == 0
            if not completed:
                error_type = "CliExitError"
                failure_mode = FailureMode.UNKNOWN_AGENT_ERROR
        except TimeoutError as exc:
            error_type = type(exc).__name__
            self._stop_agent(session)
            failure_mode = FailureMode.AGENT_TIMEOUT
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            execution_seconds = time.monotonic() - execution_started
            output = self._save_output(session, logging_dir)
            self._write_metrics(
                logging_dir,
                self._harness,
                setup_seconds=setup_seconds,
                execution_seconds=execution_seconds,
                execution_timeout_seconds=self._execution_timeout_sec,
                completed=completed,
                error_type=error_type,
                error_phase="execution" if error_type else None,
            )
            make_container_logs_host_cleanable(session)
        input_tokens, output_tokens = read_external_cli_usage(self._harness, output)
        return AgentResult(
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            failure_mode=failure_mode,
        )
