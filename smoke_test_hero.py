"""
The hero demo scenario end-to-end: Ananya asks about Google eligibility +
workshop registration. Confirms the graph uses REAL tools (not hallucinated
output) and, ideally, that the schedule collision conflict fires.
Run: python smoke_test_hero.py
"""
import asyncio
import uuid

from langgraph.types import Command

from apps.api.graph.build import graph_session

ANANYA = "1602-23-733-042"


async def main():
    run_id = "hero-" + uuid.uuid4().hex[:6]
    config = {"configurable": {"thread_id": run_id}}
    goal = ("I'm a third-year CSE student. Am I eligible for the Google internship? "
            "If yes, register me for the placement workshop, add it to my calendar, "
            "and remind me an hour before.")

    async with graph_session() as graph:
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "goal": goal, "iteration": 0}, config=config,
        )

        hops = 0
        while "__interrupt__" in result and hops < 10:
            for i in result["__interrupt__"]:
                print("PENDING APPROVAL:", i.value.get("pending_action"))
            result = await graph.ainvoke(Command(resume="approve"), config=config)
            hops += 1

        print("\n--- plan ---")
        for s in result.get("plan").steps:
            print(f"  [{s.id}] {s.agent}: {s.task} (approval={s.requires_approval}, depends_on={s.depends_on})")

        print("\n--- step results ---")
        for step_id, r in result.get("step_results", {}).items():
            print(f"  {step_id} ({r['agent']}, {r['status']}): {r['output'][:200]}")
            if r.get("data", {}).get("tool_result"):
                print(f"      tool_result: {r['data']['tool_result']}")

        print("\n--- conflicts ---")
        print(result.get("conflicts"))

        print("\n--- FINAL ANSWER ---")
        print(result.get("final_answer"))


if __name__ == "__main__":
    asyncio.run(main())
