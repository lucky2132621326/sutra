"""
Human-in-the-loop must ACTUALLY gate.

Regression tests for a governance bug found in a recorded demo run: a step
whose dependency was sitting at `pending_approval` was dispatched anyway,
because route_ready_steps treated "present in step_results" as "satisfied".
The calendar entry was written seconds BEFORE the approval was requested, and
would have survived a rejection.
"""
import json
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("MOCK_LLM", "1")

from langgraph.types import Command  # noqa: E402

from apps.api.graph.build import graph_session  # noqa: E402
from apps.api.graph.nodes import (  # noqa: E402
    BLOCKING_STATUSES, SATISFYING_STATUSES, route_after_approval, route_ready_steps,
)
from apps.api.tools import events as events_tool  # noqa: E402
from packages.contracts.plan import Plan, Step  # noqa: E402

ANANYA = "1602-23-733-042"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


# --- Unit level: the routing predicate itself ---

def test_pending_approval_does_not_satisfy_dependencies():
    assert "pending_approval" not in SATISFYING_STATUSES
    assert "rejected" not in SATISFYING_STATUSES
    assert "permission_denied" not in SATISFYING_STATUSES
    assert {"ok", "degraded"} <= SATISFYING_STATUSES


def test_route_does_not_dispatch_a_step_blocked_on_approval():
    state = {
        "plan": Plan(goal="g", steps=[
            Step(id="s1", agent="events", task="register", requires_approval=True),
            Step(id="s2", agent="services", task="calendar", depends_on=["s1"]),
        ]),
        "step_results": {"s1": {"step_id": "s1", "status": "pending_approval",
                                 "agent": "events", "output": "", "reasoning": "", "data": {}}},
    }
    # s1 already attempted, s2 blocked -> nothing ready, fall through.
    assert route_ready_steps(state) == "conflict_check"


def test_route_dispatches_the_dependent_once_approval_succeeded():
    state = {
        "plan": Plan(goal="g", steps=[
            Step(id="s1", agent="events", task="register", requires_approval=True),
            Step(id="s2", agent="services", task="calendar", depends_on=["s1"]),
        ]),
        "step_results": {"s1": {"step_id": "s1", "status": "ok",
                                 "agent": "events", "output": "", "reasoning": "", "data": {}}},
    }
    sends = route_ready_steps(state)
    assert isinstance(sends, list) and len(sends) == 1
    assert sends[0].node == "agent_services"


def test_after_approval_returns_to_dispatch_when_work_remains():
    state = {
        "plan": Plan(goal="g", steps=[
            Step(id="s1", agent="events", task="register", requires_approval=True),
            Step(id="s2", agent="services", task="calendar", depends_on=["s1"]),
        ]),
        "step_results": {"s1": {"step_id": "s1", "status": "ok",
                                 "agent": "events", "output": "", "reasoning": "", "data": {}}},
    }
    assert route_after_approval(state) == "dispatch"


def test_after_approval_goes_to_synthesize_when_nothing_runnable():
    state = {
        "plan": Plan(goal="g", steps=[Step(id="s1", agent="events", task="register")]),
        "step_results": {"s1": {"step_id": "s1", "status": "ok",
                                 "agent": "events", "output": "", "reasoning": "", "data": {}}},
    }
    assert route_after_approval(state) == "synthesize"


def test_rejected_blocks_dependents():
    assert BLOCKING_STATUSES == {"rejected", "permission_denied"}


# --- End to end through the real graph ---

async def _run(decision: str) -> list[dict]:
    run_id = f"test-gate-{decision}-{uuid.uuid4().hex[:6]}"
    async with graph_session() as graph:
        config = {"configurable": {"thread_id": run_id}}
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "role": "student",
             "goal": "register me for the placement workshop", "iteration": 0},
            config=config,
        )
        hops = 0
        while "__interrupt__" in result and hops < 8:
            result = await graph.ainvoke(Command(resume={"decision": decision}), config=config)
            hops += 1
    path = FIXTURES / f"{run_id}.jsonl"
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    path.unlink(missing_ok=True)
    return events


@pytest.mark.asyncio
async def test_dependent_write_happens_only_after_approval():
    """THE regression. add_to_calendar depends on the gated registration, so it
    must not be called until an approval has resolved."""
    events = await _run("approve")

    first_approval = next(
        (i for i, e in enumerate(events) if e["type"] == "approval.resolved"), None)
    calendar = next(
        (i for i, e in enumerate(events)
         if e["type"] == "tool.called" and e["payload"].get("tool") == "add_to_calendar"), None)

    assert first_approval is not None, "no approval was ever resolved"
    assert calendar is not None, "the calendar step never ran"
    assert calendar > first_approval, (
        "add_to_calendar executed BEFORE any approval resolved — the gate is not gating"
    )


@pytest.mark.asyncio
async def test_rejection_writes_nothing_downstream():
    """Rejecting the registration must leave no registration AND no calendar
    entry — the dependent write is cancelled, not merely skipped."""
    before = events_tool.get_event_capacity("evt_workshop_thu").seats_taken
    events = await _run("reject")
    after = events_tool.get_event_capacity("evt_workshop_thu").seats_taken

    assert after == before, "a seat was consumed despite the human rejecting"

    calendar_calls = [e for e in events
                      if e["type"] == "tool.called" and e["payload"].get("tool") == "add_to_calendar"]
    assert not calendar_calls, "calendar was written after the registration was rejected"

    resolved = [e for e in events if e["type"] == "approval.resolved"]
    assert resolved, "no approval.resolved emitted"
    assert all(e["payload"]["decision"] == "reject" for e in resolved)
    assert all(e["payload"]["outcome"] == "not_executed" for e in resolved)


@pytest.mark.asyncio
async def test_approved_execution_emits_the_real_result_with_a_receipt():
    """The tool.result issued before approval says pending_approval and carries
    no receipt. Only the post-approval one may claim success."""
    events = await _run("approve")

    approved_results = [
        e for e in events
        if e["type"] == "tool.result" and e["payload"].get("approval_id")
        and e["payload"].get("status") == "ok"
    ]
    assert approved_results, "no post-approval tool.result carrying the real outcome"
    data = approved_results[0]["payload"]["data"]
    assert data.get("receipt_id"), "approved execution reported success without a receipt"


@pytest.mark.asyncio
async def test_tool_result_carries_structured_data():
    """Evidence cards need the actual structured result, not just a status."""
    events = await _run("approve")
    eligibility = next(
        (e for e in events if e["type"] == "tool.result"
         and e["payload"].get("tool") == "check_placement_eligibility"), None)
    assert eligibility is not None
    data = eligibility["payload"].get("data")
    assert data, "tool.result carried no data"
    assert "is_eligible" in data and "breakdown" in data
    assert all({"criterion", "required", "actual", "passed"} <= set(c) for c in data["breakdown"])


# --- Idempotency ---

def test_registering_twice_does_not_consume_two_seats():
    before = events_tool.get_event_capacity("evt_cyber_ws")
    first = events_tool.register_event(ANANYA, "evt_cyber_ws", actor=ANANYA, approved=True)
    mid = events_tool.get_event_capacity("evt_cyber_ws")
    second = events_tool.register_event(ANANYA, "evt_cyber_ws", actor=ANANYA, approved=True)
    after = events_tool.get_event_capacity("evt_cyber_ws")

    assert mid.seats_taken == before.seats_taken + 1
    assert after.seats_taken == mid.seats_taken, "a retry consumed a second seat"
    assert first.status == "registered"
    assert second.status == "already_registered"
