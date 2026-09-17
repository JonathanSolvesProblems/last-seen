"""Controllers: PID, velocity limiting and body/ground frame conversion."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .protocol import VelocityCommand


@dataclass
class PID:
    kp: float
    ki: float = 0.0
    kd: float = 0.0
    out_limit: float = float("inf")
    i_limit: float = float("inf")
    _integral: float = field(default=0.0, init=False, repr=False)
    _prev_err: float | None = field(default=None, init=False, repr=False)

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_err = None

    def step(self, error: float, dt: float) -> float:
        if dt <= 0:
            dt = 1e-3
        self._integral += error * dt
        self._integral = max(-self.i_limit, min(self.i_limit, self._integral))
        d = 0.0 if self._prev_err is None else (error - self._prev_err) / dt
        self._prev_err = error
        out = self.kp * error + self.ki * self._integral + self.kd * d
        return max(-self.out_limit, min(self.out_limit, out))


@dataclass
class Limits:
    max_horizontal: float = 4.0     # m/s
    max_vertical: float = 1.5       # m/s
    max_yaw_rate: float = 45.0      # deg/s


def limit(cmd: VelocityCommand, lim: Limits) -> VelocityCommand:
    h = math.hypot(cmd.vn, cmd.ve)
    if h > lim.max_horizontal and h > 0:
        s = lim.max_horizontal / h
        cmd = VelocityCommand(cmd.vn * s, cmd.ve * s, cmd.vu, cmd.yaw_rate)
    vu = max(-lim.max_vertical, min(lim.max_vertical, cmd.vu))
    yr = max(-lim.max_yaw_rate, min(lim.max_yaw_rate, cmd.yaw_rate))
    return VelocityCommand(cmd.vn, cmd.ve, vu, yr)


def body_to_ground(forward: float, right: float, heading_deg: float) -> tuple[float, float]:
    """Convert a body-frame (forward, right) velocity to (north, east)."""
    psi = math.radians(heading_deg)
    vn = forward * math.cos(psi) - right * math.sin(psi)
    ve = forward * math.sin(psi) + right * math.cos(psi)
    return vn, ve


def ground_to_body(vn: float, ve: float, heading_deg: float) -> tuple[float, float]:
    psi = math.radians(heading_deg)
    forward = vn * math.cos(psi) + ve * math.sin(psi)
    right = -vn * math.sin(psi) + ve * math.cos(psi)
    return forward, right


def toward(target_e: float, target_n: float, e: float, n: float, speed: float,
           slow_radius: float = 4.0, stop_radius: float = 0.5) -> tuple[float, float, float]:
    """Proportional velocity toward a point. Returns (vn, ve, distance)."""
    de = target_e - e
    dn = target_n - n
    d = math.hypot(de, dn)
    if d < stop_radius:
        return 0.0, 0.0, d
    v = speed if d > slow_radius else speed * max(0.25, d / slow_radius)
    return v * dn / d, v * de / d, d


def altitude_hold(target_alt: float, alt: float, kp: float = 0.8, max_v: float = 1.5) -> float:
    return max(-max_v, min(max_v, kp * (target_alt - alt)))


def yaw_toward(target_heading: float, heading: float, kp: float = 1.2,
               max_rate: float = 45.0) -> float:
    err = (target_heading - heading + 180.0) % 360.0 - 180.0
    return max(-max_rate, min(max_rate, kp * err))
