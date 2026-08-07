"""
Guards the demo-latency work: how many LLM round-trips one run actually
costs. Local 7B models were measured at 90-130s PER NODE, so every avoided
call is real stage time.
"""
import os
import uuid

import pytest

os.environ.setdefault("MOCK_LLM", "1")

from apps.api.graph.agents import _can_skip_compose, _describe_tool_result  # noqa: E402
from apps.api.llm import router  # noqa: E402


# --- The deterministic summariser must copy values, never paraphrase them ---
#
# These assert PROPERTIES, not phrasing. The wording is allowed to change (it
# was rewritten into second person, because the student reads these strings and
# "The student meets the criteria" is a sentence about them rather than to
# them); what must never change is that the figures are exact, the verdict is
# unambiguous, and a degraded result says so.

def test_eligibility_summary_states_verdict_and_unmet_criteria():
    data = {
        "is_eligible": False, "company_id": "goldman",
        "breakdown": [
            {"criterion": "CGPA", "required": ">= 8.5", "actual": "8.4", "passed": False},
            {"criterion": "Backlogs", "required": "<= 0", "actual": "0", "passed": True},
        ],
    }
    text = _describe_tool_result("check_placement_eligibility", data, "ok")
    assert "not eligible" in text.lower()      # the verdict, unambiguously
    assert "8.5" in text and "8.4" in text     # exact figures preserved
    assert "Backlogs" not in text              # only the failing criterion is called out


def test_eligible_summary_says_so_without_hedging():
    data = {
        "is_eligible": True, "company_id": "google",
        "breakdown": [{"criterion": "CGPA", "required": ">= 8.0", "actual": "8.4", "passed": True}],
    }
    text = _describe_tool_result("check_placement_eligibility", data, "ok")
    assert "eligible" in text.lower() and "not eligible" not in text.lower()
    assert "8.4" in text
    assert "Google" in text, "company slug should be humanised, not printed raw"


def test_attendance_summary_preserves_exact_numbers():
    data = {"course_id": "CS301L", "current_pct": 70.3, "classes_attended": 26,
            "classes_held": 37, "is_eligible": False, "classes_needed_for_75": 19}
    text = _describe_tool_result("compute_attendance_eligibility", data, "ok")
    assert "70.3%" in text
    assert "26" in text and "37" in text and "19" in text
    assert "CS301L" in text


def test_degraded_result_is_flagged_not_dressed_up():
    """A cached answer must be identifiable as cached. The exact word may
    change, but the user has to be able to tell this was not live, and the
    underlying reason must survive verbatim."""
    data = {"degraded": True, "degradation_reason": "Placement service unavailable"}
    text = _describe_tool_result("check_placement_eligibility", data, "degraded")
    assert any(w in text.lower() for w in ("degraded", "cache", "cached", "couldn't reach")), (
        f"nothing in {text!r} signals this was not live data"
    )
    assert "Placement service unavailable" in text


def test_knowledge_agent_still_uses_the_llm():
    """RAG answers are genuine language work over retrieved prose — those must
    NOT be shortcut."""
    assert _can_skip_compose("knowledge", {"citations": []}, "ok") is False
    assert _can_skip_compose("placement", {"is_eligible": True}, "ok") is True


def test_no_tool_result_still_uses_the_llm():
    assert _can_skip_compose("placement", {}, "ok") is False


def test_errored_tool_still_uses_the_llm():
    assert _can_skip_compose("placement", {"error": "boom"}, "error") is False


# --- End-to-end call count ---

@pytest.mark.asyncio
async def test_happy_path_run_stays_under_call_budget():
    """A clean run must not exceed the budget. Before this work the same
    scenario cost ~14 calls; the compose-skip, critic-skip and
    memory-off-critical-path changes should keep it well under that."""
    from langgraph.types import Command

    from apps.api.graph.build import graph_session

    router.reset_call_count()
    run_id = "budget-" + uuid.uuid4().hex[:6]
    async with graph_session() as graph:
        config = {"configurable": {"thread_id": run_id}}
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": "1602-23-733-042", "role": "student",
             "goal": "Am I eligible for the Google internship?", "iteration": 0},
            config=config,
        )
        hops = 0
        while "__interrupt__" in result and hops < 5:
            result = await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
            hops += 1

    calls = router.CALL_COUNT["total"]
    print(f"\n  LLM calls for one full run: {calls}")
    assert result.get("final_answer"), "run did not complete"
    # Budget history: ~14 originally -> 10 after skipping the redundant compose
    # call and the clean-run critic -> 13 once approval gating became real.
    # That last +3 is the price of correctness: a step depending on a gated
    # write can no longer run eagerly, so it costs a second dispatch wave
    # (one agent call) plus another conflict_check and critic pass. At ~0.2s
    # per warm call that is ~2.6s a run, which is fine; running the calendar
    # write before the human approved it was not.
    assert calls <= 14, f"call budget exceeded: {calls} calls"
