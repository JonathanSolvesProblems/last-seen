"""Follow a person using only what the picture shows. No calibration needed.

Flight 02 killed the geometric approach. To turn a detection into a ground
position you need the focal length, the altitude AND the gimbal angle, and the
gimbal reported -23 degrees while the frames said about -37. Two independent
estimates of the same distance came out 10.1 m and 15.9 m. Any standoff built on
that is wrong by tens of percent, and the thing it is holding a distance from is
a person.

So do not compute a position at all. Two signals in the image are enough:

    horizontal offset of the box  ->  yaw, to keep them centred
    HEIGHT of the box in pixels   ->  distance, because apparent size is
                                      inversely proportional to range

Holding a constant box height holds a constant distance. It does not matter what
the lens is, where the gimbal is pointing, or how high the aircraft is: if they
look the same size, they are the same distance away. A 10% error in focal length
shifts what that distance IS, and changes nothing about the aircraft's ability
to hold it.

The distance is therefore set by demonstration: stand where you want it to sit,
it records your apparent size, and it keeps you that size. That is also a better
interface than typing a number in metres.

Two rules keep it honest:

  Turn before moving. Forward is only toward the person when the camera is
  pointed at them, so translation is gated on the target being near the centre.

  Too close is a pixel measurement. A box taller than `close_ratio` times the
  target means they are nearer than intended, and the only motion allowed is
  motion that makes the box smaller. This needs no calibration either, which is
  exactly why it can be trusted when the geometry cannot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .detect import Detection
    from .protocol import VelocityCommand

MAX_ASSOC_FRACTION = 0.35    # of frame width: further than this is a different person


@dataclass
class ServoConfig:
    """Everything the controller is allowed to do, in one place."""
    max_speed: float = 1.0          # m/s, forward and back
    max_yaw_rate: float = 25.0      # deg/s
    # Translate only when the target is within this fraction of frame width of
    # centre. Forward is not toward them until the camera is facing them.
    face_first: float = 0.15
    # Do nothing about a distance error smaller than this fraction of the
    # target size, so the aircraft is not permanently hunting.
    size_deadband: float = 0.12
    # And do nothing about a yaw error smaller than this fraction of the frame.
    yaw_deadband: float = 0.05
    # Box this much bigger than target means too close: back off only.
    close_ratio: float = 1.5
    kp_size: float = 4.0
    kp_yaw: float = 2.2
    # Integral, because a proportional controller lags a MOVING target by
    # construction and this one is supposed to follow a walker. Worse, apparent
    # height is compressed by the viewing angle: a person who doubles their
    # distance does not halve in the frame, they shrink to about 70%, because
    # the camera is now seeing them more side-on and less from above. At the
    # old kp of 1.6 that 30% error asked for 0.93 m/s against a 1.2 m/s walk,
    # so the aircraft settled into a lag of 10 m and more and stayed there.
    ki_size: float = 0.5
    # Hard cap on the integral's share of the command, as a fraction of
    # max_speed. An integral wound up against a person is not a tuning
    # question, so it is bounded rather than trusted.
    max_integral_authority: float = 0.5
    # Damping on the distance loop. Proportional-only control of an aircraft
    # that takes about 0.6 s to reach a commanded velocity, over a video feed
    # about 0.2 s behind, oscillates: the simulator ran a steady limit cycle
    # between 17.5 m and 28.4 m on a subject walking in a straight line. The
    # rate of change of apparent size IS the closing rate, measured in pixels
    # per second, and needs no more calibration than the size itself does.
    kd_size: float = 1.1
    # Keep the subject vertically centred by moving the gimbal, in degrees per
    # unit of frame height off centre. A closed loop only needs the sign and a
    # sane magnitude, so this is not a calibration.
    kp_pitch: float = 34.0
    max_pitch_step: float = 6.0     # deg per frame, so the shot stays watchable
    pitch_deadband: float = 0.06    # fraction of frame height
    min_pitch: float = -90.0
    max_pitch: float = -5.0


@dataclass
class ServoCommand:
    forward: float          # m/s in the aircraft's own forward direction
    yaw_rate: float         # deg/s, clockwise positive
    too_close: bool         # the pixel-space range floor fired
    facing: bool            # camera is pointed at the target, so forward means toward
    size_error: float       # (target - actual) / target; positive means too far away


def follow_from_image(offset: float | None, box_h: float | None, target_px: float,
                      cfg: ServoConfig | None = None, *,
                      size_rate: float | None = None,
                      clipped: bool = False,
                      integral: float = 0.0) -> ServoCommand:
    """One frame in, one command out.

    `offset` is the target's horizontal distance from frame centre as a fraction
    of frame width, so -0.5 is the left edge and +0.5 the right. `box_h` is the
    detection's height in pixels. Both None when there is no target, which
    commands nothing: a controller that guesses when it cannot see is worse than
    one that waits.

    `size_rate` is how fast the box is growing, in pixels per second, and is the
    damping term. `clipped` says the box runs off the top or bottom of the
    frame, which makes its height a lie: a truncated box is SHORTER than the
    person, reads as further away, and asks the aircraft to fly toward someone
    who may in fact be too close. Distance control is suspended while that is
    true, and yaw is not, because the horizontal centre is still honest.
    """
    cfg = cfg or ServoConfig()
    if offset is None or box_h is None or box_h <= 0 or target_px <= 0:
        return ServoCommand(0.0, 0.0, False, False, 0.0)

    # --- yaw: centre them -------------------------------------------------
    yaw = 0.0
    if abs(offset) > cfg.yaw_deadband:
        yaw = _clamp(offset * cfg.kp_yaw * cfg.max_yaw_rate,
                     -cfg.max_yaw_rate, cfg.max_yaw_rate)

    # --- distance: hold their apparent size -------------------------------
    # Positive error means the box is SMALLER than wanted, so they are further
    # away than wanted, so move forward.
    size_error = (target_px - box_h) / target_px
    facing = abs(offset) <= cfg.face_first
    too_close = box_h > target_px * cfg.close_ratio

    forward = 0.0
    if abs(size_error) > cfg.size_deadband:
        # PID. The derivative is in the same units as the error, one per
        # second, so the three gains are directly comparable.
        rate_term = 0.0 if size_rate is None else cfg.kd_size * size_rate / target_px
        i_term = _clamp(cfg.ki_size * integral,
                        -cfg.max_integral_authority, cfg.max_integral_authority)
        forward = _clamp((size_error * cfg.kp_size + i_term - rate_term) * cfg.max_speed,
                         -cfg.max_speed, cfg.max_speed)
    if not facing:
        # Turn first. Moving forward while the target is off to one side flies
        # past them rather than toward them.
        forward = 0.0
    if clipped:
        # A truncated box cannot be measured, so it is not acted on.
        forward = 0.0
    if too_close and forward > 0:
        # Never close on someone who is already nearer than intended. Backing
        # away stays allowed, which is the whole point of the asymmetry.
        forward = 0.0

    return ServoCommand(forward, yaw, too_close, facing, size_error)


def gimbal_step(v_offset: float | None, current_pitch: float,
                cfg: ServoConfig | None = None) -> float:
    """Where to point the camera next, to keep the subject vertically centred.

    The gimbal was set once at takeoff and never moved, which is why 296 of 785
    tracked frames in the simulator had the subject jammed against the top edge:
    walk away from a camera pinned at a fixed depression angle and you rise out
    of the shot. Pitch is a third control axis and it is as calibration-free as
    the other two, because the loop is closed on the picture.

    `v_offset` is the box centre's distance below the middle of the frame as a
    fraction of frame height, positive downward. None holds the current angle.
    """
    cfg = cfg or ServoConfig()
    if v_offset is None or abs(v_offset) <= cfg.pitch_deadband:
        return _clamp(current_pitch, cfg.min_pitch, cfg.max_pitch)
    # Below the centre of frame means closer to straight down, so pitch down.
    delta = _clamp(-v_offset * cfg.kp_pitch, -cfg.max_pitch_step, cfg.max_pitch_step)
    return _clamp(current_pitch + delta, cfg.min_pitch, cfg.max_pitch)


class SizeTracker:
    """Apparent size, and how fast it is changing, over a short window.

    A single frame's box height is too noisy to differentiate, so the rate is
    the slope of a straight line through the last `window_s` of measurements.
    The same fit supplies a smoothed height, which is what the proportional
    term should be reading as well: one detection pixel at the far end of a
    follow is several percent of the target size.
    """

    def __init__(self, window_s: float = 0.7):
        self.window_s = window_s
        self._pts: list[tuple[float, float]] = []

    def reset(self) -> None:
        self._pts.clear()

    def update(self, t: float, box_h: float) -> tuple[float, float | None]:
        """Returns (smoothed height, rate in px/s or None if not yet known)."""
        self._pts.append((t, box_h))
        cutoff = t - self.window_s
        self._pts = [p for p in self._pts if p[0] >= cutoff]
        n = len(self._pts)
        if n < 3:
            return box_h, None
        mt = sum(p[0] for p in self._pts) / n
        mh = sum(p[1] for p in self._pts) / n
        sxx = sum((p[0] - mt) ** 2 for p in self._pts)
        if sxx < 1e-9:
            return mh, None
        slope = sum((p[0] - mt) * (p[1] - mh) for p in self._pts) / sxx
        # Evaluate the fit at now, rather than returning the window mean, so
        # the smoothed height does not lag behind a subject who is moving.
        return mh + slope * (t - mt), slope


class ChaseController:
    """The image-space follow, with its two pieces of state in one place.

    `follow_from_image` stays pure and is where the rules live. This holds the
    apparent-size history that the derivative needs and the integral that stops
    the aircraft settling into a permanent lag behind a walking subject.
    """

    def __init__(self, cfg: ServoConfig | None = None, lost_timeout_s: float = 1.5):
        self.cfg = cfg or ServoConfig()
        self.sizes = SizeTracker()
        self.integral = 0.0
        self.lost_timeout_s = lost_timeout_s
        self._last_t: float | None = None
        self.smoothed: float | None = None
        self.rate: float | None = None

    def reset(self) -> None:
        self.sizes.reset()
        self.integral = 0.0
        self._last_t = None
        self.smoothed = self.rate = None

    def step(self, t: float, offset: float | None, box_h: float | None,
             target_px: float, *, clipped: bool = False) -> ServoCommand:
        dt = 0.0 if self._last_t is None else t - self._last_t
        # A long gap means this may not even be the same situation any more, so
        # the integral is abandoned rather than resumed.
        if dt < 0 or dt > self.lost_timeout_s:
            self.reset()
            dt = 0.0
        self._last_t = t

        if offset is None or box_h is None or box_h <= 0 or target_px <= 0:
            self.sizes.reset()
            self.smoothed = self.rate = None
            return follow_from_image(None, None, target_px, self.cfg)

        if clipped:
            # A truncated box is not a measurement, so it must not enter the
            # history either: one clipped frame in the window drags the fit.
            self.sizes.reset()
            self.smoothed, self.rate = box_h, None
        else:
            self.smoothed, self.rate = self.sizes.update(t, box_h)

        cmd = follow_from_image(offset, self.smoothed, target_px, self.cfg,
                                size_rate=self.rate, clipped=clipped,
                                integral=self.integral)

        # Integrate only where integrating is meaningful and safe: a real
        # measurement, the camera pointed at them, and the command not already
        # saturated. Winding up against a limit is how an integrator turns into
        # an overshoot, and this one is pointed at a person.
        saturated = abs(cmd.forward) >= self.cfg.max_speed - 1e-9
        if dt > 0 and not clipped and cmd.facing and not cmd.too_close and not saturated:
            self.integral += cmd.size_error * dt
            cap = self.cfg.max_integral_authority / max(self.cfg.ki_size, 1e-6)
            self.integral = _clamp(self.integral, -cap, cap)
        return cmd


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def search_yaw_rate(last_offset: float | None, rate: float) -> float:
    """Which way to rotate when there is no target in frame.

    Toward the side they were last seen on. Someone who walks off the left edge
    is to the left, and sweeping right from there costs most of a full circle at
    12 deg/s before the camera comes back round to them. With no history, the
    direction is arbitrary and positive is as good as any.
    """
    if last_offset is None or last_offset == 0.0:
        return rate
    return rate if last_offset > 0 else -rate


def approximate_range(box_h: float, person_height_m: float, focal_px: float) -> float | None:
    """Distance implied by apparent size. FOR REPORTING ONLY.

    The controller never uses this: it is the one number that depends on the
    focal length being right, and flight 02 showed the focal length is not
    confirmed. It is here so a log can carry a human-readable metre figure, and
    anything printed from it must be labelled approximate.
    """
    if box_h <= 0 or focal_px <= 0:
        return None
    return person_height_m * focal_px / box_h

class TargetLock:
    """One person, kept across frames by nearest-box association.

    Deliberately simple and deliberately conservative: the first lock is the
    largest person seen, and after that only a detection close to the last one
    can inherit the lock. Losing it stops the aircraft rather than re-locking
    onto whoever is now biggest.
    """

    def __init__(self, frame_w: int):
        self.max_jump = MAX_ASSOC_FRACTION * frame_w
        self.last: "Detection | None" = None
        self.locked = False
        self.lost_frames = 0

    def update(self, dets: list["Detection"]) -> "Detection | None":
        if not dets:
            self.lost_frames += 1
            if self.lost_frames > 30:
                self.last = None
                self.locked = False
            return None
        if self.last is None:
            pick = max(dets, key=lambda d: d.h)
            self.last, self.locked, self.lost_frames = pick, True, 0
            return pick
        near = min(dets, key=lambda d: math.hypot(d.cx - self.last.cx, d.cy - self.last.cy))
        if math.hypot(near.cx - self.last.cx, near.cy - self.last.cy) > self.max_jump:
            self.lost_frames += 1
            return None
        self.last, self.lost_frames = near, 0
        return near


def chase_command(servo: ServoCommand, heading_deg: float, alt_error_vu: float,
                  e: float, n: float, leash: float) -> "VelocityCommand":
    """Turn an image-space command into a ground-frame velocity.

    Yaw keeps the target centred, so the aircraft's own forward direction is
    toward them and `body_to_ground` is the whole conversion. Nothing here needs
    the camera's focal length, the gimbal angle or the altitude.

    The leash is the one part that uses real coordinates, and it uses GPS rather
    than the camera, so flight 02's projection error does not reach it: motion
    that would take the aircraft further outside its fence is dropped.
    """
    from .control import body_to_ground  # noqa: PLC0415
    from .protocol import VelocityCommand  # noqa: PLC0415
    vn, ve = body_to_ground(servo.forward, 0.0, heading_deg)
    r = math.hypot(e, n)
    if r > leash and r > 1e-6:
        outward = (vn * n + ve * e) / r
        if outward > 0:
            vn -= outward * n / r
            ve -= outward * e / r
    return VelocityCommand(vn, ve, alt_error_vu, servo.yaw_rate)
