"""Authoritative student calendar projection.

The calendar is deliberately read-only: it combines campus timetable rows,
approved event registrations, committed personal calendar writes and reminders.
Planned or rejected actions never appear because they have no database row.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import text

from apps.api.tools.db import Session
from apps.api.tools.exceptions import RecordNotFound


class CalendarItem(BaseModel):
    id: str
    kind: Literal["course", "event", "calendar", "reminder"]
    title: str
    date: str
    start_time: str | None = None
    end_time: str | None = None
    status: Literal["scheduled", "registered", "confirmed", "reminder"]
    source: str
    receipt_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class CalendarResponse(BaseModel):
    student_id: str
    student_name: str
    range_start: str
    range_end: str
    generated_at: str
    items: list[CalendarItem]


def _safe_json(raw: str) -> dict:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _receipt_maps(session, student_id: str) -> tuple[dict[str, list[str]], dict[tuple[str, str], list[str]], dict[str, list[str]]]:
    registrations: dict[str, list[str]] = {}
    calendar_writes: dict[tuple[str, str], list[str]] = {}
    reminders: dict[str, list[str]] = {}
    rows = session.execute(text(
        "SELECT id, tool, args_json FROM receipts WHERE actor=:sid ORDER BY ts"
    ), {"sid": student_id}).mappings().all()
    for row in rows:
        args = _safe_json(row["args_json"])
        if row["tool"] == "register_event" and args.get("event_id"):
            registrations.setdefault(str(args["event_id"]), []).append(row["id"])
        elif row["tool"] == "add_to_calendar" and args.get("date"):
            key = (str(args.get("title", "")), str(args["date"]))
            calendar_writes.setdefault(key, []).append(row["id"])
        elif row["tool"] == "create_reminder" and args.get("remind_at"):
            reminders.setdefault(str(args["remind_at"]).replace("T", " ")[:16], []).append(row["id"])
    return registrations, calendar_writes, reminders


def build_calendar(
    student_id: str,
    *,
    range_start: date | None = None,
    range_end: date | None = None,
) -> CalendarResponse:
    """Return only records that actually exist for this student."""
    today = date.today()
    range_start = range_start or today.replace(day=1)
    range_end = range_end or (range_start + timedelta(days=92))
    if range_end < range_start:
        raise ValueError("range_end must be on or after range_start")

    items: list[CalendarItem] = []
    with Session() as session:
        student = session.execute(
            text("SELECT id, name, branch, year FROM students WHERE id=:sid"),
            {"sid": student_id},
        ).mappings().first()
        if not student:
            raise RecordNotFound(f"No student with id {student_id}")

        registration_receipts, calendar_receipts, reminder_receipts = _receipt_maps(session, student_id)

        # Project the student's recurring timetable onto concrete dates so the
        # approved event can be understood in the context of real classes.
        timetable = session.execute(text(
            "SELECT t.id, t.course_id, c.name, t.day_of_week, t.start_time, t.end_time, t.session_type "
            "FROM timetable t JOIN courses c ON c.id=t.course_id "
            "JOIN enrollments e ON e.course_id=t.course_id "
            "WHERE e.student_id=:sid ORDER BY t.day_of_week, t.start_time"
        ), {"sid": student_id}).mappings().all()
        day = range_start
        timetable_by_day: dict[str, list] = {}
        for row in timetable:
            timetable_by_day.setdefault(row["day_of_week"], []).append(row)
        while day <= range_end:
            for row in timetable_by_day.get(day.strftime("%A"), []):
                items.append(CalendarItem(
                    id=f"course:{row['id']}:{day.isoformat()}", kind="course",
                    title=row["name"], date=day.isoformat(),
                    start_time=row["start_time"], end_time=row["end_time"],
                    status="scheduled", source="Verified campus timetable",
                    metadata={"course_id": row["course_id"], "session_type": row["session_type"]},
                ))
            day += timedelta(days=1)

        # Registration itself is authoritative. It appears even when the user
        # did not separately ask the Services agent to create a calendar row.
        registered = session.execute(text(
            "SELECT er.id AS registration_id, er.registered_at, e.* "
            "FROM event_registrations er JOIN events e ON e.id=er.event_id "
            "WHERE er.student_id=:sid AND e.date BETWEEN :start AND :end"
        ), {"sid": student_id, "start": range_start.isoformat(), "end": range_end.isoformat()}).mappings().all()

        event_items: dict[tuple[str, str, str], CalendarItem] = {}
        for row in registered:
            key = (row["date"], row["start_time"], row["end_time"])
            receipts = list(registration_receipts.get(row["id"], []))
            for (title, event_date), ids in calendar_receipts.items():
                if event_date == row["date"] and (title == row["title"] or not title):
                    receipts.extend(ids)
            item = CalendarItem(
                id=f"event:{row['id']}", kind="event", title=row["title"], date=row["date"],
                start_time=row["start_time"], end_time=row["end_time"], status="registered",
                source="Approved event registration", receipt_ids=list(dict.fromkeys(receipts)),
                metadata={
                    "event_id": row["id"], "category": row["category"],
                    "registered_at": row["registered_at"], "description": row["description"],
                },
            )
            event_items[key] = item
            items.append(item)

        calendar_rows = session.execute(text(
            "SELECT * FROM calendar_events WHERE student_id=:sid AND date BETWEEN :start AND :end "
            "ORDER BY date, start_time"
        ), {"sid": student_id, "start": range_start.isoformat(), "end": range_end.isoformat()}).mappings().all()
        for row in calendar_rows:
            key = (row["date"], row["start_time"], row["end_time"])
            receipt_ids = calendar_receipts.get((row["title"], row["date"]), [])
            if key in event_items:
                event_items[key].receipt_ids = list(dict.fromkeys(event_items[key].receipt_ids + receipt_ids))
                event_items[key].source = "Approved registration · personal calendar"
                continue
            items.append(CalendarItem(
                id=f"calendar:{row['id']}", kind="calendar", title=row["title"], date=row["date"],
                start_time=row["start_time"], end_time=row["end_time"], status="confirmed",
                source=row["source"] or "Sūtra personal calendar", receipt_ids=receipt_ids,
            ))

        reminder_rows = session.execute(text(
            "SELECT * FROM reminders WHERE student_id=:sid "
            "AND substr(remind_at, 1, 10) BETWEEN :start AND :end ORDER BY remind_at"
        ), {"sid": student_id, "start": range_start.isoformat(), "end": range_end.isoformat()}).mappings().all()
        for row in reminder_rows:
            normalized = row["remind_at"].replace("T", " ")
            items.append(CalendarItem(
                id=f"reminder:{row['id']}", kind="reminder", title=row["message"],
                date=normalized[:10], start_time=normalized[11:16] or None,
                status="reminder", source="Sūtra reminder",
                receipt_ids=reminder_receipts.get(normalized[:16], []),
            ))

    kind_order = {"course": 0, "event": 1, "calendar": 2, "reminder": 3}
    items.sort(key=lambda item: (item.date, item.start_time or "99:99", kind_order[item.kind], item.title))
    return CalendarResponse(
        student_id=student_id, student_name=student["name"],
        range_start=range_start.isoformat(), range_end=range_end.isoformat(),
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"), items=items,
    )
