"""Kinematic world: a flat field, trees with height, walking people, one drone.

The drone model is deliberately simple (first-order velocity response), which
matches how a DJI flight controller behaves under virtual stick velocity
commands well enough to test mission logic. It also reproduces the things
that actually bite in the field: watchdog hover, auto-takeoff height, RTH
behaviour, GPS noise, battery drain and a crash if you fly into a tree.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum

from .. import geo
from ..config import MissionConfig, SimPerson
from ..protocol import Telemetry, VelocityCommand, WATCHDOG_MS


class DroneMode(Enum):
    IDLE = "idle"            # on the ground, motors off
    TAKEOFF = "takeoff"      # auto takeoff climbing to 1.2 m
    FLYING = "flying"
    LANDING = "landing"
    RTH = "rth"
    CRASHED = "crashed"


@dataclass
class Person:
    cfg: SimPerson
    e: float
    n: float
    leg: int = 0
    paused_until: float = 0.0
    heading: float = 0.0

    def step(self, dt: float, now: float) -> None:
        path = self.cfg.path
        if not path or now < self.paused_until:
            return
        te, tn = path[self.leg % len(path)]
        d = math.hypot(te - self.e, tn - self.n)
        if d < 0.3:
            self.leg += 1
            if self.cfg.pause_s > 0:
                self.paused_until = now + self.cfg.pause_s
            return
        step = min(d, self.cfg.speed * dt)
        self.e += step * (te - self.e) / d
        self.n += step * (tn - self.n) / d
        self.heading = geo.bearing_deg(0, 0, te - self.e, tn - self.n)


@dataclass
class DroneState:
    e: float = 0.0
    n: float = 0.0
    alt: float = 0.0
    heading: float = 0.0
    vn: float = 0.0
    ve: float = 0.0
    vu: float = 0.0
    yaw_rate: float = 0.0
    battery: float = 100.0
    mode: DroneMode = DroneMode.IDLE
    vs_enabled: bool = False
    gimbal_pitch: float = 0.0
    gimbal_target: float = 0.0
    home_e: float = 0.0
    home_n: float = 0.0
    max_alt: float = 120.0
    max_radius: float = 500.0
    rth_alt: float = 30.0
    rth_stage: str = "climb"
    # last commanded velocity + when (wall clock: the real bridge's watchdog
    # runs on the phone's clock, not on simulated time)
    cmd: VelocityCommand = field(default_factory=VelocityCommand)
    cmd_wall: float = -1.0
    landing_confirm_needed: bool = False
    # --- behaviours measured on the real Phantom 3 Standard, 2026-09-25 ---
    # The aircraft keeps its motors turning for a while after an auto-landing,
    # and startTakeoff is acked and silently ignored for as long as it does.
    # See flights/2026-09-25-flight-01/BUGS.md bug 4.
    motors_armed: bool = False
    armed_until: float = 0.0
    # The RC mode switch. Virtual stick is only permitted in F. The pilot moving
    # this is the hardware override and it always wins.
    rc_switch: str = "F"
    recording: bool = False
    multi_mode: bool = False


@dataclass
class Episode:
    events: list[tuple[float, str, str]] = field(default_factory=list)
    crashed: bool = False
    left_field: bool = False
    max_alt: float = 0.0
    max_radius: float = 0.0
    distance_flown: float = 0.0
    watchdog_hovers: int = 0

    def add(self, t: float, name: str, detail: str = "") -> None:
        self.events.append((t, name, detail))


class World:
    TAU_H = 0.6         # s, horizontal velocity response
    TAU_V = 0.5
    TAU_YAW = 0.3
    TAKEOFF_ALT = 1.2
    LAND_SPEED = 0.6
    RTH_SPEED = 5.0
    PHONE_GPS_NOISE_M = 2.5     # a phone's GNSS in an open park, per axis

    def __init__(self, cfg: MissionConfig, seed: int | None = None, clock=time.monotonic):
        self.cfg = cfg
        self.sim = cfg.sim
        self.rng = random.Random(seed if seed is not None else cfg.sim.seed)
        self.clock = clock
        self.t = 0.0
        self.drone = DroneState(e=cfg.field.home[0], n=cfg.field.home[1],
                                home_e=cfg.field.home[0], home_n=cfg.field.home[1],
                                rth_alt=cfg.altitudes.rth)
        self.people = [Person(p, p.start[0], p.start[1]) for p in cfg.sim.people]
        self.trees = list(cfg.sim.trees)
        self.episode = Episode()
        self._last_watchdog_state = False
        self._phone_rng = random.Random((seed if seed is not None else cfg.sim.seed or 0) + 7919)

    # ---- commands -----------------------------------------------------------

    POST_LANDING_ARMED_S = 12.0     # measured: the motors keep turning this long

    def takeoff(self) -> tuple[bool, str | None]:
        d = self.drone
        if d.mode == DroneMode.CRASHED:
            return False, "aircraft crashed"
        if d.mode != DroneMode.IDLE:
            return False, "already flying"
        if d.motors_armed:
            # The real aircraft ACKS this and does nothing, which is exactly why
            # it cost a run on flight 01. Reproduce the ack, not an error.
            self.episode.add(self.t, "takeoff_ignored", "motors still armed")
            return True, None
        d.mode = DroneMode.TAKEOFF
        self.episode.add(self.t, "takeoff")
        return True, None

    def land(self) -> tuple[bool, str | None]:
        d = self.drone
        if d.mode not in (DroneMode.FLYING, DroneMode.RTH, DroneMode.TAKEOFF):
            return False, "not flying"
        d.mode = DroneMode.LANDING
        d.cmd = VelocityCommand()
        self.episode.add(self.t, "land")
        return True, None

    def go_home(self) -> tuple[bool, str | None]:
        d = self.drone
        if d.mode not in (DroneMode.FLYING, DroneMode.TAKEOFF):
            return False, "not flying"
        d.mode = DroneMode.RTH
        d.rth_stage = "climb"
        d.cmd = VelocityCommand()
        self.episode.add(self.t, "go_home")
        return True, None

    def cancel_go_home(self) -> tuple[bool, str | None]:
        if self.drone.mode == DroneMode.RTH:
            self.drone.mode = DroneMode.FLYING
            return True, None
        return False, "not returning home"

    def set_virtual_stick(self, on: bool) -> tuple[bool, str | None]:
        d = self.drone
        if on and d.rc_switch != "F":
            return False, f"virtual stick requires mode F (switch is {d.rc_switch})"
        if on and d.mode == DroneMode.TAKEOFF:
            # The real SDK refuses while auto-takeoff owns the aircraft.
            return False, "The aircraft is taking off"
        d.vs_enabled = on
        if not on:
            d.cmd = VelocityCommand()
        self.episode.add(self.t, "virtual_stick", "on" if on else "off")
        return True, None

    def set_rc_switch(self, letter: str) -> tuple[bool, str | None]:
        """The pilot's hardware override. Leaving F drops virtual stick at once."""
        d = self.drone
        d.rc_switch = letter
        if letter != "F" and d.vs_enabled:
            d.vs_enabled = False
            d.cmd = VelocityCommand()
            self.episode.add(self.t, "pilot_override", f"switch to {letter}")
        return True, None

    def stop_motors(self) -> tuple[bool, str | None]:
        d = self.drone
        d.motors_armed = False
        d.armed_until = 0.0
        d.vs_enabled = False
        d.cmd = VelocityCommand()
        if d.mode not in (DroneMode.IDLE, DroneMode.CRASHED):
            # In the air this is a drop, which is the whole point of the command.
            d.mode = DroneMode.CRASHED if d.alt > 0.5 else DroneMode.IDLE
        self.episode.add(self.t, "stop_motors")
        return True, None

    def set_recording(self, on: bool) -> tuple[bool, str | None]:
        if on and self.drone.recording:
            return False, "Camera is busy or the command is not supported in the Camera's current state"
        self.drone.recording = on
        return True, None

    def set_multi_mode(self) -> tuple[bool, str | None]:
        self.drone.multi_mode = True
        return True, None

    def set_limits(self, max_alt: float, max_radius: float) -> tuple[bool, str | None]:
        self.drone.max_alt = float(max_alt)
        self.drone.max_radius = float(max_radius)
        return True, None

    def set_gimbal(self, pitch: float) -> tuple[bool, str | None]:
        self.drone.gimbal_target = max(-90.0, min(0.0, float(pitch)))
        return True, None

    def velocity(self, v: VelocityCommand) -> None:
        d = self.drone
        if not d.vs_enabled or d.mode != DroneMode.FLYING:
            return
        d.cmd = v
        d.cmd_wall = self.clock()

    # ---- physics --------------------------------------------------------------

    def step(self, dt: float) -> None:
        self.t += dt
        d = self.drone
        for p in self.people:
            p.step(dt, self.t)

        # gimbal slews at ~90 deg/s
        dg = d.gimbal_target - d.gimbal_pitch
        d.gimbal_pitch += max(-90 * dt, min(90 * dt, dg))

        if d.motors_armed and self.t >= d.armed_until:
            d.motors_armed = False

        if d.mode == DroneMode.IDLE or d.mode == DroneMode.CRASHED:
            d.vn = d.ve = d.vu = d.yaw_rate = 0.0
            return

        target = VelocityCommand()
        if d.mode == DroneMode.TAKEOFF:
            target = VelocityCommand(0, 0, 1.0 if d.alt < self.TAKEOFF_ALT else 0.0, 0)
            if d.alt >= self.TAKEOFF_ALT - 0.05:
                d.mode = DroneMode.FLYING
                self.episode.add(self.t, "takeoff_complete")
        elif d.mode == DroneMode.FLYING:
            stale = d.cmd_wall < 0 or (self.clock() - d.cmd_wall) * 1000.0 > WATCHDOG_MS
            if d.vs_enabled and not stale:
                target = d.cmd
                if self._last_watchdog_state:
                    self._last_watchdog_state = False
            else:
                target = VelocityCommand()
                if d.vs_enabled and not self._last_watchdog_state and d.cmd_wall >= 0:
                    self.episode.watchdog_hovers += 1
                    self.episode.add(self.t, "watchdog_hover")
                    self._last_watchdog_state = True
        elif d.mode == DroneMode.LANDING:
            target = VelocityCommand(0, 0, -self.LAND_SPEED, 0)
            if d.alt <= 0.02:
                d.alt = 0.0
                d.mode = DroneMode.IDLE
                d.vn = d.ve = d.vu = 0.0
                # The motors keep turning after touchdown, and takeoff is a
                # no-op until they stop. Measured on the real aircraft.
                d.motors_armed = True
                d.armed_until = self.t + self.POST_LANDING_ARMED_S
                self.episode.add(self.t, "landed")
                return
        elif d.mode == DroneMode.RTH:
            target = self._rth_target()

        # limits imposed by the "flight controller"
        r = math.hypot(d.e - d.home_e, d.n - d.home_n)
        vn, ve, vu, yr = target.vn, target.ve, target.vu, target.yaw_rate
        if d.alt >= d.max_alt and vu > 0:
            vu = 0.0
        if r >= d.max_radius:
            # only allow inward motion
            ie, in_ = (d.home_e - d.e) / r, (d.home_n - d.n) / r
            inward = vn * in_ + ve * ie
            if inward < 0:
                vn -= inward * in_
                ve -= inward * ie
        # first-order response
        d.vn += (vn - d.vn) * min(1.0, dt / self.TAU_H)
        d.ve += (ve - d.ve) * min(1.0, dt / self.TAU_H)
        d.vu += (vu - d.vu) * min(1.0, dt / self.TAU_V)
        d.yaw_rate += (yr - d.yaw_rate) * min(1.0, dt / self.TAU_YAW)
        we, wn = self.sim.wind_en
        de = (d.ve + 0.15 * we) * dt
        dn = (d.vn + 0.15 * wn) * dt
        d.e += de
        d.n += dn
        d.alt = max(0.0, d.alt + d.vu * dt)
        d.heading = (d.heading + d.yaw_rate * dt) % 360.0
        d.battery = max(0.0, d.battery - self.sim.battery_drain_pct_per_s * dt)
        self.episode.distance_flown += math.hypot(de, dn)
        self.episode.max_alt = max(self.episode.max_alt, d.alt)
        self.episode.max_radius = max(self.episode.max_radius, r)

        # collisions
        for (te, tn, tr, th) in self.trees:
            if d.alt < th and math.hypot(d.e - te, d.n - tn) < tr:
                d.mode = DroneMode.CRASHED
                d.vn = d.ve = d.vu = 0.0
                self.episode.crashed = True
                self.episode.add(self.t, "crash", f"tree at ({te:.0f},{tn:.0f}) h={th:.0f}")
                return
        if not self.episode.left_field and not geo.point_in_polygon(d.e, d.n, self.cfg.field.polygon):
            if math.hypot(d.e - d.home_e, d.n - d.home_n) > 3.0:
                self.episode.left_field = True
                self.episode.add(self.t, "left_field", f"({d.e:.0f},{d.n:.0f})")
        if d.alt <= 0.0 and d.mode == DroneMode.FLYING and self.t > 5:
            # touched the ground while "flying": treat as a hard landing
            d.mode = DroneMode.IDLE
            self.episode.add(self.t, "hard_landing")

    def _rth_target(self) -> VelocityCommand:
        """DJI-style RTH: climb to RTH altitude, cruise home, descend holding position."""
        d = self.drone
        r = math.hypot(d.e - d.home_e, d.n - d.home_n)
        hn = (d.home_n - d.n) / r if r > 1e-6 else 0.0
        he = (d.home_e - d.e) / r if r > 1e-6 else 0.0
        if d.rth_stage == "climb":
            if d.alt >= d.rth_alt - 0.5 or r < 2.0:
                d.rth_stage = "cruise"
            return VelocityCommand(0, 0, 1.5, 0)
        if d.rth_stage == "cruise":
            if r < 1.5:
                d.rth_stage = "descend"
            v = min(self.RTH_SPEED, max(0.5, r))
            return VelocityCommand(v * hn, v * he, 0, 0)
        # descend, correcting drift so it lands on the pad
        if d.alt > 0.05:
            corr = min(1.0, r)
            return VelocityCommand(corr * hn, corr * he, -self.LAND_SPEED, 0)
        d.mode = DroneMode.IDLE
        d.alt = 0.0
        d.rth_stage = "climb"
        self.episode.add(self.t, "rth_landed")
        return VelocityCommand()

    # ---- telemetry ----------------------------------------------------------

    def telemetry(self) -> Telemetry:
        d = self.drone
        o = self.cfg.field.origin
        ne = self.rng.gauss(0, self.sim.gps_noise_m)
        nn = self.rng.gauss(0, self.sim.gps_noise_m)
        lat, lon = o.to_latlon(d.e + ne, d.n + nn)
        hlat, hlon = o.to_latlon(d.home_e, d.home_n)
        flying = d.mode in (DroneMode.TAKEOFF, DroneMode.FLYING, DroneMode.LANDING, DroneMode.RTH)
        # The phone travels with the first person, who in the field is the pilot
        # holding the RC. Its own generator, so adding it leaves every existing
        # seeded sequence (and therefore every seeded bench result) untouched.
        plat = plon = None
        if self.people:
            p = self.people[0]
            plat, plon = o.to_latlon(p.e + self._phone_rng.gauss(0, self.PHONE_GPS_NOISE_M),
                                     p.n + self._phone_rng.gauss(0, self.PHONE_GPS_NOISE_M))
        return Telemetry(
            t=self.t, lat=lat, lon=lon, alt=d.alt + self.rng.gauss(0, 0.1), heading=d.heading,
            vn=d.vn, ve=d.ve, vu=d.vu, battery=int(d.battery), gps_sats=14,
            is_flying=flying, motors_on=flying or d.motors_armed, virtual_stick=d.vs_enabled,
            flight_mode=d.mode.value, rc_mode_switch=d.rc_switch,
            home_lat=hlat, home_lon=hlon, home_set=True, gimbal_pitch=d.gimbal_pitch,
            phone_lat=plat, phone_lon=plon,
            phone_accuracy_m=self.PHONE_GPS_NOISE_M * 2 if plat is not None else None,
            phone_fix_age_s=0.5 if plat is not None else None,
        )
