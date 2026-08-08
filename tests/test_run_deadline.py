import asyncio

import pytest

from apps.api import main
from apps.api.graph import nodes
from apps.api.graph.state import RESET
from apps.api.tools import knowledge
from apps.api.tools.models import PolicySearchResult
from packages.contracts.events import EventType
from packages.contracts.plan import Plan, Step


@pytest.mark.asyncio
async def test_graph_leg_deadline_emits_terminal_error_and_closes_stream(monkeypatch):
    """Even several individually slow steps cannot strand the composer."""

    class NeverFinishes:
        async def ainvoke(self, *_args, **_kwargs):
            await asyncio.sleep(1)

    emitted = []
    closed = []

    async def capture_emit(event):
        emitted.append(event)

    async def capture_close(run_id):
        closed.append(run_id)

    monkeypatch.setattr(main, "GRAPH_LEG_TIMEOUT_S", 0.02)
    monkeypatch.setattr(main.bus, "emit", capture_emit)
    monkeypatch.setattr(main.bus, "close_run", capture_close)

    await main._drive_graph("deadline-run", NeverFinishes(), {}, {})

    assert closed == ["deadline-run"]
    assert len(emitted) == 1
    assert emitted[0].type == EventType.RUN_ERROR
    assert "timeout" in emitted[0].payload["error"].lower()


@pytest.mark.asyncio
async def test_planner_timeout_finishes_without_dispatching_a_fallback_write(monkeypatch):
    async def timed_out(*_args, **_kwargs):
        raise TimeoutError("provider deadline")

    emitted = []

    async def capture_emit(event):
        emitted.append(event)

    monkeypatch.setattr(nodes, "call_llm_async", timed_out)
    monkeypatch.setattr(nodes.bus, "emit", capture_emit)

    result = await nodes.planner_node({
        "run_id": "planner-deadline",
        "goal": "Register me and add it to my calendar",
        "student_id": "1602-23-733-042",
        "iteration": 0,
    })

    assert result["plan"].steps == []
    assert "Nothing was changed" in result["conversational_reply"]
    assert nodes.route_after_planner(result) == "synthesize"
    assert any(event.type == EventType.RUN_ERROR for event in emitted)


@pytest.mark.asyncio
async def test_revision_timeout_uses_verified_conflict_free_event(monkeypatch):
    async def timed_out(*_args, **_kwargs):
        raise TimeoutError("provider deadline")

    emitted = []

    async def capture_emit(event):
        emitted.append(event)

    monkeypatch.setattr(nodes, "call_llm_async", timed_out)
    monkeypatch.setattr(nodes.bus, "emit", capture_emit)

    result = await nodes.planner_node({
        "run_id": "revision-deadline",
        "goal": "Check Google eligibility, register me, add it to my calendar and remind me",
        "student_id": "1602-23-733-042",
        "critic_feedback": "[SCHEDULE_COLLISION] Thursday overlaps DBMS Lab",
        "iteration": 0,
        "plan": Plan(goal="original", steps=[
            Step(id="s1", agent="placement", task="Check Google eligibility"),
            Step(id="s2", agent="events", task="Register for the workshop", depends_on=["s1"]),
            Step(id="s3", agent="services", task="Add workshop to calendar", depends_on=["s2"]),
            Step(id="s4", agent="services", task="Remind me before workshop", depends_on=["s3"]),
        ]),
        "step_results": {
            "s1": {"data": {"tool_result": {"company_id": "Google"}}},
        },
        "conflicts": [{
            "type": "SCHEDULE_COLLISION",
            "evidence": {"event": {
                "id": "evt_workshop_thu", "title": "Placement Prep Workshop",
            }},
        }],
    })

    assert "Saturday" in result["plan"].goal
    assert any("evt_workshop_sat" in step.task for step in result["plan"].steps)
    registration = next(step for step in result["plan"].steps if step.tool == "register_event")
    assert registration.tool_args["event_id"] == "evt_workshop_sat"
    assert {step.tool for step in result["plan"].steps} == {
        "check_placement_eligibility", "register_event", "add_to_calendar", "create_reminder",
    }
    assert result["conversational_reply"] is None
    assert result["pending_approvals"] == [RESET]
    assert result["conflicts"] == [RESET]
    assert any(event.type == EventType.PLAN_REVISED for event in emitted)


@pytest.mark.asyncio
async def test_known_workshop_mission_does_not_depend_on_planner_provider(monkeypatch):
    async def must_not_call_model(*_args, **_kwargs):
        raise AssertionError("known workflow should be planned from campus tools")

    async def capture_emit(_event):
        return None

    monkeypatch.setattr(nodes, "call_llm_async", must_not_call_model)
    monkeypatch.setattr(nodes.bus, "emit", capture_emit)

    result = await nodes.planner_node({
        "run_id": "known-workflow",
        "goal": ("Am I eligible for the Google internship? If yes, register me for the "
                 "placement workshop, add it to my calendar, and remind me an hour before."),
        "student_id": "1602-23-733-042",
        "iteration": 0,
    })

    assert result["plan"].steps[0].tool == "check_placement_eligibility"
    registration = next(step for step in result["plan"].steps if step.tool == "register_event")
    assert registration.tool_args["event_id"] == "evt_workshop_thu"
    assert result["conversational_reply"] is None


@pytest.mark.asyncio
async def test_preflight_resolves_event_display_title_before_checking_schedule(monkeypatch):
    emitted = []

    async def capture_emit(event):
        emitted.append(event)

    monkeypatch.setattr(nodes.bus, "emit", capture_emit)
    monkeypatch.setattr(
        knowledge, "search_policy",
        lambda query: PolicySearchResult(query=query, citations=[], no_relevant_context=True),
    )

    conflicts, _, checked = await nodes._preflight_conflicts({
        "run_id": "title-preflight",
        "student_id": "1602-23-733-042",
        "pending_approvals": [{
            "tool": "register_event", "step_id": "s4",
            "args": {"event_id": "Placement Prep Workshop"},
        }],
    })

    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "SCHEDULE_COLLISION"
    assert checked == {"s4"}
    assert conflicts[0]["evidence"]["event"]["id"] == "evt_workshop_thu"
    schedule = next(event for event in emitted if event.type == EventType.SCHEDULE_CHECKED)
    assert schedule.payload["event_id"] == "evt_workshop_thu"


@pytest.mark.asyncio
async def test_critic_routes_pending_write_to_human_instead_of_replanning(monkeypatch):
    async def must_not_call_model(*_args, **_kwargs):
        raise AssertionError("pending approval belongs to the human gate")

    async def capture_emit(_event):
        return None

    monkeypatch.setattr(nodes, "call_llm_async", must_not_call_model)
    monkeypatch.setattr(nodes.bus, "emit", capture_emit)

    result = await nodes.critic_node({
        "run_id": "pending-gate",
        "plan": Plan(goal="register", steps=[
            Step(id="s1", agent="events", task="Register", requires_approval=True),
        ]),
        "step_results": {
            "s1": {"step_id": "s1", "output": "Awaiting approval", "status": "pending_approval"},
        },
        "conflicts": [],
        "iteration": 1,
    })

    assert result == {"critic_feedback": None}
    assert nodes.route_after_critic(result) == "approval_gate"
