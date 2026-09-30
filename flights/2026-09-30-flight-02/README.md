# Flight 02, 2026-09-30

Written for: me, and anyone reading the repo alongside the video.

The day the aircraft refused to arm, and the day perception finally got its
answer. Both of those are worth more than the flight plan I actually went out
with, none of which survived contact with the field.

Every number below is recomputed from the logs in `data/` by
`../analyse_flight.py`. Nothing here is typed from memory.

```
python flights/analyse_flight.py flights/2026-09-30-flight-02
```

## What was supposed to happen

Camera calibration on the ground, the east axis leg, then detection at altitude,
then the three follow stages. In order, per `../NEXT_FLIGHT.md`.

## What actually happened

| | |
|---|---|
| Attempts logged | 3 |
| Reached the air | 2 |
| Completed the sequence | 1 |
| Battery | 88% down to 67% across the logged runs |
| Max altitude | 6.4 m |
| Telemetry samples | 1028 |

**Autonomous takeoff never worked.** Not once, across five attempts and two
battery states. The command was accepted every time and the motors never
turned. Manual CSC in F mode failed too. Manual CSC in **P mode worked**, which
is the finding: on this aircraft today, arming was possible in P and impossible
in F, and F is the mode the SDK needs in order to fly.

The workaround is what produced all the data: Jonathan armed and climbed
manually in P, flipped S1 to F while hovering, and the script took over in the
air. That path worked perfectly, twice.

## The result that matters

Detection at altitude was the single biggest unknown in the project. Flight 01's
100% was a stationary aircraft looking sideways at someone a few metres away,
which says nothing about the real case.

| run | stage | frames | locked | altitude |
|---|---|---|---|---|
| 15:30:45 | watch | 415 | 3 (0.7%) | 6.0 m |
| 15:35:11 | find | 185 | **185 (100.0%)** | 6.0 m |

Both numbers are real and they are not in conflict. The first hover held its
heading and pointed at empty grass, so the detector never had a person to find;
the saved frames show an empty field. The second rotated on the spot until a
person entered frame and then stopped, and from that moment it held the lock on
**every single frame** until the link died, at a median confidence of 0.788 and
a minimum of 0.51, with zero frames clipped by the frame edge.

**Detection at 6 m works.** The thing that does not work by itself is pointing
the camera at the subject, and rotating to find them fixes it.

## The two failures worth keeping

### The phone kept dropping the aircraft's Wi-Fi

Three times: before the first bring-up, after the battery reseat, and **once in
the air**. The airborne one ended the run.

The aircraft's network has no internet, so Android treats it as a bad network
and reaps it, and the phone's own hotspot was running for the laptop at the
same time. This is the most consequential reliability defect found so far,
because it takes the aircraft out of reach mid-flight.

### A dying link produced a battery reading of 0%

Eight seconds after reporting 69%. The envelope check believed it, aborted, and
then could deliver neither the landing nor the release of virtual stick, because
the link it was reporting on had already gone. The aircraft hovered on its
watchdog and was landed manually.

No battery loses 69 points in eight seconds. The check should never have trusted
that sample.

## Calibration: still unmeasured, and now I know exactly why

The ground calibration was impossible: the P3S camera sits at grass height, so a
person 10 m away stands behind a metre of foreground grass. The saved frame
`what-the-camera-sees.jpg` shows it plainly.

Moving it into the air should have worked, and it was defeated by my own
logging. Recovering the boxes from the annotated frames (`recover_boxes.py`,
61 of 61 frames) gives two independent estimates of the same distance:

```
box height 97 px, feet row 263 of 540, altitude 6.0 m, gimbal commanded -23 deg
  from apparent size    10.1 m slant
  from ground geometry  15.9 m slant
```

Those must be the same number and they differ by 5.8 m. Reconciling them needs a
69 degree lens where this camera is specified at 94, which is not physically
possible. So the focal length is not the wrong assumption: **the gimbal pitch
is.** If the spec focal is right, the camera was actually near -37 degrees, not
the -23 it was commanded to. The frames support that independently: at -23 the
horizon would sit inside the picture, and in every frame it is above the top
edge.

I cannot close it from this flight because **I logged neither the reported
gimbal angle nor the vertical position of the detection box**, which leaves two
unknowns and one equation. Both are now logged.

This matters more than it sounds. Every distance follow-me computes runs through
that projection, so the standoff would have been wrong by tens of percent. The
translate stage never ran, which in hindsight was lucky rather than careful.

## A simulated flight wrote into the real flight logs

While analysing this day, the test suite's integration test flew `follow_me` in
the simulator and its log landed in `runs/follow-me/` beside the real runs. The
first analysis of this flight reported it as a real result: *"follow, 399 frames,
83.7% locked at 10 m, range 6.34 m."* It was a simulation.

It was caught because the timestamp was three minutes old and no such flight had
been launched. That is luck, not process. Runs now honour `DRONE_RUNS_DIR`, the
test redirects to a temp directory, and a test asserts the real directory is
untouched.

## Footage

Two clips, 2.80 GB, matched to their runs and SHA-256 verified, in
`~/Videos/DroneProject/flight-02-2026-09-30/`. The 2.8 GB one covers the
successful find run through the manual landing.

A third file on the card, a DNG dated 2013-12-31, was the camera's unset clock.
It had invented a whole flight folder in the past and renumbered the real day;
the importer now rejects timestamps that predate the project.

`follow_me.py` was not rolling the SD card at all until partway through the day,
so the first hover exists only as 720p feed frames.

## What is still unmeasured

- **The east leg.** Never ran. The control frame is still confirmed on north,
  yaw and vertical only.
- **Focal length.** See above. One hover with the new logging closes it.
- **Yaw follow and distance follow.** Never ran, and distance follow must not run
  until the projection is trusted.
- **Autonomous takeoff.** Root cause unknown. Arming in P and handing over in the
  air is a working substitute but it is not the same capability.

## The honest summary

Three hours at the park, about six minutes airborne, and one number worth
having: **100% detection at 6 m, across 185 consecutive frames.** Everything
else the day produced is a defect list, and the defect list is the more useful
half. Five of the six items would have been invisible from a desk.
