"""Run the real perception pipeline on real drone frames and draw what it sees.

    python overlay_detect.py <clip> <in_s> <out_s> <reference.jpg> <out.mp4> [--mode detect|match|ground|score]

The same CvDnnYoloDetector (OpenCV 5 DNN) and ReIdMatcher (OpenCV model zoo re-ID +
clothing colour) that flew the mission, frame by frame, nothing pre-computed.
Draws in the brand: chalk boxes, tape-pink for a strong match, orange for weak,
Red Hat type, a dusk panel for readouts. Output 1920x1080 at the source frame rate.

Modes
  detect  boxes + per-frame inference time and fps readout ("OpenCV 5 DNN · YOLO")
  match   boxes + inset: crop beside the reference, three bars (re-ID, clothing, match)
  ground  boxes + the feet point and the ground estimate (east/north metres)
  score   boxes + a tape tag with the id and score only (clean, for the 0.85 / 0.24 beats)
"""

import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain"))
from phantom_brain.camera import CameraModel  # noqa: E402
from phantom_brain.detect import CvDnnYoloDetector  # noqa: E402
from phantom_brain.reid import ASK_AT, LOOK_AT, ReIdMatcher  # noqa: E402

FONTS = ROOT / "tools" / "video" / "fonts"
W, H = 1920, 1080
CHALK, DUSK, TAPE, ORANGE, MUTED, NAVY = (238, 241, 230), (17, 26, 16), (255, 77, 134), (230, 160, 74), (169, 181, 159), (163, 173, 216)


def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # noqa: BLE001
            pass
    return f


F_DISP = font("RedHatDisplay[wght].ttf", 34, 700)
F_BIG = font("RedHatDisplay[wght].ttf", 46, 800)
F_TXT = font("RedHatText[wght].ttf", 26, 500)
F_SML = font("RedHatText[wght].ttf", 22, 400)
F_MARK = font("PermanentMarker-Regular.ttf", 28)


def tape(draw, x, y, text, col=TAPE, ink=(42, 7, 18)):
    tw = int(F_MARK.getlength(text)) + 28
    poly = [(x, y + 3), (x + 4, y), (x + tw - 4, y + 1), (x + tw, y), (x + tw - 2, y + 20), (x + tw, y + 40),
            (x + 5, y + 38), (x, y + 40), (x + 2, y + 19)]
    draw.polygon(poly, fill=col)
    draw.text((x + 14, y + 4), text, font=F_MARK, fill=ink)


def panel(draw, box, alpha_img, radius=10):
    x0, y0, x1, y1 = box
    ov = Image.new("RGBA", alpha_img.size, (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle(box, radius, fill=DUSK + (225,))
    alpha_img.alpha_composite(ov)


def bar(draw, x, y, w, v, col, label, value_text):
    draw.text((x, y - 30), label, font=F_SML, fill=MUTED)
    draw.rounded_rectangle((x, y, x + w, y + 10), 5, fill=(44, 58, 40))
    draw.rounded_rectangle((x, y, x + int(w * max(0.0, min(1.0, v))), y + 10), 5, fill=col)
    draw.text((x + w + 14, y - 10), value_text, font=F_TXT, fill=CHALK)


def main():
    clip, t_in, t_out, ref_path, out_path = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "detect"
    det = CvDnnYoloDetector(str(ROOT / "brain" / "models" / "yolo11n.onnx"))
    matcher = ReIdMatcher(cv2.imread(ref_path))
    ref_rgb = cv2.cvtColor(cv2.imread(ref_path), cv2.COLOR_BGR2RGB)
    cap = cv2.VideoCapture(clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 29.97
    sw, sh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cam = CameraModel(sw, sh)
    cap.set(cv2.CAP_PROP_POS_MSEC, t_in * 1000)
    n = int((t_out - t_in) * fps)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", f"{fps}", "-i", "-", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                            "-pix_fmt", "yuv420p", out_path], stdin=subprocess.PIPE)
    sx, sy = W / sw, H / sh
    ema_ms, best_ms = None, None
    ref_small = Image.fromarray(ref_rgb)
    ref_small = ref_small.resize((max(60, int(ref_small.width * 200 / ref_small.height)), 200))
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        t0 = time.perf_counter()
        dets = [d for d in det.detect(frame) if d.conf > 0.35 and (d.y2 - d.y1) > 24]
        ms = (time.perf_counter() - t0) * 1000
        ema_ms = ms if ema_ms is None else 0.9 * ema_ms + 0.1 * ms
        scored = []
        for d in dets:
            crop = frame[int(max(0, d.y1)):int(d.y2), int(max(0, d.x1)):int(d.x2)]
            if crop.size == 0:
                continue
            m = matcher.explain(crop)
            scored.append((d, m, crop))
        scored.sort(key=lambda s: -s[1].fused)
        img = Image.fromarray(cv2.cvtColor(cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)).convert("RGBA")
        draw = ImageDraw.Draw(img)
        for k, (d, m, crop) in enumerate(scored):
            x0, y0, x1, y1 = d.x1 * sx, d.y1 * sy, d.x2 * sx, d.y2 * sy
            col = TAPE if m.fused >= ASK_AT else (ORANGE if m.fused >= LOOK_AT else CHALK)
            draw.rounded_rectangle((x0, y0, x1, y1), 6, outline=col, width=4)
            # corner ticks, like a viewfinder, so the box reads as an instrument rather than a sticker
            for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
                draw.line((cx, cy, cx + dx * 22, cy), fill=col, width=7)
                draw.line((cx, cy, cx, cy + dy * 22), fill=col, width=7)
            if mode in ("score", "match", "ground"):
                tape(draw, x0 - 6, y0 - 50, f"{m.fused:.2f}", col if col != CHALK else (201, 211, 190),
                     (42, 7, 18) if col == TAPE else (20, 26, 18))
            if mode == "detect":
                draw.text((x0 + 8, y1 + 8), f"person {d.conf:.2f}", font=F_SML, fill=CHALK,
                          stroke_width=2, stroke_fill=DUSK)
            if mode == "ground":
                fx, fy = d.feet[0] * sx, d.feet[1] * sy
                draw.ellipse((fx - 9, fy - 9, fx + 9, fy + 9), outline=CHALK, width=4)
                draw.line((fx, fy + 12, fx, fy + 70), fill=CHALK, width=3)
                g = cam.pixel_to_ground(*d.feet, 0.0, 0.0, 8.0, -35.0, 0.0)
                if g is not None:
                    draw.text((fx + 16, fy + 40), f"{g[0]:+.1f} m east  {g[1]:+.1f} m north", font=F_TXT, fill=CHALK,
                              stroke_width=3, stroke_fill=DUSK)
            if mode == "match" and k == 0:
                panel(draw, (W - 820, 40, W - 40, 350), img)
                draw = ImageDraw.Draw(img)
                crop_rgb = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                crop_rgb = crop_rgb.resize((max(60, int(crop_rgb.width * 200 / crop_rgb.height)), 200))
                slot = 150
                x_a, x_b = W - 796, W - 796 + slot + 16
                img.paste(crop_rgb, (x_a + (slot - crop_rgb.width) // 2, 66))
                img.paste(ref_small, (x_b + (slot - ref_small.width) // 2, 66))
                draw.text((x_a + (slot - F_SML.getlength("what it sees")) // 2, 276), "what it sees", font=F_SML, fill=MUTED)
                draw.text((x_b + (slot - F_SML.getlength("the photo")) // 2, 276), "the photo", font=F_SML, fill=MUTED)
                bx = x_b + slot + 40
                bw = (W - 64) - bx - 86
                bar(draw, bx, 108, bw, m.appearance or 0, NAVY, "re-ID network, OpenCV model zoo", f"{(m.appearance or 0):.2f}")
                bar(draw, bx, 190, bw, m.clothing, ORANGE, "clothing colour", f"{m.clothing:.2f}")
                bar(draw, bx, 272, bw, m.fused, TAPE, "match", f"{m.fused:.2f}")
        if mode == "detect":
            panel(draw, (40, 40, 620, 150), img)
            draw = ImageDraw.Draw(img)
            draw.text((64, 54), "OpenCV 5 DNN  ·  YOLO person detector", font=F_TXT, fill=CHALK)
            draw.text((64, 92), f"{ema_ms:4.0f} ms per frame   {1000 / max(ema_ms, 1):3.0f} fps   {len(dets)} person" + ("s" if len(dets) != 1 else ""),
                      font=F_TXT, fill=MUTED)
        enc.stdin.write(img.convert("RGB").tobytes())
        if i % 60 == 0:
            print(f"  {i}/{n} frames, {ema_ms:.0f} ms/frame", flush=True)
    enc.stdin.close()
    enc.wait()
    print("wrote", out_path)


if __name__ == "__main__":
    main()
