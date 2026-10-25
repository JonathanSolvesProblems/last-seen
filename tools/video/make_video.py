"""Narration in, demo out.

    python make_video.py <narration.wav> [--out demo.mp4]

1. transcribe with faster-whisper `small` (vidkit's own function, so the words land in
   the exact shape vidkit's caption builder expects), with the product's vocabulary
   as the initial prompt
2. write the plan skeleton with `_transcript`, then build_plan.py maps every sentence
   to its shot
3. vidkit assemble --edit-plan renders: captions, lower thirds, audio chain, intro card
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    audio = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "demo.mp4"
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segs_iter, info = model.transcribe(
        str(audio), word_timestamps=True, vad_filter=False,
        initial_prompt="Last Seen. OpenCV 5, DNN engine, YOLO, re-ID network, OpenCV model zoo, Claude, AWS, Lambda, DynamoDB, S3, CloudFormation, geofence, Phantom 3, zero point two four, zero point eight five.")
    segments, text = [], []
    for s in segs_iter:
        words = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)} for w in (s.words or [])]
        segments.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(), "words": words})
        text.append(s.text.strip())
    transcript = {"segments": segments, "text": " ".join(text), "duration": float(info.duration)}
    print(f"transcribed {transcript['duration']:.1f}s, {sum(len(s['words']) for s in segments)} words")
    print(transcript["text"])
    skel = ROOT / "broll" / "_work" / "plan_skeleton.json"
    skel.write_text(json.dumps({"project_name": "Last Seen", "segments": [], "add_intro_card": True, "add_outro_card": False,
                                "_transcript": transcript}, indent=1), encoding="utf-8")
    plan = out.with_suffix(".edit-plan.json")
    subprocess.run([sys.executable, str(HERE / "build_plan.py"), str(skel), str(plan)], check=True)
    subprocess.run(["vidkit", "assemble", str(audio), "--clips-dir", str(ROOT / "broll"), "--edit-plan", str(plan),
                    "--out", str(out), "--project-name", "Last Seen"], check=True)
    print("rendered", out)


if __name__ == "__main__":
    main()
