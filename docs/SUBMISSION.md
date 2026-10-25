# Devpost fields

One field per block. Paste the block contents as they are.

## Project name

```
Last Seen: Drone Search That Asks Before It Follows
```

## Tagline

```
A 2015 drone finds a person from one photo, asks a human "Is this them?", then follows. Flown live: found on the first scan at 0.85, followed 90 s, 49 pins on the map.
```

## Video link

```
https://www.youtube.com/watch?v=b4s_inmWlk8
```

## Repository

```
https://github.com/JonathanSolvesProblems/last-seen
```

## Story

```
## Inspiration

Picture a search coordinator in a park at dusk. A man with dementia walked out of his care home an hour ago. She has one photo from this morning and one volunteer with an old consumer drone. The drone is only as good as the person staring at its feed, and that person is also flying it. She is a composite; the job is real. I built Last Seen for her, and the person it finds in the demo is me.

## What it does

Last Seen takes one photo and a search area. The drone searches on its own, decides where to look and when to look closer, and when it finds someone who could be the person, it stops and asks the coordinator on her phone: "Is this them?", with her photo beside what the drone sees. It never decides who someone is. Only after a human says "That's them" does it follow, dropping a pin on her map every second. If anything looks wrong, the pilot flips a switch and the aircraft is theirs.

## How I built it

![Architecture: OpenCV 5 on the laptop, the Claude agent, the envelope, AWS](https://raw.githubusercontent.com/JonathanSolvesProblems/last-seen/master/docs/img/architecture.png)

- **Aircraft:** a 2015 DJI Phantom 3 Standard, with a Kotlin bridge app on the phone (DJI Mobile SDK) carrying the video out and the stick commands in.
- **OpenCV 5, every frame:** the new DNN engine runs a YOLO person detector at 20 to 24 fps on a laptop CPU. Each person is compared with the photo two ways: the OpenCV Model Zoo person re-identification network and HSV clothing colour by body band. A camera model turns each person's feet into a point on the ground. An image-space servo flies the follow from where the person sits in the frame.
- **Claude, every few seconds:** six strict tools (scan here, fly to, look closer, ask the coordinator, follow the confirmed person, end the mission). It sees the candidate table and the crops and chooses; the runtime keeps the aircraft holding and perceiving while it thinks. Every decision is logged in trace.jsonl.
- **AWS, where the coordinator is:** a Lambda function URL serves her page and the API, DynamoDB stores every answer with a conditional write so it cannot be changed, and the crops sit in a private S3 bucket behind short-lived links. One CloudFormation stack, one command up and one command down.
- **Safety in code, not the prompt:** the follow code raises NotConfirmed for anyone the coordinator did not confirm (tested), and a flight envelope checks battery, ceiling, geofence and link on every control tick.

![The agent loop: OpenCV output changes Claude's next tool call, from run 3's trace](https://raw.githubusercontent.com/JonathanSolvesProblems/last-seen/master/docs/img/agent-workflow.png)

## Challenges I ran into

- **The photo problem.** In run 1 I gave it a photo taken from the ground. From 8 m up it scored me at 0.24, so it never asked. That is the right behaviour and the hardest open problem: a family photo does not look like a person seen from above.
- **Following a stranger.** In simulation the system once confirmed my crop and then followed a decoy. I added an identity gate and a per-frame identity check, so the lock can only be re-seeded through the matcher.
- **An eleven-year-old aircraft.** On flight 2 it would not take off under code at all, so I lifted it by hand and let the code take over in the air. On flight 3 it did the whole thing itself.
- **Measuring honestly.** The lock in run 2 held only 47% of the time, and the agent said so in its own summary. I kept that number rather than hide it.

## Accomplishments that I'm proud of

- Three real missions in a public park on 25 October 2026. Run 3 found me on its first scan at 0.85, asked, followed, and landed itself when the battery reached its floor. Run 2 followed me for 90 seconds and put down 49 pins.
- A camera check from the flight footage itself: OpenCV's solvePnP on the printed ArUco markers puts the camera 5.9 m above them where the barometer said 8.0 m, a steady gap that points at the lens model.
- check_claims.py fails the build when any number in the README, report, video script or this page stops matching the run files.

## What I learned

The model is good at choosing what to do next and should never be the thing that keeps the aircraft safe. OpenCV does the seeing at frame rate, Claude decides at decision rate, and the safety rules are code that cannot be talked out of anything. And a weak match that does not ask is a feature, not a failure.

## What's next for Last Seen: Drone Search That Asks Before It Follows

- A view-invariant match, so an ordinary photo taken at eye level works from the air.
- Grade every pin against GPS from a phone the subject carries (the bridge already sends it; on the day the only phone stayed at the base).
- A lens calibration flown on the aircraft, to close the 2 m height gap.
- Real search-and-rescue volunteers using it on a training exercise.

Code, report and every number: https://github.com/JonathanSolvesProblems/last-seen
```

## Built with

```
opencv, python, claude, aws-lambda, amazon-dynamodb, amazon-s3, aws-cloudformation, kotlin, dji-mobile-sdk, playwright, ffmpeg
```

## Notes, not for pasting

- Tagline leads with the result, under 200 characters is not required on Devpost but keep it one breath.
- Video and repository links go in once they exist; nothing else in this file changes.
