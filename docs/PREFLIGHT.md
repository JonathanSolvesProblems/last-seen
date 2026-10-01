# Preflight checklist

Work through this in order. Each step is a gate for the next one.

Updated after flight 01 (2026-09-25), which flew for real. The control frame is
now verified on hardware, so the axis test below is a re-check rather than an
unknown. See `flights/2026-09-25-flight-01/` for the numbers and
`flights/2026-09-25-flight-01/BUGS.md` for the six defects it found.

## The one command

Aircraft on, remote on, phone plugged into the laptop:

```
python brain\scripts\fieldup.py
```

It finds the phone, joins the aircraft's Wi-Fi, installs the app if the phone's
copy is stale, launches it with autostart so it registers, connects and starts
the bridge with nobody touching the screen, forwards the port, waits for
telemetry **and** for the camera component (separate events on the P3S, often
minutes apart), then runs `preflight_check.py` and prints READY or NOT READY.

`preflight_check.py` on its own still works when the link is already up. It
checks the link, GPS, home point, battery, mode switch, **whether the video feed
is carrying pixels**, the detector, the gimbal and SD recording. If the feed is
black it cycles the aircraft connection and retries once, which is the fix for
the camera-session death, and it says so rather than retrying silently.

Flight 01 discovered four of these the hard way, one at a time, standing in a
park. Do not skip it because the app looks fine: the app looked fine during all
four.

Never drive the app with `adb shell input tap`. The coordinates are layout
dependent, and a mis-tap next to a powered aircraft is the wrong failure mode.
Use the autostart intent, which is what `fieldup.py` does.

## Following a person

Use the `chase` stage. It steers from the picture alone:

```
python brain\scripts\follow_me.py --stage chase --alt 6 --hold 90 --speed 1 --max-radius 20
```

Horizontal box offset drives yaw, box HEIGHT in pixels drives forward and back.
Holding a constant apparent size holds a constant distance, and that is true
whatever the lens, the gimbal angle or the altitude happen to be. Flight 02
proved the geometric alternative wrong by 5.8 m, so **do not go back to steering
by the ground projection** until the focal length and the reported gimbal angle
are both measured and agree.

The distance is set by standing at it. **Stand still until it says it has locked
your size**: it takes the median of the first six clean frames, about a second,
rather than pinning the whole flight to one box. A single detection is good to
about 20% at follow range, and in the simulator a one-frame lock set the standoff
8 m away from where I was actually standing.

The camera tracks you too. The gimbal used to be aimed once at takeoff and left
there, which is right for exactly one distance, so walking away pushed you up and
out of the top of the shot: 296 of 785 tracked frames had you clipped by the frame
edge. It now re-aims to keep you vertically centred, in steps small enough to keep
the footage watchable, and closes that loop on the angle the gimbal **reports**
rather than the angle last asked for.

Guards, none of which need calibration: it turns before it moves, a box 1.5x the
target size counts as too close and only backing away is allowed, no detection
means no motion, and the GPS leash still applies. One more: **a box running off
the top or bottom edge is refused, not filtered.** A truncated box is shorter than
you are, which reads as further away, which would ask the aircraft to fly toward
someone who might in fact be too close. That is the only measurement error in the
design that points at a person.

If it loses you it rotates to look, **toward the side you left by**. Sweeping the
other way costs most of a full circle at 12 deg/s before the camera comes back
round to you.

## Getting airborne when auto-takeoff refuses

Flight 02: five attempts, command accepted, motors never turned, every fault
channel clean. Manual CSC failed in F and worked in P. Root cause unknown.

The working path, which produced all of that day's data:

1. S1 to the **TOP (P)**, CSC to start the motors
2. Throttle up to about 3 m, steady hover
3. S1 down to **F**
4. Run the script; it takes over in the air

`takeoff_and_take_control` already handles an aircraft that is already flying.

## The mode switch, S1

Confirmed on this controller, 2026-09-25:

| position | mode | use |
|---|---|---|
| **TOP** | P (P-GPS) | normal flight, and where to put it to take control back |
| MIDDLE | A (Atti) | no GPS hold, drifts with wind. **Do not park it here** |
| **BOTTOM** | F | required for virtual stick. Where it lives while code is flying |

F does nothing until **Multiple Flight Mode** is enabled, and it ships off. The
bridge now turns it on over the SDK (`multi_mode`), so DJI GO is not needed for
this any more. `preflight_check.py` does it for you.

To take control mid-flight, sweep S1 all the way to the **top**, not to the
middle. It passes through A for an instant, which is fine.

## 0. Before leaving the house

- [x] Drone registered with Transport Canada (done); registration number visible on the airframe
- [x] Basic pilot certificate (done): uncontrolled airspace only, so no sites inside controlled airspace
- [ ] Batteries charged: aircraft, RC, phone, laptop
- [x] DJI GO health check done 2026-09-25: firmware latest, IMU Normal, Compass Normal,
      radio Good, gimbal Normal, battery 23 C. Re-check at the site only if it complains.
- [ ] **SD card**: DJI GO says "SD Card Formatting is Recommended". Format it in DJI GO
      before leaving, and start onboard recording at the park. The bridge feed is 720p and
      compressed; the SD card holds the 2.7K footage the video actually needs.
- [ ] Only one app at a time. Fully close DJI GO before using the bridge and vice versa;
      the picture takes a few seconds to come back after a handover.
- [ ] `local.properties` has the DJI app key and the bridge APK is installed
- [ ] `python -m pytest` passes in `brain/`
- [ ] `configs/park.yaml` written: real `origin` (takeoff spot), `polygon_latlon`
      walked with the phone's GPS, altitudes chosen so `floor_alt_m` is at least
      5 m above the tallest tree in the field
- [ ] Reference photo taken in the clothes you will wear (`data/reference.jpg`),
      and `perception_test.py` confirms you score highest in a few photos from height
- [ ] `ANTHROPIC_API_KEY` set if `matcher: claude`

## 1. Site

- [ ] Open field, no bystanders within 30 m of the flight area, not within
      5.6 km of an airport or 1.9 km of a heliport, not over people or roads
- [ ] Wind under ~25 km/h. The P3S under velocity control fights wind fine, but the
      follow phase at 15 m near trees is not the place to find the limit
- [ ] Sun behind you for the camera during the follow phase if you can choose

## 2. Link

- [ ] Phone joined `PHANTOM3_xxxxxx` Wi-Fi. Mobile data can stay on.
- [ ] Bridge app: step 1 fills in by itself on launch (SDK registered; cached, no internet needed)
- [ ] Bridge app: tap **2 Connect drone**; the chain shows PHANTOM 3 STANDARD, the picture
      appears in the gate, the frame takes the scene's colour, telemetry readouts fill in
- [ ] Laptop: `adb forward tcp:8765 tcp:8765` (repeat after every USB replug)
- [ ] `python scripts\bridge_check.py` reports telemetry and ~8 fps

## 3. Telemetry sanity (drone on the ground, motors off)

Run `python scripts\keyboard_fly.py` and read the overlay:

- [ ] `sats` >= 8 and `home_set` true (wait; the P3S takes a minute for GPS)
- [ ] `heading` matches a compass app on the phone within ~10 degrees
- [ ] `alt` reads ~0
- [ ] `S1` shows the mode-switch letter derived from the aircraft's own mode string
      (P while in P-GPS / P-Atti). Flip S1 to the bottom position: the readout must
      change to **F** (mode F-GPS). The SDK on the Phantom 3 only accepts virtual stick
      in F. If it shows anything else, stop and tell me before the axis test.
- [ ] `bat` is the aircraft battery, not the RC's

## 4. Axis test

**This was done on 2026-09-25 and passed.** Commanded north moved the aircraft
N+3.50 m / E+0.02 m, and again N+3.02 m / E+0.15 m. Yaw at +30 deg/s for 3 s gave
+91 degrees. Vertical at +0.5 m/s for 3 s gave +1.6 m. `SWAP N/E AXES` stays off.

The scripted version is now the default way to run it, because it measures and
records rather than relying on someone reading an overlay:

```
python scripts\first_flight.py --dry-run                # preflight only, never arms
python scripts\first_flight.py --tests yaw,vertical     # nothing translates
python scripts\first_flight.py --tests east             # the leg still unmeasured
```

It prints its plan before it arms, including whether any leg will translate.
**Read that line out loud before saying go.** On flight 01 I told the pilot a run
would not move horizontally and it did, because a flag was parsed and never
used; the printed plan exists so that promise comes from the program.

East is the one axis never measured. Run `--tests east` early, in a direction
with 10 m of clear air, and the frame is fully covered.

The manual version below is still the fallback if the scripts are broken.
With `keyboard_fly.py` focused:

1. `T` takeoff. Drone climbs to 1.2 m and hovers.
2. `V` enable virtual stick. Overlay shows `vs=True`.
3. `R` for 6 seconds: it should climb ~6 m. `SPACE` to stop.
4. Press `Z` to zero the readout, then `W` for 2 seconds. The green line must read
   **N positive, E near zero**. `SPACE`. (DJI GO cannot run while the bridge holds
   the aircraft, so this readout, taken from the drone''s own GPS, is the witness.)
5. `Z`, then `D` for 2 seconds: must read **E positive, N near zero**. `SPACE`.
6. `E` for 2 seconds: it must yaw **clockwise** (heading increases). `SPACE`.
7. `B` twice: gimbal tilts down 20 degrees; `G` twice: back up.
8. `H` return to home. Watch it land. If it stops just above the ground and waits,
   press `C` (confirm landing).

Quitting with `ESC` hovers and then releases virtual stick, so the RC sticks work
again. That also happens if the script crashes. If you ever see "could not release
virtual stick", flip S1 out of F and the sticks come back immediately.

If W moved it east (or south), tap **Swap N/E axes** in the app and repeat. If R moved it down, stop: fix `DroneController.tick` (sign of vu) before
anything else. If yaw is reversed, fix the yaw sign there too.

Also confirm the two kill switches:
- flip the RC mode switch out of F while it hovers under virtual stick: the
  drone should hover and `vs` should drop to false; flip back to F, press `V` again
- press the red **STOP** in the app: same effect, and the whole screen cuts to black
  and white until the brain re-enables virtual stick (colour returns = brain has control)

## 5. Watchdog test (still manual)

Hovering at 10 m under virtual stick, press `W` then unplug the USB cable.
Within half a second the drone should stop moving (phone watchdog). Plug back in,
`adb forward` again, `H` to bring it home.

## 6. Perception on real footage

Fly manually with `keyboard_fly.py` at the planned search altitude and gimbal
pitch, with you and one other person walking below. The bridge run folder is not
written by keyboard_fly, so screen-record the window or save frames by hand, or
simply run `run_mission.py` with `survey.enabled: false` and `search.max_time_s: 60`
and let it search while you walk, then read `runs/<run>/frames/` and the events.

- [ ] `perception_test.py` on those frames: you are detected at that altitude
      (if not: lower `altitudes.search`, raise `yolo_imgsz` to 1280, or pitch the
      gimbal more toward the horizon, in that order)
- [ ] `tune_grass.py` on the nadir frames from the survey altitude: grass black,
      trees white; put the numbers in `GrassClassifier`

## 7. Dry run of the mission logic

- [ ] `python scripts\run_mission.py configs\park.yaml --display` against the
      **simulator** with `park.yaml` (put a fake person and the real trees in
      `sim:`); it should complete. This proves the geometry of the real field
      (polygon, altitudes, spacing) makes sense before the real drone flies it.

## 8. First autonomous flight

- [ ] `survey.enabled: true`, `follow.duration_s: 30`, `search.max_time_s: 180`
- [ ] Hands on the RC the entire time, thumb next to the mode switch
- [ ] Second person films from the ground with a phone (this is the video)
- [ ] After landing: `runs/<run>/summary.json` exists, `map.png` looks like the field

Then iterate. Every run adds a row to the scoreboard.

## Things flight 01 actually did bite with

Each of these cost real battery on 2026-09-25. Full write-ups in
`flights/2026-09-25-flight-01/BUGS.md`.

- **"CONTROLLER: NO LINK" while DJI GO connects fine, on the same phone.** The app
  bound its sockets to your phone's hotspot instead of the RC. With a hotspot
  running there are two Wi-Fi networks and the hotspot is usually first. Fixed,
  and the app now logs its choice: look for `bound to wlan0`, never `swlan0`.
  If you see `UDT-JNI: stopped udp` repeating in logcat, this is it.
- **Takeoff acked and nothing happens.** The motors stay armed for about 12 s
  after an auto-landing and takeoff is ignored the whole time. The scripts now
  disarm and retry. If you are driving manually, just wait.
- **The picture dies and the feed keeps flowing black.** Looks exactly like a
  broken detector: telemetry, gimbal and SD recording all keep working. The fix
  is a Disconnect and Connect cycle, which the brain can now do for itself
  (`bc.reconnect_product()`, about 3 s). `preflight_check.py` does it
  automatically on a black feed, and every perception script checks pixels, so
  they say "the feed is black" instead of "no people found".
- **`no camera` on a command while video is flowing.** `aircraft.camera` is null
  for a while after connect and sometimes stays null, because video arrives
  through VideoFeeder and does not need the camera component. Every camera
  command used to fail in that window. The controller now resolves the camera
  through four routes and `fieldup.py` waits for it explicitly. To see what the
  SDK thinks is attached: `await bc.components()`.
- **Do not toggle SD recording repeatedly.** Each script start/stop cycle appears
  to be what killed the camera session. One recording per sortie is safer.
- **Avoid A (middle) on S1.** Atti mode has no GPS hold. Sweep past it, do not
  stop on it.

## Things that will bite

- The P3S video feed is 720p over Wi-Fi with ~300 ms latency. The brain compensates
  by using the telemetry sample nearest the frame time, but fast yaw during search
  smears the map. Keep `search.speed` at 4 m/s or less at first.
- DJI GO and the bridge cannot both hold the connection. Close DJI GO fully.
- The Phantom's camera keeps a video session alive for about 3 minutes after a client
  vanishes. If the bridge is force-stopped or crashes while connected, the next connect
  gets telemetry but no picture until that session times out (verified 2026-09-20:
  video returned by itself 3 min later). Always leave through step 2 (**Disconnect**) before closing the app; a clean
  disconnect and reconnect brings the picture back in 5 s (verified). If the app
  crashed instead, wait the 3 minutes rather than restarting the drone.
- The phone must be on the RC hotspot (`PHANTOM3_010203`) before tapping Connect. Android
  will hop back to a network with internet if it can; the bridge binds itself to Wi-Fi
  once connected, but join the hotspot first.
- `adb forward` dies when the cable is replugged or the phone sleeps. The app keeps
  the screen on, but check `bridge_check.py` before every flight.
- Battery: the mission aborts to RTH at 30 % and lands at 15 %. A full P3S battery
  gives ~20 minutes; survey + search + follow fits once, not twice.
- The altitude floor (`fence.floor_alt_m`) is the only thing between the drone and
  the trees. Measure the tallest tree, do not guess.
