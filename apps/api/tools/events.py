"""Events Agent tools: discover workshops/hackathons, register, recommend clubs.

register_event is a write tool requiring approval — see registry.py. Called
without approved=True it returns a PendingAction; called with approved=True
it performs the write and returns an EventRegistrationResult.
"""
import time
import uuid

from sqlalchemy import text

from apps.api.tools.db import Session
from apps.api.tools.exceptions import RecordNotFound, SeatsUnavailable
from apps.api.tools.models import (
    ClubRecommendation,
    ClubRecommendations,
    EventCapacity,
    EventRegistrationResult,
    EventSearchResult,
    EventSummary,
    PendingAction,
)
from apps.api.tools.receipts import write_receipt


def _event_or_404(session, event_id: str):
    """Resolve an event by id, or failing that by title.

    The caller has just read a search result that says "Placement Prep
    Workshop" and passes exactly that; the column holds "evt_workshop_thu".
    Observed live: three consecutive attempts — "placement_workshop",
    "Placement Prep Workshop", "Placement Prep Workshop on Thursday 2026-08-13
    14:00-16:00" — all rejected.

    This one mattered more than the other lookups. register_event stages the
    human-approval gate, and a tool that raises never returns a PendingAction:
    the gate had nothing to open, so the run sailed past the one step that
    required a human and still reported success.
    """
    row = session.execute(
        text("SELECT * FROM events WHERE LOWER(id)=LOWER(:id)"), {"id": event_id}
    ).mappings().first()
    if row:
        return row

    needle = event_id.strip().lower()
    rows = session.execute(text("SELECT * FROM events")).mappings().all()
    for r in rows:
        if r["title"].strip().lower() == needle:
            return r
    # Then a containment match, shortest title first so "Placement Prep
    # Workshop" cannot silently resolve to the Saturday batch — an ambiguity
    # that would book the wrong slot.
    partial = sorted(
        (r for r in rows if needle and (r["title"].lower() in needle or needle in r["title"].lower())),
        key=lambda r: len(r["title"]),
    )
    if partial:
        return partial[0]

    raise RecordNotFound(f"No event with id {event_id}")


# Words that describe the ASKING, not the thing asked for. An LLM caller
# phrases the query the way the student did — "upcoming events", "any workshops
# this week", "show me events" — and none of those adjectives appear in an
# event title. Requiring them to match returned nothing for a database holding
# twelve perfectly good events.
_QUERY_NOISE = {
    "a", "an", "the", "any", "all", "some", "me", "my", "i", "is", "are", "there",
    "what", "whats", "which", "when", "show", "list", "find", "get", "tell", "about",
    "upcoming", "coming", "next", "this", "week", "month", "today", "tomorrow",
    "event", "events", "please", "for", "of", "on", "in", "at", "to", "do", "you",
    "have", "got", "can", "want", "looking", "search", "give",
}


def _matches(word: str, haystack: str) -> bool:
    """Substring match that survives a trailing plural.

    Students ask for "workshops"; the row says "Workshop". Nothing more
    elaborate than that is warranted here — this is a keyword filter over a
    dozen rows, not a search engine, and a real stemmer would bring a
    dependency and a pile of surprises for one letter.
    """
    if word in haystack:
        return True
    if word.endswith("es") and word[:-2] in haystack:
        return True
    return word.endswith("s") and word[:-1] in haystack


def search_events(query: str = "", category: str = "") -> EventSearchResult:
    """Find events by keyword, ranked by how well they match.

    Deliberately forgiving, because the caller is a language model relaying a
    student's own words. A query whose every word is filler ("any upcoming
    events?") means "show me everything", not "show me nothing" — and a query
    with several terms should surface the best match rather than requiring all
    of them, so one unusual word cannot empty the result set.
    """
    with Session() as session:
        rows = session.execute(text("SELECT * FROM events")).mappings().all()

        words = [w.strip(".,!?;:'\"") for w in query.lower().split()]
        terms = [w for w in words if w and w not in _QUERY_NOISE]

        # An unrecognised category is treated as no filter at all. The caller is
        # a language model, and it invents plausible-sounding values — observed:
        # category="events this week", which matched nothing and reported "no AI
        # events" for a database holding three of them. A filter nobody can
        # satisfy should be ignored, not silently empty the result set.
        known = {(r["category"] or "").lower() for r in rows}
        cat = category.strip().lower()
        if cat and cat not in known:
            cat = ""

        scored: list[tuple[int, EventSummary]] = []
        for r in rows:
            if cat and (r["category"] or "").lower() != cat:
                continue
            haystack = (r["title"] + " " + (r["description"] or "") + " "
                        + (r["category"] or "") + " " + r["day_of_week"]).lower()
            hits = sum(1 for w in terms if _matches(w, haystack))
            # No meaningful terms -> the query was "show me events": everything
            # qualifies. Otherwise at least one term has to land.
            if terms and hits == 0:
                continue
            scored.append((hits, EventSummary(
                id=r["id"], title=r["title"], description=r["description"] or "",
                day_of_week=r["day_of_week"], date=r["date"], start_time=r["start_time"],
                end_time=r["end_time"],
                seats_remaining=r["total_seats"] - r["seats_taken"], category=r["category"] or "",
            )))

        # Best matches first; ties keep the database's own (date) ordering.
        scored.sort(key=lambda pair: -pair[0])
        return EventSearchResult(events=[summary for _, summary in scored])


def get_event_capacity(event_id: str) -> EventCapacity:
    with Session() as session:
        row = _event_or_404(session, event_id)
        return EventCapacity(
            event_id=row["id"], total_seats=row["total_seats"], seats_taken=row["seats_taken"],
            seats_remaining=row["total_seats"] - row["seats_taken"],
        )


def register_event(student_id: str, event_id: str, actor: str = "", approved: bool = False):
    """requires_approval. Without approved=True, returns a PendingAction and
    performs no write. With approved=True, performs the registration."""
    with Session() as session:
        event = _event_or_404(session, event_id)
        event_id = event["id"]  # canonical case, in case the caller passed a display-name-cased guess
        remaining = event["total_seats"] - event["seats_taken"]
        if remaining <= 0:
            raise SeatsUnavailable(f"No seats remaining for {event['title']}")

        if not approved:
            return PendingAction(
                id=uuid.uuid4().hex[:8], agent="events", tool="register_event",
                args={"student_id": student_id, "event_id": event_id},
                description=f"Register {student_id} for '{event['title']}' ({event['date']} {event['start_time']}-{event['end_time']}).",
                risk="low", reversible=True,
                preview=f"{event['title']} — {event['date']} {event['start_time']}-{event['end_time']}, "
                        f"{remaining} seat(s) left.",
            )

        # Idempotency: the same registration can legitimately be attempted more
        # than once — a replan re-dispatches the step, or an approval is
        # retried. Re-running it must NOT consume a second seat. Return the
        # existing registration instead, so the action executes at most once.
        existing = session.execute(
            text("SELECT id, registered_at FROM event_registrations "
                 "WHERE event_id=:eid AND student_id=:sid"),
            {"eid": event_id, "sid": student_id},
        ).mappings().first()
        if existing:
            return EventRegistrationResult(
                receipt_id=f"existing-{existing['id']}", event_id=event_id,
                student_id=student_id, status="already_registered",
            )

        # Atomic claim: the seat check above is advisory only (it can go stale
        # between the PendingAction preview and the approved write, or race a
        # concurrent registration). This single conditional UPDATE is the real
        # gate — SQLite evaluates it atomically, so exactly one of two
        # concurrent callers can win the last seat. rowcount==0 means the
        # seats filled up in between, so nothing is written.
        claimed = session.execute(
            text("UPDATE events SET seats_taken = seats_taken + 1 "
                 "WHERE id=:id AND seats_taken < total_seats"),
            {"id": event_id},
        )
        if claimed.rowcount == 0:
            session.rollback()
            raise SeatsUnavailable(f"No seats remaining for {event['title']}")

        session.execute(
            text("INSERT INTO event_registrations (event_id, student_id, registered_at) VALUES (:eid, :sid, :ts)"),
            {"eid": event_id, "sid": student_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")},
        )

        receipt_id = write_receipt(
            session, actor=actor or student_id, tool="register_event",
            args={"student_id": student_id, "event_id": event_id},
            result={"status": "registered"}, approved_by=actor or None,
        )
        session.commit()
        return EventRegistrationResult(receipt_id=receipt_id, event_id=event_id, student_id=student_id, status="registered")


def recommend_clubs(student_id: str, interest: str = "") -> ClubRecommendations:
    with Session() as session:
        rows = session.execute(text("SELECT * FROM clubs")).mappings().all()
        recs = []
        for r in rows:
            if interest and interest.lower() not in (r["name"] + " " + (r["description"] or "")).lower():
                continue
            recs.append(ClubRecommendation(
                club_id=r["id"], name=r["name"], category=r["category"],
                reason=f"Matches your stated interest in {interest}." if interest else "Popular club on campus.",
            ))
        if not recs:
            recs = [
                ClubRecommendation(club_id=r["id"], name=r["name"], category=r["category"], reason="Popular club on campus.")
                for r in rows
            ]
        return ClubRecommendations(student_id=student_id, recommendations=recs)
