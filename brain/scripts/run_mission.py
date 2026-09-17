"""Fly the full mission against whatever is on the bridge URL (sim or phone).

    python scripts/run_mission.py configs/sim_field.yaml --display
    python scripts/run_mission.py configs/park.yaml --url ws://127.0.0.1:8765

With --sim-truth the oracle detector/matcher read ground truth from the
simulator's frame headers. Never pass it against the real drone (it would
simply find nothing, the phone never sends truth, but be explicit).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.autopilot import Mission  # noqa: E402
from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.config import MissionConfig  # noqa: E402
from phantom_brain.scoreboard import aggregate, format_table  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--url", default=None, help="bridge websocket url (default from config)")
    ap.add_argument("--display", action="store_true", help="show camera + detections window")
    ap.add_argument("--sim-truth", action="store_true", help="use simulator ground truth (oracle perception)")
    ap.add_argument("--name", default=None, help="run folder name")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = MissionConfig.load(args.config)
    url = args.url or cfg.bridge_url
    sim_truth = args.sim_truth or cfg.perception.detector == "oracle"
    async with BridgeClient(url) as bc:
        mission = Mission(cfg, bc, display=args.display, run_name=args.name, sim_truth=sim_truth)
        result = await mission.run()
    print()
    print(f"result: success={result.success} phase={result.phase.value} abort={result.abort} {result.reason}")
    print(f"run dir: {result.run_dir}")
    print()
    print(format_table(aggregate(cfg.runs_dir)))
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
