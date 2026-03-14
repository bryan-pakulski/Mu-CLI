#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, data: dict | None = None) -> dict | list:
        body = None
        headers = {"Content-Type": "application/json"}
        if data is not None:
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(
            url=f"{self.base_url}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8")
            raise SystemExit(f"HTTP {exc.code}: {detail}") from exc

    def get(self, path: str) -> dict | list:
        return self._request("GET", path)

    def post(self, path: str, data: dict | None = None) -> dict | list:
        return self._request("POST", path, data=data or {})


def watch_job_events(
    client: Client,
    job_id: str,
    seen_ids: set[str] | None = None,
) -> set[str]:
    seen = seen_ids or set()
    events = client.get(f"/jobs/{job_id}/events")
    if not isinstance(events, list):
        print("unexpected events payload")
        return seen

    for event in events:
        event_id = event.get("id")
        if event_id in seen:
            continue
        seen.add(event_id)
        print(f"[{event.get('event_type')}] {event.get('payload')}")
    return seen


def _prompt_job_input(client: Client, job_id: str) -> None:
    user_msg = input("message to agent (blank to skip): ").strip()
    if not user_msg:
        return
    out = client.post(f"/jobs/{job_id}/input", {"message": user_msg})
    print(json.dumps(out, indent=2))


def _process_pending_approvals(client: Client, session_id: str, interactive: bool) -> int:
    pending = client.get(f"/sessions/{session_id}/approvals/pending")
    if not isinstance(pending, list):
        return 0

    handled = 0
    for item in pending:
        print(
            f"approval={item['id']} job={item['job_id']} "
            f"tool={item['tool_name']} reason={item['reason']}"
        )
        if not interactive:
            continue

        while True:
            choice = input("approve? [y/n/s(skip)] ").strip().lower()
            if choice in {"s", "skip", ""}:
                break
            if choice in {"y", "yes", "n", "no"}:
                decision = "approved" if choice.startswith("y") else "denied"
                result = client.post(
                    f"/jobs/{item['job_id']}/approvals/{item['id']}",
                    {"decision": decision},
                )
                print(json.dumps(result, indent=2))
                handled += 1
                break
    return handled


def interactive_loop(
    client: Client,
    session_id: str,
    job_id: str,
    timeout_s: int,
    poll_s: float,
) -> None:
    seen: set[str] = set()
    start = time.time()

    while time.time() - start < timeout_s:
        seen = watch_job_events(client, job_id, seen_ids=seen)
        job = client.get(f"/jobs/{job_id}")
        state = job.get("state")

        _process_pending_approvals(client, session_id, interactive=True)

        if state in {"awaiting_approval", "blocked"}:
            print(f"job is {state}; you can provide input to unblock planning")
            _prompt_job_input(client, job_id)
            if state == "blocked":
                resume = input("resume blocked job now? [y/N] ").strip().lower()
                if resume in {"y", "yes"}:
                    out = client.post(f"/jobs/{job_id}/resume")
                    print(json.dumps(out, indent=2))

        if state in {"completed", "failed", "cancelled"}:
            print(f"job ended with state={state}")
            return

        time.sleep(poll_s)

    print("interactive loop timeout reached")


def cmd_session_create(args: argparse.Namespace, client: Client) -> None:
    payload = {
        "workspace": WorkspaceStore(args.workspace),
        "mode": args.mode,
        "provider_preferences": {"ordered": args.providers.split(",")},
        "policy_profile": args.policy,
    }
    payload["workspace"].attach(Path(args.workspace))
    out = client.post("/sessions", payload)
    print(json.dumps(out, indent=2))


def cmd_job_start(args: argparse.Namespace, client: Client) -> None:
    payload = {
        "goal": args.goal,
        "constraints": {},
        "acceptance_criteria": {},
    }
    if args.tool:
        payload["constraints"]["tool_name"] = args.tool

    out = client.post(f"/sessions/{args.session_id}/jobs", payload)
    print(json.dumps(out, indent=2))
    if args.watch:
        interactive_loop(
            client,
            session_id=args.session_id,
            job_id=out["id"],
            timeout_s=args.timeout,
            poll_s=args.poll,
        )


def cmd_job_control(args: argparse.Namespace, client: Client) -> None:
    out = client.post(f"/jobs/{args.job_id}/{args.action}")
    print(json.dumps(out, indent=2))


def cmd_job_input(args: argparse.Namespace, client: Client) -> None:
    out = client.post(f"/jobs/{args.job_id}/input", {"message": args.message})
    print(json.dumps(out, indent=2))


def cmd_approvals(args: argparse.Namespace, client: Client) -> None:
    handled = _process_pending_approvals(client, args.session_id, interactive=args.interactive)
    if handled == 0 and args.interactive:
        print("no approvals decided")


def cmd_loop(args: argparse.Namespace, client: Client) -> None:
    session_payload = {
        "workspace": WorkspaceStore(args.workspace),
        "mode": args.mode,
        "provider_preferences": {"ordered": args.providers.split(",")},
        "policy_profile": args.policy,
    }
    session = client.post("/sessions", session_payload)
    session_id = session["id"]
    print(f"session={session_id}")

    job_payload = {
        "goal": args.goal,
        "constraints": {},
        "acceptance_criteria": {},
    }
    if args.tool:
        job_payload["constraints"]["tool_name"] = args.tool

    job = client.post(f"/sessions/{session_id}/jobs", job_payload)
    print(f"job={job['id']}")
    interactive_loop(
        client,
        session_id=session_id,
        job_id=job["id"],
        timeout_s=args.timeout,
        poll_s=args.poll,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mu-cli")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="command", required=True)

    session_create = sub.add_parser("session-create")
    session_create.add_argument("workspace")
    session_create.add_argument("--mode", default="interactive")
    session_create.add_argument("--providers", default="ollama")
    session_create.add_argument("--policy", default="default")

    job_start = sub.add_parser("job-start")
    job_start.add_argument("session_id")
    job_start.add_argument("goal")
    job_start.add_argument("--tool")
    job_start.add_argument("--watch", action="store_true")
    job_start.add_argument("--timeout", type=int, default=300)
    job_start.add_argument("--poll", type=float, default=0.75)

    loop = sub.add_parser("loop")
    loop.add_argument("workspace")
    loop.add_argument("goal")
    loop.add_argument("--mode", default="interactive")
    loop.add_argument("--providers", default="ollama")
    loop.add_argument("--policy", default="default")
    loop.add_argument("--tool")
    loop.add_argument("--timeout", type=int, default=300)
    loop.add_argument("--poll", type=float, default=0.75)

    for action in ["run", "cancel", "resume"]:
        jp = sub.add_parser(f"job-{action}")
        jp.add_argument("job_id")

    job_input = sub.add_parser("job-input")
    job_input.add_argument("job_id")
    job_input.add_argument("message")

    approvals = sub.add_parser("approvals")
    approvals.add_argument("session_id")
    approvals.add_argument("--interactive", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    client = Client(args.api)

    if args.command == "session-create":
        cmd_session_create(args, client)
    elif args.command == "job-start":
        cmd_job_start(args, client)
    elif args.command == "loop":
        cmd_loop(args, client)
    elif args.command in {"job-run", "job-cancel", "job-resume"}:
        args.action = args.command.split("-", 1)[1]
        cmd_job_control(args, client)
    elif args.command == "job-input":
        cmd_job_input(args, client)
    elif args.command == "approvals":
        cmd_approvals(args, client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
