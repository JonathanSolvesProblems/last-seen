"""Turns a tool call into flight, perception, or a question to a person.

Every failure comes back structured (isError, errorCategory, isRetryable, a
sentence), so the model can tell "the person said no" from "that point is
outside the area" from "the link dropped" and act differently on each. A
failure is never dressed as an empty success.

Results carry pictures. When a call concerns a candidate, its best crop goes
back to the model as an image beside the numbers, so the next decision is made
on what the camera saw, not only on a score: that is the perception-decision
link the Agentic Vision rules ask for, and the trace records it.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2

from ..reid import LOOK_AT
from ..runtime import MissionRuntime, NotConfirmed

MAX_LOOKS = 2


@dataclass
class ToolOutcome:
    content: list[dict]
    is_error: bool = False
    ended: bool = False                    # end_mission ran: the loop must stop
    record: dict = field(default_factory=dict)   # for the trace, never sent to the model


def _image_block(path: str | None, max_side: int = 256) -> dict | None:
    if not path or not Path(path).exists():
        return None
    img = cv2.imread(path)
    if img is None:
        return None
    s = max_side / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.tobytes()).decode()}}


def _error(category: str, retryable: bool, message: str) -> ToolOutcome:
    body = {"isError": True, "errorCategory": category, "isRetryable": retryable, "message": message}
    return ToolOutcome([{"type": "text", "text": json.dumps(body)}], is_error=True, record=body)


class ToolExecutor:
    def __init__(self, runtime: MissionRuntime, coordinator, *, leash_m: float,
                 reference_path: str, ask_timeout_s: float = 90.0):
        self.rt = runtime
        self.coord = coordinator
        self.leash = leash_m
        self.reference = reference_path
        self.ask_timeout = ask_timeout_s
        self.outcome: dict | None = None

    def _with_crops(self, payload: dict, ids: list[str]) -> list[dict]:
        blocks: list[dict] = [{"type": "text", "text": json.dumps(payload)}]
        for cid in ids:
            c = self.rt.candidates.get(cid)
            img = _image_block(c.crop if c else None)
            if img is not None:
                blocks.append({"type": "text", "text": f"Best view of {cid}:"})
                blocks.append(img)
        return blocks

    def _top_ids(self, n: int = 3) -> list[str]:
        live = [c for c in self.rt.live() if c.status != "rejected" and c.best >= LOOK_AT]
        return [c.id for c in sorted(live, key=lambda c: -c.best)[:n]]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        try:
            return await getattr(self, f"_{name}")(**args)
        except AttributeError:
            return _error("validation", False, f"there is no tool called {name}")
        except NotConfirmed as e:
            return _error("permission", False, str(e))
        except KeyError as e:
            return _error("validation", False, f"no candidate {e}; use an id from the candidate list")
        except ValueError as e:
            return _error("validation", False, str(e))
        except ConnectionError as e:
            return _error("transient", True, f"link to the aircraft: {e}")

    # ---- the six tools --------------------------------------------------------

    async def _scan_here(self, degrees: int) -> ToolOutcome:
        if not 90 <= degrees <= 360:
            return _error("validation", False, "degrees must be between 90 and 360")
        r = await self.rt.scan_here(float(degrees))
        r["frames_analysed_so_far"] = self.rt.frames
        return ToolOutcome(self._with_crops(r, r["new_candidates"][:3] or self._top_ids(2)),
                           record={"new": r["new_candidates"]})

    async def _fly_to(self, east_m: float, north_m: float) -> ToolOutcome:
        r = await self.rt.fly_to(float(east_m), float(north_m))
        return ToolOutcome(self._with_crops(r, r["new_candidates"][:3]),
                           record={"arrived": r["arrived"], "new": r["new_candidates"]})

    async def _look_closer(self, candidate_id: str, reason: str) -> ToolOutcome:
        c = self.rt.candidates[candidate_id]
        if c.status == "rejected":
            return _error("business", False, f"the coordinator already said {candidate_id} is not them")
        if c.looks >= MAX_LOOKS:
            return _error("business", False, f"{candidate_id} has had {c.looks} closer looks; decide with what you have")
        r = await self.rt.look_closer(candidate_id)
        return ToolOutcome(self._with_crops(r, [candidate_id]),
                           record={"before": r["match_before"], "after": r["match_after"]})

    async def _ask_coordinator(self, candidate_id: str, message: str) -> ToolOutcome:
        c = self.rt.candidates[candidate_id]
        if c.status == "rejected":
            return _error("business", False, f"the coordinator already said {candidate_id} is not them")
        if c.status == "confirmed":
            return ToolOutcome([{"type": "text", "text": json.dumps({"decision": "confirmed", "already": True})}])
        if c.best < LOOK_AT:
            return _error("business", False,
                          f"{candidate_id} matches at {c.best:.2f}, below the bar for asking a person. "
                          f"Look closer or keep searching.")
        decision = await self.rt.hover_while(
            self.coord.ask(c, self.reference, message, timeout_s=self.ask_timeout))
        if decision == "confirmed":
            c.status = "confirmed"
        elif decision == "rejected":
            c.status = "rejected"
        body = {"candidate_id": candidate_id, "decision": decision,
                "note": ("no_answer is not a no: you may keep searching and ask again later"
                         if decision == "no_answer" else None)}
        return ToolOutcome([{"type": "text", "text": json.dumps(body)}], record=body)

    async def _follow_confirmed(self, candidate_id: str, seconds: int) -> ToolOutcome:
        if not 10 <= seconds <= 180:
            return _error("validation", False, "seconds must be between 10 and 180")
        on_pin = getattr(self.coord, "pin", None)
        r = await self.rt.follow(candidate_id, float(seconds), self.leash, on_pin=on_pin)
        return ToolOutcome([{"type": "text", "text": json.dumps(r)}], record=r)

    async def _end_mission(self, outcome: str, summary: str) -> ToolOutcome:
        self.outcome = {"outcome": outcome, "summary": summary}
        await self.rt.fs.return_to_origin()
        self.rt.save()
        if hasattr(self.coord, "finish"):
            await self.coord.finish(outcome, summary)
        return ToolOutcome([{"type": "text", "text": json.dumps({"returned_home": True, **self.outcome})}],
                           ended=True, record=self.outcome)
