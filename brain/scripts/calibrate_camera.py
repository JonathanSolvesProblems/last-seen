"""Measure the camera's focal length instead of trusting the spec sheet.

    python scripts/calibrate_camera.py --height 1.80 --distance 10.0

Stand a measured distance in front of the aircraft, face on, whole body in
frame, gimbal level. It watches you for a few seconds and solves

    focal_px = bbox_height_px * distance_m / your_height_m

then reports the diagonal field of view that implies, and how far that is from
the 94 degrees the Phantom 3 Standard is specified at.

Why this matters for follow-me. The follower does not chase pixels: it projects
the detection's feet pixel onto the ground to get a real position, and flies to
a standoff point from there. That projection scales with focal length, so a 10%
focal error is a 10% standoff error. Told to hold 8 m it would hold 7.2 or 8.8,
and the person it is holding away from is the pilot.

Everything happens on the ground. Nothing arms.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.camera import P3S_DIAGONAL_FOV_DEG, CameraModel  # noqa: E402
from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.detect import CvDnnYoloDetector, draw_detections  # noqa: E402
from phantom_brain.video import FeedMonitor  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "configs" / "camera.json"


def diagonal_fov(focal_px: float, w: int, h: int) -> float:
    return math.degrees(2 * math.atan((math.hypot(w, h) / 2.0) / focal_px))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--height", type=float, required=True,
                    help="your height in metres, shoes on")
    ap.add_argument("--distance", type=float, required=True,
                    help="measured distance from the CAMERA to you, in metres")
    ap.add_argument("--seconds", type=float, default=12.0, help="seconds of measuring")
    ap.add_argument("--wait", type=float, default=90.0,
                    help="seconds to wait for you to get into position first")
    ap.add_argument("--conf", type=float, default=0.4)
    ap.add_argument("--save", action="store_true", help="write configs/camera.json")
    args = ap.parse_args()

    print(f"stand {args.distance:.1f} m in front of the camera, whole body in frame, "
          f"and hold still for {args.seconds:.0f} s")
    det = CvDnnYoloDetector(str(Path(__file__).resolve().parents[1] / "models" / "yolo11n.onnx"),
                           imgsz=640, conf=args.conf)
    shots = Path(__file__).resolve().parents[2] / "broll" / f"calib-{time.strftime('%Y%m%d-%H%M%S')}"
    shots.mkdir(parents=True, exist_ok=True)

    focals: list[float] = []
    sizes: set[tuple[int, int]] = set()
    mon = FeedMonitor()
    async with BridgeClient(args.url) as bc:
        await bc.wait_for_telemetry(15)
        # Wait for someone to walk into frame rather than failing and needing a
        # second run. Standing in the right place takes longer than the measuring
        # does, and the first attempt failed with the camera buried in grass.
        print(f"waiting up to {args.wait:.0f} s for a whole person to appear in frame", flush=True)
        deadline = time.monotonic() + args.wait
        ready = False
        last_note = 0.0
        while time.monotonic() < deadline and not ready:
            fr = bc.new_frame()
            if fr is None:
                await asyncio.sleep(0.05)
                continue
            img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            mon.add(img)
            h, w = img.shape[:2]
            dets = det.detect(img)
            whole = [d for d in dets if d.y1 > 2 and d.y2 < h - 2]
            if whole:
                ready = True
                print("  got you, hold still", flush=True)
                break
            if time.monotonic() - last_note > 5:
                last_note = time.monotonic()
                clipped = len(dets) - len(whole)
                if clipped:
                    print(f"  see {clipped} person(s) but cut off by the frame edge; "
                          f"step back or raise the camera", flush=True)
                else:
                    print("  nobody in frame yet", flush=True)
        if not ready:
            print(f"\nnobody appeared in {args.wait:.0f} s. Check the camera is not "
                  f"pointed into grass or the ground: {shots}")
            fr = bc.frame
            if fr is not None:
                (shots / "what-the-camera-sees.jpg").write_bytes(fr.jpeg)
                print(f"  saved the current view to {shots / 'what-the-camera-sees.jpg'}")
            return 1

        end = time.monotonic() + args.seconds
        frames = 0
        while time.monotonic() < end:
            fr = bc.new_frame()
            if fr is None:
                await asyncio.sleep(0.03)
                continue
            img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            mon.add(img)
            frames += 1
            h, w = img.shape[:2]
            sizes.add((w, h))
            dets = det.detect(img)
            if not dets:
                continue
            person = max(dets, key=lambda d: d.h)
            # Reject anyone clipped by the frame edge: a cut-off box is shorter
            # than the person and would bias the focal length low.
            if person.y1 <= 2 or person.y2 >= h - 2:
                continue
            focals.append(person.h * args.distance / args.height)
            if frames % 5 == 0:
                cv2.imwrite(str(shots / f"{fr.seq:06d}.jpg"),
                            draw_detections(img, dets, person))

    v = mon.verdict()
    if not v.ok:
        print(f"\nvideo feed unusable: {v.reason}")
        return 1
    if len(focals) < 10:
        print(f"\nonly {len(focals)} usable detections. Stand further back so your whole "
              f"body is inside the frame, and check the light.")
        return 1

    w, h = max(sizes)
    f_med = statistics.median(focals)
    f_sd = statistics.pstdev(focals)
    fov = diagonal_fov(f_med, w, h)
    nominal = CameraModel(w, h).focal_px
    err = 100 * (f_med - nominal) / nominal

    print(f"\n  frame                {w} x {h}")
    print(f"  usable detections    {len(focals)}")
    print(f"  focal length         {f_med:.1f} px  (sd {f_sd:.1f}, "
          f"{100 * f_sd / f_med:.1f}%)")
    print(f"  implied diagonal FOV {fov:.1f} deg")
    print(f"  spec sheet           {P3S_DIAGONAL_FOV_DEG:.1f} deg -> {nominal:.1f} px")
    print(f"  difference           {err:+.1f}%")
    print(f"\n  at 8 m standoff that is {abs(err) * 0.08:.2f} m of range error")
    print(f"  frames saved to {shots}")

    if args.save:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "measured": time.strftime("%Y-%m-%d %H:%M:%S"),
            "frame_w": w, "frame_h": h,
            "focal_px": round(f_med, 1),
            "focal_px_sd": round(f_sd, 1),
            "diagonal_fov_deg": round(fov, 2),
            "samples": len(focals),
            "method": f"person {args.height} m tall at {args.distance} m",
        }, indent=2), encoding="utf-8")
        print(f"  wrote {OUT}")
    else:
        print("\n  re-run with --save to write configs/camera.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
