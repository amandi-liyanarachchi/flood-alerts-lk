"""Fetch layer for the dataset builder.

Every network call in the pipeline goes through this file, and every response is
cached to disk as raw JSON. Two reasons:

  1. Reproducibility. The paper has to be able to say which snapshot of a live
     public service the results came from. `data/raw/` IS that snapshot -- keep
     it, and `--offline` rebuilds the identical dataset from it forever.
  2. Politeness. This is someone else's infrastructure. A cached re-run costs
     them nothing.

Sources
-------
Irrigation Department, ArcGIS Online (anonymous, no key):
    hydrostations/FeatureServer/0   station master + official flood levels
    gauges_2_view/FeatureServer/0   water level + rainfall readings
    Flood_Map/FeatureServer/*       ~17 named historical flood events, mapped to
                                    the Grama Niladhari divisions they affected

Open-Meteo ERA5 archive (free, no key, hourly, back to 1940):
    archive-api.open-meteo.com/v1/archive

NOTE ON GAUGE HISTORY DEPTH. `gauges_2_view` appears to be a ROLLING window
rather than a deep archive -- around 6,400 rows across all stations, spanning
roughly three to four days. The builder measures the actual span at runtime and
reports it, so you never have to take that on trust. The consequence matters:
you cannot build a multi-year gauge dataset from one run. You accumulate it.
`data/raw/gauge_readings_archive.json` is append-only across runs, and the
backend's own scheduler is already storing every reading it sees, so the useful
move is to start collecting now and re-run this weekly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger("dataset.sources")

ARCGIS = "https://services3.arcgis.com/J7ZFXmR8rSmQ3FGf/arcgis/rest/services"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

FEET_TO_METRES = 0.3048
TIMEOUT = 60.0
RETRIES = 3


class Sources:
    """All outbound calls. Swap this class out in tests."""

    def __init__(self, cache_dir: Path, offline: bool = False, pause: float = 0.4):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.pause = pause
        self._client: httpx.Client | None = None
        self.calls = 0
        self.cache_hits = 0

    # -- plumbing --------------------------------------------------------

    def _cache_path(self, key: str) -> Path:
        safe = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{safe}.json"

    def _get_json(self, url: str, params: dict, cache_key: str) -> dict:
        path = self._cache_path(cache_key)
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text())

        if self.offline:
            raise RuntimeError(
                f"--offline was requested but there is no cached response for {cache_key}.\n"
                f"Run once without --offline to populate {self.cache_dir}."
            )

        if self._client is None:
            self._client = httpx.Client(
                timeout=TIMEOUT, headers={"User-Agent": "floodwatch-lk-research/1.0"}
            )

        last_error: Exception | None = None
        for attempt in range(RETRIES):
            try:
                response = self._client.get(url, params=params)
                response.raise_for_status()
                body = response.json()
                if isinstance(body, dict) and "error" in body:
                    # ArcGIS returns HTTP 200 with an error object in the body.
                    raise RuntimeError(f"service error: {body['error']}")
                path.write_text(json.dumps(body))
                self.calls += 1
                time.sleep(self.pause)
                return body
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                wait = 2 ** attempt
                log.warning("  retry %d/%d in %ds (%s)", attempt + 1, RETRIES, wait, exc)
                time.sleep(wait)

        raise RuntimeError(f"failed after {RETRIES} attempts: {cache_key}: {last_error}")

    def _arcgis_query(self, service: str, layer: int, params: dict, tag: str) -> list[dict]:
        url = f"{ARCGIS}/{service}/FeatureServer/{layer}/query"
        merged = {"f": "json", "outSR": 4326, **params}
        body = self._get_json(url, merged, f"{service}_{layer}_{tag}")
        return body.get("features", [])

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # -- stations --------------------------------------------------------

    def stations(self) -> list[dict]:
        """Station master list, with every level converted to metres.

        Some stations publish in feet (Nagalagam Street on the Kelani). Mixing
        the two would put central Colombo permanently in flood, so the
        conversion happens here, once, at the boundary.
        """
        features = self._arcgis_query(
            "hydrostations", 0,
            {"where": "1=1", "outFields": "*", "returnGeometry": "true"},
            "all",
        )

        out = []
        for feature in features:
            attrs = feature.get("attributes", {}) or {}
            geom = feature.get("geometry", {}) or {}
            name = (attrs.get("station") or "").strip()
            lat = attrs.get("latitude") or geom.get("y")
            lon = attrs.get("longitude") or geom.get("x")
            if not name or lat is None or lon is None:
                continue

            unit = (attrs.get("Unit") or "m").strip().lower()
            factor = FEET_TO_METRES if unit.startswith("ft") else 1.0

            def metres(value):
                return None if value is None else round(float(value) * factor, 4)

            out.append({
                "station": name,
                "basin": attrs.get("basin"),
                "tributary": attrs.get("Tributory"),      # upstream spelling
                "latitude": float(lat),
                "longitude": float(lon),
                "alert_level_m": metres(attrs.get("Alert_Level")),
                "minor_flood_level_m": metres(attrs.get("Minor_Flood_Level")),
                "major_flood_level_m": metres(attrs.get("Major_Flood_Level")),
                "elevation_m": attrs.get("Elivation_m_MSL"),  # upstream spelling
                "source_unit": "ft" if factor != 1.0 else "m",
            })
        return out

    # -- readings --------------------------------------------------------

    def readings(self, max_pages: int = 12, page_size: int = 1000) -> list[dict]:
        """Every reading the rolling view currently holds, newest first."""
        rows: list[dict] = []
        for page in range(max_pages):
            features = self._arcgis_query(
                "gauges_2_view", 0,
                {
                    "where": "1=1",
                    "outFields": "basin,gauge,water_level,rain_fall,CreationDate",
                    "orderByFields": "CreationDate DESC",
                    "resultRecordCount": page_size,
                    "resultOffset": page * page_size,
                    "returnGeometry": "true",
                },
                f"page{page}",
            )
            for feature in features:
                attrs = feature.get("attributes", {}) or {}
                geom = feature.get("geometry", {}) or {}
                epoch = attrs.get("CreationDate")
                gauge = (attrs.get("gauge") or "").strip()
                if not gauge or epoch is None:
                    continue
                try:
                    observed = datetime.fromtimestamp(
                        float(epoch) / 1000.0, tz=timezone.utc
                    ).replace(tzinfo=None)
                except (TypeError, ValueError, OSError, OverflowError):
                    continue
                rows.append({
                    "station": gauge,
                    "basin": attrs.get("basin"),
                    "observed_at": observed.isoformat(),
                    "water_level_raw": attrs.get("water_level"),
                    "rainfall_mm": attrs.get("rain_fall"),
                    "latitude": geom.get("y"),
                    "longitude": geom.get("x"),
                })
            if len(features) < page_size:
                break
        return rows

    # -- flood event catalogue -------------------------------------------

    def flood_catalogue(self) -> list[dict]:
        """The ~17 named historical events, by affected GN division.

        Flood_Map is not one map: it is a stack of layers, in repeating groups,
        each group being one named event. The GN-level layers carry a
        `Flooding_area` field holding the event name, e.g. "Yan Oya Flood 2016".
        """
        service_root = self._get_json(
            f"{ARCGIS}/Flood_Map/FeatureServer", {"f": "json"}, "Flood_Map_root"
        )
        layers = service_root.get("layers", []) or []

        rows: list[dict] = []
        for layer in layers:
            name = (layer.get("name") or "")
            # GN-level layers are the finest resolution and carry the event name.
            if "GND" not in name.upper():
                continue
            layer_id = layer["id"]
            try:
                features = self._arcgis_query(
                    "Flood_Map", layer_id,
                    {
                        "where": "1=1",
                        "outFields": "*",
                        "returnGeometry": "false",
                        "returnCentroid": "true",
                    },
                    f"catalogue_{layer_id}",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("  layer %s (%s) unreadable: %s", layer_id, name, exc)
                continue

            for feature in features:
                attrs = feature.get("attributes", {}) or {}
                centroid = feature.get("centroid", {}) or {}
                event = (attrs.get("Flooding_area") or "").strip()
                if not event:
                    continue
                rows.append({
                    "event_name": event,
                    "layer_id": layer_id,
                    "gnd_name": attrs.get("gnd_name"),
                    "gnd_no": attrs.get("gnd_no"),
                    "dsd_name": attrs.get("dsd_name"),
                    "district": attrs.get("district_n"),
                    "province": attrs.get("province_n"),
                    "admin_code": attrs.get("admin_code"),
                    "latitude": centroid.get("y"),
                    "longitude": centroid.get("x"),
                    "area_sqm": attrs.get("Shape__Area"),
                })
        return rows

    # -- rainfall --------------------------------------------------------

    def rainfall_hourly(self, latitude: float, longitude: float,
                        start: str, end: str) -> dict:
        """ERA5 reanalysis rainfall for one point, hourly, [start, end] as
        YYYY-MM-DD. Free, no key, and it goes back to 1940 -- which is why this
        is the one source that can give the project real multi-year features
        today, long before the gauge archive is deep enough."""
        lat, lon = round(float(latitude), 3), round(float(longitude), 3)
        return self._get_json(
            OPEN_METEO_ARCHIVE,
            {
                "latitude": lat,
                "longitude": lon,
                "start_date": start,
                "end_date": end,
                "hourly": "precipitation",
                "timezone": "UTC",
            },
            f"era5_{lat}_{lon}_{start}_{end}",
        )
