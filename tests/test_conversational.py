"""
Not every message is a task.

Typing "hello" used to run the whole orchestration — five agents, a conflict
arbiter, a critic — and answer with a placement eligibility verdict nobody had
asked for. That is worse than a wasted cycle: it reads as the system not
listening, which is the one impression an assistant cannot afford.

The planner now returns no steps and a direct reply for conversational input,
and the graph routes straight to the answer.
"""
import json
import os
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("MOCK_LLM", "1")
os.environ.pop("MOCK_CONFLICT", None)

from apps.api.graph.build import graph_session  # noqa: E402
from apps.api.graph.nodes import route_after_planner  # noqa: E402

ANANYA = "1602-23-733-042"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_route_skips_the_pipeline_only_when_there_is_a_reply():
    assert route_after_planner({"conversational_reply": "hi there"}) == "synthesize"
    assert route_after_planner({}) == "dispatch"
    assert route_after_planner({"conversational_reply": None}) == "dispatch"


async def _ask(goal: str):
    run_id = f"test-conv-{uuid.uuid4().hex[:6]}"
    async with graph_session() as graph:
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "role": "student", "goal": goal, "iteration": 0},
            config={"configurable": {"thread_id": run_id}},
        )
    path = FIXTURES / f"{run_id}.jsonl"
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    path.unlink(missing_ok=True)
    return result.get("final_answer", ""), events


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting", ["hello", "hi", "hey", "good morning"])
async def test_a_greeting_gets_a_greeting(greeting):
    answer, _ = await _ask(greeting)

    assert answer, "a greeting got no reply at all"
    # THE regression: the eligibility VERDICT must not appear. Offering to
    # check attendance is fine and desirable; reporting a result is the bug.
    # So assert on concrete findings, not on capability words.
    for verdict in ("you're eligible", "you are eligible", "8.4", "70.27", "receipt", "registered"):
        assert verdict not in answer.lower(), (
            f"replying to {greeting!r} leaked {verdict!r} — the pipeline ran when it should not have"
        )


@pytest.mark.asyncio
async def test_a_greeting_runs_no_agents_at_all():
    """Cheap AND correct: no dispatch, no tools, no arbitration, no approvals."""
    _, events = await _ask("hello")

    kinds = {e["type"] for e in events}
    for forbidden in ("node.started", "tool.called", "conflict.detected", "approval.requested"):
        assert forbidden not in kinds, f"a greeting triggered {forbidden}"
    assert "run.finished" in kinds


@pytest.mark.asyncio
async def test_a_capability_question_describes_the_system():
    answer, _ = await _ask("what can you do")

    assert "attendance" in answer.lower()
    assert "approval" in answer.lower(), "it should mention that writes need approval"


@pytest.mark.asyncio
async def test_thanks_is_acknowledged_not_re_introduced():
    """A greeting and a thank-you are different moments; answering both with the
    same capabilities blurb is the tell of a keyword matcher."""
    thanks, _ = await _ask("thanks")
    hello, _ = await _ask("hello")

    assert thanks != hello
    assert "campus assistant" not in thanks.lower()


@pytest.mark.asyncio
async def test_a_real_request_still_runs_the_full_pipeline():
    """The fast path must not swallow actual work."""
    answer, events = await _ask("Am I eligible for the Google internship?")

    kinds = {e["type"] for e in events}
    assert "node.started" in kinds and "tool.called" in kinds
    assert "eligible" in answer.lower()


@pytest.mark.asyncio
async def test_the_answer_addresses_the_student_directly():
    """These strings are read BY the student, so "The student meets the
    eligibility criteria" is a sentence about them, not to them."""
    answer, _ = await _ask("Am I eligible for the Google internship?")

    assert "the student" not in answer.lower()
    assert "you're eligible" in answer.lower() or "you are eligible" in answer.lower()
    assert "Google" in answer, "company slug was not humanised"


# --- Multi-turn: state must not leak between questions on one thread ---

async def _thread(goals: list[str]) -> list[tuple[str, list[dict]]]:
    """Ask several things on ONE thread, as a real conversation does."""
    thread_id = f"test-thread-{uuid.uuid4().hex[:6]}"
    out = []
    async with graph_session() as graph:
        for goal in goals:
            run_id = f"test-mt-{uuid.uuid4().hex[:6]}"
            result = await graph.ainvoke(
                {"run_id": run_id, "student_id": ANANYA, "role": "student",
                 "goal": goal, "iteration": 0},
                config={"configurable": {"thread_id": thread_id}},
            )
            path = FIXTURES / f"{run_id}.jsonl"
            events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            path.unlink(missing_ok=True)
            out.append((result.get("final_answer", ""), events))
    return out


@pytest.mark.asyncio
async def test_a_greeting_does_not_poison_the_rest_of_the_conversation():
    """THE regression. conversational_reply is checkpointed per THREAD, so once
    "hello" set it, every later question found the stale value, skipped straight
    to synthesize, and returned the greeting again while its own step sat
    unexecuted. Not writing a key is not the same as clearing it."""
    (hello, _), (real, events) = await _thread(
        ["hello", "Am I eligible for the Google internship?"])

    assert real != hello, "the second question returned the greeting verbatim"
    kinds = {e["type"] for e in events}
    assert "node.started" in kinds, "the plan was created but never dispatched"
    assert "tool.called" in kinds, "no tool ran for a question that needs data"


@pytest.mark.asyncio
async def test_one_turn_does_not_inherit_the_previous_turn_s_receipts():
    """action_log, citations and conflicts all append. Without a per-turn reset
    a later answer claims writes it never made — the exact dishonesty the
    ledger exists to prevent."""
    turns = await _thread([
        "Am I eligible for the Google internship? Register me for the placement workshop.",
        "What is the minimum attendance required?",
    ])
    _, second_events = turns[1]

    finished = next(e for e in second_events if e["type"] == "run.finished")
    for action in finished["payload"].get("actions", []):
        assert action["outcome"] != "executed", (
            "the second turn reports a write that belongs to the first turn"
        )
