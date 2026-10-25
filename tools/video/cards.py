"""Title and end cards for the Last Seen demo, in the brand (CHARTER.md): field dusk
ground, chalk text, tape pink accent, Red Hat Display. Headshot in a circle with a
pink ring, square crop from the top of the source so the hair is kept, drawn at 4x
and downsampled so the circle edge is clean.

Writes broll/s00-title.png and broll/s99-end.png at 1920x1080.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "tools" / "video" / "fonts"
OUT = ROOT / "broll"
W, H = 1920, 1080
GROUND, CHALK, MUTED, TAPE, TAPE_INK = "#111A10", "#EEF1E6", "#A9B59F", "#FF4D86", "#2A0712"


def font(name: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # noqa: BLE001
            pass
    return f


def headshot(size: int) -> Image.Image:
    src = Image.open(ROOT / "broll" / "_work" / "headshot.jpg").convert("RGB")
    w, h = src.size
    sq = src.crop((0, 0, w, w))                  # square from the top: hair at y=27 survives
    s4 = size * 4
    sq = sq.resize((s4, s4), Image.LANCZOS)
    mask = Image.new("L", (s4, s4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, s4 - 1, s4 - 1), fill=255)
    ring = Image.new("RGBA", (s4 + 64, s4 + 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    d.ellipse((0, 0, s4 + 63, s4 + 63), fill=TAPE)
    d.ellipse((20, 20, s4 + 43, s4 + 43), fill=GROUND)
    ring.paste(sq, (32, 32), mask)
    return ring.resize(((s4 + 64) // 4, (s4 + 64) // 4), Image.LANCZOS)


def tape_label(text: str, size: int = 44) -> Image.Image:
    """A strip of flagging tape with marker writing, rough ends, slightly rotated."""
    f = font("PermanentMarker-Regular.ttf", size)
    tw = int(f.getlength(text))
    pad_x, pad_y = 34, 18
    w, h = tw + pad_x * 2, size + pad_y * 2
    s = 4
    im = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    poly = [(0, h * s * .08), (w * s * .03, 0), (w * s * .97, h * s * .04), (w * s, 0), (w * s * .98, h * s * .52),
            (w * s, h * s), (w * s * .04, h * s * .94), (0, h * s), (w * s * .02, h * s * .46)]
    d.polygon(poly, fill=TAPE)
    im = im.resize((w, h), Image.LANCZOS)
    ImageDraw.Draw(im).text((pad_x, pad_y - 4), text, font=f, fill=TAPE_INK)
    return im.rotate(-2.2, expand=True, resample=Image.BICUBIC)


def grain(im: Image.Image) -> Image.Image:
    import random
    px = im.load()
    rnd = random.Random(7)
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            if rnd.random() < 0.08:
                r, g, b = px[x, y]
                k = rnd.randint(-6, 8)
                px[x, y] = (max(0, r + k), max(0, g + k), max(0, b + k))
    return im


def card(end: bool) -> Image.Image:
    im = Image.new("RGB", (W, H), GROUND)
    im = grain(im)
    d = ImageDraw.Draw(im)
    hs = headshot(300)
    im.paste(hs, ((W - hs.width) // 2, 150), hs)
    wm = Image.open(ROOT / "broll" / "_work" / "wordmark_dark.png").convert("RGBA")
    wm = wm.resize((760, int(wm.height * 760 / wm.width)), Image.LANCZOS)
    ty = 500
    im.paste(wm, ((W - wm.width) // 2, ty), wm)
    sub = font("RedHatText[wght].ttf", 40, 400)
    line = "A photo in, a person found, and nothing happens until a human says that's them." if not end \
        else "Built for the OpenCV AI Competition 2026, powered by AWS."
    lw = d.textlength(line, font=sub)
    d.text(((W - lw) // 2, ty + wm.height + 36), line, font=sub, fill=MUTED)
    d.rectangle(((W - 120) // 2, ty + wm.height + 112, (W + 120) // 2, ty + wm.height + 117), fill=TAPE)
    site = font("RedHatDisplay[wght].ttf", 44, 700)
    s = "jonathansolvesproblems.com"
    sw = d.textlength(s, font=site)
    d.text(((W - sw) // 2, ty + wm.height + 148), s, font=site, fill=CHALK)
    if end:
        ask = font("RedHatText[wght].ttf", 34, 400)
        a = "Code, report and every number: github.com/JonathanSolvesProblems/last-seen"
        aw = d.textlength(a, font=ask)
        d.text(((W - aw) // 2, ty + wm.height + 236), a, font=ask, fill=MUTED)
    return im


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    card(False).save(OUT / "s00-title.png")
    card(True).save(OUT / "s99-end.png")
    print("cards written")
