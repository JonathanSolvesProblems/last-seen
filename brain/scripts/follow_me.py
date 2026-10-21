"""Follow a person, in stages, each adding exactly one risk.

    python scripts/follow_me.py --stage watch            # hovers, measures, no tracking motion
    python scripts/follow_me.py --stage find             # rotates until it sees you, then holds
    python scripts/follow_me.py --stage yaw              # turns to keep you centred
    python scripts/follow_me.py --stage chase            # ...and holds your APPARENT SIZE
    python scripts/follow_me.py --stage follow           # the old projection-based standoff

    python scripts/follow_me.py --stage chase --url ws://127.0.0.1:8799 --detector oracle
                                                         # the same code, flown in the simulator

Stage by stage, because this is the first thing in the project that deliberately
flies an aircraft relative to a person, and that person is the pilot:

    watch   altitude hold only. Answers "can it see me at this height", which is
            completely unmeasured: flight 01's 100% detection was a stationary
            aircraft looking sideways from the ground.
    find    adds rotation until somebody is in frame, then holds. This is the
            stage to fly for the two calibration hovers.
    yaw     rotation that tracks you. The aircraft stays over one spot.
    chase   adds translation, steering by how big you look. THIS IS THE ONE TO
            FLY. It needs no focal length, no gimbal angle and no altitude, which
            is why it survives flight 02's 5.8 m projection error.
    follow  the original: flies to a standoff computed from the ground projection
            of your feet. Kept for comparison, superseded by chase.

Three guards that do not exist in the simulator version of this task:

  * a hard floor on range. If the estimate says you are closer than
    --min-standoff, the only motion allowed is motion that increases range.
    Being wrong about distance must never turn into flying at somebody.
  * a leash. The standoff point is clamped inside the geofence, so you walking
    away makes the aircraft stop rather than chase and trip the fence.
  * target continuity. It locks on to one person and then follows the detection
    nearest the last one, refusing jumps. A cyclist crossing frame does not
    become the new subject.

The pilot's override is unchanged and always wins: sweep S1 to the top.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import math
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain import geo  # noqa: E402
from phantom_brain.camera import CameraModel  # noqa: E402
from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.detect import (Detection, CvDnnYoloDetector, OracleDetector,  # noqa: E402
                                  draw_detections)
from phantom_brain.pilot import (Abort, Envelope, FlightSession, ensure_feed,
                                 runs_root)  # noqa: E402
from phantom_brain.protocol import VelocityCommand  # noqa: E402
from phantom_brain.control import body_to_ground  # noqa: E402
from phantom_brain.tasks.follow import FollowConfig, Follower  # noqa: E402
from phantom_brain.visual_servo import (ChaseController, ServoCommand,  # noqa: E402,F401
                                        ServoConfig, TargetLock, approximate_range,
                                        chase_command, gimbal_step, search_yaw_rate)

STAGES = ("watch", "find", "yaw", "chase", "follow")
SIZING_FRAMES = 6            # about a second of tracking before the distance is set
MIN_GIMBAL_INTERVAL = 0.4    # s, so re-aims cannot pile up on a slow acknowledge


def load_camera(w: int, h: int) -> tuple[CameraModel, str]:
    """Use the measured lens if one exists. The ChArUco calibration
    (calibrate_intrinsics.py) wins over the hover estimate (camera.json): it is
    measured directly by OpenCV against a printed board, not inferred from a
    person's height at a paced distance."""
    configs = Path(__file__).resolve().parents[1] / "configs"
    intr_path = configs / "camera_intrinsics.json"
    if intr_path.exists():
        d = json.loads(intr_path.read_text(encoding="utf-8"))
        return (CameraModel(w, h, float(d["diagonal_fov_deg"])),
                f"ChArUco {d['diagonal_fov_deg']:.1f} deg, {d.get('rms_reprojection_px', 0):.2f} px "
                f"reprojection, {d.get('measured', '?')}")
    path = configs / "camera.json"
    if path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        cam = CameraModel(w, h, float(d["diagonal_fov_deg"]))
        return cam, f"measured {d['diagonal_fov_deg']:.1f} deg on {d['measured']}"
    return CameraModel(w, h), "spec sheet 94 deg, NOT measured (run calibrate_camera.py)"




def guard(cmd: VelocityCommand, range_m: float | None, min_standoff: float,
          bearing_to_target: float, drone_heading: float) -> tuple[VelocityCommand, bool]:
    """Forbid any motion that closes range when already too close.

    Returns the possibly-modified command and whether the guard fired.
    """
    if range_m is None or range_m >= min_standoff:
        return cmd, False
    # Unit vector from the aircraft toward the person, in ground frame.
    b = math.radians(bearing_to_target)
    te, tn = math.sin(b), math.cos(b)
    closing = cmd.vn * tn + cmd.ve * te
    if closing <= 0:
        return cmd, False
    return VelocityCommand(cmd.vn - closing * tn, cmd.ve - closing * te,
                           cmd.vu, cmd.yaw_rate), True


def stage_command(stage: str, *, fresh: bool, full: VelocityCommand, alt_error_vu: float,
                  e: float, n: float, target_e: float | None, target_n: float | None,
                  rng: float | None, standoff: float, leash: float,
                  search_yaw: float = 12.0) -> VelocityCommand:
    """What to command this frame. Pure, so the safety rules can be tested.

    `full` is the Follower's own command, which already holds position and keeps
    yawing toward the last estimate once the target goes stale. Translation is
    only ever added on top of a FRESH fix: a position estimate goes stale the
    moment the target is lost, and flying on a stale one is how an aircraft ends
    up somewhere nobody predicted.
    """
    if stage == "watch":
        return VelocityCommand(0, 0, alt_error_vu, 0)
    if stage == "find":
        # Rotate on the spot until a person is in frame, then stop and hold.
        # The first hover found 3 of 415 frames purely because the aircraft was
        # pointed at empty grass: holding heading means never finding anybody.
        if fresh:
            return VelocityCommand(0, 0, alt_error_vu, full.yaw_rate)
        return VelocityCommand(0, 0, alt_error_vu, search_yaw)
    if stage == "yaw":
        return VelocityCommand(0, 0, full.vu, full.yaw_rate)
    if fresh and target_e is not None and target_n is not None and rng is not None and rng > 1e-3:
        ux, uy = (e - target_e) / rng, (n - target_n) / rng
        se, sn = clamp_to_leash(target_e + ux * standoff, target_n + uy * standoff, leash)
        k = 0.5
        return VelocityCommand((sn - n) * k, (se - e) * k, full.vu, full.yaw_rate)
    return VelocityCommand(0, 0, full.vu, full.yaw_rate)




def clamp_to_leash(te: float, tn: float, leash: float) -> tuple[float, float]:
    """Keep the point the aircraft flies to inside the geofence."""
    r = math.hypot(te, tn)
    if r <= leash or r < 1e-6:
        return te, tn
    return te * leash / r, tn * leash / r


async def aim(bc: BridgeClient, fs: FlightSession, pitch: float) -> None:
    """Re-aim the camera without ever letting it affect the flying.

    Runs as its own task so a slow acknowledgement cannot interrupt the
    velocity stream, and swallows its own failure: a camera that will not move
    is a worse shot, not a worse flight.
    """
    try:
        await bc.set_gimbal_pitch(pitch)
    except Exception as exc:  # noqa: BLE001
        try:
            fs.log(f"gimbal refused {pitch:.0f} deg ({exc})")
        except Exception:  # noqa: BLE001
            pass            # the session can close under us; never a flight problem


def _true_separation(truth: dict | None) -> float | None:
    """Ground-truth range, when flying the simulator. None on real hardware."""
    if not truth:
        return None
    d = truth.get("drone") or {}
    people = truth.get("people") or []
    if not people or "e" not in d:
        return None
    p = people[0]
    return round(math.hypot(p["e"] - d["e"], p["n"] - d["n"]), 2)


def feed_size(bc: BridgeClient) -> tuple[int, int]:
    """Frame size from whatever the feed last delivered."""
    fr = bc.frame
    if fr is None:
        return 1280, 720
    img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
    return (img.shape[1], img.shape[0]) if img is not None else (1280, 720)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--stage", choices=STAGES, default="watch")
    ap.add_argument("--alt", type=float, default=6.0)
    ap.add_argument("--standoff", type=float, default=8.0, help="range to hold, metres")
    ap.add_argument("--min-standoff", type=float, default=5.0,
                    help="never allow motion that closes range inside this")
    ap.add_argument("--hold", type=float, default=60.0, help="seconds of following")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--max-radius", type=float, default=25.0)
    ap.add_argument("--max-alt", type=float, default=15.0)
    ap.add_argument("--detector", choices=("yolo", "oracle"), default="yolo")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--search-yaw", type=float, default=12.0,
                    help="deg/s the find stage rotates at while looking for you")
    ap.add_argument("--target-px", type=float, default=None,
                    help="chase: box height to hold, in pixels. Default is to lock "
                         "whatever size you are when it first sees you")
    ap.add_argument("--close-ratio", type=float, default=1.5,
                    help="chase: a box this much taller than target means too close")
    args = ap.parse_args()

    if args.min_standoff > args.standoff:
        print("--min-standoff must not exceed --standoff")
        return
    leash = args.max_radius - 4.0

    print(f"plan: stage {args.stage} at {args.alt:.0f} m for {args.hold:.0f} s")
    print(f"      {'holds a spot, rotation only' if args.stage == 'yaw' else ''}"
          f"{'holds a spot, no tracking motion at all' if args.stage == 'watch' else ''}"
          f"{f'FLIES to hold {args.standoff:.0f} m range, never closing inside {args.min_standoff:.0f} m' if args.stage == 'follow' else ''}"
          f"{f'rotates at {args.search_yaw:.0f} deg/s until it finds you, then holds' if args.stage == 'find' else ''}"
          f"{'TURNS to face you and MOVES to hold your apparent size. No camera calibration used.' if args.stage == 'chase' else ''}")
    print(f"      ceiling {args.max_alt:.0f} m, fence {args.max_radius:.0f} m, "
          f"leash {leash:.0f} m, max {args.speed:.1f} m/s")

    det = (OracleDetector() if args.detector == "oracle" else
           CvDnnYoloDetector(str(Path(__file__).resolve().parents[1] / "models" / "yolo11n.onnx"),
                            imgsz=640, conf=args.conf))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    repo = (runs_root().parent if os.environ.get("DRONE_RUNS_DIR")
            else Path(__file__).resolve().parents[2])
    shots = repo / "broll" / f"followme-{stamp}"
    shots.mkdir(parents=True, exist_ok=True)
    runs = runs_root() / "follow-me"
    runs.mkdir(parents=True, exist_ok=True)
    # NOT <stamp>.jsonl: FlightSession owns that name for telemetry, and two
    # writers on one file interleave into unparseable JSON.
    perception_log = runs / f"{stamp}-perception.jsonl"
    log = open(perception_log, "w", encoding="utf-8")

    env = Envelope(max_alt=args.max_alt, max_radius=args.max_radius,
                   max_speed=args.speed, max_yaw_rate=25.0)
    frames = seen = guarded = gimbal_moves = clipped_frames = 0
    ranges: list[float] = []
    sizes: list[float] = []

    async with BridgeClient(args.url) as bc:
        # The 2.7K card file is the only footage worth cutting a video from, and
        # this script was the one that never rolled it.
        await bc.wait_for_telemetry(15)
        try:
            await bc.record_start()
            print("onboard camera recording to the SD card")
        except Exception:  # noqa: BLE001
            try:
                await bc.record_stop()
                await asyncio.sleep(1)
                await bc.record_start()
                print("onboard camera recording to the SD card (restarted)")
            except Exception as e:  # noqa: BLE001
                print(f"onboard recording did not start ({e})")

        async with FlightSession(bc, name="follow-me", envelope=env) as fs:
            await ensure_feed(bc, expect_motion=False)
            w, h = feed_size(bc)
            cam, how = load_camera(w or 1280, h or 720)
            print(f"camera: {w}x{h}, {how}")
            await fs.preflight()
            await fs.takeoff_and_take_control()
            await fs.climb_to(args.alt)
            # Now it has moved, so the picture must be changing. This is the
            # check that would have caught flight 01's dead camera in the air.
            await ensure_feed(bc, seconds=4.0, expect_motion=True)
            assert fs.origin is not None

            follower = Follower(cam, fs.origin, FollowConfig(
                standoff_m=args.standoff, altitude_m=args.alt, speed=args.speed,
                lost_timeout_s=6.0))
            pitch = follower.desired_gimbal_pitch()
            await bc.set_gimbal_pitch(pitch)
            fs.log(f"gimbal {pitch:.0f} deg, stage {args.stage}, following for {args.hold:.0f} s")

            lock: TargetLock | None = None
            target_px: float | None = None
            last_offset: float | None = None   # which side they left on
            sizing: list[float] = []           # first clean boxes, to set the standoff
            pitch_cmd = pitch
            gimbal_task: asyncio.Task | None = None
            last_gimbal_t = 0.0
            servo_cfg = ServoConfig(max_speed=args.speed, max_yaw_rate=25.0,
                                    close_ratio=args.close_ratio)
            chase = ChaseController(servo_cfg)
            # The command must be HELD between camera frames, not zeroed. Frames
            # arrive every ~200 ms and the loop spins every 30 ms, so zeroing
            # here means seven hover commands for every real one and the
            # aircraft never accelerates. In the simulator this looked exactly
            # like a follower that refuses to close range.
            hold_cmd = VelocityCommand()
            end = time.monotonic() + args.hold
            while time.monotonic() < end:
                fs.check(airborne=True)
                fr = bc.new_frame()
                if fr is None:
                    await bc.send_velocity(fs.clamp(hold_cmd))
                    await asyncio.sleep(0.03)
                    continue
                img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                frames += 1
                if lock is None:
                    lock = TargetLock(img.shape[1])
                dets = det.detect(img, fr.truth)
                target = lock.update(dets)
                tel = bc.telemetry_at(fr.t) or bc.telemetry
                assert tel is not None
                if target is not None:
                    seen += 1
                    follower.observe(target, tel, img.shape[1], img.shape[0],
                                     fr.gimbal_pitch or pitch, time.monotonic())

                e, n = fs.origin.to_local(tel.lat, tel.lon)
                rng = None
                bearing = tel.heading
                if follower.target_e is not None and follower.target_n is not None:
                    rng = math.hypot(follower.target_e - e, follower.target_n - n)
                    bearing = geo.bearing_deg(e, n, follower.target_e, follower.target_n)
                    # Only the stages that STEER by the projection report it.
                    # Quoting it for chase would put the one number flight 02
                    # showed to be wrong into chase's summary, under a heading
                    # that implies chase depends on it.
                    if args.stage != "chase":
                        ranges.append(rng)

                # A position estimate goes stale the moment the target is lost,
                # and flying on a stale estimate is how an aircraft ends up
                # somewhere nobody predicted. Only translate on a fresh fix.
                age = (time.monotonic() - follower.last_seen_t
                       if follower.last_seen_t is not None else None)
                fresh = age is not None and age < 1.5

                # --- the command, by stage ---------------------------------
                box_h = None if target is None else (target.y2 - target.y1)
                offset = None
                # A box touching the top or bottom edge is truncated, so its
                # height understates the person and reads as "further away".
                clipped = (target is not None
                           and (target.y1 <= 2 or target.y2 >= img.shape[0] - 2))
                if target is not None:
                    offset = ((target.x1 + target.x2) / 2 - img.shape[1] / 2) / img.shape[1]
                    last_offset = offset
                sweep = search_yaw_rate(last_offset, args.search_yaw)

                servo = None
                if args.stage == "chase" and not fresh:
                    # No target: rotate and look, exactly as the find stage does.
                    # Without this, chase does nothing at all whenever the
                    # aircraft happens not to be facing the subject when it
                    # takes over, which in the field is most of the time.
                    cmd = VelocityCommand(0, 0, 0.8 * (args.alt - tel.alt), sweep)
                    fired = False
                elif args.stage == "chase":
                    # Set the distance by demonstration: where you are standing
                    # when it finds you is the distance it holds. Measured over
                    # the first second of steady tracking, not from one frame.
                    # A single box is noisy by about 20% at this range, and the
                    # whole flight's standoff should not be pinned to whichever
                    # frame happened to be first, which in the simulator was
                    # taken 8 m from where I was actually standing.
                    if target_px is None and args.target_px:
                        target_px = args.target_px
                        fs.log(f"holding your apparent size at {target_px:.0f} px (given)")
                    elif (target_px is None and box_h and not clipped
                          and offset is not None
                          and abs(offset) <= servo_cfg.face_first):
                        # Only measure you once the camera is actually pointed
                        # at you. A box read off the edge of frame is both
                        # noisier and foreshortened, and this one number sets
                        # the standoff for the whole flight.
                        sizing.append(box_h)
                        if len(sizing) >= SIZING_FRAMES:
                            target_px = statistics.median(sizing)
                            fs.log(f"holding your apparent size at {target_px:.0f} px, "
                                   f"the median of {len(sizing)} frames "
                                   f"(locked from where you stand now)")
                    # The frame's own clock, not the wall clock: it is the one
                    # both the aircraft and the subject move on, and on the
                    # real bridge it is SystemClock.elapsedRealtime.
                    #
                    # While still measuring, hold the CURRENT size as the
                    # target: that is zero distance error by construction, so
                    # the aircraft turns to face you and does not translate.
                    # Standing still with the camera refusing to turn toward
                    # you is how the measurement never completes.
                    servo = chase.step(fr.t, offset if fresh else None,
                                       box_h if fresh else None,
                                       target_px or (box_h or 0.0), clipped=clipped)
                    cmd = chase_command(servo, tel.heading,
                                        0.8 * (args.alt - tel.alt), e, n, leash)
                    fired = servo.too_close and servo.forward == 0.0
                else:
                    cmd = stage_command(
                        args.stage, fresh=fresh,
                        full=follower.step(tel, time.monotonic()),
                        alt_error_vu=0.8 * (args.alt - tel.alt),
                        e=e, n=n, target_e=follower.target_e, target_n=follower.target_n,
                        rng=rng, standoff=args.standoff, leash=leash,
                        search_yaw=sweep)
                    cmd, fired = guard(cmd, rng, args.min_standoff, bearing, tel.heading)
                guarded += int(fired)
                clipped_frames += int(bool(clipped))
                # Only once a real target size exists: during the measuring
                # window the error is zero by construction and would flatter
                # the summary.
                if servo is not None and target is not None and target_px:
                    sizes.append(servo.size_error)
                hold_cmd = cmd
                await bc.send_velocity(fs.clamp(cmd))

                # Keep them vertically centred. The fixed angle set at takeoff
                # is correct for exactly one distance, and the subject walks.
                #
                # Never AWAITED. The velocity watchdog on the aircraft is 500 ms
                # and a gimbal command waits for the phone to acknowledge it, so
                # awaiting one here puts a hover in the middle of a follow every
                # time the camera re-aims. Fire it, keep flying, and skip the
                # next one if this one has not come back yet.
                if (args.stage in ("chase", "find") and target is not None
                        and (gimbal_task is None or gimbal_task.done())
                        and time.monotonic() - last_gimbal_t >= MIN_GIMBAL_INTERVAL):
                    v_off = ((target.y1 + target.y2) / 2 - img.shape[0] / 2) / img.shape[0]
                    # Close the loop on the angle the gimbal REPORTS, not on the
                    # last angle asked for. A gimbal that lags or refuses lets a
                    # command-fed loop wind up against its own clamp: in the
                    # simulator it sat at the -5 deg limit while the hardware
                    # was still 6 deg behind, and then had to unwind before it
                    # could respond to the subject coming back down.
                    now_pitch = (fr.gimbal_pitch if fr.gimbal_pitch is not None
                                 else tel.gimbal_pitch)
                    want = gimbal_step(v_off, now_pitch if now_pitch is not None
                                       else pitch_cmd, servo_cfg)
                    if abs(want - pitch_cmd) >= 1.5:
                        pitch_cmd = want
                        gimbal_moves += 1
                        last_gimbal_t = time.monotonic()
                        gimbal_task = asyncio.create_task(aim(bc, fs, pitch_cmd))
                fs.sample()

                # Log the closing component, not just the speed: "was it moving
                # while too close" is not the same question as "was it moving
                # TOWARD the person while too close", and only the second one is
                # the guard's business.
                br = math.radians(bearing)
                closing = cmd.vn * math.cos(br) + cmd.ve * math.sin(br)
                log.write(json.dumps({
                    "t": round(time.monotonic(), 3), "seq": fr.seq, "stage": args.stage,
                    "people": len(dets), "locked": target is not None,
                    # Box height and confidence are what an airborne focal-length
                    # calibration is computed from: on the ground the camera sits
                    # in the grass and cannot see a whole person at 10 m.
                    "box_h": None if target is None else round(target.y2 - target.y1, 1),
                    # What the controller actually read: the raw height above is
                    # the measurement, this is the fit through the last 0.7 s.
                    "box_h_used": None if chase.smoothed is None else round(chase.smoothed, 1),
                    "integral": round(chase.integral, 3),
                    "box_w": None if target is None else round(target.x2 - target.x1, 1),
                    # Box POSITION, not just size. Flight 02 logged the height and
                    # not the feet row, and the feet row is what turns a detection
                    # into a distance, so the run could not be calibrated at all.
                    "box_y1": None if target is None else round(target.y1, 1),
                    "box_y2": None if target is None else round(target.y2, 1),
                    "box_x1": None if target is None else round(target.x1, 1),
                    "box_x2": None if target is None else round(target.x2, 1),
                    # Reported gimbal pitch from the frame header AND telemetry:
                    # the commanded angle turned out not to be what the camera did.
                    "gimbal_frame": fr.gimbal_pitch,
                    "gimbal_tel": None if tel is None else tel.gimbal_pitch,
                    "gimbal_cmd": round(pitch_cmd, 1),
                    "size_rate": None if chase.rate is None else round(chase.rate, 2),
                    "target_px": target_px,
                    "size_error": None if servo is None else round(servo.size_error, 3),
                    "forward": None if servo is None else round(servo.forward, 3),
                    "facing": None if servo is None else servo.facing,
                    "too_close": None if servo is None else servo.too_close,
                    "approx_range_m": (None if (box_h is None or not box_h) else
                                       round(approximate_range(box_h, 1.905, cam.focal_px) or 0, 2)),
                    # Simulator only: the separation the world actually has, so
                    # the image-space error can be checked against the truth it
                    # is standing in for. Absent in the field, by definition.
                    "true_sep": _true_separation(fr.truth),
                    "clipped": None if target is None else bool(
                        target.y1 <= 2 or target.y2 >= img.shape[0] - 2),
                    "conf": None if target is None else round(target.conf, 3),
                    "frame_h": img.shape[0], "frame_w": img.shape[1],
                    "fresh": fresh, "fix_age_s": None if age is None else round(age, 2),
                    "range_m": None if rng is None else round(rng, 2),
                    "bearing": round(bearing, 1), "closing": round(closing, 3),
                    "guard": fired, "alt": round(tel.alt, 2), "hdg": round(tel.heading, 1),
                    "vn": round(cmd.vn, 2), "ve": round(cmd.ve, 2),
                    "yaw": round(cmd.yaw_rate, 1), "bat": tel.battery,
                }) + "\n")
                log.flush()
                if frames % 3 == 0:
                    cv2.imwrite(str(shots / f"{fr.seq:06d}.jpg"),
                                draw_detections(img, dets, target))
                if frames % 20 == 0:
                    print(f"  {frames:4d} frames  locked {100 * seen / frames:5.1f}%  "
                          f"range {'--' if rng is None else f'{rng:5.1f} m'}  "
                          f"guard {guarded}", flush=True)

            if gimbal_task is not None and not gimbal_task.done():
                gimbal_task.cancel()        # not left dangling past the flight

            fs.note("follow", f"{seen}/{frames} frames locked, "
                              f"{guarded} guard interventions",
                    frames > 0 and seen / max(frames, 1) > 0.5,
                    stage=args.stage, frames=frames, locked=seen, guard_fires=guarded,
                    mean_range=round(sum(ranges) / len(ranges), 2) if ranges else None,
                    min_range=round(min(ranges), 2) if ranges else None)
            await fs.return_to_origin()

        print(fs.summary())

    print("\n" + "=" * 62)
    print(f"  stage              {args.stage}")
    print(f"  frames             {frames}")
    if frames:
        print(f"  target locked      {seen} ({100 * seen / frames:.1f}%)")
    if ranges:
        print(f"  range              mean {sum(ranges) / len(ranges):.1f} m, "
              f"min {min(ranges):.1f} m, max {max(ranges):.1f} m")
    if sizes:
        held = 100 * sum(1 for x in sizes if abs(x) <= 0.15) / len(sizes)
        print(f"  apparent size      held within 15% on {held:.0f}% of locked frames")
        print(f"  size error         median {statistics.median(sizes):+.0%} "
              f"(negative = slightly closer than asked)")
        print("  NOTE: chase steers by apparent size, which needs no calibration.")
        print("        Any metre figure here is approximate until the focal length is measured.")
    print(f"  guard interventions {guarded}")
    if args.stage in ("chase", "find"):
        print(f"  gimbal re-aims     {gimbal_moves}")
        print(f"  clipped boxes      {clipped_frames}"
              f"{f' ({100 * clipped_frames / seen:.0f}% of locked frames, distance control refused)' if seen else ''}")
    print(f"  perception log     {perception_log}")
    print(f"  frames             {shots}")
    print("=" * 62)


if __name__ == "__main__":
    asyncio.run(main())
