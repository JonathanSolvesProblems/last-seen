"""Task 5, Follow: keep the confirmed target in frame from a standoff distance.

The follower does not chase pixels. It converts the detection's feet pixel to
a ground position with the camera model, smooths it, and flies to a standoff
point behind the person (relative to the drone) at the follow altitude,
yawing to face them. The gimbal pitch is chosen so the person sits around
60% down the frame at the standoff distance.

If the target is lost, the drone holds position and yaws slowly toward the
last known ground position; after `lost_timeout_s` the mission goes back to
searching.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geo
from ..camera import CameraModel
from ..control import Limits, altitude_hold, limit, toward, yaw_toward
from ..detect import Detection
from ..protocol import Telemetry, VelocityCommand


@dataclass
class FollowConfig:
    standoff_m: float = 12.0          # horizontal distance to keep
    altitude_m: float = 15.0
    speed: float = 4.0
    lost_timeout_s: float = 8.0
    ema: float = 0.4                  # smoothing of the target ground estimate
    frame_fraction_v: float = 0.6     # where the target should sit vertically
    limits: Limits = field(default_factory=lambda: Limits(max_horizontal=5.0, max_yaw_rate=60.0))


@dataclass
class FollowStats:
    frames: int = 0
    frames_with_target: int = 0
    dist_samples: list[float] = field(default_factory=list)

    def in_frame_fraction(self) -> float:
        return self.frames_with_target / self.frames if self.frames else 0.0

    def mean_distance(self) -> float | None:
        return sum(self.dist_samples) / len(self.dist_samples) if self.dist_samples else None


class Follower:
    def __init__(self, cam: CameraModel, origin: geo.Origin, cfg: FollowConfig | None = None):
        self.cam = cam
        self.origin = origin
        self.cfg = cfg or FollowConfig()
        self.target_e: float | None = None
        self.target_n: float | None = None
        self.last_seen_t: float | None = None
        self.stats = FollowStats()

    def reset(self) -> None:
        self.target_e = self.target_n = None
        self.last_seen_t = None
        self.stats = FollowStats()

    def desired_gimbal_pitch(self) -> float:
        """Pitch that puts a point standoff_m away, altitude_m below, at frame_fraction_v."""
        angle_to_target = math.degrees(math.atan2(self.cfg.altitude_m, self.cfg.standoff_m))
        # offset from the optical axis for the desired row
        row_off = (self.cfg.frame_fraction_v - 0.5) * self.cam.height
        off_deg = math.degrees(math.atan(row_off / self.cam.focal_px))
        pitch = -(angle_to_target - off_deg)
        return max(-90.0, min(-20.0, pitch))

    def observe(self, det: Detection | None, tel: Telemetry, frame_w: int, frame_h: int,
                gimbal_pitch: float, t: float) -> None:
        self.stats.frames += 1
        if det is None:
            return
        cam = self.cam if (self.cam.width, self.cam.height) == (frame_w, frame_h) else self.cam.scaled(frame_w, frame_h)
        e, n = self.origin.to_local(tel.lat, tel.lon)
        u, v = det.feet
        g = cam.pixel_to_ground(u, v, e, n, tel.alt, gimbal_pitch, tel.heading)
        if g is None:
            return
        ge, gn = g
        # sanity: ignore ground points absurdly far away (near-horizon rays)
        if math.hypot(ge - e, gn - n) > 6 * max(tel.alt, 1.0) + 40:
            return
        self.stats.frames_with_target += 1
        if self.target_e is None:
            self.target_e, self.target_n = ge, gn
        else:
            a = self.cfg.ema
            self.target_e = a * ge + (1 - a) * self.target_e
            self.target_n = a * gn + (1 - a) * self.target_n
        self.last_seen_t = t
        self.stats.dist_samples.append(math.hypot(self.target_e - e, self.target_n - n))

    def lost(self, t: float) -> bool:
        return self.last_seen_t is None or (t - self.last_seen_t) > self.cfg.lost_timeout_s

    def step(self, tel: Telemetry, t: float) -> VelocityCommand:
        e, n = self.origin.to_local(tel.lat, tel.lon)
        vu = altitude_hold(self.cfg.altitude_m, tel.alt)
        if self.target_e is None or self.target_n is None:
            return VelocityCommand(0, 0, vu, 0)
        bearing = geo.bearing_deg(e, n, self.target_e, self.target_n)
        yaw = yaw_toward(bearing, tel.heading, kp=1.5, max_rate=self.cfg.limits.max_yaw_rate)
        if self.last_seen_t is not None and (t - self.last_seen_t) > 1.5:
            # Recently lost: hold position, keep turning toward last estimate
            return limit(VelocityCommand(0, 0, vu, yaw), self.cfg.limits)
        # standoff point: on the line from target to drone, standoff_m from target
        d = math.hypot(self.target_e - e, self.target_n - n)
        if d < 1e-3:
            return limit(VelocityCommand(0, 0, vu, yaw), self.cfg.limits)
        ux, uy = (e - self.target_e) / d, (n - self.target_n) / d
        se = self.target_e + ux * self.cfg.standoff_m
        sn = self.target_n + uy * self.cfg.standoff_m
        vn, ve, _ = toward(se, sn, e, n, self.cfg.speed, slow_radius=5.0, stop_radius=1.0)
        return limit(VelocityCommand(vn, ve, vu, yaw), self.cfg.limits)
