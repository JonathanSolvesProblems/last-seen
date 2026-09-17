"""Tune the grass/obstacle classifier on real survey frames.

    python scripts/tune_grass.py runs/<run>/frames/*.jpg
    python scripts/tune_grass.py path/to/still.jpg

Drag the trackbars until trees, paths and benches are white and grass is
black, then copy the printed values into GrassClassifier (reconstruct.py)
or override them in code before the mission.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.tasks.reconstruct import GrassClassifier  # noqa: E402


def main() -> None:
    files = [f for pat in sys.argv[1:] for f in glob.glob(pat)]
    if not files:
        print(__doc__)
        return
    idx = 0
    clf = GrassClassifier()
    win = "tune_grass (n = next image, q = quit)"
    cv2.namedWindow(win)
    cv2.createTrackbar("h_lo", win, clf.h_lo, 180, lambda v: None)
    cv2.createTrackbar("h_hi", win, clf.h_hi, 180, lambda v: None)
    cv2.createTrackbar("s_min", win, clf.s_min, 255, lambda v: None)
    cv2.createTrackbar("v_min", win, clf.v_min, 255, lambda v: None)
    while True:
        img = cv2.imread(files[idx])
        if img is None:
            idx = (idx + 1) % len(files)
            continue
        clf.h_lo = cv2.getTrackbarPos("h_lo", win)
        clf.h_hi = cv2.getTrackbarPos("h_hi", win)
        clf.s_min = cv2.getTrackbarPos("s_min", win)
        clf.v_min = cv2.getTrackbarPos("v_min", win)
        mask = clf.obstacle_mask(img)
        vis = img.copy()
        vis[mask > 0] = (0.4 * vis[mask > 0] + (0, 0, 153)).astype("uint8")
        side = cv2.hconcat([vis, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)])
        h = side.shape[0]
        if h > 720:
            side = cv2.resize(side, None, fx=720 / h, fy=720 / h)
        cv2.putText(side, f"{Path(files[idx]).name}  h[{clf.h_lo},{clf.h_hi}] s>={clf.s_min} v>={clf.v_min}",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(win, side)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("q") or k == 27:
            break
        if k == ord("n"):
            idx = (idx + 1) % len(files)
    print(f"GrassClassifier(h_lo={clf.h_lo}, h_hi={clf.h_hi}, s_min={clf.s_min}, v_min={clf.v_min})")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
