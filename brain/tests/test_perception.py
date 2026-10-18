from pathlib import Path

import cv2
import pytest

from phantom_brain.detect import ColorHistogramMatcher, CvDnnYoloDetector, OnnxYoloDetector

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "yolo11n.onnx"
SAMPLE = ROOT / "data" / "samples" / "bus.jpg"

pytestmark = pytest.mark.skipif(not (MODEL.exists() and SAMPLE.exists()),
                                reason="needs models/yolo11n.onnx and data/samples/bus.jpg")


def test_onnx_yolo_finds_people():
    d = OnnxYoloDetector(str(MODEL), imgsz=640, conf=0.25)
    img = cv2.imread(str(SAMPLE))
    dets = d.detect(img)
    assert len(dets) >= 3
    for x in dets:
        assert 0 <= x.x1 < x.x2 <= img.shape[1] and 0 <= x.y1 < x.y2 <= img.shape[0]
        assert x.label == "person"


def test_color_matcher_prefers_same_outfit():
    d = CvDnnYoloDetector(str(MODEL), imgsz=640, conf=0.25)
    img = cv2.imread(str(SAMPLE))
    dets = sorted(d.detect(img), key=lambda x: x.x1)
    assert len(dets) >= 3
    # the person in the beige coat (leftmost full-body detection) is the reference
    ref_det = next(x for x in dets if x.w > 60)
    ref = ref_det.crop(img)
    m = ColorHistogramMatcher(ref)
    scores = [(x, m.score(x.crop(img))) for x in dets if x.w > 60]
    best = max(scores, key=lambda s: s[1])[0]
    assert best is ref_det
    # and the same crop under a mild brightness change still matches best
    darker = cv2.convertScaleAbs(ref, alpha=0.85, beta=-10)
    assert m.score(darker) > 0.8


def test_opencv_dnn_and_onnxruntime_find_the_same_people():
    """The field detector moved from onnxruntime to OpenCV 5's DNN module. Same
    model, same tensors, same thresholds: the boxes must be the same boxes, or
    every detection number measured before the swap stops being comparable."""
    img = cv2.imread(str(SAMPLE))
    cv = sorted(CvDnnYoloDetector(str(MODEL), imgsz=640, conf=0.25).detect(img), key=lambda d: d.x1)
    ort = sorted(OnnxYoloDetector(str(MODEL), imgsz=640, conf=0.25).detect(img), key=lambda d: d.x1)
    assert len(cv) == len(ort) >= 3
    for a, b in zip(cv, ort):
        assert abs(a.x1 - b.x1) < 2 and abs(a.y2 - b.y2) < 2
        assert abs(a.conf - b.conf) < 0.01
