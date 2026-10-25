# Last Seen: demo video narration and shot list

Recorded by Jonathan on 25 October 2026 as one take. About 3 minutes 20 seconds spoken,
under the 5:00 limit. Every number comes from a run file and is checked by
`check_claims.py`. The video is cut from this text by `tools/video/build_plan.py`:
each sentence is anchored in the transcript and gets the shot listed under it.

## Narration

So this drone's eleven years old. Today I gave it one photo of me and sent it to find me
in a park. Picture a search coordinator at dusk: a man with dementia wandered away from
his care home an hour ago, and all she's got is a photo and one volunteer with an old
drone. She's made up, but the job's real. And today the person it's looking for is me.
It's called Last Seen. Getting here took a few tries. My first flight a month ago turned
up six bugs. On the second it wouldn't even take off on its own, so I lifted it by hand
and let the code take over in the air. Today it did the whole thing itself. Claude
decides where to look, and OpenCV 5 does the detecting and matching, live, off the
drone's video. Every frame runs through OpenCV 5's new DNN engine, with a YOLO person
detector doing about 20 frames a second on just a laptop. Anyone it spots gets checked
against the photo two ways, with a re-ID network from the OpenCV model zoo and the
colours of their clothes. Then the camera model works out where their feet are on the
ground, so every person ends up as a pin on a map. On my first run I gave it a photo
taken from the ground. From the air it only scored me at zero point two four, so it
never asked, and honestly that's exactly what I want it to do. On the third run it
found me on its first scan, at zero point eight five. And then it does the part I care
about most. It stops and asks. It never decides who someone is. A person does. That's
them. Now it follows me, keeps me in frame, and drops a pin on the map every second. In
one run it stayed with me for 90 seconds and put down 49 pins, and it flagged on its own
that the lock only held 47% of the time. Your answer goes through AWS: Lambda serves the
page, DynamoDB locks in every decision so it can't be changed, and the photos stay in a
private S3 bucket, all set up with one CloudFormation command. The safety's in the code,
not the prompt. It can't follow anyone you didn't confirm, it stays inside a geofence,
and when the battery got low it landed by itself. And if anything ever looks wrong, I
flip this switch and it's mine again. Last Seen. One photo in, a person found, and
nothing happens until a human says that's them.

## Shots, in order

| Sentence starts | Picture | Source |
|---|---|---|
| So this drone's eleven years old | the aircraft handed to the lens, close on its camera | DSLR, 25 Oct |
| Today I gave it one photo | the reference print dropped on the field, tape tied on | run 3 reference, animated page |
| and sent it to find me | the drone's own view lifting off, him shrinking below | run 3 |
| Picture a search coordinator | the marker layout as a dusk time-lapse, the scenario card appearing line by line on its words (person, clock), labelled "Composite scenario" | DSLR tripod, 25 Oct |
| and all she's got | the volunteer walking out to the aircraft on the empty infield, the card's last two lines (photo, drone) | DSLR |
| She's made up | him walking away across the field | DSLR |
| It's called Last Seen | the mission page, waiting | page, recorded |
| Getting here took a few tries | drone POV along a path | flight 01, 25 Sept |
| On the second it wouldn't | aerial of him on grass | flight 02, 30 Sept |
| Today it did the whole thing itself | drone POV: controller on the ground, both hands up, walks away | run 1 |
| Claude decides where to look | drone POV with the trace typed beside it, then the Phantom Bridge app on the phone during run 3's follow | run 3 trace, phone screen |
| Every frame runs through | the real detector drawn on the real frames, ms and fps readout | run 3 frames |
| Anyone it spots | crop beside the photo, three bars: re-ID, clothing, match | run 3 frames |
| Then the camera model | the printed markers panned one by one with the camera check's result, the feet point and the ground estimate, then the map filling | phone, run 3 frames, run 2 pins |
| On my first run | run 1's ground-level reference as a print | run 1 reference |
| From the air it only scored | run 1 frames with the weak scores, then the page with no question | run 1 |
| On the third run | the page: Searching, then the question lands at 0.85 | run 3, recorded |
| And then it does the part | the phone: Is this them?, the two prints, the cursor to That's them | run 3, recorded |
| That's them | the press, the page turns to Staying with them | same recording |
| Now it follows me | split screen: DSLR of him walking and the drone's view of the same 15 s | DSLR + run 3 |
| In one run it stayed | run 2's follow from the drone with the map filling with its 49 pins as an inset, then the agent's own summary typed | run 2 |
| Your answer goes through AWS | the architecture, piece by piece, then a terminal: the deploy, a 200 from the function URL with its x-amzn-requestid, the stack's resources, the dry-run teardown | pages, real output |
| The safety's in the code | the two checks from runtime.py and pilot.py | code, read from the files |
| and when the battery got low | drone POV descending, the ABORT line | run 3 |
| And if anything ever looks wrong | his hand on the mode switch | DSLR |
| Last Seen. One photo in | him walking back carrying the drone, then his hand covering the lens, then the end card | DSLR, run 1 |
