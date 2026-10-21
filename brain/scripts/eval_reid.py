"""Re-identification on real aerial footage: flight 02's onboard 2.7K clip.

    python scripts/eval_reid.py            -> results/reid_flight02.json, results/reid_flight02_sheet.jpg

Samples the clip every 2 s, detects people with YOLO11n on OpenCV 5's DNN
engine, and scores every crop against a reference with the fused matcher
(OpenCV Model Zoo re-ID network + clothing bands).

Labels, and how I know them: on 2026-09-30 I was the only person standing in
the open; every other detection was checked by eye on the contact sheet this
script writes (`reid_flight02_sheet.jpg`, every crop with its timestamp). The
impostors are two posts and a passing cyclist, all detected as "person". They
are listed in IMPOSTORS below, by timestamp, so anyone can look them up.

What this does NOT show: the reference here is a crop from the same flight.
A real search starts from a photo taken on the ground, which is a different
view of the same clothes; flight 03's check_reference.py measures that.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.detect import CvDnnYoloDetector  # noqa: E402
from phantom_brain.reid import ASK_AT, LOOK_AT, ReIdMatcher  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CLIP = Path.home() / "Videos/DroneProject/flight-02-2026-09-30/drone/154433_follow-me_20260930-153512_DJI_0003.mov"
RESULTS = ROOT.parent / "results"
CACHE = ROOT / "tmp" / "reid"
# (seconds into the clip, detection index): checked by eye on the contact sheet
IMPOSTORS = {(0.0, 0), (2.0, 1), (4.0, 1), (12.0, 1), (16.0, 1), (42.0, 1), (354.0, 1), (356.0, 1)}
REFERENCES = [(8.0, 0), (150.0, 0), (300.0, 0)]


def extract() -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    index = CACHE / "crops.json"
    if index.exists():
        return json.loads(index.read_text())
    det = CvDnnYoloDetector(str(ROOT / "models" / "yolo11n.onnx"), imgsz=1280, conf=0.35)
    cap = cv2.VideoCapture(str(CLIP))
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    rows = []
    for t in np.arange(0, n / fps, 2.0):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok:
            continue
        for k, d in enumerate(det.detect(img)):
            crop = d.crop(img, pad=0.1)
            if crop.size:
                cv2.imwrite(str(CACHE / f"crop_{t:06.1f}_{k}.jpg"), crop)
                rows.append({"t": round(float(t), 1), "k": k, "h": round(d.h), "conf": round(d.conf, 2)})
    index.write_text(json.dumps(rows, indent=1))
    return rows


def main() -> int:
    if not CLIP.exists() and not (CACHE / "crops.json").exists():
        print(f"need the flight 02 clip at {CLIP}")
        return 1
    rows = extract()
    crops = {(r["t"], r["k"]): cv2.imread(str(CACHE / f"crop_{r['t']:06.1f}_{r['k']}.jpg")) for r in rows}
    runs = []
    for ref_key in REFERENCES:
        m = ReIdMatcher(crops[ref_key])
        me, them = [], []
        for key, c in crops.items():
            if key == ref_key:
                continue
            (them if key in IMPOSTORS else me).append(m.explain(c).fused)
        runs.append({
            "reference_t": ref_key[0], "me_n": len(me), "impostors_n": len(them),
            "me_min": round(min(me), 3), "me_p05": round(float(np.percentile(me, 5)), 3),
            "me_median": round(float(np.median(me)), 3),
            "impostors_max": round(max(them), 3), "impostors_median": round(float(np.median(them)), 3),
            "me_at_or_above_ask": sum(x >= ASK_AT for x in me),
            "impostors_at_or_above_look": sum(x >= LOOK_AT for x in them),
            "separable": min(me) > max(them)})
    # contact sheet: every crop, timestamped, impostors framed red
    tiles = []
    for (t, k), c in crops.items():
        tile = cv2.resize(c, (60, max(1, int(60 * c.shape[0] / max(c.shape[1], 1)))))[:150]
        tile = cv2.copyMakeBorder(tile, 0, 150 - tile.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(40, 40, 40))
        if (t, k) in IMPOSTORS:
            cv2.rectangle(tile, (0, 0), (59, 149), (0, 0, 255), 2)
        cv2.putText(tile, f"{t:.0f}", (2, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
        tiles.append(tile)
    per = 30
    strips = [np.hstack(tiles[i:i + per] + [np.zeros_like(tiles[0])] * (per - len(tiles[i:i + per])))
              for i in range(0, len(tiles), per)]
    RESULTS.mkdir(exist_ok=True)
    cv2.imwrite(str(RESULTS / "reid_flight02_sheet.jpg"), np.vstack(strips))
    out = {"source": "flight 02 onboard clip, 2026-09-30, 6 m altitude, 2704x1520, sampled every 2 s",
           "detector": "YOLO11n, OpenCV 5 DNN, conf 0.35", "crops": len(crops),
           "impostors": sorted([list(k) for k in IMPOSTORS]),
           "labels": "checked by eye on reid_flight02_sheet.jpg (impostors framed red)",
           "matcher": "ReIdMatcher: OpenCV zoo person_reid_youtu_2021nov (cosine) 0.7 + HSV clothing bands 0.3",
           "thresholds": {"ask": ASK_AT, "look": LOOK_AT},
           "limitation": "reference is a crop from the same flight, not a ground-level photo",
           "runs": runs}
    (RESULTS / "reid_flight02.json").write_text(json.dumps(out, indent=2))
    for r in runs:
        print(f"ref t={r['reference_t']:5.0f}: me median {r['me_median']:.3f} min {r['me_min']:.3f} | "
              f"impostors max {r['impostors_max']:.3f} | separable {r['separable']} | "
              f"{r['me_at_or_above_ask']}/{r['me_n']} of me at ask level")
    return 0


if __name__ == "__main__":
    sys.exit(main())
