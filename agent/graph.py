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
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal, Optional, TypedDict

import anthropic
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agent.integrations import calendar_client, terra_client
from agent.services import token_service
from agent.tools.tool_schemas import GET_CALENDAR_EVENTS, GET_HEALTH_DATA

_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text().split("```text")[1].split("```")[0]

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
    approval_token: Optional[str]   # minted by the app on Approve, burned in apply
    revision_count: int             # rejected proposals get at most one revision
    applied: bool
    audit_log: list[dict]           # append-only record of everything the agent did


# --------------------------------------------------------------------------
# Tool executors (application-side; the model never talks to Terra/Google
# directly).
# --------------------------------------------------------------------------

def execute_tool(name: str, tool_input: dict, state: AgentState) -> dict:
    if name == "get_health_data":
        return terra_client.get_health_data(state["user_id"], **tool_input)
    if name == "get_calendar_events":
        return calendar_client.get_calendar_events(state["user_id"], **tool_input)
    if name == "reschedule_workout":
        # Defense in depth: the model should never reach this outside the
        # apply node, and even there the token is validated server-side.
        raise PermissionError("reschedule_workout can only run in the apply node")
    raise ValueError(f"unknown tool {name}")


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
    messages = [{"role": "user", "content": json.dumps(context)}]
    while True:
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
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(execute_tool(block.name, block.input, state)),
            }
            for block in response.content
            if block.type == "tool_use"
        ]
        messages.append({"role": "user", "content": tool_results})
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
    the app calls graph.invoke(Command(resume=<decision>), config). On Approve,
    the app mints an approval token bound to this exact proposal
    (token_service.mint_token) and passes it in the resume payload."""
    decision = interrupt({
        "type": "approval_request",
        "proposal": state["proposal"],
        "rationale": state["proposal"]["rationale"],
    })
    approved = bool(decision.get("approved"))
    return {
        "approval": "approved" if approved else "rejected",
        "approval_token": decision.get("approval_token") if approved else None,
        "revision_count": state.get("revision_count", 0) + (0 if approved else 1),
    }


def apply(state: AgentState) -> AgentState:
    """Only reachable after an explicit approval. The token minted at Approve
    time is re-validated against this proposal's content hash and burned
    (single-use) before anything touches the calendar."""
    token_service.validate_and_consume(
        state.get("approval_token") or "", state["user_id"], state["proposal"]
    )
    result = calendar_client.apply_calendar_change(state["user_id"], state["proposal"])
    audit = state.get("audit_log", []) + [{"event": "applied", "data": result}]
    return {"applied": True, "approval_token": None, "audit_log": audit}


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
    # thread at the approval gate indefinitely. (from_conn_string() is a
    # context manager in current langgraph; construct from a connection so the
    # saver lives as long as the graph.)
    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))


# Usage from the application:
#
#   graph = build_graph()
#   config = {"configurable": {"thread_id": user_id}}
#
#   # Morning cron / webhook kicks off a cycle:
#   result = graph.invoke({"user_id": user_id}, config)  # runs until interrupt or END
#
#   # Later, when the user taps Approve in the app:
#   from langgraph.types import Command
#   from agent.services import token_service
#   pending = graph.get_state(config).values["proposal"]
#   token = token_service.mint_token(user_id, pending)
#   graph.invoke(Command(resume={"approved": True, "approval_token": token}), config)
#
#   # Or Reject:
#   graph.invoke(Command(resume={"approved": False}), config)


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
