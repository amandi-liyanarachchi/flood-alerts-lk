"""The risk engine, geohash, and the aggregation rules from brief section 6."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import geo, risk
from app.config import settings
from app.db import utcnow
from app.models import (
    AlertProposal,
    Feedback,
    GaugeReading,
    GaugeStation,
    LocationPing,
    RainfallObservation,
    User,
)
from app.security import hash_password, new_user_id

KELANIYA = (6.9553, 79.9219)


# --- geohash ---------------------------------------------------------------


def test_geohash_round_trips_within_the_cell():
    for precision in (4, 5, 6):
        code = geo.encode(*KELANIYA, precision)
        latitude, longitude = geo.decode_center(code)
        assert geo.haversine_km(*KELANIYA, latitude, longitude) < 40 / (2 ** (precision - 4))


def test_geohash_matches_known_values():
    # Cross-checked against a reference implementation.
    assert geo.encode(57.64911, 10.40744, 11) == "u4pruydqqvj"
    assert geo.encode(6.9271, 79.8612, 5) == "tc0z3"      # Colombo
    assert geo.encode(6.9553, 79.9219, 5) == "tc0zd"      # Kelaniya


def test_nearby_points_share_a_region_and_distant_ones_do_not():
    here = geo.encode(6.9553, 79.9219, 5)
    close = geo.encode(6.9560, 79.9225, 5)
    far = geo.encode(7.2906, 80.6337, 5)  # Kandy
    assert here == close
    assert here != far


def test_haversine_against_a_known_distance():
    # Colombo to Kandy, about 94 km great-circle.
    km = geo.haversine_km(6.9271, 79.8612, 7.2906, 80.6337)
    assert 90 < km < 100


def test_out_of_country_coordinates_are_flagged_not_rejected():
    assert geo.is_plausible_lk_coordinate(*KELANIYA) is True
    assert geo.is_plausible_lk_coordinate(51.5074, -0.1278) is False


# --- helpers ---------------------------------------------------------------


def make_users(db, count: int) -> list[User]:
    users = []
    for i in range(count):
        user = User(
            id=new_user_id(),
            nic=f"{900000000 + i}V",
            first_name="Test",
            last_name="User",
            phone="0712345678",
            password_hash=hash_password("secret123"),
        )
        db.add(user)
        users.append(user)
    db.commit()
    return users


def add_votes(db, users, yes_count, at=None, latitude=KELANIYA[0], longitude=KELANIYA[1]):
    at = at or utcnow()
    for index, user in enumerate(users):
        db.add(
            Feedback(
                user_id=user.id,
                flood_present=index < yes_count,
                latitude=latitude,
                longitude=longitude,
                answered_at=at - timedelta(minutes=5),
                geohash=geo.encode(latitude, longitude, 8),
            )
        )
    db.commit()


def add_station(db, level, alert=4.0, minor=5.0, major=7.0, at=None, hours=4):
    at = at or utcnow()
    db.add(
        GaugeStation(
            station="Test Gauge",
            basin="Kelani Ganga",
            latitude=KELANIYA[0],
            longitude=KELANIYA[1],
            alert_level_m=alert,
            minor_flood_level_m=minor,
            major_flood_level_m=major,
            source_unit="m",
            geohash=geo.encode(*KELANIYA, 8),
        )
    )
    for hours_ago in range(hours, -1, -1):
        db.add(
            GaugeReading(
                station="Test Gauge",
                basin="Kelani Ganga",
                water_level_m=level,
                rainfall_mm=0.0,
                observed_at=at - timedelta(hours=hours_ago),
            )
        )
    db.commit()


def add_rain(db, region, mm_per_hour, hours=24, at=None):
    at = at or utcnow()
    for hours_ago in range(hours):
        db.add(
            RainfallObservation(
                geohash=region,
                latitude=KELANIYA[0],
                longitude=KELANIYA[1],
                observed_at=at - timedelta(hours=hours_ago),
                precipitation_mm=mm_per_hour,
                is_forecast=False,
            )
        )
    db.commit()


# --- crowd aggregation (brief 6.2) -----------------------------------------


def test_below_the_respondent_floor_the_crowd_contributes_nothing(db):
    """3 of 4 crosses 75% but is noise. The floor exists for exactly this."""
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    users = make_users(db, 4)
    add_votes(db, users, yes_count=3)

    ctx = risk.build_context(db, region)
    assert ctx.respondents == 4
    assert ctx.yes_ratio == 0.75
    assert ctx.crowd_floor_met is False
    assert risk.crowd_subscore(ctx) is None


def test_at_the_floor_the_crowd_counts(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    users = make_users(db, 8)
    add_votes(db, users, yes_count=7)

    ctx = risk.build_context(db, region)
    assert ctx.crowd_floor_met is True
    assert risk.crowd_subscore(ctx) > 0.7


def test_one_user_answering_repeatedly_counts_once(db):
    """The cheapest attack on the model. Latest answer per user per window."""
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    (user,) = make_users(db, 1)
    now = utcnow()
    for minute in range(30):
        db.add(
            Feedback(
                user_id=user.id,
                flood_present=True,
                latitude=KELANIYA[0],
                longitude=KELANIYA[1],
                answered_at=now - timedelta(minutes=minute),
                geohash=geo.encode(*KELANIYA, 8),
            )
        )
    db.commit()

    ctx = risk.build_context(db, region)
    assert ctx.respondents == 1, "30 submissions from one person is one respondent"
    assert ctx.crowd_floor_met is False


def test_a_users_latest_answer_supersedes_their_earlier_one(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    users = make_users(db, 6)
    now = utcnow()
    for user in users:
        for minutes_ago, answer in ((40, True), (5, False)):
            db.add(
                Feedback(
                    user_id=user.id,
                    flood_present=answer,
                    latitude=KELANIYA[0],
                    longitude=KELANIYA[1],
                    answered_at=now - timedelta(minutes=minutes_ago),
                    geohash=geo.encode(*KELANIYA, 8),
                )
            )
    db.commit()

    ctx = risk.build_context(db, region)
    assert ctx.respondents == 6
    assert ctx.yes_count == 0, "the retraction should win"


def test_answers_outside_the_window_are_ignored(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    users = make_users(db, 8)
    add_votes(db, users, yes_count=8, at=utcnow() - timedelta(hours=6))

    ctx = risk.build_context(db, region)
    assert ctx.respondents == 0


# --- gauge scoring ---------------------------------------------------------


@pytest.mark.parametrize(
    "level,expected_at_least,expected_at_most",
    [(1.0, 0.0, 0.2), (4.0, 0.45, 0.55), (5.0, 0.70, 0.80), (7.0, 0.99, 1.0), (9.0, 0.99, 1.0)],
)
def test_gauge_subscore_is_anchored_on_published_flood_levels(
    db, level, expected_at_least, expected_at_most
):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=level)
    ctx = risk.build_context(db, region)
    score = risk.gauge_subscore(ctx)
    assert expected_at_least <= score <= expected_at_most


def test_rate_of_rise_raises_the_score(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    now = utcnow()
    db.add(
        GaugeStation(
            station="Test Gauge",
            basin="Kelani Ganga",
            latitude=KELANIYA[0],
            longitude=KELANIYA[1],
            alert_level_m=4.0,
            minor_flood_level_m=5.0,
            major_flood_level_m=7.0,
            geohash=geo.encode(*KELANIYA, 8),
        )
    )
    for hours_ago, level in ((6, 1.0), (3, 2.0), (0, 3.0)):
        db.add(
            GaugeReading(
                station="Test Gauge",
                water_level_m=level,
                observed_at=now - timedelta(hours=hours_ago),
            )
        )
    db.commit()

    ctx = risk.build_context(db, region)
    assert ctx.rise_m_per_3h == pytest.approx(1.0, abs=0.05)
    assert risk.gauge_subscore(ctx) > 0.5 * (3.0 / 4.0)


# --- rainfall scoring ------------------------------------------------------


def test_rainfall_subscore_uses_the_met_department_scale(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)

    add_rain(db, region, mm_per_hour=0.0)
    assert risk.rainfall_subscore(risk.build_context(db, region)) == pytest.approx(0.0, abs=0.01)

    db.query(RainfallObservation).delete()
    db.commit()
    add_rain(db, region, mm_per_hour=75 / 24)  # exactly "heavy"
    assert risk.rainfall_subscore(risk.build_context(db, region)) == pytest.approx(0.6, abs=0.05)

    db.query(RainfallObservation).delete()
    db.commit()
    add_rain(db, region, mm_per_hour=150 / 24)  # "very heavy"
    assert risk.rainfall_subscore(risk.build_context(db, region)) == pytest.approx(1.0, abs=0.01)


# --- fusion ----------------------------------------------------------------


def test_the_crowd_cannot_raise_an_alert_on_its_own(db):
    """The poisoning defence: unanimous "yes" with no physical signal at all."""
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    users = make_users(db, 20)
    add_votes(db, users, yes_count=20)

    result = risk.RulesV1Engine().score(risk.build_context(db, region))
    assert result.severity is None, "20 people alone must not trigger a public warning"
    # The signal is not discarded -- it is routed to the operator to investigate.
    assert result.features["crowdOnlySignal"] is True
    assert result.features["physicalSupport"] == 0.0


def test_crowd_only_signals_never_become_alert_proposals(db):
    from sqlalchemy import func as sa_func

    region = geo.encode(*KELANIYA, settings.geohash_precision)
    users = make_users(db, 20)
    add_votes(db, users, yes_count=20)
    db.add(
        LocationPing(
            user_id=users[0].id,
            latitude=KELANIYA[0],
            longitude=KELANIYA[1],
            recorded_at=utcnow(),
            source="auto",
            geohash=geo.encode(*KELANIYA, 8),
        )
    )
    db.commit()

    risk.evaluate_all(db)
    assert db.scalar(select(sa_func.count(AlertProposal.id))) == 0


def test_the_crowd_corroborates_a_physical_signal(db):
    """The research contribution: same rivers, more confidence with people."""
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=4.6)
    add_rain(db, region, mm_per_hour=3.0)

    without = risk.RulesV1Engine().score(risk.build_context(db, region)).score

    users = make_users(db, 10)
    add_votes(db, users, yes_count=9)
    with_crowd = risk.RulesV1Engine().score(risk.build_context(db, region))

    assert with_crowd.score > without
    assert with_crowd.features["crowdConfirms"] is True


def test_a_river_above_its_major_flood_level_always_scores_high(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=8.0, major=7.0)

    result = risk.RulesV1Engine().score(risk.build_context(db, region))
    assert result.severity == "high"
    assert result.score >= 0.9


def test_baseline_engine_ignores_gauges_and_crowd(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=8.0, major=7.0)
    users = make_users(db, 20)
    add_votes(db, users, yes_count=20)

    baseline = risk.RainfallOnlyEngine().score(risk.build_context(db, region))
    assert baseline.severity is None, "with no rain the baseline sees nothing"

    primary = risk.RulesV1Engine().score(risk.build_context(db, region))
    assert primary.severity == "high"


def test_every_score_carries_its_own_explanation(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=5.5)
    result = risk.RulesV1Engine().score(risk.build_context(db, region))

    assert "contributions" in result.features
    assert result.features["gauge"]["waterLevelM"] == 5.5
    assert result.reasons


def test_public_message_is_plain_language(db):
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=8.0)
    ctx = risk.build_context(db, region)
    result = risk.RulesV1Engine().score(ctx)
    message = risk._message_for(ctx, result)

    for jargon in ("geohash", "score", "0.", "rules_v1", "subscore"):
        assert jargon not in message
    assert len(message) < 250


# --- proposals -------------------------------------------------------------


def test_a_wet_region_produces_one_proposal_not_thirty_six(db):
    """Alert fatigue is the failure mode that kills these systems."""
    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=8.0)
    db.add(
        LocationPing(
            user_id=make_users(db, 1)[0].id,
            latitude=KELANIYA[0],
            longitude=KELANIYA[1],
            recorded_at=utcnow(),
            source="auto",
            geohash=geo.encode(*KELANIYA, 8),
        )
    )
    db.commit()

    for _ in range(6):
        risk.evaluate_all(db)

    assert db.scalar(select(__import__("sqlalchemy").func.count(AlertProposal.id))) == 1


def test_nothing_is_sent_without_a_human(db):
    """evaluate_all proposes. It never publishes."""
    from app.models import Alert

    region = geo.encode(*KELANIYA, settings.geohash_precision)
    add_station(db, level=9.0)
    risk.evaluate_all(db)

    assert db.scalars(select(AlertProposal)).first() is not None
    assert db.scalars(select(Alert)).first() is None


def test_station_regions_are_evaluated_even_with_no_users(db):
    """A 20-user pilot must still have a physical picture of the basin."""
    add_station(db, level=2.0)
    regions = risk.populated_regions(db)
    assert geo.encode(*KELANIYA, settings.geohash_precision) in regions
