"""Geohash and distance helpers.

Geohash is implemented here rather than pulled in as a dependency: it is thirty
lines, it never changes, and the region definition is the single most important
research parameter in the system. It should be readable.

Cell sizes at Sri Lanka's latitude (~7 N):
    precision 4  ~ 39 km   x 20 km
    precision 5  ~ 4.9 km  x 4.9 km   <- default, see DESIGN.md sec 4
    precision 6  ~ 1.2 km  x 0.6 km
"""

from __future__ import annotations

import math

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

# Rough bounding box for Sri Lanka, used to reject obviously bogus coordinates
# before they pollute a region bucket.
LK_LAT_MIN, LK_LAT_MAX = 5.5, 10.2
LK_LON_MIN, LK_LON_MAX = 79.3, 82.2

EARTH_RADIUS_KM = 6371.0088


def encode(latitude: float, longitude: float, precision: int = 5) -> str:
    """Encode a coordinate to a geohash of the given precision."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out: list[str] = []
    bits = 0
    bit_count = 0
    even = True  # even bits split longitude

    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if longitude > mid:
                bits = (bits << 1) | 1
                lon_lo = mid
            else:
                bits <<= 1
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if latitude > mid:
                bits = (bits << 1) | 1
                lat_lo = mid
            else:
                bits <<= 1
                lat_hi = mid
        even = not even
        bit_count += 1
        if bit_count == 5:
            out.append(_BASE32[bits])
            bits = 0
            bit_count = 0

    return "".join(out)


def decode_center(geohash: str) -> tuple[float, float]:
    """Return the (latitude, longitude) of a geohash cell's centre."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    even = True

    for char in geohash:
        idx = _BASE32.index(char)
        for shift in (4, 3, 2, 1, 0):
            bit = (idx >> shift) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                if bit:
                    lon_lo = mid
                else:
                    lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2
                if bit:
                    lat_lo = mid
                else:
                    lat_hi = mid
            even = not even

    return (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def is_plausible_lk_coordinate(latitude: float, longitude: float) -> bool:
    """True if the point falls inside Sri Lanka's bounding box.

    Used to *tag* a ping as out-of-area, never to reject it: rejecting would mean
    a 4xx, and a 4xx makes the client destroy the ping permanently (brief 5.3).
    A researcher testing from abroad should not silently lose data.
    """
    return LK_LAT_MIN <= latitude <= LK_LAT_MAX and LK_LON_MIN <= longitude <= LK_LON_MAX
