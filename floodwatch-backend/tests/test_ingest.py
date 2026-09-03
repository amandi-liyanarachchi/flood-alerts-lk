"""Ingestion parsers, against the real upstream payload shapes.

The fixtures below are the actual JSON returned by the Irrigation Department's
feature services on 2026-08-28, trimmed to a handful of features. They are here
because the two things most likely to break this system silently -- the foot /
metre mix and the epoch-millisecond timestamps -- are only visible in real data.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import respx
from sqlalchemy import select

from app.config import settings
from app.ingest import irrigation
from app.models import GaugeReading, GaugeStation

HYDROSTATIONS = {
    "features": [
        {
            "attributes": {
                "objectid": 1,
                "station": "Nagalagam Street",
                "latitude": 6.958265,
                "longitude": 79.878642,
                "basin": "Kelani Ganga",
                "Tributory": "Kelani Ganga",
                # This station publishes in FEET. Everything else is metres.
                "Alert_Level": 4.0,
                "Minor_Flood_Level": 5.0,
                "Major_Flood_Level": 7.0,
                "Unit": "ft",
                "Elivation_m_MSL": 1.2,
            },
            "geometry": {"x": 79.878642, "y": 6.958265},
        },
        {
            "attributes": {
                "objectid": 2,
                "station": "Glencourse",
                "latitude": 6.976981,
                "longitude": 80.194247,
                "basin": "Kelani Ganga",
                "Tributory": "Kelani Ganga",
                "Alert_Level": 15.0,
                "Minor_Flood_Level": 16.0,
                "Major_Flood_Level": 19.0,
                "Unit": "m",
                "Elivation_m_MSL": 20.0,
            },
            "geometry": {"x": 80.194247, "y": 6.976981},
        },
    ]
}

GAUGES = {
    "features": [
        {
            "attributes": {
                "basin": "Kelani Ganga",
                "gauge": "Glencourse",
                "water_level": 9.86,
                "rain_fall": 0.0,
                "CreationDate": 1787909776557,
            },
            "geometry": {"x": 80.194088, "y": 6.976595},
        },
        {
            "attributes": {
                "basin": "Kelani Ganga",
                "gauge": "Nagalagam Street",
                # Published in feet: 8.0 ft -> 2.438 m
                "water_level": 8.0,
                "rain_fall": 12.5,
                "CreationDate": 1787909810023,
            },
            "geometry": {"x": 79.878642, "y": 6.958265},
        },
    ]
}

THRESHOLDS = {
    "features": [
        {
            "attributes": {
                "gauge": "Glencourse",
                "alertpull": 15.0,
                # The real disagreement: hydrostations says 16, this says 15.5.
                "minorpull": 15.5,
                "majorpull": 19.0,
            }
        }
    ]
}


def _route(payload):
    return httpx.Response(200, json=payload)


@respx.mock
def test_stations_are_converted_to_metres(db):
    respx.get(url__startswith=f"{settings.arcgis_base}/hydrostations").mock(
        return_value=_route(HYDROSTATIONS)
    )
    result = irrigation.refresh_stations(db)
    assert result.ok and result.stored == 2

    feet_station = db.scalar(select(GaugeStation).where(GaugeStation.station == "Nagalagam Street"))
    # 4 ft = 1.219 m. Comparing a metre reading against "4" would put central
    # Colombo permanently in flood.
    assert feet_station.source_unit == "ft"
    assert feet_station.alert_level_m == 1.219
    assert feet_station.major_flood_level_m == 2.134

    metre_station = db.scalar(select(GaugeStation).where(GaugeStation.station == "Glencourse"))
    assert metre_station.source_unit == "m"
    assert metre_station.alert_level_m == 15.0


@respx.mock
def test_readings_are_parsed_and_unit_corrected(db):
    respx.get(url__startswith=f"{settings.arcgis_base}/hydrostations").mock(
        return_value=_route(HYDROSTATIONS)
    )
    respx.get(url__startswith=f"{settings.arcgis_base}/gauges_2_view").mock(
        return_value=_route(GAUGES)
    )
    irrigation.refresh_stations(db)
    result = irrigation.fetch_readings(db, max_pages=1)
    assert result.ok and result.stored == 2

    glencourse = db.scalar(select(GaugeReading).where(GaugeReading.station == "Glencourse"))
    assert glencourse.water_level_m == 9.86
    # 1787909776557 ms -> 2026-08-28T10:56:16Z
    assert isinstance(glencourse.observed_at, datetime)
    assert glencourse.observed_at.year == 2026

    nagalagam = db.scalar(select(GaugeReading).where(GaugeReading.station == "Nagalagam Street"))
    assert nagalagam.water_level_m == 2.438, "8 ft must be stored as 2.438 m"
    assert nagalagam.rainfall_mm == 12.5


@respx.mock
def test_replayed_readings_are_not_duplicated(db):
    respx.get(url__startswith=f"{settings.arcgis_base}/hydrostations").mock(
        return_value=_route(HYDROSTATIONS)
    )
    respx.get(url__startswith=f"{settings.arcgis_base}/gauges_2_view").mock(
        return_value=_route(GAUGES)
    )
    irrigation.refresh_stations(db)
    irrigation.fetch_readings(db, max_pages=1)
    second = irrigation.fetch_readings(db, max_pages=1)

    assert second.stored == 0, "the feed is append-only; we re-read the newest page every run"
    from sqlalchemy import func

    assert db.scalar(select(func.count(GaugeReading.id))) == 2


@respx.mock
def test_threshold_disagreement_is_surfaced_not_silently_resolved(db):
    respx.get(url__startswith=f"{settings.arcgis_base}/hydrostations").mock(
        return_value=_route(HYDROSTATIONS)
    )
    respx.get(url__startswith=f"{settings.arcgis_base}/gauges_2_view").mock(
        return_value=_route(THRESHOLDS)
    )
    irrigation.refresh_stations(db)
    mismatches = irrigation.check_threshold_agreement(db)

    assert any(m.get("threshold") == "minor" for m in mismatches)
    entry = next(m for m in mismatches if m.get("threshold") == "minor")
    assert entry["hydrostations_m"] == 16.0
    assert entry["gauges_view_m"] == 15.5


@respx.mock
def test_arcgis_error_body_does_not_crash_the_ingester(db):
    """ArcGIS returns HTTP 200 with an error object. It must not look like data."""
    respx.get(url__startswith=f"{settings.arcgis_base}/hydrostations").mock(
        return_value=httpx.Response(200, json={"error": {"code": 400, "message": "Invalid query"}})
    )
    result = irrigation.refresh_stations(db)
    assert result.ok is False
    assert "Invalid query" in result.detail


@respx.mock
def test_upstream_outage_degrades_rather_than_raising(db):
    """The gauge feed is someone else's infrastructure. It will go down."""
    respx.get(url__startswith=f"{settings.arcgis_base}/hydrostations").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = irrigation.refresh_stations(db)
    assert result.ok is False
    assert result.stored == 0


@respx.mock
def test_a_malformed_feature_is_skipped_not_fatal(db):
    respx.get(url__startswith=f"{settings.arcgis_base}/hydrostations").mock(
        return_value=_route(
            {
                "features": [
                    {"attributes": {"station": "", "latitude": 1, "longitude": 1}},
                    {"attributes": {"station": "No Coordinates"}},
                    HYDROSTATIONS["features"][1],
                ]
            }
        )
    )
    result = irrigation.refresh_stations(db)
    assert result.ok and result.stored == 1
