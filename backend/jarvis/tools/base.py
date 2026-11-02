"""Tool registry: one decorator gives the LLM schema, the risk level and the callable."""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class Result:
    say: str                                  # what Jarvis speaks (short)
    ok: bool = True
    card: dict[str, Any] | None = None        # optional UI card
    pending: dict[str, Any] | None = None     # waits for a follow-up answer (confirm / choose)
    icon: str | None = None                   # youtube | spotify | mail | folder | app | web | system


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Awaitable[Result]]
    params: dict[str, Any]
    required: list[str] = field(default_factory=list)
    risk: str = "safe"                        # safe | confirm (confirm tools are never exposed to the LLM)

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


REGISTRY: dict[str, Tool] = {}


def tool(description: str, risk: str = "safe", **params: str):
    """params: name="description" (all strings). Parameters without defaults are required."""
    def wrap(func):
        sig = inspect.signature(func)
        props = {k: {"type": "string", "description": v} for k, v in params.items()}
        required = [k for k, p in sig.parameters.items() if p.default is inspect.Parameter.empty and k in props]
        REGISTRY[func.__name__] = Tool(func.__name__, description, func, props, required, risk)
        return func
    return wrap


# Reached only through the intent engine: disruptive if the LLM guesses wrong, or already
# covered by fixed phrases. Fewer tools also means a shorter prompt for the small model.
_NOT_FOR_LLM = {"close_app", "lock_screen", "tell_time", "media_control"}


_EMAIL_TOOLS = {"draft_email", "check_email", "read_email", "emails_to_reply"}


def llm_tools() -> list[dict[str, Any]]:
    from . import CTX

    skip = _NOT_FOR_LLM | (_EMAIL_TOOLS if not CTX.cfg.get("email", {}).get("enabled", True) else set())
    if not CTX.cfg.get("claude_code", {}).get("allow_tasks", False):
        skip = skip | {"claude_code_task"}
    return [t.schema() for t in REGISTRY.values() if t.risk == "safe" and t.name not in skip]
