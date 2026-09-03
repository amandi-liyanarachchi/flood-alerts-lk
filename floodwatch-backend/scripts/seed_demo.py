"""Seed a demonstrable database.

    python -m scripts.seed_demo               # users, pings, feedback, real gauges
    python -m scripts.seed_demo --flood       # ...and drive one region into flood

Creates 18 participants around Kelaniya / Colombo North, three days of location
history at the real 10-minute cadence, crowd answers, and one hand-entered
ground-truth flood event so the evaluation endpoint has something to score
against. Live gauge and rainfall data is pulled from the real upstreams if the
network allows; otherwise plausible synthetic readings are generated so the demo
never depends on someone else's uptime.

Everything created here is obviously synthetic: NICs are in a reserved block and
every name is drawn from a fixed list. Nothing in this script should ever run
against a database holding real participants -- it refuses if it finds users it
did not create.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import timedelta

from sqlalchemy import select

sys.path.insert(0, ".")

from app import geo, security  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db, utcnow  # noqa: E402
from app.ingest import irrigation, rainfall  # noqa: E402
from app.models import (  # noqa: E402
    ConsentRecord,
    Device,
    Feedback,
    FloodEvent,
    GaugeReading,
    GaugeStation,
    LocationPing,
    User,
)

random.seed(20260828)

# Reserved NIC block for synthetic participants: 9990xxxxx + V.
NIC_PREFIX = "9990"

FIRST = ["Nimal", "Kamala", "Sunil", "Ayesha", "Ruwan", "Dilani", "Chamara", "Nadeeka",
         "Tharindu", "Sanduni", "Kasun", "Iresha", "Malith", "Piyumi", "Janaka",
         "Hasini", "Dinesh", "Rashmi"]
LAST = ["Perera", "Fernando", "Silva", "Jayawardena", "Bandara", "Wickramasinghe",
        "Gunasekara", "Rajapaksa", "Dissanayake", "Herath"]

# Kelaniya bridge area, where the Kelani meets greater Colombo.
CENTRE_LAT, CENTRE_LON = 6.9553, 79.9219


def jitter(lat: float, lon: float, km: float) -> tuple[float, float]:
    bearing = random.uniform(0, 2 * math.pi)
    distance = random.uniform(0, km)
    d_lat = distance / 111.0 * math.cos(bearing)
    d_lon = distance / (111.0 * math.cos(math.radians(lat))) * math.sin(bearing)
    return lat + d_lat, lon + d_lon


def seed(flood: bool = False) -> None:
    init_db()
    db = SessionLocal()
    now = utcnow()

    real_users = db.scalars(select(User).where(~User.nic.startswith(NIC_PREFIX))).all()
    if real_users:
        print(f"REFUSING: {len(real_users)} non-synthetic user(s) in this database.")
        print("Seeding is for an empty or demo-only database. Point DATABASE_URL elsewhere.")
        return

    # --- participants --------------------------------------------------
    users: list[User] = []
    for i in range(18):
        nic = f"{NIC_PREFIX}{i:05d}V"
        user = db.scalar(select(User).where(User.nic == nic))
        if user is None:
            user = User(
                id=security.new_user_id(),
                nic=nic,
                first_name=FIRST[i % len(FIRST)],
                last_name=LAST[i % len(LAST)],
                phone=f"07{random.randint(10000000, 99999999)}",
                password_hash=security.hash_password("demo1234"),
            )
            db.add(user)
            db.flush()
            db.add(
                ConsentRecord(user_id=user.id, notice_version=settings.consent_notice_version)
            )
            db.add(
                Device(
                    user_id=user.id,
                    fcm_token=f"demo-token-{user.id}",
                    platform="android" if i % 3 else "ios",
                )
            )
        users.append(user)
    db.commit()
    print(f"participants: {len(users)} (password 'demo1234', NIC {NIC_PREFIX}00000V ...)")

    # --- three days of location history --------------------------------
    added = 0
    for index, user in enumerate(users):
        home_lat, home_lon = jitter(CENTRE_LAT, CENTRE_LON, 6.0)
        # 3 days at one ping every 10 minutes = 432 per user, the real cadence.
        for tick in range(432):
            recorded_at = now - timedelta(minutes=10 * tick)
            lat, lon = jitter(home_lat, home_lon, 0.4)
            ping = LocationPing(
                user_id=user.id,
                latitude=lat,
                longitude=lon,
                accuracy=round(random.uniform(5, 30), 1),
                recorded_at=recorded_at,
                source="manual" if tick % 97 == 0 else "auto",
                geohash=geo.encode(lat, lon, precision=8),
                received_at=recorded_at + timedelta(seconds=random.randint(1, 40)),
                lag_seconds=random.randint(1, 40),
                in_country=True,
            )
            db.add(ping)
            added += 1
        db.commit()
    print(f"location pings: {added}")

    # --- gauges: real data if reachable, synthetic if not ---------------
    result = irrigation.refresh_stations(db)
    if result.ok and result.stored:
        print(f"stations: {result.stored} from the Irrigation Department")
        readings = irrigation.fetch_readings(db, max_pages=2)
        print(f"gauge readings: {readings.stored} stored of {readings.fetched} fetched")
    else:
        print(f"upstream unreachable ({result.detail[:80]}) -- generating synthetic gauges")
        _synthetic_gauges(db, now)

    # --- rainfall ------------------------------------------------------
    regions = sorted(
        {
            row[0][: settings.geohash_precision]
            for row in db.execute(select(LocationPing.geohash).distinct())
            if row[0]
        }
    )[:6]
    rain = rainfall.fetch_for_regions(db, regions)
    print(f"rainfall: {rain.stored} hourly values across {len(regions)} regions ({'ok' if rain.ok else 'failed'})")

    # --- crowd answers -------------------------------------------------
    answers = 0
    for user in users:
        # The last one is 20 minutes old so it falls inside the 60-minute
        # aggregation window when the evaluation runs a moment after seeding.
        for hours_ago in (30, 18, 6, 0.34):
            last = db.scalar(
                select(LocationPing)
                .where(LocationPing.user_id == user.id)
                .order_by(LocationPing.recorded_at.desc())
                .limit(1)
            )
            says_yes = flood and hours_ago <= 6 and random.random() < 0.85
            db.add(
                Feedback(
                    user_id=user.id,
                    flood_present=says_yes,
                    latitude=last.latitude,
                    longitude=last.longitude,
                    answered_at=now - timedelta(hours=hours_ago),
                    geohash=last.geohash,
                    location_inferred=False,
                )
            )
            answers += 1
    db.commit()
    print(f"crowd answers: {answers}")

    if flood:
        _force_flood(db, now)

    # --- ground truth for the evaluation endpoint -----------------------
    region = geo.encode(CENTRE_LAT, CENTRE_LON, settings.geohash_precision)
    if not db.scalar(select(FloodEvent).where(FloodEvent.geohash == region)):
        db.add(
            FloodEvent(
                geohash=region,
                started_at=now - timedelta(days=2, hours=6),
                ended_at=now - timedelta(days=2),
                severity="moderate",
                source="SYNTHETIC — replace with a real DMC situation report citation",
                notes="Seeded so /admin/metrics returns numbers. Not a real event.",
            )
        )
        db.commit()
    print(f"ground truth: 1 synthetic flood event in region {region}")

    print("\nDone. Open http://localhost:8000 and connect with your ADMIN_TOKEN.")
    print("Then press 'Run evaluation' to score every region.")
    db.close()


def _synthetic_gauges(db, now) -> None:
    """Real Kelani basin stations with their real published flood levels.

    Values transcribed from hydrostations/FeatureServer/0 on 2026-08-28 so the
    demo is realistic even with no network. Levels are converted to metres.
    """
    stations = [
        # station, tributary, lat, lon, alert, minor, major, unit
        ("Nagalagam Street", "Kelani Ganga", 6.958265, 79.878642, 4.0, 5.0, 7.0, "ft"),
        ("Hanwella", "Kelani Ganga", 6.909700, 80.083126, 6.5, 8.0, 10.0, "m"),
        ("Glencourse", "Kelani Ganga", 6.976981, 80.194247, 15.0, 16.0, 19.0, "m"),
        ("Kithulgala", "Kelani Ganga", 6.991259, 80.419213, 3.0, 4.0, 6.0, "m"),
        ("Holombuwa", "Gurugoda Oya", 7.188201, 80.266331, 3.0, 3.0, 5.0, "m"),
        ("Deraniyagala", "Seethawaka Ganga", 6.925934, 80.339212, 4.8, 6.0, 6.0, "m"),
        ("Norwood", "Kehelgamu Oya", 6.840145, 80.611301, 1.5, 3.0, 4.0, "m"),
    ]
    factor = irrigation.FEET_TO_METRES
    for name, trib, lat, lon, alert, minor, major, unit in stations:
        f = factor if unit == "ft" else 1.0
        station = db.scalar(select(GaugeStation).where(GaugeStation.station == name))
        if station is None:
            station = GaugeStation(station=name)
            db.add(station)
        station.basin = "Kelani Ganga"
        station.tributary = trib
        station.latitude, station.longitude = lat, lon
        station.alert_level_m = round(alert * f, 3)
        station.minor_flood_level_m = round(minor * f, 3)
        station.major_flood_level_m = round(major * f, 3)
        station.source_unit = unit
        station.geohash = geo.encode(lat, lon, precision=8)
    db.commit()

    for name, *_ , alert, minor, major, unit in stations:
        f = factor if unit == "ft" else 1.0
        base = alert * f * 0.55
        for hours_ago in range(72, -1, -1):
            db.add(
                GaugeReading(
                    station=name,
                    basin="Kelani Ganga",
                    water_level_m=round(base * random.uniform(0.9, 1.1), 3),
                    rainfall_mm=round(max(0.0, random.gauss(0.6, 1.4)), 1),
                    # Offset by 5 minutes so --flood can write a NEWER reading on
                    # the hour and actually become the latest observation.
                    observed_at=now - timedelta(hours=hours_ago, minutes=5),
                )
            )
        db.commit()
    print(f"synthetic gauges: {len(stations)} stations, 73 readings each")


def _force_flood(db, now) -> None:
    """Push the nearest station above its major flood level, rising fast.

    Guarantees the demo produces a 'high' proposal for a human to approve.
    """
    station = db.scalar(select(GaugeStation).where(GaugeStation.station == "Nagalagam Street"))
    if station is None:
        station = db.scalars(select(GaugeStation)).first()
    if station is None:
        print("no station to flood")
        return

    major = station.major_flood_level_m or 5.0
    for hours_ago in range(8, -1, -1):
        level = major * (0.72 + 0.05 * (8 - hours_ago))
        db.add(
            GaugeReading(
                station=station.station,
                basin=station.basin,
                water_level_m=round(level, 3),
                rainfall_mm=round(random.uniform(6, 16), 1),
                observed_at=now - timedelta(hours=hours_ago),
            )
        )
    db.commit()
    print(f"forced flood: {station.station} rising to {round(major * 1.12, 2)} m "
          f"(major flood level {major} m)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--flood", action="store_true", help="drive one region into flood")
    seed(flood=parser.parse_args().flood)
