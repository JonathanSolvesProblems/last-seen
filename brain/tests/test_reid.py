"""The re-identification matcher: OpenCV Model Zoo network plus clothing bands."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from phantom_brain.detect import CvDnnYoloDetector
from phantom_brain.reid import ASK_AT, LOOK_AT, MODEL, ReIdMatcher

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "samples" / "bus.jpg"
YOLO = ROOT / "models" / "yolo11n.onnx"

pytestmark = pytest.mark.skipif(not (MODEL.exists() and SAMPLE.exists() and YOLO.exists()),
                                reason="needs the re-ID and YOLO models and the sample image")


def people():
    img = cv2.imread(str(SAMPLE))
    dets = [d for d in CvDnnYoloDetector(str(YOLO), imgsz=640).detect(img) if d.w > 60]
    return img, sorted(dets, key=lambda d: d.x1)


def test_the_reference_person_beats_everyone_else():
    img, dets = people()
    ref = dets[0].crop(img)
    m = ReIdMatcher(ref)
    # the same person in a slightly different crop, as the next frame would give
    jittered = img[int(dets[0].y1) + 4:int(dets[0].y2) - 2, int(dets[0].x1) + 3:int(dets[0].x2) - 3]
    same = m.score(jittered)
    others = [m.score(d.crop(img)) for d in dets[1:]]
    assert same > max(others)
    assert same >= ASK_AT


def test_thresholds_are_ordered():
    assert 0 < LOOK_AT < ASK_AT < 1


def test_degenerate_crops_score_zero_rather_than_raising():
    m = ReIdMatcher(np.full((200, 80, 3), 120, np.uint8))
    assert m.score(np.zeros((0, 0, 3), np.uint8)) == 0.0
    assert m.score(np.zeros((4, 4, 3), np.uint8)) == 0.0


def test_explain_reports_both_opinions():
    img, dets = people()
    s = ReIdMatcher(dets[0].crop(img)).explain(dets[1].crop(img)).to_dict()
    assert set(s) == {"appearance", "clothing", "fused"}
    assert s["appearance"] is not None
