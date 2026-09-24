"""Run logging and the scoreboard.

Every mission writes runs/<timestamp>/ with:
    telemetry.jsonl   every telemetry message
    events.jsonl      phase changes, confirmations, aborts, safety notes
    summary.json      the per-task numbers for this run
    frames/           JPEGs saved at key moments (first confirmation etc.)
    map.png/.npz      the reconstructed occupancy grid

`aggregate()` rolls a folder of runs into the table that goes in the video:
attempts, end-to-end successes, and the per-task scores next to the
Drone-Bench human baselines.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# Human baseline scores published on the Drone-Bench page (Andon Labs).
DRONE_BENCH_HUMAN_BASELINE = {
    "reconstruct": 0.822,
    "localize": 0.840,
    "navigate": 0.920,
    "detect": 0.721,
    "follow": 0.674,
}
GPT6_ASTRA_END_TO_END = 0.028


def _jsonable(o: Any) -> Any:
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


class RunLogger:
    def __init__(self, runs_dir: str | Path, name: str | None = None):
        stamp = name or time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path(runs_dir) / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "frames").mkdir(exist_ok=True)
        self._tel = open(self.dir / "telemetry.jsonl", "a", encoding="utf-8")
        self._ev = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self.t0 = time.time()
        self.events: list[dict[str, Any]] = []

    def telemetry(self, d: dict[str, Any]) -> None:
        self._tel.write(json.dumps(d, default=_jsonable) + "\n")
        # Flush every sample: this is a flight recorder, and the run it most
        # needs to explain is the one that ends in a crash, where anything left
        # in the buffer is lost. 10 small writes a second costs nothing.
        self._tel.flush()

    def event(self, name: str, **detail: Any) -> None:
        rec = {"wall": round(time.time() - self.t0, 2), "name": name, **detail}
        self.events.append(rec)
        self._ev.write(json.dumps(rec, default=_jsonable) + "\n")
        self._ev.flush()

    def frame(self, tag: str, img: np.ndarray) -> Path:
        p = self.dir / "frames" / f"{tag}.jpg"
        cv2.imwrite(str(p), img)
        return p

    def summary(self, data: dict[str, Any]) -> Path:
        p = self.dir / "summary.json"
        p.write_text(json.dumps(data, indent=2, default=_jsonable), encoding="utf-8")
        return p

    def close(self) -> None:
        self._tel.close()
        self._ev.close()


# A simulated run is only evidence about the mission if this machine actually
# ran the simulator faithfully. These thresholds are deliberately causal: each
# names a way the host could have changed the outcome. They are judged from the
# recorded measurements alone, never from whether the run passed, so this can
# only discard runs, never select for the ones that succeeded.
STALE_ABORT_S = 2.0        # the brain's own dead-link abort
MAX_PHYSICS_LAG_S = 3.0    # a longer freeze jumps sim time far enough to matter
MAX_SIM_STALLS = 3         # occasional 1 s hiccups are normal on a laptop
MIN_CONTROL_FRACTION = 0.6  # of the requested control rate


def host_starved(summary: dict[str, Any]) -> tuple[bool, str]:
    """Did this machine fail to run the simulator faithfully?

    Returns (invalid, reason). Recomputed from the stored measurements on every
    read, so improving the rule re-judges old runs instead of leaving a stale
    boolean behind.
    """
    sim = summary.get("sim") or {}
    gap = sim.get("worst_telemetry_gap_s")
    if gap is not None and gap >= STALE_ABORT_S:
        return True, (f"simulator stalled: {gap:.1f}s without telemetry, at or past the "
                      f"{STALE_ABORT_S:.0f}s dead-link abort")
    lag = sim.get("worst_physics_lag_s")
    if lag is not None and lag > MAX_PHYSICS_LAG_S:
        return True, f"simulator froze for {lag:.1f}s (limit {MAX_PHYSICS_LAG_S:.0f}s)"
    stalls = sim.get("sim_stalls")
    if stalls is not None and stalls > MAX_SIM_STALLS:
        return True, f"simulator fell behind real time {stalls}x (limit {MAX_SIM_STALLS})"
    hz, target = summary.get("control_hz"), summary.get("control_hz_target")
    if hz and target and hz < MIN_CONTROL_FRACTION * target:
        return True, f"control loop ran at {hz:.0f} Hz against a {target:.0f} Hz target"
    return False, ""


def aggregate(runs_dir: str | Path) -> dict[str, Any]:
    runs = []
    for p in sorted(Path(runs_dir).glob("*/summary.json")):
        try:
            runs.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    # Runs the host starved are excluded from the rate: they measure this laptop,
    # not the mission. They are still reported so the exclusion is visible. The
    # verdict is recomputed here rather than trusting what the bench wrote, so an
    # improved rule re-judges every past run.
    for r in runs:
        r["invalid"], r["invalid_reason"] = host_starved(r)
    voided = [r for r in runs if r["invalid"]]
    runs = [r for r in runs if not r["invalid"]]
    n = len(runs)
    if n == 0:
        return {"runs": 0, "voided": len(voided)}
    succ = sum(1 for r in runs if r.get("success"))

    def mean(key_path: list[str]) -> float | None:
        vals = []
        for r in runs:
            v: Any = r
            for k in key_path:
                v = v.get(k) if isinstance(v, dict) else None
            if isinstance(v, (int, float)):
                vals.append(float(v))
        return sum(vals) / len(vals) if vals else None

    return {
        "runs": n,
        "voided": len(voided),
        "end_to_end_successes": succ,
        "end_to_end_rate": succ / n,
        "gpt6_astra_end_to_end": GPT6_ASTRA_END_TO_END,
        "reconstruct_coverage": mean(["reconstruct", "coverage"]),
        "reconstruct_iou": mean(["reconstruct", "iou"]),
        "localize_median_error_m": mean(["localize", "median_error_m"]),
        "localize_within_5m": mean(["localize", "within_5m"]),
        "navigate_collision_free": mean(["navigate", "collision_free"]),
        "detect_time_to_confirm_s": mean(["detect", "time_to_confirm_s"]),
        "detect_found": mean(["detect", "found"]),
        "detect_correct_person": mean(["detect", "correct_person"]),
        "follow_in_frame_fraction": mean(["follow", "in_frame_fraction"]),
        "crashes": sum(1 for r in runs if (r.get("sim") or {}).get("crashed")),
        "aborts": sum(1 for r in runs if r.get("abort") not in (None, "none")),
        "human_baseline": DRONE_BENCH_HUMAN_BASELINE,
    }


def format_table(agg: dict[str, Any]) -> str:
    if agg.get("runs", 0) == 0:
        v = agg.get("voided", 0)
        return f"no valid runs ({v} voided: the host could not run the simulator in real time)" if v else "no runs"

    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{100 * v:5.1f}%"

    def num(v: float | None, unit: str = "") -> str:
        return "n/a" if v is None else f"{v:.1f}{unit}"

    hb = agg["human_baseline"]
    voided = agg.get("voided", 0)
    lines = [
        f"runs: {agg['runs']}   end-to-end: {agg['end_to_end_successes']}/{agg['runs']} = {pct(agg['end_to_end_rate'])}"
        f"   (GPT-6 Astra average run: {pct(agg['gpt6_astra_end_to_end'])})"
        + (f"   [{voided} voided, host too slow]" if voided else ""),
        "",
        f"{'task':12} {'mine':>12} {'human baseline':>16}",
        f"{'reconstruct':12} {pct(agg['reconstruct_iou']) if agg['reconstruct_iou'] is not None else 'cov ' + pct(agg['reconstruct_coverage']):>12} {pct(hb['reconstruct']):>16}",
        f"{'localize':12} {pct(agg['localize_within_5m']):>12} {pct(hb['localize']):>16}   median err {num(agg['localize_median_error_m'], ' m')}",
        f"{'navigate':12} {pct(agg['navigate_collision_free']):>12} {pct(hb['navigate']):>16}",
        f"{'detect':12} {pct(agg['detect_correct_person']):>12} {pct(hb['detect']):>16}   found {pct(agg['detect_found'])}, {num(agg['detect_time_to_confirm_s'], ' s')} to confirm",
        f"{'follow':12} {pct(agg['follow_in_frame_fraction']):>12} {pct(hb['follow']):>16}",
        "",
        f"crashes: {agg['crashes']}   aborts: {agg['aborts']}",
    ]
    return "\n".join(lines)
