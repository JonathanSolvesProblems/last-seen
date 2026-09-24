import json
import struct
from pathlib import Path

import cv2
import numpy as np
import pytest

from phantom_brain import geo
from phantom_brain.camera import CameraModel
from phantom_brain.config import MissionConfig
from phantom_brain.detect import Detection, IdentifierConfig, OracleDetector, OracleMatcher, OracleNoise, TargetIdentifier
from phantom_brain.protocol import Telemetry, decode_frame, encode_frame
from phantom_brain.sim.render import Renderer
from phantom_brain.sim.world import World
from phantom_brain.tasks.follow import FollowConfig, Follower
from phantom_brain.tasks.localize import VisualLocalizer
from phantom_brain.tasks.navigate import astar, coverage_path, line_free, path_collides, plan, simplify
from phantom_brain.tasks.reconstruct import (FREE, OBSTACLE, GrassClassifier, OccupancyGrid, ingest_nadir_frame,
                                             lawnmower, survey_spacing, truth_grid_from_circles)

SQUARE = [(-40, -30), (40, -30), (40, 30), (-40, 30)]


def test_renderer_is_deterministic_for_a_seed():
    """Same seed, same picture. Unseeded frame noise made bench seeds meaningless
    and ORB-based tests flaky."""
    cfg = MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5}, "polygon_enu": SQUARE},
        "sim": {"random_trees": 4, "seed": 11, "frame_w": 320, "frame_h": 180},
    })
    def frame(seed):
        w = World(cfg, seed=seed)
        r = Renderer(cfg, w, seed=seed)
        d = w.drone
        d.e, d.n, d.alt, d.heading = 5, -5, 25, 30
        d.gimbal_pitch = d.gimbal_target = -90
        return r.render()[0]

    a, b = frame(7), frame(7)
    assert np.array_equal(a, b), "same seed must render identical frames"
    assert not np.array_equal(a, frame(8)), "different seeds must differ"


def test_flight_recorder_survives_an_unclean_exit(tmp_path):
    """Telemetry must be readable without close(): a crashed flight is the one
    that most needs its last seconds on disk."""
    from phantom_brain.scoreboard import RunLogger

    log = RunLogger(tmp_path, "crashy")
    for i in range(5):
        log.telemetry({"i": i, "alt": float(i)})
    # deliberately no close(): simulate the process dying mid-flight
    lines = (Path(log.dir) / "telemetry.jsonl").read_text().strip().splitlines()
    assert len(lines) == 5
    assert json.loads(lines[-1])["i"] == 4


def test_host_starved_is_causal_and_outcome_blind():
    from phantom_brain.scoreboard import host_starved

    def run(success, gap=0.5, lag=0.5, stalls=0, hz=9.5, target=10.0):
        return {"success": success, "control_hz": hz, "control_hz_target": target,
                "sim": {"worst_telemetry_gap_s": gap, "worst_physics_lag_s": lag, "sim_stalls": stalls}}

    # a healthy run is valid whether it passed or failed
    assert host_starved(run(True))[0] is False
    assert host_starved(run(False))[0] is False

    # the verdict must not depend on the outcome: identical host health, same answer
    for kwargs in ({"gap": 2.5}, {"lag": 5.0}, {"stalls": 9}, {"hz": 4.0}):
        ok_pass = host_starved(run(True, **kwargs))
        ok_fail = host_starved(run(False, **kwargs))
        assert ok_pass == ok_fail, kwargs
        assert ok_pass[0] is True, kwargs

    # a single brief hiccup in a long run is normal and must not void it
    assert host_starved(run(True, gap=1.23, lag=1.2, stalls=1, hz=9.1))[0] is False


def test_voided_runs_are_excluded_from_the_scoreboard(tmp_path):
    """A run the host starved must not count as a mission failure."""
    import json as _json

    from phantom_brain.scoreboard import aggregate, format_table

    def write(root, name, success, telemetry_gap):
        d = root / name
        d.mkdir(parents=True)
        (d / "summary.json").write_text(_json.dumps({
            "success": success,
            "control_hz": 9.5, "control_hz_target": 10.0,
            "sim": {"worst_telemetry_gap_s": telemetry_gap, "worst_physics_lag_s": 0.5, "sim_stalls": 0},
            "detect": {"found": 1.0 if success else 0.0},
            "follow": {"in_frame_fraction": 0.8 if success else 0.0},
        }))

    good = tmp_path / "good"
    write(good, "a", success=True, telemetry_gap=0.5)     # healthy host
    write(good, "b", success=False, telemetry_gap=9.0)    # host stalled past the abort
    agg = aggregate(good)
    assert agg["runs"] == 1 and agg["voided"] == 1
    assert agg["end_to_end_rate"] == 1.0
    assert "voided" in format_table(agg)

    # every run voided means there is no rate to report, not a 0% rate
    empty = tmp_path / "onlyvoid"
    write(empty, "x", success=False, telemetry_gap=9.0)
    agg2 = aggregate(empty)
    assert agg2["runs"] == 0 and agg2["voided"] == 1
    assert "no valid runs" in format_table(agg2)


def test_protocol_frame_roundtrip():
    jpeg = b"\xff\xd8fake\xff\xd9"
    data = encode_frame({"t": 1.5, "seq": 3, "w": 4, "h": 2, "gimbal_pitch": -60, "truth": {"a": 1}}, jpeg)
    fr = decode_frame(data)
    assert fr.t == 1.5 and fr.seq == 3 and fr.w == 4 and fr.jpeg == jpeg and fr.truth == {"a": 1}
    assert fr.gimbal_pitch == -60


def test_telemetry_json():
    t = Telemetry(t=1, lat=2, lon=3, alt=4, heading=5)
    d = json.loads(t.to_json())
    assert d["type"] == "telemetry"
    assert Telemetry.from_dict(d).alt == 4


def test_lawnmower_covers_polygon():
    legs = lawnmower(SQUARE, spacing_m=10, margin_m=1)
    assert len(legs) >= 10 and len(legs) % 2 == 0
    for e, n in legs:
        assert geo.point_in_polygon(e, n, SQUARE)


def test_survey_spacing_reasonable():
    cam = CameraModel(960, 540)
    s = survey_spacing(cam, 30, 0.3)
    assert 30 < s < 45


def test_grid_and_astar_avoid_obstacle():
    g = OccupancyGrid.for_polygon(SQUARE, cell_m=1.0, pad_m=2)
    g.cells[:] = FREE
    # wall across the middle with a gap at the south end
    c0, _ = g.to_cell(0, 0)
    _, r_top = g.to_cell(0, 25)
    _, r_bot = g.to_cell(0, -22)
    g.cells[r_bot:r_top, c0] = OBSTACLE
    path = plan(g, (-30, 0), (30, 0), safety_m=1.0)
    assert path is not None
    assert not path_collides(g, path)
    # the path must go around, so it is much longer than the straight line
    length = sum(geo.dist(path[i], path[i + 1]) for i in range(len(path) - 1))
    assert length > 70


def test_simplify_keeps_endpoints():
    g = OccupancyGrid.for_polygon(SQUARE, cell_m=1.0)
    g.cells[:] = FREE
    raw = astar(g, (-30, -20), (30, 20))
    s = simplify(g, raw)
    assert s[0] == raw[0] and s[-1] == raw[-1] and len(s) <= 3


def test_coverage_path_reroutes_around_tree():
    g = truth_grid_from_circles(SQUARE, [(0, 0, 6)], cell_m=1.0)
    path = coverage_path(SQUARE, g, spacing_m=15, start=(-38, -28), safety_m=2.0)
    assert len(path) > 6
    assert not path_collides(g.inflated(1.0), path)


def test_reconstruct_from_synthetic_nadir_frames():
    cfg = MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5}, "polygon_enu": SQUARE},
        "sim": {"trees": [[10, 5, 5, 10], [-20, -10, 4, 8]], "frame_w": 960, "frame_h": 540},
    })
    world = World(cfg)
    r = Renderer(cfg, world)
    cam = CameraModel(960, 540)
    grid = OccupancyGrid.for_polygon(SQUARE, cell_m=1.0)
    clf = GrassClassifier()
    d = world.drone
    d.alt = 30
    d.gimbal_pitch = -90
    d.gimbal_target = -90
    spacing = survey_spacing(cam, 30, 0.3)
    for (e, n) in lawnmower(SQUARE, spacing):
        for k in range(8):
            d.e, d.n = e, n
            d.heading = 90
            img, _ = r.render()
            ingest_nadir_frame(grid, img, cam, d.e, d.n, d.alt, d.heading, -90, clf)
    # sample along the legs too
    legs = lawnmower(SQUARE, spacing)
    for i in range(0, len(legs) - 1, 2):
        (e1, n1), (e2, n2) = legs[i], legs[i + 1]
        for t in np.linspace(0, 1, 12):
            d.e, d.n = e1 + t * (e2 - e1), n1 + t * (n2 - n1)
            img, _ = r.render()
            ingest_nadir_frame(grid, img, cam, d.e, d.n, d.alt, d.heading, -90, clf)
    grid.resolve()
    truth = truth_grid_from_circles(SQUARE, [(10, 5, 5), (-20, -10, 4)], cell_m=1.0)
    assert grid.coverage() > 0.8
    iou = grid.iou_obstacles(truth)
    assert iou > 0.5, iou
    # the tree centres are obstacles, open grass is free
    assert grid.cells[grid.to_cell(10, 5)[1], grid.to_cell(10, 5)[0]] == OBSTACLE
    assert grid.cells[grid.to_cell(-30, 20)[1], grid.to_cell(-30, 20)[0]] == FREE


def test_localizer_recovers_offset():
    cfg = MissionConfig.from_dict({
        "field": {"origin": {"lat": 45.5, "lon": -73.5}, "polygon_enu": SQUARE},
        "sim": {"random_trees": 6, "seed": 3, "frame_w": 960, "frame_h": 540},
    })
    world = World(cfg)
    r = Renderer(cfg, world)
    cam = CameraModel(960, 540)
    loc = VisualLocalizer(cam, min_keyframe_spacing_m=5)
    d = world.drone
    d.alt, d.gimbal_pitch, d.gimbal_target, d.heading = 30, -90, -90, 0
    for (e, n) in [(-20, -10), (0, -10), (20, -10), (-20, 10), (0, 10), (20, 10)]:
        d.e, d.n = e, n
        img, _ = r.render()
        assert loc.maybe_add_keyframe(img, e, n, 30, 0, -90)
    d.e, d.n = 4, -7
    img, _ = r.render()
    est = loc.estimate(img, 30, 0, -90, gps_e=4, gps_n=-7)
    assert est is not None
    assert est.error_vs_gps_m < 3.0, est


def test_oracle_detector_and_identifier():
    det = OracleDetector(OracleNoise(miss_prob=0.0, jitter_px=0, false_positive_prob=0.0, seed=1))
    matcher = OracleMatcher("target", noise=0.0)
    ident = TargetIdentifier(det, matcher, IdentifierConfig(confirm_frames=3))
    img = np.zeros((540, 960, 3), np.uint8)
    truth = {"people": [{"id": "decoy", "bbox": [100, 100, 130, 160], "visible": True},
                        {"id": "target", "bbox": [500, 300, 530, 360], "visible": True}]}
    for i in range(3):
        target, dets = ident.update(img, float(i), truth)
        assert len(dets) == 2
    assert target is not None and target.truth_id == "target"
    # losing it
    for i in range(20):
        target, _ = ident.update(img, 10.0 + i, {"people": []})
    assert target is None and not ident.state.confirmed


def test_follower_moves_toward_standoff():
    origin = geo.Origin(45.5, -73.5)
    cam = CameraModel(960, 540)
    f = Follower(cam, origin, FollowConfig(standoff_m=10, altitude_m=15, speed=4))
    lat, lon = origin.to_latlon(0, 0)
    tel = Telemetry(t=0, lat=lat, lon=lon, alt=15, heading=0, is_flying=True)
    pitch = f.desired_gimbal_pitch()
    # a person 30 m north: project to pixel, feed as detection
    p = cam.ground_to_pixel(0, 30, 0, 0, 15, pitch, 0, 0.0)
    assert p is not None
    det = Detection(p[0] - 10, p[1] - 40, p[0] + 10, p[1])
    f.observe(det, tel, 960, 540, pitch, 0.0)
    assert f.target_n == pytest.approx(30, abs=1.0)
    cmd = f.step(tel, 0.1)
    assert cmd.vn > 1.0 and abs(cmd.ve) < 0.5      # fly north toward the standoff point
    # from 8 m away it should back off (standoff 10)
    lat, lon = origin.to_latlon(0, 22)
    tel2 = Telemetry(t=1, lat=lat, lon=lon, alt=15, heading=0, is_flying=True)
    cmd = f.step(tel2, 1.0)
    assert cmd.vn < 0
