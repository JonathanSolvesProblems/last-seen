"""Start the simulator as a standalone bridge on ws://127.0.0.1:8765.

    python scripts/run_sim.py configs/sim_field.yaml --monitor
    python scripts/run_sim.py configs/sim_field.yaml --speed 4

Then in another terminal:  python scripts/run_mission.py configs/sim_field.yaml --display
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.config import MissionConfig  # noqa: E402
from phantom_brain.sim.server import SimServer  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--speed", type=float, default=1.0, help="simulation speed multiplier")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--monitor", action="store_true", help="show top-down and camera windows")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = MissionConfig.load(args.config)
    sim = SimServer(cfg, speed=args.speed, port=args.port, monitor=args.monitor, seed=args.seed)
    async with sim.running():
        print(f"simulator on ws://127.0.0.1:{args.port}  (Ctrl+C to stop)")
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    print(sim.episode_summary())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
