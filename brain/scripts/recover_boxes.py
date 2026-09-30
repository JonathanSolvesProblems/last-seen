"""Recover bounding boxes from annotated frames, and solve for the focal length.

    python scripts/recover_boxes.py --frames broll/followme-... --alt 6.0 \
        --gimbal -23 --height 1.905

Flight 02 logged the box HEIGHT but not its vertical POSITION, and the position
is what turns a detection into a distance. The annotated frames drawn during the
run still carry it: `draw_detections` outlines the tracked target in pure red, so
the box can be read back off the pixels.

With the box recovered there are two independent routes to the same distance,
and flight 02 showed them disagreeing by about 80%:

  apparent size    slant = person_height * focal / box_height_px
  ground geometry  depression = gimbal + atan((feet_row - cy) / focal)
                   ground = altitude / tan(depression)

Both use the focal length, in different ways, so requiring them to AGREE pins
it down with no tape measure and no paced distance. That is the whole point:
the ground calibration failed at the park because the camera sits in the grass,
and pacing a distance while flying is not something to rely on.

If they cannot be reconciled, the assumed gimbal pitch is the usual culprit, and
this says so rather than quietly fitting a number.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.camera import CameraModel  # noqa: E402

# draw_detections outlines the tracked target in pure red (0, 0, 255) BGR.
TARGET_BGR = (0, 0, 255)


def red_box(img: np.ndarray, tol: int = 40) -> tuple[int, int, int, int] | None:
    """The drawn target rectangle, as (x1, y1, x2, y2)."""
    b, g, r = cv2.split(img.astype(np.int16))
    mask = (r > 200) & (g < tol) & (b < tol)
    ys, xs = np.nonzero(mask)
    if len(xs) < 40:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def solve_focal(box_h: float, feet_row: float, cy: float, alt: float,
                gimbal_deg: float, person_h: float) -> float | None:
    """The focal length at which apparent size and ground geometry agree.

    Both estimates move with the focal length in opposite directions, so the
    difference crosses zero exactly once and a bisection finds it.
    """
    def gap(focal: float) -> float:
        # distance from how big they look
        slant_size = person_h * focal / box_h
        # distance from where their feet land in the frame
        depression = math.radians(abs(gimbal_deg)) + math.atan((feet_row - cy) / focal)
        if depression <= 0.01:
            return 1e6
        ground = alt / math.tan(depression)
        slant_geom = math.hypot(alt, ground)
        return slant_size - slant_geom

    lo, hi = 100.0, 4000.0
    if gap(lo) * gap(hi) > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if gap(lo) * gap(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=Path, required=True)
    ap.add_argument("--alt", type=float, required=True, help="altitude during the run, m")
    ap.add_argument("--gimbal", type=float, required=True, help="gimbal pitch, deg (negative)")
    ap.add_argument("--height", type=float, required=True, help="subject height, m")
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    files = sorted(args.frames.glob("*.jpg"))[: args.limit]
    if not files:
        print(f"no frames in {args.frames}")
        return 1

    boxes, focals, sizes, geoms = [], [], [], []
    w = h = 0
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        h, w = img.shape[:2]
        box = red_box(img)
        if box is None:
            continue
        x1, y1, x2, y2 = box
        bh = y2 - y1
        if bh < 20 or y1 <= 2 or y2 >= h - 2:
            continue
        boxes.append(box)
        focal = solve_focal(bh, y2, h / 2.0, args.alt, args.gimbal, args.height)
        if focal:
            focals.append(focal)
        nominal = CameraModel(w, h).focal_px
        sizes.append(args.height * nominal / bh)
        depression = math.radians(abs(args.gimbal)) + math.atan((y2 - h / 2.0) / nominal)
        if depression > 0.01:
            geoms.append(math.hypot(args.alt, args.alt / math.tan(depression)))

    print(f"{len(files)} frames, {len(boxes)} with a recovered target box")
    if not boxes:
        print("  no red target rectangles found; were any frames drawn with a locked target?")
        return 1

    nominal = CameraModel(w, h).focal_px
    bh = [b[3] - b[1] for b in boxes]
    feet = [b[3] for b in boxes]
    print(f"  frame              {w} x {h}, nominal focal {nominal:.0f} px")
    print(f"  box height         {statistics.median(bh):.0f} px median")
    print(f"  feet row           {statistics.median(feet):.0f} of {h}")

    print("\n  the two distance estimates, at the SPEC-SHEET focal length")
    print(f"    from apparent size   {statistics.median(sizes):.1f} m slant")
    if geoms:
        print(f"    from ground geometry {statistics.median(geoms):.1f} m slant")
        disagree = abs(statistics.median(sizes) - statistics.median(geoms))
        print(f"    they disagree by     {disagree:.1f} m")

    if focals:
        f_med = statistics.median(focals)
        fov = math.degrees(2 * math.atan((math.hypot(w, h) / 2.0) / f_med))
        print(f"\n  focal length that reconciles them  {f_med:.0f} px "
              f"(sd {statistics.pstdev(focals):.0f})")
        print(f"  implied diagonal FOV               {fov:.1f} deg")
        print(f"  spec sheet                         {nominal:.0f} px")
        print(f"  difference                         {100 * (f_med - nominal) / nominal:+.0f}%")
        if not (40 < fov < 130):
            print("\n  That FOV is not physically plausible for this lens, so the")
            print("  assumption that is wrong is the GIMBAL PITCH, not the focal length.")
            print("  Log the gimbal angle the aircraft reports per frame and redo this.")
    else:
        print("\n  no focal length reconciles the two estimates at this gimbal pitch,")
        print("  which means the gimbal pitch used here is wrong. Log the actual")
        print("  reported pitch per frame rather than the commanded one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
