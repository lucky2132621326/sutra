"""
Tests for the section-3 gap fixes: atomic seat claim, role-scoped tool
permission, and edited-approval-arg validation.

Run: python scripts/seed.py && pytest tests/test_gap_fixes.py -v
"""
import sqlite3
from pathlib import Path

import pytest

from apps.api.graph.nodes import _validated_edited_args
from apps.api.tools import events, services
from apps.api.tools.exceptions import SeatsUnavailable
from apps.api.tools.registry import TOOL_REGISTRY, can_invoke

ANANYA = "1602-23-733-042"
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "campus.db"


# --- Atomic seat claim (race condition fix) ---

def test_register_event_never_oversells_last_seat():
    """Fill an event to exactly one remaining seat, then attempt two
    registrations. Exactly one must succeed; seats_taken must never exceed
    total_seats."""
    cap = events.get_event_capacity("evt_cyber_ws")
    for i in range(cap.seats_remaining - 1):
        events.register_event(f"filler-race-{i}", "evt_cyber_ws", actor="filler", approved=True)

    assert events.get_event_capacity("evt_cyber_ws").seats_remaining == 1

    events.register_event(ANANYA, "evt_cyber_ws", actor=ANANYA, approved=True)
    with pytest.raises(SeatsUnavailable):
        events.register_event("someone-else", "evt_cyber_ws", actor="someone-else", approved=True)

    final = events.get_event_capacity("evt_cyber_ws")
    assert final.seats_remaining == 0
    assert final.seats_taken == final.total_seats  # never oversold


def test_seats_taken_never_exceeds_total_in_db():
    conn = sqlite3.connect(DB_PATH)
    bad = conn.execute("SELECT id, seats_taken, total_seats FROM events WHERE seats_taken > total_seats").fetchall()
    conn.close()
    assert bad == [], f"oversold events found: {bad}"


# --- Role-scoped permissions ---

def test_student_can_invoke_student_tools():
    assert can_invoke("get_attendance", "student") is True


def test_higher_roles_inherit_student_tools():
    assert can_invoke("get_attendance", "faculty") is True
    assert can_invoke("get_attendance", "admin") is True


def test_unknown_role_degrades_to_student_not_denied():
    assert can_invoke("get_attendance", "wizard") is True


def test_unknown_tool_is_denied():
    assert can_invoke("definitely_not_a_tool", "admin") is False


def test_role_below_requirement_is_denied():
    # Temporarily raise a tool's bar to prove the hierarchy actually gates.
    original = TOOL_REGISTRY["get_attendance"]["required_role"]
    TOOL_REGISTRY["get_attendance"]["required_role"] = "faculty"
    try:
        assert can_invoke("get_attendance", "student") is False
        assert can_invoke("get_attendance", "faculty") is True
    finally:
        TOOL_REGISTRY["get_attendance"]["required_role"] = original


# --- Edited approval args validation ---

def test_validated_edited_args_accepts_known_fields():
    args = _validated_edited_args(events.register_event, "register_event",
                                   {"student_id": ANANYA, "event_id": "evt_workshop_sat"})
    assert args == {"student_id": ANANYA, "event_id": "evt_workshop_sat"}


def test_validated_edited_args_rejects_unknown_field():
    with pytest.raises(ValueError, match="unknown field"):
        _validated_edited_args(events.register_event, "register_event",
                                {"student_id": ANANYA, "event_id": "x", "seats_taken": 0})


def test_validated_edited_args_rejects_approved_injection():
    """`approved` is the gate's own flag — a human edit must not be able to
    set it directly."""
    with pytest.raises(ValueError, match="unknown field"):
        _validated_edited_args(events.register_event, "register_event", {"approved": True})


def test_validated_edited_args_rejects_non_dict():
    with pytest.raises(ValueError, match="must be an object"):
        _validated_edited_args(services.send_email, "send_email", ["not", "a", "dict"])


def test_edited_args_recheck_catches_full_event():
    """The re-validation path calls the tool unapproved; for a full event that
    raises SeatsUnavailable, so an edit swapping in a full event cannot slip
    past the gate."""
    # Fill evt_ai_ml_3 completely, then confirm an unapproved call raises.
    remaining = events.get_event_capacity("evt_ai_ml_3").seats_remaining
    for i in range(remaining):
        events.register_event(f"filler-full-{i}", "evt_ai_ml_3", actor="filler", approved=True)
    with pytest.raises(SeatsUnavailable):
        events.register_event(ANANYA, "evt_ai_ml_3", approved=False)
