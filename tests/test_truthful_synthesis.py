"""
The final answer must not claim things that did not happen.

Two specific lies were recorded into the demo fixtures before this:

  1. On a run where the human REJECTED the registration, the answer still read
     "I booked the Saturday batch instead" — because the mock synthesizer
     returned one fixed paragraph regardless of the payload.
  2. Answers carried an inline "[doc:0]" marker on runs that emitted zero
     rag.retrieved events, so the citation resolved to nothing.

Both are worse than an incomplete answer: a judge who checks either one finds
the system asserting something its own event stream contradicts.

The fix has three parts, and this module pins all three:
  - an append-only action_log the approval gate writes, which survives replans
  - "Actions taken", rendered from that log rather than written by the model
  - citation markers stripped when they cannot be resolved
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
    _render_action_log, _strip_unresolvable_citations,
)

ANANYA = "1602-23-733-042"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOAL = "Am I eligible for the Google internship? Register me for the placement workshop."


# --- Unit: the two deterministic guards ---

def test_unresolvable_citation_markers_are_removed():
    answer = "You cannot afford to miss it [doc:0]."
    assert _strip_unresolvable_citations(answer, []) == "You cannot afford to miss it."


def test_resolvable_citation_markers_are_kept():
    answer = "Attendance must be 75% [doc:0]."
    assert "[doc:0]" in _strip_unresolvable_citations(answer, ["R22 clause 4.2"])


def test_only_out_of_range_markers_are_removed():
    """One real citation resolves [doc:0]; [doc:1] points at nothing."""
    out = _strip_unresolvable_citations("A [doc:0] and B [doc:1].", ["only one"])
    assert "[doc:0]" in out
    assert "[doc:1]" not in out


def test_action_log_renders_rejections_as_not_done():
    rendered = _render_action_log([
        {"description": "Register for workshop", "outcome": "not_executed"},
    ])
    assert "NOT DONE" in rendered
    assert "nothing was written" in rendered


def test_action_log_renders_receipts_for_real_writes():
    rendered = _render_action_log([
        {"description": "Register for workshop", "outcome": "executed", "receipt_id": "abc123"},
    ])
    assert "DONE" in rendered and "abc123" in rendered
    assert "NOT DONE" not in rendered


def test_empty_action_log_adds_nothing():
    """A read-only run must not grow an empty 'Actions taken' heading."""
    assert _render_action_log([]) == ""


# --- End to end: the answer against what the run actually did ---

async def _run(decision: str) -> tuple[str, list[dict]]:
    run_id = f"test-truth-{decision}-{uuid.uuid4().hex[:6]}"
    async with graph_session() as graph:
        config = {"configurable": {"thread_id": run_id}}
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "role": "student",
             "goal": GOAL, "iteration": 0},
            config=config,
        )
        hops = 0
        while "__interrupt__" in result and hops < 8:
            result = await graph.ainvoke(Command(resume={"decision": decision}), config=config)
            hops += 1
    path = FIXTURES / f"{run_id}.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    path.unlink(missing_ok=True)
    return result.get("final_answer", ""), events


@pytest.mark.asyncio
async def test_rejected_run_never_claims_the_registration_happened():
    """THE regression: the recorded reject fixture used to say it booked anyway."""
    answer, _ = await _run("reject")

    assert "NOT DONE" in answer, "the answer does not state that nothing was written"
    for lie in ("I booked", "I registered", "I've registered", "you are registered"):
        assert lie.lower() not in answer.lower(), f"answer claims {lie!r} on a rejected run"


@pytest.mark.asyncio
async def test_rejected_run_reports_no_receipt():
    """A receipt id is proof of a write. A rejected run has nothing to prove."""
    answer, events = await _run("reject")

    receipts = [
        e["payload"]["data"]["receipt_id"]
        for e in events
        if e["type"] == "tool.result"
        and e["payload"].get("status") == "ok"
        and isinstance(e["payload"].get("data"), dict)
        and e["payload"]["data"].get("receipt_id")
    ]
    assert not receipts, f"a rejected run produced receipts: {receipts}"
    assert "receipt" not in answer.lower()


@pytest.mark.asyncio
async def test_approved_run_reports_the_receipt_the_stream_recorded():
    """The opposite failure: the answer must not under-report a real write
    either. The receipt it prints has to be one the event stream carries."""
    answer, events = await _run("approve")

    receipts = {
        e["payload"]["data"]["receipt_id"]
        for e in events
        if e["type"] == "tool.result"
        and e["payload"].get("status") == "ok"
        and isinstance(e["payload"].get("data"), dict)
        and e["payload"]["data"].get("receipt_id")
    }
    assert receipts, "an approved run wrote nothing"
    assert "DONE" in answer
    assert any(r in answer for r in receipts), (
        f"answer cites no receipt from the stream; stream had {receipts}"
    )


@pytest.mark.asyncio
async def test_answer_has_no_citation_marker_without_a_retrieval():
    """No rag.retrieved on this path, so no [doc:N] may survive into the answer."""
    answer, events = await _run("approve")

    retrieved = [e for e in events if e["type"] == "rag.retrieved"]
    if not retrieved:
        assert "[doc:" not in answer, "answer cites a document the run never retrieved"


@pytest.mark.asyncio
async def test_run_finished_carries_the_action_log():
    """The UI renders from events, not from graph state — so the ledger has to
    be on the wire, or the frontend cannot show what was actually done."""
    _, events = await _run("reject")

    finished = next(e for e in events if e["type"] == "run.finished")
    actions = finished["payload"].get("actions")
    assert actions, "run.finished carries no action log"
    assert any(a["outcome"] == "not_executed" for a in actions)


@pytest.mark.asyncio
async def test_action_log_survives_the_replan_that_resets_step_results():
    """action_log uses operator.add, deliberately unlike step_results' RESET
    reducer. If it were derived from step_results at synthesis time, the
    Thursday rejection would vanish the moment the planner revised."""
    _, events = await _run("approve")

    assert any(e["type"] == "plan.revised" and "steps" in e["payload"] for e in events), (
        "this test is meaningless without a replan on the path"
    )
    finished = next(e for e in events if e["type"] == "run.finished")
    assert finished["payload"].get("actions"), "the action log was lost across the replan"


@pytest.mark.asyncio
async def test_a_declined_action_is_never_proposed_again():
    """The critic is instructed not to retry a rejection and the replan budget
    bounds how often it could — but neither is enforcement. Within the allowed
    replan the planner can re-propose the identical registration, and being
    asked twice to approve what you just refused reads as the veto not working.

    The gate carries the earlier "no" forward by matching on what the action
    DOES (tool + args), since a replan mints a fresh approval id for the same
    work.
    """
    _, events = await _run("reject")

    # Count DISTINCT approval ids per signature. approval.requested is re-emitted
    # verbatim on every resume (the gate re-runs from the top and re-announces
    # anything not yet decided), so raw occurrences overcount. Two different ids
    # for the same signature is the real failure: that is two separate asks.
    asked: dict[tuple, set] = {}
    for e in events:
        if e["type"] != "approval.requested":
            continue
        p = e["payload"]
        asked.setdefault(_sig(p.get("tool"), p.get("args")), set()).add(p.get("id"))
    declined = [
        _sig(a.get("tool"), a.get("args"))
        for a in next(e for e in events if e["type"] == "run.finished")["payload"]["actions"]
        if a.get("outcome") == "not_executed"
    ]
    assert declined, "nothing was declined, so this test proves nothing"
    for sig in declined:
        ids = asked.get(sig, set())
        assert len(ids) <= 1, (
            f"the human was asked {len(ids)} separate times to approve {sig} "
            f"after declining it (approval ids: {sorted(ids)})"
        )


def _sig(tool, args):
    return (tool, tuple(sorted((str(k), str(v)) for k, v in (args or {}).items())))


@pytest.mark.asyncio
async def test_the_guard_itself_fires_when_the_same_action_is_re_proposed():
    """Directly exercise the carry-forward path rather than relying on the
    planner happening not to re-propose. Feeds the gate a pending action whose
    signature already appears in action_log as declined, and asserts it is
    resolved without an interrupt."""
    from apps.api.graph import nodes

    action = {"id": "fresh-id-from-a-replan", "step_id": "s2", "agent": "events",
              "tool": "register_event",
              "args": {"student_id": ANANYA, "event_id": "evt_workshop_sat"},
              "description": "Register for the Saturday workshop"}
    state = {
        "run_id": f"test-guard-{uuid.uuid4().hex[:6]}",
        "student_id": ANANYA,
        "pending_approvals": [action],
        "approval_decisions": {},
        # Same work, DIFFERENT approval id — exactly what a replan produces.
        "action_log": [{"approval_id": "old-id", "step_id": "s2", "tool": "register_event",
                        "args": {"event_id": "evt_workshop_sat", "student_id": ANANYA},
                        "outcome": "not_executed", "decision": "reject"}],
        "plan": None,
    }

    # interrupt() raises inside a graph; reaching it here means the guard missed.
    update = await nodes.approval_gate_node(state)

    assert update["approval_decisions"][action["id"]] == "reject"
    assert update["step_results"]["s2"]["status"] == "rejected"
    assert "already declined" in update["action_log"][0]["error"]


@pytest.mark.asyncio
async def test_the_guard_does_not_block_a_genuinely_different_action(monkeypatch):
    """The Saturday batch must still be approvable after Thursday was declined —
    otherwise one rejection would freeze the whole run.

    interrupt() needs a runnable context, so it is replaced with a stub that
    records the ask and answers "approve". Reaching the stub IS the assertion:
    it means the guard let this action through to the human.
    """
    from apps.api.graph import nodes

    asked = []
    monkeypatch.setattr(nodes, "interrupt",
                        lambda payload: (asked.append(payload), {"decision": "approve"})[1])

    action = {"id": "sat", "step_id": "s2", "agent": "events", "tool": "register_event",
              "args": {"student_id": ANANYA, "event_id": "evt_workshop_sat"},
              "description": "Register for the Saturday workshop"}
    state = {
        "run_id": f"test-guard2-{uuid.uuid4().hex[:6]}",
        "student_id": ANANYA,
        "pending_approvals": [action],
        "approval_decisions": {},
        # A DIFFERENT event was declined; this one has never been refused.
        "action_log": [{"approval_id": "thu", "step_id": "s2", "tool": "register_event",
                        "args": {"student_id": ANANYA, "event_id": "evt_workshop_thu"},
                        "outcome": "not_executed", "decision": "reject"}],
        "plan": None,
    }

    update = await nodes.approval_gate_node(state)

    assert len(asked) == 1, "the Saturday action never reached the human"
    assert update["approval_decisions"]["sat"] == "approve"
    assert update["action_log"][0]["outcome"] == "executed"
