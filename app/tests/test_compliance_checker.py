"""Compliance checker tests."""

from types import SimpleNamespace

from app.services.compliance_checker import ComplianceChecker


def _src(**kwargs):
    defaults = dict(
        source_type="managed_retailer_search",
        status="active",
        allowed=True,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_active_source_allowed():
    ok, _ = ComplianceChecker().can_use_source(_src())
    assert ok


def test_paused_source_rejected():
    ok, reason = ComplianceChecker().can_use_source(_src(status="paused"))
    assert not ok
    assert "paused" in reason


def test_blocked_source_rejected():
    ok, _ = ComplianceChecker().can_use_source(_src(status="blocked_or_rate_limited"))
    assert not ok


def test_google_shopping_rejected():
    ok, reason = ComplianceChecker().can_use_source(_src(source_type="google_shopping"))
    assert not ok
    assert "google" in reason


def test_captcha_triggers_escalate_not_stop_on_tier1():
    cc = ComplianceChecker()
    esc, _ = cc.should_escalate_after_response("Please complete the captcha", 200, 1)
    stop, _ = cc.should_stop_after_response("Please complete the captcha", 200, 1)
    assert esc
    assert not stop


def test_captcha_stops_on_tier4():
    cc = ComplianceChecker()
    stop, reason = cc.should_stop_after_response("captcha required", 200, 4)
    assert stop
    assert reason == "captcha_detected"


def test_403_triggers_escalate_tier1():
    cc = ComplianceChecker()
    esc, reason = cc.should_escalate_after_response("", 403, 1)
    assert esc
    assert reason == "access_denied"


def test_429_triggers_escalate_tier1():
    cc = ComplianceChecker()
    esc, reason = cc.should_escalate_after_response("", 429, 1)
    assert esc
    assert reason == "rate_limited"
