"""Task 3, Navigate: plan collision-free paths on the occupancy grid and fly them.

  astar()            grid path between two ENU points
  simplify()         drop waypoints that a straight line already covers
  coverage_path()    lawnmower search legs, re-routed around obstacle cells
  WaypointFollower   turns (position, waypoint list) into velocity commands
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Sequence

from .. import geo
from ..control import Limits, altitude_hold, limit, toward, yaw_toward
from ..protocol import Telemetry, VelocityCommand
from .reconstruct import FREE, OBSTACLE, UNKNOWN, OccupancyGrid, lawnmower

_NEIGHBOURS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
               (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))]


def astar(grid: OccupancyGrid, start: tuple[float, float], goal: tuple[float, float],
          unknown_is_free: bool = True) -> list[tuple[float, float]] | None:
    """A* over 8-connected cells. Returns ENU waypoints (cell centres) or None."""
    sc = grid.to_cell(*start)
    gc = grid.to_cell(*goal)
    if not grid.in_bounds(*sc) or not grid.in_bounds(*gc):
        return None
    if not grid.is_free(*gc, unknown_is_free):
        gc = _nearest_free(grid, gc, unknown_is_free)
        if gc is None:
            return None
    if not grid.is_free(*sc, unknown_is_free):
        sc = _nearest_free(grid, sc, unknown_is_free) or sc

    def h(c: tuple[int, int]) -> float:
        return math.hypot(c[0] - gc[0], c[1] - gc[1])

    open_heap: list[tuple[float, int, tuple[int, int]]] = [(h(sc), 0, sc)]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    g_cost = {sc: 0.0}
    closed: set[tuple[int, int]] = set()
    counter = 0
    while open_heap:
        _, _, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        if cur == gc:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()
            return [grid.to_enu(c, r) for (c, r) in path]
        closed.add(cur)
        for dc, dr, cost in _NEIGHBOURS:
            nb = (cur[0] + dc, cur[1] + dr)
            if not grid.is_free(*nb, unknown_is_free):
                continue
            if dc and dr:
                # no corner cutting through obstacles
                if not grid.is_free(cur[0] + dc, cur[1], unknown_is_free) or \
                   not grid.is_free(cur[0], cur[1] + dr, unknown_is_free):
                    continue
            ng = g_cost[cur] + cost
            if ng < g_cost.get(nb, float("inf")):
                g_cost[nb] = ng
                came[nb] = cur
                counter += 1
                heapq.heappush(open_heap, (ng + h(nb), counter, nb))
    return None


def _nearest_free(grid: OccupancyGrid, c: tuple[int, int], unknown_is_free: bool,
                  max_r: int = 30) -> tuple[int, int] | None:
    for r in range(1, max_r):
        for dc in range(-r, r + 1):
            for dr in (-r, r):
                if grid.is_free(c[0] + dc, c[1] + dr, unknown_is_free):
                    return c[0] + dc, c[1] + dr
        for dr in range(-r + 1, r):
            for dc in (-r, r):
                if grid.is_free(c[0] + dc, c[1] + dr, unknown_is_free):
                    return c[0] + dc, c[1] + dr
    return None


def line_free(grid: OccupancyGrid, a: tuple[float, float], b: tuple[float, float],
              unknown_is_free: bool = True) -> bool:
    d = geo.dist(a, b)
    steps = max(1, int(d / (grid.cell_m * 0.5)))
    for i in range(steps + 1):
        t = i / steps
        e = a[0] + t * (b[0] - a[0])
        n = a[1] + t * (b[1] - a[1])
        if not grid.is_free(*grid.to_cell(e, n), unknown_is_free):
            return False
    return True


def simplify(grid: OccupancyGrid, path: Sequence[tuple[float, float]],
             unknown_is_free: bool = True) -> list[tuple[float, float]]:
    if len(path) <= 2:
        return list(path)
    out = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not line_free(grid, path[i], path[j], unknown_is_free):
            j -= 1
        out.append(path[j])
        i = j
    return out


def plan(grid: OccupancyGrid, start: tuple[float, float], goal: tuple[float, float],
         safety_m: float = 3.0, poly: Sequence[tuple[float, float]] | None = None,
         margin_m: float = 0.0) -> list[tuple[float, float]] | None:
    g = grid.planning_copy(poly, safety_m, margin_m) if poly is not None else grid.inflated(safety_m)
    raw = astar(g, start, goal)
    if raw is None:
        return None
    path = simplify(g, raw)
    if path and geo.dist(path[-1], goal) > grid.cell_m:
        path.append(goal)
    return path


def coverage_path(poly: Sequence[tuple[float, float]], grid: OccupancyGrid | None,
                  spacing_m: float, start: tuple[float, float], margin_m: float = 3.0,
                  safety_m: float = 3.0) -> list[tuple[float, float]]:
    """Search legs across the field, connected with obstacle-avoiding hops.

    Legs run whichever way is longer so the drone spends its time flying
    straight and looking, not turning.
    """
    x0, y0, x1, y1 = geo.polygon_bbox(poly)
    axis = "ew" if (x1 - x0) >= (y1 - y0) else "ns"
    legs = lawnmower(poly, spacing_m, margin_m, axis)
    if not legs:
        return []
    # Start at the end of the first leg nearest to the drone.
    if geo.dist(start, legs[1]) < geo.dist(start, legs[0]):
        legs = _reverse_legs(legs)
    if grid is None:
        return legs
    # hops must stay inside the field and out of the fence margin band; the
    # legs themselves already do (lawnmower margin), so block that band with a
    # slightly smaller margin than the legs use to keep leg endpoints reachable
    g = grid.planning_copy(poly, safety_m, max(0.0, margin_m - grid.cell_m))
    out: list[tuple[float, float]] = []
    prev = start
    for wp in legs:
        if out and line_free(g, prev, wp):
            out.append(wp)
        else:
            hop = astar(g, prev, wp)
            if hop:
                out.extend(simplify(g, hop)[1:] if out else simplify(g, hop))
            else:
                out.append(wp)
        prev = wp
    return out


def _reverse_legs(legs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for i in range(0, len(legs) - 1, 2):
        out.append(legs[i + 1])
        out.append(legs[i])
    return out


@dataclass
class FollowerConfig:
    speed: float = 3.0
    arrive_m: float = 2.0
    slow_radius_m: float = 6.0
    yaw_to_course: bool = True      # face the direction of travel (camera looks ahead)
    limits: Limits = field(default_factory=Limits)


class WaypointFollower:
    """Flies a list of ENU waypoints at a fixed altitude."""

    def __init__(self, waypoints: Sequence[tuple[float, float]], altitude_m: float,
                 origin: geo.Origin, cfg: FollowerConfig | None = None):
        self.waypoints = list(waypoints)
        self.altitude = altitude_m
        self.origin = origin
        self.cfg = cfg or FollowerConfig()
        self.index = 0

    @property
    def done(self) -> bool:
        return self.index >= len(self.waypoints)

    @property
    def current(self) -> tuple[float, float] | None:
        return None if self.done else self.waypoints[self.index]

    def progress(self) -> float:
        return 1.0 if not self.waypoints else self.index / len(self.waypoints)

    def step(self, tel: Telemetry) -> VelocityCommand:
        if self.done:
            return VelocityCommand(0, 0, altitude_hold(self.altitude, tel.alt), 0)
        e, n = self.origin.to_local(tel.lat, tel.lon)
        te, tn = self.waypoints[self.index]
        vn, ve, d = toward(te, tn, e, n, self.cfg.speed, self.cfg.slow_radius_m, self.cfg.arrive_m)
        if d <= self.cfg.arrive_m:
            self.index += 1
            return self.step(tel) if not self.done else VelocityCommand(0, 0, altitude_hold(self.altitude, tel.alt), 0)
        yaw = 0.0
        if self.cfg.yaw_to_course and d > self.cfg.arrive_m:
            course = geo.bearing_deg(e, n, te, tn)
            yaw = yaw_toward(course, tel.heading)
            # slow down while pointing the wrong way so the camera sees the leg
            err = abs(geo.wrap_deg(course - tel.heading))
            if err > 60:
                vn, ve = vn * 0.3, ve * 0.3
        vu = altitude_hold(self.altitude, tel.alt)
        return limit(VelocityCommand(vn, ve, vu, yaw), self.cfg.limits)


def path_length(path: Sequence[tuple[float, float]]) -> float:
    return sum(geo.dist(path[i], path[i + 1]) for i in range(len(path) - 1))


def path_collides(grid: OccupancyGrid, path: Sequence[tuple[float, float]]) -> bool:
    return any(not line_free(grid, path[i], path[i + 1]) for i in range(len(path) - 1))
