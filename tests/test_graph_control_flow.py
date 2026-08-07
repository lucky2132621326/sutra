"""
Graph control-flow guarantees that are easy to regress and expensive to
discover live: the node/edge topology, the two independent replan caps, and
the demo conflict scenario's underlying data.
"""
import os

import pytest

os.environ.setdefault("MOCK_LLM", "1")

from apps.api.graph import nodes  # noqa: E402
from apps.api.graph.build import build_graph  # noqa: E402
from apps.api.tools import academic, events, placement  # noqa: E402
from packages.contracts.plan import Plan, Step  # noqa: E402

ANANYA = "1602-23-733-042"


# --- Topology: approval AFTER critic, single dispatch funnel ---

def test_graph_topology_matches_spec():
    g = build_graph(checkpointer=None).get_graph()
    node_names = set(g.nodes.keys())
    for expected in ("intake", "planner", "dispatch", "conflict_check", "critic",
                      "approval_gate", "synthesize", "memory_write"):
        assert expected in node_names, f"missing node {expected}"
    for agent in ("academic", "placement", "events", "knowledge", "services"):
        assert f"agent_{agent}" in node_names

    edges = {(e.source, e.target) for e in g.edges}
    assert ("__start__", "intake") in edges
    assert ("intake", "planner") in edges
    assert ("planner", "dispatch") in edges
    # Every specialist funnels back through the single dispatch node — this is
    # what stops N agents finishing together from each re-dispatching the same
    # next step N times.
    for agent in ("academic", "placement", "events", "knowledge", "services"):
        assert (f"agent_{agent}", "dispatch") in edges
    # Approval gate sits AFTER critic, before synthesize.
    assert ("critic", "approval_gate") in edges
    assert ("approval_gate", "synthesize") in edges
    assert ("synthesize", "memory_write") in edges


def test_dispatch_is_the_only_conditional_router_into_agents():
    g = build_graph(checkpointer=None).get_graph()
    into_agents = [(s, t) for (s, t) in {(e.source, e.target) for e in g.edges}
                   if t.startswith("agent_")]
    sources = {s for s, _ in into_agents}
    assert sources <= {"dispatch"}, f"agents reachable from unexpected nodes: {sources}"


# --- The two independent replan caps ---

def test_replan_caps_are_separate_and_rejection_cap_is_tighter():
    assert nodes.MAX_REPLAN_ITERATIONS == 2
    assert nodes.MAX_REJECTION_REPLANS == 1
    assert nodes.MAX_REJECTION_REPLANS < nodes.MAX_REPLAN_ITERATIONS


@pytest.mark.asyncio
async def test_rejected_step_stops_replanning_once_its_budget_is_spent():
    """A repeatedly-rejected action must NOT loop forever. Once
    rejection_replans hits the cap the critic proceeds instead of replanning,
    even though a rejected step is still present."""
    state = {
        "run_id": "test-reject-cap",
        "plan": Plan(goal="send an email", steps=[
            Step(id="s1", agent="services", task="send email", requires_approval=True)]),
        "step_results": {"s1": {"step_id": "s1", "agent": "services", "output": "Rejected by user",
                                 "reasoning": "", "data": {}, "status": "rejected"}},
        "iteration": 0,
        "rejection_replans": nodes.MAX_REJECTION_REPLANS,  # budget already spent
    }
    result = await nodes.critic_node(state)
    assert result["critic_feedback"] is None, "should stop replanning, not loop"
    assert nodes.route_after_critic({**state, **result}) == "approval_gate"


@pytest.mark.asyncio
async def test_rejection_increments_its_own_counter_on_first_replan():
    """With budget remaining and an unsatisfied critic, the rejection counter
    advances — so the loop is bounded rather than open-ended."""
    import apps.api.graph.nodes as n

    async def fake_call(*_a, **_kw):
        return {"satisfied": False, "feedback": "try another way"}

    original = n.asyncio.to_thread
    n.asyncio.to_thread = lambda fn, *a, **kw: fake_call()
    try:
        state = {
            "run_id": "test-reject-inc",
            "plan": Plan(goal="g", steps=[Step(id="s1", agent="services", task="t")]),
            "step_results": {"s1": {"step_id": "s1", "agent": "services", "output": "Rejected by user",
                                     "reasoning": "", "data": {}, "status": "rejected"}},
            "iteration": 0,
            "rejection_replans": 0,
        }
        result = await n.critic_node(state)
        assert result["critic_feedback"] == "try another way"
        assert result["rejection_replans"] == 1
    finally:
        n.asyncio.to_thread = original


# --- Reset-capable reducers (a replan must genuinely discard prior state) ---

def test_step_results_reducer_can_actually_clear():
    """Merge reducers only grow, so returning {} from the planner was a silent
    no-op: every old step id stayed in `done`, route_ready_steps saw the plan
    as already complete, and a replan re-planned without re-executing."""
    from apps.api.graph.state import RESET, _merge_dicts

    existing = {"s1": {"status": "ok"}, "s2": {"status": "ok"}}
    assert _merge_dicts(existing, {}) == existing, "empty update must not clear (documents the trap)"
    assert _merge_dicts(existing, {RESET: True}) == {}
    assert _merge_dicts(existing, {RESET: True, "s1": {"status": "pending"}}) == {"s1": {"status": "pending"}}


def test_pending_approvals_reducer_can_actually_clear():
    """Approvals queued by a plan the arbiter vetoed must not survive into the
    gate — otherwise the system executes an action it just refused."""
    from apps.api.graph.state import RESET, _append_or_reset

    existing = [{"id": "a1"}, {"id": "a2"}]
    assert _append_or_reset(existing, []) == existing
    assert _append_or_reset(existing, [{"id": "a3"}]) == existing + [{"id": "a3"}]
    assert _append_or_reset(existing, [RESET]) == []
    assert _append_or_reset(existing, [RESET, {"id": "a9"}]) == [{"id": "a9"}]


@pytest.mark.asyncio
async def test_planner_resets_both_channels_on_revision():
    state = {
        "run_id": "t", "goal": "g", "critic_feedback": "conflicts were found",
        "iteration": 0, "step_results": {"s1": {"status": "ok"}},
        "pending_approvals": [{"id": "stale"}],
    }
    result = await nodes.planner_node(state)
    from apps.api.graph.state import RESET

    assert result["step_results"] == {RESET: True}
    assert result["pending_approvals"] == [RESET]


# --- The exact demo scenario's ground truth ---

def test_demo_scenario_ananya_passes_google():
    elig = placement.check_placement_eligibility(ANANYA, "google")
    assert elig.is_eligible is True


def test_demo_scenario_ananya_fails_goldman_by_exactly_0_1_cgpa():
    elig = placement.check_placement_eligibility(ANANYA, "goldman")
    assert elig.is_eligible is False
    cgpa = next(c for c in elig.breakdown if c.criterion == "CGPA")
    assert cgpa.passed is False
    assert float(cgpa.required.replace(">= ", "")) - float(cgpa.actual) == pytest.approx(0.1, abs=1e-9)


def test_demo_scenario_thursday_workshop_collides_with_dbms_lab():
    thu = next(e for e in events.search_events(query="placement workshop").events
               if e.id == "evt_workshop_thu")
    conflict = academic.check_schedule_conflict(ANANYA, thu.day_of_week, thu.start_time, thu.end_time)
    assert conflict.has_conflict is True
    assert conflict.conflicting_course_id == "CS301L"
    assert "lab" in conflict.conflicting_session.lower()


def test_demo_scenario_dbms_attendance_below_75_bar():
    elig = academic.compute_attendance_eligibility(ANANYA, "CS301L")
    assert elig.is_eligible is False
    assert elig.current_pct < 75.0
    assert elig.condonation_possible is True  # 65% <= pct < 75%


def test_demo_scenario_saturday_batch_is_the_viable_alternative():
    """Saturday must NOT collide, and must have seats — otherwise the
    arbitration has nowhere to route the student."""
    sat = next(e for e in events.search_events(query="placement workshop").events
               if e.id == "evt_workshop_sat")
    conflict = academic.check_schedule_conflict(ANANYA, sat.day_of_week, sat.start_time, sat.end_time)
    assert conflict.has_conflict is False, "Saturday batch must be free of collisions"
    assert sat.seats_remaining == 2, f"expected 2 seats left, got {sat.seats_remaining}"
