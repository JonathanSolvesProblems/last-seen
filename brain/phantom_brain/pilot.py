"""One flight session: preflight, arm, fly inside an envelope, always let go.

Flight 01 shipped two scripts with their own copies of this logic, and the worst
bug of the day (virtual stick left enabled, so the RC sticks were dead while the
aircraft sat armed on the ground) was fixed in one copy and not the other. That
is the reason this module exists: the release path must have exactly one
implementation, and it must not depend on anyone remembering to call it.

So `FlightSession` is an async context manager. Leaving the block releases
control, whatever happened inside it, including on an abort, an exception, or a
pilot takeover. Nothing else in the codebase is allowed to enable virtual stick.

The hardware behaviours encoded here were all learned on 2026-09-25 and are
documented in flights/2026-09-25-flight-01/BUGS.md:

  - a command ack means accepted, never applied: verify against telemetry
  - startTakeoff is acked and ignored while the motors are armed after a landing
  - telemetry lags the SDK, so confirm virtual stick before trusting a check
  - the pilot's override is the hardware switch, and it always wins
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import geo
from .client import BridgeClient, BridgeError
from .protocol import VelocityCommand


def runs_root() -> Path:
    """Where flight logs go. `DRONE_RUNS_DIR` redirects simulated runs away
    from the real ones, so a test flight can never be mistaken for a real
    measurement later."""
    env = os.environ.get("DRONE_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "runs"


class Abort(RuntimeError):
    """The envelope was left, or the pilot took over. Never a programming error."""


async def ensure_feed(bc: BridgeClient, seconds: float = 6.0, *, expect_motion: bool = False,
                      recover: bool = True, log=print) -> None:
    """Refuse to trust perception until the pixels say the camera is alive.

    On a black feed this cycles the aircraft connection once and re-checks,
    because the P3S camera session dies regularly and the fix is always the
    same. It says when it does that: a silent retry would hide a real fault.

    `expect_motion` False on the ground, where a parked aircraft looking at a
    static scene legitimately produces near-identical frames. True in the air,
    where the scene has to be changing.
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from .video import FeedMonitor  # noqa: PLC0415

    async def sample() -> "FeedMonitor":
        mon = FeedMonitor()
        end = time.monotonic() + seconds
        while time.monotonic() < end and mon.total < mon.window:
            fr = bc.new_frame()
            if fr is None:
                await asyncio.sleep(0.03)
                continue
            mon.add(cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR))
        return mon

    v = (await sample()).verdict(expect_motion=expect_motion)
    if v.ok:
        log(f"feed check: {v}")
        return
    if not recover:
        raise Abort(f"video feed unusable: {v.reason}")
    log(f"feed check: {v}")
    log("cycling the aircraft connection to revive the camera")
    try:
        await bc.reconnect_product()
    except BridgeError as e:
        raise Abort(f"video feed unusable and the reconnect failed: {e}") from e
    await asyncio.sleep(3)
    v = (await sample()).verdict(expect_motion=expect_motion)
    log(f"feed check after reconnect: {v}")
    if not v.ok:
        raise Abort(f"video feed unusable after a reconnect: {v.reason}")


@dataclass
class Envelope:
    """Hard bounds. Nothing in a session is allowed outside these."""
    max_alt: float = 12.0
    max_radius: float = 20.0
    min_battery: int = 25
    min_sats: int = 7
    stale_s: float = 2.0
    max_speed: float = 1.0
    max_climb: float = 0.5
    max_yaw_rate: float = 30.0


@dataclass
class Step:
    name: str
    detail: str = ""
    ok: bool | None = None
    data: dict = field(default_factory=dict)


class FlightSession:
    """Usage:

        async with FlightSession(bc, name="first-flight") as fs:
            await fs.preflight()
            await fs.takeoff_and_take_control()
            await fs.climb_to(4.0)
            ...
        # control is released here, no matter how the block ended
    """

    def __init__(self, bc: BridgeClient, *, name: str, envelope: Envelope | None = None,
                 run_root: Path | None = None, capture_dir: Path | None = None,
                 capture_every: int = 2):
        self.bc = bc
        self.name = name
        self.env = envelope or Envelope()
        self.origin: geo.Origin | None = None
        self.steps: list[Step] = []
        self.aborted: str | None = None
        self.took_control = False
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        # DRONE_RUNS_DIR lets the tests send simulated flights somewhere
        # disposable. Without it a pytest run writes into the real flight logs,
        # and a simulated "83.7% locked at 10 m" sits in the same folder as the
        # day's actual measurements waiting to be quoted as real.
        root = run_root or (runs_root() / name)
        root.mkdir(parents=True, exist_ok=True)
        self.report_path = root / f"{self.stamp}.json"
        self._tel = open(root / f"{self.stamp}.jsonl", "w", encoding="utf-8")
        # Flight 01 logged telemetry and no video, so the axis tests have numbers
        # and no pictures. Frames are cheap; capture them by default.
        self.capture_dir = capture_dir
        self.capture_every = max(1, capture_every)
        self._frames_seen = 0
        self.frames_saved = 0
        # Heading accumulated across wraps. Differencing two compass readings
        # cannot tell 366 degrees from 6, which is fine at 90 degrees a leg and
        # wrong the moment a turn exceeds half a circle.
        self.heading_total = 0.0
        self._last_heading: float | None = None
        # Highest battery seen. A pack does not lose tens of percent between two
        # samples, so a reading far below this is a corrupt frame from a dying
        # link, not a flat battery. Flight 02 aborted on a 69% -> 0% jump that
        # arrived as the Wi-Fi dropped, then could not land because the link it
        # was reporting about had already gone.
        self._battery_seen: int | None = None
        if self.capture_dir is not None:
            self.capture_dir.mkdir(parents=True, exist_ok=True)

    # ---- context manager ---------------------------------------------------

    async def __aenter__(self) -> "FlightSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is Abort:
            self.aborted = str(exc)
            self.log(f"ABORT: {exc}")
        elif exc_type is BridgeError:
            self.aborted = f"bridge: {exc}"
            self.log(f"ABORT: bridge {exc}")
        elif exc_type is not None:
            self.aborted = f"{exc_type.__name__}: {exc}"
            self.log(f"ABORT: {self.aborted}")
        await self.land_and_release()
        self.write_report()
        self._tel.close()
        # Abort and BridgeError are the expected ways a flight ends; anything
        # else is a real fault and should reach the caller with its traceback.
        return exc_type in (Abort, BridgeError)

    # ---- recording ---------------------------------------------------------

    def log(self, msg: str) -> None:
        t = self.bc.telemetry
        pos = ""
        if t and self.origin and t.gps_fix:
            e, n = self.origin.to_local(t.lat, t.lon)
            pos = f"  [N{n:+6.1f} E{e:+6.1f} alt{t.alt:5.1f} hdg{t.heading:5.0f} bat{t.battery:3d}%]"
        print(f"{time.strftime('%H:%M:%S')} {msg}{pos}", flush=True)

    def note(self, name: str, detail: str = "", ok: bool | None = None, **data: Any) -> Step:
        s = Step(name, detail, ok, data)
        self.steps.append(s)
        return s

    def sample(self) -> None:
        """One telemetry row, and a frame if one is waiting. Flushed every time:
        a crashed flight must not lose its last seconds."""
        t = self.bc.telemetry
        if t is not None:
            if self._last_heading is not None:
                self.heading_total += (t.heading - self._last_heading + 540) % 360 - 180
            self._last_heading = t.heading
            row = {"t": t.t, "lat": t.lat, "lon": t.lon, "alt": t.alt, "hdg": t.heading,
                   "hdg_total": round(self.heading_total, 1),
                   "vn": t.vn, "ve": t.ve, "vu": t.vu, "roll": t.roll, "pitch": t.pitch,
                   # The REPORTED gimbal angle, never the commanded one. Flight 02
                   # could not be calibrated because this was missing: the camera
                   # was pitched far steeper than the -23 deg it was asked for, and
                   # every distance derived from the projection was wrong as a result.
                   "gimbal": t.gimbal_pitch,
                   "bat": t.battery, "vs": t.virtual_stick, "mode": t.flight_mode}
            if self.origin and t.gps_fix:
                e, n = self.origin.to_local(t.lat, t.lon)
                row["n"], row["e"] = round(n, 2), round(e, 2)
            self._tel.write(json.dumps(row) + "\n")
            self._tel.flush()
        if self.capture_dir is not None:
            fr = self.bc.new_frame()
            if fr is not None:
                self._frames_seen += 1
                if self._frames_seen % self.capture_every == 0:
                    (self.capture_dir / f"{fr.seq:06d}.jpg").write_bytes(fr.jpeg)
                    self.frames_saved += 1

    def write_report(self) -> None:
        payload = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "name": self.name,
                   "aborted": self.aborted, "frames_saved": self.frames_saved,
                   "envelope": asdict(self.env),
                   "steps": [asdict(s) for s in self.steps]}
        self.report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ---- safety ------------------------------------------------------------

    # Below this much of a single-sample drop, believe the battery. Above it,
    # the link is failing rather than the pack. Real discharge under load is
    # well under 1% per second on this aircraft.
    MAX_BATTERY_DROP_PCT = 25

    def battery_is_plausible(self, pct: int) -> bool:
        """False for a reading that cannot be a real discharge.

        Deliberately one-sided: a battery reading that has RISEN, or fallen a
        little, is always believed. Only an implausible collapse is rejected,
        so this can never talk the aircraft out of a genuine low-battery abort
        that arrives gradually, which is how real ones arrive.
        """
        if self._battery_seen is None:
            self._battery_seen = pct
            return True
        if pct > self._battery_seen - self.MAX_BATTERY_DROP_PCT:
            self._battery_seen = max(pct, self._battery_seen - self.MAX_BATTERY_DROP_PCT)
            return True
        self.log(f"ignoring an implausible battery reading of {pct}% "
                 f"(was {self._battery_seen}%); the link is probably failing")
        return False

    def check(self, *, airborne: bool) -> None:
        """Raise Abort the moment anything leaves the envelope."""
        t = self.bc.telemetry
        if t is None:
            raise Abort("no telemetry")
        age = time.monotonic() - self.bc.last_rx
        if age > self.env.stale_s:
            raise Abort(f"link stale for {age:.1f} s")
        if self.battery_is_plausible(t.battery) and t.battery < self.env.min_battery:
            raise Abort(f"battery {t.battery}%")
        if t.alt > self.env.max_alt:
            raise Abort(f"altitude {t.alt:.1f} m over the {self.env.max_alt:.0f} m ceiling")
        if self.origin is not None and t.gps_fix:
            e, n = self.origin.to_local(t.lat, t.lon)
            r = math.hypot(e, n)
            if r > self.env.max_radius:
                raise Abort(f"{r:.1f} m from start, over the {self.env.max_radius:.0f} m fence")
        if airborne and self.took_control and not t.virtual_stick:
            raise Abort("pilot took control (virtual stick ended)")

    def clamp(self, v: VelocityCommand) -> VelocityCommand:
        """No caller can command faster than the envelope allows."""
        s, c, y = self.env.max_speed, self.env.max_climb, self.env.max_yaw_rate
        return VelocityCommand(max(-s, min(s, v.vn)), max(-s, min(s, v.ve)),
                               max(-c, min(c, v.vu)), max(-y, min(y, v.yaw_rate)))

    # ---- primitives --------------------------------------------------------

    def here(self) -> tuple[float, float]:
        """Metres north and east of the zero point."""
        t = self.bc.telemetry
        assert t is not None and self.origin is not None
        e, n = self.origin.to_local(t.lat, t.lon)
        return n, e

    async def stream(self, cmd: VelocityCommand, seconds: float, *, airborne: bool = True) -> None:
        cmd = self.clamp(cmd)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check(airborne=airborne)
            await self.bc.send_velocity(cmd)
            self.sample()
            await asyncio.sleep(0.1)

    async def hold(self, seconds: float) -> None:
        await self.stream(VelocityCommand(), seconds)

    async def climb_to(self, target: float, timeout: float = 25.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.check(airborne=True)
            t = self.bc.telemetry
            assert t is not None
            err = target - t.alt
            if abs(err) < 0.4:
                break
            await self.bc.send_velocity(self.clamp(VelocityCommand(0, 0, err)))
            self.sample()
            await asyncio.sleep(0.1)
        await self.hold(2.0)

    async def return_to_origin(self, tolerance: float = 1.0, timeout: float = 40.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.check(airborne=True)
            n, e = self.here()
            if math.hypot(n, e) < tolerance:
                break
            await self.bc.send_velocity(self.clamp(VelocityCommand(-n * 0.4, -e * 0.4)))
            self.sample()
            await asyncio.sleep(0.1)
        await self.hold(2.0)

    def zero_origin(self) -> None:
        t = self.bc.telemetry
        assert t is not None
        self.origin = geo.Origin(t.lat, t.lon)

    # ---- lifecycle ---------------------------------------------------------

    async def wait_for_switch(self, letter: str = "F", timeout: float = 120.0) -> str:
        """Give the pilot a window to reach F rather than failing them for it."""
        try:
            await self.bc.enable_multi_mode()
        except BridgeError as e:
            self.log(f"multiple flight mode: {e}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            t = self.bc.telemetry
            if t is not None and t.rc_mode_switch == letter:
                return letter
            self.log(f"waiting for S1 to reach {letter} "
                     f"(it reads {(t.rc_mode_switch if t else None) or '?'} now)")
            await asyncio.sleep(5)
        t = self.bc.telemetry
        return (t.rc_mode_switch if t else "") or ""

    async def preflight(self, *, require_switch: str | None = "F", min_battery: int = 40,
                        wait_for_switch_s: float = 120.0) -> Step:
        await self.bc.wait_for_telemetry(15)
        if require_switch:
            await self.wait_for_switch(require_switch, wait_for_switch_s)
        t = self.bc.telemetry
        assert t is not None
        problems = []
        if not t.gps_fix:
            problems.append("no GPS fix")
        if t.gps_sats < self.env.min_sats:
            problems.append(f"only {t.gps_sats} satellites")
        if not t.home_set:
            problems.append("home point not set")
        if t.battery < min_battery:
            problems.append(f"battery {t.battery}% (want {min_battery}+)")
        if require_switch and t.rc_mode_switch != require_switch:
            problems.append(f"RC mode switch is {t.rc_mode_switch or 'unknown'}, "
                            f"must be {require_switch}")
        s = self.note("preflight", "; ".join(problems) or "clear", not problems,
                      sats=t.gps_sats, battery=t.battery, switch=t.rc_mode_switch,
                      home_set=t.home_set, mode=t.flight_mode)
        self.log(f"preflight: {s.detail}")
        if problems:
            raise Abort("preflight: " + "; ".join(problems))
        await self.bc.set_limits(self.env.max_alt + 8, self.env.max_radius + 10)
        self.log(f"geofence set to {self.env.max_alt + 8:.0f} m / "
                 f"{self.env.max_radius + 10:.0f} m on the aircraft")
        return s

    async def takeoff_and_take_control(self, attempts: int = 3) -> None:
        """Get airborne and holding, or raise. Handles every refusal seen in the field."""
        t = self.bc.telemetry
        assert t is not None
        if t.is_flying:
            self.log(f"already airborne at {t.alt:.1f} m, taking it from here")
        elif t.motors_on:
            # The P3S leaves the motors turning after an auto-landing, and
            # startTakeoff is acked and ignored in that state.
            self.log("motors still armed on the ground; disarming before takeoff")
            await self.bc.stop_motors()
            await asyncio.sleep(3)

        for attempt in range(attempts):
            t = self.bc.telemetry
            assert t is not None
            if t.is_flying:
                break
            self.log("taking off" if attempt == 0 else f"takeoff retry {attempt}")
            await self.bc.takeoff()
            end = time.monotonic() + 20
            while time.monotonic() < end:
                await asyncio.sleep(0.5)
                self.sample()
                t = self.bc.telemetry
                if t and t.is_flying and t.alt > 0.8 and abs(t.vu) < 0.2:
                    break
            t = self.bc.telemetry
            assert t is not None
            if t.is_flying and t.alt > 0.5:
                self.log(f"takeoff settled at {t.alt:.1f} m")
                break
            self.log(f"takeoff did nothing (alt {t.alt:.2f} m); letting the aircraft settle")
            await asyncio.sleep(5)

        t = self.bc.telemetry
        assert t is not None
        if not t.is_flying:
            raise Abort(f"takeoff did not lift the aircraft after {attempts} attempts "
                        f"(alt {t.alt:.2f} m)")

        for attempt in range(6):
            try:
                await self.bc.enable_virtual_stick()
                break
            except BridgeError as e:
                if attempt == 5:
                    raise
                self.log(f"virtual stick not ready ({e}); retrying")
                await asyncio.sleep(2)
        self.took_control = True
        # Telemetry lags the SDK, so confirm before any check can read a stale
        # False and call it a pilot override.
        for _ in range(20):
            await asyncio.sleep(0.2)
            if self.bc.telemetry is not None and self.bc.telemetry.virtual_stick:
                break
        else:
            raise Abort("virtual stick enabled but the aircraft never confirmed it")
        self.log("virtual stick confirmed")
        await self.hold(2.0)
        self.zero_origin()
        t = self.bc.telemetry
        assert t is not None
        self.note("takeoff", f"hovering at {t.alt:.1f} m", t.is_flying, alt=round(t.alt, 2))
        self.log(f"airborne at {t.alt:.1f} m, origin zeroed")

    async def land_and_release(self) -> None:
        """Always runs, on every path. Land if that is safe, then let go regardless.

        The one case where landing is wrong is a pilot takeover: commanding a
        descent then would put a second pilot on the controls. Releasing virtual
        stick is never wrong, so it happens either way.
        """
        pilot_has_it = bool(self.aborted and "pilot took control" in self.aborted)
        if pilot_has_it:
            self.log("pilot is flying; standing down without commanding anything")
        else:
            try:
                t = self.bc.telemetry
                if t is not None and t.is_flying:
                    self.log("landing")
                    await self.bc.send_velocity(VelocityCommand())
                    await self.bc.land()
                    for _ in range(300):
                        await asyncio.sleep(0.2)
                        self.sample()
                        if self.bc.telemetry and not self.bc.telemetry.is_flying:
                            break
                    self.log("on the ground")
            except Exception as e:  # noqa: BLE001
                self.log(f"landing failed: {e}; TAKE THE STICKS")
        if not self.took_control:
            return
        try:
            await self.bc.disable_virtual_stick()
            self.took_control = False
            self.log("virtual stick released, the RC has control")
        except Exception as e:  # noqa: BLE001
            self.log(f"could not release virtual stick ({e}); FLIP S1 OUT OF F NOW")

    # ---- reporting ---------------------------------------------------------

    def summary(self) -> str:
        lines = ["=" * 62]
        for s in self.steps:
            mark = "ok  " if s.ok else ("FAIL" if s.ok is False else "--  ")
            lines.append(f"  {mark} {s.name:<14} {s.detail}")
        if self.aborted:
            lines.append(f"\n  ABORTED: {self.aborted}")
        ok = sum(1 for s in self.steps if s.ok)
        bad = sum(1 for s in self.steps if s.ok is False)
        lines.append(f"\n  {ok} passed, {bad} failed")
        if self.frames_saved:
            lines.append(f"  frames saved  {self.frames_saved} -> {self.capture_dir}")
        lines.append(f"  report        {self.report_path}")
        lines.append(f"  telemetry     {self.report_path.with_suffix('.jsonl')}")
        lines.append("=" * 62)
        return "\n".join(lines)
