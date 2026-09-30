"""Async WebSocket client for the bridge (Android app or simulator)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from .protocol import (DEFAULT_PORT, Frame, Telemetry, VelocityCommand, cmd,
                       decode_frame)

log = logging.getLogger(__name__)


class BridgeError(RuntimeError):
    pass


class BridgeClient:
    """Keeps the latest telemetry and frame, and sends commands.

    Usage:
        async with BridgeClient("ws://127.0.0.1:8765") as bc:
            await bc.wait_for_telemetry()
            await bc.takeoff()
            ...
    """

    def __init__(self, url: str = f"ws://127.0.0.1:{DEFAULT_PORT}", ack_timeout: float = 15.0):
        self.url = url
        self.ack_timeout = ack_timeout
        self.ws: ClientConnection | None = None
        self.telemetry: Telemetry | None = None
        self.tel_history: deque[Telemetry] = deque(maxlen=200)
        self.frame: Frame | None = None
        self.status: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self._frame_seq_seen = -1
        self._acks: dict[str, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._telemetry_event = asyncio.Event()
        self._frame_event = asyncio.Event()
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self.last_rx = 0.0

    async def __aenter__(self) -> "BridgeClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def connect(self) -> None:
        self.ws = await websockets.connect(self.url, max_size=16 * 1024 * 1024,
                                           ping_interval=5, ping_timeout=10)
        self._reader = asyncio.create_task(self._read_loop())
        log.info("connected to %s", self.url)

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
        if self.ws:
            await self.ws.close()
            self.ws = None

    # ---- inbound ----------------------------------------------------------

    async def _read_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                self.last_rx = time.monotonic()
                if isinstance(raw, (bytes, bytearray)):
                    try:
                        fr = decode_frame(bytes(raw))
                    except Exception as e:  # noqa: BLE001
                        log.warning("bad frame: %s", e)
                        continue
                    self.frame = fr
                    self._frame_event.set()
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("bad json: %r", raw[:80])
                    continue
                t = msg.get("type")
                if t == "telemetry":
                    self.telemetry = Telemetry.from_dict(msg)
                    self.tel_history.append(self.telemetry)
                    self._telemetry_event.set()
                elif t == "ack":
                    fut = self._acks.pop(msg.get("id", ""), None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                elif t == "status":
                    self.status = msg
                elif t == "event":
                    self.events.append(msg)
                    log.info("bridge event: %s %s", msg.get("name"), msg.get("detail", ""))
                    if self.on_event:
                        self.on_event(msg)
        except websockets.ConnectionClosed as e:
            log.warning("bridge connection closed: %s", e)
        finally:
            for fut in self._acks.values():
                if not fut.done():
                    fut.set_exception(BridgeError("connection closed"))
            self._acks.clear()

    async def wait_for_telemetry(self, timeout: float = 10.0) -> Telemetry:
        if self.telemetry is None:
            await asyncio.wait_for(self._telemetry_event.wait(), timeout)
        assert self.telemetry is not None
        return self.telemetry

    async def next_telemetry(self, timeout: float = 2.0) -> Telemetry:
        self._telemetry_event.clear()
        await asyncio.wait_for(self._telemetry_event.wait(), timeout)
        assert self.telemetry is not None
        return self.telemetry

    async def next_frame(self, timeout: float = 2.0) -> Frame:
        self._frame_event.clear()
        await asyncio.wait_for(self._frame_event.wait(), timeout)
        assert self.frame is not None
        return self.frame

    def telemetry_at(self, t: float) -> Telemetry | None:
        """Telemetry sample closest in time to t (compensates video latency)."""
        if not self.tel_history:
            return self.telemetry
        return min(self.tel_history, key=lambda x: abs(x.t - t))

    def new_frame(self) -> Frame | None:
        """Return the latest frame if it has not been returned before."""
        if self.frame is not None and self.frame.seq != self._frame_seq_seen:
            self._frame_seq_seen = self.frame.seq
            return self.frame
        return None

    # ---- outbound ---------------------------------------------------------

    async def _send(self, text: str) -> None:
        if self.ws is None:
            raise BridgeError("not connected")
        await self.ws.send(text)

    async def send_velocity(self, v: VelocityCommand) -> None:
        await self._send(v.to_json())

    async def hover(self) -> None:
        await self.send_velocity(VelocityCommand())

    async def command(self, name: str, **kwargs: Any) -> dict[str, Any]:
        cid, text = cmd(name, **kwargs)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._acks[cid] = fut
        await self._send(text)
        try:
            ack = await asyncio.wait_for(fut, self.ack_timeout)
        except asyncio.TimeoutError:
            self._acks.pop(cid, None)
            raise BridgeError(f"no ack for {name}")
        if not ack.get("ok", False):
            raise BridgeError(f"{name} failed: {ack.get('error')}")
        return ack

    async def enable_virtual_stick(self) -> None:
        await self.command("enable_vs")

    async def disable_virtual_stick(self) -> None:
        await self.command("disable_vs")

    async def takeoff(self) -> None:
        await self.command("takeoff")

    async def land(self) -> None:
        await self.command("land")

    async def confirm_landing(self) -> None:
        await self.command("confirm_landing")

    async def go_home(self) -> None:
        await self.command("go_home")

    async def cancel_go_home(self) -> None:
        await self.command("cancel_go_home")

    async def set_limits(self, max_alt: float, max_radius: float) -> None:
        await self.command("set_limits", max_alt=max_alt, max_radius=max_radius)

    async def stop_motors(self) -> None:
        """Cut the propellers. Only correct when the aircraft is in someone's hands."""
        await self.command("stop_motors")

    async def set_gimbal_pitch(self, pitch: float) -> None:
        await self.command("gimbal", pitch=pitch)

    async def enable_multi_mode(self) -> None:
        """Let the RC mode switch reach F. Off by default on the Phantom 3."""
        await self.command("multi_mode")

    async def reconnect_product(self, seconds: int = 25) -> None:
        """Drop the aircraft and pick it up again. Clears a dead camera session.

        The P3S keeps sending telemetry and accepting gimbal commands while its
        decoder emits pure black, so this is the fix for "the detector sees
        nothing" when the feed is the thing that is broken.
        """
        await self.command("reconnect", seconds=seconds)

    async def flight_state(self) -> str:
        """Everything the flight controller knows about why it might not fly."""
        ack = await self.command("flight_state")
        return str(ack.get("detail") or "")

    async def start_motors(self) -> None:
        """Arm without an auto-takeoff. Fails with a REASON where takeoff acks and ignores."""
        await self.command("start_motors")

    async def components(self) -> str:
        """What the SDK currently thinks is attached. Diagnoses 'no camera'."""
        ack = await self.command("components")
        return str(ack.get("detail") or "")

    async def sd_state(self) -> str:
        """What the camera says about its own card: inserted, formatted, space."""
        ack = await self.command("sd_state")
        return str(ack.get("detail") or "")

    async def format_sd(self) -> None:
        """ERASES THE CARD, in the camera. Pull the footage first.

        A PC format leaves DJI still warning that formatting is recommended,
        because that warning is about which device wrote the filesystem.
        """
        await self.command("format_sd")

    async def record_start(self) -> None:
        """Roll the onboard 2.7K camera to the SD card. The bridge feed is only 720p."""
        await self.command("record_start")

    async def record_stop(self) -> None:
        await self.command("record_stop")
