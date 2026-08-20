"""Tests for `_default_risk_control()` (implementationplan.md §19.4's fix) —
the pure logic that seeds `RISK_CONTROL`'s in-memory state on dashboard API
start. Only this pure function is tested, not the `/api/risk-control` routes
themselves: no dashboard_api.py route has a route-level test anywhere in this
project (see tests/test_dashboard_footprint.py) since `startup()` makes a
real MCP connection attempt, so `TestClient` isn't used for any route here.
"""

import json

from api import dashboard_api
from api.dashboard_api import _default_risk_control


def test_default_risk_control_falls_back_to_config_when_no_file(tmp_path, monkeypatch):
    missing = tmp_path / ".risk_control.json"
    monkeypatch.setattr(dashboard_api, "RISK_CONTROL_PATH", missing)

    control = _default_risk_control()

    # config.yaml's own current values — just assert the shape, since the
    # exact numbers depend on config/config.yaml's contents.
    assert set(control) == {"trailing_stop", "position_sizing_mode", "margin_pct_of_free_margin"}
    assert set(control["trailing_stop"]) == {"enabled", "trigger_pips", "lock_pips"}


def test_default_risk_control_prefers_saved_file_over_config(tmp_path, monkeypatch):
    """A previously-saved override (POST /api/risk-control/set, this process
    lifetime or an earlier one) must win over config.yaml's defaults — this
    is what keeps a dashboard-API-only restart from silently reverting the
    *displayed* values while execution/live_runner.py (which reads the file
    directly) keeps using the saved override underneath."""
    saved_path = tmp_path / ".risk_control.json"
    saved_path.write_text(json.dumps({
        "trailing_stop": {"enabled": True, "trigger_pips": 7.5, "lock_pips": 2.2},
        "position_sizing_mode": "margin_pct",
        "margin_pct_of_free_margin": 8.0,
    }))
    monkeypatch.setattr(dashboard_api, "RISK_CONTROL_PATH", saved_path)

    control = _default_risk_control()

    assert control["trailing_stop"]["enabled"] is True
    assert control["trailing_stop"]["trigger_pips"] == 7.5
    assert control["trailing_stop"]["lock_pips"] == 2.2
    assert control["position_sizing_mode"] == "margin_pct"
    assert control["margin_pct_of_free_margin"] == 8.0


def test_default_risk_control_partial_file_only_overrides_given_fields(tmp_path, monkeypatch):
    """A file that only sets one field (e.g. written before this fix, or a
    hand-edited file) must not blank out the others — missing fields still
    fall back to config.yaml, same as _apply_risk_control_overrides's
    partial-override contract in execution/live_runner.py."""
    saved_path = tmp_path / ".risk_control.json"
    saved_path.write_text(json.dumps({"position_sizing_mode": "margin_pct"}))
    monkeypatch.setattr(dashboard_api, "RISK_CONTROL_PATH", saved_path)

    control = _default_risk_control()

    assert control["position_sizing_mode"] == "margin_pct"
    # trailing_stop / margin_pct_of_free_margin still present and sane
    # (came from config.yaml, not wiped out to None/missing).
    assert isinstance(control["trailing_stop"]["trigger_pips"], float)
    assert isinstance(control["margin_pct_of_free_margin"], float)


def test_default_risk_control_malformed_file_falls_back_to_config(tmp_path, monkeypatch):
    bad_path = tmp_path / ".risk_control.json"
    bad_path.write_text("{not valid json")
    monkeypatch.setattr(dashboard_api, "RISK_CONTROL_PATH", bad_path)

    control = _default_risk_control()

    assert set(control) == {"trailing_stop", "position_sizing_mode", "margin_pct_of_free_margin"}
