# Bugs found on flight 01

Six defects, found in about 25 minutes of battery. None of them were in the
flight logic. All of them were in the states around flying: connecting, arming,
handing control over, and recovering from a failure.

That is the pattern worth remembering. The simulator and the mission code were
written by the same person on the same day, so they agree with each other about
every transition. The aircraft does not.

Each entry below has the fix, and where a fix is a behaviour rather than a
one-liner, the regression test that pins it.

---

## 1. The Wi-Fi bind chose the phone's hotspot instead of the aircraft

**Symptom.** The bridge sat on `CONTROLLER: NO LINK` forever while DJI GO, on the
same phone, on the same Wi-Fi, connected to the aircraft immediately. It looked
exactly like the aircraft was off. This blocked the entire session for about 20
minutes at the start.

**Root cause.** With the phone's hotspot running there are **two** networks with
`TRANSPORT_WIFI`:

```
402  swlan0  10.183.132.194/24   LOCAL_NETWORK   <- the hotspot
403  wlan0   192.168.1.20/24     gw 192.168.1.1  <- the RC
```

`SdkManager.bindProcessToWifi()` walked `cm.allNetworks` and took the first one
with a Wi-Fi transport. The hotspot was created first, so it won, and every DJI
UDP packet went out an interface with no route to the aircraft. The logcat tell
was `UDT-JNI: stopped udp` repeating every two seconds.

Turning the bind off did not help either, because the system default route was
cellular, which is equally wrong.

**Fix.** Score the candidates instead of taking the first:

- `+100` if a link address is in `192.168.1.x` or `192.168.2.x` (the DJI ranges)
- `-200` if the interface is `swlan*` or `ap*` (a soft AP is never the aircraft)
- `+20` for `wlan0`
- `-30` if the network is internet `VALIDATED` (that is a home router, not a drone)

and refuse to bind at all if the best score is negative. The choice is logged, so
this is now diagnosable in one line instead of twenty minutes:

```
wifi candidate swlan0 10.183.132.194 score=-230
wifi candidate wlan0  fe80::b470:... score=120
bound to wlan0
```

**Why it never showed up before.** Every previous test was at a desk with no
hotspot running, so there was exactly one Wi-Fi network and taking the first one
was always right.

---

## 2. Virtual stick was left ON after an abort, killing the pilot's sticks

**The worst bug of the day.** While virtual stick is enabled the aircraft ignores
the RC sticks entirely. After one aborted run a state check showed:

```
mode=Joystick  vs=True  motors=True  alt=0.1  flying=False
```

Armed, on the ground, with a dead controller in the pilot's hands. It was live
for roughly two minutes before a routine state check caught it.

**Root cause.** I had added an early return to `finish()` so that when the script
believed the pilot had taken over it would not fight them by commanding a
landing. That reasoning is correct. The implementation returned **before**
releasing virtual stick, so in the one case where the detection was a false
positive (see bug 3), the script stood down while still holding the controls.

**Fix.** The early return still skips the landing, and now always releases
virtual stick first. Commanding nothing and holding the controls is the worst of
both worlds.

**Rule this produced.** Releasing control is not part of the shutdown sequence,
it is unconditional. Every exit path, on every abort reason, including the paths
that exist specifically to do nothing.

---

## 3. Stale telemetry read as a pilot override

**Symptom.** Immediately after a successful `enable_vs`, the first envelope check
read `virtual_stick == False` and aborted with "pilot took control", when nobody
had touched anything.

**Root cause.** The ack for `enable_vs` returns when the SDK accepts the call.
Telemetry is a separate 10 Hz stream, so for a sample or two it still carries the
previous state. The check ran inside that window.

**Fix.** After enabling, poll telemetry until the aircraft confirms
`virtual_stick == True` (up to 4 s) before any check can run, and abort clearly
if it never confirms.

**The wider lesson.** A command ack and the telemetry stream are two different
clocks. Anything that acts on "did the command take effect" has to read the
stream, not the ack.

---

## 4. `startTakeoff` is acked and then silently ignored

**Symptom.** Twice, `takeoff()` returned success and the aircraft did not move.
Altitude stayed at 0.00 for the full 25 s wait.

**Root cause.** Two distinct states, both of which the P3S answers with an ack:

1. The motors are still armed after an auto-landing. The aircraft keeps them
   spinning for a while, and a takeoff request in that window does nothing.
2. A short cooldown after landing, even with the motors off.

**Fix.** Before takeoff, if the motors are on and the aircraft is not flying,
disarm with `turnOffMotors` and wait. Then attempt takeoff up to three times,
verifying `is_flying` and altitude after each rather than trusting the ack. If it
still has not lifted, abort with a message that says what to do.

**The wider lesson, and it is the same as bug 3.** An ack means the command was
accepted, never that it worked. Every command that changes physical state needs a
verification read.

---

## 5. A CLI flag was parsed and never used, so the aircraft flew at a tree

**Symptom.** I told the pilot the next run would not move horizontally, then ran
it, and it flew a 3 m north leg toward the branch he had just taken control to
avoid. He had to take control a second time, for a hazard I had introduced.

**Root cause.** I added `--tests` to the argument parser and to the `Flight`
class, and did not pass it at the one call site:

```python
f = Flight(bc, args.alt, args.leg)      # tests silently defaults to all four
```

**Fix.** Pass it, and make the class of bug visible rather than relying on care:
the script now prints its plan before it arms, naming which legs translate.

```
plan: yaw -> vertical at 4 m
      horizontal legs: NONE, it stays over one spot
```

**The wider lesson, and it is the most important one on this page.** A safety
promise made in conversation is worth nothing. If I tell the pilot the aircraft
will not do something, the program has to state the same thing back, from its own
configuration, before it arms. Anything else is me vouching for code I have not
re-read.

---

## 6. The camera session died and the bridge kept sending black frames

**Symptom.** The detector reported 0 people across 148 consecutive frames. I
suspected the model, the confidence threshold, and the image format before
looking at an actual frame, which was entirely black.

**Root cause.** The P3S camera stopped producing H.264 (the phone log read
`video bytes: 0 KB (no H.264 from the aircraft yet)`), most likely provoked by
repeated `record_start` / `record_stop` churn as each script run toggled the
onboard recording. The decoder kept emitting its last, empty surface, so frames
arrived at the normal rate carrying nothing.

**Fix.** A disconnect and reconnect restored it in about 10 s. The real fix is to
stop treating a frame as evidence of a working camera: the bridge feed now needs
a health check that looks at pixels, not at frame count, and the flight scripts
should refuse to start a perception run against a dead feed.

**The wider lesson.** "Frames are arriving" and "the camera works" are different
claims. A silent failure that keeps the happy path's shape is worse than a crash,
and the debugging cost lands entirely on whoever is standing in a park.

---

## What these six have in common

Five of the six are the same mistake in different clothes: **trusting a signal
that means less than it appears to mean.** The first Wi-Fi network is not the
right one. An ack is not an effect. A stale sample is not a state change. A
parsed flag is not a used flag. An arriving frame is not a picture.

The sixth (virtual stick left on) is different and more serious, because it is
the only one where the failure hands a hazard to a person instead of to the
program.

None of these could have been found in the simulator as it was written, because
the simulator models the aircraft I assumed. The work in the next session is to
make it model the aircraft I met.
