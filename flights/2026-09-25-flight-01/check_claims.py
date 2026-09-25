"""Fail if README.md and the raw logs disagree.

    python flights/2026-09-25-flight-01/check_claims.py

A writeup gets edited after the data is collected, and a number that was true on
the day quietly becomes a number that is not. This rebuilds every headline figure
from the logs and asserts the prose still contains it. It is the cheapest
protection there is against a video being narrated over a claim the data does not
support.

Exit code 0 if the writeup is honest, 1 if it is not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    subprocess.run([sys.executable, str(HERE / "analyse.py"), "--write"],
                   check=True, capture_output=True)
    f = json.loads((HERE / "findings.json").read_text(encoding="utf-8"))
    readme = (HERE / "README.md").read_text(encoding="utf-8")

    north = {r["run"]: r for r in f["axis_checks"] if r["check"] == "axis north"}
    yaw = next(r for r in f["axis_checks"] if r["check"] == "axis yaw cw")
    vert = next(r for r in f["axis_checks"] if r["check"] == "vertical")
    ground = next(r for r in f["follow"] if r["detection_rate_pct"] > 0)
    black = next(r for r in f["follow"] if r["detection_rate_pct"] == 0)
    dns = sorted(f["north_dn_m"], reverse=True)
    des = [r["de"] for r in sorted(north.values(), key=lambda r: -r["dn"])]

    claims: list[tuple[str, str]] = [
        ("attempts", f"{f['attempts_total']} attempts"),
        ("reached the air", f"{f['attempts_airborne']} reached the air"),
        ("pilot takeovers", f"{f['pilot_takeovers']} ended because I took control"),
        ("max altitude", f"{f['max_altitude_m']} m"),
        ("telemetry samples", f"{f['telemetry_samples']} samples"),
        ("north leg 1", f"N+{dns[0]:.2f} m, E+{des[0]:.2f} m"),
        ("north leg 2", f"N+{dns[1]:.2f} m, E+{des[1]:.2f} m"),
        ("yaw", f"heading +{yaw['dheading']:.0f} deg"),
        ("vertical", f"+{vert['dalt']:.1f} m"),
        ("station keeping", f"**{f['control']['max_radius_m']} m**"),
        ("final radius", f"{f['control']['final_radius_m']} m from where it started"),
        ("position samples", f"{f['control']['samples_with_position']} position samples"),
        ("detection frames", f"| {ground['frames']} | {ground['frames_with_person']} "
                             f"({ground['detection_rate_pct']}%) | {ground['mean_confidence']} "
                             f"| {ground['mean_abs_offset']:.3f} |"),
        ("black frames", f"| {black['frames']} | 0 (0.0%)"),
    ]

    bad = [(name, text) for name, text in claims if text not in readme]
    for name, text in claims:
        mark = "FAIL" if (name, text) in bad else "ok  "
        print(f"  {mark} {name:<18} {text}")
    if bad:
        print(f"\n{len(bad)} claim(s) in README.md are not supported by the logs.")
        return 1
    print(f"\nall {len(claims)} headline claims match the data in data/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
