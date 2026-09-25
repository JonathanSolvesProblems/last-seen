"""Derive every number in this flight's README from the raw logs.

    python flights/2026-09-25-flight-01/analyse.py            # print the tables
    python flights/2026-09-25-flight-01/analyse.py --write    # refresh findings.json

Nothing in the writeup is typed from memory. This reads the telemetry and the
step reports the flight scripts wrote at the time, recomputes the measurements,
and emits findings.json. check_claims.py then asserts the README agrees with it,
so a number cannot drift out of sync with the data it came from.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


@dataclass
class Attempt:
    run: str
    when: str
    aborted: str | None
    steps_ok: int
    steps_failed: int
    reached_air: bool
    max_alt: float
    samples: int
    battery_start: int | None
    battery_end: int | None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def flight_attempts() -> list[Attempt]:
    out: list[Attempt] = []
    for report in sorted((DATA / "first-flight").glob("*.json")):
        payload = json.loads(report.read_text(encoding="utf-8"))
        rows = load_jsonl(report.with_suffix(".jsonl"))
        alts = [r["alt"] for r in rows] or [0.0]
        bats = [r["bat"] for r in rows]
        steps = payload.get("steps", [])
        out.append(Attempt(
            run=report.stem,
            when=payload.get("when", ""),
            aborted=payload.get("aborted"),
            steps_ok=sum(1 for s in steps if s.get("ok") is True),
            steps_failed=sum(1 for s in steps if s.get("ok") is False),
            reached_air=max(alts) > 0.5,
            max_alt=round(max(alts), 2),
            samples=len(rows),
            battery_start=bats[0] if bats else None,
            battery_end=bats[-1] if bats else None,
        ))
    return out


def axis_results() -> list[dict]:
    """Every axis measurement the scripts recorded, across all attempts."""
    out = []
    for report in sorted((DATA / "first-flight").glob("*.json")):
        payload = json.loads(report.read_text(encoding="utf-8"))
        for s in payload.get("steps", []):
            if s["name"].startswith("axis") or s["name"] in ("vertical", "climb"):
                out.append({"run": report.stem, "check": s["name"], "ok": s["ok"],
                            "detail": s["detail"], **s.get("data", {})})
    return out


def follow_results() -> list[dict]:
    out = []
    for log in sorted((DATA / "follow").glob("*.jsonl")):
        rows = load_jsonl(log)
        if not rows:
            continue
        seen = [r for r in rows if r.get("people", 0) > 0]
        offs = [abs(r["offset"]) for r in rows if r.get("offset") is not None]
        confs = [r["conf"] for r in rows if r.get("conf") is not None]
        out.append({
            "run": log.stem,
            "frames": len(rows),
            "frames_with_person": len(seen),
            "detection_rate_pct": round(100 * len(seen) / len(rows), 1),
            "mean_abs_offset": round(sum(offs) / len(offs), 4) if offs else None,
            "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        })
    return out


def displacement(run: str, step_name: str) -> dict | None:
    for row in axis_results():
        if row["run"] == run and row["check"] == step_name:
            return row
    return None


def control_quality() -> dict:
    """How well the aircraft held station, measured on the clean run's hover samples.

    Uses only samples where the commanded velocity was zero is not available in
    the log, so this reports the spread of position while the script was in its
    hold and return phases, which is the honest proxy: it includes commanded
    motion and is therefore an upper bound on drift, not a drift figure.
    """
    rows = load_jsonl(DATA / "first-flight" / "20260925-174101.jsonl")
    pos = [(r["n"], r["e"]) for r in rows if r.get("n") is not None]
    if not pos:
        return {}
    radii = [math.hypot(n, e) for n, e in pos]
    alts = [r["alt"] for r in rows]
    return {
        "samples_with_position": len(pos),
        "max_radius_m": round(max(radii), 2),
        "final_radius_m": round(radii[-1], 2),
        "max_alt_m": round(max(alts), 2),
        "note": "radius includes commanded motion, so it is an upper bound not a drift figure",
    }


def build() -> dict:
    attempts = flight_attempts()
    axes = axis_results()
    airborne = [a for a in attempts if a.reached_air]
    # "completed" means it flew the whole sequence and landed itself. An attempt
    # that never armed also has aborted=None, so reaching the air is part of it.
    completed = [a for a in attempts if a.aborted is None and a.reached_air]
    norths = [r for r in axes if r["check"] == "axis north"]
    return {
        "date": "2026-09-25",
        "location": "park, uncontrolled airspace",
        "aircraft": "DJI Phantom 3 Standard",
        "attempts_total": len(attempts),
        "attempts_airborne": len(airborne),
        "attempts_completed": len(completed),
        "pilot_takeovers": sum(1 for a in attempts if a.aborted and "pilot took control" in a.aborted),
        "battery_start_pct": max((a.battery_start for a in attempts if a.battery_start), default=None),
        "battery_end_pct": min((a.battery_end for a in attempts if a.battery_end), default=None),
        "max_altitude_m": round(max((a.max_alt for a in attempts), default=0.0), 2),
        "telemetry_samples": sum(a.samples for a in attempts),
        "north_legs": len(norths),
        "north_dn_m": [r["dn"] for r in norths],
        "north_de_m": [r["de"] for r in norths],
        "attempts": [asdict(a) for a in attempts],
        "axis_checks": axes,
        "follow": follow_results(),
        "control": control_quality(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    f = build()

    print(f"FLIGHT 01  {f['date']}  {f['aircraft']}")
    print(f"  attempts {f['attempts_total']}  reached the air {f['attempts_airborne']}  "
          f"completed the sequence {f['attempts_completed']}  "
          f"pilot takeovers {f['pilot_takeovers']}")
    print(f"  battery {f['battery_start_pct']}% -> {f['battery_end_pct']}%   "
          f"max altitude {f['max_altitude_m']} m   {f['telemetry_samples']} telemetry samples")
    print("\n  axis checks")
    for r in f["axis_checks"]:
        print(f"    {r['run']}  {r['check']:<12} {'ok  ' if r['ok'] else 'FAIL'}  {r['detail']}")
    print("\n  detection")
    for r in f["follow"]:
        print(f"    {r['run']}  {r['frames']:4d} frames  "
              f"{r['detection_rate_pct']:5.1f}% with a person  "
              f"conf {r['mean_confidence']}  mean |offset| {r['mean_abs_offset']}")
    print("\n  attempts")
    for a in f["attempts"]:
        end = a["aborted"] or "completed"
        print(f"    {a['run']}  air={str(a['reached_air']):<5} maxalt={a['max_alt']:5.2f}  "
              f"{a['steps_ok']} ok / {a['steps_failed']} failed  {end}")

    if args.write:
        (HERE / "findings.json").write_text(json.dumps(f, indent=2), encoding="utf-8")
        print(f"\nwrote {HERE / 'findings.json'}")


if __name__ == "__main__":
    main()
