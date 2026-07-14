"""Tests for agent.integrations.calendar_client with a mocked Google service.

The google-api-python-client fluent interface (service.events().list(...)
.execute()) is emulated by a small fake so the tests exercise our
classification, ownership, and data-hygiene logic without the SDK installed.
"""

from unittest.mock import patch

import pytest

from agent.integrations import calendar_client
from agent.integrations.calendar_client import (
    AGENT_TAG,
    CalendarError,
    NotAgentManagedError,
)


def _managed_event(event_id="evt_w1", title="Heavy squat session",
                   start="2026-07-15T17:00:00-04:00", end="2026-07-15T18:00:00-04:00",
                   intensity="high"):
    return {
        "id": event_id,
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "extendedProperties": {"private": {"managed_by": AGENT_TAG, "intensity": intensity}},
    }


def _plain_event(event_id, title, start="2026-07-16T09:00:00-04:00",
                 end="2026-07-16T11:00:00-04:00", description=""):
    return {
        "id": event_id,
        "summary": title,
        "description": description,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }


class FakeCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeEvents:
    """Emulates service.events(); records mutations for assertions."""

    def __init__(self, items):
        self.items = {e["id"]: e for e in items}
        self.patched: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.inserted: list[dict] = []

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return FakeCall({"items": list(self.items.values())})

    def get(self, calendarId, eventId):
        if eventId not in self.items:
            return FakeCall(KeyError(eventId))
        return FakeCall(self.items[eventId])

    def patch(self, calendarId, eventId, body):
        self.patched.append((eventId, body))
        return FakeCall({**self.items[eventId], **body})

    def delete(self, calendarId, eventId):
        self.deleted.append(eventId)
        return FakeCall({})

    def insert(self, calendarId, body):
        self.inserted.append(body)
        return FakeCall({"id": "evt_new", **body})


class FakeService:
    def __init__(self, items):
        self._events = FakeEvents(items)

    def events(self):
        return self._events


@pytest.fixture
def service_with(monkeypatch):
    """Returns a factory: pass events, get back the FakeEvents recorder."""
    def _install(items):
        fake = FakeService(items)
        monkeypatch.setattr(calendar_client, "_service", lambda user_id: fake)
        return fake._events
    return _install


# --------------------------------------------------------------------------
# get_calendar_events
# --------------------------------------------------------------------------

def test_events_classified_into_buckets(service_with):
    service_with([
        _managed_event(),
        _plain_event("evt_a1", "CHEM 301 final exam"),
        _plain_event("evt_a2", "Project report", description="Deadline for ML project"),
        _plain_event("evt_o1", "Dinner with Sam"),
    ])
    result = calendar_client.get_calendar_events(
        "user-1", "2026-07-14", "2026-07-17", ["workouts", "academic", "other"]
    )
    assert [e["event_id"] for e in result["workouts"]] == ["evt_w1"]
    assert result["workouts"][0]["managed_by_agent"] is True
    assert result["workouts"][0]["intensity"] == "high"
    assert result["workouts"][0]["duration_min"] == 60
    assert {e["event_id"] for e in result["academic"]} == {"evt_a1", "evt_a2"}
    assert [e["event_id"] for e in result["other"]] == ["evt_o1"]


def test_include_filters_response_categories(service_with):
    service_with([_managed_event(), _plain_event("evt_o1", "Dinner")])
    result = calendar_client.get_calendar_events(
        "user-1", "2026-07-14", "2026-07-17", ["workouts"]
    )
    assert list(result.keys()) == ["workouts"]


def test_cancelled_events_skipped(service_with):
    cancelled = _plain_event("evt_x", "MATH 240 exam")
    cancelled["status"] = "cancelled"
    service_with([cancelled])
    result = calendar_client.get_calendar_events(
        "user-1", "2026-07-14", "2026-07-17", ["academic"]
    )
    assert result["academic"] == []


# --------------------------------------------------------------------------
# apply_calendar_change
# --------------------------------------------------------------------------

def _proposal(action="replace", event_id="evt_w1", title="Mobility — 15 min"):
    replacement = None
    if action in ("replace", "move"):
        replacement = {
            "title": title,
            "start": "2026-07-15T17:00:00-04:00",
            "duration_min": 15,
            "intensity": "recovery",
        }
    return {"event_id": event_id, "action": action, "replacement": replacement,
            "rationale": "low HRV + exam tomorrow"}


def test_replace_patches_event_and_keeps_agent_tag(service_with):
    events = service_with([_managed_event()])
    result = calendar_client.apply_calendar_change("user-1", _proposal("replace"))

    assert result["status"] == "applied"
    (event_id, body), = events.patched
    assert event_id == "evt_w1"
    assert body["summary"] == "Mobility — 15 min"
    assert body["extendedProperties"]["private"]["managed_by"] == AGENT_TAG
    assert body["extendedProperties"]["private"]["intensity"] == "recovery"
    # end = start + 15 min
    assert body["end"]["dateTime"] == "2026-07-15T17:15:00-04:00"


def test_cancel_deletes_event(service_with):
    events = service_with([_managed_event()])
    result = calendar_client.apply_calendar_change("user-1", _proposal("cancel"))
    assert result == {"event_id": "evt_w1", "action": "cancel", "status": "applied"}
    assert events.deleted == ["evt_w1"]


def test_refuses_to_touch_unmanaged_event(service_with):
    events = service_with([_plain_event("evt_a1", "CHEM 301 final exam")])
    with pytest.raises(NotAgentManagedError):
        calendar_client.apply_calendar_change("user-1", _proposal(event_id="evt_a1"))
    assert events.patched == [] and events.deleted == []


def test_refuses_health_data_in_event_title(service_with):
    events = service_with([_managed_event()])
    for bad_title in ["Rest day (HRV 38ms)", "Easy spin — resting HR elevated", "Sleep score 42"]:
        with pytest.raises(CalendarError, match="health data"):
            calendar_client.apply_calendar_change(
                "user-1", _proposal("replace", title=bad_title)
            )
    assert events.patched == []


def test_replace_without_replacement_block_rejected(service_with):
    service_with([_managed_event()])
    proposal = _proposal("replace")
    proposal["replacement"] = None
    with pytest.raises(CalendarError, match="replacement"):
        calendar_client.apply_calendar_change("user-1", proposal)


def test_invalid_user_id_never_reaches_filesystem(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_DIR", str(tmp_path))
    with pytest.raises(CalendarError, match="invalid user_id"):
        calendar_client._token_path("../../etc/passwd")
