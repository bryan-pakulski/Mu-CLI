"""``mucli-node`` command line (also the zipapp entry point)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from typing import List, Optional

from . import AGENT_VERSION
from .agent import DEFAULT_ETC_DIR, DEFAULT_STATE_DIR, NodeAgent, enroll
from .collectors import collect_facts
from .policy import load_policy_file
from .transport import TransportError


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mucli-node", description="mucli orchestration node agent")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--etc-dir", default=DEFAULT_ETC_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    p_enroll = sub.add_parser("enroll", help="exchange a join token for a node credential")
    p_enroll.add_argument("--master", required=True, help="https://<private address>:30312")
    p_enroll.add_argument("--token", required=True)
    p_enroll.add_argument("--fingerprint", required=True, help="SHA256:<hex> of the master certificate")
    p_enroll.add_argument("--name", default="")

    p_run = sub.add_parser("run", help="connect to the master and execute dispatches")
    p_run.add_argument("--insecure-no-sandbox", action="store_true",
                       help="run tools as plain subprocesses (development and disposable test nodes only)")

    sub.add_parser("facts", help="print the facts this node reports")
    sub.add_parser("policy", help="print the effective node policy")
    sub.add_parser("version")
    p_exec = sub.add_parser("exec-helper", help=argparse.SUPPRESS)
    p_exec.add_argument("spec")

    args = parser.parse_args(argv)
    if args.command == "version":
        print(AGENT_VERSION)
        return 0
    if args.command == "facts":
        print(json.dumps(collect_facts(), indent=2, sort_keys=True))
        return 0
    if args.command == "policy":
        print(json.dumps(load_policy_file(f"{args.etc_dir}/policy.yaml").summary(), indent=2))
        return 0
    if args.command == "exec-helper":
        from .executor import helper_main
        return helper_main([args.spec])
    if args.command == "enroll":
        try:
            identity = enroll(master=args.master, token=args.token, fingerprint=args.fingerprint,
                              state_dir=args.state_dir, etc_dir=args.etc_dir, name=args.name)
        except (TransportError, KeyError, ValueError) as exc:
            print(f"enrolment failed: {exc}", file=sys.stderr)
            return 1
        print(f"enrolled as {identity['node_id']}; waiting for the operator to accept this node")
        return 0
    if args.command == "run":
        sandbox = "none" if args.insecure_no_sandbox else "systemd"
        if sandbox == "systemd" and not shutil.which("systemd-run"):
            print("systemd-run not found: mucli-node requires Linux with systemd "
                  "(use --insecure-no-sandbox only for disposable test nodes)", file=sys.stderr)
            return 1
        if sandbox == "none":
            print("WARNING: running without the systemd sandbox", file=sys.stderr)
        try:
            agent = NodeAgent(state_dir=args.state_dir, etc_dir=args.etc_dir, sandbox=sandbox)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        try:
            agent.run_forever()
        except KeyboardInterrupt:
            agent.stop()
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
