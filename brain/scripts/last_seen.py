"""Last Seen: hand it a photo, it searches, a person confirms, it follows.

    python scripts/last_seen.py --reference photo.jpg --area configs/search_area.json
    python scripts/last_seen.py ... --coordinator console      # confirm in this terminal
    python scripts/last_seen.py ... --agent scripted           # rule-based policy, no model

Field order: fieldup.py prints READY, the aircraft is hovering in F (manual
takeoff if auto refuses, as on flight 02), then run this. Pilot override on S1,
always. The plan is printed before anything moves.

Agents:
  claude     Claude Opus 5.5 on the Anthropic API (needs ANTHROPIC_API_KEY)
  bedrock    the same model through Amazon Bedrock (needs Bedrock access)
  scripted   fixed thresholds, no language model; the baseline arm
"""

from __future__ import annotations

import argparse
import base64
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phantom_brain.agent.coordinator import (ConsoleCoordinator, MissionControlCoordinator,  # noqa: E402
                                             SimulatedCoordinator)
from phantom_brain.agent.loop import SearchAgent, make_client  # noqa: E402
from phantom_brain.agent.scripted import ScriptedClient  # noqa: E402
from phantom_brain.camera import CameraModel  # noqa: E402
from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.detect import CvDnnYoloDetector, OracleDetector, OracleMatcher  # noqa: E402
from phantom_brain.pilot import Envelope, FlightSession, ensure_feed, runs_root  # noqa: E402
from phantom_brain.reid import ReIdMatcher  # noqa: E402
from phantom_brain.runtime import MissionRuntime  # noqa: E402
from phantom_brain.tools import ToolExecutor  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_area(path: Path | None, radius: float) -> list[tuple[float, float]]:
    if path and path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        return [tuple(map(float, p)) for p in d["polygon_en"]]
    r = radius
    return [(-r, -r), (r, -r), (r, r), (-r, r)]


def load_camera(w: int, h: int) -> tuple[CameraModel, str, float]:
    p = ROOT / "configs" / "camera_intrinsics.json"
    off_p = ROOT / "configs" / "gimbal_offset.json"
    offset = json.loads(off_p.read_text())["offset_deg"] if off_p.exists() else 0.0
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return CameraModel(w, h, float(d["diagonal_fov_deg"])), f"ChArUco {d['diagonal_fov_deg']:.1f} deg", offset
    return CameraModel(w, h), "SPEC SHEET 94 deg, not measured", offset


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--reference", type=Path, required=True, help="photo of the person, ideally full length")
    ap.add_argument("--area", type=Path, default=None, help='{"polygon_en": [[e, n], ...]}')
    ap.add_argument("--radius", type=float, default=20.0, help="square area half-size if no --area")
    ap.add_argument("--alt", type=float, default=8.0)
    ap.add_argument("--speed", type=float, default=2.0)
    ap.add_argument("--max-alt", type=float, default=15.0)
    ap.add_argument("--agent", choices=("claude", "bedrock", "scripted"), default="claude")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--coordinator", choices=("mission-control", "console", "simulated"), default="console")
    ap.add_argument("--mission-control", default="http://127.0.0.1:8080")
    ap.add_argument("--token", default=os.environ.get("LASTSEEN_TOKEN", "devtoken"))
    ap.add_argument("--detector", choices=("yolo", "oracle"), default="yolo")
    ap.add_argument("--follow-s", type=int, default=60)
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--takeoff-battery", type=int, default=40, help="preflight floor, percent")
    ap.add_argument("--land-battery", type=int, default=25,
                    help="in-flight floor, percent: below it the session aborts and lands")
    args = ap.parse_args()

    if args.agent == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("no ANTHROPIC_API_KEY; use --agent scripted, or set the key")
        return 1
    area = load_area(args.area, args.radius)
    fence = max(max(abs(e), abs(n)) for e, n in area) * 1.5 + 5
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = runs_root() / "last-seen" / stamp
    out.mkdir(parents=True, exist_ok=True)
    ref = cv2.imread(str(args.reference))
    if ref is None:
        print(f"cannot read {args.reference}")
        return 1
    cv2.imwrite(str(out / "reference.jpg"), ref)

    print(f"plan: search {len(area)}-corner area up to {fence - 5:.0f} m out at {args.alt:.0f} m, "
          f"max {args.speed:.1f} m/s, fence {fence:.0f} m, ceiling {args.max_alt:.0f} m")
    print(f"      agent {args.agent}, coordinator {args.coordinator}; follows ONLY a confirmed person")

    detector = (OracleDetector() if args.detector == "oracle"
                else CvDnnYoloDetector(str(ROOT / "models" / "yolo11n.onnx"), imgsz=960, conf=0.35))
    matcher = OracleMatcher() if args.detector == "oracle" else ReIdMatcher(ref)
    coordinator = {"console": ConsoleCoordinator,
                   "simulated": SimulatedCoordinator}.get(args.coordinator, lambda: None)()
    if args.coordinator == "mission-control":
        coordinator = MissionControlCoordinator(args.mission_control, args.token, stamp)

    # The model client is built NOW, on the ground: importing the SDK and opening
    # its HTTP client blocks for seconds, which in the air is a stale link.
    llm = None if args.agent == "scripted" else make_client(args.agent)
    result: dict = {"outcome": "aborted", "summary": "the flight session ended before the agent finished"}
    env = Envelope(max_alt=args.max_alt, max_radius=fence, max_speed=args.speed, max_yaw_rate=30.0,
                   min_battery=args.land_battery)
    async with BridgeClient(args.url) as bc:
        await bc.wait_for_telemetry(15)
        if not args.no_record:
            try:
                await bc.record_start()
            except Exception as e:  # noqa: BLE001
                print(f"onboard recording did not start ({e})")
        async with FlightSession(bc, name="last-seen", envelope=env) as fs:
            await ensure_feed(bc, expect_motion=False)
            await fs.preflight(min_battery=args.takeoff_battery)
            await fs.takeoff_and_take_control()
            await fs.climb_to(args.alt)
            fr = await bc.next_frame(timeout=5)
            cam, how, offset = load_camera(fr.w, fr.h)
            fs.log(f"camera: {how}; gimbal offset {offset:+.1f} deg")
            rt = MissionRuntime(bc, fs, detector=detector, matcher=matcher, cam=cam, area=area,
                                alt=args.alt, out_dir=out, speed=args.speed, gimbal_offset_deg=offset)
            if isinstance(coordinator, MissionControlCoordinator):
                ref_b64 = base64.b64encode((out / "reference.jpg").read_bytes()).decode()
                sent = {"ref": False}

                async def sink(payload: dict) -> dict:
                    r = await coordinator.status(payload, None if sent["ref"] else ref_b64)
                    sent["ref"] = True
                    return r
                rt.status_sink = sink
            ex = ToolExecutor(rt, coordinator, leash_m=fence - 4, reference_path=str(out / "reference.jpg"))
            if args.agent == "scripted":
                wps = [(0.6 * e, 0.6 * n) for e, n in area]
                agent = SearchAgent(ex, out / "trace.jsonl", client=ScriptedClient(wps, args.follow_s),
                                    model="scripted")
            else:
                client, model, extra = llm
                agent = SearchAgent(ex, out / "trace.jsonl", client=client, model=model, extra=extra,
                                    effort=args.effort)
            try:
                result = await agent.run(area, args.alt, str(out / "reference.jpg"))
            finally:
                # An abort mid-follow (low battery, pilot override) must not lose the
                # pins already taken: flight 03 run 3 lost a whole follow this way.
                rt.save()
            fs.note("last seen", json.dumps(result), bool(result.get("outcome", "").startswith("found")))
        print(fs.summary())
        if not args.no_record:
            try:
                await bc.record_stop()
            except Exception:  # noqa: BLE001
                pass
    print(f"\n  outcome   {result}\n  run       {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
