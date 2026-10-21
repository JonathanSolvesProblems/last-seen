"""The person in the loop: how a candidate reaches the search coordinator, and back.

Three channels, one interface (`ask` returns "confirmed" | "rejected" |
"no_answer"):

  MissionControlCoordinator   the mission-control web page (local now, AWS when
                              deployed). Field and demo use.
  ConsoleCoordinator          a y/n prompt in the terminal. Bench use.
  SimulatedCoordinator        answers from the simulator's ground truth. Tests
                              only, and it says so in every answer it records.

`pin` and `finish` are optional: channels that have a map get the follow's
positions as they happen.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Protocol

from ..runtime import Candidate, Pin


class Coordinator(Protocol):
    async def ask(self, c: Candidate, reference: str, message: str, timeout_s: float) -> str: ...


class SimulatedCoordinator:
    """Confirms the simulator's target, rejects everyone else. Labelled as such."""

    label = "SIMULATED COORDINATOR (answers from simulator ground truth)"

    def __init__(self, target_id: str = "target", delay_s: float = 1.0):
        self.target_id = target_id
        self.delay = delay_s
        self.asked: list[dict] = []

    async def ask(self, c: Candidate, reference: str, message: str, timeout_s: float) -> str:
        await asyncio.sleep(self.delay)
        d = "confirmed" if c.truth_id == self.target_id else "rejected"
        self.asked.append({"candidate": c.id, "truth": c.truth_id, "decision": d, "by": self.label})
        return d


class ConsoleCoordinator:
    async def ask(self, c: Candidate, reference: str, message: str, timeout_s: float) -> str:
        print(f"\n=== {c.id}: match {c.best:.2f}, crop {c.crop}\n    agent says: {message}")
        try:
            ans = await asyncio.wait_for(asyncio.to_thread(input, "    is this them? [y/n] "), timeout_s)
        except asyncio.TimeoutError:
            return "no_answer"
        return "confirmed" if ans.strip().lower().startswith("y") else "rejected"


class MissionControlCoordinator:
    """Talks to the mission-control server over plain HTTP JSON.

    The server shows the crop beside the reference photo with two buttons, and
    the map. This class posts the question and polls for the answer; it never
    decides anything itself.
    """

    def __init__(self, base_url: str, token: str, mission_id: str):
        self.base = base_url.rstrip("/")
        self.token = token
        self.mission = mission_id

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(f"{self.base}{path}", data=data, method=method,
                                   headers={"Content-Type": "application/json",
                                            "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return json.loads(resp.read() or b"{}")

    async def ask(self, c: Candidate, reference: str, message: str, timeout_s: float) -> str:
        import base64  # noqa: PLC0415
        crop = base64.b64encode(open(c.crop, "rb").read()).decode() if c.crop else None
        q = await asyncio.to_thread(self._req, "POST", f"/api/missions/{self.mission}/questions",
                                    {"candidate": c.id, "match": round(c.best, 2), "message": message,
                                     "crop_jpeg_b64": crop, "e": c.e, "n": c.n, "asked_at": time.time()})
        qid = q["id"]
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            try:
                a = await asyncio.to_thread(self._req, "GET", f"/api/missions/{self.mission}/questions/{qid}")
            except OSError:
                a = {}
            if a.get("decision") in ("confirmed", "rejected"):
                return a["decision"]
            await asyncio.sleep(1.0)
        return "no_answer"

    async def status(self, payload: dict, reference_b64: str | None = None) -> dict:
        body = dict(payload)
        if reference_b64:
            body["reference_jpeg_b64"] = reference_b64
        return await asyncio.to_thread(self._req, "POST", f"/api/missions/{self.mission}/state", body)

    async def pin(self, p: Pin) -> None:
        try:
            await asyncio.to_thread(self._req, "POST", f"/api/missions/{self.mission}/pins",
                                    {"t": p.t, "e": p.e, "n": p.n, "phone_e": p.phone_e, "phone_n": p.phone_n})
        except OSError:
            pass                          # the map missing a pin never touches the flying

    async def finish(self, outcome: str, summary: str) -> None:
        try:
            await asyncio.to_thread(self._req, "POST", f"/api/missions/{self.mission}/finish",
                                    {"outcome": outcome, "summary": summary})
        except OSError:
            pass
