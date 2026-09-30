"""Recompute a flight's numbers from its logs. Works for any flight folder.

    python flights/analyse_flight.py flights/2026-10-03-flight-02
    python flights/analyse_flight.py flights/2026-10-03-flight-02 --write

Flight 01 has its own analyse.py, written before this one existed and kept as
the record of that day. Everything after it uses this.

It reads whatever is in <folder>/data/:

    first-flight/*.json + .jsonl   axis and envelope checks
    follow/*-perception.jsonl      detection on the feed
    follow-me/*-perception.jsonl   detection, range keeping and guard behaviour

and writes findings.json. The rule for the flights/ folder is that no number
appears in a writeup unless a script can rederive it from the logs beside it.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def flights(data: Path) -> dict:
    attempts, checks = [], []
    # Every kind of run writes the same report shape, so count them all. Only
    # looking in first-flight/ reported "0 attempts" for a day whose flying was
    # all done by follow_me.
    reports = sorted(p for kind in ("first-flight", "follow", "follow-me")
                     for p in (data / kind).glob("*.json"))
    for report in reports:
        payload = json.loads(report.read_text(encoding="utf-8"))
        rows = load_jsonl(report.with_suffix(".jsonl"))
        alts = [r["alt"] for r in rows] or [0.0]
        bats = [r["bat"] for r in rows]
        steps = payload.get("steps", [])
        attempts.append({
            "run": report.stem, "when": payload.get("when", ""),
            "aborted": payload.get("aborted"),
            "reached_air": max(alts) > 0.5, "max_alt": round(max(alts), 2),
            "samples": len(rows),
            "battery_start": bats[0] if bats else None,
            "battery_end": bats[-1] if bats else None,
            "frames_saved": payload.get("frames_saved", 0),
        })
        for s in steps:
            if s["name"].startswith("axis") or s["name"] in ("vertical", "climb", "follow"):
                checks.append({"run": report.stem, "check": s["name"], "ok": s["ok"],
                               "detail": s["detail"], **s.get("data", {})})
    return {"attempts": attempts, "checks": checks}


def perception(data: Path, kind: str) -> list[dict]:
    out = []
    # Perception logs only. The earlier version globbed the telemetry files too
    # and reported every run twice, which is the sort of thing that turns into a
    # doubled count in a writeup.
    for log in sorted((data / kind).glob("*-perception.jsonl")):
        rows = load_jsonl(log)
        if not rows or "people" not in rows[0]:
            continue
        seen = [r for r in rows if r.get("people", 0) > 0]
        locked = [r for r in rows if r.get("locked")]
        rng = [r["range_m"] for r in rows if r.get("range_m") is not None]
        confs = [r["conf"] for r in rows if r.get("conf") is not None]
        offs = [abs(r["offset"]) for r in rows if r.get("offset") is not None]
        stale = [r for r in rows if r.get("fresh") is False
                 and math.hypot(r.get("vn", 0.0), r.get("ve", 0.0)) > 0.05]
        alts = [r["alt"] for r in rows if r.get("alt") is not None]
        out.append({
            "run": log.stem, "kind": kind, "frames": len(rows),
            "stage": rows[0].get("stage"),
            "altitude_m": round(sum(alts) / len(alts), 1) if alts else None,
            "frames_with_person": len(seen),
            "detection_rate_pct": round(100 * len(seen) / len(rows), 1),
            "locked_pct": round(100 * len(locked) / len(rows), 1) if locked else None,
            "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
            "mean_abs_offset": round(sum(offs) / len(offs), 4) if offs else None,
            "range_mean_m": round(sum(rng) / len(rng), 2) if rng else None,
            "range_min_m": round(min(rng), 2) if rng else None,
            "guard_fires": sum(1 for r in rows if r.get("guard")),
            "flew_on_stale_fix": len(stale),
        })
    return out


def build(folder: Path) -> dict:
    data = folder / "data"
    f = flights(data)
    attempts = f["attempts"]
    airborne = [a for a in attempts if a["reached_air"]]
    completed = [a for a in attempts if a["aborted"] is None and a["reached_air"]]
    bats = [a["battery_start"] for a in attempts if a["battery_start"]]
    bate = [a["battery_end"] for a in attempts if a["battery_end"]]
    return {
        "flight": folder.name,
        "attempts_total": len(attempts),
        "attempts_airborne": len(airborne),
        "attempts_completed": len(completed),
        "pilot_takeovers": sum(1 for a in attempts
                               if a["aborted"] and "pilot took control" in a["aborted"]),
        "battery_start_pct": max(bats) if bats else None,
        "battery_end_pct": min(bate) if bate else None,
        "max_altitude_m": round(max((a["max_alt"] for a in attempts), default=0.0), 2),
        "telemetry_samples": sum(a["samples"] for a in attempts),
        "frames_saved": sum(a["frames_saved"] for a in attempts),
        "attempts": attempts,
        "axis_checks": f["checks"],
        "perception": perception(data, "follow") + perception(data, "follow-me"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    f = build(args.folder)

    print(f"{f['flight']}")
    print(f"  attempts {f['attempts_total']}  reached the air {f['attempts_airborne']}  "
          f"completed {f['attempts_completed']}  pilot takeovers {f['pilot_takeovers']}")
    print(f"  battery {f['battery_start_pct']}% -> {f['battery_end_pct']}%   "
          f"max altitude {f['max_altitude_m']} m   {f['telemetry_samples']} telemetry samples"
          f"   {f['frames_saved']} feed frames")
    if f["axis_checks"]:
        print("\n  checks")
        for r in f["axis_checks"]:
            print(f"    {r['run']}  {r['check']:<12} {'ok  ' if r['ok'] else 'FAIL'}  {r['detail']}")
    if f["perception"]:
        print("\n  perception")
        for r in f["perception"]:
            bits = [f"{r['frames']:4d} frames", f"{r['detection_rate_pct']:5.1f}% with a person"]
            if r["locked_pct"] is not None:
                bits.append(f"{r['locked_pct']:.1f}% locked")
            if r["altitude_m"] is not None:
                bits.append(f"at {r['altitude_m']} m")
            if r["range_mean_m"] is not None:
                bits.append(f"range {r['range_mean_m']} m (min {r['range_min_m']})")
            if r["guard_fires"]:
                bits.append(f"guard fired {r['guard_fires']}")
            if r["flew_on_stale_fix"]:
                bits.append(f"STALE-FIX FLYING {r['flew_on_stale_fix']}")
            print(f"    {r['run']:<34} {r['stage'] or r['kind']:<7} " + "  ".join(bits))

    if args.write:
        (args.folder / "findings.json").write_text(json.dumps(f, indent=2), encoding="utf-8")
        print(f"\nwrote {args.folder / 'findings.json'}")


if __name__ == "__main__":
    main()
