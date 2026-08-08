"""
3-tier memory facade.

  Tier 1 WORKING   — LangGraph thread state via AsyncSqliteSaver
                     (data/checkpoints.db). Already wired in graph/build.py;
                     nothing in this package handles it.
  Tier 2 PROFILE   — durable structured facts per student (memory/profile.py).
  Tier 3 SEMANTIC  — vector-recalled turn summaries (memory/semantic.py).

The graph touches this package at exactly two points: intake_node calls
load_memory_block() before planning, and memory_write_node calls
write_turn_memory() after synthesis.
"""
import asyncio

from apps.api.llm.router import call_llm_async
from apps.api.memory import profile, semantic

MEMORY_BLOCK_CHAR_BUDGET = 1600  # keep the injected block well under ~400 tokens

EXTRACT_SYSTEM = (
    "From this exchange, extract durable facts about the student: preferences, "
    "goals, constraints, interests. Only extract things that will still be true "
    "next week — not one-off details of this request.\n"
    'Respond with JSON: {"facts": [{"key": str, "value": str, "confidence": number}]}. '
    'Use a short snake_case key (e.g. "preference.schedule", "interest.domain"). '
    'Return {"facts": []} if there is nothing durable.'
)

SUMMARIZE_SYSTEM = (
    "Summarize this exchange in at most two sentences, written so it is useful "
    "as recalled context in a future conversation. Focus on what the student "
    "wanted and what was decided.\n"
    'Respond with JSON: {"summary": str}'
)


def load_memory_block(student_id: str, query: str) -> tuple[str, list[dict], list[dict]]:
    """Return (prompt_block, profile_facts, recalled_summaries).

    prompt_block is a compact text block injected into planner/agent system
    prompts. Returns an empty block when there is nothing known yet, so a
    first-ever turn costs nothing.
    """
    facts = profile.get_facts(student_id)
    recalled = semantic.recall(student_id, query)

    if not facts and not recalled:
        return "", [], []

    lines = []
    if facts:
        lines.append("Known about this student:")
        lines += [f"- {f['key']}: {f['value']} (confidence {f['confidence']})" for f in facts]
    if recalled:
        lines.append("Relevant context from earlier conversations:")
        lines += [f"- {r['summary']}" for r in recalled]

    block = "\n".join(lines)
    if len(block) > MEMORY_BLOCK_CHAR_BUDGET:
        block = block[:MEMORY_BLOCK_CHAR_BUDGET].rsplit("\n", 1)[0] + "\n- (older memories truncated)"
    return block, facts, recalled


async def write_turn_memory(student_id: str, thread_id: str, user_message: str, answer: str) -> dict:
    """Extract durable facts + a turn summary from a completed turn and
    persist both. Never raises — memory is an enhancement, and a failed
    extraction must not fail the user's actual request.

    Returns {"facts_written": [...], "summary": str} for event emission.
    """
    exchange = f"Student said: {user_message}\n\nAssistant answered: {answer}"
    written: list[dict] = []
    summary = ""

    try:
        parsed = await call_llm_async(
            EXTRACT_SYSTEM, [{"role": "user", "content": exchange}], json_mode=True,
        )
        for fact in parsed.get("facts", []) or []:
            key, value = fact.get("key"), fact.get("value")
            if not key or value in (None, ""):
                continue
            confidence = float(fact.get("confidence", 0.5) or 0.5)
            stored = profile.upsert_fact(student_id, str(key), str(value), confidence, evidence_turn=thread_id)
            written.append({"key": key, "value": value, "confidence": confidence, "stored": stored})
    except Exception:
        pass

    try:
        parsed = await call_llm_async(
            SUMMARIZE_SYSTEM, [{"role": "user", "content": exchange}], json_mode=True,
        )
        summary = (parsed.get("summary") or "").strip()
        if summary:
            await asyncio.to_thread(semantic.add_turn_summary, student_id, thread_id, summary)
    except Exception:
        pass

    return {"facts_written": written, "summary": summary}


def clear_all() -> None:
    profile.clear_all()
    semantic.clear_all()
