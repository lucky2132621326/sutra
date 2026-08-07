"""
Chaos / resilience tests: retry -> circuit breaker -> fallback -> degraded,
and the guarantee that domain refusals are NOT swallowed as infrastructure
faults.

Run: python scripts/seed.py && pytest tests/test_resilience.py -v
"""
import pytest

from apps.api.tools import chaos, events, resilience
from apps.api.tools.exceptions import SeatsUnavailable
from apps.api.tools.registry import TOOL_REGISTRY

ANANYA = "1602-23-733-042"


@pytest.fixture(autouse=True)
def clean_chaos_state():
    chaos.reset()
    resilience._circuits.clear()
    resilience.drain_events()
    yield
    chaos.reset()
    resilience._circuits.clear()
    resilience.drain_events()


def test_healthy_service_passes_through():
    fn = TOOL_REGISTRY["check_placement_eligibility"]["fn"]
    result = fn(student_id=ANANYA, company_id="google")
    assert result.is_eligible is True
    assert resilience.drain_events() == []  # no retries needed


def test_error_500_retries_then_falls_back_to_rag_policy():
    chaos.set_mode("placement", "error_500")
    fn = TOOL_REGISTRY["check_placement_eligibility"]["fn"]

    result = fn(student_id=ANANYA, company_id="google")

    # Fell back rather than raising or returning a wrong answer.
    assert isinstance(result, dict)
    assert result["degraded"] is True
    assert "unavailable" in result["degradation_reason"].lower()
    assert "confirm with the placement cell" in result["advice"].lower()

    kinds = [e["kind"] for e in resilience.drain_events()]
    assert kinds.count("tool.retry") == 2, f"expected 2 retries, got {kinds}"
    assert kinds[-1] == "tool.fallback"


def test_tool_without_fallback_degrades_instead_of_raising():
    chaos.set_mode("erp", "error_500")
    fn = TOOL_REGISTRY["get_attendance"]["fn"]

    result = fn(student_id=ANANYA)

    assert isinstance(result, dict)
    assert result["degraded"] is True
    assert "get_attendance is unavailable" in result["degradation_reason"]
    kinds = [e["kind"] for e in resilience.drain_events()]
    assert kinds.count("tool.retry") == 2
    assert kinds[-1] == "tool.fallback"


def test_circuit_opens_after_repeated_failures_and_short_circuits():
    chaos.set_mode("erp", "error_500")
    fn = TOOL_REGISTRY["get_timetable"]["fn"]

    fn(student_id=ANANYA)          # 3 failures (1 + 2 retries) -> opens circuit
    resilience.drain_events()

    fn(student_id=ANANYA)          # second call should short-circuit
    kinds = [e for e in resilience.drain_events()]
    assert any(e["payload"].get("reason") == "circuit_open" for e in kinds), \
        f"expected a circuit_open fallback, got {kinds}"
    # Short-circuited: no retry attempts were made on the second call.
    assert not any(e["kind"] == "tool.retry" for e in kinds)


def test_domain_refusal_is_not_swallowed_as_infrastructure_failure():
    """A full event must still raise SeatsUnavailable — retrying a correct
    'no' would be wrong, and hiding it in a degraded blob would mislead."""
    remaining = events.get_event_capacity("evt_alumni_talk").seats_remaining
    for i in range(remaining):
        events.register_event(f"filler-refusal-{i}", "evt_alumni_talk", actor="filler", approved=True)

    fn = TOOL_REGISTRY["register_event"]["fn"]
    with pytest.raises(SeatsUnavailable):
        fn(student_id=ANANYA, event_id="evt_alumni_talk", approved=True)

    # Crucially: no retries were burned on a decision that will never change.
    assert not any(e["kind"] == "tool.retry" for e in resilience.drain_events())


def test_chaos_status_roundtrip():
    chaos.set_mode("placement", "timeout")
    assert chaos.get_mode("placement") == "timeout"
    assert chaos.status()["placement"] == "timeout"
    chaos.reset()
    assert chaos.get_mode("placement") == "healthy"


def test_unknown_chaos_mode_rejected():
    with pytest.raises(ValueError, match="unknown chaos mode"):
        chaos.set_mode("placement", "explode")
