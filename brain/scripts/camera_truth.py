"""Measure where the camera REALLY points, against four markers on the grass.

    python scripts/camera_truth.py --layout configs/ground_markers.json
    python scripts/camera_truth.py --from runs/camera-truth/<stamp>      # re-solve saved frames

Flight 02 ended with the gimbal reporting -23 deg while the pictures looked like
-37, and nothing on the aircraft able to say which was true. This hovers near
four ArUco markers whose spacing was measured with a tape, steps the gimbal
through several commanded angles, and at each one compares three numbers:

    commanded   what the script asked for
    reported    what the aircraft says the gimbal is at
    measured    what solvePnP says the camera is at, from the markers

It also measures the camera's height above the grass, against the aircraft's
barometric altitude. None of the measured numbers come from the aircraft.

Field setup (five minutes): lay the four marker pages flat in a square, arrows
north, roughly 2 m apart, weigh the corners. Tape-measure the centres and write
them in configs/ground_markers.json. Take off 5 to 6 m SOUTH of the square, nose
north. The script climbs to --alt, turns until it sees at least two markers,
then runs the steps. Pilot override on S1 as always.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.calib import GroundMarkers, Intrinsics, solve_camera_pose  # noqa: E402
from phantom_brain.pilot import runs_root  # noqa: E402

CONFIGS = Path(__file__).resolve().parents[1] / "configs"
DEFAULT_STEPS = (-25.0, -35.0, -45.0, -60.0)


def load_intrinsics(path: Path | None, w: int, h: int, fov: float | None) -> tuple[Intrinsics, str]:
    if fov is not None:
        return Intrinsics.from_fov(w, h, fov), f"assumed {fov:.1f} deg diagonal (no lens calibration)"
    p = path or CONFIGS / "camera_intrinsics.json"
    if p.exists():
        intr = Intrinsics.load(p)
        if (intr.width, intr.height) != (w, h):
            intr = intr.scaled_to(w, h)
        return intr, f"ChArUco calibration {p.name}, {intr.rms_reprojection_px:.2f} px"
    return (Intrinsics.from_fov(w, h, 94.0),
            "SPEC SHEET 94 deg, NOT MEASURED: run calibrate_intrinsics.py first or every "
            "measured pitch inherits the spec's error")


def summarise(rows: list[dict]) -> list[dict]:
    """One line per commanded angle: medians, so one bad frame cannot move it."""
    out = []
    for cmd in sorted({r["commanded"] for r in rows}, reverse=True):
        rs = [r for r in rows if r["commanded"] == cmd and r.get("measured_pitch") is not None]
        if not rs:
            out.append({"commanded": cmd, "frames": 0})
            continue

        def med(k):
            vals = [r[k] for r in rs if r.get(k) is not None]
            return round(statistics.median(vals), 2) if vals else None

        out.append({"commanded": cmd, "frames": len(rs),
                    "reported": med("reported_pitch"), "reported_tel": med("reported_tel"),
                    "measured": med("measured_pitch"),
                    "measured_sd": round(statistics.pstdev([r["measured_pitch"] for r in rs]), 2),
                    "height_measured": med("measured_height"), "alt_reported": med("alt"),
                    "reprojection_px": med("reprojection_px")})
    return out


def print_table(summary: list[dict], how: str) -> None:
    print("\n" + "=" * 78)
    print(f"  camera model: {how}")
    print(f"  {'commanded':>9} {'telemetry':>9} {'frame hdr':>9} {'MEASURED':>9} {'sd':>5} {'gap':>7}   "
          f"{'height':>7} {'baro alt':>8} {'reproj':>7} {'n':>4}")
    for s in summary:
        if not s.get("frames"):
            print(f"  {s['commanded']:9.1f}   no markers seen at this angle")
            continue
        # The gap is against TELEMETRY: that is the number flight 02 doubted, and
        # the one every ground-distance calculation actually reads.
        ref = s["reported_tel"] if s.get("reported_tel") is not None else s["reported"]
        gap = s["measured"] - ref if ref is not None else float("nan")
        print(f"  {s['commanded']:9.1f} {s['reported_tel']:9.1f} {s['reported']:9.1f} {s['measured']:9.1f} {s['measured_sd']:5.2f} "
              f"{gap:+7.1f}   {s['height_measured']:6.2f}m {s['alt_reported']:7.2f}m "
              f"{s['reprojection_px']:6.2f}px {s['frames']:4d}")
    print("  gap = measured - telemetry. Anything past about 2 deg is the gimbal (or the")
    print("  telemetry) not being where it says, and every ground distance inherits it.")
    print("=" * 78)


def solve_frame(img: np.ndarray, intr: Intrinsics, layout: GroundMarkers) -> dict:
    pose = solve_camera_pose(img, intr, layout)
    if pose is None:
        return {"measured_pitch": None}
    return {"measured_pitch": round(pose.gimbal_pitch_deg, 3),
            "measured_height": round(pose.height_m, 3),
            "measured_heading": round(pose.heading_deg, 2),
            "markers": pose.markers, "reprojection_px": round(pose.reprojection_px, 3)}


def from_dir(run: Path, layout: GroundMarkers, intr_path: Path | None, fov: float | None) -> int:
    rows = [json.loads(x) for x in (run / "frames.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    first = cv2.imread(str(run / rows[0]["file"]))
    intr, how = load_intrinsics(intr_path, first.shape[1], first.shape[0], fov)
    for r in rows:
        r.update(solve_frame(cv2.imread(str(run / r["file"])), intr, layout))
    summary = summarise(rows)
    print_table(summary, how)
    (run / "summary_resolved.json").write_text(json.dumps({"camera": how, "steps": summary}, indent=2))
    return 0


async def fly(args, layout: GroundMarkers) -> int:
    from phantom_brain.client import BridgeClient  # noqa: PLC0415
    from phantom_brain.pilot import Envelope, FlightSession, ensure_feed  # noqa: PLC0415
    from phantom_brain.protocol import VelocityCommand  # noqa: PLC0415

    print(f"plan: climb to {args.alt:.0f} m, turn to find the markers, then hold position while")
    print(f"      the gimbal steps through {', '.join(f'{s:.0f}' for s in args.steps)} deg. "
          f"No translation at all.")
    out = runs_root() / "camera-truth" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    flog = open(out / "frames.jsonl", "w", encoding="utf-8")
    rows: list[dict] = []
    how = "?"
    env = Envelope(max_alt=args.max_alt, max_radius=args.max_radius, max_speed=1.0, max_yaw_rate=20.0)

    async with BridgeClient(args.url) as bc:
        await bc.wait_for_telemetry(15)
        async with FlightSession(bc, name="camera-truth", envelope=env) as fs:
            await ensure_feed(bc, expect_motion=False)
            await fs.preflight()
            await fs.takeoff_and_take_control()
            await fs.climb_to(args.alt)
            fr = await bc.next_frame(timeout=5)
            intr, how = load_intrinsics(args.intrinsics, fr.w, fr.h, args.fov)
            fs.log(f"camera model: {how}")

            # Turn on the spot until the markers are in view. The camera is
            # what decides when to stop, which is the whole loop in miniature.
            await bc.set_gimbal_pitch(-45.0)
            await fs.hold(2.0)
            found, end = False, time.monotonic() + 40
            while not found and time.monotonic() < end:
                fs.check(airborne=True)
                await bc.send_velocity(fs.clamp(VelocityCommand(0, 0, 0.8 * (args.alt - bc.telemetry.alt), 12.0)))
                fs.sample()
                f2 = bc.new_frame()
                if f2 is not None:
                    img = cv2.imdecode(np.frombuffer(f2.jpeg, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None and solve_frame(img, intr, layout)["measured_pitch"] is not None:
                        found = True
                await asyncio.sleep(0.05)
            await fs.hold(1.0)
            if not found:
                fs.log("markers never came into view; check the layout and the takeoff spot")
                fs.note("find markers", "not found", False)
            else:
                fs.log("markers in view")
                for cmd in args.steps:
                    await bc.set_gimbal_pitch(cmd)
                    await fs.hold(args.settle)              # let the gimbal arrive
                    t_end = time.monotonic() + args.measure
                    while time.monotonic() < t_end:
                        fs.check(airborne=True)
                        await bc.send_velocity(fs.clamp(VelocityCommand(0, 0, 0.8 * (args.alt - bc.telemetry.alt), 0)))
                        f3 = bc.new_frame()
                        if f3 is not None:
                            img = cv2.imdecode(np.frombuffer(f3.jpeg, np.uint8), cv2.IMREAD_COLOR)
                            if img is not None:
                                tel = bc.telemetry_at(f3.t) or bc.telemetry
                                name = f"{cmd:+04.0f}_{f3.seq:06d}.jpg"
                                cv2.imwrite(str(out / name), img)
                                row = {"file": name, "commanded": cmd,
                                       # Both reports: the frame header's (sampled with the
                                       # picture) and telemetry's. Flight 02 had neither.
                                       "reported_pitch": f3.gimbal_pitch if f3.gimbal_pitch else tel.gimbal_pitch,
                                       "reported_tel": tel.gimbal_pitch, "alt": tel.alt,
                                       **solve_frame(img, intr, layout)}
                                rows.append(row)
                                flog.write(json.dumps(row) + "\n")
                                flog.flush()
                        fs.sample()
                        await asyncio.sleep(0.05)
                summary = summarise(rows)
                fs.note("camera truth", f"{sum(s.get('frames', 0) for s in summary)} frames solved",
                        any(s.get("frames") for s in summary), steps=summary)
            await bc.set_gimbal_pitch(-45.0)
            await fs.return_to_origin()
        print(fs.summary())

    flog.close()
    summary = summarise(rows)
    print_table(summary, how)
    (out / "summary.json").write_text(json.dumps({"camera": how, "layout": layout.to_dict(),
                                                  "steps": summary}, indent=2), encoding="utf-8")
    print(f"  frames and results: {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--layout", type=Path, default=CONFIGS / "ground_markers.json")
    ap.add_argument("--intrinsics", type=Path, default=None)
    ap.add_argument("--fov", type=float, default=None,
                    help="assume this diagonal FOV instead of a calibration (the sim uses 94)")
    ap.add_argument("--alt", type=float, default=4.0)
    ap.add_argument("--steps", type=float, nargs="+", default=list(DEFAULT_STEPS))
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--measure", type=float, default=4.0)
    ap.add_argument("--max-alt", type=float, default=10.0)
    ap.add_argument("--max-radius", type=float, default=15.0)
    ap.add_argument("--from", dest="src", type=Path, default=None)
    args = ap.parse_args()
    if not args.layout.exists():
        print(f"no marker layout at {args.layout}. Measure the square and write it, e.g.:")
        print('  {"size_m": 0.18, "centres_m": {"20": [0, 0], "21": [2.0, 0], '
              '"22": [2.0, 2.0], "23": [0, 2.0]}}')
        return 1
    layout = GroundMarkers.load(args.layout)
    if args.src:
        return from_dir(args.src, layout, args.intrinsics, args.fov)
    return asyncio.run(fly(args, layout))


if __name__ == "__main__":
    sys.exit(main())
