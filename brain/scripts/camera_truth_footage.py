"""Camera truth from footage already flown: the printed markers seen in the onboard video.

    python scripts/camera_truth_footage.py

For every second of onboard 2.7K video in which an ArUco ground marker (DICT_4X4_50,
ids 20 to 23, 190 mm edge, ruler-checked) is detected, solve the camera's pose from
that one marker (`solvePnP`, SOLVEPNP_IPPE_SQUARE, then `solvePnPRefineLM`) and compare:

  * the camera's measured pitch (optical axis below the horizon) with the gimbal pitch
    the aircraft reported in telemetry at that moment
  * the camera's measured height above the marker with the barometric altitude reported

A single marker needs no tape-measured layout, only its printed size. Intrinsics are the
spec-sheet 94 degree diagonal field of view with no distortion model (the ChArUco
calibration was not flown), so this is a lower bound on what the method can do.

Clip and telemetry are aligned on their common end: the SD clip stops when the
mission script stops recording, which is also when the telemetry log is closed.
Writes results/camera_truth_flight03.json.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from phantom_brain.camera import CameraModel  # noqa: E402

FOOTAGE = Path.home() / "Videos" / "DroneProject" / "flight-03-2026-10-25" / "drone"
RUNS = ROOT / "runs" / "last-seen"
OUT = ROOT.parent / "results" / "camera_truth_flight03.json"
MARKER_M = 0.190
CLIPS = {
    "run1": ("run1_1205_search-with-ground-photo_no-match_recalled_DJI_0012.MOV", "20261025-120520.jsonl"),
    "run2": ("run2_1211_found-asked-confirmed-followed-90s_DJI_0013.MOV", "20261025-121055.jsonl"),
}


def telemetry(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open(encoding="utf-8")]


def at(tel: list[dict], t: float) -> dict:
    return min(tel, key=lambda r: abs(r["t"] - t))


def pose(corners: np.ndarray, K: np.ndarray) -> tuple[float, float] | None:
    h = MARKER_M / 2
    obj = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float32)
    ok, rvec, tvec = cv2.solvePnP(obj, corners.astype(np.float32), K, None, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    if not ok:
        return None
    rvec, tvec = cv2.solvePnPRefineLM(obj, corners.astype(np.float32), K, None, rvec, tvec)
    R, _ = cv2.Rodrigues(rvec)
    cam_in_marker = (-R.T @ tvec).ravel()          # camera centre, marker frame (z = marker normal)
    axis = R.T @ np.array([0.0, 0.0, 1.0])         # optical axis, marker frame
    height = abs(cam_in_marker[2])
    # pitch below the horizon: the axis points toward the ground plane
    pitch = math.degrees(math.asin(min(1.0, abs(axis[2]))))
    return height, pitch


def main() -> int:
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), cv2.aruco.DetectorParameters())
    rows = []
    for run, (clip, tel_name) in CLIPS.items():
        tel = telemetry(RUNS / tel_name)
        cap = cv2.VideoCapture(str(FOOTAGE / clip))
        fps = cap.get(cv2.CAP_PROP_FPS)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur = n / fps
        t_end = tel[-1]["t"]
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cam = CameraModel(w, h)
        K = np.array([[cam.focal_px, 0, cam.cx], [0, cam.focal_px, cam.cy], [0, 0, 1]], np.float64)
        for f in range(0, n, max(1, int(fps // 2))):          # two frames a second
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, img = cap.read()
            if not ok:
                break
            c, ids, _ = det.detectMarkers(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
            if ids is None:
                continue
            clip_t = f / fps
            tr = at(tel, t_end - (dur - clip_t))
            if tr["alt"] < 2.0:          # on the ground or lifting off: the gimbal reading means nothing yet
                continue
            for cc, mid in zip(c, ids.ravel()):
                if not 20 <= mid <= 23:
                    continue
                side = float(np.mean([np.linalg.norm(cc[0][k] - cc[0][(k + 1) % 4]) for k in range(4)]))
                p = pose(cc[0], K)
                if p is None:
                    continue
                rows.append({"run": run, "clip_s": round(clip_t, 2), "marker": int(mid), "side_px": round(side, 1),
                             "measured_pitch_deg": round(p[1], 2), "reported_gimbal_deg": tr["gimbal"],
                             "measured_height_m": round(p[0], 2), "reported_alt_m": round(tr["alt"], 2)})
    if not rows:
        print("no marker detections at altitude")
        return 1
    dp = [r["measured_pitch_deg"] - abs(r["reported_gimbal_deg"]) for r in rows]
    dh = [r["measured_height_m"] - r["reported_alt_m"] for r in rows]
    summary = {
        "detections": len(rows),
        "frames_seconds": len({(r["run"], r["clip_s"]) for r in rows}),
        "reported_gimbal_deg_values": sorted({r["reported_gimbal_deg"] for r in rows}),
        "measured_pitch_median_deg": round(statistics.median(r["measured_pitch_deg"] for r in rows), 1),
        "pitch_gap_median_deg": round(statistics.median(dp), 1),
        "pitch_gap_iqr_deg": [round(float(np.percentile(dp, 25)), 1), round(float(np.percentile(dp, 75)), 1)],
        "reported_alt_median_m": round(statistics.median(r["reported_alt_m"] for r in rows), 1),
        "measured_height_median_m": round(statistics.median(r["measured_height_m"] for r in rows), 1),
        "height_gap_median_m": round(statistics.median(dh), 1),
        "marker_side_px_median": round(statistics.median(r["side_px"] for r in rows), 1),
        "intrinsics": "spec sheet, 94 deg diagonal, no distortion model (ChArUco calibration not flown)",
        "alignment": "clip end = telemetry end (both stop when the mission script stops recording)",
    }
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
