"""Watch the RC mode switch live. Used to find which physical switch is S1.

    python scripts/watch_switch.py [--seconds 45]

The Phantom 3 Standard's remote does not report its switch positions through the
SDK's hardware-state callback, so the bridge derives the letter from the flight
mode string instead. That works, but it means the only way to learn which toggle
is the flight mode switch on a given controller is to move them and watch.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.client import BridgeClient  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--seconds", type=float, default=45)
    args = ap.parse_args()
    async with BridgeClient(args.url) as bc:
        t = await bc.wait_for_telemetry(10)
        last = (t.rc_mode_switch, t.flight_mode)
        print(f"start: S1={last[0]} mode={last[1]}", flush=True)
        print(f"move the switches now, watching for {args.seconds:.0f} s", flush=True)
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            try:
                t = await bc.next_telemetry(3)
            except asyncio.TimeoutError:
                continue
            cur = (t.rc_mode_switch, t.flight_mode)
            if cur != last:
                print(f"{time.strftime('%H:%M:%S')} CHANGED -> S1={cur[0]} mode={cur[1]}", flush=True)
                last = cur
        print(f"done, final: S1={last[0]} mode={last[1]}")


if __name__ == "__main__":
    asyncio.run(main())
