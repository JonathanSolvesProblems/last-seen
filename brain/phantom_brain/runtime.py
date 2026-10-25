"""The mission runtime: what the aircraft can do, with perception running the whole time.

The agent decides WHAT to do at decision rate (seconds per choice). This module
does it at frame rate: every frame that arrives while the aircraft is turning,
transiting or following goes through the detector and the matcher, and every
person seen becomes, or updates, a Candidate. The agent never sees raw frames
or sends velocities; it sees candidates and chooses among actions.

Three properties the rest of the system relies on:

  * The velocity stream never stops. Every primitive, and `hover_while` around
    the agent's own thinking, keeps commands going faster than the aircraft's
    500 ms watchdog. A pause to think must not become an uncommanded hover
    mid-manoeuvre, and it must not become an abort.
  * Nothing here can widen the envelope. Ceiling, fence, speed and battery
    belong to FlightSession; this module only asks for things inside them, and
    clamps its own targets to the search area before asking.
  * Following needs a person's confirmation. `follow` raises unless the
    candidate's status is "confirmed", and only the coordinator channel can set
    that. The agent has no tool that confirms.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import cv2
import numpy as np

from . import geo
from .camera import CameraModel
from .control import altitude_hold, toward, yaw_toward
from .detect import Detection
from .pilot import FlightSession
from .protocol import VelocityCommand
from .reid import ASK_AT, LOOK_AT
from .visual_servo import (ChaseController, ServoConfig, TargetLock, chase_command,
                           gimbal_step, search_yaw_rate)

ASSOCIATE_M = 5.0          # same person if the ground estimates are this close...
WALK_MS = 1.5              # ...plus how far a person can have walked since last seen,
REACH_CAP_M = 12.0         # ...but never more than this. Uncapped, the reach grew to 35 m and
                           # merged me into the decoy hiker's candidate (rehearsal, 2026-10-20)
ASSOCIATE_S = 60.0         # beyond this long unseen, a match by position means nothing
IDENTITY_GAP = 0.35        # a sighting whose match differs this much from a candidate's history
                           # is a different person, wherever it projects to
MIN_SIGHTINGS = 3          # one frame is a detector blip, not a person
NEAR_CONFIRMED_M = 6.0     # a blip this close to a confirmed person is them
MIN_BOX_PX = 14            # below this a crop is too small to say anything about
# Hysteresis for an established follow: seeding a lock still needs LOOK_AT, but the
# running identity only drops below this. Flight 03: dark clothes seen from above
# swung per-frame scores enough that 0.30 dropped the confirmed person 7 times in
# 90 s; a stranger (~0.13) still falls under 0.20 within about a second.
FOLLOW_DROP_AT = 0.20


def _truth_label(det: Detection, truth: dict) -> str | None:
    """SIMULATOR ONLY: which simulated person a real detector's box is, by overlap
    with the renderer's truth boxes. Used to grade rehearsals and to let the
    simulated coordinator answer; never shown to the agent."""
    best, best_iou = None, 0.3
    for p in truth.get("people", []):
        if not p.get("visible"):
            continue
        x1, y1, x2, y2 = p["bbox"]
        ix = max(0.0, min(x2, det.x2) - max(x1, det.x1))
        iy = max(0.0, min(y2, det.y2) - max(y1, det.y1))
        inter = ix * iy
        union = (x2 - x1) * (y2 - y1) + det.w * det.h - inter
        if union > 0 and inter / union > best_iou:
            best, best_iou = p["id"], inter / union
    return best or "none"


class NotConfirmed(Exception):
    """Raised when anything tries to follow a person a human has not confirmed."""


@dataclass
class Candidate:
    id: str
    first_t: float
    last_t: float
    seen: int = 0
    mean: float = 0.0                 # running mean of the fused match: the candidate's identity
    best: float = 0.0                 # best fused match score so far
    last: float = 0.0
    appearance: float | None = None   # network cosine at the best view
    clothing: float = 0.0
    box_h: float = 0.0                # pixel height at the best view
    e: float | None = None            # estimated ground position, metres east/north of home
    n: float | None = None
    crop: str | None = None           # path of the best crop on disk
    status: str = "unreviewed"        # unreviewed | confirmed | rejected
    looks: int = 0                    # how many times the agent has gone closer
    truth_id: str | None = None       # simulator only; never shown to the agent

    def band(self) -> str:
        if self.best >= ASK_AT:
            return "strong"
        if self.best >= LOOK_AT:
            return "ambiguous"
        return "weak"

    def summary(self, here_e: float, here_n: float) -> dict:
        """What the agent sees. No truth_id: the agent must not see the answer."""
        d = None if self.e is None else round(math.hypot(self.e - here_e, self.n - here_n), 1)
        b = None if self.e is None else round(geo.bearing_deg(here_e, here_n, self.e, self.n))
        return {"id": self.id, "match": round(self.best, 2), "match_band": self.band(),
                "appearance": None if self.appearance is None else round(self.appearance, 2),
                "clothing": round(self.clothing, 2), "box_height_px": round(self.box_h),
                "times_seen": self.seen, "distance_m": d, "bearing_deg": b,
                "status": self.status, "closer_looks": self.looks}


@dataclass
class Pin:
    """One reported position of the person being followed, with the grader beside it."""
    t: float
    e: float
    n: float
    phone_e: float | None = None      # where the phone actually was, from its own GNSS
    phone_n: float | None = None
    phone_accuracy_m: float | None = None

    @property
    def error_m(self) -> float | None:
        if self.phone_e is None:
            return None
        return math.hypot(self.e - self.phone_e, self.n - self.phone_n)


class MissionRuntime:
    def __init__(self, bc, fs: FlightSession, *, detector, matcher, cam: CameraModel,
                 area: list[tuple[float, float]], alt: float, out_dir: Path,
                 speed: float = 2.0, gimbal_offset_deg: float = 0.0,
                 search_pitch_deg: float = -35.0):
        self.bc, self.fs = bc, fs
        self.detector, self.matcher = detector, matcher
        self.cam = cam
        self.area = area
        self.alt = alt
        self.speed = speed
        # Measured by camera_truth.py: what to ADD to the reported gimbal angle
        # to get the real one. Flight 02 suggests this is not zero.
        self.gimbal_offset = gimbal_offset_deg
        self.search_pitch = search_pitch_deg
        self.out = out_dir
        (out_dir / "crops").mkdir(parents=True, exist_ok=True)
        self.candidates: dict[str, Candidate] = {}
        self.pins: list[Pin] = []
        self.frames = 0
        self._next_id = 1
        self._log = open(out_dir / "perception.jsonl", "a", encoding="utf-8")
        self._pitch_cmd: float | None = None
        # Mission control: a status post at most once a second, fire-and-forget.
        # `status_sink(payload) -> {"recall": ...}` is awaited on its own task;
        # the flying never waits for the page.
        self.status_sink: Callable[[dict], Awaitable[dict | None]] | None = None
        self.recalled = False
        self._status_t = 0.0
        self._status_task: asyncio.Task | None = None

    # ---- where we are ---------------------------------------------------------

    def here(self) -> tuple[float, float]:
        t = self.bc.telemetry
        return self.fs.origin.to_local(t.lat, t.lon)

    def inside_area(self, e: float, n: float) -> bool:
        return geo.point_in_polygon(e, n, self.area)

    def live(self) -> list[Candidate]:
        """Candidates worth anyone's attention: seen more than once, or already
        decided about. A single-frame detection is a blip and goes nowhere."""
        return [c for c in self.candidates.values() if c.seen >= MIN_SIGHTINGS or c.status != "unreviewed"]

    def snapshot(self) -> list[dict]:
        e, n = self.here()
        return [c.summary(e, n) for c in sorted(self.live(), key=lambda c: -c.best)]

    # ---- the heartbeat --------------------------------------------------------

    async def _send(self, cmd: VelocityCommand) -> None:
        self.fs.check(airborne=True)
        await self.bc.send_velocity(self.fs.clamp(cmd))
        self.fs.sample()
        self._maybe_status()

    def status_payload(self) -> dict:
        t = self.bc.telemetry
        e, n = self.here()
        cands = []
        confirmed = [c for c in self.live() if c.status == "confirmed" and c.e is not None]
        for c in self.live():
            d = c.summary(e, n)
            d.update({"e": None if c.e is None else round(c.e, 1), "n": None if c.n is None else round(c.n, 1)})
            # Once someone is confirmed, an unreviewed blip a few metres from them
            # is almost always a fragment of the same person (a frame projected
            # while the camera was moving). Flagged, never deleted: the agent
            # still sees it; the coordinator's map leaves it out.
            d["near_confirmed"] = bool(c.status == "unreviewed" and c.e is not None and any(
                math.hypot(c.e - k.e, c.n - k.n) < NEAR_CONFIRMED_M for k in confirmed))
            cands.append(d)
        return {"area": [list(p) for p in self.area], "battery": t.battery,
                "drone": {"e": round(e, 1), "n": round(n, 1), "alt": round(t.alt, 1), "heading": round(t.heading)},
                "candidates": sorted(cands, key=lambda c: -c["match"])}

    def _maybe_status(self) -> None:
        if self.status_sink is None or time.monotonic() - self._status_t < 1.0:
            return
        if self._status_task is not None and not self._status_task.done():
            return
        self._status_t = time.monotonic()
        self._status_task = asyncio.ensure_future(self._post_status())

    async def _post_status(self) -> None:
        try:
            r = await self.status_sink(self.status_payload())
            if r and r.get("recall"):
                if not self.recalled:
                    self.fs.log("the coordinator asked for the aircraft back")
                self.recalled = True
        except Exception:  # noqa: BLE001
            pass                                   # a missing map update never touches the flying

    def _hold_alt(self) -> float:
        return altitude_hold(self.alt, self.bc.telemetry.alt)

    async def hover_while(self, work: Awaitable[Any]) -> Any:
        """Await something slow (the agent thinking, a person deciding) while the
        aircraft holds position and keeps looking. Perception keeps running, so
        a candidate who walks into view during a long think is not missed."""
        task = asyncio.ensure_future(work)
        while not task.done():
            await self._send(VelocityCommand(0, 0, self._hold_alt(), 0))
            self.perceive()
            await asyncio.sleep(0.1)
        return task.result()

    async def set_pitch(self, pitch: float) -> None:
        if self._pitch_cmd is None or abs(pitch - self._pitch_cmd) > 0.5:
            self._pitch_cmd = pitch
            try:
                await self.bc.set_gimbal_pitch(pitch)
            except Exception as e:  # noqa: BLE001
                self.fs.log(f"gimbal refused {pitch:.0f} ({e})")

    # ---- perception ---------------------------------------------------------------

    def perceive(self) -> list[tuple[Detection, Candidate]]:
        """Run detection and matching on the newest frame, if there is one."""
        fr = self.bc.new_frame()
        if fr is None:
            return []
        img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return []
        self.frames += 1
        tel = self.bc.telemetry_at(fr.t) or self.bc.telemetry
        h, w = img.shape[:2]
        cam = self.cam if (self.cam.width, self.cam.height) == (w, h) else self.cam.scaled(w, h)
        de, dn = self.fs.origin.to_local(tel.lat, tel.lon)
        pitch = (fr.gimbal_pitch if fr.gimbal_pitch else tel.gimbal_pitch) + self.gimbal_offset
        out = []
        for det in self.detector.detect(img, fr.truth):
            if det.h < MIN_BOX_PX:
                continue
            if det.truth_id is None and fr.truth:
                det.truth_id = _truth_label(det, fr.truth)
            crop = det.crop(img)
            if hasattr(self.matcher, "bind"):
                self.matcher.bind(det)
            if hasattr(self.matcher, "explain"):
                s = self.matcher.explain(crop)
                fused, app, clo = s.fused, s.appearance, s.clothing
            else:
                fused, app, clo = self.matcher.score(crop), None, 0.0
            u, v = det.feet
            g = cam.pixel_to_ground(u, v, de, dn, tel.alt, pitch, tel.heading)
            if g is not None and math.hypot(g[0] - de, g[1] - dn) > 6 * max(tel.alt, 1.0) + 40:
                g = None                                   # a near-horizon ray, meaningless
            c = self._associate(g, fr.t, fused)
            c.mean = fused if c.seen == 0 else 0.8 * c.mean + 0.2 * fused
            c.seen += 1
            c.last_t = fr.t
            c.last = fused
            if g is not None:
                if c.e is None:
                    c.e, c.n = g
                else:
                    c.e, c.n = 0.6 * c.e + 0.4 * g[0], 0.6 * c.n + 0.4 * g[1]
            if fused >= c.best:
                c.best, c.appearance, c.clothing, c.box_h = fused, app, clo, det.h
                path = self.out / "crops" / f"{c.id}.jpg"
                cv2.imwrite(str(path), crop)
                c.crop = str(path)
            if det.truth_id is not None:
                c.truth_id = det.truth_id
            self._log.write(json.dumps({"t": fr.t, "seq": fr.seq, "cand": c.id, "fused": round(fused, 3),
                                        "appearance": None if app is None else round(app, 3),
                                        "box_h": round(det.h, 1), "ground": None if g is None else [round(g[0], 2), round(g[1], 2)],
                                        "pitch_used": round(pitch, 1), "alt": round(tel.alt, 2)}) + "\n")
            out.append((det, c))
        self._log.flush()
        return out

    def _associate(self, g: tuple[float, float] | None, t: float, fused: float) -> Candidate:
        best, best_d = None, 1.0
        if g is not None:
            for c in self.candidates.values():
                if c.e is None or t - c.last_t > ASSOCIATE_S:
                    continue
                # Identity first: never merge a sighting into someone whose match
                # history disagrees with it. Position alone merged me into the
                # hiker, and the coordinator would have confirmed MY crop and the
                # aircraft gone to HIS position.
                if c.seen >= 2 and abs(fused - c.mean) > IDENTITY_GAP:
                    continue
                d = math.hypot(c.e - g[0], c.n - g[1])
                # Normalised by how far they could have walked, capped.
                reach = min(REACH_CAP_M, ASSOCIATE_M + WALK_MS * max(0.0, t - c.last_t))
                if d < reach and d / reach < best_d:
                    best, best_d = c, d / reach
        if best is not None:
            return best
        cid = f"P{self._next_id}"
        self._next_id += 1
        c = Candidate(cid, t, t)
        self.candidates[cid] = c
        return c

    # ---- the four things the aircraft can do -----------------------------------

    async def scan_here(self, degrees: float = 360.0, yaw_rate: float = 20.0) -> dict:
        """Turn on the spot, looking."""
        await self.set_pitch(self.search_pitch)
        before = {c.id for c in self.live()}
        t_end = time.monotonic() + degrees / max(abs(yaw_rate), 1e-3)
        while time.monotonic() < t_end:
            await self._send(VelocityCommand(0, 0, self._hold_alt(), yaw_rate))
            self.perceive()
            await asyncio.sleep(0.05)
        return {"new_candidates": sorted({c.id for c in self.live()} - before), "candidates": self.snapshot()}

    async def fly_to(self, e: float, n: float, timeout: float = 60.0) -> dict:
        """Transit to a point inside the search area, camera forward, looking."""
        if not self.inside_area(e, n):
            raise ValueError(f"({e:.0f} E, {n:.0f} N) is outside the search area")
        await self.set_pitch(self.search_pitch)
        before = {c.id for c in self.live()}
        t_end = time.monotonic() + timeout
        arrived = False
        while time.monotonic() < t_end:
            here_e, here_n = self.here()
            tel = self.bc.telemetry
            vn, ve, d = toward(e, n, here_e, here_n, self.speed, 5.0, 1.0)
            if d <= 1.2:
                arrived = True
                break
            yaw = yaw_toward(geo.bearing_deg(here_e, here_n, e, n), tel.heading, kp=1.2, max_rate=30.0)
            await self._send(VelocityCommand(vn, ve, self._hold_alt(), yaw))
            self.perceive()
            await asyncio.sleep(0.05)
        here_e, here_n = self.here()
        return {"arrived": arrived, "at": {"east_m": round(here_e, 1), "north_m": round(here_n, 1)},
                "new_candidates": sorted({c.id for c in self.live()} - before), "candidates": self.snapshot()}

    async def look_closer(self, cid: str, standoff: float = 7.0, dwell: float = 5.0) -> dict:
        """Active perception: move to a better viewpoint on one candidate and look again."""
        c = self.candidates[cid]
        if c.e is None:
            raise ValueError(f"{cid} has no ground position yet; scan toward it first")
        before = c.best
        here_e, here_n = self.here()
        d = math.hypot(c.e - here_e, c.n - here_n)
        if d > standoff + 1.0:
            ux, uy = (here_e - c.e) / d, (here_n - c.n) / d
            te, tn = c.e + ux * standoff, c.n + uy * standoff
            if not self.inside_area(te, tn):
                te, tn = here_e, here_n             # cannot get closer without leaving the area
            await self.fly_to(te, tn, timeout=40.0)
        # Face them and dwell, with the camera aimed at the estimated position.
        t_end = time.monotonic() + dwell
        while time.monotonic() < t_end:
            here_e, here_n = self.here()
            tel = self.bc.telemetry
            yaw = yaw_toward(geo.bearing_deg(here_e, here_n, c.e, c.n), tel.heading, kp=1.5, max_rate=30.0)
            dist = max(1.0, math.hypot(c.e - here_e, c.n - here_n))
            await self.set_pitch(max(-80.0, -math.degrees(math.atan2(tel.alt, dist))))
            await self._send(VelocityCommand(0, 0, self._hold_alt(), yaw))
            self.perceive()
            await asyncio.sleep(0.05)
        c.looks += 1
        here_e, here_n = self.here()
        return {"candidate": c.summary(here_e, here_n), "match_before": round(before, 2),
                "match_after": round(c.best, 2), "candidates": self.snapshot()}

    async def follow(self, cid: str, seconds: float, leash: float,
                     on_pin: Callable[[Pin], Awaitable[None]] | None = None) -> dict:
        """Follow a CONFIRMED candidate by apparent size, reporting a pin each second.

        The pin's grader is the phone's own GNSS, recorded beside it."""
        c = self.candidates[cid]
        if c.status != "confirmed":
            raise NotConfirmed(f"{cid} is {c.status}; only a person the coordinator confirmed can be followed")
        cfg = ServoConfig(max_speed=self.speed, max_yaw_rate=25.0)
        chase = ChaseController(cfg)
        lock: TargetLock | None = None
        target_px: float | None = None
        sizing: list[float] = []
        last_offset: float | None = None
        hold = VelocityCommand(0, 0, self._hold_alt(), 0)
        last_pin = 0.0
        locked = frames = identity_drops = 0
        ident = 1.0                       # running identity score of whoever is locked
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            fr = self.bc.new_frame()
            if fr is None:
                await self._send(hold)
                await asyncio.sleep(0.03)
                continue
            img = cv2.imdecode(np.frombuffer(fr.jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            frames += 1
            tel = self.bc.telemetry_at(fr.t) or self.bc.telemetry
            h, w = img.shape[:2]
            dets = [d for d in self.detector.detect(img, fr.truth) if d.h >= MIN_BOX_PX]
            if lock is None or lock.last is None:
                # Never let the lock seed itself: TargetLock alone takes the
                # biggest person in frame. Seeding goes through the matcher.
                lock = TargetLock(w)
                # Seed the lock with the detection that best matches the photo,
                # not the biggest person in frame: following a stranger is the
                # failure this whole flow exists to prevent.
                if dets:
                    scored = []
                    for d in dets:
                        if hasattr(self.matcher, "bind"):
                            self.matcher.bind(d)
                        scored.append((self.matcher.score(d.crop(img)), d))
                    s, d0 = max(scored, key=lambda x: x[0])
                    if s >= LOOK_AT:
                        lock.last, lock.locked = d0, True
                        ident = s
                    else:
                        lock = None
            target = lock.update(dets) if lock is not None else None
            if target is not None:
                # The lock follows the nearest box, which is not the same thing as
                # the same person. Re-check identity on every locked frame; a
                # collapse means the lock has jumped to someone else, so drop it
                # and search, rather than follow a stranger.
                if hasattr(self.matcher, "bind"):
                    self.matcher.bind(target)
                ident = 0.7 * ident + 0.3 * self.matcher.score(target.crop(img))
                if ident < FOLLOW_DROP_AT:
                    identity_drops += 1
                    lock, target, ident = None, None, 1.0
            e, n = self.fs.origin.to_local(tel.lat, tel.lon)
            alt_vu = altitude_hold(self.alt, tel.alt)
            if target is None:
                cmd = VelocityCommand(0, 0, alt_vu, search_yaw_rate(last_offset, 15.0))
                chase.step(fr.t, None, None, target_px or 0.0)
            else:
                locked += 1
                offset = ((target.x1 + target.x2) / 2 - w / 2) / w
                last_offset = offset
                clipped = target.y1 <= 2 or target.y2 >= h - 2
                box_h = target.y2 - target.y1
                if target_px is None and not clipped and abs(offset) <= cfg.face_first:
                    sizing.append(box_h)
                    if len(sizing) >= 6:
                        target_px = float(np.median(sizing))
                servo = chase.step(fr.t, offset, box_h, target_px or box_h, clipped=clipped)
                cmd = chase_command(servo, tel.heading, alt_vu, e, n, leash)
                v_off = ((target.y1 + target.y2) / 2 - h / 2) / h
                now_pitch = fr.gimbal_pitch if fr.gimbal_pitch else tel.gimbal_pitch
                want = gimbal_step(v_off, now_pitch, cfg)
                if abs(want - (self._pitch_cmd or now_pitch)) >= 1.5:
                    self._pitch_cmd = want
                    asyncio.ensure_future(self._quiet_gimbal(want))   # never awaited in the loop
                # The pin: where the person's feet meet the ground.
                cam = self.cam if (self.cam.width, self.cam.height) == (w, h) else self.cam.scaled(w, h)
                g = cam.pixel_to_ground(*target.feet, e, n, tel.alt, now_pitch + self.gimbal_offset, tel.heading)
                if g is not None and fr.t - last_pin >= 1.0:
                    last_pin = fr.t
                    pin = Pin(fr.t, g[0], g[1])
                    fix = tel.phone_fix()
                    if fix is not None:
                        pin.phone_e, pin.phone_n = self.fs.origin.to_local(*fix)
                        pin.phone_accuracy_m = tel.phone_accuracy_m
                    self.pins.append(pin)
                    c.e, c.n = g
                    if on_pin is not None:
                        await on_pin(pin)
            hold = cmd
            await self._send(cmd)
        errs = [p.error_m for p in self.pins if p.error_m is not None]
        # Where they ended up and which way they were going: what the ground team
        # actually needs, and what the agent asked for in its first real run.
        moved = None
        mine = [p for p in self.pins if p.t >= self.pins[0].t] if self.pins else []
        if len(mine) >= 2:
            a, b = mine[max(0, len(mine) - 10)], mine[-1]
            dist = math.hypot(b.e - a.e, b.n - a.n)
            moved = {"last_east_m": round(b.e, 1), "last_north_m": round(b.n, 1),
                     "heading_deg": round(geo.bearing_deg(a.e, a.n, b.e, b.n)) if dist > 1.0 else None,
                     "speed_ms": round(dist / max(b.t - a.t, 1e-3), 2), "standing_still": dist <= 1.0}
        return {"followed_s": round(seconds), "frames": frames, "locked_frames": locked,
                "identity_drops": identity_drops,
                "locked_fraction": round(locked / max(frames, 1), 2), "pins": len(self.pins),
                "last_seen": moved,
                "pin_error_vs_phone_m": None if not errs else {
                    "median": round(float(np.median(errs)), 1), "max": round(max(errs), 1), "n": len(errs)}}

    async def _quiet_gimbal(self, pitch: float) -> None:
        try:
            await self.bc.set_gimbal_pitch(pitch)
        except Exception:  # noqa: BLE001
            pass

    def save(self) -> None:
        (self.out / "candidates.json").write_text(json.dumps(
            [asdict(c) for c in self.candidates.values()], indent=1), encoding="utf-8")
        (self.out / "pins.json").write_text(json.dumps(
            [{**asdict(p), "error_m": p.error_m} for p in self.pins], indent=1), encoding="utf-8")
