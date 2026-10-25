"""Print the calibration targets, at 300 dpi with the DPI recorded in the PNG.

    python scripts/print_targets.py      -> brain/targets/*.png

  charuco_7x5_30mm.png    one Letter page. Glue it to something flat (a
                          clipboard, foam board). Same design as Onspec's,
                          so a board already printed for Onspec works.
  ground_marker_20.png    four Letter pages, one ArUco marker each, 190 mm.
  ... _23.png             They go on the grass for the camera-truth hover.

Print at 100% / Actual size, then CHECK WITH A RULER: ChArUco squares 30.0 mm,
ground markers 190 mm edge to edge of the black. If the printer scaled them,
every distance downstream scales with it, so fix the print, not the code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.calib import (ARUCO_DICT, BOARD_MARKER_MM, BOARD_SQUARE_MM,  # noqa: E402
                                 BOARD_SQUARES, GROUND_IDS, charuco_board)

DPI = 300
PX = DPI / 25.4
LETTER = (round(215.9 * PX), round(279.4 * PX))     # width, height in px
GROUND_MARKER_MM = 190.0
OUT = Path(__file__).resolve().parents[1] / "targets"


def mm(v: float) -> int:
    return int(round(v * PX))


def page() -> np.ndarray:
    return np.full((LETTER[1], LETTER[0]), 255, np.uint8)


def text(img: np.ndarray, s: str, x_mm: float, y_mm: float, scale: float = 1.1) -> None:
    cv2.putText(img, s, (mm(x_mm), mm(y_mm)), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, 2, cv2.LINE_AA)


def charuco_page() -> np.ndarray:
    p = page()
    cols, rows = BOARD_SQUARES
    w, h = mm(cols * BOARD_SQUARE_MM), mm(rows * BOARD_SQUARE_MM)
    board = charuco_board().generateImage((w, h), marginSize=0, borderBits=1)
    # Landscape board on a portrait page: rotate so the squares stay 30 mm.
    board = cv2.rotate(board, cv2.ROTATE_90_CLOCKWISE)
    y0, x0 = mm(25), (LETTER[0] - board.shape[1]) // 2
    p[y0:y0 + board.shape[0], x0:x0 + board.shape[1]] = board
    text(p, f"ChArUco {cols}x{rows}, {BOARD_SQUARE_MM:.0f} mm squares, {BOARD_MARKER_MM:.0f} mm markers,", 15, 262)
    text(p, "DICT_4X4_50. Print at 100%. Check one square with a ruler: 30.0 mm.", 15, 271)
    return p


def ground_page(mid: int) -> np.ndarray:
    p = page()
    d = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    side = mm(GROUND_MARKER_MM)
    m = cv2.aruco.generateImageMarker(d, mid, side, borderBits=1)
    x0, y0 = (LETTER[0] - side) // 2, mm(40)
    p[y0:y0 + side, x0:x0 + side] = m
    # The arrow is the convention calib.GroundMarkers assumes: top edge to north.
    cx = LETTER[0] // 2
    cv2.arrowedLine(p, (cx, mm(34)), (cx, mm(12)), 0, 8, tipLength=0.35)
    text(p, "NORTH", 118, 20, 1.2)
    text(p, f"Ground marker id {mid}. Black square must measure {GROUND_MARKER_MM:.0f} mm.", 15, 245)
    text(p, "Lay flat, arrow pointing north. Weigh the corners down.", 15, 256)
    text(p, "Measure centre-to-centre distances with a tape and write them down.", 15, 267)
    return p


def main() -> None:
    OUT.mkdir(exist_ok=True)
    Image.fromarray(charuco_page()).save(OUT / "charuco_7x5_30mm.png", dpi=(DPI, DPI))
    for mid in GROUND_IDS:
        Image.fromarray(ground_page(mid)).save(OUT / f"ground_marker_{mid}.png", dpi=(DPI, DPI))
    print(f"wrote {len(GROUND_IDS) + 1} pages to {OUT}")
    print("print each at 100% / Actual size on Letter, then check with a ruler")


if __name__ == "__main__":
    main()
