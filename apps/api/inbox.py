"""Read-only, deterministic notification feed for the student cockpit.

The inbox deliberately does not use an LLM. It is a projection over campus
records, which keeps alerts fast, explainable and free of provider quota.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import text

from apps.api.tools.db import Session
from apps.api.tools.exceptions import RecordNotFound


class InboxItem(BaseModel):
    id: str
    kind: Literal["attendance", "event", "placement", "library", "reminder", "calendar"]
    severity: Literal["urgent", "warning", "info", "success"]
    title: str
    detail: str
    due_at: str | None = None
    source: str
    action_label: str | None = None
    action_prompt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InboxResponse(BaseModel):
    student_id: str
    student_name: str
    generated_at: str
    attention_count: int
    items: list[InboxItem]


_SEVERITY_ORDER = {"urgent": 0, "warning": 1, "info": 2, "success": 3}


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def build_inbox(student_id: str, *, today: date | None = None) -> InboxResponse:
    """Build a prioritized feed without changing any campus record."""
    today = today or date.today()
    horizon_14 = today + timedelta(days=14)
    horizon_60 = today + timedelta(days=60)
    items: list[InboxItem] = []

    with Session() as session:
        student = session.execute(
            text("SELECT * FROM students WHERE id=:sid"), {"sid": student_id}
        ).mappings().first()
        if not student:
            raise RecordNotFound(f"No student with id {student_id}")

        attendance = session.execute(text(
            "SELECT a.course_id, c.name AS course_name, a.classes_held, a.classes_attended "
            "FROM attendance a JOIN courses c ON c.id=a.course_id "
            "WHERE a.student_id=:sid"
        ), {"sid": student_id}).mappings().all()
        for row in attendance:
            held, attended = row["classes_held"], row["classes_attended"]
            pct = round(100 * attended / held, 1) if held else 0.0
            if pct >= 80:
                continue
            needed = max(0, math.ceil((0.75 * held - attended) / 0.25)) if pct < 75 else 0
            if pct < 75:
                title = f"{row['course_name']} is below 75%"
                detail = (
                    f"Attendance is {pct:.1f}% ({attended}/{held}). Attend the next {needed} "
                    f"session{'s' if needed != 1 else ''} to recover to the required threshold."
                )
                severity = "urgent"
                prompt = (
                    f"My attendance in {row['course_name']} is {pct:.1f}%. "
                    "Explain my exam eligibility and build a recovery plan."
                )
            else:
                safe_misses = max(0, math.floor((attended / 0.75) - held))
                title = f"{row['course_name']} attendance needs attention"
                detail = f"Attendance is {pct:.1f}%. You can miss at most {safe_misses} more session(s) before falling below 75%."
                severity = "warning"
                prompt = f"Show my attendance risk for {row['course_name']} and what I should do next."
            items.append(InboxItem(
                id=f"attendance:{row['course_id']}", kind="attendance", severity=severity,
                title=title, detail=detail, source="Academic records",
                action_label="Make a recovery plan", action_prompt=prompt,
                metadata={"course_id": row["course_id"], "percentage": pct, "threshold": 75},
            ))

        registered_ids = set(session.execute(
            text("SELECT event_id FROM event_registrations WHERE student_id=:sid"),
            {"sid": student_id},
        ).scalars().all())
        events = session.execute(text(
            "SELECT id, title, date, start_time, end_time, total_seats, seats_taken, category "
            "FROM events ORDER BY date, start_time"
        )).mappings().all()
        event_count = 0
        for row in events:
            event_date = _parse_date(row["date"])
            if event_date is None or not (today <= event_date <= horizon_14):
                continue
            remaining = row["total_seats"] - row["seats_taken"]
            days = (event_date - today).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            registered = row["id"] in registered_ids
            if registered:
                severity = "success"
                title = f"Registered: {row['title']}"
                detail = f"Your place is confirmed {when}, {row['start_time']}–{row['end_time']}."
                action_label = "Check my schedule"
                action_prompt = f"Check my timetable for conflicts with {row['title']} on {row['date']} at {row['start_time']}."
            else:
                severity = "warning" if remaining <= 10 else "info"
                title = row["title"]
                capacity = "full" if remaining <= 0 else f"{remaining} seats left"
                detail = f"{row['category'].title()} {when}, {row['start_time']}–{row['end_time']} · {capacity}."
                action_label = "Check & register"
                action_prompt = (
                    f"Check whether {row['title']} on {row['date']} fits my timetable and attendance. "
                    "If it does, help me register."
                )
            items.append(InboxItem(
                id=f"event:{row['id']}", kind="event", severity=severity,
                title=title, detail=detail, due_at=f"{row['date']}T{row['start_time']}",
                source="Campus events", action_label=action_label, action_prompt=action_prompt,
                metadata={"event_id": row["id"], "seats_remaining": remaining, "registered": registered},
            ))
            event_count += 1
            if event_count >= 5:
                break

        companies = session.execute(text(
            "SELECT * FROM companies WHERE application_deadline IS NOT NULL ORDER BY application_deadline"
        )).mappings().all()
        deadline_count = 0
        for row in companies:
            deadline = _parse_date(row["application_deadline"])
            if deadline is None or not (today <= deadline <= horizon_60):
                continue
            branches = row["eligible_branches"].split(",")
            eligible = (
                student["cgpa"] >= row["min_cgpa"]
                and student["backlogs"] <= row["max_backlogs"]
                and student["branch"] in branches
            )
            if not eligible:
                continue
            days = (deadline - today).days
            items.append(InboxItem(
                id=f"placement:{row['id']}", kind="placement",
                severity="warning" if days <= 14 else "info",
                title=f"{row['name']} applications close in {days} days",
                detail=f"You meet the listed criteria for {row['role']}. Deadline: {deadline.strftime('%d %b')}.",
                due_at=row["application_deadline"], source="Placement cell",
                action_label="Review eligibility",
                action_prompt=f"Verify my eligibility for {row['name']} and help me prepare the application.",
                metadata={"company_id": row["id"], "days_remaining": days},
            ))
            deadline_count += 1
            if deadline_count >= 2:
                break

        loans = session.execute(text(
            "SELECT book_title, due_at FROM library_loans "
            "WHERE student_id=:sid AND returned=0 ORDER BY due_at"
        ), {"sid": student_id}).mappings().all()
        for row in loans:
            due = _parse_date(row["due_at"])
            if due is None or due > today + timedelta(days=30):
                continue
            days = (due - today).days
            overdue = days < 0
            detail = f"Overdue by {-days} day(s)." if overdue else f"Due in {days} day(s), on {due.strftime('%d %b')}."
            items.append(InboxItem(
                id=f"library:{row['book_title']}", kind="library",
                severity="urgent" if overdue else "warning" if days <= 3 else "info",
                title=f"Library book: {row['book_title']}", detail=detail,
                due_at=row["due_at"], source="Library",
                action_label="Ask about renewal",
                action_prompt=f"Can I renew my library loan for {row['book_title']}? Show the due date first.",
            ))

        reminders = session.execute(text(
            "SELECT id, message, remind_at FROM reminders WHERE student_id=:sid ORDER BY remind_at LIMIT 3"
        ), {"sid": student_id}).mappings().all()
        for row in reminders:
            items.append(InboxItem(
                id=f"reminder:{row['id']}", kind="reminder", severity="info",
                title="Reminder", detail=row["message"], due_at=row["remind_at"],
                source="Sūtra reminder",
            ))

    items.sort(key=lambda item: (_SEVERITY_ORDER[item.severity], item.due_at or "9999"))
    return InboxResponse(
        student_id=student_id, student_name=student["name"],
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        attention_count=sum(item.severity in {"urgent", "warning"} for item in items),
        items=items,
    )
