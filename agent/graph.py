"""Adaptive Recovery Agent — state machine (LangGraph).

The agent is a small, explicit graph rather than a free-running loop, because
two properties matter more than flexibility here:

1. Durability. A cycle can pause for hours/days waiting for the user to tap
   Approve. LangGraph's checkpointer persists the full graph state per
   `thread_id` (one thread per user), so the agent "remembers its plan" across
   process restarts and app sessions.
2. A hard human-in-the-loop gate. The graph *interrupts* before the apply node.
   Nothing writes to the calendar until the app resumes the thread with an
   explicit approval decision, and the write tool additionally demands an
   approval token minted by the app (see tool_schemas.py). The pause is
   structural, not a prompt suggestion.

Graph shape:

    perceive ──> reason ──┬─(plan ok)────────────────────────> END
                          └─(change needed)──> propose ──> [INTERRUPT]
                                                              │ user decision
                          ┌───────────────(approved)──────────┤
                          ▼                                   │(rejected)
                        apply ──> END                         ▼
                                                       reason (one revision,
                                                        then END)
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal, Optional, TypedDict

import anthropic
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agent.tools.tool_schemas import ALL_TOOLS, GET_CALENDAR_EVENTS, GET_HEALTH_DATA

SYSTEM_PROMPT = open("agent/prompts/system_prompt.md").read().split("```text")[1].split("```")[0]

client = anthropic.Anthropic()
MODEL = "claude-opus-4-8"


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

class Proposal(TypedDict):
    event_id: str
    action: Literal["replace", "move", "cancel"]
    replacement: Optional[dict]
    rationale: str


class AgentState(TypedDict, total=False):
    user_id: str
    cycle_date: str                 # the day this cycle is planning for
    health_data: dict               # raw tool result from get_health_data
    calendar: dict                  # raw tool result from get_calendar_events
    assessment: dict                # {"recovery": "low|moderate|good", "verdict": "keep|change", ...}
    proposal: Optional[Proposal]    # the ONE pending proposal, if any
    approval: Optional[Literal["approved", "rejected"]]
    revision_count: int             # rejected proposals get at most one revision
    applied: bool
    audit_log: list[dict]           # append-only record of everything the agent did


# --------------------------------------------------------------------------
# Tool executors (application-side; the model never talks to Terra/Google
# directly). Real implementations live behind these two functions.
# --------------------------------------------------------------------------

def execute_tool(name: str, tool_input: dict, state: AgentState) -> dict:
    if name == "get_health_data":
        return fetch_terra_metrics(state["user_id"], **tool_input)      # TODO: Terra API client
    if name == "get_calendar_events":
        return fetch_calendar_events(state["user_id"], **tool_input)    # TODO: Google/Outlook client
    if name == "reschedule_workout":
        # Defense in depth: the model should never reach this outside the
        # apply node, and even there the token is validated server-side.
        raise PermissionError("reschedule_workout can only run in the apply node")
    raise ValueError(f"unknown tool {name}")


def fetch_terra_metrics(user_id: str, metrics: list[str], days_back: int) -> dict:
    raise NotImplementedError


def fetch_calendar_events(user_id: str, start_date: str, end_date: str, include: list[str]) -> dict:
    raise NotImplementedError


def apply_calendar_change(user_id: str, proposal: Proposal, approval_token: str) -> dict:
    """Validate the token (bound to this proposal's hash, single-use, short
    TTL), verify the target event is agent-managed, then write via the
    calendar API. Raises on any validation failure."""
    raise NotImplementedError


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------

def perceive(state: AgentState) -> AgentState:
    """Deterministic data gathering — no LLM needed to know we always want
    recent biometrics and the next few days of calendar."""
    today = state.get("cycle_date") or date.today().isoformat()
    health = execute_tool(
        "get_health_data",
        {"metrics": ["hrv", "sleep", "resting_hr"], "days_back": 7},
        state,
    )
    calendar = execute_tool(
        "get_calendar_events",
        {"start_date": today, "end_date": _plus_days(today, 3),
         "include": ["workouts", "academic"]},
        state,
    )
    return {"cycle_date": today, "health_data": health, "calendar": calendar}


def reason(state: AgentState) -> AgentState:
    """LLM reasoning pass. The model sees the fresh data and must return a
    structured assessment; if a change is warranted it also drafts the single
    proposal. Structured output means downstream nodes never parse prose."""
    context = {
        "today": state["cycle_date"],
        "health_data": state["health_data"],
        "calendar": state["calendar"],
        "rejected_proposal": state.get("proposal") if state.get("approval") == "rejected" else None,
    }
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        # Read tools stay available so the model can pull more history if a
        # trend looks ambiguous; the write tool is deliberately absent here.
        tools=[GET_HEALTH_DATA, GET_CALENDAR_EVENTS],
        output_config={"format": {"type": "json_schema", "schema": ASSESSMENT_SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(context)}],
    )
    result = json.loads(next(b.text for b in response.content if b.type == "text"))
    audit = state.get("audit_log", []) + [{"event": "assessment", "data": result}]
    return {
        "assessment": result["assessment"],
        "proposal": result.get("proposal"),
        "approval": None,
        "audit_log": audit,
    }


def propose(state: AgentState) -> AgentState:
    """Surface the proposal and PAUSE. `interrupt()` checkpoints the thread and
    returns control to the application, which renders Approve/Reject buttons.
    The graph resumes — possibly days later, possibly after a restart — when
    the app calls graph.invoke(Command(resume=<decision>), config)."""
    decision = interrupt({
        "type": "approval_request",
        "proposal": state["proposal"],
        "rationale": state["proposal"]["rationale"],
    })
    return {"approval": "approved" if decision.get("approved") else "rejected"}


def apply(state: AgentState) -> AgentState:
    """Only reachable after an explicit approval. The app minted an
    approval_token when the user clicked Approve; apply_calendar_change
    re-validates it against the proposal hash before touching the calendar."""
    result = apply_calendar_change(
        state["user_id"], state["proposal"], approval_token=_pop_token(state)
    )
    audit = state.get("audit_log", []) + [{"event": "applied", "data": result}]
    return {"applied": True, "audit_log": audit}


# --------------------------------------------------------------------------
# Edges
# --------------------------------------------------------------------------

def after_reason(state: AgentState) -> str:
    if state["assessment"]["verdict"] == "keep":
        return END                      # plan already appropriate; nothing to do
    if state.get("revision_count", 0) > 1:
        return END                      # user rejected twice; stop pushing
    return "propose"


def after_approval(state: AgentState) -> str:
    if state["approval"] == "approved":
        return "apply"
    # One respectful revision attempt, then defer to the user.
    return "reason"


def build_graph(checkpoint_path: str = "agent_state.sqlite"):
    g = StateGraph(AgentState)
    g.add_node("perceive", perceive)
    g.add_node("reason", reason)
    g.add_node("propose", propose)
    g.add_node("apply", apply)

    g.set_entry_point("perceive")
    g.add_edge("perceive", "reason")
    g.add_conditional_edges("reason", after_reason)
    g.add_conditional_edges("propose", after_approval)
    g.add_edge("apply", END)

    # The checkpointer is what makes the plan survive session end: every node
    # transition is persisted keyed by thread_id, and interrupt() parks the
    # thread at the approval gate indefinitely.
    return g.compile(checkpointer=SqliteSaver.from_conn_string(checkpoint_path))


# Usage from the application:
#
#   graph = build_graph()
#   config = {"configurable": {"thread_id": user_id}}
#
#   # Morning cron / webhook kicks off a cycle:
#   graph.invoke({"user_id": user_id}, config)          # runs until interrupt or END
#
#   # Later, when the user taps a button in the app:
#   from langgraph.types import Command
#   graph.invoke(Command(resume={"approved": True}), config)


ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {
            "type": "object",
            "properties": {
                "recovery": {"type": "string", "enum": ["low", "moderate", "good"]},
                "academic_pressure": {"type": "string", "enum": ["low", "elevated", "high"]},
                "verdict": {"type": "string", "enum": ["keep", "change"]},
                "summary": {"type": "string"},
            },
            "required": ["recovery", "academic_pressure", "verdict", "summary"],
            "additionalProperties": False,
        },
        "proposal": {
            "type": ["object", "null"],
            "properties": {
                "event_id": {"type": "string"},
                "action": {"type": "string", "enum": ["replace", "move", "cancel"]},
                "replacement": {
                    "type": ["object", "null"],
                    "properties": {
                        "title": {"type": "string"},
                        "start": {"type": "string"},
                        "duration_min": {"type": "integer"},
                        "intensity": {"type": "string",
                                      "enum": ["high", "moderate", "low", "recovery"]},
                    },
                    "required": ["title", "start", "duration_min", "intensity"],
                    "additionalProperties": False,
                },
                "rationale": {"type": "string"},
            },
            "required": ["event_id", "action", "replacement", "rationale"],
            "additionalProperties": False,
        },
    },
    "required": ["assessment", "proposal"],
    "additionalProperties": False,
}


def _plus_days(iso_day: str, n: int) -> str:
    from datetime import timedelta
    return (date.fromisoformat(iso_day) + timedelta(days=n)).isoformat()


def _pop_token(state: AgentState) -> str:
    """The app stores the freshly-minted single-use token alongside the
    checkpoint when the user approves; retrieve and invalidate it here."""
    raise NotImplementedError
