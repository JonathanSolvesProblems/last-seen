"""Pull the onboard 2.7K clips off the SD card and file them by test flight.

    python scripts/import_footage.py --list          # what is on the card
    python scripts/import_footage.py                 # copy and verify
    python scripts/import_footage.py --clear-card    # ...then delete the originals

Each clip's modification time is the moment recording stopped, and every flight
script stops recording as it finishes, so a clip can be matched to the run that
produced it by comparing that timestamp against the run's last telemetry sample.
That pairing is the point of this script: without it, a folder of DJI_0007.MOV
tells you nothing about which flight it came from six weeks later.

Footage is stored OUTSIDE the repo by default, because the repo lives in a
OneDrive folder and a flight day is several GB. The repo keeps a pointer file
and the manifest, both of which are small and worth keeping in version control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNS = Path(__file__).resolve().parents[1] / "runs"
DEFAULT_DEST = Path(os.environ.get("DRONE_FOOTAGE_DIR",
                                   Path.home() / "Videos" / "DroneProject"))
VIDEO_EXT = {".mov", ".mp4", ".dng", ".jpg"}
# A clip is matched to a run if recording stopped within this many seconds of
# the run's last telemetry sample. Generous, because the aircraft's clock and
# the laptop's clock are only roughly aligned, and because a clip can keep
# rolling well past the end of a run when the link drops before record_stop
# is delivered (flight 02: the card ran on for eight minutes after the script
# died, through the manual landing).
MATCH_WINDOW_S = 600.0

# The project did not exist before this. Anything older is a camera whose clock
# was never set, not a flight.
EARLIEST_PLAUSIBLE = datetime(2026, 1, 1).timestamp()


@dataclass
class Clip:
    path: Path
    mtime: float
    size: int
    run: str | None = None


@dataclass
class Run:
    name: str
    ended: float
    steps: list = field(default_factory=list)
    aborted: str | None = None


def find_card() -> Path | None:
    """The SD card is whichever removable volume has a DCIM/ on it."""
    for letter in "DEFGHIJKLMNOP":
        d = Path(f"{letter}:/DCIM")
        if d.is_dir():
            return d
    return None


def load_runs() -> list[Run]:
    out: list[Run] = []
    for report in sorted(RUNS.glob("*/*.json")):
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(Run(name=f"{report.parent.name}/{report.stem}",
                       ended=report.stat().st_mtime,
                       steps=payload.get("steps", []),
                       aborted=payload.get("aborted")))
    # follow runs write only a jsonl, no report
    for log in sorted(RUNS.glob("follow/*.jsonl")):
        out.append(Run(name=f"follow/{log.stem}", ended=log.stat().st_mtime))
    return out


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def flight_number(dest: Path, day: str) -> str:
    """Stable per day: the same day always lands in the same flight folder."""
    existing = sorted(p.name for p in dest.glob("flight-*") if p.is_dir())
    for name in existing:
        if name.endswith(day):
            return name
    return f"flight-{len(existing) + 1:02d}-{day}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", help="DCIM folder (default: auto-detect the card)")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                    help=f"footage store (default {DEFAULT_DEST})")
    ap.add_argument("--list", action="store_true", help="show what is on the card and exit")
    ap.add_argument("--clear-card", action="store_true",
                    help="delete each original AFTER its copy is verified by hash")
    ap.add_argument("--min-mb", type=float, default=1.0, help="skip clips smaller than this")
    args = ap.parse_args()

    src = Path(args.src) if args.src else find_card()
    if src is None or not src.is_dir():
        print("no SD card found (looked for a removable drive with DCIM/). "
              "Pass --from if it is somewhere else.")
        return 1

    clips = [Clip(p, p.stat().st_mtime, p.stat().st_size)
             for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    clips = [c for c in clips if c.size >= args.min_mb * 1024 * 1024]
    # A file whose clock was never set carries the card's default date, and that
    # invents a whole flight folder years in the past. Flight 02's import made a
    # "flight-02-2013-12-31" from one stray DNG and renumbered the real day.
    stale = [c for c in clips if c.mtime < EARLIEST_PLAUSIBLE]
    for c in stale:
        when = datetime.fromtimestamp(c.mtime).strftime("%Y-%m-%d")
        print(f"  skipping {c.path.name}: timestamp {when} predates the project, "
              f"so the camera clock was unset")
    clips = [c for c in clips if c.mtime >= EARLIEST_PLAUSIBLE]
    if not clips:
        print(f"nothing to import from {src}")
        return 0

    runs = load_runs()
    for c in clips:
        near = [r for r in runs if abs(r.ended - c.mtime) <= MATCH_WINDOW_S]
        if near:
            c.run = min(near, key=lambda r: abs(r.ended - c.mtime)).name

    total = sum(c.size for c in clips)
    print(f"{src}: {len(clips)} clips, {total / 1e9:.2f} GB")
    for c in clips:
        when = datetime.fromtimestamp(c.mtime).strftime("%H:%M:%S")
        print(f"  {c.path.name:<16} {c.size / 1e6:8.1f} MB  stopped {when}  "
              f"-> {c.run or 'no matching run'}")
    if args.list:
        return 0

    days = sorted({datetime.fromtimestamp(c.mtime).strftime("%Y-%m-%d") for c in clips})
    args.dest.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    copied = skipped = 0
    for day in days:
        folder = args.dest / flight_number(args.dest, day) / "drone"
        folder.mkdir(parents=True, exist_ok=True)
        print(f"\n{day} -> {folder}")
        for c in [x for x in clips if datetime.fromtimestamp(x.mtime).strftime("%Y-%m-%d") == day]:
            stamp = datetime.fromtimestamp(c.mtime).strftime("%H%M%S")
            tag = (c.run or "unmatched").replace("/", "_")
            out = folder / f"{stamp}_{tag}_{c.path.stem}{c.path.suffix.lower()}"
            if out.exists() and out.stat().st_size == c.size:
                print(f"  already there: {out.name}")
                skipped += 1
            else:
                t0 = time.monotonic()
                shutil.copy2(c.path, out)
                rate = c.size / 1e6 / max(time.monotonic() - t0, 1e-6)
                print(f"  {c.path.name} -> {out.name}  ({c.size / 1e6:.0f} MB, {rate:.0f} MB/s)")
                copied += 1
            digest = sha256(out)
            if digest != sha256(c.path):
                print(f"  CHECKSUM MISMATCH on {out.name}; leaving the original in place")
                continue
            manifest_rows.append({
                "file": out.name, "sd_name": c.path.name, "bytes": c.size,
                "sha256": digest, "recorded_until": datetime.fromtimestamp(c.mtime).isoformat(),
                "run": c.run,
            })
            if args.clear_card:
                c.path.unlink()
                print(f"  cleared from the card: {c.path.name}")

        manifest = folder.parent / "footage.json"
        manifest.write_text(json.dumps({"imported": datetime.now().isoformat(),
                                        "source": str(src), "clips": manifest_rows},
                                       indent=2), encoding="utf-8")
        print(f"  manifest: {manifest}")

    pointer = REPO / "broll" / "WHERE_THE_FOOTAGE_LIVES.txt"
    pointer.write_text(
        f"Onboard 2.7K footage is stored outside this repo, because the repo is inside\n"
        f"a OneDrive folder and one flight day is several GB.\n\n"
        f"    {args.dest}\n\n"
        f"Set DRONE_FOOTAGE_DIR to move it. Import with:\n"
        f"    python brain/scripts/import_footage.py\n",
        encoding="utf-8")

    print(f"\n{copied} copied, {skipped} already present, verified by SHA-256")
    if not args.clear_card:
        print("the card still holds the originals; re-run with --clear-card to free it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
