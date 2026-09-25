"""keyboard_fly must never leave the aircraft in virtual stick.

While virtual stick is enabled the RC sticks are ignored by the aircraft, so if
the teleop script exits (cleanly or by exception) without releasing it, the
pilot is holding a dead controller until S1 is flipped out of F. This is the
one property of the park-day tool worth a test.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.config import MissionConfig  # noqa: E402
from phantom_brain.sim.server import SimServer  # noqa: E402

import keyboard_fly  # noqa: E402

SQUARE = [(-40, -30), (40, -30), (40, 30), (-40, 30)]


def cfg():
    return MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5}, "polygon_enu": SQUARE},
        "sim": {"frame_w": 320, "frame_h": 180, "video_latency_s": 0},
    })


async def _airborne_in_virtual_stick(bc: BridgeClient) -> None:
    await bc.wait_for_telemetry(5)
    await bc.takeoff()
    for _ in range(200):
        tel = await bc.next_telemetry(5)
        # the climb must have STOPPED: the SDK refuses virtual stick during
        # auto-takeoff, and the simulator now reproduces that
        if tel.is_flying and tel.alt > 0.8 and abs(tel.vu) < 0.2:
            break
    await bc.enable_virtual_stick()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash", [False, True], ids=["clean exit", "exception"])
async def test_teleop_always_releases_virtual_stick(crash, monkeypatch, free_port):
    port = free_port
    async with SimServer(cfg(), speed=20, port=port).running() as sim:
        await asyncio.sleep(0.2)
        async with BridgeClient(f"ws://127.0.0.1:{port}") as setup:
            await _airborne_in_virtual_stick(setup)
        assert sim.world.drone.vs_enabled, "precondition: the sim is in virtual stick"

        async def fake_fly(bc):
            if crash:
                raise RuntimeError("camera window died mid-flight")

        monkeypatch.setattr(keyboard_fly, "fly", fake_fly)
        monkeypatch.setattr(sys, "argv", ["keyboard_fly.py", "--url", f"ws://127.0.0.1:{port}"])
        monkeypatch.setattr(keyboard_fly.cv2, "destroyAllWindows", lambda: None)

        if crash:
            with pytest.raises(RuntimeError):
                await keyboard_fly.main()
        else:
            await keyboard_fly.main()

        # whatever happened, the pilot has the sticks back and nothing is commanded
        assert not sim.world.drone.vs_enabled
        assert sim.world.drone.cmd.is_zero()
