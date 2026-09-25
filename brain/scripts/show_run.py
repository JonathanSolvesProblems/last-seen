"""Print a flight's telemetry log as a readable trace.

    python scripts/show_run.py [path.jsonl] [--every 4]

Defaults to the most recent first-flight log.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--every", type=int, default=4)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1] / "runs" / "first-flight"
    path = Path(args.path) if args.path else max(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"{path.name}  {len(rows)} samples")
    for r in rows[:: args.every]:
        n = r.get("n")
        e = r.get("e")
        pos = f" N{n:+6.1f} E{e:+6.1f}" if n is not None else ""
        print(f"alt={r['alt']:6.2f} vu={r['vu']:+5.2f} hdg={r['hdg']:5.1f}{pos} "
              f"mode={r['mode']:<8} vs={str(r['vs']):<5} roll={r['roll']:+5.1f} pitch={r['pitch']:+5.1f} bat={r['bat']}")


if __name__ == "__main__":
    main()
