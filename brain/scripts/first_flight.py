"""A bounded validation flight: prove the control frame on real hardware.

    python scripts/first_flight.py --dry-run                  # preflight only
    python scripts/first_flight.py --tests yaw,vertical       # nothing translates
    python scripts/first_flight.py --tests north,east         # one leg each, and back

It answers what a simulator cannot: when the code commands north, does the
aircraft go north. Flight 01 (2026-09-25) said yes, twice, with 0.02 m and
0.15 m of cross-axis error over 3 m of travel.

Everything is bounded. Nothing is commanded faster than 1 m/s, the aircraft never
leaves a 20 m circle or 12 m of altitude, and the run ends on a flat battery, a
stale link, or the pilot taking the sticks.

THE PILOT'S OVERRIDE IS THE HARDWARE SWITCH. Sweeping S1 out of F drops the SDK
out of virtual stick, which this sees within a few samples and treats as an
instruction to stop commanding. It does not depend on this script still working.

The safety envelope, the takeoff retries and the guaranteed release of control
all live in phantom_brain.pilot, so they cannot drift between scripts.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.pilot import Envelope, FlightSession, Step  # noqa: E402
from phantom_brain.protocol import VelocityCommand  # noqa: E402

TRANSLATING = ("north", "south", "east", "west")
ALL_TESTS = ("north", "east", "vertical", "yaw")


async def axis_leg(fs: FlightSession, name: str, cmd: VelocityCommand, leg: float) -> Step:
    """Fly one leg, measure where it actually went, then report."""
    n0, e0 = fs.here()
    h0 = fs.heading_total
    await fs.stream(cmd, leg)
    await fs.hold(2.5)                       # let GPS settle before reading
    n1, e1 = fs.here()
    dn, de = n1 - n0, e1 - e0
    # Accumulated across wraps, so a turn past half a circle reads as itself
    # rather than as its small complement.
    dh = fs.heading_total - h0

    ok = None
    if name == "north":
        ok = dn > 1.0 and abs(dn) > abs(de)
    elif name == "south":
        ok = dn < -1.0 and abs(dn) > abs(de)
    elif name == "east":
        ok = de > 1.0 and abs(de) > abs(dn)
    elif name == "west":
        ok = de < -1.0 and abs(de) > abs(dn)
    elif name == "yaw cw":
        ok = dh > 30

    s = fs.note(f"axis {name}", f"moved N{dn:+.1f} E{de:+.1f} m, heading {dh:+.0f} deg", ok,
                dn=round(dn, 2), de=round(de, 2), dheading=round(dh, 1))
    fs.log(f"{s.name}: {s.detail}  -> {'OK' if ok else 'WRONG'}")
    return s


async def run_tests(fs: FlightSession, tests: list[str], alt: float, leg: float) -> None:
    speed = fs.env.max_speed
    for name in tests:
        if name in TRANSLATING:
            vec = {"north": VelocityCommand(speed, 0), "south": VelocityCommand(-speed, 0),
                   "east": VelocityCommand(0, speed), "west": VelocityCommand(0, -speed)}[name]
            await axis_leg(fs, name, vec, leg)
            await fs.return_to_origin()
        elif name == "vertical":
            fs.log("vertical check")
            a0 = fs.bc.telemetry.alt  # type: ignore[union-attr]
            await fs.stream(VelocityCommand(0, 0, fs.env.max_climb), 3.0)
            await fs.hold(2.0)
            a1 = fs.bc.telemetry.alt  # type: ignore[union-attr]
            fs.note("vertical", f"climbed {a1 - a0:+.1f} m on +vu", (a1 - a0) > 0.6,
                    dalt=round(a1 - a0, 2))
            fs.log(f"vertical: {a1 - a0:+.1f} m -> {'OK' if a1 - a0 > 0.6 else 'WRONG'}")
            await fs.climb_to(alt)
        elif name == "yaw":
            await axis_leg(fs, "yaw cw", VelocityCommand(0, 0, 0, fs.env.max_yaw_rate), leg)
        else:
            fs.log(f"unknown test {name!r}, skipping")
    fs.log("returning to the start point")
    await fs.return_to_origin()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--alt", type=float, default=4.0, help="working altitude in metres")
    ap.add_argument("--leg", type=float, default=3.0, help="seconds per axis leg")
    ap.add_argument("--tests", default=",".join(ALL_TESTS),
                    help="which checks to run, in order. yaw and vertical need no horizontal "
                         "room; north/south/east/west each fly one leg and return")
    ap.add_argument("--dry-run", action="store_true", help="preflight only, never arms")
    ap.add_argument("--no-capture", action="store_true", help="do not save feed frames")
    ap.add_argument("--max-alt", type=float, default=12.0)
    ap.add_argument("--max-radius", type=float, default=20.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    tests = [x.strip() for x in args.tests.split(",") if x.strip()]
    unknown = [t for t in tests if t not in ALL_TESTS + ("south", "west")]
    if unknown:
        print(f"unknown test(s): {', '.join(unknown)}")
        return
    moving = [t for t in tests if t in TRANSLATING]

    # Flight 01, bug 5: a promise made in conversation is worth nothing. The
    # program states its own plan, from its own configuration, before it arms.
    print(f"plan: {' -> '.join(tests)} at {args.alt:.0f} m")
    print(f"      horizontal legs: {', '.join(moving) if moving else 'NONE, it stays over one spot'}")
    print(f"      ceiling {args.max_alt:.0f} m, fence {args.max_radius:.0f} m")

    env = Envelope(max_alt=args.max_alt, max_radius=args.max_radius)
    capture = None
    if not args.no_capture and not args.dry_run:
        capture = (Path(__file__).resolve().parents[2] / "broll"
                   / f"feed-{time.strftime('%Y%m%d-%H%M%S')}")

    async with BridgeClient(args.url) as bc:
        async with FlightSession(bc, name="first-flight", envelope=env,
                                 capture_dir=capture) as fs:
            if args.dry_run:
                await fs.preflight(wait_for_switch_s=0)
                print("\nDRY RUN: preflight passed, would take off now.")
                print(fs.summary())
                return
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

            await fs.preflight()
            await fs.takeoff_and_take_control()
            fs.log(f"climbing to {args.alt:.0f} m")
            await fs.climb_to(args.alt)
            t = fs.bc.telemetry
            assert t is not None
            fs.note("climb", f"reached {t.alt:.1f} m of {args.alt:.0f} m",
                    abs(t.alt - args.alt) < 1.5, alt=round(t.alt, 2))
            await run_tests(fs, tests, args.alt, args.leg)

        try:
            await bc.record_stop()
            print("onboard recording stopped")
        except Exception:  # noqa: BLE001
            pass
        print(fs.summary())


if __name__ == "__main__":
    asyncio.run(main())
