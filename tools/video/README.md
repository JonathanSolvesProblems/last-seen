# How the demo video was made

Everything on screen that is not raw footage is generated from the run files by the
scripts here, so a judge can regenerate it.

| Script | What it makes |
|---|---|
| `cards.py` | title and end cards from the brand kit (wordmark, headshot) |
| `mc_record.py` | the mission-control page, served by the same `App` that runs on Lambda, fed the real state, question, crop and pins of flight 03, recorded with Playwright |
| `overlay_detect.py` | the real `CvDnnYoloDetector` and `ReIdMatcher` run frame by frame on the onboard footage, boxes, scores, fps readout and the ground estimate drawn on top |
| `decision_panel.py` | the agent's decisions from `trace.jsonl`, typed beside the drone's view at the moment they happened |
| `html_shots.py` | the architecture, the deploy and dry-run teardown (real output, token and account hidden), the two safety checks read from the source files, the agent's own end-of-mission summary |
| `extras.py` | the scenario card over the dusk time-lapse of the marker layout, the bridge app in a phone, run 2's follow with the map filling as an inset |
| `cuts.py` | every clip in the plan, cut from the catalogued footage, plus the composites (phone on the field, the synchronised split screen, the landing log line) |
| `build_plan.py` | anchors each sentence of the narration in the transcript and maps it to its shot |
| `make_video.py` | narration in, demo out: transcribe, plan, render with vidkit |
| `fix_audio.py` | one decode and one mux: 48 kHz, an inaudible floor under the cards, clean packet timing |
| `validate.sh` | checks on the rendered file: loudness, black frames, head silence, packet durations, motion per beat |

Footage is not in the repository (the three onboard clips alone are 4 GB). The
narration script and the shot order are in `docs/DEMO_SCRIPT.md`.
