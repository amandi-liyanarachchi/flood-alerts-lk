"""Pre-presentation check. Run this the night before, and again on the morning.

    python -m scripts.preflight

Walks the entire demo path against a RUNNING server -- registers a user, sends a
ping, submits feedback, runs the risk engine, approves an alert, checks the user
can see it, then retracts it and cleans up after itself. If this prints all
green, the demo works.

It talks to the server over HTTP exactly as the phone does, so it catches the
things unit tests cannot: wrong port, stale database, firewall, a server that
did not actually start.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request

PASS = "  [ok]   "
FAIL = "  [FAIL] "
WARN = "  [warn] "

failures: list[str] = []
warnings: list[str] = []


def call(method: str, url: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except Exception:  # noqa: BLE001
            return exc.code, {}
    except Exception as exc:  # noqa: BLE001
        return 0, {"_error": str(exc)}


def check(label: str, ok: bool, detail: str = "", fatal: bool = True) -> bool:
    if ok:
        print(PASS + label + (f"  {detail}" if detail else ""))
    elif fatal:
        print(FAIL + label + (f"  {detail}" if detail else ""))
        failures.append(label)
    else:
        print(WARN + label + (f"  {detail}" if detail else ""))
        warnings.append(label)
    return ok


def lan_address() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except Exception:  # noqa: BLE001
        return "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--admin-token", default="demo-admin")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    api = f"{base}/api/v1"
    admin = {"X-Admin-Token": args.admin_token}

    print(f"\nChecking {base}\n" + "-" * 62)

    # 1. server up
    status, body = call("GET", f"{base}/health")
    if not check("Server is responding", status == 200, body.get("_error", "")):
        print("\nThe server is not running. Start it with ./run_local.sh (or run_local.bat)\n")
        return 1

    # 2. admin token
    status, summary = call("GET", f"{api}/admin/summary", headers=admin)
    if not check("Admin token accepted", status == 200,
                 "" if status == 200 else f"HTTP {status} -- check ADMIN_TOKEN"):
        return 1

    check(
        "Demo data present",
        (summary.get("users") or 0) > 0 and (summary.get("pings24h") or 0) > 0,
        f"{summary.get('users')} users, {summary.get('pings24h')} pings in 24h",
    )
    check(
        "Gauge stations loaded",
        (summary.get("stations") or 0) > 0,
        f"{summary.get('stations')} stations",
    )

    # 3. the client's own round trip
    nic = "998000001V"
    status, body = call("POST", f"{api}/auth/register", {
        "nic": nic, "firstName": "Preflight", "lastName": "Check",
        "phone": "0712345678", "password": "preflight123",
    })
    if status == 409:
        status, body = call("POST", f"{api}/auth/login", {"nic": nic, "password": "preflight123"})
    if not check("Register / login round trip", status in (200, 201),
                 body.get("error", {}).get("message", "")):
        return 1

    token = body.get("token", "")
    auth = {"Authorization": f"Bearer {token}"}
    check("Token issued", bool(token))

    status, body = call("POST", f"{api}/locations", {
        "latitude": 6.9553, "longitude": 79.9219, "accuracy": 12.5,
        "recordedAt": "2026-08-31T09:15:00Z", "source": "auto",
    }, auth)
    check("Location ping accepted", status == 201 and body.get("accepted") is True)

    status, body = call("POST", f"{api}/feedback", {
        "floodPresent": True, "latitude": 6.9553, "longitude": 79.9219,
        "answeredAt": "2026-08-31T09:16:00Z",
    }, auth)
    check("Feedback accepted", status == 201)

    status, body = call("POST", f"{api}/devices/fcm-token",
                        {"fcmToken": "preflight-token", "platform": "android"}, auth)
    check("FCM token registration accepted", status == 200)

    # 4. the model
    status, body = call("POST", f"{api}/admin/evaluate", {}, admin)
    check("Risk engine runs", status == 200,
          f"{body.get('regions')} regions scored, {body.get('proposals')} proposals")

    status, proposals = call("GET", f"{api}/admin/proposals", headers=admin)
    pending = proposals.get("proposals", []) if status == 200 else []
    check(
        "A proposal is waiting for approval",
        len(pending) > 0,
        f"{len(pending)} pending" if pending else "run with --reset to re-seed the flood scenario",
        fatal=False,
    )

    # 5. approve -> user sees it -> retract
    if pending:
        proposal = pending[0]
        pid = proposal["id"]
        # Query the alert at the PROPOSAL's own coordinates, not at a fixed
        # point. An alert covers one region, and the newest proposal is not
        # necessarily for the region this script pinged from.
        lat, lon = proposal["latitude"], proposal["longitude"]

        status, published = call("POST", f"{api}/admin/proposals/{pid}/approve",
                                 {"operator": "preflight"}, admin)
        approved = check("Alert publishes", status == 200,
                         f"alert {published.get('alertId')} for region {proposal['region']}")

        if approved:
            status, seen = call(
                "GET", f"{api}/alerts/active?latitude={lat}&longitude={lon}", headers=auth
            )
            check("User receives the alert", status == 200 and seen.get("alert") is not None,
                  (seen.get("alert") or {}).get("severity", ""))

            # And a user well outside the region must NOT see it.
            status, elsewhere = call(
                "GET", f"{api}/alerts/active?latitude=7.2906&longitude=80.6337", headers=auth
            )
            check("Alert does not leak to other regions",
                  elsewhere.get("alert") is None, "checked from Kandy")

            push = published.get("push") or {}
            check(
                "Push",
                True,
                "disabled -- the app will show the alert on its next refresh"
                if push.get("enabled") is False
                else f"sent to {push.get('sent')}/{push.get('targeted')} devices",
                fatal=False,
            )

            call("POST", f"{api}/admin/alerts/{published.get('alertId')}/retract",
                 {"operator": "preflight"}, admin)
            status, seen = call(
                "GET", f"{api}/alerts/active?latitude={lat}&longitude={lon}", headers=auth
            )
            check("Retraction clears the alert", seen.get("alert") is None)

    # 6. tidy up after ourselves
    call("DELETE", f"{api}/account", {"password": "preflight123"}, auth)

    # 7. what to type into the app
    port = base.rsplit(":", 1)[-1] if ":" in base.rsplit("/", 1)[-1] else "8000"
    print("-" * 62)
    print("\nMobile app base URL:")
    print(f"  Android emulator    http://10.0.2.2:{port}/api/v1")
    print(f"  Phone over USB      http://localhost:{port}/api/v1")
    print(f"                      (first run: adb reverse tcp:{port} tcp:{port})")
    print(f"  Phone over wifi     http://{lan_address()}:{port}/api/v1")
    print(f"\nAdmin dashboard       {base}   token: {args.admin_token}")
    print("Demo login            NIC 999000000V   password demo1234")

    print("\n" + "-" * 62)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for item in failures:
            print(f"  - {item}")
        return 1
    if warnings:
        print(f"All essential checks passed, with {len(warnings)} warning(s).")
    else:
        print("All checks passed. The demo will work.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
