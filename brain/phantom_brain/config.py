"""Mission and simulator configuration, loaded from YAML.

One file describes the field for both the real mission and the simulator, so
the exact plan that flies in the sim is the plan that flies in the park. See
configs/sim_field.yaml for a commented example.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import geo


@dataclass
class FieldConfig:
    origin_lat: float
    origin_lon: float
    polygon: list[tuple[float, float]]          # ENU metres
    home: tuple[float, float] = (0.0, 0.0)

    @property
    def origin(self) -> geo.Origin:
        return geo.Origin(self.origin_lat, self.origin_lon)


@dataclass
class FenceConfig:
    max_radius_m: float = 150.0
    max_alt_m: float = 50.0
    floor_alt_m: float = 15.0
    home_radius_m: float = 8.0
    margin_m: float = 5.0


@dataclass
class AltitudesConfig:
    survey: float = 30.0
    search: float = 22.0
    follow: float = 15.0
    rth: float = 35.0


@dataclass
class SearchConfig:
    spacing_m: float = 25.0
    gimbal_pitch: float = -60.0
    speed: float = 4.0
    max_time_s: float = 300.0


@dataclass
class SurveyConfig:
    enabled: bool = True
    overlap: float = 0.3
    speed: float = 5.0
    cell_m: float = 1.0
    safety_m: float = 3.0
    map_path: str | None = None       # reuse a saved map instead of surveying


@dataclass
class FollowCfg:
    standoff_m: float = 12.0
    speed: float = 4.0
    duration_s: float = 60.0
    lost_timeout_s: float = 8.0


@dataclass
class PerceptionConfig:
    detector: str = "oracle"          # oracle | yolo
    matcher: str = "oracle"           # oracle | color | claude
    reference_photo: str | None = None
    yolo_weights: str = "models/yolo11n.onnx"
    yolo_imgsz: int = 960
    yolo_conf: float = 0.25
    match_threshold: float = 0.6
    confirm_frames: int = 3
    lose_frames: int = 15


@dataclass
class SimPerson:
    id: str
    start: tuple[float, float]
    path: list[tuple[float, float]] = field(default_factory=list)
    speed: float = 1.2
    shirt_bgr: tuple[int, int, int] = (40, 40, 220)
    pause_s: float = 0.0
    # A real photograph of a person, pasted into their on-screen box, so a
    # rehearsal can run the real detector, the real matcher and a model that
    # looks at the crops. Without it the sim's people are coloured discs.
    sprite: str | None = None


@dataclass
class SimConfig:
    trees: list[tuple[float, float, float, float]] = field(default_factory=list)  # e, n, radius, height
    random_trees: int = 0
    seed: int = 1
    people: list[SimPerson] = field(default_factory=list)
    wind_en: tuple[float, float] = (0.0, 0.0)
    gps_noise_m: float = 0.6
    frame_w: int = 960
    frame_h: int = 540
    raster_res_m: float = 0.25
    battery_drain_pct_per_s: float = 0.07
    video_latency_s: float = 0.25
    # ArUco ground markers painted straight into the camera image: (id, e, n,
    # size_m). Lets the camera-truth flight be rehearsed against a camera whose
    # real pose is known exactly.
    markers: list[tuple[int, float, float, float]] = field(default_factory=list)


@dataclass
class MissionConfig:
    field: FieldConfig
    fence: FenceConfig = field(default_factory=FenceConfig)
    altitudes: AltitudesConfig = field(default_factory=AltitudesConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    survey: SurveyConfig = field(default_factory=SurveyConfig)
    follow: FollowCfg = field(default_factory=FollowCfg)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    sim: SimConfig = field(default_factory=SimConfig)
    bridge_url: str = "ws://127.0.0.1:8765"
    runs_dir: str = "runs"
    loop_hz: float = 10.0
    max_mission_s: float = 900.0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "MissionConfig":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissionConfig":
        f = data.get("field", {})
        origin = f.get("origin", {"lat": 0.0, "lon": 0.0})
        o = geo.Origin(float(origin["lat"]), float(origin["lon"]))
        if "polygon_enu" in f:
            poly = [tuple(map(float, p)) for p in f["polygon_enu"]]
        elif "polygon_latlon" in f:
            poly = [o.to_local(float(p[0]), float(p[1])) for p in f["polygon_latlon"]]
        else:
            raise ValueError("field needs polygon_enu or polygon_latlon")
        home = tuple(map(float, f.get("home", (0.0, 0.0))))
        fc = FieldConfig(o.lat, o.lon, poly, home)

        def sub(name: str, klass: Any) -> Any:
            d = data.get(name, {}) or {}
            known = {k: v for k, v in d.items() if k in klass.__dataclass_fields__}
            return klass(**known)

        sim_d = dict(data.get("sim", {}) or {})
        people = []
        for p in sim_d.pop("people", []) or []:
            people.append(SimPerson(
                id=str(p["id"]),
                start=tuple(map(float, p["start"])),
                path=[tuple(map(float, q)) for q in p.get("path", [])],
                speed=float(p.get("speed", 1.2)),
                shirt_bgr=tuple(int(x) for x in p.get("shirt_bgr", (40, 40, 220))),
                pause_s=float(p.get("pause_s", 0.0)),
                sprite=p.get("sprite"),
            ))
        trees = [tuple(map(float, t)) for t in sim_d.pop("trees", []) or []]
        markers = [(int(m[0]), float(m[1]), float(m[2]), float(m[3]))
                   for m in sim_d.pop("markers", []) or []]
        sim_known = {k: v for k, v in sim_d.items() if k in SimConfig.__dataclass_fields__}
        if "wind_en" in sim_known:
            sim_known["wind_en"] = tuple(map(float, sim_known["wind_en"]))
        sim = SimConfig(trees=trees, people=people, markers=markers, **sim_known)
        if sim.random_trees and not sim.trees:
            sim.trees = random_trees(poly, sim.random_trees, sim.seed, home)

        cfg = cls(
            field=fc,
            fence=sub("fence", FenceConfig),
            altitudes=sub("altitudes", AltitudesConfig),
            search=sub("search", SearchConfig),
            survey=sub("survey", SurveyConfig),
            follow=sub("follow", FollowCfg),
            perception=sub("perception", PerceptionConfig),
            sim=sim,
            bridge_url=str(data.get("bridge_url", "ws://127.0.0.1:8765")),
            runs_dir=str(data.get("runs_dir", "runs")),
            loop_hz=float(data.get("loop_hz", 10.0)),
            max_mission_s=float(data.get("max_mission_s", 900.0)),
            raw=data,
        )
        return cfg


def random_trees(poly: list[tuple[float, float]], count: int, seed: int,
                 home: tuple[float, float], min_from_home: float = 15.0,
                 radius_range: tuple[float, float] = (2.0, 5.0),
                 height_range: tuple[float, float] = (6.0, 14.0)) -> list[tuple[float, float, float, float]]:
    rng = random.Random(seed)
    x0, y0, x1, y1 = geo.polygon_bbox(poly)
    out: list[tuple[float, float, float, float]] = []
    tries = 0
    while len(out) < count and tries < count * 50:
        tries += 1
        e = rng.uniform(x0, x1)
        n = rng.uniform(y0, y1)
        r = rng.uniform(*radius_range)
        if not geo.point_in_polygon(e, n, poly):
            continue
        if math.hypot(e - home[0], n - home[1]) < min_from_home + r:
            continue
        if any(math.hypot(e - t[0], n - t[1]) < r + t[2] + 3.0 for t in out):
            continue
        out.append((e, n, r, rng.uniform(*height_range)))
    return out
