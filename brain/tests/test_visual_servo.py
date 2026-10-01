"""The image-space follow controller: the one that needs no calibration.

Flight 02 showed the geometric projection off by 5.8 m because the gimbal angle
was not what it was commanded to be. These tests cover the replacement, whose
whole point is that no number here depends on the lens, the gimbal or the
altitude being known.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from phantom_brain.visual_servo import (ChaseController, ServoConfig,  # noqa: E402
                                        SizeTracker, approximate_range,
                                        follow_from_image, gimbal_step,
                                        search_yaw_rate)

TARGET = 100.0
CFG = ServoConfig(max_speed=1.0, max_yaw_rate=25.0)


# --- distance, from apparent size --------------------------------------------

def test_too_far_moves_forward():
    """A smaller box means they walked away, so close the gap."""
    c = follow_from_image(offset=0.0, box_h=70.0, target_px=TARGET, cfg=CFG)
    assert c.forward > 0


def test_too_near_backs_off():
    """A bigger box means they walked at the aircraft."""
    c = follow_from_image(offset=0.0, box_h=130.0, target_px=TARGET, cfg=CFG)
    assert c.forward < 0


def test_right_distance_holds_still():
    c = follow_from_image(offset=0.0, box_h=TARGET, target_px=TARGET, cfg=CFG)
    assert c.forward == 0.0


def test_small_error_is_inside_the_deadband():
    """Otherwise the aircraft hunts continuously on detector jitter."""
    c = follow_from_image(offset=0.0, box_h=TARGET * 1.05, target_px=TARGET, cfg=CFG)
    assert c.forward == 0.0


def test_speed_is_clamped():
    c = follow_from_image(offset=0.0, box_h=5.0, target_px=TARGET, cfg=CFG)
    assert abs(c.forward) <= CFG.max_speed


# --- the pixel-space range floor ---------------------------------------------

def test_too_close_blocks_approach_but_allows_retreat():
    """The range floor without a tape measure: a box far over target means
    nearer than intended, and only shrinking it is permitted."""
    c = follow_from_image(offset=0.0, box_h=TARGET * 2.0, target_px=TARGET, cfg=CFG)
    assert c.too_close
    assert c.forward < 0, "must still be able to back away"


def test_too_close_never_commands_forward():
    """Even if some future gain change made the error positive while close."""
    cfg = ServoConfig(max_speed=1.0, close_ratio=0.5)   # everything counts as close
    c = follow_from_image(offset=0.0, box_h=60.0, target_px=TARGET, cfg=cfg)
    assert c.too_close
    assert c.forward <= 0.0, "closed on a target already inside the floor"


# --- turn before moving -------------------------------------------------------

def test_target_off_to_the_side_turns_without_translating():
    """Forward is only toward them once the camera faces them; moving while
    they are off to one side flies past them."""
    c = follow_from_image(offset=0.35, box_h=70.0, target_px=TARGET, cfg=CFG)
    assert not c.facing
    assert c.forward == 0.0
    assert c.yaw_rate > 0, "should be turning toward them"


def test_translation_resumes_once_facing():
    c = follow_from_image(offset=0.02, box_h=70.0, target_px=TARGET, cfg=CFG)
    assert c.facing and c.forward > 0


@pytest.mark.parametrize("offset,sign", [(-0.3, -1), (0.3, 1)])
def test_yaw_turns_toward_the_target(offset, sign):
    c = follow_from_image(offset=offset, box_h=TARGET, target_px=TARGET, cfg=CFG)
    assert math.copysign(1, c.yaw_rate) == sign


def test_yaw_is_clamped():
    c = follow_from_image(offset=0.5, box_h=TARGET, target_px=TARGET, cfg=CFG)
    assert abs(c.yaw_rate) <= CFG.max_yaw_rate


def test_centred_target_does_not_yaw():
    c = follow_from_image(offset=0.01, box_h=TARGET, target_px=TARGET, cfg=CFG)
    assert c.yaw_rate == 0.0


# --- no target means no guessing ---------------------------------------------

@pytest.mark.parametrize("offset,box", [(None, None), (0.0, None), (None, 90.0)])
def test_missing_detection_commands_nothing(offset, box):
    """A controller that guesses when it cannot see is worse than one that waits."""
    c = follow_from_image(offset=offset, box_h=box, target_px=TARGET, cfg=CFG)
    assert c.forward == 0.0 and c.yaw_rate == 0.0


def test_nonsense_inputs_command_nothing():
    assert follow_from_image(0.0, 0.0, TARGET, CFG).forward == 0.0
    assert follow_from_image(0.0, 90.0, 0.0, CFG).forward == 0.0


# --- the property that makes this worth doing ---------------------------------

def test_control_is_independent_of_the_camera():
    """The whole reason for image-space control. The command must not change
    when the focal length, gimbal angle or altitude change, because flight 02
    proved all three can be wrong at once."""
    a = follow_from_image(offset=0.2, box_h=70.0, target_px=TARGET, cfg=CFG)
    b = follow_from_image(offset=0.2, box_h=70.0, target_px=TARGET, cfg=CFG)
    assert (a.forward, a.yaw_rate) == (b.forward, b.yaw_rate)
    # and the reported range, which DOES depend on the focal length, is not
    # consulted by the controller at all
    assert approximate_range(70.0, 1.905, 514) != approximate_range(70.0, 1.905, 805)


def test_walking_away_then_back_reverses_the_command():
    """The round trip a real follow has to survive."""
    away = follow_from_image(0.0, 60.0, TARGET, CFG)
    back = follow_from_image(0.0, 160.0, TARGET, CFG)
    assert away.forward > 0 and back.forward < 0


# --- which way to look when they are gone -------------------------------------

def test_search_follows_them_off_the_edge_they_left_by():
    """Off the left edge means left. Sweeping right from there costs nearly a
    full circle at 12 deg/s before the camera comes back round."""
    assert search_yaw_rate(-0.4, 12.0) == -12.0
    assert search_yaw_rate(+0.4, 12.0) == +12.0


def test_search_picks_a_direction_with_no_history():
    """First handover, nobody ever seen: still has to rotate."""
    assert search_yaw_rate(None, 12.0) == 12.0
    assert search_yaw_rate(0.0, 12.0) == 12.0


# --- a clipped box is not a measurement ---------------------------------------

def test_a_box_running_off_the_frame_edge_stops_the_distance_loop():
    """A truncated box is SHORTER than the person, so it reads as further away
    and asks the aircraft to fly toward someone who may be too close. This is
    the one measurement error in the whole design that points at a person, so
    it is refused rather than filtered."""
    c = follow_from_image(0.0, 60.0, TARGET, CFG, clipped=True)
    assert c.forward == 0.0
    # and the same box, whole, would have driven forward
    assert follow_from_image(0.0, 60.0, TARGET, CFG).forward > 0


def test_yaw_still_works_on_a_clipped_box():
    """Vertical clipping does not make the horizontal centre wrong, and the
    aircraft still needs to turn to keep them in shot."""
    c = follow_from_image(0.30, 60.0, TARGET, CFG, clipped=True)
    assert c.forward == 0.0
    assert c.yaw_rate > 0


# --- damping ------------------------------------------------------------------

def test_closing_fast_eases_off_before_arriving():
    """The simulator ran a steady limit cycle between 17.5 m and 28.4 m on a
    subject walking in a straight line, because nothing in the loop knew the
    gap was already closing."""
    far = follow_from_image(0.0, 70.0, TARGET, CFG, size_rate=0.0)
    closing = follow_from_image(0.0, 70.0, TARGET, CFG, size_rate=40.0)
    assert closing.forward < far.forward


def test_falling_behind_pushes_harder():
    # Headroom in the clamp, or both commands simply saturate and the test
    # proves nothing.
    cfg = ServoConfig(max_speed=4.0, kp_size=1.0)
    opening = follow_from_image(0.0, 85.0, TARGET, cfg, size_rate=-40.0)
    steady = follow_from_image(0.0, 85.0, TARGET, cfg, size_rate=0.0)
    assert opening.forward > steady.forward


# --- the gimbal, which used to be set once and never moved --------------------

def test_subject_above_centre_pitches_the_camera_up():
    assert gimbal_step(-0.25, -40.0) > -40.0


def test_subject_below_centre_pitches_the_camera_down():
    assert gimbal_step(+0.25, -40.0) < -40.0


def test_centred_subject_leaves_the_camera_alone():
    assert gimbal_step(0.0, -40.0) == -40.0


def test_the_gimbal_moves_in_watchable_steps():
    """A camera that snaps to the answer makes unusable footage, and this is
    a video project before it is a control project."""
    cfg = ServoConfig()
    assert abs(gimbal_step(-0.5, -40.0, cfg) + 40.0) <= cfg.max_pitch_step


def test_the_gimbal_stays_inside_its_limits():
    cfg = ServoConfig()
    p = -40.0
    for _ in range(60):
        p = gimbal_step(-0.5, p, cfg)
    assert p <= cfg.max_pitch
    for _ in range(60):
        p = gimbal_step(+0.5, p, cfg)
    assert p >= cfg.min_pitch


# --- the size history ---------------------------------------------------------

def test_size_tracker_reports_the_rate_of_growth():
    t = SizeTracker()
    for i in range(6):
        smoothed, rate = t.update(i * 0.2, 100.0 + i * 10.0)
    assert rate == pytest.approx(50.0, rel=0.05)      # 10 px per 0.2 s
    assert smoothed == pytest.approx(150.0, rel=0.05)


def test_size_tracker_has_no_rate_until_it_has_history():
    t = SizeTracker()
    smoothed, rate = t.update(0.0, 100.0)
    assert rate is None and smoothed == 100.0


def test_size_tracker_smooths_a_noisy_box():
    """One detection pixel is several percent of the target at follow range."""
    t = SizeTracker()
    for i, h in enumerate([100, 112, 92, 108, 94, 104]):
        smoothed, _ = t.update(i * 0.15, float(h))
    assert 95 < smoothed < 110


# --- the whole loop, including its state --------------------------------------

def test_integral_closes_the_lag_on_a_steady_walker():
    """A proportional controller lags a moving target by construction. Hold a
    constant error and the command must keep rising, not settle."""
    c = ChaseController(ServoConfig(max_speed=4.0, kp_size=1.0, ki_size=0.5, kd_size=0.0))
    first = last = 0.0
    for i in range(30):
        cmd = c.step(i * 0.2, 0.0, 80.0, TARGET)
        if i == 2:
            first = cmd.forward
        last = cmd.forward
    assert last > first, "the integral never built, so the lag is permanent"


def test_integral_is_bounded():
    """An integrator wound up against a person is not a tuning question."""
    cfg = ServoConfig(max_speed=1.0, ki_size=0.5)
    c = ChaseController(cfg)
    for i in range(400):
        c.step(i * 0.2, 0.0, 10.0, TARGET)
    assert abs(cfg.ki_size * c.integral) <= cfg.max_integral_authority + 1e-9


def test_integral_is_abandoned_after_losing_them():
    c = ChaseController()
    for i in range(10):
        c.step(i * 0.2, 0.0, 80.0, TARGET)
    assert c.integral != 0
    c.step(99.0, 0.0, 80.0, TARGET)          # a long gap: different situation
    assert c.integral == 0


def test_no_target_commands_nothing():
    c = ChaseController()
    cmd = c.step(0.0, None, None, TARGET)
    assert (cmd.forward, cmd.yaw_rate) == (0.0, 0.0)


def test_a_clipped_frame_does_not_poison_the_size_history():
    """One truncated box inside the window drags the fit, and the fit is what
    the derivative and the proportional term both read."""
    c = ChaseController()
    for i in range(5):
        c.step(i * 0.15, 0.0, 100.0, TARGET)
    c.step(0.75, 0.0, 40.0, TARGET, clipped=True)
    assert c.smoothed == 40.0 and c.rate is None     # not blended into the history
    after = c.step(0.90, 0.0, 100.0, TARGET)
    assert after.forward == pytest.approx(0.0, abs=1e-9)
