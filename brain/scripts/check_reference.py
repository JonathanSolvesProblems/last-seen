"""Does the photo from this morning match the person the drone sees from the air?

    python scripts/check_reference.py --reference park_photo.jpg

The one link in Last Seen that no earlier flight measured. Flight 02 showed the
matcher separates me from every impostor when the reference is an AERIAL crop
(223 of 223), but a real search starts from a photo taken at eye level, and the
view from 8 m up and 15 m out is a different picture of the same clothes.

Climbs to --alt, turns a full circle looking, and prints every candidate with
its match score and band. Stand 10 to 20 m from takeoff, in the open, still.
Nothing here follows anyone or asks anyone; it only measures.

Reads well:  you score "strong" (0.60 or more) and nobody else does.
Rethink:     you land "ambiguous": the mission still works (the agent looks
             closer before asking) but say so in the writeup.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.camera import CameraModel  # noqa: E402
from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.detect import CvDnnYoloDetector  # noqa: E402
from phantom_brain.pilot import Envelope, FlightSession, ensure_feed, runs_root  # noqa: E402
from phantom_brain.reid import ReIdMatcher  # noqa: E402
from phantom_brain.runtime import MissionRuntime  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--alt", type=float, default=8.0)
    ap.add_argument("--turns", type=int, default=1)
    args = ap.parse_args()
    ref = cv2.imread(str(args.reference))
    if ref is None:
        print(f"cannot read {args.reference}")
        return 1
    out = runs_root() / "check-reference" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "reference.jpg"), ref)
    print(f"plan: climb to {args.alt:.0f} m, turn {360 * args.turns} deg on the spot, measure, come back. "
          f"No translation.")
    env = Envelope(max_alt=args.alt + 4, max_radius=12, max_speed=1.0, max_yaw_rate=25.0)
    async with BridgeClient(args.url) as bc:
        await bc.wait_for_telemetry(15)
        async with FlightSession(bc, name="check-reference", envelope=env) as fs:
            await ensure_feed(bc, expect_motion=False)
            await fs.preflight()
            await fs.takeoff_and_take_control()
            await fs.climb_to(args.alt)
            fr = await bc.next_frame(timeout=5)
            rt = MissionRuntime(bc, fs, detector=CvDnnYoloDetector(str(ROOT / "models" / "yolo11n.onnx"),
                                                                   imgsz=960, conf=0.35),
                                matcher=ReIdMatcher(ref), cam=CameraModel(fr.w, fr.h),
                                area=[(-12, -12), (12, -12), (12, 12), (-12, 12)], alt=args.alt,
                                out_dir=out, speed=1.0)
            for _ in range(args.turns):
                r = await rt.scan_here(360.0, 18.0)
            rt.save()
            await fs.return_to_origin()
    cands = r["candidates"]
    print("\n" + "=" * 64)
    print(f"  {'id':5} {'match':>6} {'band':>10} {'appearance':>11} {'clothing':>9} {'box px':>7} {'seen':>5}")
    for c in cands:
        print(f"  {c['id']:5} {c['match']:6.2f} {c['match_band']:>10} "
              f"{c['appearance'] if c['appearance'] is not None else '-':>11} {c['clothing']:9.2f} "
              f"{c['box_height_px']:7d} {c['times_seen']:5d}")
    if not cands:
        print("  nobody seen: stand in the open, 10 to 20 m out, and run it again")
    print(f"  crops: {out / 'crops'}")
    print("=" * 64)
    (out / "result.json").write_text(json.dumps(cands, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
