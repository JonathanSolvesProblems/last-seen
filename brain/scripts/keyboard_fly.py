"""Manual teleop through the bridge, with live video. The first-flight tool.

    python scripts/keyboard_fly.py [--url ws://127.0.0.1:8765]

Keys (focus the video window):
    T  takeoff        L  land         H  return home      V  enable virtual stick
    W/S  north/south  A/D  west/east  R/F  up/down        Q/E  yaw left/right
    1..5  speed 0.5..2.5 m/s          G/B  gimbal up/down 10 deg
    SPACE  stop (hover)               X  disable virtual stick
    Z  zero the N/E readout          (V zeroes it too)
    C  confirm landing (if it stops just above the ground and waits)
    O  toggle the onboard 2.7K recording (it starts automatically)
    K  cut the propellers NOW (only when the aircraft is in your hands)
    ESC  quit: hovers, then releases virtual stick so the RC has control

The onboard camera starts recording to the SD card as soon as this connects and
stops on exit, so every test leaves footage behind. The 720p bridge feed is also
written frame by frame to broll/feed-*/ (that folder is gitignored).

While virtual stick is on, the RC sticks are IGNORED by the aircraft. Quitting
(or crashing) always releases it; if you see the warning that it could not,
flip S1 out of F immediately and the sticks come back.

Use this on the first real flight to verify the axis mapping before any
autonomous phase: at ~10 m, press W for two seconds and confirm the drone
moved NORTH (check DJI GO's map or a compass), then D for EAST, then R for UP.
If any axis is wrong, tick "Swap N/E axes" in the app and do not fly the mission.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain import geo  # noqa: E402
from phantom_brain.client import BridgeClient, BridgeError  # noqa: E402
from phantom_brain.protocol import VelocityCommand  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    async with BridgeClient(args.url) as bc:
        print("connected; waiting for telemetry")
        try:
            await bc.wait_for_telemetry(15)
        except asyncio.TimeoutError:
            print("no telemetry yet (is the drone connected in the bridge app?)")
        try:
            await bc.record_start()
            print("onboard camera recording to the SD card")
        except Exception as e:  # noqa: BLE001
            print(f"could not start the onboard recording ({e}); flying anyway")
        try:
            await fly(bc)
        finally:
            try:
                await bc.record_stop()
                print("onboard recording stopped")
            except Exception:  # noqa: BLE001
                pass
            # Never leave the aircraft in virtual stick: while it is on the RC
            # sticks are ignored, so a crash here would leave the pilot holding
            # a dead controller until S1 is flipped out of F.
            try:
                await bc.hover()
                await bc.disable_virtual_stick()
                print("virtual stick released, the RC has control")
            except Exception as e:  # noqa: BLE001
                print(f"could not release virtual stick ({e}); FLIP S1 OUT OF F NOW")
    cv2.destroyAllWindows()


async def fly(bc: BridgeClient) -> None:
    speed = 1.0
    gimbal = -30.0
    cmd = VelocityCommand()
    pending: set[asyncio.Task] = set()
    # Origin for the axis test. DJI GO cannot run while the bridge holds the
    # aircraft, so the drone's own GPS is the only available witness to which
    # way it actually moved. Zeroed by V and by Z.
    origin: geo.Origin | None = None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = Path(__file__).resolve().parents[1] / "runs" / "teleop"
    log_path.mkdir(parents=True, exist_ok=True)
    rec = open(log_path / f"{stamp}.jsonl", "w", encoding="utf-8")
    # Keep the raw feed as b-roll. Small, and it is the only record of what the
    # brain actually saw, which is the interesting shot even though it is 720p.
    broll = Path(__file__).resolve().parents[2] / "broll" / f"feed-{stamp}"
    broll.mkdir(parents=True, exist_ok=True)
    recording = True
    held_since: float | None = None

    def zero(tel) -> geo.Origin | None:
        if tel is None or not tel.gps_fix:
            print("cannot zero: no GPS fix")
            return None
        print(f"zeroed at {tel.lat:.6f}, {tel.lon:.6f}")
        return geo.Origin(tel.lat, tel.lon)

    def fire(coro) -> None:
        """Run a slow command without stalling the velocity stream."""
        task = asyncio.create_task(coro)
        pending.add(task)
        task.add_done_callback(pending.discard)

    while True:
        fr = bc.new_frame()
        if fr is not None:
            img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
            (broll / f"{fr.seq:06d}.jpg").write_bytes(fr.jpeg)
        else:
            img = np.zeros((360, 640, 3), np.uint8)
        tel = bc.telemetry
        if tel:
            txt = (f"alt {tel.alt:.1f} hdg {tel.heading:.0f} sats {tel.gps_sats}{'' if tel.gps_fix else ' NOFIX'} "
                   f"bat {tel.battery}% fly={tel.is_flying} vs={tel.virtual_stick} mode={tel.flight_mode} "
                   f"S1={tel.rc_mode_switch} home={'set' if tel.home_set else 'NOT SET'}")
            cv2.putText(img, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            rec.write(json.dumps({"t": tel.t, "lat": tel.lat, "lon": tel.lon, "alt": tel.alt,
                                  "hdg": tel.heading, "vs": tel.virtual_stick,
                                  "cmd": [cmd.vn, cmd.ve, cmd.vu, cmd.yaw_rate]}) + "\n")
            rec.flush()
            # THE AXIS TEST READOUT: how far has it moved since Z / V, in metres
            if origin is not None and tel.gps_fix:
                e, n = origin.to_local(tel.lat, tel.lon)
                axis = f"N {n:+6.1f} m   E {e:+6.1f} m   (up {tel.alt:+.1f} m)"
                cv2.putText(img, axis, (8, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5, cv2.LINE_AA)
                cv2.putText(img, axis, (8, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 255, 80), 2, cv2.LINE_AA)
            else:
                hint = "press Z to zero the axis readout (needs GPS fix)"
                cv2.putText(img, hint, (8, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
                cv2.putText(img, hint, (8, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 200, 255), 1, cv2.LINE_AA)
            # "It is in his hands": motors turning, close to the ground, and
            # tilted far past anything the aircraft does by itself down there.
            # Sustained, because a single bad attitude sample is not evidence.
            tilted = max(abs(tel.roll), abs(tel.pitch)) > 40
            if tel.motors_on and tel.alt < 1.5 and tilted:
                held_since = held_since or time.monotonic()
                if time.monotonic() - held_since > 0.6:
                    print("aircraft appears to be in your hands: cutting the propellers")
                    try:
                        await bc.stop_motors()
                    except BridgeError as e:
                        print("motor cut failed:", e)
                    held_since = None
                    cmd = VelocityCommand()
            else:
                held_since = None
        cv2.putText(img, f"cmd vn={cmd.vn:+.1f} ve={cmd.ve:+.1f} vu={cmd.vu:+.1f} yaw={cmd.yaw_rate:+.0f}  speed {speed}",
                    (8, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)
        if recording:
            cv2.circle(img, (img.shape[1] - 28, 24), 8, (0, 0, 220), -1)
            cv2.putText(img, "REC", (img.shape[1] - 78, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 0, 220), 2, cv2.LINE_AA)
        cv2.imshow("keyboard_fly", img)
        k = cv2.waitKey(50) & 0xFF
        try:
            if k == 27:
                break                      # the finally block releases control
            elif k == ord("t"):
                await bc.takeoff()
            elif k == ord("l"):
                cmd = VelocityCommand()
                await bc.land()
            elif k == ord("h"):
                cmd = VelocityCommand()
                await bc.go_home()
            elif k == ord("v"):
                await bc.enable_virtual_stick()
                origin = zero(bc.telemetry) or origin
            elif k == ord("z"):
                origin = zero(bc.telemetry) or origin
            elif k == ord("x"):
                cmd = VelocityCommand()
                await bc.disable_virtual_stick()
            elif k == ord(" "):
                cmd = VelocityCommand()
            elif k == ord("w"):
                cmd = VelocityCommand(speed, 0, 0, 0)
            elif k == ord("s"):
                cmd = VelocityCommand(-speed, 0, 0, 0)
            elif k == ord("d"):
                cmd = VelocityCommand(0, speed, 0, 0)
            elif k == ord("a"):
                cmd = VelocityCommand(0, -speed, 0, 0)
            elif k == ord("r"):
                cmd = VelocityCommand(0, 0, min(speed, 1.0), 0)
            elif k == ord("f"):
                cmd = VelocityCommand(0, 0, -min(speed, 1.0), 0)
            elif k == ord("e"):
                cmd = VelocityCommand(0, 0, 0, 30)
            elif k == ord("q"):
                cmd = VelocityCommand(0, 0, 0, -30)
            elif k in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
                speed = 0.5 * (k - ord("0"))
            elif k == ord("c"):
                await bc.confirm_landing()
            elif k == ord("g"):
                gimbal = min(0.0, gimbal + 10)
                fire(bc.set_gimbal_pitch(gimbal))     # ~2 s on the P3S
            elif k == ord("b"):
                gimbal = max(-90.0, gimbal - 10)
                fire(bc.set_gimbal_pitch(gimbal))
            elif k == ord("o"):
                recording = not recording
                fire(bc.record_start() if recording else bc.record_stop())
                print("onboard recording", "on" if recording else "off")
            elif k == ord("k"):
                cmd = VelocityCommand()
                await bc.stop_motors()
        except BridgeError as e:
            print("bridge:", e)
        # stream the current command continuously (the phone watchdog needs it)
        try:
            await bc.send_velocity(cmd)
        except BridgeError:
            pass
        await asyncio.sleep(0.05)


if __name__ == "__main__":
    asyncio.run(main())
