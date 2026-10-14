"""Claude Code / Codex stages in a real stage container, with fake CLIs speaking the recorded
protocols (require Docker and the locally built mucli-job-runtime image)."""
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.worktree import JobWorktreeManager
from mu.jobs.sandbox.runner import run_stage_in_container

FAKE_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, uuid
    args = sys.argv[1:]
    def out(obj):
        print(json.dumps(obj), flush=True)
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-fake", "credential missing"
    assert os.environ["CLAUDE_CONFIG_DIR"] == "/tmp/claude-config"
    assert os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:3128", "egress proxy not set"
    frame = json.loads(sys.stdin.readline())
    resume = args[args.index("--resume") + 1] if "--resume" in args else ""
    sid = resume or str(uuid.uuid4())
    store = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-workspace")
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, sid + ".jsonl"), "a") as handle:
        handle.write(json.dumps(frame) + "\\n")
    out({"type": "system", "subtype": "init", "session_id": sid, "model": args[args.index("--model") + 1]})
    if "--dangerously-skip-permissions" in args:
        out({"type": "assistant", "message": {"id": "m1", "model": "fake", "content": [{"type": "text",
             "text": '{"summary": "plan from fake claude", "resumed": %s}' % json.dumps(bool(resume))}],
             "usage": {"input_tokens": 10, "output_tokens": 5}}})
    else:
        tool_input = {"file_path": "/workspace/widget.txt", "content": "claude wrote this"}
        out({"type": "assistant", "message": {"id": "m1", "model": "fake", "content": [{"type": "tool_use",
             "id": "toolu_1", "name": "Write", "input": tool_input}], "usage": {"input_tokens": 10, "output_tokens": 5}}})
        out({"type": "control_request", "request_id": "r1", "request": {"subtype": "can_use_tool", "tool_name": "Write",
             "input": tool_input, "tool_use_id": "toolu_1"}})
        reply = json.loads(sys.stdin.readline())["response"]["response"]
        if reply["behavior"] == "allow":
            with open(reply["updatedInput"]["file_path"], "w") as handle:
                handle.write(reply["updatedInput"]["content"])
            out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}})
            out({"type": "assistant", "message": {"id": "m2", "model": "fake", "content": [{"type": "text",
                 "text": "Implementation complete."}], "usage": {"input_tokens": 20, "output_tokens": 5}}})
        else:
            out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": reply["message"], "is_error": True}]}})
            sys.stdin.readline()  # parked: the worker stops us
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "num_turns": 1,
         "total_cost_usd": 0.01, "result": "done", "usage": {"input_tokens": 30, "output_tokens": 10}})
    sys.stdin.read()
''')

FAKE_CODEX = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    assert os.environ["CODEX_HOME"] == "/tmp/codex-home"
    assert json.load(open(os.path.join(os.environ["CODEX_HOME"], "auth.json")))["auth_mode"] == "chatgpt"
    assert "--dangerously-bypass-approvals-and-sandbox" in args, args
    prompt = sys.stdin.read()
    assert "read-only" in prompt
    day = os.path.join(os.environ["CODEX_HOME"], "sessions", "2026", "10", "14")
    os.makedirs(day, exist_ok=True)
    with open(os.path.join(day, "rollout-2026-10-14T00-00-00-th-1.jsonl"), "w") as handle:
        handle.write(json.dumps({"type": "turn_context", "payload": {"model": "gpt-5.5"}}, separators=(",", ":")) + "\\n")
    for event in ({"type": "thread.started", "thread_id": "th-1"}, {"type": "turn.started"},
                  {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}},
                  {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "ls",
                   "aggregated_output": "readme.txt\\n", "exit_code": 0, "status": "completed"}},
                  {"type": "item.completed", "item": {"id": "i2", "type": "agent_message",
                   "text": '{"summary": "plan from fake codex"}'}},
                  {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 7}}):
        print(json.dumps(event), flush=True)
''')


@pytest.fixture
def docker():
    try:
        result = subprocess.run(["docker", "image", "inspect", "mucli-job-runtime:latest"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker unavailable")
    if result.returncode:
        pytest.skip("Docker or mucli-job-runtime image unavailable")


def _job(tmp_path, monkeypatch, agents):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repo / "readme.txt").write_text("a workspace")
    git("add", ".")
    git("commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="Harness smoke", repository=str(repo),
                                 execution={"pipeline": "staged", "stage_agents": agents}))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    return service, service.get(job.id)


def _fake(tmp_path, name, body):
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def test_claude_stage_parks_for_approval_and_resumes_its_session(docker, tmp_path, monkeypatch):
    from mu.jobs import job_settings
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.core.commands import dispatch
    from mu.jobs.core.vm import job_vm
    service, job = _job(tmp_path, monkeypatch, {"plan": "claude", "implement": "claude"})
    job_settings.save_settings({"harness": {"claude": {"binary": str(_fake(tmp_path, "claude", FAKE_CLAUDE)), "auth": "api_key"}}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    plan = run_stage_in_container(service, job, "plan", "Return a JSON summary.")
    assert plan["kind"] == "completed", plan
    assert "plan from fake claude" in plan["text"] and plan["cost_usd"] == pytest.approx(0.01)
    starting = [e for e in service.events(job.id) if e.event_type == "sandbox_starting"][-1]
    assert starting.payload["agent"] == "claude" and starting.payload["agent_model"] == "claude-opus-5-5"

    service.transition(job.id, "preparing")
    service.transition(job.id, "running")
    paused = run_stage_in_container(service, service.get(job.id), "implement", "Write widget.txt.")
    assert paused["kind"] == "needs_human" and paused["attention_reason"] == "approval_required", paused
    assert paused["attention_payload"]["tool_name"] == "Write"
    assert not (Path(service.get(job.id).worktree) / "widget.txt").exists()
    art = artifacts_for(service.get(job.id))
    session_id = art.read_json("harness/claude/implement.json")["session_id"]
    assert list(Path(art.path("harness", "claude", "state", "claude-projects", "-workspace")).glob(f"{session_id}.jsonl"))
    assert not Path(art.path("harness", "claude", "state", "auth")).exists()  # credentials never persisted

    service.require_human(job.id, paused["attention_reason"], paused["error"], payload=paused["attention_payload"])
    action = next(a for a in job_vm(service, job.id)["actions"] if a["id"] == "approveTool")
    dispatch(service, {"type": "approveTool", "job_id": job.id, "args": action["args"]})
    finished = run_stage_in_container(service, service.get(job.id), "implement", "Write widget.txt.")
    assert finished["kind"] == "completed", finished
    assert (Path(service.get(job.id).worktree) / "widget.txt").read_text() == "claude wrote this"
    assert art.read_json("harness/claude/implement.json")["session_id"] == session_id  # same session resumed
    transcript = art.read_json("logs/implement-transcript.json")
    assert any(p.get("type") == "tool_call" and p.get("harness_tool") == "Write" for m in transcript for p in m["parts"])


def test_codex_plan_stage_runs_read_only_with_copied_login(docker, tmp_path, monkeypatch):
    from mu.jobs import job_settings
    service, job = _job(tmp_path, monkeypatch, {"plan": "codex"})
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    job_settings.save_settings({"harness": {"codex": {"binary": str(_fake(tmp_path, "codex", FAKE_CODEX))}}})
    result = run_stage_in_container(service, job, "plan", "Return a JSON summary.")
    assert result["kind"] == "completed", result
    assert "plan from fake codex" in result["text"] and result["cost_usd"] > 0
    events = [e.payload for e in service.events(job.id) if e.event_type == "sandbox_event"]
    assert any(e.get("event") == "tool_call_ui" and e["payload"]["tool_name"] == "bash" for e in events)
    from mu.jobs.core.artifacts import artifacts_for
    art = artifacts_for(service.get(job.id))
    assert art.read_json("harness/codex/plan.json")["session_id"] == "th-1"
    costs = list(art.iter_costs())
    assert costs and costs[-1]["model"] == "gpt-5.5" and costs[-1]["provider"] == "codex"


MESSAGE_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, select, sys
    def out(obj):
        print(json.dumps(obj), flush=True)
    assert "--replay-user-messages" in sys.argv
    first = json.loads(sys.stdin.readline())
    out({"type": "system", "subtype": "init", "session_id": "msg-session", "model": "fake"})
    out({**first, "isReplay": True})
    # Like Claude Code 2.1.299: a message sent mid-turn is echoed and folded into the same turn (one result).
    ready, _, _ = select.select([sys.stdin], [], [], 30)
    queued = json.loads(sys.stdin.readline()) if ready else None
    text = "turn 1"
    if queued:
        out({**queued, "isReplay": True})
        text = "ACK " + queued["message"]["content"]
    out({"type": "assistant", "message": {"id": "m1", "model": "fake", "content": [{"type": "text", "text": text}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": "msg-session", "result": text})
    sys.stdin.read()
''')

MESSAGE_CODEX = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, sys, time
    prompt = sys.stdin.read()
    if "resume" in sys.argv:
        events = [{"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "ACK " + prompt}}]
    else:
        time.sleep(8)  # the human's message arrives while this turn runs
        events = [{"type": "thread.started", "thread_id": "th-msg"},
                  {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "first turn"}}]
    for event in events + [{"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]:
        print(json.dumps(event), flush=True)
''')


@pytest.mark.parametrize("agent, body", [("claude", MESSAGE_CLAUDE), ("codex", MESSAGE_CODEX)], ids=["claude", "codex"])
def test_message_agent_reaches_harness_stages(docker, tmp_path, monkeypatch, agent, body):
    import threading
    import time
    from mu.jobs import job_settings
    service, job = _job(tmp_path, monkeypatch, {"plan": agent})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-fake")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex-login"))
    job_settings.save_settings({"harness": {agent: {"binary": str(_fake(tmp_path, agent, body))}}})
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(run_stage_in_container(service, job, "plan", "Plan it.")))
    thread.start()
    deadline = time.monotonic() + 60
    while not any(e.event_type == "sandbox_starting" for e in service.events(job.id)) and time.monotonic() < deadline:
        time.sleep(0.2)
    time.sleep(3)  # the CLI is now mid-turn (both fakes keep their first turn open for 8s+)
    service.store.append_event(job.id, "stage_message", payload={"stage": "planning", "text": "please use tabs"})
    thread.join(120)
    assert outcome.get("kind") == "completed", outcome
    assert "ACK" in outcome["text"] and "please use tabs" in outcome["text"]
    from mu.jobs.core.artifacts import artifacts_for
    transcript = artifacts_for(service.get(job.id)).read_json("logs/plan-transcript.json")
    assert any("please use tabs" in (p.get("text") or "") for m in transcript if m["role"] == "user" for p in m["parts"])
