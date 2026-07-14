"""Google Calendar client — implements `get_calendar_events` and the
write path behind `reschedule_workout`.

Auth: OAuth user credentials with the narrow `calendar.events` scope only
(docs/SECURITY.md §3). Expects a per-user authorized-user token file (the JSON
written by google-auth after the consent flow) under GOOGLE_TOKEN_DIR, named
`<user_id>.json`; expired access tokens are refreshed with the stored refresh
token and re-persisted.

Security invariants enforced here, independent of anything the model says:
- Only events carrying our extendedProperties tag (managed_by=AGENT_TAG) can be
  modified. Academic/personal events are structurally read-only.
- Outgoing event text is screened so health metrics never land on the calendar.

Google SDK imports are lazy (inside _service) so this module is importable and
testable without google-api-python-client installed.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

AGENT_TAG = "adaptive-recovery-agent"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CALENDAR_ID = "primary"

ACADEMIC_KEYWORDS = re.compile(
    r"\b(exam|midterm|final|quiz|deadline|due|assignment|lecture|class|seminar|"
    r"lab|thesis|defen[cs]e|presentation|study)\b",
    re.IGNORECASE,
)

# Health data must never be written to the calendar (docs/SECURITY.md §2):
# numeric values with physiological units, or metric names alongside numbers.
_HEALTH_TEXT = re.compile(
    r"(\b\d+(\.\d+)?\s*(ms|bpm|hrs?|hours)\b)|(\b(hrv|heart\s*rate|resting\s*hr|"
    r"sleep\s*(score|debt)|recovery\s*(score|%))\b)",
    re.IGNORECASE,
)


class CalendarError(RuntimeError):
    """Raised on API failures or invariant violations."""


class NotAgentManagedError(CalendarError):
    """Target event is not managed by this agent — refusing to touch it."""


# --------------------------------------------------------------------------
# Auth / service
# --------------------------------------------------------------------------

def _token_path(user_id: str) -> Path:
    token_dir = Path(os.environ.get("GOOGLE_TOKEN_DIR", "secrets/google_tokens"))
    # user_id comes from the authenticated session, but never trust it as a path
    if not re.fullmatch(r"[A-Za-z0-9_-]+", user_id):
        raise CalendarError(f"invalid user_id for token lookup: {user_id!r}")
    return token_dir / f"{user_id}.json"


def _service(user_id: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = _token_path(user_id)
    if not path.exists():
        raise CalendarError(f"no Google credentials on file for user {user_id}")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    if not creds.valid:
        raise CalendarError(f"Google credentials for user {user_id} are invalid; re-auth needed")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# --------------------------------------------------------------------------
# Read: get_calendar_events
# --------------------------------------------------------------------------

def get_calendar_events(
    user_id: str, start_date: str, end_date: str, include: list[str]
) -> dict:
    """Fetch and classify events in [start_date, end_date] (inclusive).

    Returns {"workouts": [...], "academic": [...], "other": [...]} filtered to
    the requested categories, matching the tool-result example in
    tool_schemas.py.
    """
    service = _service(user_id)
    items: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=f"{start_date}T00:00:00Z",
                timeMax=f"{end_date}T23:59:59Z",
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
                maxResults=250,
            )
            .execute()
        )
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    buckets: dict[str, list[dict]] = {"workouts": [], "academic": [], "other": []}
    for event in items:
        if event.get("status") == "cancelled":
            continue
        buckets[_classify(event)].append(_summarize(event))
    return {category: buckets[category] for category in include}


def _classify(event: dict) -> str:
    if _is_agent_managed(event):
        return "workouts"
    text = f"{event.get('summary', '')} {event.get('description', '')}"
    return "academic" if ACADEMIC_KEYWORDS.search(text) else "other"


def _is_agent_managed(event: dict) -> bool:
    private = event.get("extendedProperties", {}).get("private", {})
    return private.get("managed_by") == AGENT_TAG


def _summarize(event: dict) -> dict:
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    end = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
    summary = {
        "event_id": event["id"],
        "title": event.get("summary", "(untitled)"),
        "start": start,
        "duration_min": _duration_min(start, end),
    }
    if _is_agent_managed(event):
        private = event["extendedProperties"]["private"]
        summary["intensity"] = private.get("intensity", "moderate")
        summary["managed_by_agent"] = True
    return summary


def _duration_min(start: Optional[str], end: Optional[str]) -> Optional[int]:
    if not start or not end or "T" not in start or "T" not in end:
        return None  # all-day events have date-only bounds
    delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    return int(delta.total_seconds() // 60)


# --------------------------------------------------------------------------
# Write: apply an approved proposal
# --------------------------------------------------------------------------

def apply_calendar_change(user_id: str, proposal: dict) -> dict:
    """Execute an approved proposal. Token validation happens in the caller
    (agent/graph.py apply node) BEFORE this runs; this function still enforces
    the ownership and data-hygiene invariants itself."""
    service = _service(user_id)
    event_id = proposal["event_id"]
    action = proposal["action"]

    event = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
    if not _is_agent_managed(event):
        raise NotAgentManagedError(
            f"event {event_id} is not managed by {AGENT_TAG}; refusing to modify"
        )

    if action == "cancel":
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return {"event_id": event_id, "action": "cancel", "status": "applied"}

    if action not in ("replace", "move"):
        raise CalendarError(f"unknown action {action!r}")

    replacement = proposal.get("replacement")
    if not replacement:
        raise CalendarError(f"action {action!r} requires a replacement block")
    _assert_no_health_data(replacement["title"])

    start = datetime.fromisoformat(replacement["start"])
    end = start + timedelta(minutes=replacement["duration_min"])
    body = {
        "summary": replacement["title"],
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "extendedProperties": {
            "private": {"managed_by": AGENT_TAG, "intensity": replacement["intensity"]}
        },
    }
    updated = (
        service.events()
        .patch(calendarId=CALENDAR_ID, eventId=event_id, body=body)
        .execute()
    )
    return {
        "event_id": updated["id"],
        "action": action,
        "status": "applied",
        "new": {
            "title": replacement["title"],
            "start": start.isoformat(),
            "duration_min": replacement["duration_min"],
            "intensity": replacement["intensity"],
        },
    }


def create_workout_event(user_id: str, title: str, start_iso: str,
                         duration_min: int, intensity: str) -> dict:
    """Create a new agent-managed workout (used when seeding a plan)."""
    _assert_no_health_data(title)
    service = _service(user_id)
    start = datetime.fromisoformat(start_iso)
    end = start + timedelta(minutes=duration_min)
    created = (
        service.events()
        .insert(
            calendarId=CALENDAR_ID,
            body={
                "summary": title,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
                "extendedProperties": {
                    "private": {"managed_by": AGENT_TAG, "intensity": intensity}
                },
            },
        )
        .execute()
    )
    return {"event_id": created["id"], "status": "created"}


def _assert_no_health_data(text: str) -> None:
    if _HEALTH_TEXT.search(text):
        raise CalendarError(
            f"refusing to write health data to the calendar: {text!r}"
        )
