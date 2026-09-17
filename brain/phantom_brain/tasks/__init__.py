"""The five Drone-Bench tasks, adapted to an outdoor field.

  reconstruct  survey flight -> 2D occupancy map of the field (Task 1)
  localize     visual position estimate vs survey keyframes (Task 2)
  navigate     A* on the map + coverage search path (Task 3)
  detect       person detection + reference matching, see ../detect.py (Task 4)
  follow       standoff tracking of the confirmed target (Task 5)
"""
