"""
Smoke test for apps/api/graph/ — runs a real goal through planner -> parallel
dispatch -> conflict_check -> critic -> approval_gate -> synthesize, resuming
past any interrupt() pauses. Run from the repo root: python smoke_test_graph.py
"""
import asyncio
import uuid

from langgraph.types import Command

from apps.api.graph.build import graph_session


async def run_goal(graph, goal: str):
    run_id = "smoke-graph-" + uuid.uuid4().hex[:6]
    config = {"configurable": {"thread_id": run_id}}
    print(f"\n{'=' * 60}\nGOAL: {goal}\nrun_id: {run_id}\n{'=' * 60}")

    result = await graph.ainvoke({"run_id": run_id, "goal": goal, "iteration": 0}, config=config)

    hops = 0
    while "__interrupt__" in result and hops < 10:
        interrupts = result["__interrupt__"]
        print(f"\n--- paused on {len(interrupts)} interrupt(s) ---")
        for i in interrupts:
            print("  pending_action:", i.value.get("pending_action"))
        result = await graph.ainvoke(Command(resume="approve"), config=config)
        hops += 1

    print("\n--- plan ---")
    plan = result.get("plan")
    if plan:
        for s in plan.steps:
            print(f"  [{s.id}] agent={s.agent} depends_on={s.depends_on} approval={s.requires_approval}")
            print(f"      task: {s.task}")

    print("\n--- step results ---")
    for step_id, r in result.get("step_results", {}).items():
        print(f"  {step_id} ({r['agent']}, {r['status']}): {r['output'][:150]}")

    print("\n--- conflicts ---", result.get("conflicts"))
    print("--- iteration ---", result.get("iteration"))
    print("\n--- FINAL ANSWER ---")
    print(result.get("final_answer"))
    print("--- citations ---", result.get("citations"))

    assert result.get("final_answer"), "graph did not reach synthesize with an answer"
    return result


async def main():
    async with graph_session() as graph:
        await run_goal(graph, "What events are happening on campus this week, and can you register me for the AI workshop?")
    print("\nALL GRAPH SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
