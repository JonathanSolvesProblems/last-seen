"""Turn a day's run logs into a flight folder, at home, while it is still fresh.

    python scripts/new_flight.py --number 02              # today's logs
    python scripts/new_flight.py --number 02 --since 14:00
    python scripts/new_flight.py --number 02 --dry-run

Collects everything the flight scripts wrote today out of runs/, copies it into
flights/<date>-flight-NN/data/, runs the analysis, and leaves a README stub with
the measured numbers already in it.

The point is that the writeup starts from the data. Flight 01's README was
written the same evening and a claim check still caught three figures that were
spelled as words and therefore unverifiable; writing one a week later from
memory would be worse.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1]
REPO = BRAIN.parent
RUNS = BRAIN / "runs"
KINDS = ("first-flight", "follow", "follow-me", "teleop")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--number", required=True, help="flight number, e.g. 02")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--since", default="00:00", help="HH:MM, ignore earlier runs")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cutoff = datetime.strptime(f"{args.date} {args.since}", "%Y-%m-%d %H:%M").timestamp()
    folder = REPO / "flights" / f"{args.date}-flight-{args.number}"

    found: list[tuple[str, Path]] = []
    for kind in KINDS:
        d = RUNS / kind
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.stat().st_mtime >= cutoff:
                found.append((kind, p))

    if not found:
        print(f"no run logs in {RUNS} from {args.date} {args.since} onward")
        return 1

    print(f"{folder}")
    by_kind: dict[str, int] = {}
    for kind, p in found:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    for kind, n in by_kind.items():
        print(f"  {kind:<14} {n} files")
    if args.dry_run:
        print("\ndry run, nothing copied")
        return 0

    for kind, p in found:
        dest = folder / "data" / kind
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest / p.name)

    readme = folder / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# Flight {args.number}, {args.date}\n\n"
            f"Written for: me, and anyone reading the repo alongside the video.\n\n"
            f"TODO: what this flight was for, and what actually happened.\n\n"
            f"Every number below is recomputed from the logs in `data/` by\n"
            f"`../analyse_flight.py`. Do not type figures from memory.\n\n"
            f"```\npython flights/analyse_flight.py flights/{folder.name}\n```\n\n"
            f"## Conditions\n\nTODO\n\n## What was measured\n\nTODO, from findings.json\n\n"
            f"## What was not tested\n\nTODO. State the gaps plainly; they matter more\n"
            f"than the passes.\n\n## Bugs\n\nTODO, see BUGS.md if there were any.\n",
            encoding="utf-8")
        print(f"\n  README stub written, fill it in from the numbers below")

    print()
    subprocess.run([sys.executable, str(REPO / "flights" / "analyse_flight.py"),
                    str(folder), "--write"], check=False)
    print(f"\nnext: import the card, then write up {readme}")
    print(f"      python brain/scripts/import_footage.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
