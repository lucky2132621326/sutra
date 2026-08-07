"""
Checkpointer durability — PROCESS B. A brand-new process reads the paused
run's state back from data/checkpoints.db and RESUMES it to completion.

If the checkpointer were MemorySaver, this process would find nothing and the
resume would be impossible — which is exactly what this proves.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MOCK_LLM"] = "1"

from langgraph.types import Command  # noqa: E402

from apps.api.graph.build import CHECKPOINT_DB, graph_session  # noqa: E402

THREAD_ID = "checkpoint-durability-thread"


async def main():
    print(f"[B pid={os.getpid()}] checkpoint db: {CHECKPOINT_DB}")
    print(f"[B pid={os.getpid()}] resuming thread_id={THREAD_ID} from a COLD process")
    failures = []

    async with graph_session() as graph:
        config = {"configurable": {"thread_id": THREAD_ID}}

        state = await graph.aget_state(config)
        if not state.values:
            failures.append("no checkpointed state found — state did not survive the restart")
            print("[B] FAIL: empty state")
            sys.exit(1)

        print(f"[B] recovered goal: {state.values.get('goal')!r}")
        print(f"[B] recovered plan steps: {[s.id for s in state.values['plan'].steps]}")
        print(f"[B] paused before node: {state.next}")
        if not state.next:
            failures.append("recovered state was not paused — nothing to resume")

        result = await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
        hops = 0
        while "__interrupt__" in result and hops < 5:
            result = await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
            hops += 1

        answer = result.get("final_answer")
        print(f"[B] resumed to completion, final answer: {(answer or '')[:160]}")
        if not answer:
            failures.append("resumed run produced no final answer")

    if failures:
        print("[B] FAIL:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("[B] OK — a cold process resumed a paused run purely from data/checkpoints.db")


if __name__ == "__main__":
    asyncio.run(main())
