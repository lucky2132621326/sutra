"""
Cross-session memory acceptance test — PROCESS A (write side).

Run as its own OS process, then exit. tests/memory_session_b.py runs as a
SEPARATE process afterwards and must recall what this one wrote. Splitting
them across real processes is the whole point: anything held only in RAM
(module singletons, the in-memory checkpointer, a warm embedder) dies here.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MOCK_LLM"] = "1"

from apps.api.memory import write_turn_memory  # noqa: E402

STUDENT = "1602-23-733-042"


async def main():
    print(f"[A pid={os.getpid()}] writing memory for {STUDENT}")
    result = await write_turn_memory(
        student_id=STUDENT,
        thread_id="session-A-thread",
        user_message="I prefer morning classes and I'm interested in machine learning.",
        answer="Noted — I'll prioritise morning sessions and ML-related opportunities for you.",
    )
    print(f"[A pid={os.getpid()}] facts written: {result['facts_written']}")
    print(f"[A pid={os.getpid()}] summary: {result['summary']}")

    if not result["facts_written"]:
        print("[A] FAIL: no facts written")
        sys.exit(1)
    print("[A] OK")


if __name__ == "__main__":
    asyncio.run(main())
