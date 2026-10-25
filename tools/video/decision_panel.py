"""Drone POV with the agent's real decisions typed into a side panel.

    python decision_panel.py <run_dir> <clip> <in_s> <out_s> <out.mp4> [--speed 1.0]

Reads trace.jsonl, keeps the model turns and tool results, and types them into a
dusk panel on the right third of the frame, each line at the moment it happened
relative to the clip (clip time = wall time minus the clip's start). The POV is
scaled to 1920x1080 and the panel sits over its right edge. Lines are the trace's
own words: the tool name, its input, and the model's one-line reason when it gave one.
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "tools" / "video" / "fonts"
W, H = 1920, 1080
CHALK, DUSK, TAPE, ORANGE, MUTED, NAVY = (238, 241, 230), (17, 26, 16), (255, 77, 134), (230, 160, 74), (169, 181, 159), (163, 173, 216)

# wall-clock start of each onboard clip (from the trace against the footage)
CLIP_START = {"20261025-121053": "12:10:56", "20261025-122602": "12:25:53", "20261025-120518": "12:05:21"}


def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # noqa: BLE001
            pass
    return f


F_H = font("RedHatDisplay[wght].ttf", 30, 700)
F_T = font("RedHatText[wght].ttf", 25, 500)
F_S = font("RedHatText[wght].ttf", 22, 400)


def wrap(draw, text, f, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def events(run: str) -> list[tuple[float, str, str, tuple]]:
    """(clip_seconds, kind, text, colour)"""
    day = datetime.date(2026, 10, 25)
    h, m, s = map(int, CLIP_START[run].split(":"))
    t_clip0 = datetime.datetime.combine(day, datetime.time(h, m, s)).timestamp()
    out = []
    for l in open(ROOT / "brain" / "runs" / "last-seen" / run / "trace.jsonl"):
        r = json.loads(l)
        t = r["t"] - t_clip0
        if r.get("kind") == "model":
            for c in r.get("calls", []):
                a = c["input"]
                if c["name"] == "scan_here":
                    out.append((t, "call", f"scan_here  {a['degrees']}°", CHALK))
                elif c["name"] == "fly_to":
                    out.append((t, "call", f"fly_to  east {a['east_m']} m, north {a['north_m']} m", CHALK))
                elif c["name"] == "look_closer":
                    out.append((t, "call", f"look_closer  {a['candidate_id']}", ORANGE))
                    out.append((t + 0.4, "why", a.get("reason", ""), MUTED))
                elif c["name"] == "ask_coordinator":
                    out.append((t, "call", f"ask_coordinator  {a['candidate_id']}", TAPE))
                    out.append((t + 0.4, "why", a.get("message", ""), MUTED))
                elif c["name"] == "follow_confirmed":
                    out.append((t, "call", f"follow_confirmed  {a['candidate_id']}  {a['seconds']} s", TAPE))
                elif c["name"] == "end_mission":
                    out.append((t, "call", f"end_mission  {a['outcome']}", CHALK))
            if r.get("text") and "chair" not in r["text"][0].lower():
                out.append((t - 0.2, "say", r["text"][0], MUTED))
        elif r.get("kind") == "tool":
            rec = r.get("record") or {}
            if r["name"] == "scan_here":
                out.append((t, "res", f"→ new: {', '.join(rec.get('new', [])) or 'nobody'}", NAVY))
            elif r["name"] == "look_closer":
                out.append((t, "res", f"→ {rec.get('before')} → {rec.get('after')}", NAVY))
            elif r["name"] == "ask_coordinator":
                out.append((t, "res", f"→ coordinator: {rec.get('decision')}", TAPE if rec.get("decision") == "confirmed" else NAVY))
            elif r["name"] == "follow_confirmed":
                out.append((t, "res", f"→ {rec.get('followed_s')} s, {rec.get('pins')} pins, lock {int(100 * rec.get('locked_fraction', 0))}%", NAVY))
            elif r["name"] == "fly_to":
                out.append((t, "res", "→ arrived" + (f", new: {', '.join(rec['new'])}" if rec.get("new") else ""), NAVY))
    return sorted(out, key=lambda e: e[0])


def main():
    run, clip, t_in, t_out, out_path = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
    speed = float(sys.argv[sys.argv.index("--speed") + 1]) if "--speed" in sys.argv else 1.0
    ev = events(run)
    cap = cv2.VideoCapture(clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 29.97
    cap.set(cv2.CAP_PROP_POS_MSEC, t_in * 1000)
    n = int((t_out - t_in) * fps)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", f"{fps}",
                            "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", out_path], stdin=subprocess.PIPE)
    px, pw_ = W - 700, 640          # panel x and width
    chars_per_s = 40 * speed
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        t = t_in + i / fps
        img = Image.fromarray(cv2.cvtColor(cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)).convert("RGBA")
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        d.rounded_rectangle((px - 24, 40, W - 36, H - 40), 14, fill=DUSK + (222,))
        d.text((px, 60), "What the agent decided", font=F_H, fill=CHALK)
        d.text((px, 98), "Claude, live, from what it had just seen", font=F_S, fill=MUTED)
        y = 150
        shown = [e for e in ev if e[0] <= t and e[0] >= t_in - 40]
        # keep the latest that fit, typed in from the bottom
        lines_out = []
        for (te, kind, text, col) in shown:
            typed = int((t - te) * chars_per_s)
            txt = text[:typed] if typed < len(text) else text
            f = F_T if kind in ("call", "res") else F_S
            for ln in wrap(d, txt, f, pw_ - 10):
                lines_out.append((ln, f, col, kind))
            lines_out.append(("", F_S, col, "gap"))
        max_lines = int((H - 80 - y) / 32)
        lines_out = lines_out[-max_lines:]
        for ln, f, col, kind in lines_out:
            if kind == "gap":
                y += 10
                continue
            d.text((px + (0 if kind == "call" else 18), y), ln, font=f, fill=col)
            y += 32 if f is F_T else 28
        img.alpha_composite(ov)
        enc.stdin.write(img.convert("RGB").tobytes())
    enc.stdin.close()
    enc.wait()
    print("wrote", out_path)


if __name__ == "__main__":
    main()
