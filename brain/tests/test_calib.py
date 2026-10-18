"""Calibration and camera-pose recovery, against scenes rendered from a KNOWN camera.

These are synthetic on purpose and say so: the point is to prove the geometry
code recovers a pose that was put there, before it is trusted to tell the
gimbal's reported angle apart from the real one. The field version is graded by
a tape measure, not by these tests.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from phantom_brain.calib import (ARUCO_DICT, GroundMarkers, Intrinsics, calibrate_from_views,
                                 charuco_board, detect_charuco, pixel_to_ground,
                                 solve_camera_pose)

W, H = 1280, 720
TRUE = Intrinsics.from_fov(W, H, 94.0)


def look_rotation(forward: np.ndarray) -> np.ndarray:
    """World->camera rotation for a camera looking along `forward` (x east, y north, z up)."""
    f = forward / np.linalg.norm(forward)
    right = np.cross(f, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(f, right)
    return np.vstack([right, down, f])


def pose_for(cam: np.ndarray, pitch_deg: float, heading_deg: float) -> tuple[np.ndarray, np.ndarray]:
    p, h = math.radians(pitch_deg), math.radians(heading_deg)
    fwd = np.array([math.sin(h) * math.cos(p), math.cos(h) * math.cos(p), math.sin(p)])
    R = look_rotation(fwd)
    return R, -R @ cam


def render_plane(img_plane: np.ndarray, corners_world: np.ndarray, R, t, K, out: np.ndarray) -> None:
    """Paint a planar image (z=0) into `out` as the camera sees it."""
    h, w = img_plane.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    rvec, _ = cv2.Rodrigues(R)
    dst, _ = cv2.projectPoints(corners_world.astype(np.float64), rvec, t, K, np.zeros(5))
    Hm = cv2.getPerspectiveTransform(src, dst.reshape(4, 2).astype(np.float32))
    warped = cv2.warpPerspective(img_plane, Hm, (out.shape[1], out.shape[0]),
                                 flags=cv2.INTER_LINEAR, borderValue=0)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), Hm, (out.shape[1], out.shape[0]))
    out[mask > 0] = warped[mask > 0]


LAYOUT = GroundMarkers({20: (0.0, 0.0), 21: (2.0, 0.0), 22: (2.0, 2.0), 23: (0.0, 2.0)}, size_m=0.18)


def render_ground(R, t, K=TRUE.K) -> np.ndarray:
    frame = np.full((H, W), 110, np.uint8)            # grass-grey
    d = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    for mid, (cx, cy) in LAYOUT.centres_m.items():
        side = 240
        quiet = side // 4
        m = cv2.aruco.generateImageMarker(d, mid, side, borderBits=1)
        m = cv2.copyMakeBorder(m, quiet, quiet, quiet, quiet, cv2.BORDER_CONSTANT, value=255)
        half = LAYOUT.size_m / 2 * (side + 2 * quiet) / side
        corners = np.array([[cx - half, cy + half, 0], [cx + half, cy + half, 0],
                            [cx + half, cy - half, 0], [cx - half, cy - half, 0]])
        render_plane(m, corners, R, t, K, frame)
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


@pytest.mark.parametrize("pitch", [-23.0, -37.0, -55.0])
def test_pose_recovers_the_real_gimbal_pitch(pitch):
    """The question flight 02 could not answer: was the camera at -23 or -37?
    Rendered at each, the solver must say which, to well under a degree."""
    cam = np.array([1.0, -4.5, 4.0])
    R, t = pose_for(cam, pitch, heading_deg=5.0)
    pose = solve_camera_pose(render_ground(R, t), TRUE, LAYOUT)
    assert pose is not None and pose.markers >= 2
    assert pose.gimbal_pitch_deg == pytest.approx(pitch, abs=0.6)
    assert pose.height_m == pytest.approx(4.0, abs=0.15)
    assert pose.heading_deg == pytest.approx(5.0, abs=1.0)


def test_a_14_degree_gimbal_error_is_unmistakable():
    """Rendered at -37, reported as -23. The measured pitch must sit nearer the
    truth than the report by a wide margin, or this cannot settle anything."""
    cam = np.array([1.0, -4.5, 4.0])
    R, t = pose_for(cam, -37.0, heading_deg=0.0)
    pose = solve_camera_pose(render_ground(R, t), TRUE, LAYOUT)
    assert abs(pose.gimbal_pitch_deg - -37.0) < 1.0
    assert abs(pose.gimbal_pitch_deg - -23.0) > 12.0


def test_one_marker_is_not_enough():
    frame = render_ground(*pose_for(np.array([1.0, -4.5, 4.0]), -37.0, 0.0))
    only_one = GroundMarkers({20: (0.0, 0.0)}, 0.18)
    assert solve_camera_pose(frame, TRUE, only_one) is None


def test_pixel_to_ground_round_trips():
    """The pin: a ground point projected into the frame must come back out of
    pixel_to_ground at the same place."""
    cam = np.array([1.0, -4.5, 6.0])
    R, t = pose_for(cam, -40.0, 10.0)
    pose = solve_camera_pose(render_ground(R, t), TRUE, LAYOUT)
    rvec, _ = cv2.Rodrigues(pose.R)
    for gx, gy in ((1.0, 1.0), (3.5, 4.0), (-1.0, 2.5)):
        px, _ = cv2.projectPoints(np.array([[gx, gy, 0.0]]), rvec, pose.t, TRUE.K, TRUE.dist)
        u, v = px.reshape(2)
        back = pixel_to_ground(u, v, TRUE, pose)
        assert back is not None
        assert math.hypot(back[0] - gx, back[1] - gy) < 0.05


def test_above_the_horizon_has_no_ground_point():
    pose = solve_camera_pose(render_ground(*pose_for(np.array([1.0, -4.5, 4.0]), -37.0, 0.0)),
                             TRUE, LAYOUT)
    # far above the top edge, the ray points at the sky
    assert pixel_to_ground(W / 2, -2000.0, TRUE, pose) is None


def test_charuco_calibration_recovers_the_focal_length():
    """Hand-held board views, rendered from known poses, through calibrateCamera."""
    board = charuco_board()
    cell, margin = 180, 40                 # square cells, or the board is not the board
    img = board.generateImage((7 * cell + 2 * margin, 5 * cell + 2 * margin),
                              marginSize=margin, borderBits=1)
    bw, bh = 7 * 0.030, 5 * 0.030
    m = margin / cell * 0.030
    plane = np.array([[-m, bh + m, 0], [bw + m, bh + m, 0],
                      [bw + m, -m, 0], [-m, -m, 0]])
    views = []
    rng = np.random.default_rng(3)
    for _ in range(14):
        cam = np.array([bw / 2 + rng.uniform(-0.15, 0.15), bh / 2 + rng.uniform(-0.12, 0.12),
                        rng.uniform(0.28, 0.5)])
        target = np.array([bw / 2 + rng.uniform(-0.05, 0.05), bh / 2 + rng.uniform(-0.04, 0.04), 0.0])
        R = look_rotation(target - cam)
        # roll the camera a little so views are not all upright
        roll = rng.uniform(-0.3, 0.3)
        Rz = np.array([[math.cos(roll), -math.sin(roll), 0], [math.sin(roll), math.cos(roll), 0], [0, 0, 1]])
        R = Rz @ R
        t = -R @ cam
        frame = np.full((H, W), 200, np.uint8)
        render_plane(img, plane, R, t, TRUE.K, frame)
        v = detect_charuco(frame, board)
        if v is not None:
            views.append(v)
    assert len(views) >= 8
    got = calibrate_from_views(views, (W, H))
    assert got.fx == pytest.approx(TRUE.fx, rel=0.03)
    assert got.rms_reprojection_px < 1.0


def test_intrinsics_scale_with_resolution():
    half = TRUE.scaled_to(640, 360)
    assert half.fx == pytest.approx(TRUE.fx / 2)
    assert half.diagonal_fov_deg == pytest.approx(TRUE.diagonal_fov_deg, abs=0.01)
