"""Tests for agent.integrations.terra_client with mocked Terra HTTP responses."""

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from agent.integrations import terra_client


def _daily_payload(day: date, hrv=55.0, rhr=52.0):
    return {
        "metadata": {"start_time": f"{day.isoformat()}T00:00:00+00:00"},
        "heart_rate_data": {"summary": {"avg_hrv_rmssd": hrv, "resting_hr_bpm": rhr}},
    }


def _sleep_payload(day: date, hours=7.5, efficiency=90.0):
    return {
        "metadata": {"start_time": f"{day.isoformat()}T23:10:00+00:00"},
        "sleep_durations_data": {
            "asleep": {"duration_asleep_state_seconds": hours * 3600},
            "sleep_efficiency": efficiency,
        },
    }


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def terra_env(monkeypatch):
    monkeypatch.setenv("TERRA_API_KEY", "test-key")
    monkeypatch.setenv("TERRA_DEV_ID", "test-dev")


def test_full_fetch_shape_and_baselines():
    today = date.today()
    daily = [_daily_payload(today - timedelta(days=i), hrv=60 - i, rhr=50 + i) for i in range(7)]
    sleep = [_sleep_payload(today - timedelta(days=i), hours=7.0) for i in range(7)]

    def fake_get(url, headers=None, params=None, timeout=None):
        assert headers["x-api-key"] == "test-key"
        assert headers["dev-id"] == "test-dev"
        payloads = daily if url.endswith("/daily") else sleep
        return FakeResponse({"status": "success", "data": payloads})

    with patch.object(terra_client.requests, "get", side_effect=fake_get):
        result = terra_client.get_health_data("user-1", ["hrv", "sleep", "resting_hr"], 7)

    assert set(result["metrics"]) == {"hrv", "sleep", "resting_hr"}
    assert result["stale"] is False

    hrv = result["metrics"]["hrv"]
    assert hrv["unit"] == "ms"
    assert len(hrv["daily"]) == 7
    assert hrv["daily"] == sorted(hrv["daily"], key=lambda d: d["date"])
    # hrv values are 54..60 -> mean 57
    assert hrv["baseline_7d"] == pytest.approx(57.0)

    sleep_metric = result["metrics"]["sleep"]
    assert sleep_metric["baseline_7d"] == pytest.approx(7.0)
    assert sleep_metric["daily"][0]["efficiency_pct"] == 90.0


def test_suspect_values_flagged_and_excluded_from_baseline():
    today = date.today()
    daily = [
        _daily_payload(today - timedelta(days=2), hrv=60),
        _daily_payload(today - timedelta(days=1), hrv=500),  # outside sanity band
        _daily_payload(today, hrv=50),
    ]
    with patch.object(
        terra_client.requests, "get",
        return_value=FakeResponse({"status": "success", "data": daily}),
    ):
        result = terra_client.get_health_data("user-1", ["hrv"], 3)

    entries = {e["date"]: e for e in result["metrics"]["hrv"]["daily"]}
    assert entries[(today - timedelta(days=1)).isoformat()]["suspect"] is True
    assert "suspect" not in entries[today.isoformat()]
    # baseline averages only the trusted 60 and 50
    assert result["metrics"]["hrv"]["baseline_7d"] == pytest.approx(55.0)


def test_stale_flag_when_data_is_old():
    old_day = date.today() - timedelta(days=5)
    with patch.object(
        terra_client.requests, "get",
        return_value=FakeResponse({"status": "success", "data": [_daily_payload(old_day)]}),
    ):
        result = terra_client.get_health_data("user-1", ["hrv"], 7)
    assert result["stale"] is True


def test_no_data_is_stale_not_crash():
    with patch.object(
        terra_client.requests, "get",
        return_value=FakeResponse({"status": "success", "data": []}),
    ):
        result = terra_client.get_health_data("user-1", ["hrv", "resting_hr"], 7)
    assert result["stale"] is True
    assert result["metrics"]["hrv"]["baseline_7d"] is None
    assert result["metrics"]["hrv"]["daily"] == []


def test_http_error_raises_terra_error():
    with patch.object(
        terra_client.requests, "get",
        return_value=FakeResponse({"message": "forbidden"}, status_code=403),
    ):
        with pytest.raises(terra_client.TerraError, match="403"):
            terra_client.get_health_data("user-1", ["hrv"], 7)


def test_unknown_metric_rejected_before_any_request():
    with patch.object(terra_client.requests, "get") as mock_get:
        with pytest.raises(ValueError, match="unknown metrics"):
            terra_client.get_health_data("user-1", ["blood_glucose"], 7)
    mock_get.assert_not_called()


def test_missing_env_vars_raise(monkeypatch):
    monkeypatch.delenv("TERRA_API_KEY")
    with pytest.raises(terra_client.TerraError, match="TERRA_API_KEY"):
        terra_client.get_health_data("user-1", ["hrv"], 7)
