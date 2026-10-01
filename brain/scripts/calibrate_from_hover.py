"""Derive the camera's focal length from a hover, not from the ground.

    python scripts/calibrate_from_hover.py --height 1.905 --ground-distance 10

Run this AFTER a `follow_me.py --stage find` hover in which you stood a known
ground distance from the takeoff point. It reads that run's perception log and
solves, per frame,

    focal_px = box_height_px / (atan(A / D) - atan((A - H) / D))

for aircraft height A, ground distance D and your height H, with A taken from
the altitude the aircraft actually logged rather than the one it was asked for.
That is the angle your head and your feet subtend, which is what the camera
measures. Note it is NOT box_h * slant / H: looking down at a standing person
foreshortens them, and ignoring that overstates the focal length by about 14% at
6 m up and 10 m out, which is the same size as the error flight 02 could not
explain.

Use the `find` stage rather than `watch`. `find` rotates until you are in frame
and the gimbal keeps you vertically centred, and the relation above holds near
the centre of the image.

**Stand at two different distances and run this on both.** One hover gives a
number with nothing to check it against, which is exactly the position flight 02
ended in. Two distances have to agree, and that is a test the camera can fail:

    python scripts/calibrate_from_hover.py --height 1.905 \
        --at runs/follow-me/<near>-perception.jsonl 10 \
        --at runs/follow-me/<far>-perception.jsonl 20

What that check does and does not catch. It catches anything whose effect grows
or shrinks with distance, which is the whole family flight 02 fell into: a
reported gimbal angle that is not the real one, a wrong altitude, a mis-paced
distance. It is blind to a constant proportional bias, such as a detector that
boxes every person 20% too tall at every range, because one focal length does
explain both hovers then. Agreement is therefore necessary and not sufficient,
and it is still far more than one hover gives.

A WORD ON THE SIMULATOR, so a future check is not misread as a bug. The sim
cannot grade this script. It draws a person as a 0.7 m deep box and takes the
bounding box of all eight corners, which is 24% taller than the projection of a
real thin body, so the recovered focal length comes out roughly 20% high against
a known truth of 342.4 px. That is the renderer's geometry, not this formula's:
the measured box matches the sim's own eight-corner prediction to 1%. Verified
2026-10-01.

Why this and not `calibrate_camera.py`. On the ground the P3S camera sits at
grass height, so a person 10 m away stands behind a metre of foreground grass
and the detector never sees a whole body. From 6 m up, looking down, there is
nothing in the way, and it is the same geometry follow-me uses to convert a
detection into a real position. Measuring in the condition the number gets
applied in is strictly better.

It reports the spread honestly: you will sway, the aircraft drifts, and the
box jitters, so a wide spread is information about how much the standoff can
be trusted rather than something to hide.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.camera import P3S_DIAGONAL_FOV_DEG, CameraModel  # noqa: E402

RUNS = Path(__file__).resolve().parents[1] / "runs" / "follow-me"
OUT = Path(__file__).resolve().parents[1] / "configs" / "camera.json"


def focal_from(box_h: float, alt: float, ground: float, height: float) -> float | None:
    """The focal length a box of this height implies, exactly.

    Your head and your feet subtend an angle; the camera turns that angle into
    pixels, and the constant is the focal length. Nothing here is linearised.
    """
    if alt <= height:
        return None          # below head height the geometry inverts
    span = math.atan(alt / ground) - math.atan((alt - height) / ground)
    return None if span <= 1e-9 else box_h / span


def measure(path: Path, ground: float, height: float) -> dict | None:
    """One hover at one known distance."""
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    usable = [r for r in rows if r.get("box_h") and r.get("alt") and not r.get("clipped")]
    print(f"{path.name} at {ground:.0f} m: {len(rows)} frames, "
          f"{len(usable)} with a whole person and an altitude")
    if len(usable) < 10:
        clipped = sum(1 for r in rows if r.get("clipped"))
        print(f"  not enough to measure ({clipped} were cut off by the frame edge).")
        print("  Stand further from the takeoff point, or hover higher, so your whole")
        print("  body sits inside the frame.")
        return None
    focals = [f for f in (focal_from(r["box_h"], r["alt"], ground, height) for r in usable)
              if f is not None]
    if len(focals) < 10:
        print(f"  the aircraft was not far enough above {height} m to measure anything.")
        return None
    return {
        "path": path, "ground": ground, "n": len(focals),
        "focal": statistics.median(focals),
        "sd": statistics.pstdev(focals),
        "alt": statistics.median(r["alt"] for r in usable),
        "slant": statistics.median(math.hypot(r["alt"], ground) for r in usable),
        "w": max(r["frame_w"] for r in usable),
        "h": max(r["frame_h"] for r in usable),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--height", type=float, required=True, help="your height in metres")
    ap.add_argument("--ground-distance", type=float, default=None,
                    help="horizontal metres between you and the takeoff point")
    ap.add_argument("--log", type=Path, default=None, help="default: the newest find run")
    ap.add_argument("--at", nargs=2, action="append", metavar=("LOG", "METRES"),
                    help="repeatable: one hover at one measured distance. Give this TWICE, "
                         "at two different distances, and the two answers have to agree")
    ap.add_argument("--save", action="store_true", help="write configs/camera.json")
    args = ap.parse_args()

    pairs: list[tuple[Path, float]] = []
    for log, metres in (args.at or []):
        pairs.append((Path(log), float(metres)))
    if not pairs:
        if args.ground_distance is None:
            print("give --ground-distance, or --at LOG METRES (ideally twice).")
            return 1
        path = args.log or max(RUNS.glob("*-perception.jsonl"),
                               key=lambda p: p.stat().st_mtime, default=None)
        if path is None or not path.exists():
            print(f"no perception log in {RUNS}. Fly `follow_me.py --stage find` first.")
            return 1
        pairs.append((path, args.ground_distance))

    runs = []
    for path, ground in pairs:
        if not path.exists():
            print(f"{path} does not exist")
            return 1
        m = measure(path, ground, args.height)
        if m is None:
            return 1
        runs.append(m)

    if len(runs) > 1:
        print("\n  AGREEMENT between distances, which is the only check here that")
        print("  the camera can actually fail:")
        for m in runs:
            print(f"    {m['ground']:5.1f} m out -> {m['focal']:6.1f} px "
                  f"(sd {100 * m['sd'] / m['focal']:.1f}%, n={m['n']})")
        spread = max(m["focal"] for m in runs) - min(m["focal"] for m in runs)
        mid = statistics.median([m["focal"] for m in runs])
        print(f"    spread {spread:.1f} px, {100 * spread / mid:.1f}% of the median")
        if spread / mid > 0.10:
            print("    THEY DO NOT AGREE. One pinhole focal length cannot explain both")
            print("    hovers, so something else in the model is wrong (the reported")
            print("    gimbal angle, the altitude, or the paced distances). Flight 02")
            print("    failed exactly here. Do not trust a metre figure until this closes.")
        else:
            print("    Consistent. A single focal length explains both hovers.")

    usable = runs[0]
    focals = [m["focal"] for m in runs]
    slants = [m["slant"] for m in runs]
    f_med = statistics.median(focals)
    f_sd = statistics.median(m["sd"] for m in runs)
    w, h = usable["w"], usable["h"]
    med_alt = usable["alt"]
    n_used = sum(m["n"] for m in runs)

    fov = math.degrees(2 * math.atan((math.hypot(w, h) / 2.0) / f_med))
    nominal = CameraModel(w, h).focal_px
    err = 100 * (f_med - nominal) / nominal

    print(f"\n  frame                {w} x {h}")
    print(f"  altitude             {med_alt:.1f} m median")
    print(f"  slant range          {statistics.median(slants):.1f} m")
    print(f"  focal length         {f_med:.1f} px  (sd {f_sd:.1f}, {100 * f_sd / f_med:.1f}%)")
    print(f"  implied diagonal FOV {fov:.1f} deg")
    print(f"  spec sheet           {P3S_DIAGONAL_FOV_DEG:.1f} deg -> {nominal:.1f} px")
    print(f"  difference           {err:+.1f}%")
    print(f"\n  at 8 m standoff that is {abs(err) * 0.08:.2f} m of range error")
    if f_sd / f_med > 0.15:
        print("  NOTE: the spread is wide. Treat the standoff as approximate and keep")
        print("  --min-standoff well clear of the distance you actually want.")
    if len(runs) == 1:
        print("\n  ONE DISTANCE ONLY, so nothing here is cross-checked. Fly a second")
        print("  hover at a clearly different distance and pass both with --at.")

    if args.save:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "measured": [m["path"].stem for m in runs],
            "frame_w": w, "frame_h": h,
            "focal_px": round(f_med, 1),
            "focal_px_sd": round(f_sd, 1),
            "diagonal_fov_deg": round(fov, 2),
            "samples": n_used,
            "distances_m": [m["ground"] for m in runs],
            "cross_checked": len(runs) > 1,
            "method": (f"person {args.height} m, head-to-foot angle, from hover at "
                       + ", ".join(f"{m['ground']:.0f} m" for m in runs)),
        }, indent=2), encoding="utf-8")
        print(f"  wrote {OUT}")
    else:
        print("\n  re-run with --save to write configs/camera.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
