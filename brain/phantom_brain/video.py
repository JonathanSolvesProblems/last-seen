"""Is the video feed actually carrying a picture?

Flight 01 lost several minutes to a detector that found nobody in 148 consecutive
frames. The detector was fine. The aircraft's camera session had died and the
decoder kept emitting its last, empty surface, so frames arrived at the normal
rate carrying nothing but black.

"Frames are arriving" and "the camera works" are different claims, and only the
first one is easy to measure. This module measures the second: a feed is healthy
when its pixels have some spread and successive frames are not byte-identical.

Used by the preflight check and by any script that is about to trust perception.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

# A black frame from a dead decoder is exactly 0, but JPEG ringing and sensor
# noise put a real night-time frame a little above it, so allow some headroom.
BLANK_MEAN = 8.0
BLANK_STD = 4.0


@dataclass
class FeedHealth:
    ok: bool
    reason: str
    frames: int
    blank_fraction: float
    distinct_fraction: float
    mean_luma: float

    def __str__(self) -> str:
        return (f"{self.reason} ({self.frames} frames, {100 * self.blank_fraction:.0f}% blank, "
                f"{100 * self.distinct_fraction:.0f}% distinct, mean luma {self.mean_luma:.1f})")


def is_blank(img: np.ndarray) -> bool:
    """True when the frame carries no picture: near-black and near-uniform.

    Both conditions are needed. A legitimately dark frame still has spread, and
    a legitimately flat frame (a white wall) is not dark.
    """
    if img is None or img.size == 0:
        return True
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(grey.mean()) < BLANK_MEAN and float(grey.std()) < BLANK_STD


def _fingerprint(img: np.ndarray) -> str:
    small = cv2.resize(img, (32, 18), interpolation=cv2.INTER_AREA)
    return hashlib.sha1(small.tobytes()).hexdigest()


class FeedMonitor:
    """Rolling verdict on the last `window` frames.

    Two failure modes, both of which keep the happy path's shape:
      blank  - the decoder is emitting an empty surface (the flight 01 failure)
      frozen - frames keep arriving but never change, so the feed is a still
    """

    def __init__(self, window: int = 30, min_frames: int = 10,
                 max_blank: float = 0.5, min_distinct: float = 0.2):
        self.window = window
        self.min_frames = min_frames
        self.max_blank = max_blank
        self.min_distinct = min_distinct
        self._blank: deque[bool] = deque(maxlen=window)
        self._prints: deque[str] = deque(maxlen=window)
        self._luma: deque[float] = deque(maxlen=window)
        self.total = 0

    def add(self, img: np.ndarray) -> None:
        if img is None or img.size == 0:
            self._blank.append(True)
            self._prints.append("empty")
            self._luma.append(0.0)
            self.total += 1
            return
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        self._blank.append(float(grey.mean()) < BLANK_MEAN and float(grey.std()) < BLANK_STD)
        self._prints.append(_fingerprint(img))
        self._luma.append(float(grey.mean()))
        self.total += 1

    def verdict(self, expect_motion: bool = True) -> FeedHealth:
        """`expect_motion` false while the aircraft is parked.

        A black feed is unambiguous: no camera produces it. Identical frames are
        not. A real sensor dithers even on a static scene, but a rendered one
        does not, and neither claim is worth aborting a flight over while the
        aircraft is sitting still looking at a car park. Check for stillness
        once it is airborne and the scene has to be changing.
        """
        n = len(self._blank)
        if n == 0:
            return FeedHealth(False, "no frames arrived at all", 0, 1.0, 0.0, 0.0)
        blank = sum(self._blank) / n
        distinct = len(set(self._prints)) / n
        luma = sum(self._luma) / n
        if n < self.min_frames:
            return FeedHealth(False, f"only {n} frames so far, need {self.min_frames}",
                              n, blank, distinct, luma)
        if blank > self.max_blank:
            return FeedHealth(False, "the feed is black: the camera session is dead, "
                                     "reconnect the aircraft in the bridge app",
                              n, blank, distinct, luma)
        if expect_motion and distinct < self.min_distinct:
            return FeedHealth(False, "the feed is frozen: frames arrive but never change",
                              n, blank, distinct, luma)
        return FeedHealth(True, "feed healthy", n, blank, distinct, luma)
