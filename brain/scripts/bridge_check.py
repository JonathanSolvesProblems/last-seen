"""Non-interactive health check of the bridge (phone or sim).

    adb forward tcp:8765 tcp:8765
    python scripts/bridge_check.py [--url ws://127.0.0.1:8765] [--seconds 8]

Prints the status message, telemetry rate, frame rate and the first
telemetry sample. Sends no flight commands (a "ping" only).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.client import BridgeClient, BridgeError  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        async with BridgeClient(args.url, ack_timeout=5.0) as bc:
            await asyncio.sleep(0.5)
            print("status:", bc.status or "(none yet)")
            try:
                await bc.command("ping")
                print("ping: ok")
            except BridgeError as e:
                print("ping:", e, "(sim does not implement ping; fine)")
            t0 = time.monotonic()
            tel_n = 0
            frame_seqs: set[int] = set()
            first = None
            last_seq = -1
            while time.monotonic() - t0 < args.seconds:
                await asyncio.sleep(0.05)
                if bc.telemetry is not None:
                    tel_n += 1 if bc.telemetry is not first else 0
                    if first is None:
                        first = bc.telemetry
                        print("first telemetry:", first)
                    first = bc.telemetry
                if bc.frame is not None and bc.frame.seq != last_seq:
                    last_seq = bc.frame.seq
                    frame_seqs.add(last_seq)
            el = time.monotonic() - t0
            print(f"telemetry samples: ~{tel_n} in {el:.1f}s")
            print(f"frames: {len(frame_seqs)} in {el:.1f}s ({len(frame_seqs) / el:.1f} fps)")
            if bc.frame is not None:
                print(f"last frame: {bc.frame.w}x{bc.frame.h}, {len(bc.frame.jpeg)} bytes, gimbal {bc.frame.gimbal_pitch}")
            ok = first is not None
            print("RESULT:", "OK" if ok else "NO TELEMETRY (drone not connected in the bridge app?)")
            return 0 if ok else 2
    except OSError as e:
        print("cannot connect:", e)
        print("is the bridge app running with 'Start bridge', and did you run: adb forward tcp:8765 tcp:8765 ?")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
