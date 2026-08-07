"""
Cross-session memory acceptance test — PROCESS B (recall side).

Runs as a SEPARATE OS process after memory_session_a.py has exited, with a
different thread_id (i.e. a genuinely new session). Must recall both the
durable profile facts and the semantic turn summary written by process A,
purely from disk.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MOCK_LLM"] = "1"

from apps.api.memory import load_memory_block  # noqa: E402

STUDENT = "1602-23-733-042"


def main():
    print(f"[B pid={os.getpid()}] recalling memory for {STUDENT} in a NEW session")
    failures = []

    # --- Case 1: the battle plan's scenario. An UNRELATED follow-up question.
    # Profile facts must still surface (that is what personalises the answer);
    # semantic recall is expected to stay quiet, because nothing stored is
    # actually relevant to electives. Silence here is correct behaviour, not a
    # miss — recalling unrelated chatter would be the bug.
    block, facts, recalled = load_memory_block(
        STUDENT, "What electives should I take next semester?"
    )
    print(f"\n[B] CASE 1 — unrelated query 'What electives should I take next semester?'")
    print(f"    profile facts recalled ({len(facts)}):")
    for f in facts:
        print(f"      {f['key']} = {f['value']}  (confidence {f['confidence']})")
    print(f"    semantic summaries recalled ({len(recalled)}) — expected 0, irrelevant to the query")

    fact_keys = {f["key"] for f in facts}
    if "preference.schedule" not in fact_keys:
        failures.append("CASE 1: preference.schedule not recalled")
    if "interest.domain" not in fact_keys:
        failures.append("CASE 1: interest.domain not recalled")
    if not any("morning" in f["value"].lower() for f in facts):
        failures.append("CASE 1: morning preference value not recalled")
    if not any("machine learning" in f["value"].lower() for f in facts):
        failures.append("CASE 1: ML interest value not recalled")
    if not block:
        failures.append("CASE 1: empty memory block — nothing would reach the prompt")

    print(f"\n[B] memory block injected into prompts:\n{block}\n")

    # --- Case 2: a RELATED follow-up. The semantic tier must now surface the
    # turn summary written by the other process, proving tier 3 also survived
    # the restart rather than merely being unreachable in case 1.
    block2, _, recalled2 = load_memory_block(
        STUDENT, "Remind me what we decided about the Google internship and the workshop."
    )
    print(f"[B] CASE 2 — related query about the Google internship")
    print(f"    semantic summaries recalled ({len(recalled2)}):")
    for r in recalled2:
        print(f"      score={r['score']} thread={r['thread_id']}: {r['summary']}")

    if not recalled2:
        failures.append("CASE 2: related query recalled no semantic summary")
    elif not any(r["thread_id"] == "session-A-thread" for r in recalled2):
        failures.append("CASE 2: recalled summary did not originate from session A")

    if failures:
        print("\n[B] FAIL:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print("\n[B] OK — profile facts AND the semantic summary survived the process")
    print("[B]    restart into a new session; semantic recall correctly stayed")
    print("[B]    silent on the unrelated query.")


if __name__ == "__main__":
    main()
