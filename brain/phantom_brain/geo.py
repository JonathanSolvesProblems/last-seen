"""Small geodesy helpers: lat/lon <-> local east/north metres, bearings, polygons.

Everything the brain plans in is a local tangent plane (ENU) anchored at the
mission origin (normally the takeoff point). Fields are a few hundred metres
across, so an equirectangular projection is accurate to centimetres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class Origin:
    lat: float
    lon: float

    @property
    def _cos_lat(self) -> float:
        return math.cos(math.radians(self.lat))

    def to_local(self, lat: float, lon: float) -> tuple[float, float]:
        """Return (east, north) metres of a lat/lon relative to the origin."""
        d_lat = math.radians(lat - self.lat)
        d_lon = math.radians(lon - self.lon)
        north = d_lat * EARTH_RADIUS_M
        east = d_lon * EARTH_RADIUS_M * self._cos_lat
        return east, north

    def to_latlon(self, east: float, north: float) -> tuple[float, float]:
        lat = self.lat + math.degrees(north / EARTH_RADIUS_M)
        lon = self.lon + math.degrees(east / (EARTH_RADIUS_M * self._cos_lat))
        return lat, lon


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(e_from: float, n_from: float, e_to: float, n_to: float) -> float:
    """Compass bearing (0 = north, clockwise) from one ENU point to another."""
    return (math.degrees(math.atan2(e_to - e_from, n_to - n_from)) + 360.0) % 360.0


def wrap_deg(angle: float) -> float:
    """Wrap an angle to (-180, 180]."""
    a = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point_in_polygon(x: float, y: float, poly: Sequence[Sequence[float]]) -> bool:
    """Ray casting test. poly is a list of (x, y) vertices, open or closed."""
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def polygon_bbox(poly: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def dist_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    ab2 = abx * abx + aby * aby
    if ab2 == 0:
        return math.hypot(apx, apy)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx, cy = ax + t * abx, ay + t * aby
    return math.hypot(px - cx, py - cy)


def dist_to_polygon_edge(x: float, y: float, poly: Sequence[Sequence[float]]) -> float:
    n = len(poly)
    best = float("inf")
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        best = min(best, dist_to_segment(x, y, ax, ay, bx, by))
    return best


def polygon_area(poly: Sequence[Sequence[float]]) -> float:
    n = len(poly)
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def polygon_centroid(poly: Sequence[Sequence[float]]) -> tuple[float, float]:
    n = len(poly)
    a = 0.0
    cx = cy = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if a == 0:
        return sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n
    a *= 0.5
    return cx / (6 * a), cy / (6 * a)
