"""End-to-end graph flow tests: perceive -> reason -> propose -> interrupt ->
resume -> apply, with Terra, Google Calendar, and the Anthropic API all mocked.

Requires langgraph + anthropic installed; skipped otherwise so the pure unit
tests still run in minimal environments.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("anthropic")

from langgraph.types import Command

from agent import graph as graph_module
from agent.services import token_service

USER = "user-1"

HEALTH = {
    "as_of": "2026-07-14T06:30:00+00:00",
    "metrics": {
        "hrv": {"unit": "ms", "baseline_7d": 62,
                "daily": [{"date": "2026-07-13", "value": 44}]},
        "sleep": {"unit": "hours", "baseline_7d": 7.4,
                  "daily": [{"date": "2026-07-13", "value": 5.1}]},
        "resting_hr": {"unit": "bpm", "baseline_7d": 54,
                       "daily": [{"date": "2026-07-13", "value": 61}]},
    },
    "stale": False,
}

CALENDAR = {
    "workouts": [{"event_id": "evt_w1", "title": "Heavy squat session",
                  "start": "2026-07-15T17:00:00-04:00", "duration_min": 60,
                  "intensity": "high", "managed_by_agent": True}],
    "academic": [{"event_id": "evt_a1", "title": "CHEM 301 final exam",
                  "start": "2026-07-16T09:00:00-04:00", "duration_min": 120}],
}

PROPOSAL = {
    "event_id": "evt_w1",
    "action": "replace",
    "replacement": {"title": "Mobility — 15 min", "start": "2026-07-15T17:00:00-04:00",
                    "duration_min": 15, "intensity": "recovery"},
    "rationale": "HRV 29% below baseline with an exam in <48h; downgrade to mobility.",
}

LLM_RESULT = {
    "assessment": {"recovery": "low", "academic_pressure": "high",
                   "verdict": "change", "summary": "Under-recovered before an exam."},
    "proposal": PROPOSAL,
}


def _fake_llm_response(payload):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(payload))],
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Graph with fresh checkpoint DB and all externals mocked; yields the
    compiled graph, thread config, and the recorded calendar writes."""
    monkeypatch.setenv("APPROVAL_TOKEN_SECRET", "flow-test-secret")
    monkeypatch.setattr(token_service, "DEFAULT_DB_PATH", str(tmp_path / "tokens.sqlite"))
    writes = []
    with (
        patch.object(graph_module.terra_client, "get_health_data", return_value=HEALTH),
        patch.object(graph_module.calendar_client, "get_calendar_events", return_value=CALENDAR),
        patch.object(
            graph_module.calendar_client, "apply_calendar_change",
            side_effect=lambda user_id, proposal: writes.append((user_id, proposal))
            or {"event_id": proposal["event_id"], "action": proposal["action"], "status": "applied"},
        ),
        patch.object(graph_module.client.messages, "create",
                     return_value=_fake_llm_response(LLM_RESULT)),
    ):
        g = graph_module.build_graph(str(tmp_path / "ckpt.sqlite"))
        yield g, {"configurable": {"thread_id": USER}}, writes


def test_cycle_pauses_at_approval_gate(wired):
    g, config, writes = wired
    result = g.invoke({"user_id": USER}, config)

    assert result["assessment"]["verdict"] == "change"
    assert result["proposal"] == PROPOSAL
    interrupts = result["__interrupt__"]
    assert interrupts[0].value["type"] == "approval_request"
    assert writes == []  # nothing written before approval


def test_approve_resumes_and_applies_exactly_once(wired):
    g, config, writes = wired
    g.invoke({"user_id": USER}, config)

    pending = g.get_state(config).values["proposal"]
    token = token_service.mint_token(USER, pending)
    result = g.invoke(Command(resume={"approved": True, "approval_token": token}), config)

    assert result["applied"] is True
    assert writes == [(USER, PROPOSAL)]
    assert any(entry["event"] == "applied" for entry in result["audit_log"])


def test_reject_never_writes(wired):
    g, config, writes = wired
    g.invoke({"user_id": USER}, config)
    result = g.invoke(Command(resume={"approved": False}), config)

    assert writes == []
    assert result.get("applied") is not True
    assert result["revision_count"] >= 1


def test_approval_without_valid_token_blocks_the_write(wired):
    g, config, writes = wired
    g.invoke({"user_id": USER}, config)

    with pytest.raises(token_service.ApprovalTokenError):
        g.invoke(Command(resume={"approved": True, "approval_token": "forged.token"}), config)
    assert writes == []
