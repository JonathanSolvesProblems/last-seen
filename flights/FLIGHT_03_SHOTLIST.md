# Flight 03: everything to capture, in one trip

Fly **Thursday Oct 22**, with **Friday Oct 23 as the weather backup**. The rule I
work to: the first live success has to exist by Friday evening, because the video
and the report are built from it over the weekend and the deadline is
**Monday Oct 26, 11:45 pm PT**.

Ordered by battery. If a battery runs short, everything above the line is safe.

## Bring

- Aircraft, **both batteries charged**, RC charged, phone charged, laptop charged, USB cable
- **A second camera on a stand** (an old phone, or a friend): the ground shots of the
  drone following you are the hero b-roll and the bridge phone is busy
- The 4 printed ground markers, 8 pegs or stones to hold them flat, a **tape measure**
- The ChArUco board (only if Monday's calibration did not happen)
- If at all possible, **one friend**: they play the decoy in different clothes (or the
  coordinator holding the phone). The "Not them" tap is the best moment in the demo.
- Wear the **blue Old Navy t-shirt and black trousers**: flight 02 proved that outfit
  is unmistakable from 6 m (223 of 223 frames)

## Before the props turn (15 min)

1. **Reference photo**: a friend or a propped phone takes a **full-length photo of you
   standing**, head to toe, at the park. That is "the photo from this morning". Send it
   to the laptop.
2. **Lay the markers** in a square about 2 m on a side, arrows pointing north, 4 to 6 m
   north of the takeoff spot. **Tape-measure** the centres and say the numbers out loud
   to me (I update `configs/ground_markers.json`).
3. Start a **screen recording on the laptop** (Win + Alt + R, or OBS). Leave it running
   all session: the terminal and the mission-control page are the "app working" footage.
4. Run `fieldup.py` until READY.
5. **Ground b-roll on the second camera**: wide of the park, the drone on the grass,
   battery going in, the laptop showing READY, the markers being measured.

## Battery 1: the measurements (about 12 min)

| # | What | Command (I run it) | What it gives us |
|---|---|---|---|
| A | **Camera truth**: hover beside the markers, gimbal steps -25 / -35 / -45 / -60 | `camera_truth.py` | the real gimbal angle against what the drone reports: settles flight 02's -23 vs -37 |
| B | **Photo check**: you stand 15 m out, it looks at you with your reference photo | `check_reference.py` | the match score from a ground photo to an aerial view, the one number not yet measured |
| C | **Calibration hover 1**: stand still about 10 m out, paced | `follow_me.py --stage find` | focal length |
| D | **Calibration hover 2**: stand still about 20 m out, paced | same | the cross-check: two distances must agree |
| E | **Follow walk**: stand still till it locks, then walk 40 m out and back slowly | `follow_me.py --stage chase` | follow numbers, graded by your phone's GPS |

**Second camera rolling on E**, wide, so you and the drone are both in frame.

## Battery 2: the mission (about 12 min), this is the demo

| # | What | Notes |
|---|---|---|
| F | **Full Last Seen mission**: you stand somewhere 15 to 25 m away **behind** the drone's nose; it searches, finds you, asks; you tap **That's them** on the phone; it follows you while you walk about 60 s; it ends and comes home | the climax of the video; phone screen recording ON when you tap |
| G | **Decoy run** (only with a friend): friend stands in the open, you stand further away. It should ask about one of you; tap **Not them** for the friend, and it keeps searching until it finds you | the refusal moment: a person said no and the aircraft listened |
| H | Repeat F if battery allows | n = 2 is a result, n = 1 is an anecdote |

**Second camera rolling on F and G** from where it can see you and the drone at once.

## You on camera (5 min, any time, no flying needed)

The rules require the video to show the team. Two or three takes of each:

1. **Opening**, at the park: who you are, what this is, in two sentences.
2. **The why**: a person who goes missing in an open space is usually found by
   someone walking past; a drone that never gets tired and asks before it acts helps.
3. **Closing**: "Last Seen. A photo in, a person found, and nothing happens until a
   human says that's them."

## Automatic, you do nothing

- 2.7K onboard video to the SD card on every run
- Every frame, candidate, decision and pin in `runs/`, with your phone's GPS beside every pin
- The agent's full decision trace (what it saw, what it chose, why)

## After landing

- `new_flight.py --number 03` and `import_footage.py` (I run them)
- Do not format the SD card until I have verified the import

## Safety, unchanged

S1 to the top and it is yours. No flying over people; if anyone walks through, say so
and I hold. Cyclists on the path: same as flight 02, we wait.
