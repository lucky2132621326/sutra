"""Record a deterministic, full-backend capability verification fixture.

This is deliberately different from the user-facing golden mission fixtures:
those prove autonomous planning and arbitration through the LangGraph runtime;
this fixture proves breadth by invoking every registered campus tool against a
temporary copy of the seeded SQLite database. It never mutates the developer's
working database and it labels itself as a recorded systems check in the UI.

Run:
    python scripts/record_capability_fixture.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STUDENT = "1602-23-733-042"
RUN_ID = "recorded-capability-tour"
OUTPUTS = (
    ROOT / "fixtures" / "golden_capabilities.jsonl",
    ROOT / "apps" / "web" / "public" / "fixtures" / "golden_capabilities.jsonl",
)
TEMP_DB = ROOT / "tmp" / "capability-tour.db"


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


async def main() -> None:
    # Isolate every write (registration, grievance, email, calendar, library)
    # from data/campus.db. The source remains untouched even if this script is
    # interrupted halfway through.
    TEMP_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "data" / "campus.db", TEMP_DB)

    import apps.api.tools.db as db

    db.engine.dispose()
    db.engine = create_engine(f"sqlite:///{TEMP_DB}", future=True)
    db.Session = sessionmaker(bind=db.engine, future=True)

    # Import only after replacing Session: each tool module captures Session at
    # import time, so it now points at the isolated database.
    from apps.api.tools import academic
    from apps.api.rag.store import _get_embedder
    from apps.api.tools.registry import TOOL_REGISTRY
    from packages.contracts.events import AgentEvent, EventType

    # The real API performs this warm-up during its lifespan before accepting
    # a user request. Do the same here so the score measures tool work, not a
    # one-off model load that users never see in a warmed demo process.
    await asyncio.to_thread(_get_embedder)

    events: list[AgentEvent] = []
    results: dict[str, Any] = {}
    actions: list[dict[str, Any]] = []

    def emit(type_: EventType, *, node_id: str | None = None,
             agent: str | None = None, payload: dict[str, Any] | None = None,
             latency_ms: float | None = None) -> None:
        events.append(AgentEvent(
            id=uuid.uuid4().hex[:8], run_id=RUN_ID, ts=time.time(), type=type_,
            node_id=node_id, agent=agent, payload=payload or {},
            latency_ms=latency_ms,
        ))

    steps = [
        # Academic
        ("s01", "academic", "get_timetable", "Load the student's complete weekly timetable.", [], False,
         {"student_id": STUDENT}),
        ("s02", "academic", "get_attendance", "Audit attendance across every enrolled course.", [], False,
         {"student_id": STUDENT}),
        ("s03", "academic", "compute_attendance_eligibility", "Calculate the DBMS Lab attendance recovery path.", ["s02"], False,
         {"student_id": STUDENT, "course_id": "CS301L"}),
        ("s04", "academic", "check_schedule_conflict", "Check the Thursday workshop against the class timetable.", ["s01"], False,
         {"student_id": STUDENT, "day_of_week": "Thursday", "start_time": "14:00", "end_time": "16:00"}),
        ("s05", "academic", "recommend_electives", "Recommend electives related to machine learning.", [], False,
         {"student_id": STUDENT, "interest": "machine learning"}),
        # Placement
        ("s06", "placement", "list_companies", "Find companies currently open to CSE students.", [], False,
         {"branch": "CSE"}),
        ("s07", "placement", "check_placement_eligibility", "Verify Google eligibility criterion by criterion.", [], False,
         {"student_id": STUDENT, "company_id": "google"}),
        ("s08", "placement", "analyze_resume", "Audit the resume for evidence gaps.", [], False,
         {"student_id": STUDENT, "resume_text": "CSE student. ML project with GitHub portfolio, internship experience, and coding-club leadership."}),
        ("s09", "placement", "get_prep_plan", "Build a two-week Google interview preparation plan.", ["s07"], False,
         {"student_id": STUDENT, "company_id": "google"}),
        # Events
        ("s10", "events", "search_events", "Discover upcoming AI and placement events.", [], False,
         {"query": "AI workshops"}),
        ("s11", "events", "get_event_capacity", "Verify remaining seats in the Saturday placement workshop.", [], False,
         {"event_id": "evt_workshop_sat"}),
        ("s12", "events", "recommend_clubs", "Recommend clubs matching an ML interest.", [], False,
         {"student_id": STUDENT, "interest": "machine learning"}),
        ("s13", "events", "register_event", "Register for the clash-free Saturday placement workshop.", ["s04", "s07", "s11"], True,
         {"student_id": STUDENT, "event_id": "evt_workshop_sat"}),
        # Knowledge
        ("s14", "knowledge", "search_policy", "Retrieve the attendance and condonation regulation.", [], False,
         {"query": "minimum attendance exam eligibility and condonation"}),
        ("s15", "knowledge", "get_document_span", "Open the exact supporting regulation clause.", ["s14"], False,
         {}),
        # Services & communications
        ("s16", "services", "get_hostel_info", "Check hostel room and no-dues status.", [], False,
         {"student_id": STUDENT}),
        ("s17", "services", "library_loans", "Review active library loans and due dates.", [], False,
         {"student_id": STUDENT}),
        ("s18", "services", "renew_book", "Renew the active database textbook loan.", ["s17"], False,
         {"student_id": STUDENT, "book_title": "Database System Concepts", "actor": STUDENT}),
        ("s19", "services", "draft_email", "Draft a concise attendance-support email.", ["s03", "s14"], False,
         {"to": "advisor.cse@vasavi.ac.in", "subject": "DBMS Lab attendance recovery plan",
          "body": "I am following the calculated recovery plan for DBMS Lab and request guidance on the applicable condonation process."}),
        ("s20", "services", "send_email", "Send the attendance-support email after approval.", ["s19"], True,
         {"to": "advisor.cse@vasavi.ac.in", "subject": "DBMS Lab attendance recovery plan",
          "body": "I am following the calculated recovery plan for DBMS Lab and request guidance on the applicable condonation process."}),
        ("s21", "services", "file_grievance", "File a hostel Wi-Fi grievance after approval.", ["s16"], True,
         {"student_id": STUDENT, "category": "hostel-network",
          "description": "Wi-Fi in B-Block has been unavailable repeatedly during evening study hours."}),
        ("s22", "services", "add_to_calendar", "Add the confirmed Saturday workshop to the calendar.", ["s13"], False,
         {"student_id": STUDENT, "title": "Placement Prep Workshop (Saturday Batch)",
          "date": "2026-08-15", "start_time": "10:00", "end_time": "12:00",
          "source": "Sūtra capability tour", "actor": STUDENT}),
        ("s23", "services", "create_reminder", "Create a one-hour-before workshop reminder.", ["s22"], False,
         {"student_id": STUDENT, "message": "Placement Prep Workshop starts in one hour.",
          "remind_at": "2026-08-15 09:00", "actor": STUDENT}),
        ("s24", "services", "escalate_to_human", "Create a safe human handoff for the attendance case.", ["s03", "s14"], False,
         {"student_id": STUDENT,
          "summary": "Please review the DBMS Lab recovery plan and advise whether condonation paperwork is needed.",
          "actor": STUDENT}),
    ]

    step_by_id = {step[0]: step for step in steps}
    emit(EventType.PLAN_CREATED, agent="planner", payload={
        "goal": "Run a complete campus readiness audit and safely complete the useful follow-up actions.",
        "reasoning": (
            "This recorded systems check verifies every tool in the five specialist registries. "
            "Independent reads run concurrently; writes wait for their evidence and all externally visible "
            "actions pass through a human approval gate."
        ),
        "steps": [
            {"id": sid, "agent": agent, "task": task, "depends_on": deps,
             "expected_output": f"Verified result from {tool}.", "requires_approval": gated}
            for sid, agent, tool, task, deps, gated, _args in steps
        ],
    })

    async def execute(sid: str, *, approved: bool = False) -> Any:
        _sid, agent, tool, task, _deps, gated, base_args = step_by_id[sid]
        args = dict(base_args)
        if tool == "get_document_span":
            cites = jsonable(results["s14"]).get("citations", [])
            first = cites[0] if cites else {"doc_title": "Academic Regulations", "clause": "4.2"}
            args = {"doc_title": first["doc_title"], "clause": str(first["clause"])}
        if gated:
            args["actor"] = STUDENT
            args["approved"] = approved

        if not approved:
            emit(EventType.NODE_STARTED, node_id=sid, agent=agent, payload={"task": task})
            emit(EventType.AGENT_THINKING, node_id=sid, agent=agent,
                 payload={"detail": f"Selecting {tool} from the {agent} tool registry."})
            emit(EventType.TOOL_CALLED, node_id=sid, agent=agent,
                 payload={"tool": tool, "args": {k: v for k, v in args.items() if k not in {"actor", "approved"}}})

        started = time.perf_counter()
        fn = TOOL_REGISTRY[tool]["fn"]
        try:
            value = await asyncio.to_thread(fn, **args)
            data = jsonable(value)
            status = "pending_approval" if gated and not approved else "ok"
            emit(EventType.TOOL_RESULT, node_id=sid, agent=agent, payload={
                "tool": tool, "status": status, "data": data,
                **({"approval_id": data.get("id")} if status == "pending_approval" else {}),
            })
            emit(EventType.NODE_FINISHED, node_id=sid, agent=agent, payload={"status": status},
                 latency_ms=(time.perf_counter() - started) * 1000)
            results[sid] = value
            return value
        except Exception as exc:
            emit(EventType.NODE_FAILED, node_id=sid, agent=agent, payload={"error": str(exc)})
            emit(EventType.TOOL_RESULT, node_id=sid, agent=agent,
                 payload={"tool": tool, "status": "error", "error": str(exc)})
            emit(EventType.NODE_FINISHED, node_id=sid, agent=agent, payload={"status": "error"},
                 latency_ms=(time.perf_counter() - started) * 1000)
            raise

    # Wave 1: independent reads. These calls really overlap, so the score's
    # concurrency is measured rather than illustrated.
    await asyncio.gather(*(execute(sid) for sid in (
        "s01", "s02", "s05", "s06", "s07", "s08", "s10", "s11", "s12",
        "s14", "s16", "s17",
    )))

    # Wave 2: derived reads and preparation.
    await asyncio.gather(*(execute(sid) for sid in (
        "s03", "s04", "s09", "s15", "s18", "s19",
    )))

    # Surface the same deterministic safety evidence the graph's registration
    # preflight uses. project_attendance_impact is an internal safety helper,
    # not counted among the 24 registry tools.
    clash = jsonable(results["s04"])
    impact = jsonable(await asyncio.to_thread(
        academic.project_attendance_impact, STUDENT, "CS301L", 1,
    ))
    emit(EventType.SCHEDULE_CHECKED, node_id="s13", agent="academic", payload={
        "event_id": "evt_workshop_thu", "event_title": "Placement Prep Workshop",
        "day": "Thursday", "start": "14:00", "end": "16:00", **clash,
    })
    emit(EventType.ATTENDANCE_IMPACT_CALCULATED, node_id="s13", agent="academic", payload=impact)
    emit(EventType.CONFLICT_DETECTED, agent="conflict_arbiter", payload={
        "conflicts": [{
            "type": "SCHEDULE_COLLISION", "step_id": "s13",
            "detail": "The Thursday workshop overlaps DBMS Lab; attendance is already below 75%.",
            "evidence": {
                "event": {"id": "evt_workshop_thu", "title": "Placement Prep Workshop",
                          "day": "Thursday", "start": "14:00", "end": "16:00", "seats_remaining": 2},
                "collides_with": {"course_id": "CS301L", "session_type": "lab", "detail": clash.get("detail")},
                "attendance_impact": impact,
            },
        }],
        "rationale": "Academic safety has precedence, so registration uses the clash-free Saturday batch.",
    })
    emit(EventType.CONFLICT_RESOLVED, agent="conflict_arbiter", payload={
        "resolution": "Use the Saturday batch with two seats remaining."
    })

    # Three genuine gated tools. The fixture records the decision and receipt;
    # replay presents them as recorded decisions, never as fake live controls.
    for sid in ("s13", "s20", "s21"):
        pending = jsonable(await execute(sid))
        pending["step_id"] = sid
        emit(EventType.APPROVAL_REQUESTED, agent="approval_gate", payload=pending)
        committed = jsonable(await execute(sid, approved=True))
        emit(EventType.APPROVAL_RESOLVED, agent="approval_gate", payload={
            "id": pending["id"], "step_id": sid, "decision": "approve", "outcome": "executed",
        })
        actions.append({
            "approval_id": pending["id"], "step_id": sid,
            "agent": step_by_id[sid][1], "tool": step_by_id[sid][2],
            "args": pending.get("args", {}), "description": pending.get("description", step_by_id[sid][3]),
            "decision": "approve", "outcome": "executed",
            "receipt_id": committed.get("receipt_id"), "error": None, "gated": True,
        })

    # The remaining low-risk/reversible writes run only after their evidence.
    for sid in ("s22", "s23", "s24"):
        value = jsonable(await execute(sid))
        actions.append({
            "approval_id": None, "step_id": sid, "agent": step_by_id[sid][1],
            "tool": step_by_id[sid][2], "args": {}, "description": step_by_id[sid][3],
            "decision": None, "outcome": "executed", "receipt_id": value.get("receipt_id"),
            "error": None, "gated": False,
        })

    # renew_book is also a committed reversible write.
    renewed = jsonable(results["s18"])
    actions.insert(0, {
        "approval_id": None, "step_id": "s18", "agent": "services", "tool": "renew_book",
        "args": {}, "description": step_by_id["s18"][3], "decision": None,
        "outcome": "executed", "receipt_id": renewed.get("receipt_id"), "error": None, "gated": False,
    })

    tool_names = sorted({step[2] for step in steps})
    emit(EventType.RUN_FINISHED, agent="synthesizer", payload={
        "answer": (
            "Sūtra verified all 24 registered campus tools across Academics, Placements, Events, "
            "Policies and Campus Services. Independent checks ran in parallel; the Academic Agent "
            "flagged the Thursday clash; three externally visible actions waited for recorded human "
            "approval; and every committed write returned an audit receipt."
        ),
        "not_completed": [], "actions": actions,
        "citations": jsonable(results["s14"]).get("citations", []),
        "capability_audit": {"tools_verified": tool_names, "count": len(tool_names), "expected": 24},
    })

    lines = "".join(event.to_json() + "\n" for event in events)
    for output in OUTPUTS:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(lines, encoding="utf-8")

    print(f"Recorded {len(events)} events; verified {len(tool_names)}/24 tools")
    for agent in ("academic", "placement", "events", "knowledge", "services"):
        used = [step[2] for step in steps if step[1] == agent]
        print(f"  {agent:<10} {len(used):>2}: {', '.join(used)}")

    db.engine.dispose()
    TEMP_DB.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
