"""The three guards that keep follow-me away from the person it is following.

This is the first thing in the project that deliberately flies the aircraft
relative to a human, and that human is the pilot. The control law is the same
Follower the simulator uses; these tests cover the guards wrapped around it.
"""

from __future__ import annotations

import asyncio
import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from phantom_brain.client import BridgeClient  # noqa: E402
from phantom_brain.detect import Detection  # noqa: E402
from phantom_brain.protocol import VelocityCommand  # noqa: E402

import follow_me  # noqa: E402


# --- the range floor ---------------------------------------------------------

def test_guard_blocks_motion_that_closes_an_already_short_range():
    """Being wrong about distance must never become flying at somebody."""
    # person due north, aircraft commanded straight at them, already too close
    cmd, fired = follow_me.guard(VelocityCommand(vn=1.0, ve=0.0), range_m=3.0,
                                 min_standoff=5.0, bearing_to_target=0.0, drone_heading=0.0)
    assert fired
    assert cmd.vn == pytest.approx(0.0, abs=1e-9), "closing component must be removed"


def test_guard_allows_retreat_when_too_close():
    cmd, fired = follow_me.guard(VelocityCommand(vn=-1.0, ve=0.0), range_m=3.0,
                                 min_standoff=5.0, bearing_to_target=0.0, drone_heading=0.0)
    assert not fired
    assert cmd.vn == pytest.approx(-1.0), "backing away must be untouched"


def test_guard_allows_sideways_motion_when_too_close():
    """Only the closing component is removed, so it can still circle."""
    cmd, fired = follow_me.guard(VelocityCommand(vn=0.0, ve=1.0), range_m=3.0,
                                 min_standoff=5.0, bearing_to_target=0.0, drone_heading=0.0)
    assert not fired
    assert cmd.ve == pytest.approx(1.0)


def test_guard_is_inert_at_a_safe_range():
    cmd, fired = follow_me.guard(VelocityCommand(vn=1.0, ve=0.5), range_m=9.0,
                                 min_standoff=5.0, bearing_to_target=0.0, drone_heading=0.0)
    assert not fired and cmd.vn == 1.0 and cmd.ve == 0.5


def test_guard_does_nothing_without_a_range_estimate():
    """No estimate means no basis to intervene; the envelope still applies."""
    cmd, fired = follow_me.guard(VelocityCommand(vn=1.0), None, 5.0, 0.0, 0.0)
    assert not fired and cmd.vn == 1.0


@pytest.mark.parametrize("bearing", [0, 45, 90, 180, 270, 315])
def test_guard_removes_closing_motion_from_any_direction(bearing):
    b = math.radians(bearing)
    toward = VelocityCommand(vn=math.cos(b), ve=math.sin(b))
    cmd, fired = follow_me.guard(toward, 2.0, 5.0, bearing, 0.0)
    assert fired
    closing = cmd.vn * math.cos(b) + cmd.ve * math.sin(b)
    assert closing == pytest.approx(0.0, abs=1e-9)


# --- the leash ---------------------------------------------------------------

def test_leash_keeps_the_target_point_inside_the_fence():
    """You walking away must stop the aircraft, not drag it through the fence."""
    te, tn = follow_me.clamp_to_leash(40.0, 0.0, leash=20.0)
    assert math.hypot(te, tn) == pytest.approx(20.0)


def test_leash_leaves_a_point_inside_the_fence_alone():
    te, tn = follow_me.clamp_to_leash(3.0, 4.0, leash=20.0)
    assert (te, tn) == (3.0, 4.0)


def test_leash_preserves_direction():
    te, tn = follow_me.clamp_to_leash(30.0, 40.0, leash=10.0)
    assert te / tn == pytest.approx(30.0 / 40.0)


# --- target continuity -------------------------------------------------------

def box(cx, cy, h=100.0, w=40.0):
    return Detection(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def test_lock_takes_the_largest_person_first():
    lock = follow_me.TargetLock(frame_w=1280)
    small, big = box(200, 300, h=40), box(900, 300, h=160)
    assert lock.update([small, big]) is big


def test_lock_follows_the_same_person_across_frames():
    lock = follow_me.TargetLock(frame_w=1280)
    lock.update([box(600, 300)])
    picked = lock.update([box(640, 305), box(200, 300, h=200)])
    assert picked is not None and picked.cx == pytest.approx(640)


def test_a_cyclist_crossing_frame_does_not_steal_the_lock():
    """A bigger, nearer person appearing elsewhere must not become the subject."""
    lock = follow_me.TargetLock(frame_w=1280)
    lock.update([box(600, 300)])
    intruder = box(100, 300, h=400)          # much larger, far away in the frame
    assert lock.update([intruder]) is None, "lock jumped to a different person"


def test_losing_the_target_reports_nothing_rather_than_guessing():
    lock = follow_me.TargetLock(frame_w=1280)
    lock.update([box(600, 300)])
    assert lock.update([]) is None
    assert lock.lost_frames == 1


def test_lock_releases_after_a_long_absence():
    lock = follow_me.TargetLock(frame_w=1280)
    lock.update([box(600, 300)])
    for _ in range(31):
        lock.update([])
    assert not lock.locked
    # and is then free to lock onto whoever is actually there
    assert lock.update([box(100, 300)]) is not None


# --- never translate on a stale position estimate ----------------------------

FULL = VelocityCommand(vn=2.0, ve=2.0, vu=0.3, yaw_rate=10.0)
KW = dict(full=FULL, alt_error_vu=0.4, e=0.0, n=0.0,
          target_e=0.0, target_n=20.0, rng=20.0, standoff=8.0, leash=50.0)


def test_follow_translates_on_a_fresh_fix():
    cmd = follow_me.stage_command("follow", fresh=True, **KW)
    assert cmd.vn > 0, "should fly toward the standoff point"


def test_follow_refuses_to_translate_on_a_stale_fix():
    """Found in the simulator: the leash branch overrode the Follower's own
    'target lost, hold position' fallback and kept flying on an old estimate."""
    cmd = follow_me.stage_command("follow", fresh=False, **KW)
    assert cmd.vn == 0.0 and cmd.ve == 0.0, "flew on a stale position estimate"
    assert cmd.yaw_rate == FULL.yaw_rate, "should still turn toward the last estimate"


def test_follow_without_any_estimate_holds_position():
    cmd = follow_me.stage_command("follow", fresh=True, full=FULL, alt_error_vu=0.4,
                                  e=0.0, n=0.0, target_e=None, target_n=None,
                                  rng=None, standoff=8.0, leash=50.0)
    assert cmd.vn == 0.0 and cmd.ve == 0.0


def test_yaw_stage_never_translates():
    cmd = follow_me.stage_command("yaw", fresh=True, **KW)
    assert cmd.vn == 0.0 and cmd.ve == 0.0
    assert cmd.yaw_rate == FULL.yaw_rate


def test_watch_stage_only_holds_altitude():
    cmd = follow_me.stage_command("watch", fresh=True, **KW)
    assert (cmd.vn, cmd.ve, cmd.yaw_rate) == (0.0, 0.0, 0.0)
    assert cmd.vu == 0.4


def test_follow_respects_the_leash_when_the_target_walks_away():
    """A target beyond the fence must not drag the aircraft out through it."""
    cmd = follow_me.stage_command("follow", fresh=True, full=FULL, alt_error_vu=0.0,
                                  e=0.0, n=0.0, target_e=0.0, target_n=200.0,
                                  rng=200.0, standoff=8.0, leash=20.0)
    # the point it flies to is clamped to the leash, so the commanded velocity
    # corresponds to a target 20 m away, not 192 m away
    assert cmd.vn == pytest.approx(20.0 * 0.5)


# --- the camera the follower projects with ------------------------------------

def test_camera_falls_back_to_the_spec_sheet_and_says_so():
    cam, how = follow_me.load_camera(1280, 720)
    assert cam.width == 1280
    # whichever branch it took, the caller is told which
    assert "measured" in how or "NOT measured" in how


# --- the command must be held between camera frames ---------------------------

@pytest.mark.asyncio
async def test_the_command_survives_the_gaps_between_frames(free_port, monkeypatch, tmp_path):
    """The bug this catches made follow look completely broken.

    Video arrives about every 200 ms and the control loop spins every 30 ms. The
    loop used to send a hover on every spin that had no new frame, so six or
    seven zeros landed on top of each real command and the aircraft never
    accelerated. In the simulator it looked like a follower that refused to
    close range: the person walked away and the drone sat still.

    So: fly the real script at a stationary target and require that the aircraft
    actually moved toward it.
    """
    from phantom_brain.config import MissionConfig
    from phantom_brain.sim.server import SimServer

    cfg = MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5},
                  "polygon_enu": [[-80, -80], [80, -80], [80, 80], [-80, 80]]},
        "altitudes": {"follow": 10},
        # 18 m north: inside what the gimbal can see from 10 m at this standoff
        # (the top of the frame lands around 21 m), so detection is geometry and
        # not luck, and there is still 12 m to fly.
        "sim": {"frame_w": 320, "frame_h": 180, "video_latency_s": 0, "random_trees": 0,
                "people": [{"id": "target", "start": [0, 18], "path": [[0, 18]],
                            "speed": 0.0}]},
    })
    real_runs = Path(__file__).resolve().parents[1] / "runs" / "follow-me"
    baseline = {p.name for p in real_runs.glob("*")} if real_runs.exists() else set()

    async with SimServer(cfg, speed=6, port=free_port).running() as sim:
        await asyncio.sleep(0.2)
        monkeypatch.setattr(sys, "argv", [
            "follow_me.py", "--url", f"ws://127.0.0.1:{free_port}", "--detector", "oracle",
            "--stage", "follow", "--alt", "10", "--standoff", "6", "--min-standoff", "4",
            "--hold", "15", "--speed", "3", "--max-radius", "60", "--max-alt", "20",
        ])
        # Send this simulated flight's logs and frames somewhere disposable.
        # Without it the run lands in the real runs/follow-me/ directory beside
        # the day's actual measurements, and a simulated result gets quoted as
        # a real one. That happened on flight 02 and was caught by luck.
        monkeypatch.setenv("DRONE_RUNS_DIR", str(tmp_path / "runs"))
        monkeypatch.chdir(tmp_path)
        await follow_me.main()

        real_runs = Path(__file__).resolve().parents[1] / "runs" / "follow-me"
        before = {p.name for p in real_runs.glob("*")} if real_runs.exists() else set()
        assert not any(p.startswith(time.strftime("%Y%m%d")) and "sim" not in p
                       for p in (before - baseline)), \
            "a simulated flight wrote into the real flight-log directory"

        # Measure the excursion, not the final position: the script returns to
        # its start before landing, by design.
        assert sim.world.episode.max_radius > 5.0, (
            f"aircraft never got further than {sim.world.episode.max_radius:.1f} m from "
            f"home while chasing a target 18 m away; commands are being drowned "
            f"between frames")


# --- chase: the stage flight 03 exists to fly ---------------------------------

@pytest.mark.asyncio
async def test_chase_rotates_until_it_finds_someone_standing_behind_it(
        free_port, monkeypatch, tmp_path):
    """The handover case, which is every real handover.

    On flight 02 the aircraft was pointed wherever the manual takeoff left it,
    and a follow stage that only steers once it can already see you does nothing
    at all. So: put the subject directly BEHIND the aircraft, where no amount of
    good control helps, and require that it turns round and finds them.
    """
    import json

    from phantom_brain.config import MissionConfig
    from phantom_brain.sim.server import SimServer

    cfg = MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5},
                  "polygon_enu": [[-80, -80], [80, -80], [80, 80], [-80, 80]]},
        "altitudes": {"follow": 10},
        # Due SOUTH. The aircraft starts on heading 0, so this person is out of
        # frame by 180 degrees and the only way to see them is to rotate.
        "sim": {"frame_w": 320, "frame_h": 180, "video_latency_s": 0, "random_trees": 0,
                "people": [{"id": "target", "start": [0, -16], "path": [[0, -16]],
                            "speed": 0.0}]},
    })

    async with SimServer(cfg, speed=6, port=free_port).running():
        await asyncio.sleep(0.2)
        monkeypatch.setattr(sys, "argv", [
            "follow_me.py", "--url", f"ws://127.0.0.1:{free_port}", "--detector", "oracle",
            "--stage", "chase", "--alt", "10", "--hold", "40", "--speed", "2",
            "--max-radius", "60", "--max-alt", "20", "--search-yaw", "30",
        ])
        monkeypatch.setenv("DRONE_RUNS_DIR", str(tmp_path / "runs"))
        monkeypatch.chdir(tmp_path)
        await follow_me.main()

    rows = [json.loads(ln) for p in (tmp_path / "runs" / "follow-me").glob("*-perception.jsonl")
            for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows, "chase wrote no perception log"

    # It started blind...
    assert not rows[0]["locked"], "test is void: the subject was visible from the start"
    # ...and rotated rather than sitting there, which is the whole fix.
    assert any(not r["locked"] and abs(r["yaw"]) > 1.0 for r in rows), \
        "chase commanded no rotation while it had no target; it will sit still in the field"
    # ...and the rotation worked.
    locked = sum(r["locked"] for r in rows)
    assert locked > 0.3 * len(rows), \
        f"only {locked}/{len(rows)} frames locked after searching; it never found them"
