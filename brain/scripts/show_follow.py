"""Read a follow-me log and say whether the range guard behaved.

    python scripts/show_follow.py [runs/follow-me/<stamp>.jsonl]

Two different things can put the aircraft inside its range floor, and only one
of them is the guard's job:

  commanded   the aircraft flew at the person. The guard must have fired.
  approached  the person walked at a stationary aircraft. The guard cannot
              prevent that and should not try: reversing away from someone
              walking toward you just moves the hazard behind the aircraft,
              where nothing is looking.

This separates them, so "min range 6.3 m, guard fired 0 times" can be read as
either a fault or a fact.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs" / "follow-me"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--min-standoff", type=float, default=None,
                    help="override; otherwise inferred from the run report")
    args = ap.parse_args()

    path = Path(args.path) if args.path else max(RUNS.glob("*-perception.jsonl"),
                                                 key=lambda p: p.stat().st_mtime)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r.get("range_m") is not None]
    if not rows:
        print(f"{path.name}: no range estimates in this run")
        return

    floor = args.min_standoff or 5.0

    inside = [r for r in rows if r["range_m"] < floor]
    # "closing" is logged at the time, where the bearing to the target is known.
    # Moving is not the same as moving toward, and only the second one matters.
    commanded = [r for r in inside if r.get("closing", 0.0) > 0.05]
    fired = [r for r in rows if r.get("guard")]
    stale = [r for r in rows if r.get("fresh") is False
             and math.hypot(r.get("vn", 0.0), r.get("ve", 0.0)) > 0.05]

    print(f"{path.name}  {len(rows)} samples with a range estimate")
    print(f"  range        min {min(r['range_m'] for r in rows):.1f} m, "
          f"mean {sum(r['range_m'] for r in rows) / len(rows):.1f} m, "
          f"max {max(r['range_m'] for r in rows):.1f} m")
    print(f"  floor        {floor:.1f} m")
    print(f"  inside floor {len(inside)} samples "
          f"({100 * len(inside) / len(rows):.1f}%)")
    print(f"  ...closing   {len(commanded)} of those commanded motion TOWARD the person")
    print(f"  guard fired  {len(fired)} samples")
    locked = [r for r in rows if r.get("locked")]
    print(f"  locked       {len(locked)} ({100 * len(locked) / len(rows):.1f}%)")
    print(f"  flew on a stale fix  {len(stale)} samples")

    problems = []
    if commanded and not fired:
        problems.append("THE GUARD SHOULD HAVE FIRED: the aircraft commanded closing "
                        "motion while inside the floor")
    if stale:
        problems.append(f"translated on a stale position estimate in {len(stale)} samples")
    if problems:
        print()
        for p in problems:
            print(f"  PROBLEM: {p}")
        return
    if fired:
        print("\n  verdict: the guard engaged, which is what it is for.")
    elif inside:
        print("\n  verdict: went inside the floor without ever commanding closing motion,")
        print("  so the person closed the gap. The guard correctly did nothing: it only")
        print("  removes commanded closing, and reversing away from someone walking at")
        print("  you just moves the hazard behind the aircraft where nothing is looking.")
    else:
        print("\n  verdict: never went inside the floor.")


if __name__ == "__main__":
    main()
