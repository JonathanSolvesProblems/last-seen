"""The mission: take off, map the field, search it, confirm the person, follow them, come home.

    INIT -> ARM -> TAKEOFF -> CLIMB -> SURVEY -> SEARCH <-> CONFIRM -> FOLLOW -> RTH -> DONE
                                          \\_______________________________________/  (abort)

Runs at loop_hz against a BridgeClient (phone or simulator). Every velocity
command passes through the SafetyMonitor. All numbers that end up in the
scoreboard are collected here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import cv2
import numpy as np

from . import geo
from .camera import CameraModel
from .client import BridgeClient, BridgeError
from .config import MissionConfig
from .control import Limits, altitude_hold, limit
from .detect import (ClaudeMatcher, ColorHistogramMatcher, Detection, IdentifierConfig,
                     OracleMatcher, TargetIdentifier, draw_detections, make_detector)
from .protocol import Frame, Telemetry, VelocityCommand
from .safety import Abort, Fence, SafetyMonitor
from .scoreboard import RunLogger
from .tasks.follow import FollowConfig, Follower
from .tasks.localize import VisualLocalizer
from .tasks.navigate import FollowerConfig, WaypointFollower, coverage_path, path_collides, plan
from .tasks.reconstruct import (GrassClassifier, OccupancyGrid, ingest_nadir_frame, lawnmower,
                                survey_spacing, truth_grid_from_circles)

log = logging.getLogger(__name__)


class Phase(Enum):
    INIT = "init"
    ARM = "arm"
    TAKEOFF = "takeoff"
    CLIMB = "climb"
    SURVEY = "survey"
    DESCEND = "descend"
    SEARCH = "search"
    CONFIRM = "confirm"
    FOLLOW = "follow"
    RTH = "rth"
    LAND = "land"
    DONE = "done"
    ABORTED = "aborted"


@dataclass
class MissionResult:
    success: bool = False
    phase: Phase = Phase.INIT
    abort: str = "none"
    reason: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    run_dir: str = ""


class Mission:
    # phases in which the brain is steering through virtual stick
    AUTONOMOUS = (Phase.CLIMB, Phase.SURVEY, Phase.DESCEND, Phase.SEARCH, Phase.CONFIRM, Phase.FOLLOW)

    def __init__(self, cfg: MissionConfig, client: BridgeClient, display: bool = False,
                 run_name: str | None = None, sim_truth: bool = False):
        self.cfg = cfg
        self.client = client
        self.display = display
        self.sim_truth = sim_truth
        self.origin = cfg.field.origin
        self.cam = CameraModel(cfg.sim.frame_w, cfg.sim.frame_h)
        fence = Fence(cfg.field.polygon, cfg.fence.max_radius_m, cfg.fence.max_alt_m,
                      cfg.fence.floor_alt_m, cfg.fence.home_radius_m, cfg.fence.margin_m)
        self.safety = SafetyMonitor(fence, self.origin)
        self.log = RunLogger(cfg.runs_dir, run_name)
        self.phase = Phase.INIT
        self.phase_t0 = 0.0
        self.phase_times: dict[str, float] = {}
        self.grid: OccupancyGrid | None = None
        self.classifier = GrassClassifier()
        self.localizer = VisualLocalizer(self.cam)
        self.identifier = self._build_identifier()
        self.follower = Follower(self.cam, self.origin, FollowConfig(
            standoff_m=cfg.follow.standoff_m, altitude_m=cfg.altitudes.follow,
            speed=cfg.follow.speed, lost_timeout_s=cfg.follow.lost_timeout_s))
        self.wp: WaypointFollower | None = None
        self.search_path: list[tuple[float, float]] = []
        self.search_started = 0.0
        self.search_elapsed = 0.0
        self.confirm_t0 = 0.0
        self.follow_t0 = 0.0
        self.t_confirmed: float | None = None
        self.time_to_confirm: float = 0.0
        self.confirmed_truth_id: str | None = None
        self.result = MissionResult()
        self.last_frame_img: np.ndarray | None = None
        self.last_dets: list[Detection] = []
        self.last_target: Detection | None = None
        self._localize_last = 0.0
        self._survey_cells = 0
        self._climb_target = 0.0
        self._last_pos: tuple[float, float] | None = None
        self._flown_m = 0.0
        self._last_gimbal_sent: float | None = None
        self._vs_lost = 0
        self._loops = 0

    # ---- setup -----------------------------------------------------------------------

    def _build_identifier(self) -> TargetIdentifier:
        p = self.cfg.perception
        ref = None
        if p.reference_photo:
            ref = cv2.imread(p.reference_photo)
            if ref is None:
                raise FileNotFoundError(f"reference photo not found: {p.reference_photo}")
        detector: Any = make_detector(p.detector, p.yolo_weights, p.yolo_imgsz, p.yolo_conf)
        if p.matcher == "claude":
            if ref is None:
                raise ValueError("matcher=claude needs perception.reference_photo")
            matcher: Any = ClaudeMatcher(ref)
        elif p.matcher == "color":
            if ref is None:
                raise ValueError("matcher=color needs perception.reference_photo")
            matcher = ColorHistogramMatcher(ref)
        else:
            matcher = OracleMatcher("target")
        return TargetIdentifier(detector, matcher, IdentifierConfig(
            match_threshold=p.match_threshold, confirm_frames=p.confirm_frames, lose_frames=p.lose_frames))

    # ---- helpers ------------------------------------------------------------------------

    def _set_phase(self, phase: Phase, t: float, why: str = "") -> None:
        if self.phase != Phase.INIT:
            self.phase_times[self.phase.value] = round(t - self.phase_t0, 1)
        log.info("phase %s -> %s %s", self.phase.value, phase.value, why)
        self.log.event("phase", frm=self.phase.value, to=phase.value, t=round(t, 2), why=why)
        self.phase = phase
        self.phase_t0 = t

    def _pos(self, tel: Telemetry) -> tuple[float, float]:
        return self.origin.to_local(tel.lat, tel.lon)

    async def _gimbal(self, pitch: float) -> None:
        if self._last_gimbal_sent is None or abs(self._last_gimbal_sent - pitch) > 0.5:
            try:
                await self.client.set_gimbal_pitch(pitch)
                self._last_gimbal_sent = pitch
            except BridgeError as e:
                log.warning("gimbal: %s", e)

    async def _send(self, cmd: VelocityCommand, tel: Telemetry) -> None:
        cmd = self.safety.filter(cmd, tel)
        await self.client.send_velocity(cmd)

    # ---- main loop ------------------------------------------------------------------------

    async def run(self) -> MissionResult:
        cfg = self.cfg
        period = 1.0 / cfg.loop_hz
        wall_start = time.monotonic()
        tel = await self.client.wait_for_telemetry(30.0)
        t_start = tel.t
        try:
            while True:
                loop_t0 = time.monotonic()
                self._loops += 1
                tel = self.client.telemetry or tel
                t = tel.t
                if self._last_pos is not None:
                    self._flown_m += geo.dist(self._last_pos, self._pos(tel))
                self._last_pos = self._pos(tel)
                self.log.telemetry({"phase": self.phase.value, **tel.__dict__})

                # pilot override: virtual stick gone while we thought we were flying it
                # (S1 flipped out of F, STOP pressed, or the FC dropped the mode). Never
                # re-enable it ourselves; end the run and hand over.
                if self.phase in self.AUTONOMOUS and not tel.virtual_stick:
                    self._vs_lost += 1
                    if self._vs_lost >= 5:      # ~0.5 s of consistent readings
                        self.result.reason = "pilot override (virtual stick ended)"
                        self.log.event("pilot_override", phase=self.phase.value, t=round(t, 2))
                        self._set_phase(Phase.ABORTED, t, "pilot override")
                        break
                else:
                    self._vs_lost = 0

                # safety first
                abort = self.safety.check(tel, now=t, rx_age_s=time.monotonic() - self.client.last_rx)
                if abort != Abort.NONE and self.phase not in (Phase.RTH, Phase.LAND, Phase.DONE, Phase.ABORTED,
                                                                Phase.INIT, Phase.ARM):
                    self.result.abort = abort.value
                    self.result.reason = ",".join(self.safety.state.violations[-3:])
                    self.log.event("abort", kind=abort.value, violations=self.safety.state.violations[-5:])
                    await self._begin_rth(tel, land_now=(abort == Abort.LAND_NOW))
                if t - t_start > cfg.max_mission_s and self.phase not in (Phase.RTH, Phase.LAND, Phase.DONE, Phase.ABORTED):
                    self.result.reason = "mission timeout"
                    await self._begin_rth(tel)

                frame = self.client.new_frame()
                if frame is not None:
                    self._on_frame(frame, tel)

                await self._step_phase(tel, t)

                if self.display and self.last_frame_img is not None:
                    self._show(tel)

                if self.phase in (Phase.DONE, Phase.ABORTED):
                    break
                elapsed = time.monotonic() - loop_t0
                await asyncio.sleep(max(0.0, period - elapsed))
        except Exception as e:  # noqa: BLE001
            log.exception("mission loop error: %s", e)
            self.result.reason = f"exception: {e}"
            with_tel = self.client.telemetry
            try:
                if with_tel and with_tel.is_flying:
                    await self.client.hover()
                    await self.client.go_home()
            except Exception:  # noqa: BLE001
                pass
            self.phase = Phase.ABORTED
        finally:
            try:
                await self.client.disable_virtual_stick()
            except Exception:  # noqa: BLE001
                pass
            self.result.phase = self.phase
            self.result.summary = self._summary(time.monotonic() - wall_start)
            self.result.run_dir = str(self.log.dir)
            self.log.summary(self.result.summary)
            self.log.close()
            if self.display:
                cv2.destroyAllWindows()
        return self.result

    # ---- frames -------------------------------------------------------------------------------

    def _on_frame(self, frame: Frame, tel_now: Telemetry) -> None:
        img = cv2.imdecode(np.frombuffer(frame.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return
        self.last_frame_img = img
        h, w = img.shape[:2]
        if (self.cam.width, self.cam.height) != (w, h):
            self.cam = CameraModel(w, h)
            self.localizer.cam = self.cam
            self.follower.cam = self.cam
        # use the telemetry sample from when the frame was captured, not now
        tel = self.client.telemetry_at(frame.t) or tel_now
        gp = frame.gimbal_pitch if frame.gimbal_pitch != 0.0 else tel.gimbal_pitch
        e, n = self._pos(tel)

        if self.phase == Phase.SURVEY and self.grid is not None:
            self._survey_cells += ingest_nadir_frame(self.grid, img, self.cam, e, n, tel.alt, tel.heading,
                                                     gp, self.classifier)
            self.localizer.maybe_add_keyframe(img, e, n, tel.alt, tel.heading, gp)

        if self.phase in (Phase.SEARCH, Phase.SURVEY, Phase.DESCEND) and frame.t - self._localize_last > 1.0 \
                and gp <= -80 and self.localizer.keyframes:
            self._localize_last = frame.t
            self.localizer.estimate(img, tel.alt, tel.heading, gp, e, n)

        if self.phase in (Phase.SEARCH, Phase.CONFIRM, Phase.FOLLOW):
            truth = frame.truth if self.sim_truth else None
            target, dets = self.identifier.update(img, frame.t, truth)
            self.last_dets, self.last_target = dets, target
            if target is not None and self.t_confirmed is None:
                self.t_confirmed = frame.t
                self.time_to_confirm = max(0.0, frame.t - self.search_started)
                self.confirmed_truth_id = target.truth_id
                self.log.event("confirmed", t=round(frame.t, 2), match=target.match, truth_id=target.truth_id,
                               bbox=[target.x1, target.y1, target.x2, target.y2])
                self.log.frame("first_confirmation", draw_detections(img, dets, target))
            if self.phase == Phase.FOLLOW:
                self.follower.observe(target, tel, w, h, gp, frame.t)

    # ---- phases ---------------------------------------------------------------------------------

    async def _step_phase(self, tel: Telemetry, t: float) -> None:
        cfg = self.cfg
        ph = self.phase
        if ph == Phase.INIT:
            if (tel.gps_sats < self.safety.config.min_gps_sats or not tel.gps_fix) and not self.sim_truth:
                if t - self.phase_t0 > 60:
                    raise BridgeError("no GPS lock after 60 s")
                return
            await self.client.set_limits(cfg.fence.max_alt_m, cfg.fence.max_radius_m)
            self.log.event("limits", max_alt=cfg.fence.max_alt_m, max_radius=cfg.fence.max_radius_m)
            await self._gimbal(-90.0)
            self._set_phase(Phase.ARM, t)
            return
        if ph == Phase.ARM:
            # Takeoff is a DJI "mission"; virtual stick is enabled once airborne.
            self._set_phase(Phase.TAKEOFF, t)
            await self.client.takeoff()
            return
        if ph == Phase.TAKEOFF:
            # Wait for the climb to STOP, not merely to start. Auto-takeoff owns
            # the aircraft until it settles and the SDK refuses virtual stick
            # for the whole of it ("The aircraft is taking off"), which is what
            # aborted flight 01's first takeoff. `is_flying` goes true the
            # instant the motors spin, so it is not enough on its own.
            settled = tel.is_flying and tel.alt >= 1.0 and abs(tel.vu) < 0.2
            if settled:
                try:
                    await self.client.enable_virtual_stick()
                except BridgeError as e:
                    # Still climbing as far as the aircraft is concerned. Try
                    # again next tick rather than failing the mission.
                    if "taking off" in str(e).lower():
                        return
                    raise
                self.log.event("virtual_stick", on=True)
                self._climb_target = cfg.altitudes.survey if (cfg.survey.enabled and cfg.survey.map_path is None) \
                    else cfg.altitudes.search
                self._set_phase(Phase.CLIMB, t)
            elif t - self.phase_t0 > 45:
                raise BridgeError("takeoff did not complete")
            return
        if ph == Phase.CLIMB:
            vu = altitude_hold(self._climb_target, tel.alt, kp=1.0, max_v=2.0)
            await self._send(VelocityCommand(0, 0, vu, 0), tel)
            if abs(tel.alt - self._climb_target) < 1.0:
                if cfg.survey.enabled and cfg.survey.map_path is None:
                    self._begin_survey(tel, t)
                else:
                    self._load_or_skip_map()
                    self._begin_search(tel, t)
            return
        if ph == Phase.SURVEY:
            assert self.wp is not None
            await self._gimbal(-90.0)
            await self._send(self.wp.step(tel), tel)
            if self.wp.done:
                self._finish_survey(t)
                self._climb_target = cfg.altitudes.search
                self._set_phase(Phase.DESCEND, t)
            return
        if ph == Phase.DESCEND:
            vu = altitude_hold(self._climb_target, tel.alt, kp=1.0, max_v=1.5)
            await self._send(VelocityCommand(0, 0, vu, 0), tel)
            if abs(tel.alt - self._climb_target) < 1.0:
                self._begin_search(tel, t)
            return
        if ph == Phase.SEARCH:
            assert self.wp is not None
            await self._gimbal(cfg.search.gimbal_pitch)
            self.search_elapsed = t - self.search_started
            if self.last_target is not None:
                await self._start_follow(tel, t)
                return
            if self.identifier.state.hits > 0:
                self.confirm_t0 = t
                self._set_phase(Phase.CONFIRM, t, "candidate")
                await self._send(VelocityCommand(0, 0, 0, 0), tel)
                return
            if self.search_elapsed > cfg.search.max_time_s:
                self.result.reason = "search timeout"
                self.log.event("search_timeout", elapsed=round(self.search_elapsed, 1))
                await self._begin_rth(tel)
                return
            if self.wp.done:
                # loop the pattern the other way
                self.log.event("search_pattern_restart")
                e, n = self._pos(tel)
                self.search_path = coverage_path(cfg.field.polygon, self.grid, cfg.search.spacing_m, (e, n),
                                                 margin_m=cfg.fence.margin_m, safety_m=cfg.survey.safety_m)
                self.wp = WaypointFollower(self.search_path, cfg.altitudes.search, self.origin,
                                           FollowerConfig(speed=cfg.search.speed))
            await self._send(self.wp.step(tel), tel)
            return
        if ph == Phase.CONFIRM:
            await self._send(VelocityCommand(0, 0, altitude_hold(cfg.altitudes.search, tel.alt), 0), tel)
            if self.last_target is not None:
                await self._start_follow(tel, t)
            elif t - self.confirm_t0 > 4.0 or self.identifier.state.hits == 0:
                self._set_phase(Phase.SEARCH, t, "candidate rejected")
            return
        if ph == Phase.FOLLOW:
            if self.follower.lost(t):
                self.log.event("target_lost", t=round(t, 2))
                self.identifier.reset()
                self.last_target = None
                # resume the search from where the target was last seen
                e, n = self._pos(tel)
                start = (self.follower.target_e, self.follower.target_n) if self.follower.target_e is not None else (e, n)
                self.search_path = coverage_path(cfg.field.polygon, self.grid, cfg.search.spacing_m, start,
                                                 margin_m=cfg.fence.margin_m, safety_m=cfg.survey.safety_m)
                self.wp = WaypointFollower(self.search_path, cfg.altitudes.search, self.origin,
                                           FollowerConfig(speed=cfg.search.speed))
                self.search_started = t - self.search_elapsed
                self._set_phase(Phase.SEARCH, t, "target lost")
                return
            if t - self.follow_t0 >= cfg.follow.duration_s:
                self.result.success = True
                self.log.event("follow_complete", seconds=round(t - self.follow_t0, 1),
                               in_frame=self.follower.stats.in_frame_fraction())
                await self._begin_rth(tel)
                return
            await self._send(self.follower.step(tel, t), tel)
            return
        if ph == Phase.RTH:
            if not tel.is_flying:
                self._set_phase(Phase.DONE if self.result.abort == "none" else Phase.ABORTED, t)
            elif t - self.phase_t0 > 240:
                self.log.event("rth_timeout")
                self._set_phase(Phase.ABORTED, t)
            return
        if ph == Phase.LAND:
            if not tel.is_flying:
                self._set_phase(Phase.ABORTED, t)
            return

    async def _start_follow(self, tel: Telemetry, t: float) -> None:
        """Seed the follower with the confirming detection so it has a target at once."""
        self.follower.reset()
        if self.last_target is not None and self.last_frame_img is not None:
            h, w = self.last_frame_img.shape[:2]
            gp = self.client.frame.gimbal_pitch if self.client.frame and self.client.frame.gimbal_pitch != 0.0 \
                else tel.gimbal_pitch
            self.follower.observe(self.last_target, tel, w, h, gp, t)
        self.follow_t0 = t
        self._set_phase(Phase.FOLLOW, t, "target confirmed")
        await self._gimbal(self.follower.desired_gimbal_pitch())

    async def _begin_rth(self, tel: Telemetry, land_now: bool = False) -> None:
        if self.phase in (Phase.RTH, Phase.LAND):
            return
        try:
            await self.client.hover()
            if land_now:
                await self.client.land()
                self._set_phase(Phase.LAND, tel.t, "land now")
            else:
                await self.client.go_home()
                self._set_phase(Phase.RTH, tel.t)
        except BridgeError as e:
            log.error("rth failed: %s, trying land", e)
            try:
                await self.client.land()
            except BridgeError:
                pass
            self._set_phase(Phase.LAND, tel.t, "go_home failed")

    def _begin_survey(self, tel: Telemetry, t: float) -> None:
        cfg = self.cfg
        self.grid = OccupancyGrid.for_polygon(cfg.field.polygon, cfg.survey.cell_m)
        spacing = survey_spacing(self.cam, cfg.altitudes.survey, cfg.survey.overlap)
        # legs stop a full fence margin inside the edge; the camera footprint
        # covers the strip beyond them
        legs = lawnmower(cfg.field.polygon, spacing, margin_m=cfg.fence.margin_m)
        self.wp = WaypointFollower(legs, cfg.altitudes.survey, self.origin, FollowerConfig(speed=cfg.survey.speed))
        self.log.event("survey_start", legs=len(legs), spacing_m=round(spacing, 1))
        self._set_phase(Phase.SURVEY, t)

    def _finish_survey(self, t: float) -> None:
        assert self.grid is not None
        self.grid.resolve()
        self.grid.save(self.log.dir / "map")
        self.log.event("survey_done", cells_seen=self._survey_cells, coverage=self.grid.coverage(),
                       keyframes=len(self.localizer.keyframes))

    def _load_or_skip_map(self) -> None:
        p = self.cfg.survey.map_path
        if p:
            self.grid = OccupancyGrid.load(p)
            self.log.event("map_loaded", path=p)

    def _begin_search(self, tel: Telemetry, t: float) -> None:
        cfg = self.cfg
        e, n = self._pos(tel)
        self.search_path = coverage_path(cfg.field.polygon, self.grid, cfg.search.spacing_m, (e, n),
                                         margin_m=cfg.fence.margin_m, safety_m=cfg.survey.safety_m)
        self.wp = WaypointFollower(self.search_path, cfg.altitudes.search, self.origin,
                                   FollowerConfig(speed=cfg.search.speed))
        self.identifier.reset()
        self.search_started = t
        self.search_elapsed = 0.0
        self.log.event("search_start", waypoints=len(self.search_path))
        self._set_phase(Phase.SEARCH, t)

    # ---- display + summary ----------------------------------------------------------------------

    def _show(self, tel: Telemetry) -> None:
        img = draw_detections(self.last_frame_img, self.last_dets, self.last_target)
        txt = f"{self.phase.value}  alt {tel.alt:.1f}  hdg {tel.heading:.0f}  bat {tel.battery}%  sats {tel.gps_sats}"
        cv2.putText(img, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow("brain", img)
        if self.grid is not None:
            cv2.imshow("map", self.grid.render(3))
        cv2.waitKey(1)

    def _summary(self, wall_s: float) -> dict[str, Any]:
        cfg = self.cfg
        st = self.identifier.state
        recon: dict[str, Any] = {"coverage": self.grid.coverage() if self.grid else None,
                                 "cells_seen": self._survey_cells}
        if self.grid is not None and cfg.sim.trees and self.sim_truth:
            truth = truth_grid_from_circles(cfg.field.polygon, [(t[0], t[1], t[2]) for t in cfg.sim.trees],
                                            cfg.survey.cell_m)
            recon["iou"] = self.grid.iou_obstacles(truth)
        nav: dict[str, Any] = {"search_waypoints": len(self.search_path), "flown_m": round(self._flown_m, 1)}
        if self.grid is not None and self.search_path:
            nav["collision_free"] = 0.0 if path_collides(self.grid, self.search_path) else 1.0
        det: dict[str, Any] = {
            "found": 1.0 if self.t_confirmed is not None else 0.0,
            "time_to_confirm_s": round(self.time_to_confirm, 1) if self.t_confirmed else None,
            "candidates_scored": st.candidates_scored,
        }
        if self.sim_truth:
            det["correct_person"] = None if self.t_confirmed is None else (1.0 if self.confirmed_truth_id == "target" else 0.0)
        summary = {
            "success": self.result.success,
            "phase": self.phase.value,
            "abort": self.result.abort,
            "reason": self.result.reason,
            "wall_s": round(wall_s, 1),
            "phase_times_s": self.phase_times,
            # achieved vs requested control rate: well below target means the host
            # starved the loop, and any flight-quality failure is suspect
            "control_hz": round(self._loops / wall_s, 1) if wall_s > 0 else None,
            "control_hz_target": cfg.loop_hz,
            "reconstruct": recon,
            "localize": {"n": len(self.localizer.stats.estimates),
                         "median_error_m": self.localizer.stats.median_error_m(),
                         "within_5m": self.localizer.stats.success_rate(5.0),
                         "keyframes": len(self.localizer.keyframes)},
            "navigate": nav,
            "detect": det,
            "follow": {"in_frame_fraction": self.follower.stats.in_frame_fraction(),
                       "mean_distance_m": self.follower.stats.mean_distance(),
                       "seconds": round(cfg.follow.duration_s, 1) if self.result.success else None},
            "safety": {"violations": self.safety.state.violations, "abort": self.result.abort},
            "perception": {"detector": cfg.perception.detector, "matcher": cfg.perception.matcher},
        }
        return summary
