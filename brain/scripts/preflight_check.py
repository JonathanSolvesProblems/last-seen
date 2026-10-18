"""Every ground check, in one command, before anything flies.

    python scripts/preflight_check.py
    python scripts/preflight_check.py --skip-detector     # faster, no ONNX load

Flight 01 spent roughly 20 of its 25 minutes of battery discovering, one at a
time and in a park, things that are all checkable on the ground in 40 seconds:
the bridge was bound to the wrong Wi-Fi, the mode switch was in the wrong
position and could not reach F, the camera had stopped sending pixels, and the
onboard recording refused because it was already running.

This asks all of it at once and prints GO or NO-GO with the reason. Run it while
still standing next to the car.

Nothing here arms the aircraft or commands any motion. The gimbal check moves the
camera, which is the only thing that physically moves.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.client import BridgeClient, BridgeError  # noqa: E402
from phantom_brain.video import FeedMonitor  # noqa: E402


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


def show(c: Check) -> None:
    mark = "ok  " if c.ok else ("STOP" if c.blocking else "warn")
    print(f"  {mark}  {c.name:<22} {c.detail}", flush=True)


async def run(args) -> list[Check]:
    out: list[Check] = []
    try:
        bc = BridgeClient(args.url)
        await bc.connect()
    except Exception as e:  # noqa: BLE001
        out.append(Check("bridge", False,
                         f"cannot reach {args.url} ({e}). Start the bridge in the app, "
                         f"then: adb forward tcp:8765 tcp:8765"))
        show(out[-1])
        return out
    out.append(Check("bridge", True, f"connected to {args.url}"))
    show(out[-1])

    try:
        t = await bc.wait_for_telemetry(15)
    except asyncio.TimeoutError:
        out.append(Check("aircraft link", False,
                         "no telemetry. If the app shows CONTROLLER: NO LINK while DJI GO "
                         "connects fine, the phone is bound to the wrong Wi-Fi: check the "
                         "app log for 'bound to wlan0' and not 'swlan0' (flight 01, bug 1)"))
        show(out[-1])
        await bc.close()
        return out
    product = bc.status.get("product") or "unknown"
    out.append(Check("aircraft link", True, f"{product}, telemetry flowing"))
    show(out[-1])

    out.append(Check("GPS", t.gps_fix and t.gps_sats >= 7,
                     f"{t.gps_sats} satellites, fix={t.gps_fix}"))
    show(out[-1])
    out.append(Check("home point", t.home_set, "set" if t.home_set else "NOT set"))
    show(out[-1])
    out.append(Check("aircraft battery", t.battery >= 40,
                     f"{t.battery}%" + ("" if t.battery >= 40 else ", want 40+ for a full sortie")))
    show(out[-1])

    # Multiple Flight Mode ships off, and without it S1 can never reach F.
    try:
        await bc.enable_multi_mode()
        out.append(Check("multiple flight mode", True, "enabled over the SDK (no DJI GO needed)"))
    except BridgeError as e:
        out.append(Check("multiple flight mode", False, str(e)))
    show(out[-1])

    switch = t.rc_mode_switch or "?"
    out.append(Check("RC mode switch", switch == "F",
                     f"reads {switch}. TOP=P  MIDDLE=A (Atti, avoid)  BOTTOM=F",
                     blocking=False))
    show(out[-1])

    # The check that flight 01 did not have: are the frames carrying a picture?
    async def sample_feed(seconds: float) -> "FeedMonitor":
        mon = FeedMonitor()
        end = time.monotonic() + seconds
        while time.monotonic() < end and mon.total < mon.window:
            fr = bc.new_frame()
            if fr is None:
                await asyncio.sleep(0.03)
                continue
            mon.add(cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR))
        return mon

    # On the ground a still scene is not evidence of a dead camera; a black one is.
    v = (await sample_feed(args.feed_seconds)).verdict(expect_motion=False)
    if not v.ok and not args.no_recover:
        # A dead camera session is the single most common field failure, and the
        # fix is always the same. Do it here rather than making someone tap a
        # phone in a park, and say so, because a silent retry hides a real fault.
        print(f"  ....  video feed            {v.reason}")
        print("  ....  recovering             cycling the aircraft connection", flush=True)
        try:
            await bc.reconnect_product()
            await asyncio.sleep(3)
            v = (await sample_feed(args.feed_seconds)).verdict(expect_motion=False)
            if v.ok:
                v = type(v)(True, "feed healthy after a reconnect", v.frames,
                            v.blank_fraction, v.distinct_fraction, v.mean_luma)
        except BridgeError as e:
            print(f"  ....  recovery failed       {e}")
    out.append(Check("video feed", v.ok, str(v)))
    show(out[-1])

    if not args.skip_detector and v.ok:
        from phantom_brain.detect import CvDnnYoloDetector  # noqa: PLC0415
        det = CvDnnYoloDetector(
            str(Path(__file__).resolve().parents[1] / "models" / "yolo11n.onnx"),
            imgsz=640, conf=0.35)
        lat: list[float] = []
        people = 0
        frames = 0
        end = time.monotonic() + 6
        while time.monotonic() < end and frames < 20:
            fr = bc.new_frame()
            if fr is None:
                await asyncio.sleep(0.03)
                continue
            img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            frames += 1
            t0 = time.monotonic()
            people += len(det.detect(img))
            lat.append(time.monotonic() - t0)
        ms = 1000 * sum(lat) / len(lat) if lat else 0
        out.append(Check("detector", frames > 0,
                         f"{frames} frames at {ms:.0f} ms ({1000 / max(ms, 1e-6):.0f} fps ceiling), "
                         f"{people} person detections",
                         blocking=False))
        show(out[-1])

    try:
        await bc.set_gimbal_pitch(-45)
        await asyncio.sleep(2)
        await bc.set_gimbal_pitch(0)
        out.append(Check("gimbal", True, "moved to -45 and back"))
    except BridgeError as e:
        out.append(Check("gimbal", False, str(e), blocking=False))
    show(out[-1])

    # Recording refuses with "Camera is busy" when a previous run left it rolling.
    try:
        await bc.record_start()
        await asyncio.sleep(1)
        await bc.record_stop()
        out.append(Check("SD recording", True, "started and stopped cleanly"))
    except BridgeError as e:
        try:
            await bc.record_stop()
            await asyncio.sleep(1)
            await bc.record_start()
            await asyncio.sleep(1)
            await bc.record_stop()
            out.append(Check("SD recording", True, "was already rolling; cleared and re-tested"))
        except BridgeError as e2:
            out.append(Check("SD recording", False, f"{e2}. Check the card is in and formatted",
                             blocking=False))
    show(out[-1])

    await bc.close()
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--feed-seconds", type=float, default=8.0)
    ap.add_argument("--skip-detector", action="store_true")
    ap.add_argument("--no-recover", action="store_true",
                    help="do not auto-cycle the connection when the feed is dead")
    args = ap.parse_args()

    print("PREFLIGHT, on the ground. Nothing here arms the aircraft.\n")
    checks = await run(args)
    blocking = [c for c in checks if not c.ok and c.blocking]
    warnings = [c for c in checks if not c.ok and not c.blocking]

    print()
    if blocking:
        print("NO-GO")
        for c in blocking:
            print(f"  fix first: {c.name}, {c.detail}")
        return 1
    print("GO" + (f", with {len(warnings)} warning(s)" if warnings else ""))
    for c in warnings:
        print(f"  note: {c.name}, {c.detail}")
    if any(c.name == "RC mode switch" and not c.ok for c in checks):
        print("\n  S1 must be at the BOTTOM (F) before a flight script can take control.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
