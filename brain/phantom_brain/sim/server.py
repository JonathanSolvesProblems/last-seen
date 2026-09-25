"""WebSocket server that emulates the Android bridge over the simulated world.

Run standalone:
    python scripts/run_sim.py configs/sim_field.yaml [--speed 4] [--monitor]

Or in-process (tests, bench):
    async with SimServer(cfg, speed=8).running() as sim: ...
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, AsyncIterator

import cv2
import websockets
from websockets.asyncio.server import Server, ServerConnection, serve

from ..config import MissionConfig
from ..protocol import (BRIDGE_PROTOCOL_VERSION, DEFAULT_PORT, VelocityCommand,
                        encode_frame)
from .render import Renderer
from .world import DroneMode, World

log = logging.getLogger(__name__)


class SimServer:
    PHYSICS_HZ = 50
    TELEMETRY_HZ = 10
    FRAME_HZ = 8

    def __init__(self, cfg: MissionConfig, speed: float = 1.0, port: int = DEFAULT_PORT,
                 host: str = "127.0.0.1", monitor: bool = False, seed: int | None = None,
                 jpeg_quality: int = 80):
        self.cfg = cfg
        self.speed = speed
        self.port = port
        self.host = host
        self.monitor = monitor
        self.world = World(cfg, seed=seed)
        self.renderer = Renderer(cfg, self.world, seed=seed)
        self.clients: set[ServerConnection] = set()
        self.server: Server | None = None
        self._tasks: list[asyncio.Task] = []
        self.frame_seq = 0
        self.jpeg_quality = jpeg_quality
        self.trail: list[tuple[float, float]] = []
        self.last_frame: Any = None
        self.last_truth: dict[str, Any] | None = None
        self._pending_frames: list[tuple[float, bytes]] = []
        # host-health counters: a simulator that cannot keep pace produces
        # mission failures that say nothing about the mission
        self.sim_stalls = 0
        self.worst_physics_lag = 0.0
        self.worst_telemetry_gap = 0.0

    # ---- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        self.server = await serve(self._handler, self.host, self.port, max_size=16 * 1024 * 1024)
        self._tasks = [asyncio.create_task(self._physics_loop()),
                       asyncio.create_task(self._telemetry_loop()),
                       asyncio.create_task(self._frame_loop())]
        log.info("sim listening on ws://%s:%d (speed x%.1f)", self.host, self.port, self.speed)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.monitor:
            cv2.destroyAllWindows()

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator["SimServer"]:
        await self.start()
        try:
            yield self
        finally:
            await self.stop()

    # ---- connections -------------------------------------------------------------

    async def _handler(self, ws: ServerConnection) -> None:
        self.clients.add(ws)
        await ws.send(json.dumps({"type": "status", "sdk_registered": True, "product_connected": True,
                                  "product": "Phantom 3 Standard (sim)", "virtual_stick": self.world.drone.vs_enabled,
                                  "bridge_version": f"sim-{BRIDGE_PROTOCOL_VERSION}"}))
        try:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._on_message(ws, msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    async def _on_message(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        t = msg.get("type")
        if t == "vel":
            self.world.velocity(VelocityCommand(float(msg.get("vn", 0)), float(msg.get("ve", 0)),
                                                float(msg.get("vu", 0)), float(msg.get("yaw_rate", 0))))
            return
        if t != "cmd":
            return
        name = msg.get("name")
        w = self.world
        ok, err = False, f"unknown command {name}"
        if name == "takeoff":
            ok, err = w.takeoff()
        elif name == "land":
            ok, err = w.land()
        elif name == "confirm_landing":
            ok, err = True, None
        elif name == "go_home":
            ok, err = w.go_home()
        elif name == "cancel_go_home":
            ok, err = w.cancel_go_home()
        elif name == "enable_vs":
            ok, err = w.set_virtual_stick(True)
        elif name == "disable_vs":
            ok, err = w.set_virtual_stick(False)
        elif name == "set_limits":
            ok, err = w.set_limits(msg.get("max_alt", 120), msg.get("max_radius", 500))
        elif name == "gimbal":
            ok, err = w.set_gimbal(msg.get("pitch", 0))
        elif name == "stop_motors":
            ok, err = w.stop_motors()
        elif name == "record_start":
            ok, err = w.set_recording(True)
        elif name == "record_stop":
            ok, err = w.set_recording(False)
        elif name == "multi_mode":
            ok, err = w.set_multi_mode()
        elif name == "ping":
            ok, err = True, None
        await ws.send(json.dumps({"type": "ack", "id": msg.get("id"), "ok": ok, "error": err}))
        if name in ("takeoff", "land", "go_home"):
            await self._broadcast(json.dumps({"type": "event", "name": name, "detail": "ok" if ok else str(err)}))

    async def _broadcast(self, data: str | bytes) -> None:
        dead = []
        for c in list(self.clients):
            try:
                await c.send(data)
            except websockets.ConnectionClosed:
                dead.append(c)
        for c in dead:
            self.clients.discard(c)

    # ---- loops -----------------------------------------------------------------

    async def _physics_loop(self) -> None:
        dt = 1.0 / self.PHYSICS_HZ
        wall = time.monotonic()
        while True:
            self.world.step(dt)
            d = self.world.drone
            if d.mode != DroneMode.IDLE and (not self.trail or
                                             abs(self.trail[-1][0] - d.e) + abs(self.trail[-1][1] - d.n) > 1.0):
                self.trail.append((d.e, d.n))
            wall += dt / self.speed
            delay = wall - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                self.worst_physics_lag = max(self.worst_physics_lag, -delay)
                await asyncio.sleep(0)
                if delay < -1.0:
                    self.sim_stalls += 1
                    wall = time.monotonic()   # fell behind badly; resync instead of spinning

    async def _telemetry_loop(self) -> None:
        period = 1.0 / self.TELEMETRY_HZ / self.speed
        last = time.monotonic()
        while True:
            await self._broadcast(self.world.telemetry().to_json())
            now = time.monotonic()
            # Longest wall-clock gap between telemetry messages. The brain's
            # dead-link abort is measured in wall clock, so if this exceeds its
            # threshold the abort is the host being slow, not the mission failing.
            self.worst_telemetry_gap = max(self.worst_telemetry_gap, now - last)
            last = now
            await asyncio.sleep(period)

    async def _frame_loop(self) -> None:
        period = 1.0 / self.FRAME_HZ / self.speed
        latency = self.cfg.sim.video_latency_s
        while True:
            img, truth = await asyncio.get_running_loop().run_in_executor(None, self.renderer.render)
            self.last_frame, self.last_truth = img, truth
            self.frame_seq += 1
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if ok:
                header = {"t": self.world.t, "seq": self.frame_seq, "w": img.shape[1], "h": img.shape[0],
                          "gimbal_pitch": truth["drone"]["gimbal_pitch"], "truth": truth}
                data = encode_frame(header, buf.tobytes())
                if latency > 0:
                    # deliver after the simulated video latency
                    asyncio.get_running_loop().call_later(latency / self.speed,
                                                          lambda d=data: asyncio.ensure_future(self._broadcast(d)))
                else:
                    await self._broadcast(data)
            if self.monitor:
                top = self.renderer.top_down(self.trail)
                cv2.imshow("sim: field", top)
                cv2.imshow("sim: camera", img)
                cv2.waitKey(1)
            await asyncio.sleep(period)

    # ---- scoring helpers ------------------------------------------------------

    def episode_summary(self) -> dict[str, Any]:
        ep = self.world.episode
        return {"crashed": ep.crashed, "left_field": ep.left_field, "max_alt": round(ep.max_alt, 1),
                "max_radius": round(ep.max_radius, 1), "distance_flown": round(ep.distance_flown, 1),
                "watchdog_hovers": ep.watchdog_hovers, "sim_time": round(self.world.t, 1),
                "speed": self.speed,
                "sim_stalls": self.sim_stalls,
                "worst_physics_lag_s": round(self.worst_physics_lag, 2),
                "worst_telemetry_gap_s": round(self.worst_telemetry_gap, 2),
                "events": [(round(t, 1), n, d) for (t, n, d) in ep.events]}
