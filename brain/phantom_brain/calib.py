"""What the camera actually is, and where it is actually pointing, from OpenCV 5.

Flight 02 left the project with two distance estimates for the same person that
disagreed by 5.8 m (10.1 m from apparent size, 15.9 m from ground geometry), and
no way to say which was wrong. The likeliest culprit was the gimbal: it reported
-23 degrees while the frames looked like about -37. Nothing on the aircraft can
settle that, because every number it reports is the thing in question.

Two printed targets settle it from outside:

  ChArUco board, hand-held at home     -> intrinsics: focal length, principal
                                          point, distortion (calib module).
  Four ArUco markers on the grass,      -> the camera's true pose over the
  their spacing measured with a tape       ground: its real pitch, its real
                                          height, its heading (3d module,
                                          solvePnP on a plane).

The board is the same 7x5, 30 mm, DICT_4X4_50 design as Onspec's, so one print
serves both. Ground markers use ids 20 to 23, which the board never contains.

Follow-me does NOT need any of this: it steers by apparent size, which no
calibration error can move. This is for the other half of the job, which is
saying WHERE the person is, so the coordinator's pin is a place you can walk to.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

ARUCO_DICT = cv2.aruco.DICT_4X4_50
BOARD_SQUARES = (7, 5)
BOARD_SQUARE_MM = 30.0
BOARD_MARKER_MM = 22.0
GROUND_IDS = (20, 21, 22, 23)


# --- intrinsics ----------------------------------------------------------------

@dataclass
class Intrinsics:
    K: np.ndarray
    dist: np.ndarray
    width: int
    height: int
    rms_reprojection_px: float = 0.0
    frames_used: int = 0

    @property
    def fx(self) -> float:
        return float(self.K[0, 0])

    @property
    def diagonal_fov_deg(self) -> float:
        """For CameraModel, which predates full intrinsics and wants one number."""
        f = 0.5 * (self.K[0, 0] + self.K[1, 1])
        return math.degrees(2 * math.atan(math.hypot(self.width, self.height) / 2 / f))

    def scaled_to(self, width: int, height: int) -> "Intrinsics":
        """The same lens at another resolution. Focal length and principal point
        scale with the frame; distortion coefficients are resolution-free."""
        sx, sy = width / self.width, height / self.height
        K = self.K.copy()
        K[0, 0] *= sx
        K[0, 2] *= sx
        K[1, 1] *= sy
        K[1, 2] *= sy
        return Intrinsics(K, self.dist.copy(), width, height, self.rms_reprojection_px, self.frames_used)

    def to_dict(self) -> dict:
        return {"K": self.K.tolist(), "dist": self.dist.ravel().tolist(),
                "width": self.width, "height": self.height,
                "rms_reprojection_px": round(self.rms_reprojection_px, 4),
                "frames_used": self.frames_used,
                "diagonal_fov_deg": round(self.diagonal_fov_deg, 3)}

    def save(self, path: Path | str, **extra) -> None:
        Path(path).write_text(json.dumps({**self.to_dict(), **extra}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> "Intrinsics":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(np.array(d["K"], dtype=np.float64), np.array(d["dist"], dtype=np.float64),
                   int(d["width"]), int(d["height"]), float(d.get("rms_reprojection_px", 0.0)),
                   int(d.get("frames_used", 0)))

    @classmethod
    def from_fov(cls, width: int, height: int, diagonal_fov_deg: float) -> "Intrinsics":
        f = math.hypot(width, height) / 2 / math.tan(math.radians(diagonal_fov_deg) / 2)
        K = np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]], dtype=np.float64)
        return cls(K, np.zeros(5), width, height)


def charuco_board() -> cv2.aruco.CharucoBoard:
    d = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    return cv2.aruco.CharucoBoard(BOARD_SQUARES, BOARD_SQUARE_MM / 1000.0,
                                  BOARD_MARKER_MM / 1000.0, d)


def detect_charuco(gray: np.ndarray, board: cv2.aruco.CharucoBoard | None = None
                   ) -> tuple[np.ndarray, np.ndarray] | None:
    """(object points, image points) for one view, or None if too few corners."""
    board = board or charuco_board()
    ch_corners, ch_ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(gray)
    if ch_ids is None or len(ch_ids) < 8:
        return None
    obj, img = board.matchImagePoints(ch_corners, ch_ids)
    if obj is None or len(obj) < 8:
        return None
    return obj, img


def calibrate_from_views(views: Sequence[tuple[np.ndarray, np.ndarray]],
                         size: tuple[int, int]) -> Intrinsics:
    """Intrinsics from ChArUco views. Needs variety: tilted, near, far, corners."""
    if len(views) < 8:
        raise ValueError(f"need at least 8 good views of the board, got {len(views)}")
    obj = [v[0] for v in views]
    img = [v[1] for v in views]
    # Five-term distortion, no tangential games: a 94 deg lens is barrel-
    # distorted and k3 matters at the edges, which is where a person walks off.
    rms, K, dist, _, _ = cv2.calibrateCamera(obj, img, size, None, None)
    return Intrinsics(K, dist, size[0], size[1], float(rms), len(views))


# --- the camera's pose over the ground --------------------------------------------

@dataclass
class GroundMarkers:
    """Marker centres on the grass, in metres east and north of marker 20.

    Lay four markers on a rectangle, measure the sides with a tape, and write
    the numbers here. The tape is the grader: every pose solved from these is
    only as good as that measurement, and nothing in this repository wrote it.
    Each marker's top edge points north (toward +y).
    """
    centres_m: dict[int, tuple[float, float]]
    size_m: float

    def object_points(self, mid: int) -> np.ndarray:
        cx, cy = self.centres_m[mid]
        h = self.size_m / 2
        # ArUco corner order: top-left, top-right, bottom-right, bottom-left,
        # with "top" toward +y (north). World axes: x east, y north, z up.
        return np.array([[cx - h, cy + h, 0], [cx + h, cy + h, 0],
                         [cx + h, cy - h, 0], [cx - h, cy - h, 0]], dtype=np.float64)

    def to_dict(self) -> dict:
        return {"size_m": self.size_m,
                "centres_m": {str(k): list(v) for k, v in self.centres_m.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "GroundMarkers":
        return cls({int(k): (float(v[0]), float(v[1])) for k, v in d["centres_m"].items()},
                   float(d["size_m"]))

    @classmethod
    def load(cls, path: Path | str) -> "GroundMarkers":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class CameraPose:
    R: np.ndarray            # world -> camera rotation
    t: np.ndarray            # world -> camera translation
    markers: int
    reprojection_px: float

    @property
    def position(self) -> np.ndarray:
        """Camera centre in world metres (x east, y north, z up)."""
        return (-self.R.T @ self.t.reshape(3, 1)).ravel()

    @property
    def height_m(self) -> float:
        return float(self.position[2])

    @property
    def optical_axis(self) -> np.ndarray:
        return self.R.T @ np.array([0.0, 0.0, 1.0])

    @property
    def gimbal_pitch_deg(self) -> float:
        """Pitch in DJI's convention: 0 level, -90 straight down."""
        a = self.optical_axis
        return math.degrees(math.atan2(a[2], math.hypot(a[0], a[1])))

    @property
    def heading_deg(self) -> float:
        """Where the camera points, clockwise from the markers' north."""
        a = self.optical_axis
        return math.degrees(math.atan2(a[0], a[1])) % 360.0

    def to_dict(self) -> dict:
        p = self.position
        return {"x_m": round(float(p[0]), 3), "y_m": round(float(p[1]), 3),
                "height_m": round(self.height_m, 3),
                "gimbal_pitch_deg": round(self.gimbal_pitch_deg, 2),
                "heading_deg": round(self.heading_deg, 2),
                "markers": self.markers,
                "reprojection_px": round(self.reprojection_px, 3)}


def detect_markers(gray: np.ndarray) -> tuple[list[np.ndarray], np.ndarray | None]:
    params = cv2.aruco.DetectorParameters()
    # Sub-pixel corners: from 5 m up a marker is ~30 px across, and a quarter
    # pixel of corner error is a degree of pitch.
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), params)
    corners, ids, _ = det.detectMarkers(gray)
    return list(corners), ids


def solve_camera_pose(frame_bgr: np.ndarray, intr: Intrinsics,
                      markers: GroundMarkers) -> CameraPose | None:
    """The camera's real pose over the ground, from markers whose spacing a tape
    measured. Needs at least two markers: one marker's four corners pin a pose
    too, but a single 18 cm square seen from 6 m leaves pitch soft."""
    gray = frame_bgr if frame_bgr.ndim == 2 else cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    corners, ids = detect_markers(gray)
    if ids is None:
        return None
    obj, img = [], []
    for c, mid in zip(corners, ids.ravel()):
        mid = int(mid)
        if mid in markers.centres_m:
            obj.append(markers.object_points(mid))
            img.append(c.reshape(4, 2).astype(np.float64))
    if len(obj) < 2:
        return None
    obj_pts, img_pts = np.vstack(obj), np.vstack(img)
    ok, rvec, tvec = cv2.solvePnP(obj_pts, img_pts, intr.K, intr.dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    rvec, tvec = cv2.solvePnPRefineLM(obj_pts, img_pts, intr.K, intr.dist, rvec, tvec)
    proj, _ = cv2.projectPoints(obj_pts, rvec, tvec, intr.K, intr.dist)
    err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img_pts) ** 2, axis=1))))
    R, _ = cv2.Rodrigues(rvec)
    return CameraPose(R, tvec.ravel(), len(obj), err)


def pixel_to_ground(u: float, v: float, intr: Intrinsics, pose: CameraPose,
                    z: float = 0.0) -> tuple[float, float] | None:
    """Where a pixel lands on the ground plane, in world metres. None above the
    horizon. Undistorts first: at the frame edge a 94 deg lens moves a point by
    tens of pixels, which is metres on the ground."""
    pt = cv2.undistortPoints(np.array([[[u, v]]], dtype=np.float64), intr.K, intr.dist).reshape(2)
    ray_cam = np.array([pt[0], pt[1], 1.0])
    ray_w = pose.R.T @ ray_cam
    c = pose.position
    if abs(ray_w[2]) < 1e-9:
        return None
    s = (z - c[2]) / ray_w[2]
    if s <= 0:
        return None
    g = c + s * ray_w
    return float(g[0]), float(g[1])
