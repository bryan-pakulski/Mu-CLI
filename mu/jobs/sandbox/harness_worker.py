"""One job stage run by Claude Code or Codex, executed only inside the job stage container.

The container is the same locked-down one the mucli worker uses (``--network none``, read-only
rootfs, cap-drop ALL); the host CLI binary is mounted read-only at /opt/harness, the one credential
the host resolved is mounted read-only at /harness/creds and copied into a tmpfs config dir, and
CLI session files live in /harness/state (copied back to the job by the host). The worker writes
the same ``sandbox_event`` lines, /output/result.json and /output/transcript.json as the mucli
worker, so the rest of the job pipeline is unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from mu.jobs import harness_cli as cli

CREDS = Path("/harness/creds")
STATE = Path("/harness/state")
PENDING = STATE / "pending_approval.json"
DELIVERED = STATE / "delivered_messages.json"   # ids of "Message agent" messages already given to the CLI
MESSAGE_POLL_S = 3.0
IDLE_CLOSE_S = 120.0   # after a result, close an idle session even if a turn looks unanswered


def emit(event, payload=None, reason=""):
    print(json.dumps({"event": event, "payload": payload or {}, "reason": reason}), flush=True)


def relay_post(path, payload, timeout=15):
    import httpx
    transport = httpx.HTTPTransport(uds=os.environ["MUCLI_MODEL_RELAY"])
    with httpx.Client(transport=transport, timeout=timeout, trust_env=False) as client:
        response = client.post(f"http://relay{path}", headers={"X-Mucli-Stage-Token": os.environ["MUCLI_STAGE_TOKEN"]},
                               json=payload)
        response.raise_for_status()
        return response.json()


def prepare_home(agent):
    """Copy the credential into a fresh tmpfs config dir and link only the session store to /harness/state."""
    env = {}
    try:
        env.update({k: str(v) for k, v in json.loads((CREDS / "env.json").read_text()).items()})
    except (OSError, ValueError):
        pass
    if agent == "claude":
        home = Path("/tmp/claude-config")
        store, link = STATE / "claude-projects", "projects"
        env["CLAUDE_CONFIG_DIR"] = str(home)
    else:
        home = Path("/tmp/codex-home")
        store, link = STATE / "codex-sessions", "sessions"
        env["CODEX_HOME"] = str(home)
        env["PATH"] = f"{cli.CODEX_ROOT}/codex-path:" + os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = CREDS / "files"
    if files.is_dir():
        for item in files.iterdir():
            if item.is_file() and not item.is_symlink():
                target = home / item.name
                shutil.copyfile(item, target)
                target.chmod(0o600)
    store.mkdir(parents=True, exist_ok=True)
    (home / link).symlink_to(store)
    return env


def codex_session_model(thread_id):
    """The model Codex actually used (its JSON stream never names it): from the session rollout."""
    import re
    if not thread_id:
        return ""
    for path in (STATE / "codex-sessions").rglob(f"rollout-*{thread_id}.jsonl"):
        try:
            with open(path, encoding="utf-8") as handle:
                for _ in range(200):
                    match = re.search(r'"model":"([\w.:/@+-]{1,120})"', handle.readline())
                    if match:
                        return match.group(1)
        except OSError:
            continue
    return ""


class Run:
    def __init__(self, agent, request):
        self.agent, self.request = agent, request
        self.stage = os.environ["MUCLI_JOB_STAGE"]
        self.read_only = self.stage != "implement"
        self.state = cli.Translation(agent=agent, model=str(request.get("model") or ""))
        self.proc = None
        self.lock = threading.Lock()
        self.parked = None       # approval the stage is waiting on
        self.stopped = ""        # budget / time-limit stop reason
        self.seen_messages = set()
        self.last_ids = []
        self.frames = 0          # Claude: user turns written (each ends with one result event)
        self.results = 0
        self.acked = 0           # Claude: user turns echoed back (--replay-user-messages) = consumed
        self.closed = False
        try:
            self.delivered = set(json.loads(DELIVERED.read_text()))
        except (OSError, ValueError):
            self.delivered = set()

    # ------------------------------------------------------------ process

    def command(self, resume=None):
        harness = self.request
        resume = str(harness.get("resume_session") or "") if resume is None else resume
        if self.agent == "claude":
            return cli.claude_args(model=harness.get("model") or "", effort=harness.get("effort") or "",
                                   read_only=self.read_only, auto_approve=bool(harness.get("auto_approve")),
                                   resume=resume, system=cli.stage_system(self.stage, self.read_only))
        return cli.codex_args(model=harness.get("model") or "", effort=harness.get("effort") or "",
                              read_only=self.read_only, auto_approve=bool(harness.get("auto_approve")), resume=resume)

    def prompt(self):
        text = str(self.request.get("prompt") or "")
        if self.request.get("resume_session") and PENDING.is_file():
            try:
                pending = json.loads(PENDING.read_text())
            except (OSError, ValueError):
                pending = {}
            text = (f"A human has now decided on your pending {pending.get('tool_name', 'tool')} call. Retry that exact call "
                    "with the same arguments to continue; if it is denied, follow the reason given.\n\n" + text)
        pending_messages = self.new_messages()
        if pending_messages:
            text += "\n\n" + self.message_text(pending_messages)
        if self.agent == "codex" and not self.request.get("resume_session"):
            text = cli.codex_prompt(cli.stage_system(self.stage, self.read_only), text)
        return text

    # ------------------------------------------------------------ "Message agent"

    def new_messages(self):
        """Human messages for this stage not yet given to the CLI (marked delivered)."""
        try:
            messages = relay_post("/messages", {}).get("messages") or []
        except Exception:  # noqa: BLE001 - messaging is best-effort
            return []
        fresh = [m for m in messages if isinstance(m, dict) and m.get("id") not in self.delivered and str(m.get("text") or "").strip()]
        self._mark([m["id"] for m in fresh], True)
        self.last_ids = [m["id"] for m in fresh]
        return [str(m["text"]) for m in fresh]

    def _mark(self, ids, delivered):
        if not ids:
            return
        if delivered:
            self.delivered.update(ids)
        else:
            self.delivered.difference_update(ids)
        try:
            DELIVERED.write_text(json.dumps(sorted(self.delivered)))
        except OSError:
            pass

    @staticmethod
    def message_text(texts):
        return "Message from the human overseeing this job:\n" + "\n\n".join(texts)

    def _poll_messages(self):
        """Claude: deliver new messages into the running session as extra user turns."""
        while True:
            time.sleep(MESSAGE_POLL_S)
            with self.lock:
                if self.closed or not self.proc or self.proc.poll() is not None:
                    return
            texts = self.new_messages()
            if not texts:
                continue
            with self.lock:
                if self.closed:  # the session ended meanwhile: leave them for the next run
                    self._mark(self.last_ids, False)
                    return
                self.frames += 1
            text = self.message_text(texts)
            self.state.add_user(text)
            emit("runtime_info", {"text": "Delivered your message to Claude Code"})
            self.write(cli.claude_user_frame(text))

    def write(self, data):
        with self.lock:
            if self.proc and self.proc.stdin and not self.proc.stdin.closed:
                try:
                    self.proc.stdin.write(data)
                    self.proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass

    def stop(self, reason):
        self.stopped = self.stopped or reason
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # ------------------------------------------------------------ events

    def report_usage(self, payload):
        try:
            answer = relay_post("/usage", payload)
        except Exception:  # noqa: BLE001 - accounting must not kill the stage
            return
        if answer.get("stop"):
            emit("runtime_error", {"text": str(answer.get("reason") or "stage budget exhausted")})
            threading.Thread(target=self.stop, args=(str(answer.get("reason") or "budget"),), daemon=True).start()

    def on_claude_usage(self, event):
        message = event.get("message") or {}
        usage, mid = message.get("usage"), message.get("id")
        if event.get("type") == "assistant" and isinstance(usage, dict) and mid and mid not in self.seen_messages:
            self.seen_messages.add(mid)
            read, write = int(usage.get("cache_read_input_tokens") or 0), int(usage.get("cache_creation_input_tokens") or 0)
            self.report_usage({"input_tokens": int(usage.get("input_tokens") or 0) + read + write,
                               "output_tokens": int(usage.get("output_tokens") or 0), "cached_tokens": read,
                               "cache_write_tokens": write})
        elif event.get("type") == "result" and event.get("total_cost_usd") is not None:
            self.report_usage({"total_cost_usd": event["total_cost_usd"], "total_only": True})

    def on_control(self, event):
        request = event.get("request") or {}
        request_id = str(event.get("request_id") or "")
        if request.get("subtype") != "can_use_tool":
            self.write(cli.claude_control_error(request_id, f"unsupported control request {request.get('subtype')!r}"))
            return
        name, tool_input = str(request.get("tool_name") or ""), request.get("input") or {}
        if self.read_only:
            self.write(cli.claude_permission_reply(request_id, allow=False, message="This job stage is read-only."))
            return
        try:
            response = relay_post("/interaction", {"kind": "approval_required", "tool_name": name, "tool_args": tool_input})
            response = response.get("response")
        except Exception as exc:  # noqa: BLE001
            self.write(cli.claude_permission_reply(request_id, allow=False, message=f"Approval unavailable: {exc}"))
            return
        if response is None:
            # No human decision yet: deny this attempt, park the stage and resume the session later.
            self.parked = {"tool_name": name, "tool_args": tool_input, "tool_use_id": request.get("tool_use_id") or "",
                           "can_approve": True, "harness": "claude"}
            PENDING.write_text(json.dumps(self.parked))
            self.write(cli.claude_permission_reply(request_id, allow=False, message=(
                "Paused for human approval of this call. Do not retry it or work around it now; this session "
                "will be resumed after the decision.")))
            threading.Thread(target=self._park_after_result, daemon=True).start()
            return
        decision = str(response.get("decision") or "").lower()
        detail = str(response.get("detail") or "").strip()
        if decision == "approve":
            self.write(cli.claude_permission_reply(request_id, allow=True, tool_input=tool_input))
        else:
            self.write(cli.claude_permission_reply(request_id, allow=False, message=(
                f"The human denied this call. Reason: {detail}" if detail else "The human denied this call.")))
        try:
            PENDING.unlink()
        except OSError:
            pass

    def _close_if_idle(self, results):
        with self.lock:
            if not self.closed and self.results == results and self.proc and self.proc.poll() is None:
                self.closed = True
                try:
                    self.proc.stdin.close()
                except (OSError, ValueError):
                    pass

    def _park_after_result(self):
        time.sleep(3)  # let Claude record the denial in its transcript before stopping
        self.stop("approval")

    # ------------------------------------------------------------ main

    def run(self, env):
        full_env = dict(os.environ, **env)
        if self.agent == "claude":
            full_env.update(cli.claude_env(int(self.request.get("tool_timeout_s") or 3600)))
        code, stderr = self._run_once(full_env, self.prompt(), None)
        # Codex takes no input mid-turn: messages sent meanwhile resume its thread afterwards.
        for _ in range(20):
            if self.agent != "codex" or code != 0 or self.parked or self.stopped or self.state.is_error:
                break
            texts = self.new_messages()
            if not texts or not self.state.session_id:
                break
            emit("runtime_info", {"text": "Delivering your message to Codex (resuming its thread)"})
            self.state.finished = False
            code, stderr = self._run_once(full_env, self.message_text(texts), self.state.session_id)
        return code, stderr

    def _run_once(self, full_env, prompt, resume):
        self.state.add_user(prompt)
        try:
            self.proc = subprocess.Popen(self.command(resume), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, env=full_env, cwd="/workspace", bufsize=1)
        except OSError as exc:  # e.g. a glibc binary in a musl image: the loader is missing
            raise RuntimeError(f"{self.agent} could not start in this image ({exc.strerror}); a glibc-linked CLI "
                               "such as Claude Code needs a Debian/Ubuntu-based job image") from exc
        stderr_tail = []

        def drain():
            for line in self.proc.stderr:
                stderr_tail.append(line)
                del stderr_tail[:-40]
        threading.Thread(target=drain, daemon=True).start()
        if self.agent == "claude":
            self.frames, self.results, self.acked, self.closed = 1, 0, 0, False
            self.write(cli.claude_user_frame(prompt))
            threading.Thread(target=self._poll_messages, daemon=True).start()
        else:
            self.write(prompt)
            with self.lock:
                self.proc.stdin.close()
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if self.agent == "claude" and event.get("type") == "control_request":
                self.on_control(event)
                continue
            if self.agent == "claude" and event.get("type") == "user" and event.get("isReplay"):
                with self.lock:
                    self.acked += 1
                continue
            for ui in cli.translate(self.agent, event, self.state):
                emit(ui["event"], ui["payload"])
            if self.agent == "claude":
                self.on_claude_usage(event)
                if event.get("type") == "result":
                    with self.lock:
                        self.results += 1
                        # Every user turn consumed (or, for a CLI that doesn't echo, answered): let it exit.
                        answered = self.acked if self.acked else self.results
                        if answered >= self.frames and not self.closed:
                            self.closed = True
                            self.proc.stdin.close()
                        elif not self.closed:
                            threading.Timer(IDLE_CLOSE_S, self._close_if_idle, args=(self.results,)).start()
            elif event.get("type") == "turn.completed":
                self.state.model = self.state.model or codex_session_model(self.state.session_id)
                usage = event.get("usage") or {}
                self.report_usage({"model": self.state.model, "input_tokens": int(usage.get("input_tokens") or 0),
                                   "output_tokens": int(usage.get("output_tokens") or 0),
                                   "cached_tokens": int(usage.get("cached_input_tokens") or 0),
                                   "reasoning_tokens": int(usage.get("reasoning_output_tokens") or 0)})
        code = self.proc.wait()
        with self.lock:
            self.closed = True
        return code, "".join(stderr_tail)[-4000:]

    def result(self, code, stderr):
        state = self.state
        base = {"text": state.final_text, "harness": state.result()}
        if self.parked:
            return {**base, "kind": "needs_human", "status": "needs_input", "error": f"Approval required for {self.parked['tool_name']}",
                    "attention_reason": "approval_required", "attention_payload": self.parked}
        if self.stopped:
            return {**base, "kind": "needs_human", "status": "paused", "error": self.stopped}
        if state.usage_limit:
            when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(state.resets_at)) if state.resets_at else ""
            label = "Claude" if self.agent == "claude" else "Codex"
            return {**base, "kind": "needs_human", "status": "usage_limit", "resets_at": state.resets_at,
                    "error": f"{label} usage limit reached" + (f"; resets {when}" if when else "") + ". Resume the job after that."}
        if state.is_error or (code != 0 and not state.finished):
            detail = state.error or stderr.strip().splitlines()[-1:] or [f"exit code {code}"]
            detail = detail if isinstance(detail, str) else detail[0]
            return {**base, "kind": "failed", "status": "error", "error": f"{self.agent}: {detail}"[:2000]}
        try:
            PENDING.unlink()
        except OSError:
            pass
        return {**base, "kind": "completed", "status": "completed", "error": ""}


def main():
    from .egress import start_container_proxy
    start_container_proxy()
    request = json.loads(Path("/artifacts/request.json").read_text())
    harness = dict(request.get("harness") or {})
    agent = str(harness.get("agent") or "")
    harness.update(prompt=request.get("prompt"), tool_timeout_s=request.get("tool_timeout_s"),
                   auto_approve=bool(request.get("auto_approve_writes")))
    result = {"kind": "failed", "status": "error", "error": "harness did not run"}
    run = None
    signal.signal(signal.SIGTERM, lambda signum, frame: run.stop("stage time limit") if run else None)
    try:
        if agent not in ("claude", "codex"):
            raise RuntimeError(f"unknown harness {agent!r}")
        binary = cli.CLAUDE_BIN if agent == "claude" else f"{cli.CODEX_ROOT}/bin/codex"
        if not os.access(binary, os.X_OK):
            raise RuntimeError(f"{agent} binary is not mounted at {binary}")
        env = prepare_home(agent)
        run = Run(agent, harness)
        code, stderr = run.run(env)
        result = run.result(code, stderr)
        if run.stopped == "stage time limit":
            result = {**result, "kind": "needs_human", "status": "paused", "error": "stage time limit"}
    except Exception as exc:  # noqa: BLE001 - every failure must reach the host as a result
        result = {"kind": "failed", "status": "error", "error": str(exc)[:2000]}
    finally:
        if run is not None:
            Path("/output/transcript.json").write_text(json.dumps(run.state.history, default=str))
            result.setdefault("harness", run.state.result())
        Path("/output/result.json").write_text(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
