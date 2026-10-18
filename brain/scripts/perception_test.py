"""Check detection + reference matching on photos or a video before flying.

    python scripts/perception_test.py --ref data/reference.jpg photo1.jpg photo2.jpg
    python scripts/perception_test.py --ref data/reference.jpg flight.mp4 --matcher claude
    python scripts/perception_test.py runs/<run>/frames/*.jpg      # detection only

Take the reference photo with your phone in the clothes you will wear, full
body, similar light. Then take a few photos from a balcony or a hill at
20 to 40 m (the drone's view) with you and someone else in them, and confirm
the box on you scores highest. Press any key to step, q to quit.
"""

from __future__ import annotations

import argparse
import glob
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.detect import (ClaudeMatcher, ColorHistogramMatcher, CvDnnYoloDetector,  # noqa: E402
                                  YoloDetector, draw_detections)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--ref", default=None, help="reference photo of the target")
    ap.add_argument("--matcher", choices=["color", "claude"], default="color")
    ap.add_argument("--weights", default=str(Path(__file__).resolve().parents[1] / "models" / "yolo11n.onnx"))
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--every", type=int, default=5, help="for video: run every Nth frame")
    args = ap.parse_args()

    det = CvDnnYoloDetector(args.weights, args.imgsz, args.conf) if args.weights.endswith(".onnx") \
        else YoloDetector(args.weights, args.imgsz, args.conf)
    matcher = None
    if args.ref:
        ref = cv2.imread(args.ref)
        if ref is None:
            sys.exit(f"cannot read {args.ref}")
        # use the largest person in the reference photo as the reference crop
        rdets = det.detect(ref)
        if rdets:
            big = max(rdets, key=lambda d: d.w * d.h)
            ref = big.crop(ref)
            print(f"reference crop: {int(big.w)}x{int(big.h)} px")
        matcher = ClaudeMatcher(ref) if args.matcher == "claude" else ColorHistogramMatcher(ref)

    files = [f for pat in args.inputs for f in glob.glob(pat)] or args.inputs
    for f in files:
        if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
            cap = cv2.VideoCapture(f)
            i = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                i += 1
                if i % args.every:
                    continue
                if not show(frame, f"{Path(f).name} #{i}", det, matcher):
                    return
            cap.release()
        else:
            img = cv2.imread(f)
            if img is None:
                print("skip", f)
                continue
            if not show(img, Path(f).name, det, matcher):
                return
    cv2.destroyAllWindows()


def show(img, title, det, matcher) -> bool:
    t0 = time.perf_counter()
    dets = det.detect(img)
    ms = (time.perf_counter() - t0) * 1000
    best = None
    if matcher is not None:
        for d in dets:
            d.match = matcher.score(d.crop(img))
        if dets:
            best = max(dets, key=lambda d: d.match or 0)
    vis = draw_detections(img, dets, best)
    txt = f"{title}: {len(dets)} persons, {ms:.0f} ms" + (f", best match {best.match:.2f}" if best else "")
    cv2.putText(vis, txt, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
    cv2.putText(vis, txt, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
    print(txt, [round(d.match, 2) if d.match is not None else None for d in dets])
    h = vis.shape[0]
    if h > 900:
        vis = cv2.resize(vis, None, fx=900 / h, fy=900 / h)
    cv2.imshow("perception_test", vis)
    return (cv2.waitKey(0) & 0xFF) not in (ord("q"), 27)


if __name__ == "__main__":
    main()
