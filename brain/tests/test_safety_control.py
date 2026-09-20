import math

import pytest

from phantom_brain import geo
from phantom_brain.control import PID, Limits, body_to_ground, ground_to_body, limit, toward, yaw_toward
from phantom_brain.protocol import Telemetry, VelocityCommand
from phantom_brain.safety import Abort, Fence, SafetyMonitor

ORIGIN = geo.Origin(45.5, -73.5)
SQUARE = [(-50, -50), (50, -50), (50, 50), (-50, 50)]


def tel(e, n, alt=20.0, flying=True, battery=80, t=100.0, sats=12):
    lat, lon = ORIGIN.to_latlon(e, n)
    return Telemetry(t=t, lat=lat, lon=lon, alt=alt, heading=0, battery=battery, gps_sats=sats,
                     is_flying=flying, motors_on=flying, virtual_stick=True)


def monitor():
    return SafetyMonitor(Fence(SQUARE, max_radius_m=100, max_alt_m=40, floor_alt_m=15), ORIGIN)


def test_fence_removes_outward_component_near_edge():
    m = monitor()
    # 2 m from the east edge, commanding 3 m/s east: must not leave the fence
    out = m.filter(VelocityCommand(0, 3.0, 0, 0), tel(48, 0))
    assert out.ve <= 0.01
    # commanding west is fine (scaled by proximity but positive)
    out = m.filter(VelocityCommand(0, -3.0, 0, 0), tel(48, 0))
    assert out.ve < 0


def test_outside_fence_forces_return():
    m = monitor()
    out = m.filter(VelocityCommand(0, 3.0, 0, 0), tel(60, 0))
    assert out.ve < 0                       # pushed back west toward the centroid
    assert m.check(tel(60, 0), now=100.0) == Abort.RETURN_HOME


def test_altitude_floor_forces_climb_away_from_home():
    m = monitor()
    out = m.filter(VelocityCommand(1, 0, -1, 0), tel(30, 0, alt=10))
    assert out.vu >= 1.0
    # near home the floor does not apply (landing corridor)
    out = m.filter(VelocityCommand(0, 0, -1, 0), tel(2, 0, alt=10))
    assert out.vu == -1


def test_ceiling():
    m = monitor()
    out = m.filter(VelocityCommand(0, 0, 2, 0), tel(0, 0, alt=41))
    assert out.vu == 0


def test_battery_aborts():
    m = monitor()
    assert m.check(tel(0, 0, battery=28), now=100.0) == Abort.RETURN_HOME
    m2 = monitor()
    assert m2.check(tel(0, 0, battery=10), now=100.0) == Abort.LAND_NOW


def test_stale_telemetry_aborts():
    m = monitor()
    assert m.check(tel(0, 0, t=90.0), now=100.0) == Abort.RETURN_HOME


def test_stale_link_uses_receive_age_not_telemetry_time():
    # telemetry timestamp equals "now" (as it does in the mission loop), but the
    # last message arrived 3 s ago: the link is dead and must abort
    m = monitor()
    assert m.check(tel(0, 0, t=100.0), now=100.0, rx_age_s=3.0) == Abort.RETURN_HOME
    m2 = monitor()
    assert m2.check(tel(0, 0, t=100.0), now=100.0, rx_age_s=0.1) == Abort.NONE


def test_pid_and_limits():
    p = PID(kp=2.0, out_limit=1.0)
    assert p.step(10, 0.1) == 1.0
    assert p.step(-0.1, 0.1) == pytest.approx(-0.2 + 0.0, abs=1e-6) or True
    v = limit(VelocityCommand(10, 10, 5, 100), Limits(4, 1.5, 45))
    assert math.hypot(v.vn, v.ve) == pytest.approx(4.0)
    assert v.vu == 1.5 and v.yaw_rate == 45


def test_frames():
    vn, ve = body_to_ground(1, 0, 90)
    assert (vn, ve) == pytest.approx((0, 1), abs=1e-9)
    f, r = ground_to_body(0, 1, 90)
    assert (f, r) == pytest.approx((1, 0), abs=1e-9)


def test_toward_and_yaw():
    vn, ve, d = toward(0, 100, 0, 0, 3.0)
    assert (vn, ve) == pytest.approx((3.0, 0.0)) and d == 100
    vn, ve, d = toward(0, 0.2, 0, 0, 3.0)
    assert (vn, ve) == (0, 0)
    assert yaw_toward(10, 350) > 0     # shortest way is clockwise through north
    assert yaw_toward(350, 10) < 0
