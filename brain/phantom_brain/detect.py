"""Task 4, Detect: find people in a frame and decide if one is the target.

Two stages, both pluggable:

  Detector         frame -> list[Detection]        (YOLO on the real drone,
                                                     OracleDetector in the sim)
  ReferenceMatcher person crop -> score in [0, 1]  (colour histogram, or
                                                     Claude vision)

TargetIdentifier combines them with hysteresis so a single lucky frame does
not "confirm" the target and a single missed frame does not lose it.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float = 1.0
    label: str = "person"
    match: float | None = None     # reference match score once computed
    truth_id: str | None = None    # sim only: which simulated person this is

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    @property
    def feet(self) -> tuple[float, float]:
        """Pixel where the person touches the ground (bottom centre)."""
        return self.cx, self.y2

    def crop(self, img: np.ndarray, pad: float = 0.15) -> np.ndarray:
        h, w = img.shape[:2]
        px, py = self.w * pad, self.h * pad
        x1 = int(max(0, self.x1 - px))
        y1 = int(max(0, self.y1 - py))
        x2 = int(min(w, self.x2 + px))
        y2 = int(min(h, self.y2 + py))
        if x2 <= x1 or y2 <= y1:
            return img[0:1, 0:1]
        return img[y1:y2, x1:x2]


class Detector(Protocol):
    def detect(self, frame_bgr: np.ndarray, truth: dict[str, Any] | None = None) -> list[Detection]:
        ...


class YoloDetector:
    """Ultralytics YOLO person detector. Imported lazily so tests run without torch."""

    def __init__(self, weights: str = "yolo11n.pt", imgsz: int = 960, conf: float = 0.25,
                 device: str | None = None):
        from ultralytics import YOLO  # noqa: PLC0415
        self.model = YOLO(weights)
        self.imgsz = imgsz
        self.conf = conf
        self.device = device

    def detect(self, frame_bgr: np.ndarray, truth: dict[str, Any] | None = None) -> list[Detection]:
        results = self.model.predict(frame_bgr, imgsz=self.imgsz, conf=self.conf,
                                    classes=[0], verbose=False, device=self.device)
        out: list[Detection] = []
        for r in results:
            if r.boxes is None:
                continue
            for b in r.boxes:
                x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                out.append(Detection(x1, y1, x2, y2, float(b.conf[0])))
        return out


class _YoloOnnxBase:
    """Shared pre- and post-processing for an Ultralytics YOLO11 ONNX export
    (input NCHW float RGB 0..1, output [1, 84, N], class 0 = person).

    Subclasses supply only `_infer`, so the two backends are guaranteed to see
    identical tensors and apply identical thresholds. That is what makes the
    backend comparison a comparison of backends and nothing else.
    """

    backend = "?"

    def __init__(self, imgsz: int, conf: float, iou: float):
        self.imgsz = int(round(imgsz / 32)) * 32
        self.conf = conf
        self.iou = iou
        self.last_infer_ms: float | None = None

    def _infer(self, blob: np.ndarray) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    def _letterbox(self, img: np.ndarray) -> tuple[np.ndarray, float, int, int]:
        h, w = img.shape[:2]
        s = self.imgsz / max(h, w)
        nw, nh = int(round(w * s)), int(round(h * s))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        # pad to a multiple of 32 on each side (keeps the model happy and the
        # tensor small for 16:9 frames)
        pw = (32 - nw % 32) % 32
        ph = (32 - nh % 32) % 32
        left, top = pw // 2, ph // 2
        canvas = np.full((nh + ph, nw + pw, 3), 114, dtype=np.uint8)
        canvas[top:top + nh, left:left + nw] = resized
        return canvas, s, left, top

    def detect(self, frame_bgr: np.ndarray, truth: dict[str, Any] | None = None) -> list[Detection]:
        canvas, s, left, top = self._letterbox(frame_bgr)
        # blobFromImage does the BGR->RGB swap, the 1/255 scale and the NCHW
        # transpose in one call, on OpenCV's own optimised path.
        blob = cv2.dnn.blobFromImage(canvas, scalefactor=1.0 / 255.0, swapRB=True)
        t0 = time.perf_counter()
        out = self._infer(blob)                                        # [1, 84, N]
        self.last_infer_ms = (time.perf_counter() - t0) * 1000.0
        pred = out[0].T                                                # [N, 84]
        scores = pred[:, 4]                                            # class 0 = person
        keep = scores >= self.conf
        pred, scores = pred[keep], scores[keep]
        if len(pred) == 0:
            return []
        cx, cy, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        x1 = (cx - w / 2 - left) / s
        y1 = (cy - h / 2 - top) / s
        x2 = (cx + w / 2 - left) / s
        y2 = (cy + h / 2 - top) / s
        boxes = [[float(a), float(b), float(c - a), float(d - b)] for a, b, c, d in zip(x1, y1, x2, y2)]
        idx = cv2.dnn.NMSBoxes(boxes, scores.astype(float).tolist(), self.conf, self.iou)
        if idx is None or len(idx) == 0:
            return []
        fh, fw = frame_bgr.shape[:2]
        out_dets = []
        for i in np.array(idx).flatten():
            bx, by, bw, bh = boxes[i]
            out_dets.append(Detection(max(0.0, bx), max(0.0, by), min(float(fw), bx + bw), min(float(fh), by + bh),
                                      float(scores[i])))
        return out_dets


class CvDnnYoloDetector(_YoloOnnxBase):
    """YOLO11 person detector on OpenCV 5's DNN module. The default.

    Same ONNX file as the onnxruntime backend, and on the same input the two
    agree to within 0.002 on raw scores (checked 2026-10-18), so swapping this in
    changes where the work runs and nothing about what it decides.
    """

    backend = "opencv-dnn"

    def __init__(self, model_path: str = "models/yolo11n.onnx", imgsz: int = 960, conf: float = 0.25,
                 iou: float = 0.5):
        super().__init__(imgsz, conf, iou)
        # OpenCV 5 loads ONNX into its new graph engine by default. Setting a
        # backend or target here would push it back to the classic engine (and
        # warns that the new one does not take targets), so leave both alone.
        self.net = cv2.dnn.readNet(model_path)

    def _infer(self, blob: np.ndarray) -> np.ndarray:
        self.net.setInput(blob)
        return self.net.forward()


class OnnxYoloDetector(_YoloOnnxBase):
    """YOLO11 on ONNX Runtime. Kept as the comparison backend for the OpenCV
    DNN path, and as a fallback if a model ever needs an operator OpenCV lacks.

    No torch, so it also runs where Windows Smart App Control blocks torch's
    unsigned DLLs. models/yolo11n.onnx is the official Ultralytics export
    mirrored on Hugging Face (deepghs/yolos).
    """

    backend = "onnxruntime"

    def __init__(self, model_path: str = "models/yolo11n.onnx", imgsz: int = 960, conf: float = 0.25,
                 iou: float = 0.5, threads: int | None = None):
        super().__init__(imgsz, conf, iou)
        import onnxruntime as ort  # noqa: PLC0415
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        providers = ["CPUExecutionProvider"]
        if "CUDAExecutionProvider" in ort.get_available_providers():
            providers.insert(0, "CUDAExecutionProvider")
        self.session = ort.InferenceSession(model_path, opts, providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    def _infer(self, blob: np.ndarray) -> np.ndarray:
        return self.session.run(None, {self.input_name: blob})[0]


def make_detector(kind: str, weights: str, imgsz: int, conf: float) -> Detector:
    """oracle | yolo (OpenCV DNN when the weights end in .onnx, else ultralytics)
    | yolo-cv (OpenCV DNN) | yolo-onnx (onnxruntime, for comparison)"""
    if kind == "oracle":
        return OracleDetector()
    if kind == "yolo-onnx":
        return OnnxYoloDetector(weights, imgsz, conf)
    if kind == "yolo-cv" or (kind == "yolo" and weights.lower().endswith(".onnx")):
        return CvDnnYoloDetector(weights, imgsz, conf)
    if kind == "yolo":
        return YoloDetector(weights, imgsz, conf)
    raise ValueError(f"unknown detector {kind}")


@dataclass
class OracleNoise:
    miss_prob: float = 0.15          # chance a visible person is not detected
    jitter_px: float = 3.0           # bbox noise
    false_positive_prob: float = 0.02
    min_box_px: float = 6.0          # below this size the oracle cannot see it
    seed: int | None = None


class OracleDetector:
    """Sim-only detector that reads ground truth from the frame header.

    truth = {"people": [{"id": "target", "bbox": [x1, y1, x2, y2], "visible": bool}, ...]}
    """

    def __init__(self, noise: OracleNoise | None = None):
        self.noise = noise or OracleNoise()
        self.rng = random.Random(self.noise.seed)

    def detect(self, frame_bgr: np.ndarray, truth: dict[str, Any] | None = None) -> list[Detection]:
        out: list[Detection] = []
        if not truth:
            return out
        h, w = frame_bgr.shape[:2]
        for p in truth.get("people", []):
            if not p.get("visible"):
                continue
            x1, y1, x2, y2 = p["bbox"]
            if (x2 - x1) < self.noise.min_box_px or (y2 - y1) < self.noise.min_box_px:
                continue
            if self.rng.random() < self.noise.miss_prob:
                continue
            j = self.noise.jitter_px
            out.append(Detection(
                x1 + self.rng.uniform(-j, j), y1 + self.rng.uniform(-j, j),
                x2 + self.rng.uniform(-j, j), y2 + self.rng.uniform(-j, j),
                conf=self.rng.uniform(0.5, 0.95), truth_id=p.get("id")))
        if self.rng.random() < self.noise.false_positive_prob:
            bw, bh = self.rng.uniform(10, 40), self.rng.uniform(20, 60)
            x = self.rng.uniform(0, w - bw)
            y = self.rng.uniform(0, h - bh)
            out.append(Detection(x, y, x + bw, y + bh, conf=0.35, truth_id="fp"))
        return out


# ---- reference matching -----------------------------------------------------

class ReferenceMatcher(Protocol):
    def score(self, crop_bgr: np.ndarray) -> float:
        ...


class ColorHistogramMatcher:
    """Cheap local matcher: HSV histogram correlation with the reference crop.

    Works when the target wears something distinctive. Used as the default and
    as the pre-filter before an (expensive) Claude call.
    """

    def __init__(self, reference_bgr: np.ndarray, bins: tuple[int, int] = (24, 8)):
        self.bins = bins
        self.ref_hist = self._hist(reference_bgr)

    def _hist(self, img: np.ndarray) -> np.ndarray:
        if img.size == 0:
            return np.zeros((self.bins[0], self.bins[1]), dtype=np.float32)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # ignore very dark and very unsaturated pixels (shadows, ground)
        mask = cv2.inRange(hsv, (0, 40, 40), (180, 255, 255))
        hist = cv2.calcHist([hsv], [0, 1], mask, list(self.bins), [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    def score(self, crop_bgr: np.ndarray) -> float:
        h = self._hist(crop_bgr)
        if float(h.sum()) == 0 or float(self.ref_hist.sum()) == 0:
            return 0.0
        c = cv2.compareHist(self.ref_hist, h, cv2.HISTCMP_CORREL)
        return float(max(0.0, min(1.0, (c + 1.0) / 2.0)))


class ClaudeMatcher:
    """Ask Claude whether a person crop is the person in the reference photo.

    Rate limited (min_interval seconds between calls) because it sits in a
    10 Hz loop. Returns a score in [0, 1]; on any API failure returns 0 and
    logs, so the flight never depends on the network.
    """

    SYSTEM = ("You compare a reference photo of one specific person with a small "
              "crop taken from an aerial drone camera. Decide whether the crop shows "
              "that same person, using clothing colours, build, hair and any visible "
              "features. Reply with only a JSON object: "
              '{"same_person": true|false, "confidence": 0.0-1.0, "reason": "<8 words>"}')

    def __init__(self, reference_bgr: np.ndarray, model: str = "claude-opus-5",
                 min_interval: float = 1.0, client: Any | None = None):
        import anthropic  # noqa: PLC0415
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.min_interval = min_interval
        self._last_call = 0.0
        self._last_score = 0.0
        self.ref_b64 = self._b64(reference_bgr, max_side=768)
        self.calls = 0

    @staticmethod
    def _b64(img: np.ndarray, max_side: int = 384) -> str:
        h, w = img.shape[:2]
        s = max_side / max(h, w, 1)
        if s < 1:
            img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.standard_b64encode(buf.tobytes()).decode("ascii")

    def score(self, crop_bgr: np.ndarray) -> float:
        now = time.monotonic()
        if now - self._last_call < self.min_interval:
            return self._last_score
        self._last_call = now
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                system=[{"type": "text", "text": self.SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "Reference photo of the target person:"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": self.ref_b64}},
                    {"type": "text", "text": "Crop from the drone camera:"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": self._b64(crop_bgr)}},
                    {"type": "text", "text": "Is this the same person? JSON only."},
                ]}],
            )
            self.calls += 1
            text = "".join(b.text for b in resp.content if b.type == "text")
            m = re.search(r"\{.*\}", text, re.S)
            data = json.loads(m.group(0)) if m else {}
            conf = float(data.get("confidence", 0.0))
            same = bool(data.get("same_person", False))
            self._last_score = conf if same else 1.0 - conf
            log.info("claude match: same=%s conf=%.2f (%s)", same, conf, data.get("reason", ""))
        except Exception as e:  # noqa: BLE001
            log.warning("claude matcher failed: %s", e)
            self._last_score = 0.0
        return self._last_score


class OracleMatcher:
    """Sim-only: the detection carries the simulated person's id."""

    def __init__(self, target_id: str = "target", noise: float = 0.1, seed: int | None = None):
        self.target_id = target_id
        self.noise = noise
        self.rng = random.Random(seed)
        self._current: Detection | None = None

    def bind(self, det: Detection) -> None:
        self._current = det

    def score(self, crop_bgr: np.ndarray) -> float:
        if self._current is None:
            return 0.0
        base = 0.9 if self._current.truth_id == self.target_id else 0.15
        return max(0.0, min(1.0, base + self.rng.uniform(-self.noise, self.noise)))


# ---- combining ----------------------------------------------------------------

@dataclass
class IdentifierConfig:
    match_threshold: float = 0.6
    confirm_frames: int = 3         # consecutive frames above threshold to confirm
    lose_frames: int = 15           # consecutive frames without the target to lose it
    max_assoc_px: float = 120.0     # track association distance between frames


@dataclass
class TargetState:
    confirmed: bool = False
    last_det: Detection | None = None
    last_seen_t: float = 0.0
    hits: int = 0
    misses: int = 0
    candidates_scored: int = 0
    history: list[tuple[float, float, float]] = field(default_factory=list)  # (t, cx, cy)


class TargetIdentifier:
    def __init__(self, detector: Detector, matcher: ReferenceMatcher,
                 config: IdentifierConfig | None = None):
        self.detector = detector
        self.matcher = matcher
        self.cfg = config or IdentifierConfig()
        self.state = TargetState()

    def reset(self) -> None:
        self.state = TargetState()

    def update(self, frame_bgr: np.ndarray, t: float,
               truth: dict[str, Any] | None = None) -> tuple[Detection | None, list[Detection]]:
        """Run detection + matching on one frame.

        Returns (target detection or None, all detections).
        """
        dets = self.detector.detect(frame_bgr, truth)
        st = self.state
        best: Detection | None = None
        best_score = 0.0
        for d in dets:
            if isinstance(self.matcher, OracleMatcher):
                self.matcher.bind(d)
            d.match = self.matcher.score(d.crop(frame_bgr))
            st.candidates_scored += 1
            # Prefer continuity: if we have a track, weight nearby detections.
            score = d.match
            if st.last_det is not None and st.confirmed:
                dpx = np.hypot(d.cx - st.last_det.cx, d.cy - st.last_det.cy)
                if dpx > self.cfg.max_assoc_px:
                    score *= 0.5
            if score > best_score:
                best, best_score = d, score

        if best is not None and best_score >= self.cfg.match_threshold:
            st.hits += 1
            st.misses = 0
            st.last_det = best
            st.last_seen_t = t
            st.history.append((t, best.cx, best.cy))
            if len(st.history) > 300:
                st.history = st.history[-300:]
            if st.hits >= self.cfg.confirm_frames:
                st.confirmed = True
            return (best if st.confirmed else None), dets

        st.misses += 1
        st.hits = 0
        if st.confirmed and st.misses > self.cfg.lose_frames:
            log.info("target lost after %d misses", st.misses)
            st.confirmed = False
            st.last_det = None
        return None, dets

    def seconds_since_seen(self, now: float) -> float | None:
        if self.state.last_seen_t == 0.0:
            return None
        return now - self.state.last_seen_t


def draw_detections(img: np.ndarray, dets: list[Detection], target: Detection | None) -> np.ndarray:
    out = img.copy()
    for d in dets:
        color = (0, 200, 255)
        if target is not None and d is target:
            color = (0, 0, 255)
        cv2.rectangle(out, (int(d.x1), int(d.y1)), (int(d.x2), int(d.y2)), color, 2)
        label = f"{d.label} {d.conf:.2f}"
        if d.match is not None:
            label += f" m={d.match:.2f}"
        cv2.putText(out, label, (int(d.x1), max(12, int(d.y1) - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return out
