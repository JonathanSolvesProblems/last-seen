"""Pinhole camera model for the Phantom 3 Standard gimbal camera.

Conventions
    Image: u right, v down, origin top-left.
    Gimbal pitch: 0 = horizon, -90 = straight down (DJI convention).
    Heading: compass degrees, 0 = north, clockwise. The P3 gimbal yaw follows
    the aircraft, so camera yaw == aircraft heading.
    Ground: flat plane at altitude 0 (the takeoff point).

The P3 Standard camera is a 94 degree diagonal FOV (20 mm equivalent) lens.
The live feed the bridge streams is 16:9, so the intrinsics are derived from
the diagonal FOV and the frame size the bridge actually sends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

P3S_DIAGONAL_FOV_DEG = 94.0


@dataclass
class CameraModel:
    width: int
    height: int
    diagonal_fov_deg: float = P3S_DIAGONAL_FOV_DEG

    @property
    def focal_px(self) -> float:
        diag = math.hypot(self.width, self.height)
        return (diag / 2.0) / math.tan(math.radians(self.diagonal_fov_deg / 2.0))

    @property
    def cx(self) -> float:
        return self.width / 2.0

    @property
    def cy(self) -> float:
        return self.height / 2.0

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2 * math.atan((self.width / 2.0) / self.focal_px))

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2 * math.atan((self.height / 2.0) / self.focal_px))

    def scaled(self, width: int, height: int) -> "CameraModel":
        return CameraModel(width, height, self.diagonal_fov_deg)

    # ---- rays -------------------------------------------------------------

    def pixel_to_ray_enu(self, u: float, v: float, gimbal_pitch_deg: float,
                         heading_deg: float) -> tuple[float, float, float]:
        """Unit-free direction (dE, dN, dU) of the ray through pixel (u, v)."""
        f = self.focal_px
        x = (u - self.cx) / f      # camera right
        y = (v - self.cy) / f      # camera down
        z = 1.0                    # camera forward (optical axis)
        theta = math.radians(-gimbal_pitch_deg)   # positive = looking down
        ct, st = math.cos(theta), math.sin(theta)
        # camera -> FRD (forward, right, down) of a level aircraft
        fwd = ct * z - st * y
        right = x
        down = st * z + ct * y
        psi = math.radians(heading_deg)
        cp, sp = math.cos(psi), math.sin(psi)
        north = fwd * cp - right * sp
        east = fwd * sp + right * cp
        up = -down
        return east, north, up

    def pixel_to_ground(self, u: float, v: float, drone_e: float, drone_n: float,
                        altitude_m: float, gimbal_pitch_deg: float,
                        heading_deg: float) -> tuple[float, float] | None:
        """Ground point (east, north) seen at pixel (u, v), or None if above the horizon."""
        de, dn, du = self.pixel_to_ray_enu(u, v, gimbal_pitch_deg, heading_deg)
        if du >= -1e-6 or altitude_m <= 0:
            return None
        t = altitude_m / (-du)
        return drone_e + t * de, drone_n + t * dn

    def ground_to_pixel(self, ge: float, gn: float, drone_e: float, drone_n: float,
                        altitude_m: float, gimbal_pitch_deg: float,
                        heading_deg: float, point_height_m: float = 0.0
                        ) -> tuple[float, float] | None:
        """Project a ground point into the image. None if behind the camera."""
        east = ge - drone_e
        north = gn - drone_n
        up = point_height_m - altitude_m
        psi = math.radians(heading_deg)
        cp, sp = math.cos(psi), math.sin(psi)
        fwd = north * cp + east * sp
        right = -north * sp + east * cp
        down = -up
        theta = math.radians(-gimbal_pitch_deg)
        ct, st = math.cos(theta), math.sin(theta)
        # inverse of the FRD rotation used in pixel_to_ray_enu
        z = ct * fwd + st * down
        y = -st * fwd + ct * down
        x = right
        if z <= 1e-6:
            return None
        f = self.focal_px
        return self.cx + f * x / z, self.cy + f * y / z

    def nadir_gsd(self, altitude_m: float) -> float:
        """Metres per pixel at the image centre when looking straight down."""
        return altitude_m / self.focal_px

    def horizon_in_frame(self, gimbal_pitch_deg: float) -> bool:
        return -gimbal_pitch_deg < self.vfov_deg / 2.0


P3S_LIVE_FEED = CameraModel(1280, 720)
