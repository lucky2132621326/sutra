"""
Tests for all 24 tools in apps/api/tools/, run against the seeded
data/campus.db (scripts/seed.py). Not literally in-memory as P3 suggested —
seed.py is deterministic and cheap to run, so testing against the real seed
gives the same guarantees without a second DB-setup path to keep in sync.

Run: python scripts/seed.py && pytest tests/test_tools.py -v
"""
import pytest

from apps.api.tools import academic, events, placement, services
from apps.api.tools.exceptions import RecordNotFound, SeatsUnavailable
from apps.api.tools.models import PendingAction
from apps.api.tools.registry import TOOL_REGISTRY, TOOLS_BY_AGENT

ANANYA = "1602-23-733-042"
RAHUL = "1602-24-736-018"


def test_registry_has_24_tools():
    assert len(TOOL_REGISTRY) == 24


def test_registry_covers_5_agents():
    assert set(TOOLS_BY_AGENT.keys()) == {"academic", "placement", "events", "knowledge", "services"}


# --- Academic ---

def test_get_timetable_ananya_has_dbms_lab_thursday():
    result = academic.get_timetable(ANANYA)
    lab = next(e for e in result.entries if e.course_id == "CS301L")
    assert lab.day_of_week == "Thursday"
    assert lab.start_time == "14:00" and lab.end_time == "16:00"


def test_attendance_eligibility_dbms_lab_below_75():
    elig = academic.compute_attendance_eligibility(ANANYA, "CS301L")
    assert elig.classes_attended == 26
    assert elig.classes_held == 37
    assert elig.is_eligible is False
    assert elig.condonation_possible is True  # >= 65%


def test_schedule_conflict_detects_thursday_collision():
    conflict = academic.check_schedule_conflict(ANANYA, "Thursday", "14:00", "16:00")
    assert conflict.has_conflict is True
    assert conflict.conflicting_course_id == "CS301L"


def test_schedule_conflict_none_on_free_slot():
    conflict = academic.check_schedule_conflict(ANANYA, "Thursday", "18:00", "19:00")
    assert conflict.has_conflict is False


def test_get_attendance_unknown_student_raises():
    with pytest.raises(RecordNotFound):
        academic.get_attendance("no-such-id")


# --- Placement ---

def test_ananya_eligible_for_google():
    elig = placement.check_placement_eligibility(ANANYA, "google")
    assert elig.is_eligible is True
    assert all(c.passed for c in elig.breakdown)


def test_ananya_ineligible_for_goldman_by_cgpa():
    elig = placement.check_placement_eligibility(ANANYA, "goldman")
    assert elig.is_eligible is False
    cgpa_check = next(c for c in elig.breakdown if c.criterion == "CGPA")
    assert cgpa_check.passed is False


def test_rahul_ineligible_everywhere():
    for company_id in ("google", "microsoft", "amazon"):
        elig = placement.check_placement_eligibility(RAHUL, company_id)
        assert elig.is_eligible is False


def test_check_placement_eligibility_is_case_insensitive_on_company_id():
    # An LLM picking a tool arg is as likely to pass "Google" (display name
    # casing) as "google" (the literal id) — must resolve either way.
    elig = placement.check_placement_eligibility(ANANYA, "Google")
    assert elig.is_eligible is True


def test_list_companies_filters_by_branch():
    result = placement.list_companies(branch="MECH")
    assert all("MECH" in c.eligible_branches for c in result.companies)


# --- Events ---

def test_search_events_returns_thu_and_sat_workshops():
    result = events.search_events(query="Placement Prep")
    ids = {e.id for e in result.events}
    assert {"evt_workshop_thu", "evt_workshop_sat"} <= ids


def test_thursday_workshop_has_2_seats_left():
    cap = events.get_event_capacity("evt_workshop_thu")
    assert cap.seats_remaining == 2


def test_register_event_without_approval_returns_pending_action():
    result = events.register_event(ANANYA, "evt_workshop_sat")
    assert isinstance(result, PendingAction)
    assert result.tool == "register_event"


def test_register_event_with_approval_writes_and_returns_receipt():
    before = events.get_event_capacity("evt_ai_ml_1").seats_taken
    result = events.register_event(RAHUL, "evt_ai_ml_1", actor=RAHUL, approved=True)
    assert result.status == "registered"
    assert result.receipt_id
    after = events.get_event_capacity("evt_ai_ml_1").seats_taken
    assert after == before + 1


def test_register_event_raises_seats_unavailable_when_full():
    # evt_ai_ml_1 has 50 total, 41 taken pre-seed; fill remaining then expect raise.
    cap = events.get_event_capacity("evt_ai_ml_1")
    for i in range(cap.seats_remaining):
        events.register_event(f"filler-{i}", "evt_ai_ml_1", actor="filler", approved=True)
    with pytest.raises(SeatsUnavailable):
        events.register_event(ANANYA, "evt_ai_ml_1", actor=ANANYA, approved=True)


def test_get_event_capacity_is_case_insensitive_on_event_id():
    cap = events.get_event_capacity("EVT_Workshop_Thu")
    assert cap.event_id == "evt_workshop_thu"
    assert cap.seats_remaining == 2


def test_recommend_clubs_matches_ml_interest():
    result = events.recommend_clubs(ANANYA, interest="machine learning")
    assert any("Machine Learning" in r.name for r in result.recommendations)


# --- Services & Comms ---

def test_get_hostel_info_ananya():
    info = services.get_hostel_info(ANANYA)
    assert info.block == "B-Block" and info.no_dues is True


def test_library_loans_ananya_has_active_loan():
    result = services.library_loans(ANANYA)
    assert any(not loan.returned for loan in result.loans)


def test_renew_book_extends_due_date():
    before = services.library_loans(ANANYA).loans[0].due_at
    result = services.renew_book(ANANYA, "Database System Concepts", actor=ANANYA)
    assert result.new_due_at != before


def test_file_grievance_without_approval_returns_pending_action():
    result = services.file_grievance(ANANYA, "hostel", "Water supply issue in B-Block.")
    assert isinstance(result, PendingAction)


def test_file_grievance_with_approval_writes():
    result = services.file_grievance(ANANYA, "hostel", "Water supply issue.", actor=ANANYA, approved=True)
    assert result.status == "open"
    assert result.receipt_id


def test_draft_email_does_not_require_approval():
    draft = services.draft_email("hod.cse@vasavi.ac.in", "Makeup exam request", "Requesting permission...")
    assert draft.to == "hod.cse@vasavi.ac.in"


def test_send_email_without_approval_returns_pending_action():
    result = services.send_email("hod.cse@vasavi.ac.in", "Makeup exam request", "body")
    assert isinstance(result, PendingAction)


def test_send_email_with_approval_sends():
    result = services.send_email("hod.cse@vasavi.ac.in", "Makeup exam request", "body", actor=ANANYA, approved=True)
    assert result.status == "sent"


def test_add_to_calendar():
    result = services.add_to_calendar(ANANYA, "Saturday Placement Workshop", "2026-08-15", "10:00", "12:00", actor=ANANYA)
    assert result.title == "Saturday Placement Workshop"


def test_create_reminder():
    result = services.create_reminder(ANANYA, "Workshop starts in 1 hour", "2026-08-15T09:00:00", actor=ANANYA)
    assert result.message == "Workshop starts in 1 hour"


def test_escalate_to_human():
    result = services.escalate_to_human(ANANYA, "Out-of-scope request about fee refunds.", actor=ANANYA)
    assert result.status == "escalated"


# --- Tools must tolerate how an LLM addresses them ---

def test_attendance_accepts_a_course_name_not_just_an_id():
    """The caller reads an answer saying "DBMS Lab", not a schema saying
    "CS301L". Passing the name used to raise RecordNotFound, so a run reported
    "no attendance record for DBMS Lab" in the same answer that printed that
    course's attendance."""
    from apps.api.tools.academic import compute_attendance_eligibility

    by_id = compute_attendance_eligibility(ANANYA, "CS301L")
    for alias in ("DBMS Lab", "dbms lab", "DBMS", "cs301l"):
        assert compute_attendance_eligibility(ANANYA, alias).course_id == by_id.course_id


def test_attendance_still_rejects_a_course_that_does_not_exist():
    """Forgiving is not the same as inventing: an unknown course must fail."""
    from apps.api.tools.academic import compute_attendance_eligibility
    from apps.api.tools.exceptions import RecordNotFound

    with pytest.raises(RecordNotFound):
        compute_attendance_eligibility(ANANYA, "CS999")


def test_event_search_ignores_filler_and_unknown_categories():
    """Both were observed from a live model: query="upcoming events" (no event
    contains "upcoming") and category="events this week" (not a real category).
    Each returned nothing from a table holding twelve events."""
    from apps.api.tools.events import search_events

    assert len(search_events("upcoming events?").events) == 12
    assert len(search_events("AI", category="events this week").events) > 0
    assert len(search_events("any workshops this week").events) > 0
    # A genuinely unmatched query must still return nothing.
    assert search_events("quantum blockchain").events == []
