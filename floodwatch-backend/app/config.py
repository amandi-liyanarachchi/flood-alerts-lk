"""Application configuration.

Everything is read from the environment. See .env.example for the full list.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core -----------------------------------------------------------
    environment: str = "development"
    # SQLite by default so the server runs on a laptop with no Docker and no
    # database to install. docker-compose.yml overrides this with PostgreSQL.
    # SQLite is the right choice for a pilot of this size -- a single writer,
    # a few thousand rows a day -- and it is what the test suite runs against.
    database_url: str = "sqlite:///./floodwatch.db"

    # JWT. The client holds a 30-day access token and cannot refresh (brief 5.6).
    jwt_secret: str = "change-me-in-production-this-is-not-a-secret"
    jwt_algorithm: str = "HS256"
    jwt_ttl_days: int = 30

    # Admin dashboard. A single shared token, checked on X-Admin-Token.
    # A prototype for a team of three does not need a second user system.
    admin_token: str = "dev-admin-token"

    # --- region / aggregation (see DESIGN.md sec 4) ---------------------
    geohash_precision: int = 5           # ~4.9 km cell
    crowd_window_minutes: int = 60       # "recent" answers
    crowd_min_respondents: int = 5       # floor before the ratio means anything
    crowd_yes_ratio: float = 0.75        # the brief's >=75%

    # --- risk scoring ---------------------------------------------------
    gauge_search_radius_km: float = 25.0
    weight_gauge: float = 0.45
    weight_rainfall: float = 0.35
    weight_crowd: float = 0.20
    score_low: float = 0.30
    score_moderate: float = 0.50
    score_high: float = 0.75

    # --- ingestion ------------------------------------------------------
    arcgis_base: str = "https://services3.arcgis.com/J7ZFXmR8rSmQ3FGf/arcgis/rest/services"
    open_meteo_base: str = "https://api.open-meteo.com/v1/forecast"
    # The Irrigation Department's `rain_fall` field is not documented anywhere.
    # Evidence for treating it as INCREMENTAL (rainfall since the previous
    # reading) rather than a 24-hour running total: across the full ~6,400-row
    # history the maximum value is 32.4 mm, which is a plausible hourly burst but
    # implausibly low for a monsoon daily total. Readings arrive roughly every
    # 45-90 minutes per station.
    #
    # This is an INFERENCE, not a fact, and it is the single most consequential
    # unverified assumption in the pipeline: if the field is really cumulative,
    # summing it inflates 24-hour rainfall by an order of magnitude and the
    # system will cry wolf. CONFIRM WITH THE IRRIGATION DEPARTMENT, then set
    # this flag accordingly and say which it is in the paper's methods section.
    gauge_rainfall_is_cumulative: bool = False

    ingest_enabled: bool = True
    ingest_gauges_minutes: int = 10
    ingest_rainfall_minutes: int = 60
    risk_evaluate_minutes: int = 10
    upstream_timeout_seconds: float = 20.0

    # --- retention (see DESIGN.md sec 7) --------------------------------
    retention_locations_days: int = 90
    retention_feedback_days: int = 730
    retention_readings_days: int = 365

    # --- abuse control --------------------------------------------------
    # Backpressure returns 503, never 429 -- see brief sec 5.3.
    locations_max_per_hour: int = 120
    feedback_max_per_hour: int = 12

    # --- push -----------------------------------------------------------
    # Absent credentials disable push rather than blocking startup, mirroring
    # the guarded Firebase.initializeApp() in the client.
    firebase_credentials_file: str | None = None

    # --- consent --------------------------------------------------------
    consent_notice_version: str = "2026-08-v1"


settings = Settings()
