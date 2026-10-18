"""Detect a person on the real feed and hold them centred with yaw only.

    python scripts/follow_test.py --ground          # no flying, just detect
    python scripts/follow_test.py --alt 5 --hold 45 # hover and yaw-follow

This is the honest half of Drone-Bench's follow task. It answers the two
questions the simulator cannot: does the detector see a person in the real 720p
feed at altitude, and can the control loop keep them in frame.

It deliberately does NOT translate. Yaw is the only axis it commands, so the
aircraft turns on the spot and never flies toward the person it is watching.
Distance keeping comes later, once these numbers say whether it is worth trying.

Before committing to any perception result it checks that the feed is carrying a
picture. On flight 01 the camera session died and the bridge kept delivering
black frames at the normal rate, and 148 frames of "no person found" looked like
a detector fault for several minutes. See phantom_brain/video.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.detect import CvDnnYoloDetector, draw_detections  # noqa: E402
from phantom_brain.pilot import (Abort, Envelope, FlightSession, ensure_feed,
                                 runs_root)  # noqa: E402
from phantom_brain.protocol import VelocityCommand  # noqa: E402

DEADBAND = 0.06         # fraction of frame width; inside this, do not chase


def biggest(dets):
    """The nearest person is the one with the largest box. Good enough here."""
    return max(dets, key=lambda d: (d.x2 - d.x1) * (d.y2 - d.y1), default=None)


async def watch(bc: BridgeClient, det, seconds: float, *, fs: FlightSession | None,
                shots: Path, log) -> dict:
    """Run the detector on every frame; yaw toward the target when flying."""
    frames = seen = centred = 0
    latencies: list[float] = []
    # Held between frames rather than zeroed: frames arrive every ~200 ms and
    # this loop spins every 30 ms, so sending zeros here would drown the real
    # command and the aircraft would never turn.
    hold_cmd = VelocityCommand()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if fs is not None:
            fs.check(airborne=True)
        fr = bc.new_frame()
        if fr is None:
            if fs is not None:
                await bc.send_velocity(fs.clamp(hold_cmd))
            await asyncio.sleep(0.03)
            continue
        img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        frames += 1
        t0 = time.monotonic()
        dets = det.detect(img)
        latencies.append(time.monotonic() - t0)
        target = biggest(dets)
        yaw = 0.0
        off = None
        if target is not None:
            seen += 1
            cx = (target.x1 + target.x2) / 2
            off = (cx - img.shape[1] / 2) / img.shape[1]      # -0.5 .. +0.5
            if abs(off) < DEADBAND:
                centred += 1
            else:
                yaw = off * 2.5 * 25.0
        if fs is not None:
            hold_cmd = VelocityCommand(0, 0, 0, yaw)
            await bc.send_velocity(fs.clamp(hold_cmd))
        tel = bc.telemetry
        log.write(json.dumps({
            "t": round(time.monotonic(), 3), "seq": fr.seq, "people": len(dets),
            "offset": None if off is None else round(off, 4),
            "yaw_cmd": round(yaw, 2), "conf": None if target is None else round(target.conf, 3),
            "box_h": None if target is None else round(target.y2 - target.y1, 1),
            "alt": None if tel is None else round(tel.alt, 2),
            "hdg": None if tel is None else round(tel.heading, 1),
            "bat": None if tel is None else tel.battery,
        }) + "\n")
        log.flush()
        if frames % 3 == 0:
            cv2.imwrite(str(shots / f"{fr.seq:06d}.jpg"), draw_detections(img, dets, target))
        if frames % 10 == 0:
            print(f"  {frames:4d} frames  person in {100 * seen / frames:5.1f}%  "
                  f"offset {'--' if off is None else f'{off:+.2f}'}  yaw {yaw:+5.1f}", flush=True)
    lat = 1000 * sum(latencies) / len(latencies) if latencies else 0.0
    return {"frames": frames, "seen": seen, "centred": centred, "latency_ms": lat}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--alt", type=float, default=5.0)
    ap.add_argument("--hold", type=float, default=45.0, help="seconds of follow")
    ap.add_argument("--gimbal", type=float, default=-20.0)
    ap.add_argument("--ground", action="store_true", help="detect only, never arm")
    ap.add_argument("--conf", type=float, default=0.35)
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    repo = (runs_root().parent if os.environ.get("DRONE_RUNS_DIR")
            else Path(__file__).resolve().parents[2])
    shots = repo / "broll" / f"follow-{stamp}"
    shots.mkdir(parents=True, exist_ok=True)
    runs = runs_root() / "follow"
    runs.mkdir(parents=True, exist_ok=True)
    # NOT <stamp>.jsonl: FlightSession owns that name for telemetry when flying,
    # and two writers on one file interleave into unparseable JSON.
    perception_log = runs / f"{stamp}-perception.jsonl"
    log = open(perception_log, "w", encoding="utf-8")

    print("loading the detector")
    det = CvDnnYoloDetector(str(Path(__file__).resolve().parents[1] / "models" / "yolo11n.onnx"),
                           imgsz=640, conf=args.conf)
    print("detector ready")

    stats: dict = {}
    async with BridgeClient(args.url) as bc:
        await bc.wait_for_telemetry(15)
        try:
            await bc.record_start()
        except Exception:  # noqa: BLE001
            try:
                await bc.record_stop()
                await asyncio.sleep(1)
                await bc.record_start()
            except Exception:  # noqa: BLE001
                print("onboard recording unavailable, continuing")

        if args.ground:
            try:
                await ensure_feed(bc, expect_motion=False)
                stats = await watch(bc, det, args.hold, fs=None, shots=shots, log=log)
            except Abort as e:
                print(f"ABORT: {e}")
        else:
            env = Envelope(max_yaw_rate=25.0)
            async with FlightSession(bc, name="follow", envelope=env) as fs:
                await ensure_feed(bc, expect_motion=False)
                await fs.preflight()
                await fs.takeoff_and_take_control()
                await fs.climb_to(args.alt)
                await bc.set_gimbal_pitch(args.gimbal)
                fs.log(f"holding {args.alt:.0f} m, gimbal {args.gimbal:.0f} deg, "
                       f"yaw-only follow for {args.hold:.0f} s")
                stats = await watch(bc, det, args.hold, fs=fs, shots=shots, log=log)
                fs.note("follow", f"{stats['seen']}/{stats['frames']} frames with a person",
                        stats["frames"] > 0 and stats["seen"] / max(stats["frames"], 1) > 0.5,
                        **{k: round(v, 2) for k, v in stats.items()})
                await fs.return_to_origin()
            print(fs.summary())

        try:
            await bc.record_stop()
        except Exception:  # noqa: BLE001
            pass

    f = stats.get("frames", 0)
    print("\n" + "=" * 62)
    print(f"  frames processed   {f}")
    if f:
        print(f"  person detected    {stats['seen']} ({100 * stats['seen'] / f:.1f}%)")
        print(f"  centred in frame   {stats['centred']} ({100 * stats['centred'] / f:.1f}%)")
        print(f"  detector latency   {stats['latency_ms']:.0f} ms mean "
              f"({1000 / max(stats['latency_ms'], 1e-6):.1f} fps ceiling)")
    print(f"  perception log     {perception_log}")
    print(f"  annotated frames   {shots}")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
