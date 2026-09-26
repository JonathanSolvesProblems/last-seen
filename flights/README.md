# Flight log

One folder per day the aircraft left the ground. Each holds the raw telemetry, a
writeup whose numbers are recomputed from that telemetry, and the defects the day
produced.

The rule for this folder: **no number appears in a writeup unless a script can
rederive it from the logs sitting next to it.** Every flight folder has an
`analyse.py` that recomputes, and a `check_claims.py` that fails if the prose has
drifted from the data. The video's credibility rests on these numbers, and a
figure that was true on the day quietly stops being true after an edit.

| flight | date | outcome |
|---|---|---|
| [01](2026-09-25-flight-01/) | 2026-09-25 | first real flights. Control frame verified: north, yaw, vertical all correct. 6 bugs found, all outside the flight logic |

**Next trip: [NEXT_FLIGHT.md](NEXT_FLIGHT.md)** carries the running order, what is
still unmeasured, and the follow-me staging.

## Running the checks

```
python flights/2026-09-25-flight-01/analyse.py
python flights/2026-09-25-flight-01/check_claims.py
```

## Where the footage is

Telemetry lives here, in git. Video does not: one flight day is around 4 GB and
this repo is inside a OneDrive folder. Clips are imported to a store outside the
repo, named for the run that produced them, with a `footage.json` manifest
carrying the SHA-256 of each file.

```
python brain/scripts/import_footage.py --list      # what is on the card
python brain/scripts/import_footage.py             # copy and verify
```

Default store is `~/Videos/DroneProject`, overridable with `DRONE_FOOTAGE_DIR`.
