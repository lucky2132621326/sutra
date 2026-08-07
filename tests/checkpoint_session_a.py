"""
Checkpointer durability — PROCESS A. Runs a graph until it pauses at the
approval interrupt, then EXITS without resuming. Everything in RAM dies here;
only data/checkpoints.db survives.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MOCK_LLM"] = "1"

from apps.api.graph.build import graph_session  # noqa: E402

THREAD_ID = "checkpoint-durability-thread"
STUDENT = "1602-23-733-042"


async def main():
    print(f"[A pid={os.getpid()}] starting run on thread_id={THREAD_ID}")
    async with graph_session() as graph:
        config = {"configurable": {"thread_id": THREAD_ID}}
        result = await graph.ainvoke(
            {"run_id": THREAD_ID, "student_id": STUDENT, "role": "student",
             "goal": "Register me for the placement workshop.", "iteration": 0},
            config=config,
        )
        interrupted = "__interrupt__" in result
        print(f"[A pid={os.getpid()}] paused at interrupt: {interrupted}")
        if not interrupted:
            print("[A] FAIL: expected the run to pause at the approval gate")
            sys.exit(1)
        for i in result["__interrupt__"]:
            print(f"[A] pending: {i.value.get('pending_action', {}).get('description')}")

        state = await graph.aget_state(config)
        print(f"[A] checkpointed next-node: {state.next}")
        print(f"[A] plan steps in checkpoint: {[s.id for s in state.values['plan'].steps]}")
    print("[A] OK — exiting WITHOUT resuming; state must now live only on disk")


if __name__ == "__main__":
    asyncio.run(main())
