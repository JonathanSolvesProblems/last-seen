"""Record the mission-control page with the real run data from flight 03.

Each scene serves a fresh in-memory mission through the same App that runs on
Lambda, feeds it the recorded state, question, crop and pins from brain/runs, and
records the browser with Playwright. Nothing on screen is invented: the candidates,
scores, message, crop and pins are the ones the system produced on the day.

Scenes (written to broll/_work/mc/):
  idle.webm          "Waiting for the drone"            1920x1080
  search_run1.webm   run 1: three weak tags, no ask     1920x1080
  ask_run3.webm      run 3: question arrives, the tape ties, cursor clicks That's them,
                     page turns to "Staying with them"  1920x1080
  ask_run3_phone.webm  same, phone layout at 2x         780x1688
  follow_run2.webm   run 2: confirmed, 49 pins stream onto the map   1920x1080
"""

import base64
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "brain"))
import mission_control.app as appmod  # noqa: E402
from mission_control.server import make_handler  # noqa: E402
from mission_control.store import MemoryStore  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

RUNS = ROOT / "brain" / "runs" / "last-seen"
OUT = ROOT / "broll" / "_work" / "mc"
OUT.mkdir(parents=True, exist_ok=True)
A = {"Authorization": "Bearer devtoken", "Content-Type": "application/json"}
b64 = lambda p: base64.b64encode(Path(p).read_bytes()).decode()

CURSOR_JS = """
(() => {
  const c = document.createElement('div');
  c.id = 'fakecursor';
  c.innerHTML = `<svg width="34" height="44" viewBox="0 0 24 32"><path d="M2 2 L2 24 L8 18 L12 29 L16 27 L12 17 L20 17 Z" fill="#EEF1E6" stroke="#141A12" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
  Object.assign(c.style, {position:'fixed', left:'-80px', top:'60%', zIndex:9999, pointerEvents:'none',
     transition:'left 1.1s cubic-bezier(.3,.9,.3,1), top 1.1s cubic-bezier(.3,.9,.3,1), transform .12s', filter:'drop-shadow(0 2px 3px rgba(0,0,0,.45))'});
  document.body.appendChild(c);
  window.moveCursorTo = (sel) => { const r = document.querySelector(sel).getBoundingClientRect();
     c.style.left = (r.left + r.width*0.46) + 'px'; c.style.top = (r.top + r.height*0.55) + 'px'; };
  window.pressCursor = () => { c.style.transform = 'scale(.82)'; setTimeout(() => c.style.transform = '', 140); };
})();
"""


def last_ground(run: str) -> dict[str, list[float]]:
    pos = {}
    for l in open(RUNS / run / "perception.jsonl"):
        r = json.loads(l)
        pos[r["cand"]] = r["ground"]
    return pos


def ask_payload(run: str) -> tuple[list[dict], dict]:
    tr = [json.loads(l) for l in open(RUNS / run / "trace.jsonl")]
    ask = [r for r in tr if r.get("kind") == "model" and any(c["name"] == "ask_coordinator" for c in r.get("calls", []))][0]
    call = [c for c in ask["calls"] if c["name"] == "ask_coordinator"][0]["input"]
    pos = last_ground(run)
    cands = []
    for c in ask["candidates_seen"]:
        d = {"id": c["id"], "match": c["match"], "status": c["status"], "distance_m": c.get("distance_m")}
        if c["id"] in pos:
            d["e"], d["n"] = pos[c["id"]]
        cands.append(d)
    return cands, call


class Scene:
    def __init__(self, port: int):
        self.store = MemoryStore()
        self.app = appmod.App(self.store, "devtoken")
        self.srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.app))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = port

    def post(self, path: str, body: dict) -> dict:
        code, _, out = self.app.handle("POST", path, A, json.dumps(body).encode())
        return json.loads(out or b"{}")

    def state(self, m: str, st: dict) -> None:
        self.post(f"/api/missions/{m}/state", st)

    def url(self, m: str | None) -> str:
        return f"http://127.0.0.1:{self.port}/?token=devtoken" + (f"&mission={m}" if m else "")

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def record(scene: Scene, mission: str | None, name: str, seconds: float, script, *, size=(1920, 1080),
           zoom: float = 1.45, scheme: str = "dark") -> None:
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme,
                             record_video_dir=str(OUT), record_video_size={"width": size[0], "height": size[1]})
        pg = ctx.new_page()
        pg.goto(scene.url(mission), wait_until="domcontentloaded")
        if zoom != 1.0:
            pg.add_style_tag(content=f"html{{zoom:{zoom}}}")
        pg.wait_for_timeout(1500)
        pg.evaluate(CURSOR_JS)
        t0 = time.time()
        script(pg, scene, t0)
        left = seconds - (time.time() - t0)
        if left > 0:
            pg.wait_for_timeout(int(left * 1000))
        path = pg.video.path()
        ctx.close()
        br.close()
    Path(path).replace(OUT / f"{name}.webm")
    print("recorded", name)


def keep_fresh(scene: Scene, m: str, st: dict, until: float) -> threading.Thread:
    """Re-post the state every second so the page never shows 'No word from the drone'."""
    def run():
        while time.time() < until:
            scene.state(m, st)
            time.sleep(1.0)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def main() -> None:
    only_phone = "--only-phone" in sys.argv
    # ---- idle --------------------------------------------------------------
    s = Scene(8101)
    if not only_phone:
        record(s, None, "idle", 6, lambda pg, sc, t0: None)
    s.close()

    # ---- run 1: searching, three weak tags, never asks ---------------------
    s = Scene(8102)
    tr1 = [json.loads(l) for l in open(RUNS / "20261025-120518" / "trace.jsonl")]
    seen = [r for r in tr1 if r.get("kind") == "model" and r.get("candidates_seen")][-1]["candidates_seen"]
    pos1 = last_ground("20261025-120518")
    cands1 = [{"id": c["id"], "match": c["match"], "status": "unreviewed", "distance_m": c.get("distance_m"),
               **({"e": pos1[c["id"]][0], "n": pos1[c["id"]][1]} if c["id"] in pos1 else {})} for c in seen]
    st1 = {"area": [[-12, -12], [12, -12], [12, 12], [-12, 12]], "battery": 66,
           "drone": {"e": 7.4, "n": 7.2, "alt": 7.9, "heading": 228}, "candidates": cands1,
           "reference_jpeg_b64": b64(RUNS / "20261025-120518" / "reference.jpg")}
    s.state("r1", st1)
    kf = keep_fresh(s, "r1", {k: v for k, v in st1.items() if k != "reference_jpeg_b64"}, time.time() + 20)
    if not only_phone:
        record(s, "r1", "search_run1", 8, lambda pg, sc, t0: None)
    s.close()

    # ---- run 3: the question, the click ------------------------------------
    cands3, call3 = ask_payload("20261025-122602")
    st3 = {"area": [[-12, -12], [12, -12], [12, 12], [-12, 12]], "battery": 27,
           "drone": {"e": 0.6, "n": 2.1, "alt": 8.0, "heading": 15}, "candidates": cands3}
    crop3 = b64(RUNS / "20261025-122602" / "crops" / "P2.jpg")
    ref3 = b64(RUNS / "20261025-122602" / "reference.jpg")

    def ask_script(pg, sc, t0):
        pg.wait_for_timeout(2500)                           # searching, then the question lands
        sc.post("/api/missions/r3/questions", {"candidate": "P2", "match": 0.85, "message": call3["message"],
                                               "crop_jpeg_b64": crop3})
        pg.wait_for_timeout(12000 if pg.viewport_size["width"] < 1000 else 7000)   # read it
        pg.evaluate("moveCursorTo('#yes')")
        pg.wait_for_timeout(1300)
        pg.evaluate("pressCursor()")
        pg.wait_for_timeout(120)
        pg.click("#yes")
        pg.wait_for_timeout(4500)                           # "Staying with them"

    for port, name, size, zoom in ((8103, "ask_run3", (1920, 1080), 1.45), (8105, "ask_run3_phone", (780, 1688), 2.0)):
        s = Scene(port)
        s.state("r3", {**st3, "reference_jpeg_b64": ref3})
        keep_fresh(s, "r3", st3, time.time() + 40)
        if "--only-phone" in sys.argv and name != "ask_run3_phone":
            s.close(); continue
        record(s, "r3", name, 22 if name == "ask_run3_phone" else 17, ask_script, size=size, zoom=zoom)
        s.close()

    # ---- run 2: confirmed, 49 pins stream in -------------------------------
    s = Scene(8104)
    c2 = json.load(open(RUNS / "20261025-121053" / "candidates.json"))
    cands2 = [{"id": c["id"], "match": round(c["best"], 2), "status": c["status"], "e": c.get("e"), "n": c.get("n")}
              for c in c2 if c.get("e") is not None and c["seen"] >= 3]
    st2 = {"area": [[-12, -12], [12, -12], [12, 12], [-12, 12]], "battery": 50,
           "drone": {"e": 1.2, "n": 4.8, "alt": 8.0, "heading": 300}, "candidates": cands2}
    tr2 = [json.loads(l) for l in open(RUNS / "20261025-121053" / "trace.jsonl")]
    msg2 = [c for r in tr2 if r.get("kind") == "model" for c in r.get("calls", []) if c["name"] == "ask_coordinator"][0]["input"]["message"]
    s.state("r2", {**st2, "reference_jpeg_b64": b64(RUNS / "20261025-121053" / "reference.jpg")})
    q = s.post("/api/missions/r2/questions", {"candidate": "P1", "match": 0.76, "message": msg2,
                                              "crop_jpeg_b64": b64(RUNS / "20261025-121053" / "crops" / "P1.jpg")})
    s.post(f"/api/missions/r2/questions/{q['id']}/answer", {"decision": "confirmed"})
    pins = json.load(open(RUNS / "20261025-121053" / "pins.json"))
    keep_fresh(s, "r2", st2, time.time() + 40)

    def follow_script(pg, sc, t0):
        pg.wait_for_timeout(1500)
        for p in pins:                                      # 49 pins, four a second
            sc.post("/api/missions/r2/pins", {"t": p["t"], "e": p["e"], "n": p["n"], "phone_e": None, "phone_n": None})
            pg.wait_for_timeout(250)
        pg.wait_for_timeout(3000)

    if not only_phone:
        record(s, "r2", "follow_run2", 18, follow_script)
    s.close()
    print("all scenes done")


if __name__ == "__main__":
    main()
