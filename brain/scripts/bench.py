"""Run N end-to-end episodes in the simulator and print the scoreboard.

    python scripts/bench.py configs/sim_field.yaml --runs 10 --speed 8

Each episode gets a different seed (tree layout stays as configured, the
person's start position and walking path are randomised inside the field)
so the number at the end is not one lucky layout.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain import geo  # noqa: E402
from phantom_brain.autopilot import Mission  # noqa: E402
from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.config import MissionConfig  # noqa: E402
from phantom_brain.scoreboard import aggregate, format_table, host_starved  # noqa: E402
from phantom_brain.sim.server import SimServer  # noqa: E402


def randomise_people(cfg: MissionConfig, seed: int) -> MissionConfig:
    cfg = copy.deepcopy(cfg)
    rng = random.Random(seed)
    x0, y0, x1, y1 = geo.polygon_bbox(cfg.field.polygon)
    trees = cfg.sim.trees

    def free_point() -> tuple[float, float]:
        for _ in range(200):
            e, n = rng.uniform(x0 + 5, x1 - 5), rng.uniform(y0 + 5, y1 - 5)
            if not geo.point_in_polygon(e, n, cfg.field.polygon):
                continue
            if any(geo.dist((e, n), (t[0], t[1])) < t[2] + 2 for t in trees):
                continue
            if geo.dist((e, n), cfg.field.home) < 12:
                continue
            return e, n
        return 20.0, 20.0

    for p in cfg.sim.people:
        p.start = free_point()
        p.path = [p.start] + [free_point() for _ in range(3)] + [p.start]
    return cfg


async def _hang_watchdog(mission, stall_s: float = 45.0) -> None:
    """Turn a silent wedge into evidence.

    The mission loop counts iterations. If that counter stops advancing the
    process is stuck on an await, so print every task's stack instead of
    leaving someone to guess from file sizes (which are buffered and lie).
    """
    last, last_change = mission._loops, time.monotonic()
    while True:
        await asyncio.sleep(5)
        if mission._loops != last:
            last, last_change = mission._loops, time.monotonic()
            continue
        if time.monotonic() - last_change > stall_s:
            print(f"\n!! mission loop stalled {stall_s:.0f}s in phase {mission.phase.value}; task stacks:")
            for task in asyncio.all_tasks():
                print(f"--- {task.get_name()} ---")
                task.print_stack(limit=12)
            last_change = time.monotonic()   # report once per stall_s, keep watching


async def run_one(cfg: MissionConfig, seed: int, speed: float, port: int, runs_dir: str) -> dict:
    cfg = randomise_people(cfg, seed)
    cfg.runs_dir = runs_dir
    cfg.loop_hz = min(100.0, 10.0 * speed)   # keep ~10 Hz control in sim time
    cfg.bridge_url = f"ws://127.0.0.1:{port}"
    sim = SimServer(cfg, speed=speed, port=port, seed=seed)
    async with sim.running():
        await asyncio.sleep(0.3)
        async with BridgeClient(cfg.bridge_url) as bc:
            mission = Mission(cfg, bc, run_name=f"bench-{seed:03d}", sim_truth=True)
            watchdog = asyncio.create_task(_hang_watchdog(mission))
            try:
                result = await mission.run()
            finally:
                watchdog.cancel()
        ep = sim.episode_summary()
    summary = result.summary
    summary["sim"] = ep
    summary["seed"] = seed
    # a crash or leaving the field is never a success, whatever the follow did
    if ep["crashed"] or ep["left_field"]:
        summary["success"] = False
    summary["invalid"], summary["invalid_reason"] = host_starved(summary)
    (Path(result.run_dir) / "summary.json").write_text(__import__("json").dumps(summary, indent=2, default=str))
    return summary


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--speed", type=float, default=8.0)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--seed0", type=int, default=100)
    ap.add_argument("--runs-dir", default="runs/bench")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.v else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = MissionConfig.load(args.config)
    t0 = time.time()
    for i in range(args.runs):
        seed = args.seed0 + i
        s = await run_one(cfg, seed, args.speed, args.port + (i % 5), args.runs_dir)
        flag = "VOID" if s["invalid"] else ("OK  " if s["success"] else "FAIL")
        print(f"[{i + 1}/{args.runs}] seed {seed}: {flag} phase={s['phase']} found={s['detect']['found']:.0f} "
              f"t_confirm={s['detect']['time_to_confirm_s']} follow={s['follow']['in_frame_fraction']:.2f} "
              f"crash={s['sim']['crashed']} left={s['sim']['left_field']} reason={s['reason']!r}")
        if s["invalid"]:
            print(f"       ^ not counted: {s['invalid_reason']}")
    print()
    print(format_table(aggregate(args.runs_dir)))
    print(f"\nwall time {time.time() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
