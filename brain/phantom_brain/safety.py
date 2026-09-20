"""Safety layer: geofence, altitude band, battery, link watchdog.

Every velocity command the autopilot produces passes through
SafetyMonitor.filter() before it is sent. The monitor can:
  * clamp the command so the drone never accelerates out of the fence,
  * force a climb when below the altitude floor over the field,
  * demand an abort (return to home / land) on low battery, stale telemetry
    or a fence breach that clamping cannot fix.

The Phantom 3 Standard has no obstacle sensing, so the altitude floor is the
only thing between the drone and a tree. It is configured, not measured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from . import geo
from .protocol import Telemetry, VelocityCommand


class Abort(Enum):
    NONE = "none"
    RETURN_HOME = "return_home"
    LAND_NOW = "land_now"


@dataclass
class Fence:
    polygon: Sequence[tuple[float, float]]   # ENU vertices, metres
    max_radius_m: float = 150.0               # from home, belt and braces
    max_alt_m: float = 60.0
    floor_alt_m: float = 15.0                 # never below this while away from home
    home_radius_m: float = 8.0                # inside this the floor does not apply
    margin_m: float = 5.0                     # start braking this far from the edge

    def inside(self, e: float, n: float) -> bool:
        if math.hypot(e, n) > self.max_radius_m:
            return False
        return geo.point_in_polygon(e, n, self.polygon)

    def edge_distance(self, e: float, n: float) -> float:
        return geo.dist_to_polygon_edge(e, n, self.polygon)


@dataclass
class SafetyConfig:
    battery_rth_percent: int = 30
    battery_land_percent: int = 15
    telemetry_stale_s: float = 2.0
    min_gps_sats: int = 7
    max_violations_before_rth: int = 20   # consecutive clamp events


@dataclass
class SafetyState:
    consecutive_clamps: int = 0
    violations: list[str] = field(default_factory=list)
    abort: Abort = Abort.NONE


class SafetyMonitor:
    def __init__(self, fence: Fence, origin: geo.Origin, config: SafetyConfig | None = None):
        self.fence = fence
        self.origin = origin
        self.config = config or SafetyConfig()
        self.state = SafetyState()

    def position(self, tel: Telemetry) -> tuple[float, float]:
        return self.origin.to_local(tel.lat, tel.lon)

    def check(self, tel: Telemetry, now: float, rx_age_s: float | None = None) -> Abort:
        """Non-command checks. Returns the strongest abort demanded.

        rx_age_s is how long ago the LAST telemetry message arrived, measured
        on the brain's own clock. (Comparing tel.t against itself can never
        detect a dead link, which is what an earlier version did.)
        """
        cfg = self.config
        abort = Abort.NONE
        age = rx_age_s if rx_age_s is not None else (now - tel.t)
        if age > cfg.telemetry_stale_s:
            self._note("telemetry_stale")
            abort = Abort.RETURN_HOME
        if tel.is_flying:
            if tel.battery <= cfg.battery_land_percent:
                self._note("battery_critical")
                abort = Abort.LAND_NOW
            elif tel.battery <= cfg.battery_rth_percent:
                self._note("battery_low")
                abort = max_abort(abort, Abort.RETURN_HOME)
            if tel.alt > self.fence.max_alt_m + 3.0:
                self._note("altitude_ceiling")
                abort = max_abort(abort, Abort.RETURN_HOME)
            e, n = self.position(tel)
            if not self.fence.inside(e, n):
                self._note("fence_breach")
                abort = max_abort(abort, Abort.RETURN_HOME)
        if self.state.consecutive_clamps > cfg.max_violations_before_rth:
            self._note("repeated_clamps")
            abort = max_abort(abort, Abort.RETURN_HOME)
        self.state.abort = max_abort(self.state.abort, abort)
        return self.state.abort

    def filter(self, cmd: VelocityCommand, tel: Telemetry) -> VelocityCommand:
        """Clamp a command so it cannot push the drone out of the safe volume."""
        e, n = self.position(tel)
        vn, ve, vu, yr = cmd.vn, cmd.ve, cmd.vu, cmd.yaw_rate
        clamped = False

        # Altitude ceiling
        if tel.alt >= self.fence.max_alt_m and vu > 0:
            vu = 0.0
            clamped = True
        # Altitude floor away from home: force a climb
        away_from_home = math.hypot(e, n) > self.fence.home_radius_m
        if tel.is_flying and away_from_home and tel.alt < self.fence.floor_alt_m:
            vu = max(vu, 1.0)
            clamped = True

        # Fence: soft brake near the edge, hard stop of the outward component at it
        edge = self.fence.edge_distance(e, n)
        inside = self.fence.inside(e, n)
        if inside and edge < self.fence.margin_m:
            # Predict one second ahead using both the command and the drone's
            # actual velocity (momentum). If either leaves the fence, strip the
            # outward component and actively brake against current motion.
            leaves_cmd = not self.fence.inside(e + ve, n + vn)
            leaves_now = not self.fence.inside(e + tel.ve, n + tel.vn)
            if leaves_cmd or leaves_now:
                vn, ve = self._remove_outward(vn, ve, e, n)
                bn, be = self._remove_outward(tel.vn, tel.ve, e, n)
                # (tel - b) is the outward part of the current velocity; oppose it
                vn -= (tel.vn - bn)
                ve -= (tel.ve - be)
                clamped = True
            else:
                s = max(0.2, edge / self.fence.margin_m)
                vn, ve = vn * s, ve * s
        elif not inside:
            # Already outside: only allow motion back toward the centroid
            ce, cn = geo.polygon_centroid(self.fence.polygon)
            d = math.hypot(ce - e, cn - n) or 1.0
            vn, ve = 2.0 * (cn - n) / d, 2.0 * (ce - e) / d
            clamped = True

        self.state.consecutive_clamps = self.state.consecutive_clamps + 1 if clamped else 0
        return VelocityCommand(vn, ve, vu, yr)

    def _remove_outward(self, vn: float, ve: float, e: float, n: float) -> tuple[float, float]:
        ce, cn = geo.polygon_centroid(self.fence.polygon)
        ie, in_ = ce - e, cn - n
        d = math.hypot(ie, in_)
        if d == 0:
            return 0.0, 0.0
        ie, in_ = ie / d, in_ / d
        inward = vn * in_ + ve * ie
        if inward >= 0:
            return vn, ve
        # subtract the outward projection
        return vn - inward * in_, ve - inward * ie

    def _note(self, name: str) -> None:
        if not self.state.violations or self.state.violations[-1] != name:
            self.state.violations.append(name)

    def reset_abort(self) -> None:
        self.state.abort = Abort.NONE


_ORDER = {Abort.NONE: 0, Abort.RETURN_HOME: 1, Abort.LAND_NOW: 2}


def max_abort(a: Abort, b: Abort) -> Abort:
    return a if _ORDER[a] >= _ORDER[b] else b
