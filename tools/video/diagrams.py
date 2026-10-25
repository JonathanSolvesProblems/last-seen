"""The two diagrams the submission asks for, rendered to PNG from HTML in the brand.

  docs/img/architecture.png   OpenCV 5 and AWS components, laptop, aircraft, coordinator
  docs/img/agent-workflow.png perception, decision, action, human, with run 3's real trace
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
FONTS = (ROOT / "tools" / "video" / "fonts").as_uri()
OUT = ROOT / "docs" / "img"
OUT.mkdir(parents=True, exist_ok=True)

CSS = f"""
@font-face {{ font-family: "Red Hat Display"; src: url("{FONTS}/RedHatDisplay[wght].ttf"); font-weight: 300 900; }}
@font-face {{ font-family: "Red Hat Text"; src: url("{FONTS}/RedHatText[wght].ttf"); font-weight: 300 700; }}
@font-face {{ font-family: "Permanent Marker"; src: url("{FONTS}/PermanentMarker-Regular.ttf"); }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; width: 1800px; background: #111A10; color: #EEF1E6; font: 500 22px/1.4 "Red Hat Text", sans-serif; padding: 56px 64px; }}
h1 {{ font: 800 48px/1.1 "Red Hat Display", sans-serif; letter-spacing: -.02em; margin: 0 0 6px; }}
.sub {{ color: #A9B59F; margin: 0 0 36px; }}
.row {{ display: flex; gap: 22px; align-items: stretch; }}
.col {{ display: flex; flex-direction: column; gap: 18px; flex: 1; }}
.box {{ background: #1A2618; border: 1px solid #2C3A28; border-radius: 14px; padding: 20px 22px; }}
.box h2 {{ font: 700 26px "Red Hat Display", sans-serif; margin: 0 0 10px; }}
.box p {{ margin: 4px 0; color: #A9B59F; font-size: 20px; }}
.box b {{ color: #EEF1E6; font-weight: 700; }}
.cv {{ border-color: #A3ADD8; }} .cv h2 {{ color: #A3ADD8; }}
.aws {{ border-color: #E6A04A; }} .aws h2 {{ color: #E6A04A; }}
.ag {{ border-color: #FF4D86; }} .ag h2 {{ color: #FF4D86; }}
.arrow {{ align-self: center; color: #A9B59F; font: 700 34px "Red Hat Display"; padding: 0 2px; }}
.label {{ font-size: 17px; color: #A9B59F; text-align: center; }}
.tape {{ display: inline-block; font: 400 22px/1 "Permanent Marker", cursive; padding: 10px 20px 8px 16px; background: #FF4D86; color: #2A0712; transform: rotate(-2deg);
        clip-path: polygon(0 8%,3% 0,97% 4%,100% 0,98% 52%,100% 100%,4% 94%,0 100%,2% 46%); }}
table {{ border-collapse: collapse; width: 100%; font-size: 19px; }}
td {{ padding: 7px 10px; border-top: 1px solid #2C3A28; vertical-align: top; }}
td.t {{ color: #A9B59F; white-space: nowrap; font-variant-numeric: tabular-nums; }}
td.k {{ font-weight: 700; white-space: nowrap; }}
.p {{ color: #A3ADD8; }} .d {{ color: #FF4D86; }} .a {{ color: #E6A04A; }} .h {{ color: #EEF1E6; }}
"""

ARCH = """
<h1>Last Seen: architecture</h1>
<p class="sub">OpenCV 5 runs every frame on the laptop. Claude decides every few seconds. AWS carries the human decision. Safety never waits on the cloud.</p>
<div class="row">
  <div class="col" style="flex:.85">
    <div class="box"><h2>DJI Phantom 3 Standard (2015)</h2><p>Camera 2.7K to SD, 720p live over the controller's Wi-Fi</p><p>Flight controller, GPS, barometer, gimbal</p></div>
    <div class="box"><h2>Phone: Phantom Bridge</h2><p>Kotlin app on the DJI Mobile SDK</p><p>Video and telemetry out, virtual stick in, over a WebSocket on USB</p><p>500 ms watchdog: hovers if the laptop goes quiet</p></div>
  </div>
  <div class="arrow">&harr;</div>
  <div class="col" style="flex:1.5">
    <div class="box cv"><h2>OpenCV 5 perception, every frame</h2>
      <p><b>cv2.dnn</b> (new engine): YOLO11n person detector, about 20 to 24 fps on a CPU</p>
      <p><b>cv2.dnn</b>: OpenCV Model Zoo person re-ID (768-d, cosine) + <b>imgproc</b> HSV clothing bands &rarr; fused match</p>
      <p><b>Camera model</b>: feet of each box &rarr; a point on the ground (a pin)</p>
      <p><b>objdetect</b> ArUco + <b>3d</b> solvePnP: camera height and pitch from printed markers</p>
      <p><b>Image-space servo</b>: yaw, range and gimbal from where the person sits in the frame</p></div>
    <div class="box ag"><h2>Claude agent, every few seconds</h2><p>Six strict tools: scan_here, fly_to, look_closer, ask_coordinator, follow_confirmed, end_mission</p><p>Sees the candidate table and the crops; every decision written to trace.jsonl</p></div>
    <div class="box"><h2>Envelope, every control tick (code, not prompt)</h2><p>Geofence, ceiling, speed, battery floor, stale link, NotConfirmed gate; the pilot's switch above all</p></div>
  </div>
  <div class="arrow">&harr;</div>
  <div class="col" style="flex:1">
    <div class="box aws"><h2>AWS (one CloudFormation stack)</h2>
      <p><b>Lambda</b> function URL: the coordinator's page and the API</p>
      <p><b>DynamoDB</b>: questions, answers (conditional write, never changed), pins, state</p>
      <p><b>S3</b>: crops and the reference photo, private, presigned, expiring</p>
      <p><b>CloudWatch Logs</b>, 14 days</p></div>
    <div class="box"><h2>The coordinator's phone</h2><p>Is this them? Her photo beside the drone's crop.</p><p><span class="tape">That's them</span> &nbsp; Not them &nbsp; Bring it back</p></div>
  </div>
</div>
"""

AGENT = """
<h1>Last Seen: the agent loop</h1>
<p class="sub">Perception, decision, action, and a human in the loop. Below it, run 3 of 25 October 2026 from its trace: what OpenCV saw changed what Claude did next.</p>
<div class="row" style="margin-bottom:30px">
  <div class="box cv" style="flex:1"><h2>1. Perceive</h2><p>OpenCV 5 on every frame: detect people, score each against the photo, put each on the ground.</p><p>Out: a candidate table (id, match, distance, times seen) and crops.</p></div>
  <div class="arrow">&rarr;</div>
  <div class="box ag" style="flex:1"><h2>2. Decide</h2><p>Claude reads the table and the crops and picks one tool: scan, fly to, look closer, ask, follow, end.</p><p>A weak match is never put to a person.</p></div>
  <div class="arrow">&rarr;</div>
  <div class="box" style="flex:1"><h2>3. Act</h2><p>The runtime flies it inside the envelope, keeps perceiving while the model thinks, and returns the result.</p></div>
  <div class="arrow">&rarr;</div>
  <div class="box aws" style="flex:1"><h2>4. A human confirms</h2><p>ask_coordinator goes to her phone through AWS. follow_confirmed raises NotConfirmed for anyone she did not confirm.</p></div>
</div>
<div class="box">
<table>
<tr><td class="t">12:26:39</td><td class="k d">decide</td><td>scan_here 360&deg;</td></tr>
<tr><td class="t">12:26:57</td><td class="k p">perceive</td><td>new candidates P2 (match 0.85) and P3 (0.25)</td></tr>
<tr><td class="t">12:27:01</td><td class="k d">decide</td><td>"P3 isn't a person ... P2 is a strong match with a clear crop" &rarr; ask_coordinator P2</td></tr>
<tr><td class="t">12:28:33</td><td class="k a">human</td><td>no answer in 90 s (the coordinator was running) &rarr; "no_answer is not a no"</td></tr>
<tr><td class="t">12:28:36</td><td class="k d">decide</td><td>"No answer yet. I'll keep covering the rest of the area and ask again." &rarr; fly_to 0, -8</td></tr>
<tr><td class="t">12:28:44</td><td class="k p">perceive</td><td>new candidate P6 (0.58)</td></tr>
<tr><td class="t">12:28:51</td><td class="k d">decide</td><td>look_closer P6 &rarr; still 0.58</td></tr>
<tr><td class="t">12:29:02</td><td class="k d">decide</td><td>ask_coordinator P2 again, noting P6 nearby</td></tr>
<tr><td class="t">12:29:04</td><td class="k a">human</td><td>confirmed</td></tr>
<tr><td class="t">12:29:07</td><td class="k d">decide</td><td>follow_confirmed P2 &rarr; follows until the battery floor, then the envelope lands it</td></tr>
</table>
</div>
<p class="sub" style="margin:22px 0 0">Run 1, the counter-case: best match 0.24, so Claude looked closer twice, searched two corners, and never asked anyone.</p>
"""


def render(name, body):
    html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
    p = ROOT / "broll" / "_work" / f"{name}.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1800, "height": 1000}, device_scale_factor=1)
        pg.goto(p.as_uri())
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        b.close()
    print("wrote", name)


if __name__ == "__main__":
    render("architecture", ARCH)
    render("agent-workflow", AGENT)
