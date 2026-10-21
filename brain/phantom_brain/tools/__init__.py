"""The six things the search agent can do, and the one thing it cannot.

Each definition below is what the model reads when choosing, so each carries
its inputs, an example, its edge cases, and where it stops and the next tool
starts. The executor turns a call into flight, perception and, for one tool, a
question to a person.

The thing it cannot do: confirm an identity. There is no tool for it. Only the
coordinator, answering `ask_coordinator`, can set a candidate to "confirmed",
and `follow_confirmed` refuses anything else in code (runtime.NotConfirmed),
not in the prompt.
"""

from __future__ import annotations

from .definitions import TOOLS
from .executor import ToolExecutor, ToolOutcome

__all__ = ["TOOLS", "ToolExecutor", "ToolOutcome"]
