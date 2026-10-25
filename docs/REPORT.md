# Last Seen: technical report

OpenCV AI Competition 2026, powered by AWS. Jonathan Andrei, solo entry.

Every number here is read from a file in this repository, and `python check_claims.py`
fails when a number in this report stops matching its source. Where a number was
planned and not produced, this report says "not measured" and why.

## 1. Problem and users

People who go missing on foot in open ground (a person with dementia who walks out of
a care home, a child separated in a park) are usually found by someone who happens to
look in the right place. Volunteer search teams increasingly carry a consumer drone,
but the drone is only as good as the person staring at its feed, and that person is
also flying it.

Last Seen is for the **search coordinator** (the user) and the **missing person** (the
beneficiary). The coordinator gives it one photo. It searches, finds candidates, shows
her each plausible one beside the photo, and only after a human says "that's them"
does it stay with the person and keep their position on her map while the ground
team walks in.

The demonstration subject is the author, in a public park, playing the missing person.
The coordinator in the opening story is a composite and is labelled as one.

## 2. Architecture

```
 phone (DJI SDK bridge, Kotlin)  <-- USB, WebSocket -->  laptop (Python)
   video 1280x720, telemetry 10 Hz                       perception: OpenCV 5, every frame
                                                         runtime: scan / fly_to / look_closer / follow
                                                         agent: Claude, six strict tools
                                                         envelope: ceiling, fence, battery, link, override
                                     HTTPS
 coordinator's phone  <---->  AWS: Lambda (function URL) + DynamoDB + S3   <---->  laptop
   "Is this them?"               questions, answers, pins, state;
   map, recall                   a decision is a conditional write
```

| Loop | Rate | Who |
|---|---|---|
| velocity and gimbal | 10 to 20 Hz | the runtime; never waits on anything slower |
| perception | every frame, about 7 fps from the bridge | OpenCV 5 |
| decisions | seconds | the agent, while the aircraft holds and keeps looking |
| identity | a person's tap | the coordinator, through AWS |

Safety never depends on the cloud round trip: the envelope and the confirm gate run on
the laptop.

## 3. OpenCV 5 implementation

| Module | Use | Flown on 25 Oct |
|---|---|---|
| `dnn` (new graph engine) | YOLO11n person detection, `cv2.dnn.readNet` on the ONNX export; OpenCV Model Zoo person re-identification (`person_reid_youtu_2021nov.onnx`, 768-d, cosine) | yes |
| `imgproc` | HSV clothing histograms by body band (torso 0.18 to 0.52, legs 0.52 to 0.90 of the box), `compareHist`, blob preparation | yes |
| camera model | feet of each box to a ground point from altitude, gimbal pitch and heading; the pins on the map | yes |
| image-space servo | yaw from horizontal offset, range from box height, gimbal from vertical offset; no calibration needed to follow | yes |
| `objdetect` ArUco, ChArUco | lens calibration board (7x5, 30 mm) and four 190 mm ground markers, printed and checked with a ruler | markers detected in the onboard video (5.4) |
| `calib`, `3d` | `solvePnP` (IPPE square) + `solvePnPRefineLM` on the ground markers in the flight footage for the camera's real height and pitch; `calibrateCamera` on ChArUco views | single-marker pose measured on 25 Oct footage (5.4); ChArUco calibration not flown |

Detection on OpenCV DNN matches the previous onnxruntime path to within 2 px on the
same boxes (`test_opencv_dnn_and_onnxruntime_find_the_same_people`). Detector speed on
the flight footage, measured while drawing the overlay in the video: 41 to 51 ms a
frame on the laptop CPU at 960 px, 20 to 24 fps. The bridge delivers about 7 fps, so
perception is not the bottleneck.

## 4. The agent

Claude (`claude-opus-5-5`) in a manual tool loop, one tool call at a time, with strict
schemas. It sees a candidate table (id, fused match, appearance, clothing, box height,
distance, times seen, status) and the crops, and chooses among `scan_here`, `fly_to`,
`look_closer`, `ask_coordinator`, `follow_confirmed`, `end_mission`. The executor
returns structured errors (`isError`, `errorCategory`, `isRetryable`); a weak candidate
cannot be put to the coordinator, and `follow_confirmed` on anyone not confirmed raises
`NotConfirmed` in `runtime.follow` (tested). While the model thinks, `hover_while` keeps
velocity commands streaming so the aircraft never waits on the model.

Decisions the model made on 25 October, from `trace.jsonl`:

- Run 1: full scan; two `look_closer` calls on the strongest candidates (0.07 and 0.24
  before and after); flew to two corners "in case trees blocked the view"; never asked.
- Run 2: scan; `look_closer` on P1 ("I have no crop of P1 yet to describe to the
  coordinator"); asked; followed on confirmation; wrote in its own summary "The lock was
  weak: it held only 47% of the time and lost the person 7 times."
- Run 3: scan; rejected a detection whose crop held no person; asked about P2 at 0.85;
  when no answer came in 90 s, kept covering the area and asked again; followed on
  confirmation.

## 5. Evaluation

### 5.1 Re-identification on real aerial footage

Flight 02 onboard clip (2026-09-30, 6 m up, 2.7K), sampled every 2 s: 232 person crops,
of which 8 are impostors (two posts and a passing cyclist detected as people), labelled
by eye on the published contact sheet. With three different references, every crop of
the subject scored above every impostor; the subject's median fused score was 0.934 to
0.956 and no impostor exceeded 0.219. Source: `results/reid_flight02.json`.

Those references are crops from the same flight. The ground-photo case is 5.2.

### 5.2 Finding, on 25 October

| Run | Reference | Best match of the subject | Asked | Time from airborne to asked |
|---|---|---|---|---|
| 1 | ground photo, eye level (the drone's own camera on the infield) | 0.24 | no | not applicable, recalled after 2 min 4 s |
| 2 | aerial crop (from run 1's footage) | 0.76 | yes, after a closer look | 42 s |
| 3 | aerial crop (run 2's confirmed crop) | 0.85 | yes, on the first scan | 28 s |

Source: `brain/runs/last-seen/*/trace.jsonl`. "Best match" is the best fused score of a
candidate tracked for ten or more frames. In run 1 a six-frame detection 34 m out, which
the agent judged to be a tree trunk and ignored, touched 0.34 once; the person was P5,
seen 21 times at 5 m, at 0.24. A photo taken at eye level does not match
the same person seen from 8 m up; the matcher is clothing and build from above. This is
the main unsolved problem and the next thing to fix (a view-invariant embedding, or a
reference taken from a raised position).

In runs 2 and 3 the system split the one person present into two candidate tracks ("P1"
and "P2" in run 2, "P2" and "P6" in run 3). The agent reported the second track to the
coordinator rather than hiding it. The fix is in the association rule.

### 5.3 Following, on 25 October

Run 2, `follow_confirmed` record: 90 s, 566 frames, 264 locked (47%), 7 identity drops
(the per-frame identity check below 0.30 released the lock; the lock was re-seeded only
through the matcher), 49 pins. Run 3: 64 s of following until the battery floor; the
pins of that run were lost to a bug (no save on abort), fixed the same day in
`scripts/last_seen.py` (`try/finally rt.save()`).

**Pin error against the subject's phone GPS: not measured.** The bridge app was carrying
the phone's own GNSS in telemetry, but the field laptop ran an earlier build in run 2 and
the run 3 pins were lost as above. The map in the video shows the drone's pins only.

After run 2 the identity threshold for an established follow was given hysteresis
(`FOLLOW_DROP_AT = 0.20`, seeding still needs 0.30), because dark clothing seen from
above swung per-frame scores enough to drop a confirmed person seven times in 90 s.
That change has not been flown.

### 5.4 Camera truth, from the flight footage

The four 190 mm markers (ruler-checked) were on the infield during the flights and
the onboard 2.7K video caught them in 137 sampled half-seconds of runs 1 and 2
(260 marker detections at altitude, median 50.5 px across). For each detection
`scripts/camera_truth_footage.py` solves the camera's pose from that one marker
(`solvePnP` with `SOLVEPNP_IPPE_SQUARE`, refined with `solvePnPRefineLM`): one marker of
known size needs no surveyed layout. It compares the result with the telemetry at the
same moment (clip and log aligned on their common end). Source:
`results/camera_truth_flight03.json`.

- **Height:** the camera measured itself 5.9 m above the markers where the barometer
  reported 8.0 m, a median gap of -2.0 m, and the gap is steady (interquartile
  range about 0.3 m in each run). A steady gap is a systematic error, not noise. The
  likely cause is the lens model: these numbers use the spec-sheet 94 degree field of
  view, and the 2.7K video mode crops the sensor, so the real focal length is longer and
  every distance from the camera model comes out short. A drifting barometer would show
  the same sign. The ChArUco calibration (implemented, not flown) is what separates the
  two, and until then the pins on the map carry the same scale error.
- **Pitch:** the measured pitch agrees with the gimbal's report to a median of 4.3 degrees,
  but single frames scatter widely (interquartile range -11.5 to 12.8 degrees). One 50 px
  marker seen near-overhead is not enough to pin the angle in a single frame. With
  two or more markers in a frame and a measured layout, the multi-marker solve in
  `phantom_brain/calib.py` (tested in simulation, where it found the renderer's
  unannounced gimbal clamp: commanded and reported -25.0, measured -30.7) would.

### 5.5 Ablation, OpenCV switched off

**Not measured.** The harness exists (`--agent` and `--detector` are pluggable) and the
comparison was not run before the deadline.

## 6. AWS deployment

Stack `lastseen-mission-control`, us-east-1, from `infra/lastseen.yaml`: Lambda
(python3.13, arm64) behind a function URL, DynamoDB on-demand table, private S3 bucket
with expiry and presigned reads, 14-day log group. Deployed with
`python infra/deploy.py --profile lastseen` from an IAM user scoped to this project.
Smoke-tested live before the flight: 401 without the token, question and answer through
DynamoDB, a second answer to the same question refused with 409, crops served from S3.
Teardown `python infra/teardown.py --profile lastseen` (dry run by default; sweeps every
region by tag). Teardown date: 10 November 2026.

## 7. Limitations and failure cases

Published failure cases from flights 01 and 02 are in `flights/*/BUGS.md`. From 25 October:

- The ground-photo case (5.2).
- One person split into two tracks (5.2).
- Follow lock held 47% in run 2 (5.3).
- Pins lost on abort (fixed), phone GPS not in the saved pins (5.3).
- The spec-sheet lens model reads distances about a quarter short in this video mode (5.4).
- The aircraft has no obstacle sensing; it is kept over open ground by the geofence and
  ceiling.

## 8. Responsible use

Follows only a person a human has confirmed (enforced in code, tested); a weak match is
never put to a person; no face recognition; crops are stored privately and expire; the
coordinator can recall the aircraft; the pilot's switch overrides everything; the demo
subject consented and the scenario is a labelled composite.
