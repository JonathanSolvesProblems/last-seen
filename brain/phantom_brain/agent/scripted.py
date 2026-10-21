"""A rule-based stand-in for the model, speaking the same Messages API shape.

Two jobs, both labelled as what they are:

  * Lets the loop, the tools, the confirmation gate and the coordinator be
    exercised end to end in the simulator with no API key and no cost.
  * Is the "rules, no LLM" arm of the comparison: the same perception and the
    same tools, driven by fixed thresholds instead of a model's judgement.

It never sees the simulator's ground truth: it reads only the tool results the
real model would read.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any

from ..reid import ASK_AT, LOOK_AT

LABEL = "RULE-BASED POLICY (no language model)"


@dataclass
class _Block:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict | None = None

    def model_dump(self, exclude_none: bool = True) -> dict:
        d = {"type": self.type, "text": self.text, "id": self.id, "name": self.name, "input": self.input}
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _Response:
    content: list[_Block]
    stop_reason: str
    usage: _Usage = field(default_factory=_Usage)


class _Messages:
    def __init__(self, owner: "ScriptedClient"):
        self.o = owner

    async def create(self, **kw: Any) -> _Response:
        return self.o.decide(kw["messages"])


class _Beta:
    def __init__(self, owner):
        self.messages = _Messages(owner)


class ScriptedClient:
    def __init__(self, waypoints: list[tuple[float, float]], follow_s: int = 30):
        self.waypoints = list(waypoints)
        self.follow_s = follow_s
        self.beta = _Beta(self)
        self._ids = itertools.count(1)
        self.state = "scan"
        self.asked: set[str] = set()
        self.looked: dict[str, int] = {}

    def _call(self, name: str, args: dict, why: str) -> _Response:
        return _Response([_Block("text", text=f"[{LABEL}] {why}"),
                          _Block("tool_use", id=f"toolu_{next(self._ids)}", name=name, input=args)], "tool_use")

    @staticmethod
    def _last_result(messages: list[dict]) -> dict:
        for m in reversed(messages):
            if m["role"] == "user" and isinstance(m["content"], list):
                for b in m["content"]:
                    if b.get("type") == "tool_result":
                        for c in b["content"]:
                            if c.get("type") == "text":
                                try:
                                    return json.loads(c["text"])
                                except json.JSONDecodeError:
                                    return {}
        return {}

    def decide(self, messages: list[dict]) -> _Response:
        r = self._last_result(messages)
        if self.state == "done":
            return _Response([_Block("text", text="Mission ended.")], "end_turn")
        if r.get("decision") == "confirmed":
            self.state = "following"
            return self._call("follow_confirmed", {"candidate_id": r["candidate_id"], "seconds": self.follow_s},
                              f"{r['candidate_id']} confirmed by the coordinator; following")
        if self.state == "following" and "pins" in r:
            self.state = "done"
            return self._call("end_mission", {"outcome": "found_and_followed",
                                              "summary": f"followed for {r['followed_s']} s, {r['pins']} pins"},
                              "follow complete")
        cands = r.get("candidates") or []
        for c in cands:
            if c["status"] != "unreviewed" or c["id"] in self.asked:
                continue
            if c["match"] >= ASK_AT and (c["box_height_px"] >= 40 or self.looked.get(c["id"], 0) >= 1):
                self.asked.add(c["id"])
                return self._call("ask_coordinator", {"candidate_id": c["id"],
                                                      "message": f"match {c['match']:.2f} at {c['distance_m']} m"},
                                  f"{c['id']} strong")
            if c["match"] >= LOOK_AT and self.looked.get(c["id"], 0) < 2 and c["distance_m"] is not None:
                self.looked[c["id"]] = self.looked.get(c["id"], 0) + 1
                return self._call("look_closer", {"candidate_id": c["id"], "reason": "ambiguous or small"},
                                  f"{c['id']} ambiguous")
        if self.state == "scan":
            self.state = "sweep"
            return self._call("scan_here", {"degrees": 360}, "first look around from takeoff")
        if self.waypoints:
            e, n = self.waypoints.pop(0)
            return self._call("fly_to", {"east_m": e, "north_m": n}, "next unsearched part of the area")
        self.state = "done"
        return self._call("end_mission", {"outcome": "not_found", "summary": "area covered, no match"},
                          "area covered")
