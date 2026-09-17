"""Task 1, Reconstruct: build a 2D obstacle map of the field from a survey flight.

Drone-Bench reconstructs an office in 3D from video. Outdoors, over a flat
field, the useful equivalent is an occupancy grid: which ground cells are open
grass and which are trees, benches, paths, water. The drone flies a lawnmower
pattern with the camera pointing straight down, and every frame is classified
pixel by pixel (grass vs not-grass) and projected onto the grid using the
GPS position, altitude and the camera model.

This is deliberately simple. It is real, it runs live, and a judge can see the
map appear while the drone flies. Heights are not measured (a nadir camera
cannot), so the mission keeps a fixed altitude floor over the whole field.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from .. import geo
from ..camera import CameraModel

UNKNOWN, FREE, OBSTACLE = 0, 1, 2


@dataclass
class OccupancyGrid:
    min_e: float
    min_n: float
    cell_m: float
    cols: int
    rows: int
    cells: np.ndarray = field(default=None)           # uint8 UNKNOWN/FREE/OBSTACLE
    votes_obst: np.ndarray = field(default=None)      # float32 evidence
    votes_free: np.ndarray = field(default=None)

    def __post_init__(self) -> None:
        if self.cells is None:
            self.cells = np.zeros((self.rows, self.cols), dtype=np.uint8)
        if self.votes_obst is None:
            self.votes_obst = np.zeros((self.rows, self.cols), dtype=np.float32)
        if self.votes_free is None:
            self.votes_free = np.zeros((self.rows, self.cols), dtype=np.float32)

    @classmethod
    def for_polygon(cls, poly: Sequence[tuple[float, float]], cell_m: float = 1.0,
                    pad_m: float = 5.0) -> "OccupancyGrid":
        x0, y0, x1, y1 = geo.polygon_bbox(poly)
        x0 -= pad_m
        y0 -= pad_m
        x1 += pad_m
        y1 += pad_m
        cols = int(math.ceil((x1 - x0) / cell_m))
        rows = int(math.ceil((y1 - y0) / cell_m))
        return cls(x0, y0, cell_m, cols, rows)

    # ---- coordinates ----------------------------------------------------

    def to_cell(self, e: float, n: float) -> tuple[int, int]:
        """(col, row) for an ENU point. Row 0 is the SOUTH edge."""
        c = int((e - self.min_e) / self.cell_m)
        r = int((n - self.min_n) / self.cell_m)
        return c, r

    def to_enu(self, c: int, r: int) -> tuple[float, float]:
        return (self.min_e + (c + 0.5) * self.cell_m, self.min_n + (r + 0.5) * self.cell_m)

    def in_bounds(self, c: int, r: int) -> bool:
        return 0 <= c < self.cols and 0 <= r < self.rows

    def is_free(self, c: int, r: int, unknown_is_free: bool = True) -> bool:
        if not self.in_bounds(c, r):
            return False
        v = self.cells[r, c]
        return v == FREE or (unknown_is_free and v == UNKNOWN)

    # ---- evidence ---------------------------------------------------------

    def add_votes(self, obst_map: np.ndarray, free_map: np.ndarray) -> None:
        self.votes_obst += obst_map
        self.votes_free += free_map
        self.resolve()

    def resolve(self, min_votes: float = 1.0) -> None:
        total = self.votes_obst + self.votes_free
        known = total >= min_votes
        obst = known & (self.votes_obst > 0.45 * total)
        self.cells[:] = UNKNOWN
        self.cells[known] = FREE
        self.cells[obst] = OBSTACLE

    def inflated(self, radius_m: float) -> "OccupancyGrid":
        """Copy with obstacles dilated by radius_m (safety margin for planning)."""
        r = max(1, int(round(radius_m / self.cell_m)))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        obst = (self.cells == OBSTACLE).astype(np.uint8)
        dil = cv2.dilate(obst, k)
        g = OccupancyGrid(self.min_e, self.min_n, self.cell_m, self.cols, self.rows,
                          cells=self.cells.copy(), votes_obst=self.votes_obst.copy(),
                          votes_free=self.votes_free.copy())
        g.cells[dil > 0] = OBSTACLE
        return g

    def mark_outside_polygon(self, poly: Sequence[tuple[float, float]], margin_m: float = 0.0) -> None:
        """Block cells outside the polygon, and within margin_m of its edge."""
        for r in range(self.rows):
            for c in range(self.cols):
                e, n = self.to_enu(c, r)
                if not geo.point_in_polygon(e, n, poly) or \
                        (margin_m > 0 and geo.dist_to_polygon_edge(e, n, poly) < margin_m):
                    self.cells[r, c] = OBSTACLE

    def planning_copy(self, poly: Sequence[tuple[float, float]], safety_m: float,
                      margin_m: float) -> "OccupancyGrid":
        """Inflated obstacles plus the fence band, for A*. Not for scoring."""
        g = self.inflated(safety_m)
        g.mark_outside_polygon(poly, margin_m)
        return g

    # ---- io ---------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        path = Path(path)
        np.savez_compressed(path.with_suffix(".npz"), cells=self.cells,
                            votes_obst=self.votes_obst, votes_free=self.votes_free)
        meta = {"min_e": self.min_e, "min_n": self.min_n, "cell_m": self.cell_m,
                "cols": self.cols, "rows": self.rows}
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        cv2.imwrite(str(path.with_suffix(".png")), self.render())

    @classmethod
    def load(cls, path: str | Path) -> "OccupancyGrid":
        path = Path(path)
        meta = json.loads(path.with_suffix(".json").read_text())
        data = np.load(path.with_suffix(".npz"))
        return cls(meta["min_e"], meta["min_n"], meta["cell_m"], meta["cols"], meta["rows"],
                   cells=data["cells"], votes_obst=data["votes_obst"], votes_free=data["votes_free"])

    def render(self, scale: int = 4) -> np.ndarray:
        img = np.zeros((self.rows, self.cols, 3), dtype=np.uint8)
        img[self.cells == UNKNOWN] = (60, 60, 60)
        img[self.cells == FREE] = (90, 170, 90)
        img[self.cells == OBSTACLE] = (40, 40, 200)
        img = cv2.resize(img, (self.cols * scale, self.rows * scale), interpolation=cv2.INTER_NEAREST)
        return cv2.flip(img, 0)   # north up

    # ---- scoring ------------------------------------------------------------

    def iou_obstacles(self, truth: "OccupancyGrid") -> float:
        a = self.cells == OBSTACLE
        b = truth.cells == OBSTACLE
        inter = np.logical_and(a, b).sum()
        union = np.logical_or(a, b).sum()
        return float(inter / union) if union else 1.0

    def coverage(self) -> float:
        return float((self.cells != UNKNOWN).mean())


# ---- classification ------------------------------------------------------------

@dataclass
class GrassClassifier:
    """Very small HSV rule: grass is green-ish, reasonably bright and not too dark.

    Tree canopies seen from above are green too, but markedly darker than
    sunlit grass, so v_min does most of the work. Deep shadow on grass will
    also be flagged as obstacle, which is the safe direction to be wrong.
    Tune the thresholds on the first real survey frames with
    scripts/tune_grass.py and put the values in the mission config.
    """
    h_lo: int = 30
    h_hi: int = 95
    s_min: int = 45
    v_min: int = 95
    blur: int = 5

    def obstacle_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        img = cv2.GaussianBlur(frame_bgr, (self.blur, self.blur), 0) if self.blur else frame_bgr
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        grass = cv2.inRange(hsv, (self.h_lo, self.s_min, self.v_min), (self.h_hi, 255, 255))
        obst = cv2.bitwise_not(grass)
        obst = cv2.morphologyEx(obst, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        return obst


def ingest_nadir_frame(grid: OccupancyGrid, frame_bgr: np.ndarray, cam: CameraModel,
                       drone_e: float, drone_n: float, alt: float, heading: float,
                       gimbal_pitch: float, classifier: GrassClassifier,
                       max_range_m: float = 60.0, weight: float = 1.0) -> int:
    """Project one frame's obstacle mask onto the grid. Returns cells touched."""
    if cam.horizon_in_frame(gimbal_pitch) or alt < 2.0:
        return 0
    h, w = frame_bgr.shape[:2]
    cam = cam.scaled(w, h) if (cam.width, cam.height) != (w, h) else cam
    obst = classifier.obstacle_mask(frame_bgr)

    # Homography image -> grid. Sample 4 pixels well inside the frame, get
    # their ground points, and map ground points to grid cell coordinates.
    src = np.array([[w * 0.2, h * 0.2], [w * 0.8, h * 0.2], [w * 0.8, h * 0.8], [w * 0.2, h * 0.8]],
                   dtype=np.float32)
    dst = []
    for (u, v) in src:
        g = cam.pixel_to_ground(float(u), float(v), drone_e, drone_n, alt, gimbal_pitch, heading)
        if g is None or math.hypot(g[0] - drone_e, g[1] - drone_n) > max_range_m:
            return 0
        c = (g[0] - grid.min_e) / grid.cell_m
        r = (g[1] - grid.min_n) / grid.cell_m
        dst.append([c, r])
    dst_arr = np.array(dst, dtype=np.float32)
    H = cv2.getPerspectiveTransform(src, dst_arr)

    obst_f = (obst > 0).astype(np.float32)
    # Average the mask over roughly one grid cell's worth of pixels before
    # sampling, so a cell's value is the obstacle fraction, not one pixel.
    px_per_cell = int(round(grid.cell_m / max(cam.nadir_gsd(alt), 1e-3)))
    k = max(1, min(101, px_per_cell | 1))
    if k > 1:
        obst_f = cv2.blur(obst_f, (k, k))
    seen = np.ones_like(obst_f)
    obst_g = cv2.warpPerspective(obst_f, H, (grid.cols, grid.rows), flags=cv2.INTER_LINEAR)
    seen_g = cv2.warpPerspective(seen, H, (grid.cols, grid.rows), flags=cv2.INTER_LINEAR)
    free_g = np.clip(seen_g - obst_g, 0, 1)
    grid.add_votes(obst_g * weight, free_g * weight)
    return int((seen_g > 0.01).sum())


# ---- survey planning -------------------------------------------------------------

def lawnmower(poly: Sequence[tuple[float, float]], spacing_m: float, margin_m: float = 0.0,
              axis: str = "ew") -> list[tuple[float, float]]:
    """Back-and-forth legs covering the polygon. Returns ENU waypoints.

    Legs run east-west (axis="ew") or north-south (axis="ns"). Each leg is
    clipped to the polygon by sampling, which is plenty for a field.
    """
    x0, y0, x1, y1 = geo.polygon_bbox(poly)
    x0 += margin_m
    y0 += margin_m
    x1 -= margin_m
    y1 -= margin_m
    pts: list[tuple[float, float]] = []
    step = 0.5
    if axis == "ew":
        ys = np.arange(y0 + spacing_m / 2, y1, spacing_m)
        if len(ys) == 0:
            ys = np.array([(y0 + y1) / 2])
        for i, y in enumerate(ys):
            xs = np.arange(x0, x1 + 1e-6, step)
            inside = [x for x in xs if geo.point_in_polygon(x, y, poly)]
            if not inside:
                continue
            a, b = min(inside), max(inside)
            if i % 2 == 1:
                a, b = b, a
            pts.append((a, float(y)))
            pts.append((b, float(y)))
    else:
        xs = np.arange(x0 + spacing_m / 2, x1, spacing_m)
        if len(xs) == 0:
            xs = np.array([(x0 + x1) / 2])
        for i, x in enumerate(xs):
            ys = np.arange(y0, y1 + 1e-6, step)
            inside = [y for y in ys if geo.point_in_polygon(x, y, poly)]
            if not inside:
                continue
            a, b = min(inside), max(inside)
            if i % 2 == 1:
                a, b = b, a
            pts.append((float(x), a))
            pts.append((float(x), b))
    return pts


def survey_spacing(cam: CameraModel, altitude_m: float, overlap: float = 0.3) -> float:
    """Leg spacing so nadir footprints overlap by `overlap` across track."""
    half_w = altitude_m * math.tan(math.radians(cam.hfov_deg / 2))
    return 2 * half_w * (1 - overlap)


def truth_grid_from_circles(poly: Sequence[tuple[float, float]], circles: Sequence[tuple[float, float, float]],
                            cell_m: float = 1.0, pad_m: float = 5.0) -> OccupancyGrid:
    """Ground-truth grid for scoring in the sim: circles are (e, n, radius)."""
    g = OccupancyGrid.for_polygon(poly, cell_m, pad_m)
    g.cells[:] = FREE
    for (ce, cn, r) in circles:
        c, rr = g.to_cell(ce, cn)
        rad = int(round(r / cell_m))
        cv2.circle(g.cells, (c, rr), rad, int(OBSTACLE), -1)
    return g
