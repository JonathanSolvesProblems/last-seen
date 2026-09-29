"""Format the SD card in the camera, which is the only format DJI trusts.

    python scripts/format_card.py --check      # read the card's state, change nothing
    python scripts/format_card.py              # read it, then format after confirming

A PC format produces a perfectly good exFAT filesystem and DJI GO still says
"SD Card Formatting is Recommended", because that warning is about which device
laid the filesystem down. The camera's own format clears it, and the SDK can
drive it, so this does not need DJI GO and does not need the bridge released.

THIS ERASES THE CARD. It refuses to run if the camera reports the card is not
empty, unless --force is passed, because a card with footage on it has probably
not been imported yet:

    python brain/scripts/import_footage.py
    python brain/scripts/verify_footage.py --against D:/DCIM

Nothing here arms the aircraft or moves anything except the camera's filesystem.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.client import BridgeClient, BridgeError  # noqa: E402


def parse_state(text: str) -> dict:
    out: dict[str, str] = {}
    for key, value in re.findall(r"(\w+)=(\S+)", text):
        out[key] = value
    return out


def looks_empty(state: dict, total_hint_mb: float = 0.0) -> tuple[bool, str]:
    """A card the camera says is empty. Conservative: unknown means not empty."""
    remaining = state.get("remainingMB")
    if remaining is None:
        return False, "the camera did not report remaining space"
    try:
        mb = float(remaining)
    except ValueError:
        return False, f"could not read remaining space ({remaining!r})"
    # The card is 476 GB. Anything close to that is a card with nothing on it.
    if mb > 400_000:
        return True, f"{mb / 1000:.0f} GB free, which is the whole card"
    return False, f"only {mb / 1000:.1f} GB free, so there is something on it"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--check", action="store_true", help="read the state and stop")
    ap.add_argument("--force", action="store_true",
                    help="format even though the camera says the card is not empty")
    args = ap.parse_args()

    async with BridgeClient(args.url) as bc:
        t = await bc.wait_for_telemetry(15)
        print(f"aircraft: {bc.status.get('product') or 'connected'}, battery {t.battery}%")

        try:
            before = await bc.sd_state()
        except BridgeError as e:
            print(f"could not read the card state: {e}")
            return 1
        print(f"\nbefore: {before}")
        state = parse_state(before)

        if state.get("inserted") == "false":
            print("\nno card in the camera. Put it in and try again.")
            return 1

        empty, why = looks_empty(state)
        print(f"        {why}")

        if args.check:
            print("\n--check only, nothing was changed")
            return 0

        if not empty and not args.force:
            print("\nREFUSING TO FORMAT: the camera says this card has data on it.")
            print("  Import and verify it first:")
            print("    python brain/scripts/import_footage.py")
            print("    python brain/scripts/verify_footage.py --against D:/DCIM")
            print("  Then re-run with --force if you are sure.")
            return 1

        print("\nformatting in the camera, this erases the card")
        try:
            await bc.format_sd()
        except BridgeError as e:
            print(f"format failed: {e}")
            return 1

        await asyncio.sleep(3)
        after = await bc.sd_state()
        print(f"\nafter:  {after}")
        done = parse_state(after)
        if done.get("formatted") == "true" and done.get("inserted") == "true":
            print("\ncard formatted by the camera. DJI's formatting warning should be gone.")
            return 0
        print("\nthe camera did not report a clean formatted card; check DJI GO before flying")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
