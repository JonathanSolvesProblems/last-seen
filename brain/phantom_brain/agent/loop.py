"""The search agent's loop. Control flow is stop_reason and nothing else.

  tool_use     run the tool(s) the model asked for, append the results, call again
  end_turn     the model has stopped; if it never ended the mission, it is told once
               that only end_mission lands the aircraft, then the harness ends it
  max_tokens   the reply was cut off: NOT done; ask it to continue
  refusal      stop deciding; the harness ends the mission safely

The aircraft hovers and keeps looking while the model thinks (runtime.hover_while),
so a slow decision is never an uncommanded drift and never trips the watchdog.

Tools run one at a time (disable_parallel_tool_use): an aircraft cannot fly to
two places at once, and the order of physical actions is part of the decision.

Every turn is written to trace.jsonl: what the model saw (candidate scores and
which crops it was shown), what it said, which tool it called with what, and
what came back. That file is the evidence that a visual result changed a later
action, which is the Agentic Vision Award's qualifying test.
"""

from __future__ import annotations

import asyncio
import inspect
import base64
import json
import time
from pathlib import Path
from typing import Any

from ..pilot import Abort
from ..tools import TOOLS, ToolExecutor
from .prompts import SYSTEM, briefing

DEFAULT_MODEL = "claude-opus-5-5"
BEDROCK_MODEL = "anthropic.claude-opus-5-5"
MAX_TURNS = 40          # a safety net against a runaway loop, never the planned stop


def make_client(provider: str = "anthropic", region: str = "us-east-1"):
    """(client, model, extra request params). Bedrock through the Mantle client,
    once the account is allowed to invoke models.

    CALL THIS BEFORE TAKEOFF. Importing the SDK and building its HTTP client
    takes seconds and blocks the event loop while it does; the second real
    rehearsal aborted with "link stale for 3.9 s" because the agent was built
    with the aircraft already in the air."""
    import anthropic  # noqa: PLC0415
    # Synchronous clients, called on a worker thread (see SearchAgent._create):
    # anything blocking inside the HTTP stack, such as the first connection's
    # certificate load, must never stall the event loop that streams velocity.
    if provider == "bedrock":
        return anthropic.AnthropicBedrockMantle(aws_region=region), BEDROCK_MODEL, {}
    # Server-side fallback on a policy decline, routed by category.
    return (anthropic.Anthropic(), DEFAULT_MODEL,
            {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"})


def _image(path: str, max_side: int = 384) -> dict:
    import cv2  # noqa: PLC0415
    img = cv2.imread(path)
    s = max_side / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.tobytes()).decode()}}


def _dump(block: Any) -> dict:
    return block.model_dump(exclude_none=True) if hasattr(block, "model_dump") else dict(block)


class SearchAgent:
    def __init__(self, executor: ToolExecutor, trace_path: Path, *, provider: str = "anthropic",
                 effort: str = "medium", client=None, model: str | None = None,
                 extra: dict | None = None):
        self.ex = executor
        self.rt = executor.rt
        if client is None:
            client, default_model, self.extra = make_client(provider)
        else:
            default_model, self.extra = DEFAULT_MODEL, dict(extra or {})
        self.client = client
        self.model = model or default_model
        self.effort = effort
        self.trace = open(trace_path, "a", encoding="utf-8")
        self.turns = 0

    def _log(self, kind: str, **data: Any) -> None:
        self.trace.write(json.dumps({"t": round(time.time(), 2), "turn": self.turns, "kind": kind,
                                     **data}, default=str) + "\n")
        self.trace.flush()

    async def _create(self, messages: list[dict]):
        """One model call, OFF the event loop.

        The first real rehearsal (2026-10-20) aborted with "link stale for 3.2 s":
        building the first HTTPS connection blocked the loop, so telemetry stopped
        being read and velocity stopped being sent. On a worker thread the flight
        loop keeps running whatever the network stack does."""
        kw = dict(model=self.model, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                  tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                  thinking={"type": "adaptive", "display": "summarized"},
                  output_config={"effort": self.effort},
                  # The history is re-sent every turn and most of it is unchanged:
                  # cache it. About 100k input tokens a mission, mostly repeats.
                  cache_control={"type": "ephemeral"}, **self.extra)
        if self.model == "scripted":
            kw.pop("cache_control")
        create = self.client.beta.messages.create
        if inspect.iscoroutinefunction(create):
            return await create(**kw)
        return await asyncio.to_thread(create, **kw)

    async def run(self, area, alt: float, reference_path: str, notes: str = "") -> dict:
        tel = self.rt.bc.telemetry
        messages: list[dict] = [{"role": "user", "content": [
            {"type": "text", "text": briefing(area, alt, tel.battery, notes)},
            _image(reference_path)]}]
        self._log("briefing", area=area, alt=alt, reference=reference_path)
        nudged = False
        while True:
            self.turns += 1
            if self.turns > MAX_TURNS:
                self._log("safety_net", reason=f"{MAX_TURNS} turns")
                await self.ex.execute("end_mission", {"outcome": "aborted",
                                                      "summary": "turn limit reached; returned home"})
                break
            if self.rt.recalled and self.ex.outcome is None:
                self._log("recall", by="coordinator")
                await self.ex.execute("end_mission", {"outcome": "aborted",
                                                      "summary": "recalled by the coordinator"})
                break
            snapshot = self.rt.snapshot()
            try:
                resp = await self.rt.hover_while(self._create(messages))
            except Abort:
                raise                  # a safety abort is the flight session's to handle, not an API error
            except Exception as e:  # noqa: BLE001
                # The SDK has already retried what is retryable (429, 5xx, network).
                # Whatever reaches here will not fix itself mid-flight: bring the
                # aircraft home with the reason on record, rather than crash.
                self._log("api_error", error=f"{type(e).__name__}: {str(e)[:300]}")
                if self.ex.outcome is None:
                    await self.ex.execute("end_mission", {"outcome": "aborted",
                                                          "summary": f"model unavailable ({type(e).__name__}); returned home"})
                break
            text = [b.text for b in resp.content if b.type == "text"]
            thinking = [getattr(b, "thinking", "") for b in resp.content if b.type == "thinking"]
            calls = [b for b in resp.content if b.type == "tool_use"]
            self._log("model", stop_reason=resp.stop_reason, text=text,
                      thinking=[t for t in thinking if t], calls=[{"name": c.name, "input": c.input} for c in calls],
                      candidates_seen=snapshot,
                      usage={"in": resp.usage.input_tokens, "out": resp.usage.output_tokens})
            messages.append({"role": "assistant", "content": [_dump(b) for b in resp.content]})

            if resp.stop_reason == "tool_use":
                results, ended = [], False
                for call in calls:
                    out = await self.ex.execute(call.name, dict(call.input))
                    self._log("tool", name=call.name, input=call.input, is_error=out.is_error,
                              record=out.record, crops_shown=sum(1 for b in out.content if b["type"] == "image"))
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": out.content, "is_error": out.is_error})
                    ended = ended or out.ended
                messages.append({"role": "user", "content": results})
                if ended:
                    break
                continue
            if resp.stop_reason == "max_tokens":
                messages.append({"role": "user", "content": "Your reply was cut off. Continue."})
                continue
            if resp.stop_reason == "refusal":
                self._log("refusal", details=getattr(resp, "stop_details", None))
                await self.ex.execute("end_mission", {"outcome": "aborted",
                                                      "summary": "the model declined; returned home"})
                break
            # end_turn (or anything else): the model stopped without landing.
            if not nudged and self.ex.outcome is None:
                nudged = True
                messages.append({"role": "user", "content":
                                 "The aircraft is still in the air. Only end_mission brings it home."})
                continue
            if self.ex.outcome is None:
                await self.ex.execute("end_mission", {"outcome": "aborted",
                                                      "summary": "agent stopped without ending; returned home"})
            break
        self._log("done", outcome=self.ex.outcome)
        self.trace.close()
        return self.ex.outcome or {}
