from datetime import date

from apps.api.calendar import build_calendar
from apps.api.tools import events, services

ANANYA = "1602-23-733-042"


def test_calendar_projects_real_courses_but_no_unapproved_event():
    staged = events.register_event(ANANYA, "evt_workshop_sat", actor=ANANYA)
    calendar = build_calendar(
        ANANYA, range_start=date(2026, 8, 10), range_end=date(2026, 8, 16),
    )
    assert any(item.kind == "course" and item.metadata.get("course_id") == "CS301L" for item in calendar.items)
    assert not any(item.kind == "event" and item.metadata.get("event_id") == "evt_workshop_sat" for item in calendar.items)
    assert staged.description


def test_approved_event_calendar_write_and_reminder_merge_with_receipts():
    registration = events.register_event(
        ANANYA, "evt_workshop_sat", actor=ANANYA, approved=True,
    )
    calendar_write = services.add_to_calendar(
        ANANYA, "Placement Prep Workshop (Saturday Batch)",
        "2026-08-15", "10:00", "12:00", actor=ANANYA,
    )
    reminder = services.create_reminder(
        ANANYA, "Placement workshop starts in one hour.",
        "2026-08-15 09:00", actor=ANANYA,
    )

    calendar = build_calendar(
        ANANYA, range_start=date(2026, 8, 10), range_end=date(2026, 8, 16),
    )
    event_item = next(item for item in calendar.items if item.kind == "event")
    reminder_item = next(item for item in calendar.items if item.kind == "reminder")

    assert event_item.source == "Approved registration · personal calendar"
    assert set(event_item.receipt_ids) == {registration.receipt_id, calendar_write.receipt_id}
    assert reminder.receipt_id in reminder_item.receipt_ids
    assert len([item for item in calendar.items if item.kind in {"event", "calendar"}]) == 1

    # A browser refresh creates a new API request and database session. The
    # commitment and its proof must survive that round trip; none of this is
    # transient frontend state.
    refreshed = build_calendar(
        ANANYA, range_start=date(2026, 8, 10), range_end=date(2026, 8, 16),
    )
    refreshed_event = next(item for item in refreshed.items if item.kind == "event")
    assert set(refreshed_event.receipt_ids) == {registration.receipt_id, calendar_write.receipt_id}
    assert any(item.id == reminder_item.id for item in refreshed.items)
