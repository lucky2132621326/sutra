"""
The hero demo path must produce its conflict from real data, not a flag.

For a while the Thursday-workshop clash only appeared when MOCK_CONFLICT=1
told the mock arbiter to invent one. That makes the centrepiece of the demo a
scripted animation: nothing was actually checked, so nothing could actually be
wrong. A judge asking "what if the timetable were different?" would get no
answer.

These tests pin the honest version: conflict_check_node runs a deterministic
preflight (search_events -> check_schedule_conflict -> project_attendance_impact)
over every pending gated registration, so the collision is discovered from
campus.db. MOCK_CONFLICT is explicitly cleared in every test here.
"""
import json
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("MOCK_LLM", "1")
os.environ.pop("MOCK_CONFLICT", None)  # the entire point of this module

from langgraph.types import Command  # noqa: E402

from apps.api.graph.build import graph_session  # noqa: E402
from apps.api.tools import academic  # noqa: E402

ANANYA = "1602-23-733-042"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def _no_mock_conflict(monkeypatch):
    monkeypatch.delenv("MOCK_CONFLICT", raising=False)


# --- The evidence, at the tool level ---

def test_thursday_workshop_really_does_collide_with_the_dbms_lab():
    """The clash is a fact about the seeded timetable, not a narrative device."""
    conflict = academic.check_schedule_conflict(ANANYA, "Thursday", "14:00", "16:00")
    assert conflict.has_conflict
    assert conflict.conflicting_course_id == "CS301L"


def test_saturday_batch_is_actually_free():
    """The replan target must be genuinely safe, or the fix is theatre too."""
    conflict = academic.check_schedule_conflict(ANANYA, "Saturday", "10:00", "12:00")
    assert not conflict.has_conflict


def test_attendance_impact_is_computed_not_asserted():
    impact = academic.project_attendance_impact(ANANYA, "CS301L", sessions_missed=1)
    assert impact.current_pct == pytest.approx(70.27, abs=0.01)
    assert impact.projected_pct == pytest.approx(68.42, abs=0.01)
    assert impact.delta_pct < 0
    assert impact.already_below, "70.3% is under the 75% bar before the clash"


# --- End to end through the real graph, with no MOCK_CONFLICT ---

async def _hero_events() -> list[dict]:
    run_id = f"test-hero-{uuid.uuid4().hex[:6]}"
    async with graph_session() as graph:
        config = {"configurable": {"thread_id": run_id}}
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "role": "student",
             "goal": "Register me for the placement workshop", "iteration": 0},
            config=config,
        )
        hops = 0
        while "__interrupt__" in result and hops < 8:
            result = await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
            hops += 1
    path = FIXTURES / f"{run_id}.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    path.unlink(missing_ok=True)
    return events


def _first(events, event_type) -> int | None:
    return next((i for i, e in enumerate(events) if e["type"] == event_type), None)


@pytest.mark.asyncio
async def test_conflict_fires_without_mock_conflict():
    events = await _hero_events()
    assert os.environ.get("MOCK_CONFLICT") is None

    detected = [e for e in events if e["type"] == "conflict.detected"]
    assert detected, "no conflict was detected on the hero path"
    assert detected[0]["payload"]["conflicts"][0]["type"] == "SCHEDULE_COLLISION"


@pytest.mark.asyncio
async def test_conflict_carries_checkable_evidence():
    """A judge must be able to verify the arbitration, not just read it."""
    events = await _hero_events()

    conflict = next(e for e in events if e["type"] == "conflict.detected")
    evidence = conflict["payload"]["conflicts"][0]["evidence"]
    assert evidence["collides_with"]["course_id"] == "CS301L"
    assert evidence["attendance_impact"]["current_pct"] == pytest.approx(70.27, abs=0.01)
    assert evidence["attendance_impact"]["projected_pct"] == pytest.approx(68.42, abs=0.01)

    # and the raw checks were shown on the wire before the verdict
    assert _first(events, "schedule.checked") < _first(events, "conflict.detected")
    assert _first(events, "attendance.impact.calculated") < _first(events, "conflict.detected")


@pytest.mark.asyncio
async def test_approval_is_requested_only_after_the_safe_alternative_is_chosen():
    """The human is asked to approve Saturday. They are never asked to approve
    the clashing Thursday slot — the system resolves that on its own first."""
    events = await _hero_events()

    replan = _first(events, "plan.revised")
    approval = _first(events, "approval.requested")
    assert replan is not None and approval is not None
    assert approval > replan, "approval was requested before the plan was revised"

    for e in events:
        if e["type"] == "approval.requested":
            assert e["payload"]["args"].get("event_id") == "evt_workshop_sat", (
                "the human was asked to approve the clashing slot"
            )


@pytest.mark.asyncio
async def test_thursday_is_proposed_but_never_written():
    """Proposing Thursday is correct and is the visible reason for the replan.
    Executing it is not. `pending_approval` results are stages, not writes."""
    events = await _hero_events()

    executed = [
        e for e in events
        if e["type"] == "tool.result"
        and e["payload"].get("tool") == "register_event"
        and e["payload"].get("status") == "ok"
    ]
    assert len(executed) == 1, f"expected exactly one real registration, got {len(executed)}"
    assert executed[0]["payload"]["data"]["event_id"] == "evt_workshop_sat"

    thursday_writes = [
        e for e in events
        if e["type"] == "tool.result"
        and e["payload"].get("status") == "ok"
        and "evt_workshop_thu" in json.dumps(e["payload"])
    ]
    assert not thursday_writes, "the clashing Thursday slot was actually written"


@pytest.mark.asyncio
async def test_replan_happens_exactly_once():
    """The cap matters: an uncapped critic loop re-planned 8 times and buried
    the demo in duplicate plan events."""
    events = await _hero_events()

    plans = [e for e in events if e["type"] == "plan.revised" and "steps" in e["payload"]]
    assert len(plans) == 1, f"expected 1 replan, got {len(plans)}"
