"""Measure the end-to-end warning pipeline latency against a running server.

    python -m scripts.measure_latency --base http://127.0.0.1:8000 --token <admin> --repeat 30

Decomposes the path from "a triggering observation exists" to "the client can
read the alert" into the five stages the architecture actually has, and reports
each separately. An aggregate figure would hide which stage is slow, which is the
only thing the number is useful for.

Stages measured:
  1  store       persist one gauge reading (the database write ingest performs)
  2  score       POST /admin/evaluate -- run the risk engine over every region
  3  read queue  GET  /admin/proposals -- what the operator sees
  4  authorise   POST /admin/proposals/{id}/approve -- publish
  5  deliver     GET  /alerts/active -- what the phone reads

Stage 4 excludes the human. The operator's decision time is not a software
property and is reported separately as a known, dominant term.

The *fetch* half of ingestion -- the HTTP round trip to the Irrigation
Department service -- is deliberately not measured. It is dominated by an
external service outside this system's control, and the environment these
measurements were taken in has no route to it. What is measured is the part the
system is responsible for: storing the reading once it has arrived.

Every measurement is wall-clock at the client, so it includes HTTP framing,
serialisation and the database write -- the same path the phone takes. The
process runs on one machine, so network transit is excluded; that is stated in
the reported result rather than silently assumed away.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


def call(method: str, url: str, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read() or b"{}")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            payload = {"_raw": raw[:200].decode(errors="replace")}
        status = exc.code
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return status, payload, elapsed_ms


def summarise(name: str, samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "stage": name,
        "n": len(samples),
        "median_ms": round(statistics.median(ordered), 1),
        "p90_ms": round(ordered[int(0.9 * (len(ordered) - 1))], 1),
        "max_ms": round(max(ordered), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--out", default="thesis/latency.json")
    args = parser.parse_args()

    api = args.base.rstrip("/") + "/api/v1"
    admin_headers = {"X-Admin-Token": args.token}

    status, summary, _ = call("GET", f"{api}/admin/summary", headers=admin_headers)
    if status != 200:
        print(f"cannot reach the admin API: {status} {summary}")
        return 1
    print(f"server reachable -- {summary.get('users')} users, "
          f"{summary.get('stations')} stations\n")

    # A user token, so stage 5 is measured exactly as the phone experiences it
    # (bearer auth, position query) rather than through an admin route.
    nic = f"{int(time.time()) % 900000000 + 100000000}V"
    status, body, _ = call("POST", f"{api}/auth/register", {
        "nic": nic, "firstName": "Latency", "lastName": "Probe",
        "phone": "0770000000", "password": "Measure#2026",
    })
    if status not in (200, 201):
        print(f"could not create a probe account: {status} {body}")
        return 1
    user_headers = {"Authorization": f"Bearer {body['token']}"}

    status, gauges, _ = call("GET", f"{api}/admin/gauges", headers=admin_headers)
    station = gauges["stations"][0]
    lat, lon = station["latitude"], station["longitude"]

    # Put the probe account in the region so stage 5 has something to resolve.
    call("POST", f"{api}/locations", {
        "latitude": lat, "longitude": lon, "accuracy": 12.0,
        "recordedAt": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "source": "auto",
    }, headers=user_headers)

    from app.db import SessionLocal  # noqa: PLC0415 -- only needed for stage 1
    from app.models import GaugeReading  # noqa: PLC0415

    stages: dict[str, list[float]] = {
        "store": [], "score": [], "read_queue": [], "authorise": [], "deliver": [],
    }
    published = 0

    for i in range(args.repeat):
        observed = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=i)
        level = (station.get("majorFloodLevelM") or 3.0) + 0.4

        start = time.perf_counter()
        with SessionLocal() as session:
            session.add(GaugeReading(
                station=station["station"], water_level_m=level,
                rainfall_mm=45.0, observed_at=observed,
                ingested_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            session.commit()
        stages["store"].append((time.perf_counter() - start) * 1000.0)

        _, _, ms = call("POST", f"{api}/admin/evaluate", {}, headers=admin_headers)
        stages["score"].append(ms)

        status, queue, ms = call("GET", f"{api}/admin/proposals?status=proposed",
                                 headers=admin_headers)
        stages["read_queue"].append(ms)

        proposals = queue.get("proposals") or []
        if proposals:
            pid = proposals[0]["id"]
            status, alert, ms = call("POST", f"{api}/admin/proposals/{pid}/approve",
                                     {"operator": "latency-probe"}, headers=admin_headers)
            if status in (200, 201):
                stages["authorise"].append(ms)
                published += 1
                # Retract before the next iteration. A region that already holds a
                # live alert is not re-proposed -- that suppression is deliberate
                # (§alert fatigue), but it would otherwise leave this loop with a
                # single authorise sample.
                aid = alert.get("alertId")
                if aid:
                    call("POST", f"{api}/admin/alerts/{aid}/retract",
                         {"operator": "latency-probe"}, headers=admin_headers)

        _, _, ms = call("GET", f"{api}/alerts/active?latitude={lat}&longitude={lon}",
                        headers=user_headers)
        stages["deliver"].append(ms)

    call("DELETE", f"{api}/account", {"password": "Measure#2026"}, headers=user_headers)

    rows = [summarise(name, s) for name, s in stages.items() if s]
    total_median = sum(r["median_ms"] for r in rows)

    print(f"{'stage':<12}{'n':>5}{'median ms':>12}{'p90 ms':>10}{'max ms':>10}")
    for r in rows:
        print(f"{r['stage']:<12}{r['n']:>5}{r['median_ms']:>12}{r['p90_ms']:>10}{r['max_ms']:>10}")
    print(f"\nsum of stage medians: {total_median:.1f} ms   "
          f"({published} alerts actually published)")

    payload = {
        "base": args.base,
        "repeat": args.repeat,
        "alerts_published": published,
        "stages": rows,
        "sum_of_stage_medians_ms": round(total_median, 1),
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "note": "Single machine, loopback. Excludes network transit, push provider "
                "delivery, and the operator's decision time.",
    }
    with open(args.out, "w") as handle:
        json.dump(payload, handle, indent=2)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
