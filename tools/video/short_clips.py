"""Portrait clips for the Last Seen Short, 1080x1920, all native vertical.

Drone footage (2704x1520) gets a crop that follows the person: the same OpenCV 5
detector finds them every frame, the crop centre follows a smoothed x, and the
pink box (plus the live re-ID match score where asked) is drawn on the full-res
frame before cropping, so it stays sharp.
"""

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain"))
from phantom_brain.detect import CvDnnYoloDetector  # noqa: E402
from phantom_brain.reid import ReIdMatcher  # noqa: E402

D = Path.home() / "Videos" / "DroneProject" / "flight-03-2026-10-25"
R2 = D / "drone" / "run2_1211_found-asked-confirmed-followed-90s_DJI_0013.MOV"
R3 = D / "drone" / "run3_1226_found-0.85-followed_low-battery-landing_DJI_0016.MOV"
DS13 = next((D / "dslr").glob("13_*.MOV"))
OUT = ROOT / "broll" / "short" / "clips"
OUT.mkdir(parents=True, exist_ok=True)
FONTS = ROOT / "tools" / "video" / "fonts"
TAPE, TAPE_INK, CHALK, DUSK = (255, 77, 134), (42, 7, 18), (238, 241, 230), (17, 26, 16)
ENC = ["-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p", "-an"]


def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # noqa: BLE001
            pass
    return f


F_MARK = font("PermanentMarker-Regular.ttf", 64)


def tape(draw, x, y, text):
    tw = int(F_MARK.getlength(text)) + 50
    h = 92
    poly = [(x, y + 6), (x + 8, y), (x + tw - 8, y + 3), (x + tw, y), (x + tw - 4, y + h / 2), (x + tw, y + h),
            (x + 10, y + h - 4), (x, y + h), (x + 4, y + h / 2)]
    draw.polygon(poly, fill=TAPE)
    draw.text((x + 25, y + 8), text, font=F_MARK, fill=TAPE_INK)


def tracked(src, t0, t1, name, ref=None, score=False, center_bias=0.0):
    det = CvDnnYoloDetector(str(ROOT / "brain" / "models" / "yolo11n.onnx"))
    matcher = ReIdMatcher(cv2.imread(str(ref))) if ref else None
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
    n = int((t1 - t0) * fps)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1080x1920", "-r", f"{fps}",
                            "-i", "-", *ENC, str(OUT / f"{name}.mp4")], stdin=subprocess.PIPE)
    cx_s, shown = None, None
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        cw = int(h * 9 / 16)
        people = [d for d in det.detect(frame) if d.conf > 0.4 and (d.y2 - d.y1) > 40]
        target = max(people, key=lambda d: d.conf) if people else None
        if target is not None:
            cx = (target.x1 + target.x2) / 2 + center_bias * cw
            cx_s = cx if cx_s is None else 0.88 * cx_s + 0.12 * cx
        if cx_s is None:
            cx_s = w / 2
        x0 = int(min(max(cx_s - cw / 2, 0), w - cw))
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        dr = ImageDraw.Draw(img)
        if target is not None:
            pad = 10
            dr.rectangle((target.x1 - pad, target.y1 - pad, target.x2 + pad, target.y2 + pad), outline=TAPE, width=8)
            if score and matcher is not None:
                crop = frame[int(max(0, target.y1)):int(target.y2), int(max(0, target.x1)):int(target.x2)]
                if crop.size:
                    v = matcher.explain(crop).fused
                    shown = v if shown is None else 0.8 * shown + 0.2 * v
                    tape(dr, int(target.x1 - 20), int(target.y1 - 120), f"{shown:.2f}")
        img = img.crop((x0, 0, x0 + cw, h)).resize((1080, 1920), Image.LANCZOS)
        enc.stdin.write(img.tobytes())
    enc.stdin.close()
    enc.wait()
    print("wrote", name)


def center_crop(src, t0, t1, name, x_frac=0.5, fade_out=None):
    vf = f"crop=ih*9/16:ih:(iw-ih*9/16)*{x_frac}:0,scale=1080:1920:flags=lanczos,setsar=1"
    if fade_out:
        vf += f",fade=t=out:st={fade_out[0]}:d={fade_out[1]}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t0), "-to", str(t1), "-i", str(src), "-vf", vf, *ENC, "-r", "30", str(OUT / f"{name}.mp4")], check=True)
    print("wrote", name)


def photo_card():
    ref = Image.open(ROOT / "brain" / "runs" / "last-seen" / "20261025-122602" / "reference.jpg").convert("RGB")
    bg = Image.new("RGB", (1080, 1920), DUSK)
    d = ImageDraw.Draw(bg)
    ph = 820
    pw = int(ref.width * ph / ref.height)
    ref = ref.resize((pw, ph), Image.LANCZOS)
    card = Image.new("RGB", (pw + 36, ph + 36), (227, 232, 218))
    card.paste(ref, (18, 18))
    card = card.rotate(-2, expand=True, fillcolor=DUSK, resample=Image.BICUBIC)
    x, y = (1080 - card.width) // 2, 330
    bg.paste(card, (x, y))
    tape(d, (1080 - 420) // 2, y - 40, "THE PHOTO")
    t = font("RedHatText[wght].ttf", 44, 500)
    s = "a frame from run 1"
    d.text(((1080 - d.textlength(s, font=t)) // 2, y + card.height + 40), s, font=t, fill=(169, 181, 159))
    bg.save(OUT / "p02-photo.png")
    print("wrote p02-photo")


def phone(name, t0, t1):
    src = ROOT / "broll" / "_work" / "mc" / "ask_run3_phone.webm"
    vf = "scale=1080:-2:flags=lanczos,crop=1080:1920:0:0,setsar=1"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t0), "-to", str(t1), "-i", str(src), "-vf", vf, *ENC, "-r", "30", str(OUT / f"{name}.mp4")], check=True)
    print("wrote", name)


if __name__ == "__main__":
    which = set(sys.argv[1:])
    want = lambda k: not which or k in which
    if want("p01"): center_crop(DS13, 38.45, 40.1, "p01-drone-hand", x_frac=0.33)
    if want("p02"): photo_card()
    if want("p03"): tracked(R3, 22.0, 25.0, "p03-liftoff")
    if want("p04"): tracked(R3, 59.0, 64.6, "p04-found", ref=None, score=False)
    if want("p05"): phone("p05-ask", 6.6, 16.6)
    if want("p06"): tracked(R2, 152.0, 157.6, "p06-follow")
    if want("p07"): center_crop(DS13, 19.0, 26.0, "p07-walk-back", x_frac=0.5)
    if want("p08"): center_crop(next((D / "dslr").glob("13_*.MOV")), 0, 0.1, "_probe")
