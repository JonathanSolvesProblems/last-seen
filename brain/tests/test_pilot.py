"""Regressions for every defect found on flight 01, 2026-09-25.

Each test names the bug it pins. The point of this file is that the aircraft
taught me six things in 25 minutes of battery, and none of them should ever have
to be learned a second time in a park.

See flights/2026-09-25-flight-01/BUGS.md.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.config import MissionConfig  # noqa: E402
from phantom_brain.pilot import Abort, Envelope, FlightSession  # noqa: E402
from phantom_brain.protocol import VelocityCommand  # noqa: E402
from phantom_brain.sim.server import SimServer  # noqa: E402
from phantom_brain.sim.world import DroneMode  # noqa: E402
from phantom_brain.video import FeedMonitor, is_blank  # noqa: E402

SQUARE = [(-60, -60), (60, -60), (60, 60), (-60, 60)]


def cfg():
    return MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5}, "polygon_enu": SQUARE},
        "sim": {"frame_w": 320, "frame_h": 180, "video_latency_s": 0},
    })


async def _session(port, tmp_path, **kw) -> FlightSession:
    bc = BridgeClient(f"ws://127.0.0.1:{port}")
    await bc.connect()
    return FlightSession(bc, name="test", run_root=tmp_path, **kw)


# --- bug 2: virtual stick left enabled, pilot holding a dead controller -------

@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["clean", "abort", "crash", "pilot override"])
async def test_control_is_always_released(ending, tmp_path, free_port):
    """The worst bug of flight 01. Leaving the block must release the sticks,
    on EVERY path, including the one that exists to command nothing."""
    async with SimServer(cfg(), speed=20, port=free_port).running() as sim:
        await asyncio.sleep(0.2)
        fs = await _session(free_port, tmp_path)
        try:
            async with fs:
                await fs.preflight(min_battery=0)
                await fs.takeoff_and_take_control()
                assert sim.world.drone.vs_enabled, "precondition: we hold the controls"
                if ending == "abort":
                    raise Abort("envelope")
                if ending == "crash":
                    raise RuntimeError("something unexpected")
                if ending == "pilot override":
                    sim.world.set_rc_switch("P")       # the pilot's hardware override
                    await fs.hold(1.0)                 # next check sees it and aborts
        except RuntimeError as e:
            assert ending == "crash", e
        finally:
            await fs.bc.close()

        assert not sim.world.drone.vs_enabled, f"sticks left dead after a {ending} ending"
        assert sim.world.drone.cmd.is_zero()


@pytest.mark.asyncio
async def test_pilot_override_does_not_command_a_landing(tmp_path, free_port):
    """Standing down means commanding nothing, not flying a second aircraft.
    But it still lets go of the controls (that combination was bug 2)."""
    async with SimServer(cfg(), speed=20, port=free_port).running() as sim:
        await asyncio.sleep(0.2)
        fs = await _session(free_port, tmp_path)
        async with fs:
            await fs.preflight(min_battery=0)
            await fs.takeoff_and_take_control()
            sim.world.set_rc_switch("P")
            await fs.hold(1.0)
        await fs.bc.close()

        assert fs.aborted and "pilot took control" in fs.aborted
        assert sim.world.drone.mode is not DroneMode.LANDING, "must not fight the pilot"
        assert not sim.world.drone.vs_enabled, "must still release the controls"


# --- bug 3: telemetry lags the SDK, so a fresh enable reads stale ------------

@pytest.mark.asyncio
async def test_takeoff_waits_for_the_aircraft_to_confirm_virtual_stick(tmp_path, free_port):
    """Flight 01 aborted with 'pilot took control' one sample after enabling,
    because the check ran before telemetry caught up."""
    async with SimServer(cfg(), speed=20, port=free_port).running() as sim:
        await asyncio.sleep(0.2)
        fs = await _session(free_port, tmp_path)
        async with fs:
            await fs.preflight(min_battery=0)
            await fs.takeoff_and_take_control()
            assert fs.bc.telemetry.virtual_stick, "returned before the aircraft confirmed"
            await fs.hold(0.5)
        await fs.bc.close()
        assert fs.aborted is None, f"spurious abort: {fs.aborted}"


# --- bug 4: startTakeoff acked and ignored while the motors are armed --------

@pytest.mark.asyncio
async def test_takeoff_recovers_from_the_post_landing_armed_state(tmp_path, free_port):
    """Two runs were lost to this. The aircraft acks and does nothing, so the
    only way through is to disarm, then verify rather than trust the ack."""
    async with SimServer(cfg(), speed=20, port=free_port).running() as sim:
        await asyncio.sleep(0.2)
        # put the world in the state that follows an auto-landing
        sim.world.drone.motors_armed = True
        sim.world.drone.armed_until = sim.world.t + sim.world.POST_LANDING_ARMED_S
        assert sim.world.takeoff() == (True, None), "the sim must ack, like the aircraft"
        assert sim.world.drone.mode is DroneMode.IDLE, "and must not lift"

        fs = await _session(free_port, tmp_path)
        async with fs:
            await fs.preflight(min_battery=0)
            await fs.takeoff_and_take_control()
        await fs.bc.close()
        assert fs.aborted is None, f"never got airborne: {fs.aborted}"


# --- the envelope holds ------------------------------------------------------

@pytest.mark.asyncio
async def test_commands_are_clamped_to_the_envelope(tmp_path, free_port):
    """No caller can out-command the envelope, however wrong its arithmetic."""
    async with SimServer(cfg(), speed=20, port=free_port).running():
        await asyncio.sleep(0.2)
        fs = await _session(free_port, tmp_path,
                            envelope=Envelope(max_speed=1.0, max_climb=0.5, max_yaw_rate=30))
        v = fs.clamp(VelocityCommand(99, -99, 99, -999))
        assert (v.vn, v.ve, v.vu, v.yaw_rate) == (1.0, -1.0, 0.5, -30.0)
        await fs.bc.close()


@pytest.mark.asyncio
async def test_virtual_stick_is_refused_outside_f_mode(tmp_path, free_port):
    """The hardware override is real: in P the SDK will not give us the sticks."""
    async with SimServer(cfg(), speed=20, port=free_port).running() as sim:
        await asyncio.sleep(0.2)
        sim.world.set_rc_switch("P")
        fs = await _session(free_port, tmp_path)
        async with fs:
            with pytest.raises(Abort):
                await fs.preflight(min_battery=0, wait_for_switch_s=0)
        await fs.bc.close()
        assert not sim.world.drone.vs_enabled


# --- heading must survive wrapping -------------------------------------------

@pytest.mark.asyncio
async def test_heading_accumulates_past_a_full_turn(tmp_path, free_port):
    """Differencing two compass readings cannot tell 366 degrees from 6. Found
    in the simulator, where a fast clock turned the aircraft a whole revolution
    and the axis check reported +6 and called the yaw axis broken."""
    async with SimServer(cfg(), speed=20, port=free_port).running() as sim:
        await asyncio.sleep(0.2)
        fs = await _session(free_port, tmp_path)
        async with fs:
            await fs.preflight(min_battery=0)
            await fs.takeoff_and_take_control()
            start = fs.heading_total
            # a bit over one full turn at the envelope's yaw rate
            await fs.stream(VelocityCommand(0, 0, 0, fs.env.max_yaw_rate), 1.0)
            await fs.hold(0.5)
            turned = fs.heading_total - start
        await fs.bc.close()
        assert turned > 180, f"a turn past half a circle read as {turned:.0f} deg"


# --- bug 6: the feed goes black and keeps the happy path's shape -------------

def test_blank_frames_are_recognised():
    black = np.zeros((180, 320, 3), np.uint8)
    noise = np.random.default_rng(0).integers(0, 255, (180, 320, 3), dtype=np.uint8)
    assert is_blank(black)
    assert not is_blank(noise)
    # a genuinely dark but real frame has spread, and must not be called blank
    dark = (noise // 12).astype(np.uint8)
    assert not is_blank(dark)


def test_feed_monitor_flags_a_dead_camera():
    """148 black frames looked exactly like 'the detector found nobody'."""
    mon = FeedMonitor()
    for _ in range(30):
        mon.add(np.zeros((180, 320, 3), np.uint8))
    v = mon.verdict()
    assert not v.ok and "black" in v.reason
    assert v.blank_fraction == 1.0


def test_feed_monitor_flags_a_frozen_feed():
    mon = FeedMonitor()
    still = np.random.default_rng(1).integers(0, 255, (180, 320, 3), dtype=np.uint8)
    for _ in range(30):
        mon.add(still)
    v = mon.verdict()
    assert not v.ok and "frozen" in v.reason


def test_feed_monitor_passes_a_live_feed():
    mon = FeedMonitor()
    rng = np.random.default_rng(2)
    for _ in range(30):
        mon.add(rng.integers(0, 255, (180, 320, 3), dtype=np.uint8))
    assert mon.verdict().ok


# --- bug 5: a flag that is parsed but never used -----------------------------

def test_requested_tests_are_the_tests_that_run():
    """The flag was wired to the parser and not to the call site, so the aircraft
    flew a north leg after I promised the pilot it would not move."""
    import first_flight

    async def scenario(tests):
        ran: list[str] = []

        class FakeTelemetry:
            alt = 4.0
            heading = 0.0

        class FakeBridge:
            telemetry = FakeTelemetry()

        class FakeSession:
            env = Envelope()
            bc = FakeBridge()

            def here(self):
                return (0.0, 0.0)

            def log(self, *_a):
                pass

            def note(self, name, *_a, **_k):
                ran.append(name)

            async def stream(self, *_a, **_k):
                pass

            async def hold(self, *_a, **_k):
                pass

            async def climb_to(self, *_a, **_k):
                pass

            async def return_to_origin(self, *_a, **_k):
                pass

        async def fake_leg(fs, name, cmd, leg):
            ran.append(f"axis {name}")

        first_flight.axis_leg = fake_leg
        await first_flight.run_tests(FakeSession(), tests, 4.0, 3.0)
        return ran

    ran = asyncio.run(scenario(["yaw", "vertical"]))
    assert "axis yaw cw" in ran
    assert not any("north" in r or "east" in r for r in ran), \
        "a test nobody asked for translated the aircraft"
