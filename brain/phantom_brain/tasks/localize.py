"""Task 2, Localize: where is the drone, from the camera alone?

Outdoors the Phantom has GPS, so this task is honest about being a check on
GPS rather than a replacement for it. During the survey the brain stores
nadir keyframes with their GPS position. Later, a nadir frame is matched
(ORB features + homography) against the nearest keyframes and the pixel
offset is converted to metres with the ground sample distance. The score is
the error against GPS over many frames, in metres.

This is the same "match the live frame against reference footage" idea as
Drone-Bench's localize step, in 2D.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..camera import CameraModel


@dataclass
class Keyframe:
    e: float
    n: float
    alt: float
    heading: float
    gray: np.ndarray
    kps: list
    des: np.ndarray | None


@dataclass
class LocalizeEstimate:
    e: float
    n: float
    inliers: int
    keyframe_index: int
    error_vs_gps_m: float | None = None


@dataclass
class LocalizeStats:
    estimates: list[LocalizeEstimate] = field(default_factory=list)

    def median_error_m(self) -> float | None:
        errs = [x.error_vs_gps_m for x in self.estimates if x.error_vs_gps_m is not None]
        return float(np.median(errs)) if errs else None

    def success_rate(self, within_m: float = 5.0) -> float | None:
        errs = [x.error_vs_gps_m for x in self.estimates if x.error_vs_gps_m is not None]
        if not errs:
            return None
        return float(np.mean([e <= within_m for e in errs]))


class VisualLocalizer:
    def __init__(self, cam: CameraModel, work_width: int = 640, n_features: int = 1500,
                 min_keyframe_spacing_m: float = 8.0):
        self.cam = cam
        self.work_width = work_width
        self.orb = cv2.ORB_create(nfeatures=n_features)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        self.keyframes: list[Keyframe] = []
        self.min_spacing = min_keyframe_spacing_m
        self.stats = LocalizeStats()

    def _prep(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, float]:
        h, w = frame_bgr.shape[:2]
        s = self.work_width / w
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if s < 1:
            gray = cv2.resize(gray, (self.work_width, int(h * s)))
        else:
            s = 1.0
        return gray, s

    def maybe_add_keyframe(self, frame_bgr: np.ndarray, e: float, n: float, alt: float,
                           heading: float, gimbal_pitch: float) -> bool:
        if gimbal_pitch > -80:      # keyframes must be near-nadir
            return False
        for k in self.keyframes:
            if math.hypot(k.e - e, k.n - n) < self.min_spacing:
                return False
        gray, _ = self._prep(frame_bgr)
        kps, des = self.orb.detectAndCompute(gray, None)
        if des is None or len(kps) < 30:
            return False
        self.keyframes.append(Keyframe(e, n, alt, heading, gray, kps, des))
        return True

    def estimate(self, frame_bgr: np.ndarray, alt: float, heading: float, gimbal_pitch: float,
                 gps_e: float | None = None, gps_n: float | None = None,
                 search_radius_m: float = 40.0) -> LocalizeEstimate | None:
        if not self.keyframes or gimbal_pitch > -80:
            return None
        gray, s = self._prep(frame_bgr)
        kps, des = self.orb.detectAndCompute(gray, None)
        if des is None or len(kps) < 30:
            return None
        # candidate keyframes: near the GPS prior if we have one, else all
        cands = list(enumerate(self.keyframes))
        if gps_e is not None and gps_n is not None:
            cands = [(i, k) for i, k in cands
                     if math.hypot(k.e - gps_e, k.n - gps_n) <= search_radius_m] or cands
        best: LocalizeEstimate | None = None
        for i, k in cands:
            matches = self.bf.knnMatch(des, k.des, k=2)
            good = [m for m, nn in (p for p in matches if len(p) == 2) if m.distance < 0.75 * nn.distance]
            if len(good) < 12:
                continue
            src = np.float32([kps[m.queryIdx].pt for m in good])
            dst = np.float32([k.kps[m.trainIdx].pt for m in good])
            H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
            if H is None:
                continue
            inliers = int(mask.sum())
            if inliers < 10:
                continue
            # Where does the live image centre land in the keyframe?
            cx, cy = gray.shape[1] / 2.0, gray.shape[0] / 2.0
            p = cv2.perspectiveTransform(np.float32([[[cx, cy]]]), H)[0][0]
            du = (p[0] - k.gray.shape[1] / 2.0) / s     # in full-res pixels
            dv = (p[1] - k.gray.shape[0] / 2.0) / s
            gsd = self.cam.nadir_gsd(k.alt)
            # image up = keyframe heading direction, image right = heading + 90
            fwd = -dv * gsd
            right = du * gsd
            psi = math.radians(k.heading)
            de = fwd * math.sin(psi) + right * math.cos(psi)
            dn = fwd * math.cos(psi) - right * math.sin(psi)
            est = LocalizeEstimate(k.e + de, k.n + dn, inliers, i)
            if best is None or inliers > best.inliers:
                best = est
        if best is not None:
            if gps_e is not None and gps_n is not None:
                best.error_vs_gps_m = math.hypot(best.e - gps_e, best.n - gps_n)
            self.stats.estimates.append(best)
        return best
