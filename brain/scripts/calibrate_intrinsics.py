"""Measure the Phantom's lens with a ChArUco board, at home, propellers OFF.

    python scripts/calibrate_intrinsics.py               # live, through the bridge
    python scripts/calibrate_intrinsics.py --from DIR    # re-run on saved frames

Setup: aircraft on, propellers off, `fieldup.py` printed READY (or the bridge is
up some other way), gimbal level. Hold the board 0.3 to 1.5 m from the camera
and move it slowly: near and far, tilted left, right, up and down, and into
every corner of the frame. The script keeps a view only when the board is seen
well AND sits somewhere new, so holding it still in one place adds nothing.

It stops at --views good views (default 30), solves, prints the result, and
writes configs/camera_intrinsics.json. Every accepted frame is saved under
runs/calib/ so the solve can be re-run, or audited, later.

What grades the answer: OpenCV's reprojection error (under 0.5 px is good on a
720p feed), and the printed square, which you checked with a ruler.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.calib import (Intrinsics, calibrate_from_views,  # noqa: E402
                                 charuco_board, detect_charuco)
from phantom_brain.camera import CameraModel  # noqa: E402
from phantom_brain.pilot import runs_root  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "camera_intrinsics.json"


def coverage_cell(img_pts: np.ndarray, w: int, h: int, grid: int = 4) -> tuple[int, int, int]:
    """Where the board sits (grid cell of its centre) and roughly how big, so a
    new view must add something: a different place or a different distance."""
    c = img_pts.reshape(-1, 2).mean(axis=0)
    span = np.ptp(img_pts.reshape(-1, 2), axis=0).max() / max(w, h)
    size_bin = 0 if span < 0.25 else 1 if span < 0.5 else 2
    return int(c[0] / w * grid), int(c[1] / h * grid), size_bin


def solve_and_save(views, size, source: str) -> int:
    intr = calibrate_from_views(views, size)
    spec = CameraModel(size[0], size[1])
    print("\n" + "=" * 60)
    print(f"  views used         {intr.frames_used}")
    print(f"  reprojection error {intr.rms_reprojection_px:.3f} px "
          f"({'good' if intr.rms_reprojection_px < 0.5 else 'usable' if intr.rms_reprojection_px < 1.0 else 'POOR, redo with more varied views'})")
    print(f"  focal length       fx {intr.K[0, 0]:.1f}  fy {intr.K[1, 1]:.1f} px")
    print(f"  principal point    {intr.K[0, 2]:.1f}, {intr.K[1, 2]:.1f}  (centre is {size[0] / 2:.0f}, {size[1] / 2:.0f})")
    print(f"  diagonal FOV       {intr.diagonal_fov_deg:.1f} deg  (spec sheet 94.0 deg -> {spec.focal_px:.1f} px)")
    print(f"  distortion         {np.round(intr.dist.ravel(), 4).tolist()}")
    print("=" * 60)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    intr.save(CONFIG, measured=time.strftime("%Y-%m-%d %H:%M"), source=source,
              method="ChArUco 7x5 30 mm, cv2.calibrateCamera, OpenCV " + cv2.__version__)
    print(f"  wrote {CONFIG}")
    return 0


def from_dir(path: Path) -> int:
    board = charuco_board()
    views, size = [], None
    for f in sorted(path.glob("*.jpg")):
        img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        size = (img.shape[1], img.shape[0])
        v = detect_charuco(img, board)
        if v is not None:
            views.append(v)
    print(f"{len(views)} usable views in {path}")
    return solve_and_save(views, size, str(path))


async def live(url: str, want: int, timeout_s: float) -> int:
    from phantom_brain.client import BridgeClient  # noqa: PLC0415
    out = runs_root() / "calib" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    board = charuco_board()
    views, size = [], None
    seen: Counter = Counter()
    end = time.monotonic() + timeout_s
    async with BridgeClient(url) as bc:
        await bc.wait_for_telemetry(15)
        try:
            await bc.set_gimbal_pitch(0.0)       # level, so the board can be held in front of it
        except Exception as e:  # noqa: BLE001
            print(f"(gimbal did not level: {e}; carry on)")
        print(f"hold the board up to the camera and move it around; need {want} views")
        while len(views) < want and time.monotonic() < end:
            fr = await bc.next_frame(timeout=5)
            img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            size = (gray.shape[1], gray.shape[0])
            v = detect_charuco(gray, board)
            if v is None:
                continue
            # Motion blur makes corners lie; skip frames where the board is soft.
            if cv2.Laplacian(gray, cv2.CV_64F).var() < 60:
                continue
            cell = coverage_cell(v[1], *size)
            # Each place-and-size may contribute at most two views.
            if seen[cell] >= 2:
                continue
            seen[cell] += 1
            views.append(v)
            cv2.imwrite(str(out / f"view_{len(views):03d}.jpg"), img)
            print(f"  view {len(views):2d}/{want}  corners {len(v[0]):2d}  cell {cell}", flush=True)
    if size is None or len(views) < 8:
        print(f"only {len(views)} views; is the board in front of the camera and in focus?")
        return 1
    print(f"frames saved in {out}")
    return solve_and_save(views, size, str(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--views", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--from", dest="src", type=Path, default=None)
    args = ap.parse_args()
    if args.src:
        return from_dir(args.src)
    return asyncio.run(live(args.url, args.views, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
