"""Paired devices (mu/gui/devices.py): pairing, the device list and revocation.

Operator routes (start a pairing, list, revoke) are loopback-only and CSRF-checked for browsers: a
paired phone can never pair other devices or see the list. The device itself may check who it is and
sign out. ``POST /api/devices/pair`` (apps: the token in the body) and ``POST /api/devices/pair-browser``
(a browser on another machine: the token in an HttpOnly cookie) are the routes a non-loopback client may
call unpaired.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from mu.gui import csrf
from mu.gui.devices import (
    PairingError, clear_device_cookie, client_host, device_cookie, display_code, is_loopback, request_auth,
    request_device,
)

router = APIRouter()


def _store(request: Request):
    store = getattr(request.app.state, "devices", None)
    if store is None:
        raise HTTPException(503, "device pairing isn't available")
    return store


def require_operator(request: Request) -> None:
    """This machine only (not a paired device), with the browser CSRF checks."""
    if request_device(request) is not None or not is_loopback(client_host(request)):
        raise HTTPException(403, "Only this computer can manage paired devices.")
    csrf.check_request(request)


def require_local(request: Request) -> None:
    if request_device(request) is not None or not is_loopback(client_host(request)):
        raise HTTPException(403, "Only this computer can manage paired devices.")
    csrf.check_host(request)


@router.get("/api/csrf")
def csrf_token(request: Request):
    """A CSRF token for this machine's non-page clients (the CLI's ``mucli gui pair``)."""
    require_local(request)
    return {"token": csrf.issue(), "header": csrf.HEADER}


def _addresses(request: Request) -> List[str]:
    """Base URLs a phone on the same network might use, best first."""
    port = request.url.port or 30311
    scheme = request.url.scheme
    out: List[str] = []
    try:
        from mu.orchestration.overlay import classify, local_addresses
        for item in local_addresses():
            address = item["address"]
            if ":" in address or classify(address).kind not in ("private", "overlay"):
                continue
            url = f"{scheme}://{address}:{port}"
            if url not in out:
                out.append(url)
    except Exception:  # noqa: BLE001 - addresses are a convenience
        pass
    return out


def _bound_to_loopback(request: Request) -> bool:
    host = str(getattr(getattr(request.app.state, "args", None), "host", "") or "127.0.0.1")
    return is_loopback(host)


@router.post("/api/devices/pairing", dependencies=[Depends(require_operator)])
def start_pairing(request: Request):
    """Start a pairing: an 12-character code (10 minutes, single use) and a QR code carrying the
    GUI address and the code."""
    pairing = _store(request).start_pairing(created_by=client_host(request))
    urls = _addresses(request)
    payload = f"mucli://pair?code={pairing['code']}" + (f"&url={urls[0]}" if urls else "")
    qr_svg = ""
    try:
        import segno
        qr_svg = segno.make(payload, error="m").svg_inline(scale=5, border=2, dark="#1a1814", light="#ffffff")
    except Exception:  # noqa: BLE001 - the code alone is enough
        qr_svg = ""
    return {**pairing, "urls": urls, "payload": payload, "qr_svg": qr_svg,
            "listening_on_loopback": _bound_to_loopback(request)}


@router.delete("/api/devices/pairing", dependencies=[Depends(require_operator)])
def cancel_pairing(request: Request):
    _store(request).cancel_pairings()
    return {"ok": True}


def _pair(request: Request, payload: Dict[str, Any]):
    try:
        return _store(request).pair(str(payload.get("code") or ""), str(payload.get("device_name") or ""),
                                    client=client_host(request))
    except PairingError as exc:
        headers = {"Retry-After": str(int(exc.retry_after + 0.999))} if exc.retry_after else None
        raise HTTPException(exc.status, str(exc), headers=headers) from exc


@router.post("/api/devices/pair")
def pair(request: Request, payload: Dict[str, Any]):
    """A device exchanges a pairing code for its token (shown once; store it securely)."""
    token, device = _pair(request, payload)
    return {"token": token, "device": device.public()}


@router.post("/api/devices/pair-browser")
def pair_browser(request: Request, response: Response, payload: Dict[str, Any]):
    """A browser on another machine pairs from the pairing page: the token goes into an HttpOnly,
    SameSite=Strict cookie and never into the body, so page scripts can't read it."""
    if not is_loopback(client_host(request)):
        origin = str(request.headers.get("origin") or "")
        from urllib.parse import urlsplit
        if origin and urlsplit(origin).netloc.lower() != str(request.headers.get("host") or "").lower():
            raise HTTPException(403, "cross-origin request refused")
    token, device = _pair(request, payload)
    response.headers["set-cookie"] = device_cookie(token, secure=request.url.scheme == "https")
    return {"device": device.public()}


# /self before /{device_id}, or DELETE /api/devices/self would match the operator route.
@router.get("/api/devices/self")
def whoami(request: Request):
    device = request_device(request)
    return {"device": device.public() if device else None, "loopback": device is None,
            "auth": request_auth(request) or None}


@router.delete("/api/devices/self")
def sign_out(request: Request, response: Response):
    device = request_device(request)
    if device is None:
        raise HTTPException(400, "this request isn't from a paired device")
    _store(request).revoke(device.id)
    if request_auth(request) == "cookie":
        response.headers["set-cookie"] = clear_device_cookie()
    return {"ok": True}


@router.get("/api/devices", dependencies=[Depends(require_local)])
def list_devices(request: Request):
    return {"devices": [d.public() for d in _store(request).list()]}


@router.delete("/api/devices/{device_id}", dependencies=[Depends(require_operator)])
def revoke_device(device_id: str, request: Request):
    device = _store(request).revoke(device_id)
    if device is None:
        raise HTTPException(404, "unknown device")
    return {"ok": True, "revoked": device.public()}


__all__ = ["router", "display_code"]
