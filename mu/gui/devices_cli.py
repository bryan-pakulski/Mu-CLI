"""``mucli gui pair`` / ``mucli gui devices [revoke NAME]``: pair and manage devices from the terminal.

Talks to the running GUI on this machine (loopback), like the web's Settings → Devices.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from mu.ui.markup import esc


def _base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _call(port: int, method: str, path: str, body: Optional[Dict[str, Any]] = None, csrf: bool = False) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if csrf:
        token = _call(port, "GET", "/api/csrf")
        headers[token["header"]] = token["token"]
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_base(port) + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:  # noqa: BLE001
            detail = exc.reason
        raise SystemExit(f"mucli gui: {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"mucli gui: the GUI isn't running on port {port} ({exc.reason}). Start it with: mucli --gui") from None


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="mucli gui", description="Pair and manage devices (mobile) for the GUI.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MUCLI_GUI_PORT", "30311")))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pair", help="Start a pairing: shows a code and a QR code for the phone")
    devices = sub.add_parser("devices", help="List paired devices, or revoke one")
    devices.add_argument("action", nargs="?", choices=["list", "revoke"], default="list")
    devices.add_argument("name", nargs="?", help="Device name or id (for revoke)")
    args = parser.parse_args(argv)

    from mu.ui.theme import get_console
    console = get_console()
    if args.command == "pair":
        pairing = _call(args.port, "POST", "/api/devices/pairing", {}, csrf=True)
        try:
            import segno
            segno.make(pairing["payload"], error="m").terminal(compact=True)
        except Exception:  # noqa: BLE001 - the code alone is enough
            pass
        minutes = max(1, int((pairing["expires_at"] - time.time()) // 60))
        console.print(f"\n[wash.title]Pair a device[/]  code [bold]{esc(pairing['display'])}[/]  (valid {esc(str(minutes))} min, one use)")
        if pairing.get("urls"):
            console.print("GUI address for the phone: " + ", ".join(esc(u) for u in pairing["urls"]))
        if pairing.get("listening_on_loopback"):
            console.print("[wash.caution]The GUI only listens on this computer.[/] To reach it from a phone, restart it with "
                          "MUCLI_GUI_ALLOW_REMOTE=1 mucli --gui --host 0.0.0.0")
        console.print("In the app: More → Settings → Devices, then scan the code or type the address and code. "
                      "In a browser on another machine: open the GUI address and enter the code.")
        return 0
    if args.action == "revoke":
        if not args.name:
            parser.error("revoke needs a device name or id")
        result = _call(args.port, "DELETE", f"/api/devices/{urllib.request.quote(args.name, safe='')}", csrf=True)
        console.print(f"Revoked {esc(result['revoked']['name'])}. It's signed out now.")
        return 0
    listing = _call(args.port, "GET", "/api/devices")["devices"]
    if not listing:
        console.print("No paired devices. Pair one with: mucli gui pair")
        return 0
    for d in listing:
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["last_seen"])) if d.get("last_seen") else "never"
        console.print(f"{esc(d['name'])}  [wash.muted]{esc(d['id'])} · last seen {esc(seen)} · {esc(d.get('address') or '')}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
