"""
ONE real run of the hero scenario against local Ollama only — Groq/Gemini
keys are blanked in-process for this run so it isolates local Ollama's
actual reasoning quality, not the fallback waterfall (which would also work,
just slower/noisier from exhausted-quota attempts first).
Run: python smoke_test_ollama_local.py
"""
import asyncio
import os
import time
import uuid

os.environ["GROQ_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ.pop("MOCK_LLM", None)

from langgraph.types import Command  # noqa: E402

from apps.api.graph.build import graph_session  # noqa: E402

ANANYA = "1602-23-733-042"


async def main():
    run_id = "ollama-local-" + uuid.uuid4().hex[:6]
    config = {"configurable": {"thread_id": run_id}}
    goal = ("I'm a third-year CSE student. Am I eligible for the Google internship? "
            "If yes, register me for the placement workshop, add it to my calendar, "
            "and remind me an hour before.")
    t0 = time.time()

    async with graph_session() as graph:
        result = await graph.ainvoke(
            {"run_id": run_id, "student_id": ANANYA, "goal": goal, "iteration": 0}, config=config,
        )

        hops = 0
        while "__interrupt__" in result and hops < 10:
            for i in result["__interrupt__"]:
                print("PENDING APPROVAL:", i.value.get("pending_action", {}).get("description"))
            result = await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
            hops += 1

    print(f"\nTotal wall time: {time.time() - t0:.1f}s")

    print("\n--- plan ---")
    for s in result.get("plan").steps:
        print(f"  [{s.id}] {s.agent}: {s.task} (approval={s.requires_approval}, depends_on={s.depends_on})")

    print("\n--- step results ---")
    for step_id, r in result.get("step_results", {}).items():
        print(f"  {step_id} ({r['agent']}, {r['status']}): {r['output'][:200]}")

    print("\n--- conflicts ---", result.get("conflicts"))
    print("\n--- FINAL ANSWER ---")
    print(result.get("final_answer"))


if __name__ == "__main__":
    asyncio.run(main())
