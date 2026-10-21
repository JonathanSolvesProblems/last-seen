"""The whole Last Seen mission in the simulator, plus the gate it is built around.

Driven by the rule-based policy (no model, no key, no cost) and the simulated
coordinator, which answers from ground truth and says so. What is under test is
everything between them: the runtime, the tools, the confirmation gate, the
pins and their grader.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from phantom_brain.agent.coordinator import SimulatedCoordinator
from phantom_brain.agent.loop import SearchAgent
from phantom_brain.agent.scripted import ScriptedClient
from phantom_brain.camera import CameraModel
from phantom_brain.client import BridgeClient
from phantom_brain.config import MissionConfig
from phantom_brain.detect import OracleDetector, OracleMatcher
from phantom_brain.pilot import Envelope, FlightSession
from phantom_brain.runtime import Candidate, MissionRuntime, NotConfirmed
from phantom_brain.sim.server import SimServer
from phantom_brain.tools import ToolExecutor

AREA = [(-25.0, -25.0), (25.0, -25.0), (25.0, 25.0), (-25.0, 25.0)]


def scene() -> MissionConfig:
    return MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5},
                  "polygon_enu": [[-60, -60], [60, -60], [60, 60], [-60, 60]]},
        "sim": {"frame_w": 640, "frame_h": 360, "video_latency_s": 0, "random_trees": 0,
                # target first: the simulated phone travels with people[0]
                "people": [{"id": "target", "start": [12, 15], "path": [[12, 15]], "speed": 0.0},
                           {"id": "hiker", "start": [-10, 8], "path": [[-10, 8]], "speed": 0.0}]}})


@pytest.mark.asyncio
async def test_mission_finds_confirms_and_follows_only_the_target(free_port, tmp_path, monkeypatch):
    monkeypatch.setenv("DRONE_RUNS_DIR", str(tmp_path))
    ref = tmp_path / "ref.jpg"
    cv2.imwrite(str(ref), np.full((64, 32, 3), 128, np.uint8))
    coord = SimulatedCoordinator(delay_s=0.5)
    async with SimServer(scene(), speed=2, port=free_port).running():
        async with BridgeClient(f"ws://127.0.0.1:{free_port}") as bc:
            await bc.wait_for_telemetry(10)
            env = Envelope(max_alt=15, max_radius=45, max_speed=2.0, max_yaw_rate=30)
            async with FlightSession(bc, name="ls", envelope=env) as fs:
                await fs.preflight(require_switch="F")
                await fs.takeoff_and_take_control()
                await fs.climb_to(8.0)
                rt = MissionRuntime(bc, fs, detector=OracleDetector(), matcher=OracleMatcher(),
                                    cam=CameraModel(640, 360), area=AREA, alt=8.0, out_dir=tmp_path / "m")
                ex = ToolExecutor(rt, coord, leash_m=40, reference_path=str(ref))
                agent = SearchAgent(ex, tmp_path / "trace.jsonl",
                                    client=ScriptedClient([(15, 15), (-15, 15)], follow_s=15), model="scripted")
                result = await agent.run(AREA, 8.0, str(ref))

    assert result["outcome"] == "found_and_followed"
    # only the target was ever confirmed, and the decoy was never put to a person
    assert [a["truth"] for a in coord.asked if a["decision"] == "confirmed"] == ["target"]
    assert not any(a["truth"] == "hiker" for a in coord.asked)
    # the follow came after the confirmation, in that order, in the trace
    tools = [json.loads(x) for x in (tmp_path / "trace.jsonl").read_text().splitlines()
             if json.loads(x)["kind"] == "tool"]
    names = [t["name"] for t in tools]
    assert names.index("ask_coordinator") < names.index("follow_confirmed")
    # every pin carries its grader, and the pins are near where the phone was
    pins = json.loads((tmp_path / "m" / "pins.json").read_text())
    errs = [p["error_m"] for p in pins if p["error_m"] is not None]
    assert len(errs) >= 5
    assert float(np.median(errs)) < 8.0


@pytest.mark.asyncio
async def test_follow_refuses_anyone_not_confirmed(tmp_path):
    """The gate, in code. A perfect score is not a confirmation."""
    rt = MissionRuntime.__new__(MissionRuntime)
    rt.candidates = {"P1": Candidate("P1", 0, 0, best=0.99, status="unreviewed"),
                     "P2": Candidate("P2", 0, 0, best=0.99, status="rejected")}
    for cid in ("P1", "P2"):
        with pytest.raises(NotConfirmed):
            await rt.follow(cid, 5, leash=20)


@pytest.mark.asyncio
async def test_executor_returns_the_refusal_as_a_permission_error(tmp_path):
    rt = MissionRuntime.__new__(MissionRuntime)
    rt.candidates = {"P1": Candidate("P1", 0, 0, best=0.99)}
    ex = ToolExecutor(rt, SimulatedCoordinator(), leash_m=20, reference_path="x")
    out = await ex.execute("follow_confirmed", {"candidate_id": "P1", "seconds": 30})
    body = json.loads(out.content[0]["text"])
    assert out.is_error and body["errorCategory"] == "permission" and body["isRetryable"] is False


@pytest.mark.asyncio
async def test_weak_candidates_are_not_put_to_a_person(tmp_path):
    rt = MissionRuntime.__new__(MissionRuntime)
    rt.candidates = {"P9": Candidate("P9", 0, 0, best=0.12)}
    coord = SimulatedCoordinator()
    ex = ToolExecutor(rt, coord, leash_m=20, reference_path="x")
    out = await ex.execute("ask_coordinator", {"candidate_id": "P9", "message": "maybe"})
    assert out.is_error and json.loads(out.content[0]["text"])["errorCategory"] == "business"
    assert coord.asked == []


# --- identity survives, not just position (rehearsal 2026-10-20) ---------------

def _rt():
    rt = MissionRuntime.__new__(MissionRuntime)
    rt.candidates, rt._next_id = {}, 1
    return rt


def test_a_strong_sighting_is_never_merged_into_a_weak_candidate():
    """What happened: my sightings merged into the hiker's candidate, so the
    coordinator would have confirmed MY crop and the aircraft gone to HIS spot."""
    rt = _rt()
    hiker = rt._associate((-10.0, 7.0), 0.0, 0.25)
    hiker.mean, hiker.seen, hiker.e, hiker.n, hiker.last_t = 0.25, 40, -10.0, 7.0, 0.0
    me = rt._associate((-9.0, 8.0), 5.0, 0.98)       # 1.4 m away, a very different match
    assert me is not hiker


def test_the_same_person_still_merges():
    rt = _rt()
    c = rt._associate((12.0, 15.0), 0.0, 0.9)
    c.mean, c.seen, c.e, c.n, c.last_t = 0.9, 10, 12.0, 15.0, 0.0
    assert rt._associate((14.0, 17.0), 4.0, 0.88) is c


def test_reach_is_capped_however_long_someone_was_unseen():
    """Uncapped, 20 s unseen meant a 35 m reach."""
    rt = _rt()
    c = rt._associate((0.0, 0.0), 0.0, 0.9)
    c.mean, c.seen, c.e, c.n, c.last_t = 0.9, 10, 0.0, 0.0, 0.0
    assert rt._associate((25.0, 0.0), 20.0, 0.9) is not c
