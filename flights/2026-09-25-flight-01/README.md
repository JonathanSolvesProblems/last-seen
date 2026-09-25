# Flight 01, 2026-09-25

First time any of this code touched a flying aircraft. A park in uncontrolled
airspace, DJI Phantom 3 Standard, trees on one side and water on the other, with
me on the controller ready to take it.

The goal was not to fly a mission. It was to answer the one question a simulator
can never answer: **when the code commands "north", does the aircraft go north?**
Everything else built on top of the control frame is worthless if that mapping is
wrong, and there is no way to check it indoors.

Every number below is recomputed from the logs in `data/` by `analyse.py`, and
`check_claims.py` fails if this file and the data disagree.

```
python flights/2026-09-25-flight-01/analyse.py
python flights/2026-09-25-flight-01/check_claims.py
```

## Conditions

| | |
|---|---|
| Aircraft | DJI Phantom 3 Standard, firmware current |
| Satellites | 9 to 12 throughout |
| Battery | 68% at the first connection, 43% when I stopped |
| Working altitude | 4 m, ceiling set to 12 m, fence 20 m |
| Max altitude reached | 5.7 m, during the vertical check |
| Telemetry captured | 966 samples across all attempts |

## What was measured

| check | commanded | measured | verdict |
|---|---|---|---|
| north | 1.0 m/s for 3 s | N+3.50 m, E+0.02 m | correct |
| north (repeat) | 1.0 m/s for 3 s | N+3.02 m, E+0.15 m | correct |
| yaw clockwise | +30 deg/s for 3 s | heading +91 deg | correct |
| vertical | +0.5 m/s for 3 s | +1.6 m | correct |
| climb to altitude | target 4.0 m | reached 3.9 m | correct |

**The control frame is verified. `SWAP N/E AXES` stays off.**

Two things are worth more than the pass marks. The cross-axis error on the north
legs was 0.02 m and 0.15 m against 3 m of travel, so the frame is not merely
right, it is clean. And the yaw test commanded 90 degrees and got 91, which says
the rate is trustworthy and not just the sign.

The one run that flew the whole sequence held within **1.01 m** of its takeoff
point across 309 position samples, and finished 0.57 m from where it started.
That figure includes commanded motion during the yaw and vertical checks, so it
is an upper bound on station keeping rather than a drift measurement, but it is
the number that says the closed loop works.

## Detection

Two ground tests with the detector (ONNX YOLO11n, 640 px, conf 0.35) reading the
live 720p bridge feed.

| run | frames | person found | mean confidence | mean offset from centre |
|---|---|---|---|---|
| 17:43:31 | 148 | 0 (0.0%) | n/a | n/a |
| 17:45:24 | 83 | 83 (100.0%) | 0.718 | 0.056 |

The first run found nothing because every frame was black: the aircraft's camera
session had died and the bridge was sending empty video without saying so. That
is bug 6 below, and it cost several minutes of looking for a detector fault that
did not exist. After a connect cycle the same detector found a person in every
single frame at 23 ms per frame, which is a 43 fps ceiling on this laptop against
a feed that arrives at about 7 fps. Perception is not the bottleneck.

## Attempt by attempt

The session ran 9 attempts. 4 reached the air, 1 completed the full sequence and
landed itself, and 3 ended because I took control.

| time | outcome |
|---|---|
| 17:22:49 | dry run, preflight only |
| 17:25:50 | refused: S1 in P |
| 17:30:00 | refused: S1 in P |
| 17:32:14 | took off to 1.5 m, then `enable_vs failed: The aircraft is taking off`, landed itself |
| 17:33:16 | takeoff acked and ignored, never left the ground |
| 17:35:36 | flew, north leg passed, I took control near a tree branch |
| 17:38:37 | flew, north leg passed again, I took control (my `--tests` bug, see bug 5) |
| 17:39:55 | takeoff acked and ignored again |
| 17:41:01 | **complete: takeoff, yaw, vertical, return, land. 5 of 5, no intervention** |

The three takeovers were all correct decisions. Two were obstacles I could see
and the code could not. One was my bug pointing the aircraft at a branch.

## Footage

Ten clips, 3.93 GB, matched to their runs automatically by recording stop time.
See `footage.json` in the store for the mapping and SHA-256 of each file.

```
python brain/scripts/import_footage.py --list
```

The clips worth keeping are the ones paired with `20260925-174101` (the clean
run) and `20260925-174524` (detection working). The 2 GB `DJI_0001` is mostly the
aircraft sitting on the ground while I debugged the link.

## What was not tested

Stating this plainly because the gaps matter more than the passes:

- **The east leg never ran.** East is the same ground-frame mapping as north and
  is very likely fine, but "likely" is not measured.
- **Follow never flew.** Detection was only ever tested on the ground, with the
  aircraft stationary and the subject a few metres away. Nothing is known about
  detection at 4 m altitude with a downward gimbal, which is the case that
  actually matters.
- **No feed frames were saved during any flight.** `first_flight.py` logged
  telemetry and no video, so the axis tests have numbers and no pictures.
- **Nothing ran longer than about 90 seconds**, so there is no data on thermal
  behaviour, link stability over time, or battery curve under load.

## The honest summary

Roughly 25 minutes of battery produced about 4 minutes of flight and 5 bugs. That
ratio is the real result of the day: the code was not wrong about flying, it was
wrong about all the states around flying (connecting, arming, handing over,
recovering). Those are exactly the states a simulator is written by the same
person who wrote the flight logic, and therefore exactly the states a simulator
agrees with itself about.

See [BUGS.md](BUGS.md) for each defect, its root cause, and the fix.
