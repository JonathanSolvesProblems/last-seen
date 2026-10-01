import math
import sys
from pathlib import Path

import pytest

from phantom_brain import geo
from phantom_brain.camera import CameraModel


def test_origin_roundtrip():
    o = geo.Origin(45.5017, -73.5673)
    for e, n in [(0, 0), (100, 50), (-250, 80), (3.3, -400)]:
        lat, lon = o.to_latlon(e, n)
        e2, n2 = o.to_local(lat, lon)
        assert abs(e - e2) < 0.01 and abs(n - n2) < 0.01


def test_to_local_matches_haversine():
    o = geo.Origin(45.5017, -73.5673)
    lat, lon = o.to_latlon(120, -90)
    assert abs(geo.haversine_m(o.lat, o.lon, lat, lon) - 150.0) < 0.2


def test_bearing():
    assert geo.bearing_deg(0, 0, 0, 10) == pytest.approx(0)
    assert geo.bearing_deg(0, 0, 10, 0) == pytest.approx(90)
    assert geo.bearing_deg(0, 0, 0, -10) == pytest.approx(180)
    assert geo.bearing_deg(0, 0, -10, 0) == pytest.approx(270)


def test_wrap_deg():
    assert geo.wrap_deg(190) == -170
    assert geo.wrap_deg(-190) == 170
    assert geo.wrap_deg(180) == 180


def test_point_in_polygon():
    sq = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    assert geo.point_in_polygon(0, 0, sq)
    assert not geo.point_in_polygon(2, 0, sq)
    assert geo.polygon_area(sq) == pytest.approx(4.0)
    assert geo.polygon_centroid(sq) == pytest.approx((0, 0))
    assert geo.dist_to_polygon_edge(0, 0, sq) == pytest.approx(1.0)


def test_camera_fov():
    cam = CameraModel(1280, 720)
    assert 85 < cam.hfov_deg < 88
    assert 54 < cam.vfov_deg < 57
    assert cam.horizon_in_frame(-20)
    assert not cam.horizon_in_frame(-45)


def test_nadir_centre_pixel_is_below_drone():
    cam = CameraModel(1280, 720)
    g = cam.pixel_to_ground(cam.cx, cam.cy, 10, 20, 30, -90, 45)
    assert g == pytest.approx((10, 20), abs=1e-6)


def test_nadir_image_top_is_forward():
    cam = CameraModel(1280, 720)
    # heading north, top of image should be north of the drone
    g = cam.pixel_to_ground(cam.cx, 0, 0, 0, 30, -90, 0)
    assert g[1] > 5 and abs(g[0]) < 1e-6
    # heading east, top of image should be east
    g = cam.pixel_to_ground(cam.cx, 0, 0, 0, 30, -90, 90)
    assert g[0] > 5 and abs(g[1]) < 1e-6
    # right side of the image is to the right of heading (east when facing north)
    g = cam.pixel_to_ground(cam.width, cam.cy, 0, 0, 30, -90, 0)
    assert g[0] > 5


def test_oblique_ground_projection_roundtrip():
    cam = CameraModel(960, 540)
    for pitch in (-90, -60, -45):
        for heading in (0, 37, 200):
            for (u, v) in ((100, 100), (480, 270), (900, 500)):
                g = cam.pixel_to_ground(u, v, 5, -3, 20, pitch, heading)
                assert g is not None
                p = cam.ground_to_pixel(g[0], g[1], 5, -3, 20, pitch, heading)
                assert p == pytest.approx((u, v), abs=1e-3)


def test_gsd():
    cam = CameraModel(1280, 720)
    assert 0.04 < cam.nadir_gsd(30) < 0.05


# --- recovering the focal length from a hover ---------------------------------

def test_focal_from_round_trips_through_the_camera_model():
    """Synthesise the box a known camera would draw, then recover the camera.

    This is the number every metre figure in the writeup runs through, and the
    simulator cannot grade it (its people are 0.7 m deep boxes, 24% taller in
    frame than a real body), so a round trip against the projection itself is
    the check available.
    """
    import math

    from phantom_brain.camera import CameraModel

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from calibrate_from_hover import focal_from  # noqa: E402

    cam = CameraModel(1280, 720)
    height = 1.905
    for alt, ground in ((6.0, 10.0), (10.0, 16.0), (10.0, 26.0), (6.0, 25.0)):
        # the angle a thin upright body of this height subtends, in pixels
        span = math.atan(alt / ground) - math.atan((alt - height) / ground)
        box_h = cam.focal_px * span
        got = focal_from(box_h, alt, ground, height)
        assert got == pytest.approx(cam.focal_px, rel=1e-6), f"at {alt} m up, {ground} m out"


def test_focal_from_refuses_geometry_it_cannot_solve():
    """Below head height the head is above the horizon and the relation breaks."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from calibrate_from_hover import focal_from  # noqa: E402

    assert focal_from(40.0, 1.5, 10.0, 1.905) is None
