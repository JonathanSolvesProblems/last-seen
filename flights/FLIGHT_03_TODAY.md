# Flight 03, Sun Oct 25: the one-day version

Deadline is **tomorrow, Mon Oct 26, 11:45 pm PT (2:45 am Tue in Montreal)**. Today's
flight is the entry: one real mission that searches, asks, gets "That's them", and
follows, graded by the phone's GPS. Everything else is optional. Sunset is about
6 pm: be in the air by 4:30.

## At home before leaving (20 min, saves the day if something is broken)

1. Drone on, props OFF, RC on, phone on the RC wifi, phone on USB to the laptop.
2. `brain/.venv/Scripts/python.exe brain/scripts/fieldup.py` until READY.
3. **Internet test, the one that matters**: with the phone still on the RC wifi, turn on
   USB tethering (Settings > Connections > Mobile Hotspot and Tethering > USB tethering).
   On the laptop: `curl -sI https://api.anthropic.com | head -1` must print a line, and
   `adb devices` must still list the phone. The agent and the AWS page both need this
   in the field. If it fails, see "No internet" below.
4. Optional, 10 min: lens calibration, props off, board held in front of the camera at
   many angles and distances: `brain/.venv/Scripts/python.exe brain/scripts/calibrate_intrinsics.py`.
   Without it the mission uses the spec-sheet lens and says so.

## Bring

- Aircraft, both batteries, RC, phone, laptop (all charged), USB cable
- DSLR + tripod (the tripod matters more than the camera when you are alone)
- The 4 ground markers on cardstock, 8 pegs or stones, tape measure
- Blue Old Navy t-shirt, black trousers (unmistakable from the air in flight 02)
- A friend if possible: the decoy (different clothes) and a second pair of hands

## At the park (15 min, before props)

1. DSLR, tripod: a **full-length photo of you standing**, head to toe. Copy it to the
   laptop as `brain/data/reference_today.jpg`. That is "the photo from this morning".
2. Markers in a 2 m square, arrows north, 4 to 6 m north of takeoff. Tape-measure the
   centres and edit `brain/configs/ground_markers.json` (metres east/north of marker 20).
3. Start the laptop screen recording (Win + Alt + R) and leave it running.
4. Open the mission-control page on the laptop (URL and token in `infra/outputs.json`).

## Battery 1: the mission (this is the demo)

```
brain/.venv/Scripts/python.exe brain/scripts/last_seen.py --reference brain/data/reference_today.jpg --coordinator mission-control --mission-control <Url from infra/outputs.json> --token <token from infra/outputs.json> --follow-s 60
```

Stand 15 to 25 m away, off the drone's nose. It searches, looks closer, asks on the
page. Click **That's them**. Walk slowly for about a minute. It ends and comes home.
With a friend: they stand in the open nearer the drone; click **Not them** for them.

## Battery 2: second run and the camera check

1. Repeat the mission (n = 2 is a result, n = 1 is an anecdote).
2. If battery remains: `brain/.venv/Scripts/python.exe brain/scripts/camera_truth.py`
   (hover beside the markers, gimbal steps, measured pitch against reported).

## No internet in the field

Run the page locally instead and keep everything else the same:
`PYTHONPATH=brain brain/.venv/Scripts/python.exe -m mission_control.server --port 8080`
in one Git Bash terminal, then the mission with `--mission-control http://127.0.0.1:8080 --token devtoken`.
If the Claude API itself is unreachable, `--agent scripted` flies the same mission with
the rule-based policy. It is labelled as such everywhere and is the weaker story; use it
only to get footage, never instead of trying the real one.

## After landing

Do not format the SD card. Bring the laptop home with `runs/` intact; I import, measure,
and build the video and write-up from it tomorrow.
