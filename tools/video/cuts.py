"""Cut every clip the edit plan names into broll/, 1920x1080, from the catalogued sources.

Windows come from the labelled contact sheets (broll/_work/lab_*.jpg) and the run
traces. Composites (phone on the field, the synchronised split screen, the landing
log line, the hand over the lens) are built here too, so vidkit only has to place them.
"""

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
V = Path.home() / "Videos" / "DroneProject"
D = V / "flight-03-2026-10-25"
R1 = D / "drone" / "run1_1205_search-with-ground-photo_no-match_recalled_DJI_0012.MOV"
R2 = D / "drone" / "run2_1211_found-asked-confirmed-followed-90s_DJI_0013.MOV"
R3 = D / "drone" / "run3_1226_found-0.85-followed_low-battery-landing_DJI_0016.MOV"
DS = {n: next((D / "dslr").glob(f"{n:02d}_*.MOV")) for n in (2, 4, 8, 9, 10, 11, 12, 13)}
F01 = V / "flight-01-2026-09-25" / "drone" / "174156_first-flight_20260925-174101_DJI_0008.mov"
F02 = V / "flight-02-2026-09-30" / "drone" / "154433_follow-me_20260930-153512_DJI_0003.mov"
W_ = ROOT / "broll" / "_work"
OUT = ROOT / "broll"
FONTS = ROOT / "tools" / "video" / "fonts"
FONT_T = str(FONTS / "RedHatText[wght].ttf").replace("\\", "/").replace(":", "\\:")
FONT_D = str(FONTS / "RedHatDisplay[wght].ttf").replace("\\", "/").replace(":", "\\:")
FIT = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1"
ENC = ["-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p", "-an"]
DUSK = "eq=brightness=-0.07:contrast=1.06:saturation=0.85,colorbalance=rs=-0.05:gs=-0.02:bs=0.10:rm=-0.03:bm=0.05,vignette=PI/5"


def run(args):
    print("  ", " ".join(str(a) for a in args[-1:]))
    subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], check=True)


def cut(src, t0, t1, name, vf=FIT, extra=()):
    run(["-ss", t0, "-to", t1, "-i", src, "-vf", vf, *extra, *ENC, OUT / f"{name}.mp4"])


def conv(src, name, t0=None, t1=None, vf=FIT):
    seek = ["-ss", t0] if t0 is not None else []
    to = ["-to", t1] if t1 is not None else []
    run([*seek, *to, "-i", src, "-vf", vf, *ENC, "-r", "30", OUT / f"{name}.mp4"])


def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # noqa: BLE001
            pass
    return f


def phone_composite():
    """The phone recording (780x1688) on the field, with the beat's words beside it."""
    bg = Image.open(OUT / "s00-title.png").convert("RGB").resize((1920, 1080))
    bg = Image.new("RGB", (1920, 1080), (17, 26, 16))
    d = ImageDraw.Draw(bg)
    px, py, pw, ph = 1180, 40, 462, 1000
    d.rounded_rectangle((px - 14, py - 14, px + pw + 14, py + ph + 14), 46, fill=(8, 12, 8))
    h1 = font("RedHatDisplay[wght].ttf", 84, 800)
    d.text((120, 300), "One question.", font=h1, fill=(238, 241, 230))
    d.text((120, 400), "Two buttons.", font=h1, fill=(238, 241, 230))
    t = font("RedHatText[wght].ttf", 34, 400)
    d.text((120, 540), "The photo they gave, beside what the drone sees.", font=t, fill=(169, 181, 159))
    d.text((120, 590), "Nothing follows anyone until a person answers.", font=t, fill=(169, 181, 159))
    bg.save(W_ / "phone_bg.png")
    frame = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    fd = ImageDraw.Draw(frame)
    fd.rounded_rectangle((px - 14, py - 14, px + pw + 14, py + ph + 14), 46, fill=(8, 12, 8, 255))
    fd.rounded_rectangle((px, py, px + pw, py + ph), 34, fill=(0, 0, 0, 0))
    frame.save(W_ / "phone_frame.png")
    run(["-ss", 2.0, "-to", 21.5, "-i", W_ / "mc" / "ask_run3_phone.webm", "-loop", "1", "-i", W_ / "phone_bg.png", "-loop", "1", "-i", W_ / "phone_frame.png",
         "-filter_complex", f"[0:v]scale={pw}:{ph}[p];[1:v][p]overlay={px}:{py}:shortest=1[a];[a][2:v]overlay=0:0:shortest=1,format=yuv420p",
         *ENC, "-r", "30", OUT / "20-phone-ask.mp4"])


def split_screen():
    """The same fifteen seconds of the run 3 follow, the DSLR on the ground and the drone above.
    dslr09 runs 40 s ahead of the run 3 clip (both anchored on the 12:26:09 takeoff)."""
    lab = (f"drawtext=fontfile='{FONT_T}':text='from the ground':fontcolor=#EEF1E6:fontsize=30:x=40:y=h-80:box=1:boxcolor=0x111A10@0.86:boxborderw=14")
    lab2 = (f"drawtext=fontfile='{FONT_T}':text='from the drone, the same moment':fontcolor=#EEF1E6:fontsize=30:x=40:y=h-80:box=1:boxcolor=0x111A10@0.86:boxborderw=14")
    run(["-ss", 278, "-to", 294, "-i", DS[9], "-ss", 238, "-to", 254, "-i", R3,
         "-filter_complex", f"[0:v]scale=-2:1080,crop=958:1080,{lab}[l];[1:v]scale=-2:1080,crop=958:1080,{lab2}[r];"
                            f"[l][r]hstack,pad=1920:1080:(ow-iw)/2:0:color=#111A10,format=yuv420p",
         *ENC, "-r", "30", OUT / "22-follow-split.mp4"])


def landing():
    txt = "12\\:30\\:11  ABORT\\: battery 18%   landing"
    vf = (FIT + f",drawtext=fontfile='{FONT_T}':text='{txt}':fontcolor=#EEF1E6:fontsize=34:x=56:y=h-150:"
          f"box=1:boxcolor=0x111A10@0.88:boxborderw=18:enable='gte(t,1.2)'")
    cut(R3, 256.5, 269, "28-landing", vf)


def cover():
    """Run 1's landing: his hand comes over the lens. Fade to black on the last half second."""
    cut(R1, 308.6, 312.8, "31-cover", FIT + ",fade=t=out:st=3.5:d=0.6")


def main():
    which = set(sys.argv[1:])
    def want(n): return not which or n in which
    OUT.mkdir(exist_ok=True)
    if want("01"): cut(DS[13], 37.0, 41.2, "01-drone-hand")
    if want("02"): conv(W_ / "html" / "photo.webm", "02-photo")
    if want("03"): cut(DS[4], 202.5, 216.0, "03-takeoff-sky")
    if want("04"): cut(DS[12], 0.0, 14.0, "04-empty-field", FIT + "," + DUSK)
    if want("05"): cut(DS[4], 432.0, 444.0, "05-volunteer")
    if want("06"): cut(DS[13], 2.0, 11.8, "06-walk-away")
    if want("07"): conv(W_ / "mc" / "idle.webm", "07-page-idle", 1.0, 5.5)
    if want("08"): cut(F01, 3.0, 15.0, "08-flight01")
    if want("09"): cut(F02, 8.0, 24.0, "09-flight02")
    if want("10"): cut(R1, 133.0, 150.0, "10-hands-up"); cut(R3, 8.0, 26.0, "10b-liftoff")
    if want("11"): conv(W_ / "ov" / "run3_panel.mp4", "11-panel")
    if want("12"): conv(W_ / "ov" / "run3_detect.mp4", "12-detect")
    if want("13"): conv(W_ / "ov" / "run3_match.mp4", "13-match")
    if want("14"): conv(W_ / "ov" / "run3_ground.mp4", "14-ground")
    if want("15"): conv(W_ / "mc" / "follow_run2.webm", "15-map-pins", 4.0, 16.0)
    if want("16"): conv(W_ / "html" / "photo_ground.webm", "16-ground-ref")
    if want("17"):
        run(["-i", W_ / "ov" / "run1_score_a.mp4", "-i", W_ / "ov" / "run1_score_b.mp4", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", *ENC, OUT / "17-run1-weak.mp4"])
    if want("18"): conv(W_ / "mc" / "search_run1.webm", "18-page-search", 1.0, 7.5)
    if want("19"): conv(W_ / "mc" / "ask_run3.webm", "19-page-ask", 0.5, 10.5)
    if want("20"): phone_composite()
    if want("21"): conv(W_ / "mc" / "ask_run3.webm", "21-click", 9.5, 16.5)
    if want("22"): split_screen()
    if want("23"): conv(W_ / "mc" / "follow_run2.webm", "23-pins", 1.5, 17.5)
    if want("24"): conv(W_ / "html" / "summary.webm", "24-summary")
    if want("25"): conv(W_ / "html" / "arch.webm", "25-arch")
    if want("26"): conv(W_ / "html" / "terminal.webm", "26-terminal")
    if want("27"): conv(W_ / "html" / "code.webm", "27-code")
    if want("28"): landing()
    if want("29"): cut(DS[11], 0.0, 10.0, "29-switch")
    if want("30"): cut(DS[13], 19.0, 36.5, "30-final")
    if want("31"): cover()
    print("cuts done")


if __name__ == "__main__":
    main()
