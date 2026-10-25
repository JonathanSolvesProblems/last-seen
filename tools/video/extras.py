"""Composites for the demo that need more than a cut.

  briefing     the scenario card (person, clock, photo, drone) over the dusk time-lapse of
               the marker layout, then over the volunteer walking out to the aircraft;
               each line appears on the word it belongs to
  bridge       the Phantom Bridge app (the real phone screen recording during run 3's
               follow) in a landscape phone on the field
  follow_pins  run 2's follow from the drone, with the coordinator's map filling with
               the 49 pins as a live inset

Usage: python extras.py [briefing|bridge|follow_pins ...]
"""

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
D = Path.home() / "Videos" / "DroneProject" / "flight-03-2026-10-25"
W_ = ROOT / "broll" / "_work"
OUT = ROOT / "broll"
FONTS = ROOT / "tools" / "video" / "fonts"
ENC = ["-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p", "-an"]
FIT = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1"
DUSK = "eq=brightness=-0.07:contrast=1.06:saturation=0.85,colorbalance=rs=-0.05:gs=-0.02:bs=0.10:rm=-0.03:bm=0.05,vignette=PI/5"


def run(args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], check=True)


def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # noqa: BLE001
            pass
    return f


# ---- briefing ----------------------------------------------------------------
# word times relative to the beat start, read from the transcript
T_DEMENTIA, T_AGO, T_SWITCH, T_PHOTO, T_VOLUNTEER, T_END = 2.9, 5.46, 6.3, 7.48, 8.38, 10.8

CARD_HTML = """<!doctype html><html><head><meta charset='utf-8'><style>
@font-face { font-family: "Red Hat Display"; src: url("%(fonts)s/RedHatDisplay[wght].ttf"); font-weight: 300 900; }
@font-face { font-family: "Red Hat Text"; src: url("%(fonts)s/RedHatText[wght].ttf"); font-weight: 300 700; }
@font-face { font-family: "Permanent Marker"; src: url("%(fonts)s/PermanentMarker-Regular.ttf"); }
html, body { margin:0; width:1920px; height:1080px; background:transparent; overflow:hidden; }
.card { position:absolute; right:84px; top:84px; width:640px; padding:30px 32px 26px; border-radius:16px;
        background: rgba(17,26,16,.90); border:1px solid #2C3A28; box-shadow: 0 30px 60px -30px rgba(0,0,0,.9);
        font: 500 30px/1.35 "Red Hat Text", sans-serif; color:#EEF1E6; }
.tape { position:absolute; top:-22px; left:28px; font: 400 28px/1 "Permanent Marker", cursive; padding:13px 26px 11px 22px; background:#FF4D86; color:#2A0712;
        transform: rotate(-2.2deg); clip-path: polygon(0 8%,3% 0,97% 4%,100% 0,98% 52%,100% 100%,4% 94%,0 100%,2% 46%); }
h2 { font: 800 40px/1.1 "Red Hat Display", sans-serif; letter-spacing:-.02em; margin:12px 0 18px; }
.line { display:flex; align-items:center; gap:18px; margin:14px 0; }
.line svg { width:44px; height:44px; flex:none; stroke:#EEF1E6; stroke-width:2.4; fill:none; stroke-linecap:round; stroke-linejoin:round; }
.line b { font-weight:700; }
.muted { color:#A9B59F; }
</style></head><body>
<div class="card" id="card">
  <span class="tape">LAST SEEN 17:40</span>
  <h2>A search coordinator's evening</h2>
  <div class="line" id="l1"><svg viewBox="0 0 44 44"><circle cx="22" cy="13" r="7"/><path d="M8 40c1-9 6-14 14-14s13 5 14 14"/></svg><span>A man, 79, <b>with dementia</b></span></div>
  <div class="line" id="l2"><svg viewBox="0 0 44 44"><circle cx="22" cy="22" r="17"/><path d="M22 10v12l8 5"/></svg><span>Walked out of his care home <b>an hour ago</b></span></div>
  <div class="line" id="l3"><svg viewBox="0 0 44 44"><rect x="6" y="9" width="32" height="26" rx="3"/><circle cx="16" cy="18" r="3"/><path d="M8 33l9-9 6 6 5-5 8 8"/></svg><span><b>One photo</b>, from this morning</span></div>
  <div class="line" id="l4"><svg viewBox="0 0 44 44"><path d="M14 14l16 16M30 14L14 30"/><circle cx="10" cy="10" r="5"/><circle cx="34" cy="10" r="5"/><circle cx="10" cy="34" r="5"/><circle cx="34" cy="34" r="5"/></svg><span><b>One volunteer</b>, one 2015 drone</span></div>
</div>
<script>
const S = { card: 0.5, l1: %(t1)s, l2: %(t2)s, l3: %(t3)s, l4: %(t4)s };
const ease = p => 1 - Math.pow(1 - p, 3);
window.setT = t => {
  for (const [id, t0] of Object.entries(S)) {
    const el = document.getElementById(id); const p = Math.max(0, Math.min(1, (t - t0) / 0.45));
    el.style.opacity = ease(p); el.style.transform = id === 'card' ? `translateX(${(1 - ease(p)) * 40}px)` : `translateY(${(1 - ease(p)) * 14}px)`;
  }
};
setT(0);
</script></body></html>"""


def briefing():
    base = W_ / "briefing_base.mp4"
    tl = W_ / "timelapse_markers.mp4"
    run(["-ss", 2, "-t", 150, "-i", D / "dslr" / "01_MVI_0679_2m28s.MOV", "-vf",
         f"setpts=PTS/20,tmix=frames=8,fps=30,{FIT},{DUSK}", *ENC, tl])
    seg2 = W_ / "briefing_b.mp4"
    run(["-ss", 7.3, "-t", T_END - T_SWITCH + 0.2, "-i", D / "dslr" / "08_MVI_0686_2m31s.MOV", "-vf", f"{FIT},{DUSK}", *ENC, "-r", "30", seg2])
    seg1 = W_ / "briefing_a.mp4"
    run(["-t", T_SWITCH, "-i", tl, "-c", "copy", seg1])
    run(["-i", seg1, "-i", seg2, "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", *ENC, base])
    # the card, one PNG with alpha per frame
    frames = W_ / "briefing_frames"
    frames.mkdir(exist_ok=True)
    for f in frames.glob("*.png"):
        f.unlink()
    html = CARD_HTML
    for k, v in (("%(fonts)s", FONTS.as_uri()), ("%(t1)s", T_DEMENTIA), ("%(t2)s", T_AGO), ("%(t3)s", T_PHOTO), ("%(t4)s", T_VOLUNTEER)):
        html = html.replace(k, str(v))
    (W_ / "briefing_card.html").write_text(html, encoding="utf-8")
    fps = 30
    n = int(T_END * fps)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto((W_ / "briefing_card.html").as_uri())
        pg.wait_for_timeout(600)
        for i in range(n):
            pg.evaluate(f"setT({i / fps:.3f})")
            pg.screenshot(path=str(frames / f"f{i:04d}.png"), omit_background=True)
        br.close()
    run(["-i", base, "-framerate", fps, "-i", frames / "f%04d.png", "-filter_complex",
         "[1:v]format=rgba[c];[0:v][c]overlay=0:0:shortest=1,format=yuv420p", *ENC, OUT / "04-briefing.mp4"])
    print("04-briefing", T_END, "s")


# ---- bridge app -------------------------------------------------------------
def bridge():
    bg = Image.new("RGB", (1920, 1080), (17, 26, 16))
    d = ImageDraw.Draw(bg)
    h1 = font("RedHatDisplay[wght].ttf", 60, 800)
    d.text((120, 84), "The phone in the middle", font=h1, fill=(238, 241, 230))
    t = font("RedHatText[wght].ttf", 30, 400)
    d.text((120, 164), "Phantom Bridge, a Kotlin app on the DJI Mobile SDK. The drone's video comes out of it,", font=t, fill=(169, 181, 159))
    d.text((120, 206), "the stick commands go into it, about seven frames a second over the controller's Wi-Fi.", font=t, fill=(169, 181, 159))
    px, py, pw, ph = 190, 290, 1540, 711
    d.rounded_rectangle((px - 16, py - 16, px + pw + 16, py + ph + 16), 44, fill=(8, 12, 8))
    bg.save(W_ / "bridge_bg.png")
    frame = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    fd = ImageDraw.Draw(frame)
    fd.rounded_rectangle((px - 16, py - 16, px + pw + 16, py + ph + 16), 44, fill=(8, 12, 8, 255))
    fd.rounded_rectangle((px, py, px + pw, py + ph), 30, fill=(0, 0, 0, 0))
    frame.save(W_ / "bridge_frame.png")
    run(["-ss", 62, "-t", 12, "-i", D / "phone-screen" / "phone_122812_12.mp4", "-loop", "1", "-i", W_ / "bridge_bg.png", "-loop", "1", "-i", W_ / "bridge_frame.png",
         "-filter_complex", f"[0:v]scale={pw}:{ph}[p];[1:v][p]overlay={px}:{py}:shortest=1[a];[a][2:v]overlay=0:0:shortest=1,format=yuv420p",
         *ENC, "-r", "30", OUT / "11b-bridge.mp4"])
    print("11b-bridge")


# ---- follow + pins ----------------------------------------------------------
def follow_pins():
    pov = D / "drone" / "run2_1211_found-asked-confirmed-followed-90s_DJI_0013.MOV"
    lab = ("drawtext=fontfile='" + str(FONTS / "RedHatText[wght].ttf").replace("\\", "/").replace(":", "\\:") +
           "':text='the map on her phone, live':fontcolor=#EEF1E6:fontsize=26:x=1920-24-440+14:y=1080-24-440-44:box=1:boxcolor=0x111A10@0.86:boxborderw=10")
    run(["-ss", 150, "-t", 16, "-i", pov, "-ss", 1.5, "-t", 16, "-i", W_ / "mc" / "follow_run2.webm",
         "-filter_complex", f"[0:v]{FIT}[a];[1:v]crop=800:800:946:160,scale=440:440,pad=446:446:3:3:color=#2C3A28[m];"
                            f"[a][m]overlay=1920-24-446:1080-24-446:shortest=1,{lab},format=yuv420p",
         *ENC, "-r", "30", OUT / "23-follow-pins.mp4"])
    print("23-follow-pins")


# ---- the markers, panned one by one -------------------------------------------
def markers():
    import json
    ct = json.load(open(ROOT / "results" / "camera_truth_flight03.json"))["summary"]
    bg = Image.new("RGB", (1920, 1080), (17, 26, 16))
    d = ImageDraw.Draw(bg)
    h1 = font("RedHatDisplay[wght].ttf", 58, 800)
    t = font("RedHatText[wght].ttf", 30, 400)
    b = font("RedHatText[wght].ttf", 30, 700)
    x = 110
    d.text((x, 250), "The camera's own check", font=h1, fill=(238, 241, 230))
    d.text((x, 350), "Printed ArUco markers, 190 mm, checked with a ruler.", font=t, fill=(169, 181, 159))
    d.text((x, 396), "OpenCV's solvePnP on the flight footage puts the camera", font=t, fill=(169, 181, 159))
    d.text((x, 442), f"{ct['measured_height_median_m']} m above them where the barometer said {ct['reported_alt_median_m']} m.", font=b, fill=(238, 241, 230))
    d.text((x, 506), "A steady 2 m gap: the lens model, not noise. Report, section 5.4.", font=t, fill=(169, 181, 159))
    bg.save(W_ / "markers_bg.png")
    run(["-ss", 0.3, "-t", 8, "-i", D / "phone-camera" / "20261025_114527.mp4", "-loop", "1", "-i", W_ / "markers_bg.png",
         "-filter_complex", "[0:v]scale=-2:1080[p];[1:v][p]overlay=1920-24-W:0:shortest=1,format=yuv420p", *ENC, "-r", "30", OUT / "14a-markers.mp4"])
    print("14a-markers")


if __name__ == "__main__":
    for w in (sys.argv[1:] or ["briefing", "bridge", "follow_pins", "markers"]):
        globals()[w]()
