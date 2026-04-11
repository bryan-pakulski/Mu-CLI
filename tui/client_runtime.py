"""Thin TUI client runtime that connects to the authoritative MuCLI server."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from core.server_client import MuCLIServerClient, ServerClientError

PROFILE_PATH = Path.home() / ".mucli" / "client_profile.json"
WATCH_ACTIVE_STATUSES = {"queued", "running"}


def load_client_profile(path: Path | None = None) -> dict:
    target = Path(path or PROFILE_PATH)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_client_profile(profile: dict, path: Path | None = None) -> None:
    target = Path(path or PROFILE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")


def run_client_loop(ui, client: MuCLIServerClient, *, remember_server: bool = True):
    live_stream = {
        "thread": None,
        "stop_event": None,
        "task_id": None,
    }
    ui.show_info(f"Connected to μCLI server at {client.base_url}")
    try:
        capabilities = client.capabilities().get("capabilities", {})
        runtime_mode = capabilities.get("server_runtime", "unknown")
        ui.show_info(f"Server runtime mode: {runtime_mode}")
    except ServerClientError:
        pass

    if remember_server:
        save_client_profile({"server_url": client.base_url})

    while True:
        try:
            state_payload = client.state()
            state = state_payload.get("state", {}) if state_payload.get("ok") else {}
            session_name = state.get("current_session") or "remote-session"
            staged = state.get("staged_files") or []
            runtime = state.get("runtime") or {}
            if hasattr(ui, "set_variables"):
                ui.set_variables(dict(runtime.get("variables") or {}))
            feature_state = state.get("feature_state") or {}
            feature_plan = (
                feature_state.get("feature_plan")
                if isinstance(feature_state.get("feature_plan"), dict)
                else {}
            )
            next_task = feature_plan.get("next_task") or feature_plan.get("next_phase")
            current_task = (
                str(next_task.get("title", "")).strip()
                if isinstance(next_task, dict)
                else None
            )
            user_input = ui.get_input(
                session_name,
                staged,
                agent_mode=runtime.get("agent_mode", "default"),
                current_task=current_task,
                feature_context=None,
            )
            if not user_input:
                continue

            if user_input.strip() == "/tasks":
                result = client.tasks()
                if result.get("ok"):
                    tasks = result.get("tasks", [])
                    ui.show_info(f"Tasks: {len(tasks)}")
                    for task in tasks[:20]:
                        ui.show_info(
                            f"{task.get('task_id','?')} [{task.get('status','unknown')}] {task.get('kind','task')}"
                        )
                else:
                    ui.show_error(result.get("error") or "Unable to list tasks.")
                continue
            if user_input.startswith("/task "):
                task_id = user_input.split(" ", 1)[1].strip()
                result = client.task(task_id)
                if result.get("ok"):
                    task = result.get("task", {})
                    ui.show_info(
                        f"Task {task.get('task_id','?')} status={task.get('status','unknown')}"
                    )
                else:
                    ui.show_error(result.get("error") or "Task lookup failed.")
                continue
            if user_input.startswith("/watch"):
                parts = user_input.split(maxsplit=1)
                watch_target = parts[1].strip() if len(parts) > 1 else ""
                if watch_target.lower() == "latest":
                    listing = client.tasks()
                    tasks = listing.get("tasks", []) if listing.get("ok") else []
                    watch_target = str(tasks[0].get("task_id", "")).strip() if tasks else ""
                if not watch_target:
                    ui.show_error("Usage: /watch <task_id> (or /watch latest)")
                    continue
                _watch_task(ui, client, watch_target)
                continue
            if user_input.strip() == "/approvals":
                _list_approvals(ui, client)
                continue
            if user_input.startswith("/approve "):
                _resolve_approval(ui, client, user_input)
                continue
            if user_input.startswith("/stream"):
                _stream_events(ui, client, user_input, live_stream=live_stream)
                continue
            if user_input.strip() in {"/lock", "/lock status"}:
                _lock_status(ui, client)
                continue
            if user_input.strip() == "/lock claim":
                _lock_claim(ui, client, force=False)
                continue
            if user_input.strip() == "/lock force":
                _lock_claim(ui, client, force=True)
                continue
            if user_input.strip() == "/lock release":
                _lock_release(ui, client)
                continue
            if user_input.strip() == "/lock observe on":
                _lock_observer(ui, client, enabled=True)
                continue
            if user_input.strip() == "/lock observe off":
                _lock_observer(ui, client, enabled=False)
                continue

            if user_input.startswith("/"):
                result = client.command(user_input)
                if not result.get("ok"):
                    ui.show_error(result.get("message") or result.get("error") or "Command failed.")
                else:
                    message = result.get("message")
                    if message:
                        ui.show_info(message)
                    if remember_server:
                        current = load_client_profile()
                        current.update(
                            {
                                "server_url": client.base_url,
                                "session_name": result.get("session_name")
                                or session_name,
                            }
                        )
                        save_client_profile(current)
                if result.get("data", {}).get("exit"):
                    break
                continue

            result = client.message(user_input)
            if result.get("ok"):
                ui.render_message(
                    "assistant",
                    result.get("assistant_text", ""),
                    model_name=(state.get("provider") or {}).get("model"),
                )
            else:
                ui.show_error(result.get("error") or "Message request failed.")
        except ServerClientError as exc:
            ui.show_error(f"Server request failed: {exc}")
        except KeyboardInterrupt:
            ui.show_info("\n(Interrupted. Type /quit to exit)")
        except EOFError:
            ui.show_info("\nGoodbye!")
            break
    _stop_live_stream(ui, live_stream)


def _watch_task(ui, client: MuCLIServerClient, task_id: str, poll_interval: float = 1.0):
    ui.show_info(f"Watching task {task_id}...")
    while True:
        result = client.task(task_id)
        if not result.get("ok"):
            ui.show_error(result.get("error") or f"Unable to fetch task {task_id}.")
            return
        task = result.get("task", {}) or {}
        status = str(task.get("status", "unknown") or "unknown")
        kind = str(task.get("kind", "task") or "task")
        ui.show_info(f"Task {task_id} [{kind}] status={status}")
        if status not in WATCH_ACTIVE_STATUSES:
            return
        time.sleep(max(0.1, float(poll_interval or 1.0)))


def _list_approvals(ui, client: MuCLIServerClient):
    payload = client.approvals()
    if not payload.get("ok"):
        ui.show_error(payload.get("error") or "Unable to list approvals.")
        return
    approvals = payload.get("pending_approvals", []) or []
    ui.show_info(f"Pending approvals: {len(approvals)}")
    for approval in approvals[:20]:
        ui.show_info(
            f"{approval.get('approval_id','?')} task={approval.get('task_id','?')} tool={approval.get('tool_name','?')}"
        )


def _resolve_approval(ui, client: MuCLIServerClient, raw_input: str):
    parts = raw_input.split(maxsplit=3)
    if len(parts) < 3:
        ui.show_error("Usage: /approve <approval_id> <approve|reject|explain> [reason]")
        return
    approval_id = parts[1].strip()
    decision = parts[2].strip().lower()
    reason = parts[3].strip() if len(parts) > 3 else None
    payload = client.resolve_approval(approval_id, decision, reason)
    if not payload.get("ok"):
        ui.show_error(payload.get("error") or "Approval resolution failed.")
        return
    approval = payload.get("approval", {}) or {}
    ui.show_info(
        f"Resolved {approval.get('approval_id', approval_id)} decision={approval.get('decision', decision)}"
    )


def _stream_events(ui, client: MuCLIServerClient, raw_input: str, *, live_stream: dict):
    parts = raw_input.split()
    if len(parts) > 1 and parts[1].strip().lower() == "stop":
        _stop_live_stream(ui, live_stream)
        return
    if len(parts) > 1 and parts[1].strip().lower() == "status":
        running = _is_live_stream_running(live_stream)
        if running:
            ui.show_info(
                f"Live stream is running{(' for task ' + str(live_stream.get('task_id'))) if live_stream.get('task_id') else ''}."
            )
        else:
            ui.show_info("Live stream is not running.")
        return

    live_mode = len(parts) > 1 and parts[1].strip().lower() == "live"
    task_id = None
    count = 20
    if live_mode:
        task_id = parts[2].strip() if len(parts) > 2 else None
        _start_live_stream(ui, client, task_id, live_stream)
        return
    else:
        task_id = parts[1].strip() if len(parts) > 1 else None
    if not live_mode and len(parts) > 2:
        try:
            count = max(1, int(parts[2]))
        except ValueError:
            ui.show_error("Usage: /stream [task_id] [count] or /stream live [task_id]")
            return
    ui.show_info(
        f"Streaming up to {count} events{' for task ' + task_id if task_id else ''}..."
    )
    for event in client.stream_events(task_id=task_id, max_events=count):
        _render_stream_event(ui, client, event)


def _render_stream_event(ui, client: MuCLIServerClient, event: dict):
    event_name = str(event.get("event", "event") or "event")
    payload = event.get("payload", {}) or {}
    preview = str(payload)[:200]
    ui.show_info(f"[{event_name}] {preview}")

    if event_name != "approval.requested":
        return

    approval_id = str(payload.get("approval_id", "") or "").strip()
    if not approval_id:
        return
    if not hasattr(ui, "prompt_choices"):
        return

    choice = ui.prompt_choices(
        f"Resolve approval {approval_id}",
        choices=["approve", "reject", "explain"],
        default="approve",
    )
    reason = None
    if choice == "explain" and hasattr(ui, "prompt"):
        reason = ui.prompt("Explain why")

    response = client.resolve_approval(approval_id, choice, reason)
    if response.get("ok"):
        ui.show_info(f"Approval {approval_id} resolved via stream: {choice}")
    else:
        ui.show_error(
            response.get("error")
            or f"Failed to resolve approval {approval_id} from stream."
        )


def _is_live_stream_running(live_stream: dict) -> bool:
    thread = live_stream.get("thread")
    return bool(thread and thread.is_alive())


def _start_live_stream(
    ui,
    client: MuCLIServerClient,
    task_id: str | None,
    live_stream: dict,
):
    if _is_live_stream_running(live_stream):
        ui.show_info("Live stream already running. Use /stream stop first.")
        return
    stop_event = threading.Event()

    def _runner():
        try:
            for event in client.stream_events(task_id=task_id, max_events=None):
                if stop_event.is_set():
                    break
                _render_stream_event(ui, client, event)
        except Exception as exc:  # noqa: BLE001
            ui.show_error(f"Live stream stopped due to error: {exc}")

    thread = threading.Thread(target=_runner, daemon=True)
    live_stream["thread"] = thread
    live_stream["stop_event"] = stop_event
    live_stream["task_id"] = task_id
    thread.start()
    ui.show_info(
        f"Live stream started{' for task ' + task_id if task_id else ''}. Use /stream stop to end."
    )


def _stop_live_stream(ui, live_stream: dict):
    stop_event = live_stream.get("stop_event")
    thread = live_stream.get("thread")
    if not thread:
        return
    if stop_event:
        stop_event.set()
    if thread.is_alive():
        thread.join(timeout=1.0)
    live_stream["thread"] = None
    live_stream["stop_event"] = None
    live_stream["task_id"] = None
    ui.show_info("Live stream stopped.")


def _lock_status(ui, client: MuCLIServerClient):
    payload = client.arbiter_status()
    if not payload.get("ok"):
        ui.show_error(payload.get("error") or "Unable to fetch lock status.")
        return
    arbiter = payload.get("arbiter", {}) or {}
    ui.show_info(
        f"Lock active={arbiter.get('lock_active')} owner={arbiter.get('owner_client_id')}"
    )


def _lock_claim(ui, client: MuCLIServerClient, *, force: bool):
    payload = client.arbiter_claim(force=force)
    if payload.get("ok"):
        ui.show_info(f"Lock claimed by {client.client_id}.")
    else:
        ui.show_error(payload.get("error") or "Unable to claim lock.")


def _lock_release(ui, client: MuCLIServerClient):
    payload = client.arbiter_release()
    if payload.get("ok"):
        ui.show_info("Lock released.")
    else:
        ui.show_error(payload.get("error") or "Unable to release lock.")


def _lock_observer(ui, client: MuCLIServerClient, *, enabled: bool):
    payload = client.arbiter_set_observer(enabled)
    if payload.get("ok"):
        ui.show_info(
            f"Observer mode {'enabled' if enabled else 'disabled'} for {client.client_id}."
        )
    else:
        ui.show_error(payload.get("error") or "Unable to set observer mode.")
