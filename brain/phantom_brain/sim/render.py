"""Render what the Phantom's camera would see over the simulated field.

The field is a pre-rendered top-down raster (grass texture, trees, people).
Because the ground is a plane, the camera image of it is a homography of the
raster, so one cv2.warpPerspective per frame gives a geometrically correct
oblique or nadir view. People are also reported as ground-truth pixel boxes
in the frame header for the oracle detector and for scoring.

The renderer refuses gimbal pitches that would put the horizon in frame,
which keeps the maths honest and matches how the mission flies anyway.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .. import geo
from ..camera import CameraModel
from ..config import MissionConfig
from .world import World

GRASS = np.array([62, 140, 72], dtype=np.float32)       # BGR
TREE = np.array([22, 62, 28], dtype=np.float32)          # canopy from above: dark green
PERSON_HEIGHT_M = 1.75
PERSON_FOOTPRINT_M = 0.7


@dataclass
class Raster:
    img: np.ndarray            # BGR
    min_e: float
    min_n: float
    res_m: float               # metres per pixel

    def to_px(self, e: float, n: float) -> tuple[float, float]:
        # row 0 is the NORTH edge (image up = north)
        x = (e - self.min_e) / self.res_m
        y = self.img.shape[0] - (n - self.min_n) / self.res_m
        return x, y


def build_raster(cfg: MissionConfig, pad_m: float = 80.0) -> Raster:
    poly = cfg.field.polygon
    x0, y0, x1, y1 = geo.polygon_bbox(poly)
    x0 -= pad_m
    y0 -= pad_m
    x1 += pad_m
    y1 += pad_m
    res = cfg.sim.raster_res_m
    w = int((x1 - x0) / res)
    h = int((y1 - y0) / res)
    rng = np.random.default_rng(cfg.sim.seed)
    # grass: base colour + low-frequency mottling + fine noise
    low = rng.normal(0, 1, (h // 16 + 1, w // 16 + 1)).astype(np.float32)
    low = cv2.resize(low, (w, h), interpolation=cv2.INTER_CUBIC)
    fine = rng.normal(0, 1, (h, w)).astype(np.float32)
    fine = cv2.GaussianBlur(fine, (0, 0), 1.2)
    img = np.empty((h, w, 3), dtype=np.float32)
    for c in range(3):
        img[..., c] = GRASS[c] + 9.0 * low + 7.0 * fine
    # a few mown stripes so ORB has texture to lock on to
    for k in range(0, w, max(8, int(6 / res))):
        img[:, k:k + max(2, int(3 / res)), :] += 6.0
    img = np.clip(img, 0, 255).astype(np.uint8)
    r = Raster(img, x0, y0, res)
    # outside the field: slightly different grass so the edge is visible
    mask = np.zeros((h, w), dtype=np.uint8)
    pts = np.array([r.to_px(e, n) for (e, n) in poly], dtype=np.int32)
    cv2.fillPoly(mask, [pts], 255)
    img[mask == 0] = (img[mask == 0] * 0.85).astype(np.uint8)
    # trees: dark disc, lighter rim, soft shadow to the north-west
    for (te, tn, tr, th) in cfg.sim.trees:
        cx, cy = r.to_px(te, tn)
        rad = int(tr / res)
        cv2.circle(img, (int(cx - rad * 0.3), int(cy + rad * 0.3)), int(rad * 1.05), (26, 52, 28), -1)
        cv2.circle(img, (int(cx), int(cy)), rad, tuple(int(v) for v in TREE), -1)
        cv2.circle(img, (int(cx), int(cy)), max(1, int(rad * 0.55)), (18, 50, 24), -1)
    return r


class Renderer:
    def __init__(self, cfg: MissionConfig, world: World, seed: int | None = None):
        self.cfg = cfg
        self.world = world
        # One seeded generator for sensor noise. It used to be created unseeded
        # per frame, which made every render different, so the same bench seed
        # produced a different flight each time and ORB-based tests were flaky.
        self.rng = np.random.default_rng(cfg.sim.seed if seed is None else seed)
        self.raster = build_raster(cfg)
        self.cam = CameraModel(cfg.sim.frame_w, cfg.sim.frame_h)
        self.w, self.h = cfg.sim.frame_w, cfg.sim.frame_h
        self._sky = np.full((self.h, self.w, 3), (200, 180, 150), dtype=np.uint8)

    def min_gimbal_pitch(self) -> float:
        return -(self.cam.vfov_deg / 2.0 + 3.0)

    def render(self) -> tuple[np.ndarray, dict[str, Any]]:
        d = self.world.drone
        pitch = min(d.gimbal_pitch, self.min_gimbal_pitch())
        truth: dict[str, Any] = {"people": [], "drone": {"e": d.e, "n": d.n, "alt": d.alt,
                                                          "heading": d.heading, "gimbal_pitch": pitch}}
        if d.alt < 0.5:
            # on the ground: camera sees grass close up
            img = np.full((self.h, self.w, 3), (70, 150, 80), dtype=np.uint8)
            return img, truth

        canvas = self.raster.img.copy()
        for p in self.world.people:
            if not p.cfg.sprite:            # a photographed person needs no disc
                self._draw_person(canvas, p.e, p.n, p.cfg.shirt_bgr)

        # homography raster px -> image px using 4 image pixels' ground points
        src_img = np.array([[self.w * 0.2, self.h * 0.25], [self.w * 0.8, self.h * 0.25],
                            [self.w * 0.8, self.h * 0.85], [self.w * 0.2, self.h * 0.85]], dtype=np.float32)
        src_raster = []
        for (u, v) in src_img:
            g = self.cam.pixel_to_ground(float(u), float(v), d.e, d.n, d.alt, pitch, d.heading)
            if g is None:
                return self._sky.copy(), truth
            src_raster.append(self.raster.to_px(*g))
        H = cv2.getPerspectiveTransform(np.array(src_raster, dtype=np.float32), src_img)
        img = cv2.warpPerspective(canvas, H, (self.w, self.h), flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_CONSTANT, borderValue=(90, 110, 90))
        self._draw_markers(img, d, pitch)
        # slight blur + sensor noise, like a 720p WiFi feed
        img = cv2.GaussianBlur(img, (0, 0), 0.6)
        noise = self.rng.normal(0, 2.5, img.shape).astype(np.float32)
        img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        for p in self.world.people:
            bbox, visible = self._person_bbox(p.e, p.n, d.e, d.n, d.alt, pitch, d.heading)
            if visible and p.cfg.sprite:
                self._paste_sprite(img, p.cfg.sprite, bbox)
            truth["people"].append({"id": p.cfg.id, "bbox": bbox, "visible": visible,
                                    "e": p.e, "n": p.n})
        return img, truth

    def _draw_markers(self, img: np.ndarray, d, pitch: float) -> None:
        """Paint ArUco ground markers straight into the frame through the camera
        model, so they stay sharp regardless of the raster's 25 cm pixels. Each
        marker's top edge points north, as calib.GroundMarkers assumes.

        Rendered at the CLAMPED pitch, like everything else in the frame, while
        telemetry reports the commanded one. That gap is the sim's version of
        flight 02's gimbal question, and the camera-truth flight should find it.
        """
        if not self.cfg.sim.markers:
            return
        if not hasattr(self, "_marker_tex"):
            dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
            self._marker_tex = {}
            for mid, *_ in self.cfg.sim.markers:
                side = 200
                q = side // 4
                m = cv2.aruco.generateImageMarker(dic, mid, side, borderBits=1)
                m = cv2.copyMakeBorder(m, q, q, q, q, cv2.BORDER_CONSTANT, value=255)
                self._marker_tex[mid] = (cv2.cvtColor(m, cv2.COLOR_GRAY2BGR), (side + 2 * q) / side)
        for mid, me, mn, size in self.cfg.sim.markers:
            tex, pad = self._marker_tex[mid]
            h = size / 2 * pad
            corners = [(me - h, mn + h), (me + h, mn + h), (me + h, mn - h), (me - h, mn - h)]
            pts = [self.cam.ground_to_pixel(ce, cn, d.e, d.n, d.alt, pitch, d.heading) for ce, cn in corners]
            if any(p is None for p in pts):
                continue
            th, tw = tex.shape[:2]
            src = np.array([[0, 0], [tw, 0], [tw, th], [0, th]], dtype=np.float32)
            Hm = cv2.getPerspectiveTransform(src, np.array(pts, dtype=np.float32))
            warped = cv2.warpPerspective(tex, Hm, (self.w, self.h), flags=cv2.INTER_AREA)
            mask = cv2.warpPerspective(np.full((th, tw), 255, np.uint8), Hm, (self.w, self.h))
            img[mask > 127] = warped[mask > 127]

    def _paste_sprite(self, img: np.ndarray, path: str, bbox: list[float]) -> None:
        """A real photo of a person, fitted to their projected box. The box is the
        renderer's own geometry, so detection, matching and the model's eyes all
        see a person exactly where the world says one stands."""
        if not hasattr(self, "_sprites"):
            self._sprites = {}
        spr = self._sprites.get(path)
        if spr is None:
            spr = cv2.imread(path)
            self._sprites[path] = spr
        if spr is None:
            return
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        w, h = x2 - x1, y2 - y1
        if w < 3 or h < 6:
            return
        # Keep the photo's aspect: fit to the box height, centred on the box.
        sw = max(3, int(round(spr.shape[1] * h / spr.shape[0])))
        tile = cv2.resize(spr, (sw, h), interpolation=cv2.INTER_AREA)
        cx = (x1 + x2) // 2
        ax, bx = cx - sw // 2, cx - sw // 2 + sw
        sx0, sx1 = max(0, -ax), sw - max(0, bx - img.shape[1])
        ax, bx = max(0, ax), min(img.shape[1], bx)
        ay, by = max(0, y1), min(img.shape[0], y2)
        sy0 = ay - y1
        if bx <= ax or by <= ay:
            return
        img[ay:by, ax:bx] = tile[sy0:sy0 + (by - ay), sx0:sx0 + (bx - ax)]

    def _draw_person(self, canvas: np.ndarray, e: float, n: float, shirt: tuple[int, int, int]) -> None:
        x, y = self.raster.to_px(e, n)
        r = max(2, int(PERSON_FOOTPRINT_M / self.raster.res_m / 2))
        cv2.circle(canvas, (int(x + r), int(y + r)), r, (35, 60, 35), -1)   # shadow
        cv2.circle(canvas, (int(x), int(y)), r, tuple(int(c) for c in shirt), -1)
        cv2.circle(canvas, (int(x), int(y)), max(1, r // 2), (60, 90, 140), -1)  # head/skin

    def _person_bbox(self, pe: float, pn: float, de: float, dn: float, alt: float, pitch: float,
                     heading: float) -> tuple[list[float], bool]:
        half = PERSON_FOOTPRINT_M / 2
        pts = []
        for (oe, on) in ((-half, -half), (half, -half), (half, half), (-half, half)):
            for zh in (0.0, PERSON_HEIGHT_M):
                p = self.cam.ground_to_pixel(pe + oe, pn + on, de, dn, alt, pitch, heading, zh)
                if p is None:
                    return [0, 0, 0, 0], False
                pts.append(p)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        visible = x2 > 0 and y2 > 0 and x1 < self.w and y1 < self.h and (x2 - x1) >= 3
        # clip to frame
        bbox = [max(0.0, x1), max(0.0, y1), min(float(self.w), x2), min(float(self.h), y2)]
        return bbox, visible

    # ---- monitor view ----------------------------------------------------------

    def top_down(self, trail: list[tuple[float, float]] | None = None, scale_px: int = 700) -> np.ndarray:
        """Top-down monitor: field, trees, people, drone, camera footprint."""
        d = self.world.drone
        img = self.raster.img.copy()
        r = self.raster
        # field outline
        pts = np.array([r.to_px(e, n) for (e, n) in self.cfg.field.polygon], dtype=np.int32)
        cv2.polylines(img, [pts], True, (255, 255, 255), 2)
        if trail:
            tp = np.array([r.to_px(e, n) for (e, n) in trail], dtype=np.int32)
            cv2.polylines(img, [tp], False, (255, 200, 0), 1)
        # camera footprint
        pitch = min(d.gimbal_pitch, self.min_gimbal_pitch())
        if d.alt > 0.5:
            fp = []
            for (u, v) in ((0, 0), (self.w, 0), (self.w, self.h), (0, self.h)):
                g = self.cam.pixel_to_ground(u, v, d.e, d.n, d.alt, pitch, d.heading)
                if g is not None and math.hypot(g[0] - d.e, g[1] - d.n) < 400:
                    fp.append(r.to_px(*g))
            if len(fp) == 4:
                cv2.polylines(img, [np.array(fp, dtype=np.int32)], True, (0, 255, 255), 1)
        for p in self.world.people:
            x, y = r.to_px(p.e, p.n)
            cv2.circle(img, (int(x), int(y)), 5, tuple(int(c) for c in p.cfg.shirt_bgr), -1)
            cv2.putText(img, p.cfg.id, (int(x) + 6, int(y) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        x, y = r.to_px(d.e, d.n)
        psi = math.radians(d.heading)
        tip = (int(x + 12 * math.sin(psi)), int(y - 12 * math.cos(psi)))
        cv2.circle(img, (int(x), int(y)), 7, (0, 0, 255), 2)
        cv2.line(img, (int(x), int(y)), tip, (0, 0, 255), 2)
        # crop to the field plus a border
        x0, y0, x1, y1 = geo.polygon_bbox(self.cfg.field.polygon)
        px0, py1 = r.to_px(x0 - 15, y0 - 15)
        px1, py0 = r.to_px(x1 + 15, y1 + 15)
        img = img[max(0, int(py0)):int(py1), max(0, int(px0)):int(px1)]
        cv2.putText(img, f"alt {d.alt:.1f}m  hdg {d.heading:.0f}  bat {d.battery:.0f}%  {d.mode.value}",
                    (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        s = scale_px / max(img.shape[:2])
        return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
