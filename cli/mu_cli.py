#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
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
    poll_s: float = 0.75,
    timeout_s: int = 60,
) -> None:
    seen_ids: set[str] = set()
    start = time.time()
    while time.time() - start < timeout_s:
        events = client.get(f"/jobs/{job_id}/events")
        if not isinstance(events, list):
            print("unexpected events payload")
            return

        for event in events:
            event_id = event.get("id")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            print(f"[{event.get('event_type')}] {event.get('payload')}")

        job = client.get(f"/jobs/{job_id}")
        state = job.get("state")
        if state in {"completed", "failed", "cancelled", "blocked"}:
            print(f"job ended with state={state}")
            return
        time.sleep(poll_s)


def cmd_session_create(args: argparse.Namespace, client: Client) -> None:
    payload = {
        "workspace_path": args.workspace,
        "mode": args.mode,
        "provider_preferences": {"ordered": args.providers.split(",")},
        "policy_profile": args.policy,
    }
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
        watch_job_events(client, out["id"], timeout_s=args.timeout)


def cmd_job_control(args: argparse.Namespace, client: Client) -> None:
    out = client.post(f"/jobs/{args.job_id}/{args.action}")
    print(json.dumps(out, indent=2))


def cmd_job_input(args: argparse.Namespace, client: Client) -> None:
    out = client.post(f"/jobs/{args.job_id}/input", {"message": args.message})
    print(json.dumps(out, indent=2))


def cmd_approvals(args: argparse.Namespace, client: Client) -> None:
    pending = client.get(f"/sessions/{args.session_id}/approvals/pending")
    if not isinstance(pending, list) or not pending:
        print("no pending approvals")
        return

    for item in pending:
        print(
            f"approval={item['id']} job={item['job_id']} "
            f"tool={item['tool_name']} reason={item['reason']}"
        )
        if args.interactive:
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
                    break


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mu-cli")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="command", required=True)

    session_create = sub.add_parser("session-create")
    session_create.add_argument("workspace")
    session_create.add_argument("--mode", default="interactive")
    session_create.add_argument("--providers", default="ollama,mock")
    session_create.add_argument("--policy", default="default")

    job_start = sub.add_parser("job-start")
    job_start.add_argument("session_id")
    job_start.add_argument("goal")
    job_start.add_argument("--tool")
    job_start.add_argument("--watch", action="store_true")
    job_start.add_argument("--timeout", type=int, default=60)

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
