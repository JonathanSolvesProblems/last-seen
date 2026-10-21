"""Is this the person in the photo? Two independent OpenCV opinions, fused.

  appearance   OpenCV Model Zoo's person re-identification network
               (person_reid_youtu_2021nov.onnx, 768-d embedding) through
               cv2.dnn, compared by cosine similarity.
  clothing     HSV histograms of the upper and lower body, compared
               separately, because a blue shirt over black trousers is not
               the same evidence as a blue post.

Measured on flight 02's onboard clip (2026-09-30, 6 m up, 2.7K), with one
crop of me as the reference: all 223 other crops of me scored above the best
of 8 impostors (two posts and a cyclist, all detected as people) on both
signals; the network's medians were 0.88 for me against 0.22 for them. That
reference came from the same flight, so it says nothing yet about a photo
taken on the ground that morning, which is the real use. The field run is
what grades that.

Neither score is a probability. The thresholds below come from that clip and
are deliberately conservative: a match here only ever leads to a person
being ASKED, never to the aircraft acting on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parents[1] / "models" / "person_reid_youtu_2021nov.onnx"

# From flight 02 with three different references: impostors topped out at
# 0.18 to 0.22, 95% of my own crops scored 0.76 or more, and the low tail was
# me crouching or half out of frame. So:
ASK_AT = 0.60       # good enough to put in front of the coordinator
LOOK_AT = 0.30      # ambiguous: get closer before deciding anything
# below LOOK_AT: not them
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _hist(img: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Low-saturation pixels carry no hue, so they get their own value bins
    # rather than being smeared across hue: black trousers are evidence too.
    h = cv2.calcHist([hsv], [0, 1], None, [18, 6], [0, 180, 0, 256])
    v = cv2.calcHist([hsv], [2], cv2.inRange(hsv, (0, 0, 0), (180, 50, 255)), [8], [0, 256])
    out = np.concatenate([h.ravel(), v.ravel()]).astype(np.float32)
    return out / max(out.sum(), 1e-6)


def _bands(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Torso and legs, skipping the head (hair, sky behind it) and the feet
    (grass between them)."""
    h = crop.shape[0]
    return crop[int(0.18 * h):int(0.52 * h)], crop[int(0.52 * h):int(0.90 * h)]


@dataclass
class MatchScore:
    appearance: float | None     # cosine, -1..1; None if the network is unavailable
    clothing: float              # histogram correlation of the two bands, 0..1
    fused: float                 # what decisions read

    def to_dict(self) -> dict:
        return {"appearance": None if self.appearance is None else round(self.appearance, 3),
                "clothing": round(self.clothing, 3), "fused": round(self.fused, 3)}


class ReIdMatcher:
    """Implements the ReferenceMatcher protocol (score(crop) -> float), plus
    `explain` for the full breakdown the agent and the coordinator see."""

    def __init__(self, reference_bgr: np.ndarray, model_path: Path | str = MODEL):
        self.net = None
        if Path(model_path).exists():
            self.net = cv2.dnn.readNet(str(model_path))
        self.ref_feat = self._feat(reference_bgr)
        self.ref_bands = [_hist(b) for b in _bands(reference_bgr)]

    def _feat(self, img: np.ndarray) -> np.ndarray | None:
        if self.net is None or img.size == 0:
            return None
        x = cv2.resize(img, (128, 256))[:, :, ::-1].astype(np.float32) / 255.0
        x = (x - _MEAN) / _STD
        self.net.setInput(cv2.dnn.blobFromImage(x))
        f = self.net.forward().ravel()
        return f / max(float(np.linalg.norm(f)), 1e-9)

    def explain(self, crop_bgr: np.ndarray) -> MatchScore:
        if crop_bgr is None or crop_bgr.size == 0 or min(crop_bgr.shape[:2]) < 8:
            return MatchScore(None, 0.0, 0.0)
        f = self._feat(crop_bgr)
        app = None if f is None or self.ref_feat is None else float(f @ self.ref_feat)
        bands = [_hist(b) for b in _bands(crop_bgr)]
        corr = [max(0.0, cv2.compareHist(a, b, cv2.HISTCMP_CORREL)) for a, b in zip(self.ref_bands, bands)]
        # Torso counts more than legs: trousers are mostly dark for everybody.
        clothing = 0.65 * corr[0] + 0.35 * corr[1]
        if app is None:
            fused = clothing
        else:
            # Map the network's cosine onto 0..1 over the range it actually
            # spans for people (about 0.1 for strangers to 0.9 for a match).
            app01 = min(1.0, max(0.0, (app - 0.1) / 0.8))
            fused = 0.7 * app01 + 0.3 * clothing
        return MatchScore(app, clothing, fused)

    def score(self, crop_bgr: np.ndarray) -> float:
        return self.explain(crop_bgr).fused
