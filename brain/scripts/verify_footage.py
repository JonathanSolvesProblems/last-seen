"""Re-hash the imported footage and prove it matches the manifest.

    python scripts/verify_footage.py
    python scripts/verify_footage.py --against D:/DCIM     # also check the card

Run this immediately before erasing an SD card. import_footage.py verifies each
file as it copies, but that was then: a verification that happened yesterday is
not evidence about a disk today, and the card is about to be the only other copy.

Exit code 0 only if every clip in every manifest re-hashes to its recorded value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

DEFAULT_DEST = Path(os.environ.get("DRONE_FOOTAGE_DIR",
                                   Path.home() / "Videos" / "DroneProject"))


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--against", type=Path, default=None,
                    help="also confirm each clip still on the card matches")
    args = ap.parse_args()

    manifests = sorted(args.dest.glob("*/footage.json"))
    if not manifests:
        print(f"no manifests under {args.dest}. Nothing has been imported.")
        return 1

    total = ok = bad = missing = 0
    card_seen = 0
    for manifest in manifests:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        folder = manifest.parent / "drone"
        print(f"\n{manifest.parent.name}  ({len(payload['clips'])} clips)")
        for clip in payload["clips"]:
            total += 1
            path = folder / clip["file"]
            if not path.exists():
                print(f"  MISSING  {clip['file']}")
                missing += 1
                continue
            if path.stat().st_size != clip["bytes"]:
                print(f"  SIZE     {clip['file']}: {path.stat().st_size} != {clip['bytes']}")
                bad += 1
                continue
            digest = sha256(path)
            if digest != clip["sha256"]:
                print(f"  CORRUPT  {clip['file']}")
                bad += 1
                continue
            ok += 1
            note = ""
            if args.against:
                src = Path(args.against) / "100MEDIA" / clip["sd_name"]
                if not src.exists():
                    src = next(Path(args.against).rglob(clip["sd_name"]), None)  # type: ignore[arg-type]
                if src and src.exists():
                    card_seen += 1
                    note = "  (card copy matches)" if sha256(src) == digest else "  (CARD DIFFERS)"
            print(f"  ok       {clip['file']}  {clip['bytes'] / 1e6:.0f} MB"
                  f"  <- {clip['run'] or 'unmatched run'}{note}")

    print(f"\n{ok}/{total} verified by SHA-256")
    if args.against:
        print(f"{card_seen} of them still present on the card")
    if bad or missing:
        print(f"{bad} corrupt, {missing} missing. DO NOT ERASE THE CARD.")
        return 1
    print("every imported clip re-hashes to its recorded value; the card is safe to erase")
    return 0


if __name__ == "__main__":
    sys.exit(main())
