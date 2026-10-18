"""The camera-truth flight, flown against the simulator, must find the gimbal clamp.

The simulator refuses gimbal pitches that would put the horizon in frame and
renders at the clamped angle, but its telemetry keeps reporting the commanded
one. Nobody planted that for this test; it is how the renderer has always
worked. It is exactly the shape of flight 02's question (telemetry said -23,
the pictures said steeper), so the tool earns its keep only if it catches it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import camera_truth  # noqa: E402

from phantom_brain.calib import GroundMarkers  # noqa: E402


@pytest.mark.asyncio
async def test_camera_truth_catches_an_unreported_gimbal_clamp(free_port, monkeypatch, tmp_path):
    from phantom_brain.config import MissionConfig
    from phantom_brain.sim.server import SimServer

    layout = GroundMarkers({20: (0.0, 4.0), 21: (2.0, 4.0), 22: (2.0, 6.0), 23: (0.0, 6.0)}, 0.19)
    lp = tmp_path / "layout.json"
    lp.write_text(json.dumps(layout.to_dict()))
    cfg = MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5},
                  "polygon_enu": [[-40, -40], [40, -40], [40, 40], [-40, 40]]},
        "sim": {"frame_w": 1280, "frame_h": 720, "video_latency_s": 0, "random_trees": 0,
                "markers": [[mid, e, n, 0.19] for mid, (e, n) in layout.centres_m.items()]},
    })
    monkeypatch.setenv("DRONE_RUNS_DIR", str(tmp_path / "runs"))
    async with SimServer(cfg, speed=2, port=free_port).running():
        args = argparse.Namespace(url=f"ws://127.0.0.1:{free_port}", layout=lp, intrinsics=None,
                                  fov=94.0, alt=4.0, steps=[-25.0, -45.0], settle=2.0, measure=2.0,
                                  max_alt=10.0, max_radius=15.0, src=None)
        assert await camera_truth.fly(args, layout) == 0

    summary = json.loads(next((tmp_path / "runs" / "camera-truth").glob("*/summary.json")).read_text())
    by = {s["commanded"]: s for s in summary["steps"]}
    assert by[-45.0]["frames"] > 0 and by[-25.0]["frames"] > 0
    # where the gimbal can go, measured agrees with telemetry
    assert abs(by[-45.0]["measured"] - by[-45.0]["reported_tel"]) < 1.0
    # where it cannot, telemetry still says -25 and the markers say otherwise
    assert by[-25.0]["reported_tel"] == pytest.approx(-25.0, abs=0.5)
    assert by[-25.0]["measured"] < -29.0
    # and the camera's height is the aircraft's, to a few centimetres
    assert abs(by[-45.0]["height_measured"] - by[-45.0]["alt_reported"]) < 0.2
