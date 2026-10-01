# Flight 03 plan

The goal this time is one thing: **it follows you while you walk.**

Everything below is ordered so that if the battery dies early, the most valuable
data is already captured.

## What changed since flight 02

**Follow-me no longer uses the camera geometry.** Flight 02 proved the geometric
projection wrong by 5.8 m: converting a detection into a ground position needs
the focal length, the altitude and the gimbal angle all correct, and the gimbal
reported -23 degrees while the frames said about -37.

The new `chase` stage steers entirely from the picture:

| signal | controls |
|---|---|
| how far off-centre you are | yaw, to turn and face you |
| **how tall your box is in pixels** | forward and back, to hold distance |

If you look the same size, you are the same distance away. That is true
regardless of the lens, the gimbal, or the altitude, which is exactly why it is
trustworthy when the geometry is not.

What it does in the simulator, flown against someone actually walking rather
than standing still. Three runs of each, so these are ranges, because one run of
this is not a number:

| scenario | mean error | worst | locked |
|---|---|---|---|
| walk out 26 m and back, 1.2 m/s | 3.9 to 5.6 m | 8.3 to 21.0 m | 84 to 85% |
| walk straight at the aircraft | 3.4 to 4.7 m | 5.7 to 12.5 m | 73 to 87% |

Read those honestly before flying:

- **The worst error always lands on the turnaround**, where the aircraft has to
  reverse through its own lag. One run of the out-and-back let the gap reach
  21 m against a demonstrated 18 m.
- **Walking at the aircraft, it closed to 9.6 m of a demonstrated 20 m in the
  worst of three runs** (14.5 m in the best). The guard is "never move toward
  someone already too close", not "hold distance against someone walking at
  you". If I walk at it, I close the distance, not it. Expect that and do not
  test it at short range.
- **The gains are not tuned by measurement.** Six gain sets all landed within
  4.2 to 5.0 m mean error, so the loop is not gain-limited and flight 03 should
  not be spent fiddling with it.

**You set the distance by standing at it.** It takes the median of the first six
clean frames, about a second, and keeps you that size. No number in metres to
guess at. Stand still until it says it has locked.

**The camera now tracks you as well.** The gimbal was aimed once at takeoff and
left there, which is correct for exactly one distance, so walking away pushed you
out of the top of the shot. It re-aims to keep you centred.

Other fixes from flight 02: the app now asks Android to keep the aircraft's
Wi-Fi (it dropped three times, once airborne); an implausible battery collapse
is rejected rather than triggering an abort; `follow_me` records to the SD card;
the gimbal angle and box position are logged now so the focal length can finally
be measured.

One fix worth knowing about because it would have ruined the day silently: a
gimbal command waits for the phone to acknowledge it, and the aircraft hovers if
it goes 500 ms without a velocity command. Re-aiming the camera inside the
control loop would therefore have put a hover into the middle of every follow.
The camera is now re-aimed on a separate task that the flying never waits for.

## Before leaving

- [ ] Charge: aircraft, RC, phone, laptop. Bring the second battery.
- [ ] `python -m pytest` passes in `brain/`
- [ ] Clear the SD card after checking flight 02 is imported:
      `python brain\scripts\verify_footage.py --against D:/DCIM`
- [ ] Know your height in shoes (1.905 m on record)
- [ ] **Pick a launch spot with short grass or a hard surface.** Flight 02 lost
      twenty minutes to the camera sitting in vegetation, and tall grass is a
      bad launch surface anyway.

## On arrival

```
python brain\scripts\fieldup.py
```

Must print **READY**. It joins the Wi-Fi, starts the app, waits for telemetry
and the camera, and runs the full preflight.

**S1: TOP is P, MIDDLE is A (avoid), BOTTOM is F.**

## Getting airborne, given flight 02

**Autonomous takeoff did not work at all on flight 02** and the root cause is
still unknown: five attempts, command accepted, motors never turned, every fault
channel clean. Manual CSC failed in F and worked in P.

Try it once:

```
python brain\scripts\first_flight.py --dry-run
```

then a real takeoff. If the motors do not turn within about 20 seconds, stop
retrying and use the path that worked:

1. **S1 to the TOP (P)**
2. **CSC** to start the motors
3. **Throttle up to about 3 m**, steady hover
4. **S1 down to F**
5. Tell me, and the script takes over in the air

That handover worked twice and produced all of flight 02's data.

## 1. Calibration hovers, TWO of them, 60 seconds each

```
python brain\scripts\follow_me.py --stage find --alt 6 --hold 50 --max-radius 14 --max-alt 10
```

Stand and **stay still** while it finds you. Flight 02 got 185 of 185 frames
locked doing this. Then land, **pace out a clearly different distance**, and run
it again. Write both paced distances down.

Two, not one, because one hover produces a number with nothing to check it
against, which is exactly where flight 02 ended up: two distance estimates
disagreeing by 5.8 m with no way to tell which was wrong. Two hovers have to
agree on one focal length, and that is a test the camera can fail.

Aim for roughly 10 m and roughly 20 m. Both must have your **whole body inside
the frame** or the boxes are refused, so do not stand so close that you overflow
the shot.

Afterwards, at home:

```
python brain\scripts\calibrate_from_hover.py --height 1.905 ^
    --at brain\runs\follow-me\<near>-perception.jsonl 10 ^
    --at brain\runs\follow-me\<far>-perception.jsonl 20
```

It prints both answers and the spread. Over 10% apart means one pinhole focal
length cannot explain both hovers, so something else in the model is wrong (the
reported gimbal angle, the altitude, or my pacing) and no metre figure should be
quoted until it closes.

This does not block the follow, which is the whole point of the redesign. It is
what turns "held your apparent size" into "held 8 metres" for the video.

## 2. The follow, which is what this trip is for

```
python brain\scripts\follow_me.py --stage chase --alt 6 --hold 90 --speed 1 --max-radius 20 --max-alt 10
```

What happens:

- **It rotates until it finds you**, so it does not matter which way the
  aircraft is pointing when you hand it over. If it loses you later it sweeps
  back toward the side you left by
- It turns to face you, then **stand still for a second or two** while it takes
  the median of six clean frames and locks your apparent size
- **Walk.** Slowly at first. It turns to keep you centred, tilts the camera to
  keep you in the middle of the shot, and moves to hold your size, so walking
  away pulls it along and walking at it pushes it back
- It will not translate while you are off to one side: it turns first, then
  moves. That is deliberate, because forward is only toward you once it is
  facing you

Start at `--speed 1`. Watch a full run at walking pace before raising it.

Guards, all tested and none needing calibration:

- **Too close** is measured in pixels: a box 1.5x the target size means nearer
  than intended, and only backing away is allowed
- **A box running off the frame edge is refused.** Truncated means shorter than
  you are, which reads as further away, which would ask the aircraft to fly at
  someone who might be too close. It is the one measurement error in the design
  that points at a person, so it is dropped rather than filtered
- **No target, no motion.** It commands nothing rather than guessing
- **Leash** on GPS, so walking out of the area stops it rather than dragging it
  through the fence
- **Target continuity**, so a passer-by cannot steal the lock
- **S1 to the top** and it is yours, instantly

## 3. The east leg, if battery allows

```
python brain\scripts\first_flight.py --tests east
```

Never run, twice now. Read the printed plan before saying go: it names which
legs translate.

## After landing

```
python brain\scripts\new_flight.py --number 03
python brain\scripts\import_footage.py
```

plus the two-distance calibration from step 1, with both paced distances.

## What success looks like

One clip of the aircraft turning to face you and moving as you walk, plus a log
showing the apparent size held. That is the shot the video needs and the
capability the project is named for.

Everything else is a bonus. A stage that fails is material too, as flight 02's
five bugs showed.
