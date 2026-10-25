"""Devpost gallery for Last Seen: 3:2 stills from the raw clips (no burned captions), in demo order."""
from pathlib import Path
import subprocess
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "broll"
OUT = B / "preview"
OUT.mkdir(parents=True, exist_ok=True)

SHOTS = [  # name, source, second (None for a still)
    ("1-last-seen", B / "s00-title.png", None),
    ("2-opencv-detects", B / "12-detect.mp4", 11.0),
    ("3-reid-match", B / "13-match.mp4", 6.0),
    ("4-is-this-them", B / "19-page-ask.mp4", 6.5),
    ("5-weak-match-no-question", B / "17-run1-weak.mp4", 20.0),
    ("6-follow-and-map", B / "23-follow-pins.mp4", 12.0),
    ("7-camera-check", B / "14a-markers.mp4", 1.2),
    ("8-safety-in-code", B / "27-code.mp4", 10.0),
]

PAGES = {"7-camera-check", "8-safety-in-code"}  # designed pages: letterbox in their own dark ground
CROP = {"2-opencv-detects": 0.0, "3-reid-match": 1.0, "6-follow-and-map": 1.0, "7-camera-check": 0.15}

CAPTIONS = {
    "1-last-seen": "Last Seen: an 11-year-old drone searches a park for a person from one photo, and a human confirms before it follows anyone.",
    "2-opencv-detects": "OpenCV 5's DNN engine runs a YOLO person detector on the drone's own video: 41 ms a frame, 24 fps, on a laptop CPU.",
    "3-reid-match": "Each person is compared with the photo two ways: the OpenCV model zoo re-ID network and the colours of their clothes.",
    "4-is-this-them": "Run 3 found me on its first scan at 0.85 and asked. The page is served by AWS Lambda; DynamoDB keeps the answer.",
    "5-weak-match-no-question": "Run 1, with a photo taken from the ground: I never scored above 0.24, so it never asked anyone. That is the hard case.",
    "6-follow-and-map": "After a human says that's them, it follows and drops a pin every second. Run 2: 90 s, 49 pins, lock held 47%.",
    "7-camera-check": "solvePnP on the printed markers in the flight footage puts the camera 5.9 m up where the barometer said 8.0 m.",
    "8-safety-in-code": "Safety lives in code, not the prompt: no follow without a confirmed person, and the envelope checks every control tick.",
}


def crop_32(img: Image.Image, x_frac: float = 0.5) -> Image.Image:
    """Footage: crop to 3:2 rather than letterbox, so no coloured bars."""
    w, h = img.size
    cw = int(h * 1.5)
    x0 = int((w - cw) * x_frac)
    return img.crop((x0, 0, x0 + cw, h)).resize((1500, 1000), Image.LANCZOS)


def to_32(img: Image.Image) -> Image.Image:
    w, h = img.size
    W, H = 1500, 1000
    s = min(W / w, H / h)
    img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    bg = Image.new("RGB", (W, H), img.getpixel((2, 2)))
    bg.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return bg


lines = []
for name, src, t in SHOTS:
    if t is None:
        img = Image.open(src).convert("RGB")
    else:
        tmp = OUT / f"_{name}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(src), "-frames:v", "1", str(tmp)], check=True)
        img = Image.open(tmp).convert("RGB"); tmp.unlink()
    p = OUT / f"{name}.png"
    (to_32(img) if t is None or name in PAGES else crop_32(img, CROP.get(name, 0.5))).save(p, optimize=True)
    cap = CAPTIONS[name]
    assert len(cap) <= 140, (name, len(cap))
    mb = p.stat().st_size / 1e6
    print(f"{'ok ' if mb < 5 else 'BAD'} {name}  {mb:.1f} MB  caption {len(cap)}")
    lines.append(f"## {name}\n\n{cap}\n")
(OUT / "captions.md").write_text("\n".join(lines), encoding="utf-8")
