# Devpost fields

One field per block. Paste the block contents as they are.

## Project name

```
Last Seen
```

## Tagline

```
An eleven-year-old drone finds a person from one photo, asks a human "Is this them?", and only then follows. Flown live: found on the first scan at 0.85, followed 90 s, 49 pins on the coordinator's map.
```

## Video link

```
[video link]
```

## Repository

```
[repository link]
```

## Story

```
## The moment

Picture a search coordinator at a park at dusk. A man with dementia walked away from his care home an hour ago. She has one photo from this morning and one volunteer with an old consumer drone. She is a composite; the job is real. The person the drone finds in this video is me.

## What it does

Last Seen takes one photo and a search area. OpenCV 5 does the seeing: a YOLO person detector on the new DNN engine at about 20 frames a second on a laptop CPU, the OpenCV Model Zoo re-identification network and clothing colour to compare every person with the photo, and a camera model that turns each person's feet into a point on the ground. Claude decides what to do next from what it has just seen: scan, fly to a corner, look closer, or ask. It never decides who someone is. The coordinator gets "Is this them?" on her phone, the photo beside what the drone sees, and two buttons. Only a confirmed answer reaches the follow code; that refusal is enforced in Python, not in a prompt, and it is tested. Then the drone stays with the person and drops a pin on her map every second.

## What happened on 25 October 2026

Three missions in a public park on a 2015 DJI Phantom 3 Standard.

Run 1, with a photo taken at eye level: best match 0.24 from the air, so it never asked, and I called it back. That is the right behaviour and the hard case, stated plainly in the report.

Run 2, with an aerial photo: looked closer at 0.76, asked, confirmed, followed for 90 seconds, 49 pins. The agent wrote in its own summary that the lock held only 47% of the time and it lost me seven times.

Run 3: found me on the first scan at 0.85, asked, confirmed, followed until the battery reached its floor, then the envelope aborted and the aircraft landed itself.

Every number above is read from the run files in the repository, and a script fails the build when the write-up and the data disagree.

## How it is built

Phantom 3 Standard, a Kotlin bridge app on the phone (DJI MSDK) carrying video out and stick commands in, a Python brain on the laptop (OpenCV 5 at frame rate, Claude at decision rate, a deterministic envelope on every tick), and AWS where the coordinator is: a Lambda function URL serves the page and the API, DynamoDB stores every decision with a conditional write so an answer cannot be changed, S3 holds the crops privately behind short-lived links, all from one CloudFormation stack with a one-command deploy and a one-command teardown. Safety never depends on the cloud round trip.

## What is not done

Pin error against the subject's phone GPS and the gimbal-angle check against printed markers were planned and not measured; the report says why. A photo taken at eye level does not match the same person seen from above; that is the next problem. One person was sometimes split into two tracks, and the agent reported it rather than hiding it.

## Responsible use

Clothing and build, no face recognition. Follows only a person a human confirmed. Crops expire. The coordinator can recall the aircraft. The pilot's switch overrides everything. The subject consented; the scenario is a labelled composite.
```

## Built with

```
opencv, python, claude, aws-lambda, amazon-dynamodb, amazon-s3, aws-cloudformation, kotlin, dji-mobile-sdk, playwright, ffmpeg
```

## Notes, not for pasting

- Tagline leads with the result, under 200 characters is not required on Devpost but keep it one breath.
- Video and repository links go in once they exist; nothing else in this file changes.
