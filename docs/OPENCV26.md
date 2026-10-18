# Last Seen: OpenCV AI Competition 2026 entry

Working name, easy to change. Entry for the OpenCV AI Competition 2026, powered by AWS.
Deadline **Monday October 26, 2026, 11:45 pm PT**. Judging October 27 to November 9.
Winners November 10 on OpenCV Live. Teardown date: **November 10, 2026**.

Aiming at all three prizes an entry can take: an Overall place, the Agentic Vision Award,
and (only if the Graviton work fits) Best Use of COOL.

## Pitch

Composite user, labelled as such in the writeup: Marie coordinates volunteer searches in a
rural county. It is 6:40 pm in October, a 79-year-old man with dementia walked out of his
care home forty minutes ago, the light is going, and the only aircraft on scene is a
volunteer's 2015 Phantom. She has a photo from that morning. She hands the photo to the
agent; the drone searches the field, finds a person, shows her the crop beside the photo,
and when she taps **That's him** it stays on him and keeps his position live on her map
while the ground team walks in.

Genre in three seconds: it is a search-and-rescue spotter drone, but on an 11-year-old
aircraft that never had follow-me, and the person it finds is confirmed by a human before
it does anything.

The one sentence no competitor can say: **it flew, outdoors, closed loop, on a drone
older than OpenCV 3, and every distance it claims is graded against the subject's own
phone GPS, not against its own camera.**

The demo test volunteer is me, in a park, standing where a missing person would be. The
report says exactly that. Nothing here is a real search.

## Why this, for this panel

Carried over from the Onspec brief (`Time4/OpenCV/BRIEF.md`), which did the judge research:

- **Gary Bradski** is geometry and robotics: Stanley's vision team, ORB, Industrial
  Perception. Every past OpenCV grand prize was a physical system doing a physical thing
  (a duckweed boat on Lake Titicaca, a laser scanner, a NICU monitor, an autonomous
  wheelchair, an educational robot; 2023's top prize was a four-legged sprinkler robot,
  2025's was a live bin-picking robot). This is a physical closed loop.
- **Technical execution is 30%, defined as "correctness and depth of the OpenCV 5
  implementation."** Onspec's own warning applies to the drone as it stood on 2026-10-18:
  *a YOLO-plus-chatbot entry cannot score here.* The fix is below: calibration, pose and
  geometry from OpenCV 5's own modules, solving the project's real unsolved problem.
- Organiser's suggested areas, verbatim: "active perception for autonomous inspection with
  agentic orchestration", "healthcare, safety ... environmental monitoring". The Agentic
  Vision rule ("the visual evidence must change what the system does next") is the
  `chase` loop plus the look-closer step plus the human confirmation.

## The OpenCV 5 core (what makes it not YOLO-plus-chatbot)

| Module | Used for | Status |
|---|---|---|
| `calib` | ChArUco intrinsics of the P3S camera through the bridge feed, at home, props off | port from Onspec |
| `objdetect` (ArUco) | ground markers at surveyed spots | port from Onspec |
| `3d` | `solvePnP` on ground markers: the camera's **real** pitch and height, to settle flight 02's -23 vs -37 deg; `findHomography` ground plane | port from Onspec |
| `dnn` (new engine) | YOLO11n person detection, replacing onnxruntime | to do |
| `video` | tracker between detections, so the 8 fps feed is not the control rate ceiling | to do |
| imgproc | HSV clothing histograms for re-identification against the photo | exists |
| `features2d` | ORB keyframe localisation | exists |
| image-space servo | yaw, distance by apparent size, gimbal tilt; no calibration needed to fly | exists, 130 tests |

Calibration is not needed to FOLLOW (that is the point of the image-space servo). It is
needed to say WHERE the person is, which is the pin Marie walks to. Both halves are in.

## The agent (Agentic Vision Award)

Perception-decision-action, with the LLM kept out of the fast loop on purpose:

- **OpenCV at frame rate** flies: search pattern, detection, tracking, servo.
- **The agent at decision rate** chooses: where to search next, whether a candidate is
  worth a closer look (descend, approach, re-image: active perception), when to ask Marie,
  when to give up an area.
- **A human confirms identity** before any follow begins. That refusal is the demo moment.
- **Deterministic envelope** under everything: geofence, ceiling, speed, battery, the
  pixel-space too-close rule, pilot override on S1. The agent cannot widen any of them.

Model: Claude. Bedrock is blocked on the account as of 2026-10-18 ("Access to Bedrock
models is not allowed for this account"), so the agent is written provider-pluggable and
runs on the Anthropic API until that clears.

## AWS (meaningful, and it does not fly the aircraft)

Safety must never depend on a cloud round trip, and the report says so. The cloud is where
Marie is: she is not standing next to the pilot.

- Mission control web endpoint: map, drone, search coverage, candidate crop beside the
  reference photo, **That's him / Not him**, recall button. Serverless.
- Event and telemetry ledger (DynamoDB), frames and crops (S3, expiring), metrics and alarms
  (CloudWatch), the confirm decision travelling back to the field laptop.
- Post-flight evaluation runs in AWS against the uploaded logs and publishes the results page.
- Stretch: field mosaic on Graviton with COOL (Best Use of COOL Award).

Credentials on this machine are a scoped user for another project, with no CloudFormation
rights. Needs a project profile, `--profile lastseen`, never `default` (rule 96).

## Headline numbers, named before the code, with their graders

| Number | Unit | Graded by |
|---|---|---|
| Pin error: where the drone says the person is vs where they are | metres | the subject's own phone GPS (Android location), not the drone's camera |
| Time to find, takeoff to confirmed | minutes | flight log clock and the area searched in m² |
| Distance held while following | metres | phone GPS vs aircraft GPS |
| Camera truth: reported gimbal pitch vs measured | degrees | `solvePnP` on printed markers whose size is checked with a ruler |
| **Ablation: OpenCV off.** Claude alone looking at frames | seconds per decision, follow error | wall clock; the same follow replayed at that latency |

The ablation is the number the OpenCV Foundation cannot generate for itself: Andon Labs
measured frontier models flying a drone end to end at 2.8%. Claude decides; OpenCV flies.

## Gate result

| Check | Result |
|---|---|
| 1. Centre of the ring | "see, reason, and act", Physical AI emphasis, every past grand prize physical. A drone acting on what it sees. Pass. |
| 2. Personal traps | Maker side: the drone acts and the coordinator ends holding a confirmed position. Subject is a person, but one who wants to be found, and nothing happens without a human confirming. Genre nameable. Pass, with the responsible-use section doing real work. |
| 3. Served, not supervised | Beneficiary: the missing person. They get found and kept in sight. Pass. |
| 4. Pitch sentence | Named composite person in a moment, statistic second. Survives without naming the drone. Pass. |
| 5. Headline and grader | Phone GPS and printed geometry, not my code. Pass. |
| 6. Real, on the sponsor's surface, ablation | Real flights; OpenCV 5 surface now eight modules; AWS where the coordinator is; ablation with OpenCV off. Pass once built. |
| 7. Crowd | Gallery unpublished (checked 2026-10-18, zero items). Market proxy fires: drone SAR person detection has funded products, so assume several entries analysing drone datasets. Differentiator: it flies, closed loop, outdoors, graded by GPS. Re-check the gallery at T-48h. |
| 8. Whose budget | OpenCV Foundation: "classical CV still matters in the agent era," with a number. AWS: serverless mission control plus (if unblocked) Bedrock. Pass. |

Verdict: **GO**, conditional on flights this week.

## Build order (un-fakeable work first)

| Day | Me (code) | Jonathan (hardware) |
|---|---|---|
| Sun 18 | brief; `cv2.dnn` detector; ChArUco + ArUco tools; phone GPS into telemetry | print the ChArUco board and four markers |
| Mon 19 | agent layer; mission script; confirm flow | **at home, props off:** ChArUco calibration through the bridge (15 min) |
| Tue 20 | AWS mission control | **Flight A:** markers on the grass, hover over them (camera truth), then chase |
| Wed 21 | evaluation scripts, ablation harness, `check_claims.py` | **Flight B:** full mission, find from photo, confirm, follow, pin |
| Thu 22 | report, architecture diagram | **Flight C:** repeats for n, video footage, b-roll |
| Fri 23 | web polish, README | spare flight day |
| Sat 24 | **T-48h pre-submit gate** | record the video (team on camera) |
| Sun 25 | fixes from the gate | |
| Mon 26 | submit, screenshot, teardown reminder set for Nov 10 | |

## Responsible use (the rules can reject surveillance risk without safeguards)

Follows only a person enrolled by photo for this mission; a human confirms identity before
any follow; clothing and body, no face recognition; frames processed on the laptop, crops
uploaded to a private expiring bucket; geofence, ceiling and pilot override always on.
