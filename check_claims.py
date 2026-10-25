"""Fail when the prose and the data disagree.

    python check_claims.py           # every stated number must match its source file
    python check_claims.py --final   # and no [pending] / [X] placeholder may remain

Each claim names a document, a regex that captures the number as written there,
and the value in the data it must equal. Add a claim whenever a number goes into
the report, the README, the demo script or the submission.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
R = ROOT / "results"
RUNS = ROOT / "brain" / "runs" / "last-seen"
RUN1, RUN2, RUN3 = "20261025-120518", "20261025-121053", "20261025-122602"


def reid() -> dict:
    return json.loads((R / "reid_flight02.json").read_text())


def trace(run: str) -> list[dict]:
    return [json.loads(l) for l in (RUNS / run / "trace.jsonl").open(encoding="utf-8")]


def tool(run: str, name: str, which: int = 0) -> dict:
    return [r for r in trace(run) if r.get("kind") == "tool" and r["name"] == name][which]


def best_match(run: str, min_seen: int = 10) -> float:
    """Best fused score of a tracked candidate (seen in at least `min_seen` frames). A
    six-frame blip 34 m out in run 1, which the agent judged to be a tree trunk, touched
    0.34 once; the person was P5 at 0.24. The report states both."""
    best = 0.0
    for r in trace(run):
        for c in r.get("candidates_seen") or []:
            if c.get("times_seen", 0) >= min_seen:
                best = max(best, c["match"])
    return best


def asked(run: str) -> bool:
    return any(r.get("kind") == "tool" and r["name"] == "ask_coordinator" for r in trace(run))


def camtruth() -> dict:
    return json.loads((R / "camera_truth_flight03.json").read_text())["summary"]


def follow2() -> dict:
    return tool(RUN2, "follow_confirmed")["record"]


CLAIMS = [
    # re-identification on flight 02 footage
    ("docs/REPORT.md", r"(\d+) person crops", lambda: reid()["crops"]),
    ("docs/REPORT.md", r"of which (\d+) are impostors", lambda: len(reid()["impostors"])),
    ("docs/REPORT.md", r"median fused score was (\d+(?:\.\d+)?) to", lambda: min(r["me_median"] for r in reid()["runs"])),
    ("docs/REPORT.md", r"median fused score was \d+(?:\.\d+)? to\s+(\d+(?:\.\d+)?)", lambda: max(r["me_median"] for r in reid()["runs"])),
    ("docs/REPORT.md", r"no impostor exceeded (\d+(?:\.\d+)?)", lambda: max(r["impostors_max"] for r in reid()["runs"])),
    ("docs/REPORT.md", r"every crop of\s+the subject scored above every impostor", lambda: all(r["separable"] for r in reid()["runs"])),
    # finding, 25 October
    ("docs/REPORT.md", r"\| 1 \| ground photo[^|]*\| (\d\.\d+) \|", lambda: best_match(RUN1)),
    ("docs/REPORT.md", r"\| 2 \| aerial crop \(from run 1's footage\) \| (\d\.\d+) \|", lambda: best_match(RUN2)),
    ("docs/REPORT.md", r"\| 3 \| aerial crop \(run 2's confirmed crop\) \| (\d\.\d+) \|", lambda: best_match(RUN3)),
    ("docs/REPORT.md", r"\| 1 \| ground photo[^|]*\| \d\.\d+ \| (no) \|", lambda: not asked(RUN1)),
    ("docs/REPORT.md", r"touched (\d\.\d+) once", lambda: best_match(RUN1, 1)),
    # following, run 2
    ("docs/REPORT.md", r"`follow_confirmed` record: (\d+) s", lambda: follow2()["followed_s"]),
    ("docs/REPORT.md", r"`follow_confirmed` record: \d+ s, (\d+) frames", lambda: follow2()["frames"]),
    ("docs/REPORT.md", r"(\d+) locked \((\d+)%\)", lambda: follow2()["locked_frames"]),
    ("docs/REPORT.md", r"\d+ locked \((\d+)%\)", lambda: round(100 * follow2()["locked_fraction"])),
    ("docs/REPORT.md", r"(\d+) identity drops", lambda: follow2()["identity_drops"]),
    ("docs/REPORT.md", r"(\d+) pins\. Run 3", lambda: follow2()["pins"]),
    # camera truth from the footage
    ("docs/REPORT.md", r"\((\d+) marker detections at altitude", lambda: camtruth()["detections"]),
    ("docs/REPORT.md", r"measured itself (\d+(?:\.\d+)?) m above", lambda: camtruth()["measured_height_median_m"]),
    ("docs/REPORT.md", r"barometer\s+reported (\d+(?:\.\d+)?) m", lambda: camtruth()["reported_alt_median_m"]),
    ("docs/REPORT.md", r"median gap of (-?\d+(?:\.\d+)?) m", lambda: camtruth()["height_gap_median_m"]),
    ("docs/REPORT.md", r"report to a median of (-?\d+(?:\.\d+)?) degrees", lambda: camtruth()["pitch_gap_median_deg"]),
    ("README.md", r"camera\s+(\d+(?:\.\d+)?) m lower than the barometer", lambda: abs(camtruth()["height_gap_median_m"])),
    # README table
    ("README.md", r"best match (\d\.\d+) \| never asked", lambda: best_match(RUN1)),
    ("README.md", r"look closer \((\d\.\d+)\), asked", lambda: best_match(RUN2)),
    ("README.md", r"first scan at (\d\.\d+), asked", lambda: best_match(RUN3)),
    ("README.md", r"followed (\d+) s \| (\d+) pins; lock held (\d+)% of frames, (\d+) identity drops", lambda: follow2()["followed_s"]),
    ("README.md", r"followed \d+ s \| (\d+) pins", lambda: follow2()["pins"]),
    ("README.md", r"lock held (\d+)% of frames", lambda: round(100 * follow2()["locked_fraction"])),
    ("README.md", r"(\d+) identity drops", lambda: follow2()["identity_drops"]),
    # submission text
    ("docs/SUBMISSION.md", r"found on the first scan at (\d\.\d+), followed", lambda: best_match(RUN3)),
    ("docs/SUBMISSION.md", r"followed (\d+) s, (\d+) pins", lambda: follow2()["followed_s"]),
    ("docs/SUBMISSION.md", r"followed \d+ s, (\d+) pins", lambda: follow2()["pins"]),
    ("docs/SUBMISSION.md", r"lock held only (\d+)% of the time", lambda: round(100 * follow2()["locked_fraction"])),
    ("docs/SUBMISSION.md", r"best match (\d\.\d+) from the air", lambda: best_match(RUN1)),
    ("docs/SUBMISSION.md", r"looked closer at (\d\.\d+), asked", lambda: best_match(RUN2)),
    # the narration
    ("docs/DEMO_SCRIPT.md", r"scored me at zero point (two four)", lambda: f"{best_match(RUN1):.2f}" == "0.24"),
    ("docs/DEMO_SCRIPT.md", r"first scan, at zero point (eight five)", lambda: f"{best_match(RUN3):.2f}" == "0.85"),
    ("docs/DEMO_SCRIPT.md", r"stayed with me for (\d+) seconds and put down (\d+) pins", lambda: follow2()["followed_s"]),
    ("docs/DEMO_SCRIPT.md", r"stayed with me for \d+ seconds and put down (\d+) pins", lambda: follow2()["pins"]),
    ("docs/DEMO_SCRIPT.md", r"lock only held (\d+)% of the time", lambda: round(100 * follow2()["locked_fraction"])),
]

PLACEHOLDER = re.compile(r"\[(pending[^\]]*|X|N|-?\d+\]?)\]")
FINAL_DOCS = ["docs/REPORT.md", "docs/DEMO_SCRIPT.md", "README.md", "docs/SUBMISSION.md"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    bad = 0
    for doc, pattern, expect in CLAIMS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        want = expect()
        m = re.search(pattern, text, re.S)
        if isinstance(want, bool):
            ok = bool(m) == want
            got = bool(m)
        else:
            ok = bool(m) and abs(float(m.group(1)) - float(want)) < 1e-9
            got = m.group(1) if m else None
        print(f"{'ok  ' if ok else 'FAIL'} {doc}: {pattern!r} -> {got} (data: {want})")
        bad += not ok
    if a.final:
        for doc in FINAL_DOCS:
            p = ROOT / doc
            if p.exists():
                for hit in PLACEHOLDER.finditer(p.read_text(encoding="utf-8")):
                    print(f"FAIL {doc}: placeholder {hit.group(0)} still in a final build")
                    bad += 1
    print(f"\n{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
