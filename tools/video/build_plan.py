"""Turn the narration transcript into the vidkit edit plan for Last Seen.

    python build_plan.py <edit-plan-from-vidkit.json> <out.json>

vidkit's first pass writes an edit plan carrying `_transcript` (word timestamps).
This script keeps that transcript and replaces the segments with the shot list
below: each beat is anchored on the first words of the sentence it belongs to,
found in the transcript, so the cut lands where the sentence starts no matter how
the take was paced. Clip ids are file stems in broll/ (made by cuts.py).

A beat with several clips splits its span in the proportions given.
"""

import json
import re
import sys
from pathlib import Path

# (anchor words at the start of the sentence, [(clip_id, weight, effect, lower_third, in_point), ...])
BEATS = [
    ("so this drone's eleven years old",
     [("01-drone-hand", 1, "none", "DJI Phantom 3 Standard, released 2015", 0)]),
    ("today i gave it one photo",
     [("02-photo", 1, "none", "", 0)]),
    ("and sent it to find me",
     [("03-takeoff-sky", 1, "none", "", 0)]),
    ("picture a search coordinator",
     [("04-empty-field", 1, "none", "Composite scenario, not a real search", 0)]),
    ("and all she's got",
     [("05-volunteer", 1, "none", "", 0)]),
    ("she's made up",
     [("06-walk-away", 1, "none", "", 0)]),
    ("it's called last seen",
     [("07-page-idle", 1, "none", "", 0)]),
    ("getting here took a few tries",
     [("08-flight01", 1, "none", "Flight 01, 25 Sept 2026", 0)]),
    ("on the second it wouldn't",
     [("09-flight02", 1, "none", "Flight 02, 30 Sept 2026", 0)]),
    ("today it did the whole thing",
     [("10-hands-up", 1, "none", "Flight 03, 25 Oct 2026", 3.0)]),
    ("claude decides where to look",
     [("11-panel", 1, "none", "", 18.0)]),
    ("every frame runs through",
     [("12-detect", 1, "none", "Measured live on this footage, laptop CPU", 0)]),
    ("anyone it spots",
     [("13-match", 1, "none", "", 0)]),
    ("then the camera model",
     [("14-ground", 0.6, "none", "", 0), ("15-map-pins", 0.4, "none", "", 0)]),
    ("on my first run",
     [("16-ground-ref", 1, "none", "Run 1, 12:05", 0)]),
    ("from the air it only scored",
     [("17-run1-weak", 0.62, "none", "Run 1: best match 0.24, no question asked", 0), ("18-page-search", 0.38, "none", "", 0)]),
    ("on the third run",
     [("19-page-ask", 1, "none", "Run 3, 12:27: match 0.85 on the first scan", 0)]),
    ("and then it does the part",
     [("20-phone-ask", 1, "none", "", 0)]),
    ("that's them",
     [("20-phone-ask", 1, "none", "", "continue")]),
    ("now it follows me",
     [("22-follow-split", 1, "none", "Same 15 seconds, from the ground and from the air", 0)]),
    ("in one run it stayed",
     [("23-pins", 0.55, "none", "Run 2, 12:13: 90 s, 49 pins, lock held 47%", 0), ("24-summary", 0.45, "none", "", 0)]),
    ("your answer goes through aws",
     [("25-arch", 0.62, "none", "", 0), ("26-terminal", 0.38, "none", "", 0)]),
    ("the safety's in the code",
     [("27-code", 1, "none", "", 0)]),
    ("and when the battery got low",
     [("28-landing", 1, "none", "Run 3, 12:30: battery 18%, landed on its own", 0)]),
    ("and if anything ever looks wrong",
     [("29-switch", 1, "none", "", 0)]),
    ("last seen one photo in",
     [("30-final", 1, "none", "", 0)]),
]

END_CARD = ("s99-end", 4.0)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9' ]+", " ", s.lower()).strip()


def find_anchor(words: list[dict], anchor: str, start_from: int) -> int | None:
    toks = norm(anchor).split()
    seq = [norm(w["word"]) for w in words]
    n = len(toks)
    best = None
    for i in range(start_from, len(words) - n + 1):
        window = seq[i:i + n]
        hits = sum(1 for a, b in zip(toks, window) if a == b or (len(a) > 3 and len(b) > 3 and (a.startswith(b[:4]) or b.startswith(a[:4]))))
        if hits >= max(2, n - 1):
            return i
        if best is None and hits >= max(2, n - 2):
            best = i
    return best


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    plan = json.loads(src.read_text(encoding="utf-8"))
    tr = plan["_transcript"]
    words = [w for seg in tr["segments"] for w in seg.get("words", [])]
    starts = []
    cursor = 0
    for anchor, _ in BEATS:
        i = find_anchor(words, anchor, cursor)
        if i is None:
            print(f"  ANCHOR NOT FOUND: {anchor!r} (after word {cursor})")
            starts.append(None)
            continue
        starts.append(words[i]["start"])
        cursor = i + 1
    audio_end = words[-1]["end"]
    segments = []
    for k, ((anchor, clips), t0) in enumerate(zip(BEATS, starts)):
        if t0 is None:
            continue
        t1 = next((s for s in starts[k + 1:] if s is not None), audio_end)
        if k == 0:
            t0 = 0.0
        span = t1 - t0
        wsum = sum(c[1] for c in clips)
        t = t0
        for cid, w, effect, lt, inp in clips:
            d = span * w / wsum
            seg = {"clip_id": cid, "start_time": round(t, 3), "end_time": round(t + d, 3), "lower_third": lt, "effect": effect}
            if inp == "continue":          # carry straight on from the previous segment of the same clip
                prev = segments[-1]
                inp = round(prev.get("in_point", 0) + prev["end_time"] - prev["start_time"], 3)
            if inp:
                seg["in_point"] = inp
            segments.append(seg)
            t += d
    # hold the final shot a beat after the last word, then his hand over the lens, then the end card
    segments[-1]["end_time"] = round(audio_end + 0.5, 3)
    t = segments[-1]["end_time"]
    segments.append({"clip_id": "31-cover", "start_time": t, "end_time": round(t + 4.1, 3), "lower_third": "", "effect": "none"})
    t = segments[-1]["end_time"]
    segments.append({"clip_id": END_CARD[0], "start_time": t, "end_time": round(t + END_CARD[1], 3), "lower_third": "", "effect": "none"})
    plan.update({
        "project_name": "Last Seen",
        "segments": segments,
        "add_intro_card": True, "intro_clip": "s00-title", "intro_duration": 3.5, "intro_effect": "none",
        "add_outro_card": False,
        "theme": {
            "palette": {"bg": "#111A10", "accent": "#FF4D86", "text": "#EEF1E6", "text2": "#A9B59F"},
            "lower_third": {"font": str(Path(__file__).resolve().parents[2] / "tools/video/fonts/RedHatText[wght].ttf"),
                            "size": 30, "color": "#EEF1E6", "box_color": "0x111A10@0.86", "pad": 14, "x": "56", "y": "h-96"},
            "captions": {"font": "Segoe UI", "size": 54, "spoken": "EEF1E6", "active": "FF4D86", "outline": "111A10", "back": "A0101A11", "outline_w": 4, "shadow": 2, "margin_v": 112, "words_per_line": 6},
        },
    })
    out.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    for s in segments:
        print(f"  {s['start_time']:7.2f} - {s['end_time']:7.2f}  {s['clip_id']:18s} {s['lower_third']}")
    print(f"written {out}, {len(segments)} segments, audio ends {audio_end:.1f}s")


if __name__ == "__main__":
    main()
