"""Wire protocol shared by the Android bridge, the simulator and the brain.

Transport: one WebSocket connection, port 8765.

Text messages are JSON objects with a "type" field.

Bridge -> brain
    {"type": "status", "sdk_registered": bool, "product_connected": bool,
     "product": str, "virtual_stick": bool, "bridge_version": str}
    {"type": "telemetry", ...Telemetry fields...}
    {"type": "ack", "id": str, "ok": bool, "error": str | null}
    {"type": "event", "name": str, "detail": str}

Brain -> bridge
    {"type": "cmd", "id": str, "name": "takeoff" | "land" | "go_home" |
        "enable_vs" | "disable_vs" | "cancel_go_home" | "confirm_landing"}
    {"type": "cmd", "id": str, "name": "set_limits", "max_alt": m, "max_radius": m}
    {"type": "cmd", "id": str, "name": "gimbal", "pitch": deg}
    {"type": "vel", "vn": m/s, "ve": m/s, "vu": m/s, "yaw_rate": deg/s}

Binary messages carry one video frame:
    4 bytes big-endian header length, JSON header, then JPEG bytes.
    header = {"type": "frame", "t": seconds, "seq": int, "w": int, "h": int,
              "gimbal_pitch": deg, "truth": {...}?}
    "truth" is only ever set by the simulator (ground truth for the oracle
    detector and for scoring). The real bridge never sends it.

Velocity commands are in the GROUND frame: vn = north, ve = east, vu = up.
yaw_rate is clockwise-positive in degrees per second. The bridge holds the
last velocity for at most WATCHDOG_MS and then commands a hover, so the brain
must stream "vel" messages continuously (10 Hz) while flying.
"""

from __future__ import annotations

import json
import struct
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_PORT = 8765
WATCHDOG_MS = 500
VEL_RATE_HZ = 10
BRIDGE_PROTOCOL_VERSION = 1


@dataclass
class Telemetry:
    t: float                       # seconds (bridge monotonic or sim time)
    lat: float
    lon: float
    alt: float                     # metres above takeoff point
    heading: float                 # degrees, 0 = north, clockwise
    vn: float = 0.0                # m/s north
    ve: float = 0.0                # m/s east
    vu: float = 0.0                # m/s up
    battery: int = 100             # percent
    gps_sats: int = 0
    gps_fix: bool = True           # False while the aircraft reports no position (lat/lon were NaN)
    is_flying: bool = False
    motors_on: bool = False
    virtual_stick: bool = False
    flight_mode: str = ""
    rc_mode_switch: str = ""       # "P" / "A" / "F" / "" when unknown
    home_lat: float | None = None
    home_lon: float | None = None
    home_set: bool = False
    gimbal_pitch: float = 0.0
    roll: float = 0.0              # degrees, right wing down positive
    pitch: float = 0.0             # degrees, nose up positive
    # The phone's own GNSS. The phone rides on the RC in the pilot's hands, so
    # during a follow or a search test this is where the SUBJECT stands, from a
    # sensor the vision pipeline never touches. It grades every distance claimed.
    phone_lat: float | None = None
    phone_lon: float | None = None
    phone_accuracy_m: float | None = None
    phone_fix_age_s: float | None = None

    def phone_fix(self, max_age_s: float = 3.0, max_accuracy_m: float = 15.0) -> tuple[float, float] | None:
        """The phone's position if it is fresh and tight enough to grade with."""
        if self.phone_lat is None or self.phone_lon is None:
            return None
        if self.phone_fix_age_s is None or self.phone_fix_age_s > max_age_s:
            return None
        if self.phone_accuracy_m is None or self.phone_accuracy_m > max_accuracy_m:
            return None
        return self.phone_lat, self.phone_lon

    def to_json(self) -> str:
        d = asdict(self)
        d["type"] = "telemetry"
        return json.dumps(d)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Telemetry":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class VelocityCommand:
    vn: float = 0.0
    ve: float = 0.0
    vu: float = 0.0
    yaw_rate: float = 0.0

    def to_json(self) -> str:
        return json.dumps({"type": "vel", "vn": round(self.vn, 3), "ve": round(self.ve, 3),
                           "vu": round(self.vu, 3), "yaw_rate": round(self.yaw_rate, 2)})

    def is_zero(self) -> bool:
        return self.vn == 0 and self.ve == 0 and self.vu == 0 and self.yaw_rate == 0


@dataclass
class Frame:
    t: float
    seq: int
    w: int
    h: int
    jpeg: bytes
    gimbal_pitch: float = 0.0
    roll: float = 0.0              # degrees, right wing down positive
    pitch: float = 0.0             # degrees, nose up positive
    truth: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def cmd(name: str, **kwargs: Any) -> tuple[str, str]:
    """Build a command message. Returns (id, json_text)."""
    cid = new_id()
    body = {"type": "cmd", "id": cid, "name": name}
    body.update(kwargs)
    return cid, json.dumps(body)


def encode_frame(header: dict[str, Any], jpeg: bytes) -> bytes:
    header = dict(header)
    header["type"] = "frame"
    hb = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(hb)) + hb + jpeg


def decode_frame(data: bytes) -> Frame:
    if len(data) < 4:
        raise ValueError("frame too short")
    (hlen,) = struct.unpack(">I", data[:4])
    header = json.loads(data[4:4 + hlen].decode("utf-8"))
    jpeg = data[4 + hlen:]
    known = {"t", "seq", "w", "h", "gimbal_pitch", "truth"}
    extra = {k: v for k, v in header.items() if k not in known and k != "type"}
    return Frame(
        t=float(header.get("t", 0.0)),
        seq=int(header.get("seq", 0)),
        w=int(header.get("w", 0)),
        h=int(header.get("h", 0)),
        jpeg=jpeg,
        gimbal_pitch=float(header.get("gimbal_pitch", 0.0)),
        truth=header.get("truth"),
        extra=extra,
    )
