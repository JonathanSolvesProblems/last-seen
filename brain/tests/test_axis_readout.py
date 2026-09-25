"""The axis-test readout must report the direction the aircraft actually moved.

This is the one number the first real flight turns on: DJI GO cannot run while
the bridge holds the aircraft, so this readout is the only witness to whether
"W" really means north. If it were wrong it would confirm a broken axis mapping
as correct, which is the worst failure available on flight day.
"""

import asyncio

import pytest

from phantom_brain import geo
from phantom_brain.client import BridgeClient
from phantom_brain.config import MissionConfig
from phantom_brain.protocol import VelocityCommand
from phantom_brain.sim.server import SimServer

SQUARE = [(-60, -60), (60, -60), (60, 60), (-60, 60)]


def cfg():
    return MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5}, "polygon_enu": SQUARE},
        "sim": {"frame_w": 320, "frame_h": 180, "video_latency_s": 0, "gps_noise_m": 0.0},
    })


async def _fly_for(bc: BridgeClient, v: VelocityCommand, seconds: float) -> None:
    t0 = (await bc.wait_for_telemetry(5)).t
    while (bc.telemetry.t - t0) < seconds:
        await bc.send_velocity(v)
        await asyncio.sleep(0.02)
    await bc.hover()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label, vel, expect",
    [
        ("W = north", VelocityCommand(2.0, 0, 0, 0), "north"),
        ("D = east", VelocityCommand(0, 2.0, 0, 0), "east"),
        ("S = south", VelocityCommand(-2.0, 0, 0, 0), "south"),
        ("A = west", VelocityCommand(0, -2.0, 0, 0), "west"),
    ],
)
async def test_readout_reports_the_direction_actually_flown(label, vel, expect, free_port):
    async with SimServer(cfg(), speed=20, port=free_port).running():
        await asyncio.sleep(0.2)
        async with BridgeClient(f"ws://127.0.0.1:{free_port}") as bc:
            tel = await bc.wait_for_telemetry(5)
            await bc.takeoff()
            for _ in range(300):
                tel = await bc.next_telemetry(5)
                # Wait for the climb to STOP, not just to start: auto-takeoff owns
                # the aircraft until then and the SDK refuses virtual stick while
                # it does (flight 01, bug 4's neighbour).
                if tel.is_flying and tel.alt > 0.8 and abs(tel.vu) < 0.2:
                    break
            await bc.enable_virtual_stick()
            await _fly_for(bc, VelocityCommand(0, 0, 1.0, 0), 5.0)      # climb clear

            # this is exactly what pressing Z does in keyboard_fly
            tel = await bc.next_telemetry(5)
            origin = geo.Origin(tel.lat, tel.lon)

            await _fly_for(bc, vel, 4.0)
            tel = await bc.next_telemetry(5)
            e, n = origin.to_local(tel.lat, tel.lon)

            got = {"north": n, "south": -n, "east": e, "west": -e}[expect]
            cross = abs(e) if expect in ("north", "south") else abs(n)
            assert got > 3.0, f"{label}: expected {expect}, readout was N {n:+.1f} E {e:+.1f}"
            assert cross < got / 2, f"{label}: drifted sideways, N {n:+.1f} E {e:+.1f}"
