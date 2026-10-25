"""Animated brand-styled pages recorded with Playwright, for beats no footage can show.

  photo      the reference photo dropped on the field as a print, tape tied on
  arch       the architecture, one piece at a time, left to right
  terminal   the deploy and the dry-run teardown, typed (real output, token hidden)
  code       the two safety checks that live in code, from the files themselves
  summary    the agent's own end-of-mission summary from run 2, typed

Writes broll/_work/html/<name>.webm at 1920x1080.
"""

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "broll" / "_work" / "html"
OUT.mkdir(parents=True, exist_ok=True)
FONTS = (ROOT / "tools" / "video" / "fonts").as_uri()

BASE_CSS = f"""
@font-face {{ font-family: "Red Hat Display"; src: url("{FONTS}/RedHatDisplay[wght].ttf"); font-weight: 300 900; }}
@font-face {{ font-family: "Red Hat Text"; src: url("{FONTS}/RedHatText[wght].ttf"); font-weight: 300 700; }}
@font-face {{ font-family: "Permanent Marker"; src: url("{FONTS}/PermanentMarker-Regular.ttf"); }}
:root {{ --ground:#111A10; --raised:#1A2618; --ink:#EEF1E6; --ink-2:#A9B59F; --line:#2C3A28; --tape:#FF4D86; --tape-ink:#2A0712;
         --orange:#E6A04A; --navy:#A3ADD8; --print:#E3E8DA; }}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; width:1920px; height:1080px; background:var(--ground); color:var(--ink); overflow:hidden;
             font: 500 30px/1.4 "Red Hat Text", sans-serif; font-feature-settings:"tnum" 1; }}
body {{ background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 .27  0 0 0 0 .4  0 0 0 0 .2  0 0 0 .09 0'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)'/%3E%3C/svg%3E"); }}
h1 {{ font: 800 72px/1.05 "Red Hat Display", sans-serif; letter-spacing:-.025em; margin:0; }}
h2 {{ font: 700 36px/1.2 "Red Hat Display", sans-serif; margin:0; }}
.tape {{ display:inline-block; font: 400 30px/1 "Permanent Marker", cursive; padding:14px 30px 12px 24px; background:var(--tape); color:var(--tape-ink);
        transform: rotate(-2.2deg); clip-path: polygon(0 8%,3% 0,97% 4%,100% 0,98% 52%,100% 100%,4% 94%,0 100%,2% 46%); }}
.tape--orange {{ background:var(--orange); color:#2A1A04; }}
.muted {{ color: var(--ink-2); }}
"""


def write(name: str, body: str, extra_css: str = "", script: str = "") -> Path:
    p = OUT / f"{name}.html"
    p.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{BASE_CSS}{extra_css}</style></head><body>{body}<script>{script}</script></body></html>", encoding="utf-8")
    return p


def record(name: str, seconds: float) -> None:
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(OUT),
                             record_video_size={"width": 1920, "height": 1080})
        pg = ctx.new_page()
        pg.goto((OUT / f"{name}.html").as_uri())
        pg.wait_for_timeout(int(seconds * 1000))
        path = pg.video.path()
        ctx.close()
        br.close()
    Path(path).replace(OUT / f"{name}.webm")
    print("recorded", name)


# ---- photo ------------------------------------------------------------------
def photo():
    ref = (ROOT / "brain" / "runs" / "last-seen" / "20261025-122602" / "reference.jpg").as_uri()
    css = """
    .stage { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; }
    .print { position:relative; padding:14px; background:var(--print); border-radius:4px; box-shadow:0 30px 60px -20px rgba(0,0,0,.8);
             transform: translateY(-900px) rotate(-6deg); animation: drop .9s cubic-bezier(.2,.9,.3,1.05) .3s forwards; }
    .print img { display:block; height:640px; width:auto; }
    @keyframes drop { to { transform: translateY(0) rotate(-1.5deg); } }
    .print .tape { position:absolute; top:-26px; left:50%; translate:-50% 0; white-space:nowrap; opacity:0; animation: tie .5s ease-out 1.25s forwards; transform-origin: 8% 50%; }
    @keyframes tie { from { opacity:1; clip-path: polygon(0 8%,3% 0,3% 4%,3% 0,3% 52%,3% 100%,3% 94%,0 100%,2% 46%);} to { opacity:1; clip-path: polygon(0 8%,3% 0,97% 4%,100% 0,98% 52%,100% 100%,4% 94%,0 100%,2% 46%);} }
    .cap { position:absolute; bottom:120px; left:0; right:0; text-align:center; font-size:34px; color:var(--ink-2); opacity:0; animation: fade .6s ease-out 1.8s forwards; }
    @keyframes fade { to { opacity:1; } }
    """
    body = f"""<div class="stage"><figure class="print" style="margin:0"><span class="tape">the photo</span><img src="{ref}"></figure></div>
"""
    write("photo", body, css)
    record("photo", 8)


# ---- architecture -----------------------------------------------------------
def arch():
    css = """
    .wrap { position:absolute; inset:0; padding:70px 90px; }
    .row { display:flex; align-items:stretch; gap:26px; margin-top:54px; }
    .node { flex:1; background:var(--raised); border:1px solid var(--line); border-radius:14px; padding:26px 28px; opacity:0; transform:translateY(18px);
            animation: in .55s cubic-bezier(.2,.8,.3,1) forwards; }
    .node h2 { margin-bottom:10px; }
    .node p { margin:6px 0; font-size:26px; color:var(--ink-2); }
    .node b { color:var(--ink); font-weight:600; }
    .arrow { align-self:center; width:70px; height:4px; background:var(--line); position:relative; opacity:0; animation: in .4s ease-out forwards; }
    .arrow::after { content:""; position:absolute; right:-2px; top:-8px; border:10px solid transparent; border-left:14px solid var(--line); }
    .arrow.pink { background:var(--tape); } .arrow.pink::after { border-left-color:var(--tape); }
    @keyframes in { to { opacity:1; transform:none; } }
    .back { margin-top:46px; display:flex; justify-content:center; align-items:center; gap:18px; opacity:0; animation: in .5s ease-out 3.6s forwards; font-size:28px; color:var(--ink-2); }
    .back .tape { font-size:26px; }
    .ab { position:absolute; bottom:70px; left:90px; right:90px; display:flex; justify-content:space-between; font-size:24px; color:var(--ink-2); opacity:0; animation: in .5s ease-out 4.2s forwards; }
    """
    body = """<div class="wrap">
      <h1>How the answer travels</h1>
      <div class="row">
        <div class="node" style="animation-delay:.2s"><h2>Phantom 3 Standard</h2><p>2015 aircraft, its own camera</p><p>720p over Wi-Fi to the phone</p><p>Android bridge app: video out, sticks in</p></div>
        <div class="arrow" style="animation-delay:.7s"></div>
        <div class="node" style="animation-delay:.9s"><h2>Laptop, in the field</h2><p><b>OpenCV 5</b> DNN: YOLO person detector</p><p><b>OpenCV</b> model zoo re-ID + clothing colour</p><p><b>OpenCV</b> camera model: feet to ground</p><p><b>Claude</b> picks the next action from the tools</p></div>
        <div class="arrow pink" style="animation-delay:1.6s"></div>
        <div class="node" style="animation-delay:1.8s"><h2>AWS</h2><p><b>Lambda</b> serves the page and the API</p><p><b>DynamoDB</b> keeps every decision, conditional write: once answered, never changed</p><p><b>S3</b> holds the crops, private, short-lived links</p><p><b>CloudFormation</b>, one command up, one down</p></div>
        <div class="arrow pink" style="animation-delay:2.5s"></div>
        <div class="node" style="animation-delay:2.7s"><h2>The coordinator's phone</h2><p>Is this them?</p><p>The photo beside what the drone sees</p><p><span class="tape">That's them</span></p></div>
      </div>
      <div class="back"><span>Only a confirmed answer reaches the follow code</span><span class="tape tape--orange">no answer, no follow</span></div>
      <div class="ab"><span>Nothing fabricated: every number on screen is read from a run file</span><span>IAM user scoped to the stack · no key leaves the laptop</span></div>
    </div>"""
    write("arch", body, css)
    record("arch", 9)


# ---- terminal ---------------------------------------------------------------
def terminal():
    dry = (ROOT / "broll" / "_work" / "teardown_dryrun.txt").read_text(encoding="utf-8").strip().splitlines()
    dry = [re.sub(r"\b\d{12}\b", "•" * 6, l) for l in dry if "Connect timeout" not in l]
    url = json.load(open(ROOT / "infra" / "outputs.json"))["Url"].rstrip("/")
    url = re.sub(r"https://([a-z0-9]{6})[a-z0-9]+\.", lambda m: "https://" + m.group(1) + "….", url)
    ev = (ROOT / "broll" / "_work" / "aws_evidence.txt").read_text(encoding="utf-8").strip().splitlines()
    lines = ["$ python infra/deploy.py --profile lastseen",
             f"mission control: {url}/?token=••••••••", ""] + ev + ["", "$ python infra/teardown.py --profile lastseen"] + dry[:4]
    css = """
    .term { position:absolute; inset:60px 100px; background:#0C130B; border:1px solid var(--line); border-radius:14px; padding:34px 44px;
            font: 500 24px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; color:var(--ink); white-space:pre-wrap; }
    .bar { height:14px; display:flex; gap:10px; margin-bottom:26px; } .bar i { width:14px; height:14px; border-radius:50%; background:var(--line); }
    .c { color:var(--ink-2); } .p { color:var(--tape); } .k { color:var(--navy); }
    .cur { display:inline-block; width:14px; height:30px; background:var(--ink); vertical-align:-6px; animation: blink 1s steps(2) infinite; }
    @keyframes blink { 50% { opacity:0; } }
    """
    body = """<div class="term"><div class="bar"><i></i><i></i><i></i></div><div id="t"></div><span class="cur"></span></div>"""
    script = "const L=%s;let i=0,j=0;const el=document.getElementById('t');function cls(s){return s.startsWith('$')?'p':(s.startsWith('  LEFT')||s.includes('DRY RUN')||s.startsWith('AWS::')||s.startsWith('HTTP/2')?'k':'c')}function step(){if(i>=L.length)return;const line=L[i];if(el.querySelectorAll('div').length<=i){const d=document.createElement('div');d.className=cls(line);el.appendChild(d)}const d=el.querySelectorAll('div')[i];j+=line.startsWith('$')?2:line.length;d.textContent=line.slice(0,j);if(j>=line.length){i++;j=0;setTimeout(step,line.startsWith('$')?380:110)}else setTimeout(step,22)}setTimeout(step,300);" % json.dumps(lines)
    write("terminal", body, css, script)
    record("terminal", 14)


# ---- code -------------------------------------------------------------------
def code():
    rt = (ROOT / "brain" / "phantom_brain" / "runtime.py").read_text(encoding="utf-8").splitlines()
    pl = (ROOT / "brain" / "phantom_brain" / "pilot.py").read_text(encoding="utf-8").splitlines()
    def grab(lines, start_pat, n):
        for i, l in enumerate(lines):
            if re.search(start_pat, l):
                return lines[i:i + n]
        return []
    i = next(i for i, l in enumerate(rt) if 'if c.status != "confirmed":' in l)
    a = rt[i - 1:i + 2]
    b = grab(pl, r"if self\.battery_is_plausible\(t\.battery\)", 7)
    def hl(src):
        out = []
        for l in src:
            s = l.replace("&", "&amp;").replace("<", "&lt;")
            s = re.sub(r"\b(if|raise|and|not|is|None|self)\b", r"<span class='k'>\1</span>", s)
            s = re.sub(r"(f?\"[^\"]*\")", r"<span class='s'>\1</span>", s)
            s = re.sub(r"\b(NotConfirmed|Abort)\b", r"<span class='e'>\1</span>", s)
            out.append(s)
        return "\n".join(out)
    css = """
    .wrap { position:absolute; inset:70px 100px; }
    .file { background:#0C130B; border:1px solid var(--line); border-radius:14px; padding:26px 34px; margin-top:26px; opacity:0; transform:translateY(14px); animation: in .5s ease-out forwards;
            font: 500 26px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; white-space:pre; color:var(--ink); }
    .file .name { font: 500 22px "Red Hat Text", sans-serif; color:var(--ink-2); margin-bottom:12px; }
    .k { color:var(--navy); } .s { color:var(--orange); } .e { color:var(--tape); font-weight:700; }
    @keyframes in { to { opacity:1; transform:none; } }
    .why { margin-top:30px; font-size:30px; color:var(--ink-2); opacity:0; animation: in .5s ease-out 3.0s forwards; }
    """
    body = f"""<div class="wrap"><h1>Safety that a prompt cannot promise</h1>
      <div class="file" style="animation-delay:.2s"><div class="name">brain/phantom_brain/runtime.py · follow()</div>{hl(a)}</div>
      <div class="file" style="animation-delay:1.7s"><div class="name">brain/phantom_brain/pilot.py · every control tick</div>{hl(b)}</div>
      <div class="why">The follow code refuses anyone the coordinator did not confirm. The envelope check runs on every tick: battery, ceiling, geofence, link. The pilot's switch sits above all of it.</div>
    </div>"""
    write("code", body, css)
    record("code", 13)


# ---- summary ----------------------------------------------------------------
def summary():
    tr = [json.loads(l) for l in open(ROOT / "brain" / "runs" / "last-seen" / "20261025-121053" / "trace.jsonl")]
    done = [r for r in tr if r.get("kind") == "done"][0]
    text = done["outcome"]["summary"]
    css = """
    .wrap { position:absolute; inset:90px 140px; }
    .box { margin-top:34px; background:var(--raised); border:1px solid var(--line); border-radius:14px; padding:40px 46px; font-size:34px; line-height:1.45; min-height:420px; }
    .box b { color:var(--tape); font-weight:700; }
    .who { color:var(--ink-2); font-size:26px; margin-top:18px; }
    """
    body = """<div class="wrap"><h1>What the agent wrote at the end of run 2</h1><div class="box" id="b"></div><div class="who">end_mission summary, verbatim, brain/runs/last-seen/20261025-121053/trace.jsonl</div></div>"""
    script = "const T=%s;const el=document.getElementById('b');let i=0;function step(){i+=3;let s=T.slice(0,i).replace(/&/g,'&amp;').replace(/</g,'&lt;');s=s.replace(/(The lock was weak[^.]*\\.)/,'<b>$1</b>');el.innerHTML=s;if(i<T.length)setTimeout(step,22)}setTimeout(step,400);" % json.dumps(text)
    write("summary", body, css, script)
    record("summary", 16)


def photo_ground():
    """Run 1's reference: the drone's own camera on the ground, looking across at him."""
    ref = (ROOT / "brain" / "runs" / "last-seen" / "20261025-120518" / "reference.jpg").as_uri()
    css = """
    .stage { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; gap:60px; }
    .print { position:relative; padding:14px; background:var(--print); border-radius:4px; box-shadow:0 30px 60px -20px rgba(0,0,0,.8);
             transform: rotate(-1.5deg); opacity:0; animation: fade .6s ease-out .2s forwards; }
    .print img { display:block; height:560px; width:auto; image-rendering:auto; }
    .print .tape { position:absolute; top:-26px; left:50%; translate:-50% 0; white-space:nowrap; }
    .side { max-width:720px; opacity:0; animation: fade .6s ease-out 1.0s forwards; }
    .side h1 { font-size:64px; margin-bottom:20px; }
    .side p { font-size:32px; color:var(--ink-2); margin:0 0 14px; }
    @keyframes fade { to { opacity:1; } }
    """
    body = f"""<div class="stage"><figure class="print" style="margin:0"><span class="tape">run 1 · the photo</span><img src="{ref}"></figure>
    <div class="side"><h1>Taken from the ground</h1><p>The drone's own camera, sitting on the infield, looking across at me.</p><p>Eye level. The hard case for a search photo.</p></div></div>"""
    write("photo_ground", body, css)
    record("photo_ground", 9)


if __name__ == "__main__":
    which = sys.argv[1:] or ["photo", "photo_ground", "arch", "terminal", "code", "summary"]
    for w in which:
        globals()[w]()
