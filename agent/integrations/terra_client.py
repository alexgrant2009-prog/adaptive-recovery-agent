"""Terra API client — implements the `get_health_data` tool.

Talks to Terra's REST API (https://docs.tryterra.co) directly with `requests`.
Auth is two headers: `x-api-key` (TERRA_API_KEY) and `dev-id` (TERRA_DEV_ID).

The return shape matches the example documented next to GET_HEALTH_DATA in
agent/tools/tool_schemas.py:

    {
      "as_of": "<ISO 8601 UTC>",
      "metrics": {
        "<metric>": {"unit": ..., "baseline_7d": ..., "daily": [...]},
      },
      "stale": bool,   # newest reading older than STALE_AFTER_HOURS
    }

Readings outside physiological sanity bands (see docs/SECURITY.md §5) are kept
but marked "suspect": true and excluded from the baseline, so a spoofed or
glitched reading can't quietly shift the agent's judgement.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

BASE_URL = "https://api.tryterra.co/v2"
REQUEST_TIMEOUT = 15  # seconds
STALE_AFTER_HOURS = 48

# Physiological sanity bands: (min, max) inclusive. Values outside are suspect.
SANITY_BANDS = {
    "hrv": (10.0, 200.0),        # ms, rMSSD
    "sleep": (0.0, 16.0),        # hours
    "resting_hr": (30.0, 120.0), # bpm
}

UNITS = {"hrv": "ms", "sleep": "hours", "resting_hr": "bpm"}


class TerraError(RuntimeError):
    """Raised when Terra returns an error or an unusable payload."""


def _headers() -> dict[str, str]:
    try:
        return {
            "x-api-key": os.environ["TERRA_API_KEY"],
            "dev-id": os.environ["TERRA_DEV_ID"],
            "accept": "application/json",
        }
    except KeyError as e:
        raise TerraError(f"missing required environment variable {e.args[0]}") from e


def _get(endpoint: str, user_id: str, start: date, end: date) -> list[dict]:
    resp = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers=_headers(),
        params={
            "user_id": user_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "to_webhook": "false",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise TerraError(f"Terra {endpoint} returned {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if body.get("status") not in (None, "success"):
        raise TerraError(f"Terra {endpoint} status={body.get('status')}: {body.get('message')}")
    return body.get("data", [])


def get_health_data(user_id: str, metrics: list[str], days_back: int) -> dict:
    """Fetch daily health metrics for the last `days_back` days (ending today)."""
    unknown = set(metrics) - set(UNITS)
    if unknown:
        raise ValueError(f"unknown metrics: {sorted(unknown)}")

    today = date.today()
    start = today - timedelta(days=days_back - 1)
    out_metrics: dict[str, dict] = {}

    # hrv and resting_hr both live in Terra's daily payloads — one fetch covers both.
    if {"hrv", "resting_hr"} & set(metrics):
        daily_payloads = _get("daily", user_id, start, today)
        if "hrv" in metrics:
            out_metrics["hrv"] = _series(
                daily_payloads, "hrv",
                lambda p: _dig(p, "heart_rate_data", "summary", "avg_hrv_rmssd"),
            )
        if "resting_hr" in metrics:
            out_metrics["resting_hr"] = _series(
                daily_payloads, "resting_hr",
                lambda p: _dig(p, "heart_rate_data", "summary", "resting_hr_bpm"),
            )

    if "sleep" in metrics:
        sleep_payloads = _get("sleep", user_id, start, today)
        out_metrics["sleep"] = _series(
            sleep_payloads, "sleep", _sleep_hours, extra=_sleep_extra,
        )

    newest = max(
        (d["date"] for m in out_metrics.values() for d in m["daily"] if d["value"] is not None),
        default=None,
    )
    stale = (
        newest is None
        or (today - date.fromisoformat(newest)) > timedelta(hours=STALE_AFTER_HOURS)
    )
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": out_metrics,
        "stale": stale,
    }


# --------------------------------------------------------------------------
# Payload mapping
# --------------------------------------------------------------------------

def _series(payloads: list[dict], metric: str, extractor, extra=None) -> dict:
    """Build one metric's {unit, baseline_7d, daily} block from Terra payloads."""
    by_date: dict[str, dict] = {}
    lo, hi = SANITY_BANDS[metric]
    for p in payloads:
        day = _payload_date(p)
        raw = extractor(p)
        if day is None or raw is None:
            continue
        value = round(float(raw), 2)
        entry: dict[str, Any] = {"date": day, "value": value}
        if not (lo <= value <= hi):
            entry["suspect"] = True  # kept for transparency, excluded from baseline
        if extra:
            entry.update(extra(p))
        by_date[day] = entry  # last payload for a date wins

    daily = [by_date[d] for d in sorted(by_date)]
    trusted = [e["value"] for e in daily[-7:] if not e.get("suspect")]
    baseline = round(sum(trusted) / len(trusted), 2) if trusted else None
    return {"unit": UNITS[metric], "baseline_7d": baseline, "daily": daily}


def _payload_date(payload: dict) -> Optional[str]:
    start_time = _dig(payload, "metadata", "start_time")
    if not start_time:
        return None
    try:
        return datetime.fromisoformat(start_time.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _sleep_hours(payload: dict) -> Optional[float]:
    seconds = _dig(payload, "sleep_durations_data", "asleep", "duration_asleep_state_seconds")
    return seconds / 3600 if seconds is not None else None


def _sleep_extra(payload: dict) -> dict:
    efficiency = _dig(payload, "sleep_durations_data", "sleep_efficiency")
    return {"efficiency_pct": round(float(efficiency), 1)} if efficiency is not None else {}


def _dig(obj: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj
