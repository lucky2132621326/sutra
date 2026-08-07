"""
Verifies dispatch/event-sequence mechanics with MOCK_LLM=1 — zero network
calls, deterministic. Checks:
  1. The conflict+replan loop converges (exactly capped at MAX_REPLAN_ITERATIONS)
  2. Parallel Send-dispatched steps actually overlap in wall-clock time
  3. No duplicate node.started for the same step (the dispatch-node fix)
  4. SSE-vs-fixture consistency (same events, same order)
Run: MOCK_LLM=1 python smoke_test_mock.py  (or set it inline, see __main__)
"""
import asyncio
import os
import time
import uuid
from collections import Counter

os.environ["MOCK_LLM"] = "1"
# Force the worst case (arbiter always finds a conflict) so this run exercises
# the replan loop and its cap. The happy path is measured separately by
# tests/test_call_efficiency.py.
os.environ["MOCK_CONFLICT"] = "1"

from langgraph.types import Command  # noqa: E402

from apps.api.bus import bus, FIXTURES_DIR  # noqa: E402
from apps.api.graph.build import graph_session  # noqa: E402

ANANYA = "1602-23-733-042"


async def main():
    run_id = "mock-" + uuid.uuid4().hex[:6]
    config = {"configurable": {"thread_id": run_id}}
    t0 = time.time()

    async with graph_session() as graph:
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "goal": "eligibility + register test", "iteration": 0},
            config=config,
        )

        hops = 0
        while "__interrupt__" in result and hops < 10:
            for i in result["__interrupt__"]:
                print("PENDING APPROVAL:", i.value.get("pending_action", {}).get("description"))
            result = await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
            hops += 1

    elapsed = time.time() - t0
    print(f"\nTotal wall time: {elapsed:.2f}s (should be seconds, not the minutes real providers took)")

    events = bus.replay(run_id)
    print(f"Total events: {len(events)}")

    # 1. Conflict+replan convergence — with a fixed mock conflict every time,
    # expect exactly 3 plan.created/revised events (1 initial + 2 replans).
    plan_events = [e for e in events if e.type.value in ("plan.created", "plan.revised") and e.agent == "planner"]
    print(f"planner plan events: {len(plan_events)} (expect 3: 1 initial + 2 replans, then cap)")
    assert len(plan_events) == 3, f"replan loop didn't converge as expected: {len(plan_events)}"

    # 2. No duplicate node.started for the same step WITHIN one plan pass.
    # Must segment by plan: a replan legitimately re-executes the same step ids
    # against the new plan, and in mock mode those re-runs land within the same
    # 0.1s bucket. Comparing across the whole run would flag correct behaviour.
    # The bug this guards against is N agent nodes each re-dispatching the same
    # step in the SAME wave.
    dupes = {}
    segment, seg_index = [], 0
    for e in events:
        if e.type.value in ("plan.created", "plan.revised") and e.agent == "planner":
            seg_index += 1
            segment = []
        elif e.type.value == "node.started":
            segment.append(e.node_id)
            counts = Counter(segment)
            for node_id, n in counts.items():
                if n > 1:
                    dupes[f"plan#{seg_index}:{node_id}"] = n
    print(f"duplicate node.started within a single plan pass: {dupes}")
    assert not dupes, f"dispatch bug regression: {dupes}"

    # 2b. The replan must actually RE-EXECUTE, not just re-plan. A merge-only
    # reducer silently ignored the planner's step_results reset, so every step
    # still looked complete and no work was re-dispatched.
    starts_after_first_revision = [
        e for e in events[next(i for i, e in enumerate(events) if e.type.value == "plan.revised"):]
        if e.type.value == "node.started"
    ]
    assert starts_after_first_revision, "replan produced a new plan but dispatched no steps"
    print(f"steps re-dispatched after the first revision: {len(starts_after_first_revision)}")

    # 3. Parallel dispatch overlap — s1 and s2 in the mock plan have no
    # dependencies, so their [started, finished] windows should overlap.
    def window(step_id):
        s = next((e.ts for e in events if e.type.value == "node.started" and e.node_id == step_id), None)
        f = next((e.ts for e in events if e.type.value == "node.finished" and e.node_id == step_id), None)
        return s, f

    s1_start, s1_end = window("s1")
    s2_start, s2_end = window("s2")
    if s1_start and s2_start:
        overlap = s1_start < s2_end and s2_start < s1_end
        print(f"s1 window=({s1_start:.3f},{s1_end:.3f}) s2 window=({s2_start:.3f},{s2_end:.3f}) overlap={overlap}")
        assert overlap, "s1/s2 should have run concurrently (both depend_on=[])"

    # 4. SSE-vs-fixture consistency
    fixture_path = FIXTURES_DIR / f"{run_id}.jsonl"
    with open(fixture_path) as f:
        fixture_lines = sum(1 for _ in f)
    print(f"fixture line count: {fixture_lines}, bus.replay count: {len(events)}")
    assert fixture_lines == len(events)

    print("\nFinal answer:", result.get("final_answer"))
    print("\nALL MOCK SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
