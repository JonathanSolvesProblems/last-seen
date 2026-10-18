"""The at-home lens calibration, run end to end from saved frames.

The live path needs the aircraft; the --from path is the same solve, and it is
what a judge or I would run to audit the frames afterwards.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import calibrate_intrinsics  # noqa: E402
from test_calib import TRUE, look_rotation, render_plane  # noqa: E402

from phantom_brain.calib import charuco_board  # noqa: E402


def test_solve_from_saved_frames_writes_the_lens(tmp_path, monkeypatch):
    board = charuco_board()
    cell, margin = 180, 40
    img = board.generateImage((7 * cell + 2 * margin, 5 * cell + 2 * margin), marginSize=margin)
    bw, bh, m = 0.21, 0.15, margin / cell * 0.03
    plane = np.array([[-m, bh + m, 0], [bw + m, bh + m, 0], [bw + m, -m, 0], [-m, -m, 0]])
    rng = np.random.default_rng(11)
    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(16):
        cam = np.array([bw / 2 + rng.uniform(-0.15, 0.15), bh / 2 + rng.uniform(-0.12, 0.12),
                        rng.uniform(0.28, 0.5)])
        R = look_rotation(np.array([bw / 2, bh / 2, 0.0]) - cam)
        f = np.full((720, 1280), 200, np.uint8)
        render_plane(img, plane, R, -R @ cam, TRUE.K, f)
        cv2.imwrite(str(frames / f"view_{i:03d}.jpg"), f)

    out = tmp_path / "camera_intrinsics.json"
    monkeypatch.setattr(calibrate_intrinsics, "CONFIG", out)
    assert calibrate_intrinsics.from_dir(frames) == 0
    d = json.loads(out.read_text())
    assert abs(d["K"][0][0] - TRUE.fx) / TRUE.fx < 0.03
    assert d["diagonal_fov_deg"] == pytest_approx(94.0, 2.0)
    assert "OpenCV" in d["method"]


def pytest_approx(v: float, tol: float):
    import pytest  # noqa: PLC0415
    return pytest.approx(v, abs=tol)
