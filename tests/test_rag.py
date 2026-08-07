"""
Knowledge Agent / RAG tests: clause-level citations, the specific demo
citation (R22 clause 4.2 = the 75% attendance rule), and abstention on
low-relevance queries instead of answering from unrelated text.

Requires the policy collection to be built:
    python -m apps.api.rag.ingest_docs
"""
import asyncio

import pytest

from apps.api.rag.ingest_docs import DOCS_DIR, parse_document
from apps.api.tools.knowledge import NO_CONTEXT_THRESHOLD, get_document_span, search_policy


def test_citations_carry_title_number_clause_and_page():
    result = search_policy("attendance requirement to be eligible for semester end examination")
    assert result.citations, "no citations returned"
    top = result.citations[0]
    assert top.doc_title, "citation missing doc_title"
    assert top.doc_number, "citation missing doc_number"
    assert top.clause, "citation missing clause number"
    assert top.page >= 1, "citation missing a sane page number"
    assert 0.0 <= top.score <= 1.0


def test_75_percent_attendance_rule_resolves_to_r22_clause_4_2():
    """The demo's headline citation. The conflict story tells the student they
    are below the 75% bar 'under R22 clause 4.2' — that must be real."""
    result = search_policy("minimum attendance percentage required to sit the semester end examination")
    assert not result.no_relevant_context
    top = result.citations[0]
    assert top.clause == "4.2", f"expected clause 4.2, got {top.clause}"
    assert "R22" in top.doc_title
    assert "seventy-five percent (75%)" in top.text
    assert "detained" in top.text


def test_condonation_procedure_resolves_to_clause_4_5():
    result = search_policy("condonation of shortage of attendance on medical grounds")
    clauses = [c.clause for c in result.citations]
    assert "4.5" in clauses, f"expected clause 4.5 among {clauses}"


def test_abstains_on_out_of_scope_query():
    result = search_policy("what is the best pizza topping in Naples")
    assert result.no_relevant_context is True
    assert result.citations[0].score < NO_CONTEXT_THRESHOLD


def test_in_scope_query_does_not_abstain():
    result = search_policy("library book loan period and overdue fine")
    assert result.no_relevant_context is False
    assert result.citations[0].score >= NO_CONTEXT_THRESHOLD


def test_get_document_span_returns_exact_clause_text():
    span = get_document_span("Academic Regulations R22", "4.2")
    assert span.clause == "4.2"
    assert "75%" in span.text
    assert span.doc_number == "VCE/ACAD/R22/2022"


@pytest.mark.asyncio
async def test_knowledge_agent_emits_real_citations_on_the_wire():
    """The Knowledge agent must retrieve from the POLICY corpus (agentx_policies)
    and put full Citation objects on rag.retrieved.

    Regression guard: it previously called rag.store.retrieve(), which queries
    the generic `agentx_docs` collection — so it was fed unrelated uploaded
    documents and emitted only a bare chunk count, making clause-accurate
    citation impossible in the UI.
    """
    import os

    os.environ["MOCK_LLM"] = "1"
    from apps.api.bus import bus
    from apps.api.graph.agents import run_agent_step
    from packages.contracts.plan import Step

    run_id = "test-rag-citations"
    seen = []

    async def watch():
        async for e in bus.subscribe(run_id):
            seen.append(e)

    task = asyncio.create_task(watch())
    await asyncio.sleep(0.05)
    step = Step(id="k1", agent="knowledge",
                task="What is the minimum attendance required to sit the semester end examination?")
    await run_agent_step("knowledge", step,
                         {"run_id": run_id, "student_id": "1602-23-733-042", "step_results": {}})
    await bus.close_run(run_id)
    await task

    rag = [e for e in seen if e.type.value == "rag.retrieved"]
    assert rag, "no rag.retrieved event emitted"
    payload = rag[0].payload

    assert payload["abstained"] is False
    assert payload["citations"], "citations must be on the wire, not just a count"
    top = payload["citations"][0]
    assert top["clause"] == "4.2", f"expected the 75% rule at clause 4.2, got {top['clause']}"
    assert top["doc_number"] == "VCE/ACAD/R22/2022"
    assert top["page"] >= 1
    assert "seventy-five percent (75%)" in top["text"]
    # Back-compat: the old integer count must still be present for existing fixtures.
    assert payload["chunks"] == len(payload["citations"])


@pytest.mark.asyncio
async def test_knowledge_agent_signals_abstention_on_the_wire():
    import os

    os.environ["MOCK_LLM"] = "1"
    from apps.api.bus import bus
    from apps.api.graph.agents import run_agent_step
    from packages.contracts.plan import Step

    run_id = "test-rag-abstain"
    seen = []

    async def watch():
        async for e in bus.subscribe(run_id):
            seen.append(e)

    task = asyncio.create_task(watch())
    await asyncio.sleep(0.05)
    step = Step(id="k1", agent="knowledge", task="What is the best pizza topping in Naples?")
    await run_agent_step("knowledge", step,
                         {"run_id": run_id, "student_id": "1602-23-733-042", "step_results": {}})
    await bus.close_run(run_id)
    await task

    payload = next(e for e in seen if e.type.value == "rag.retrieved").payload
    assert payload["abstained"] is True
    assert payload["citations"] == [], "must not surface citations it is abstaining on"


def test_parser_extracts_clause_structure():
    doc_meta, chunks = parse_document(DOCS_DIR / "R22_academic_regulations.md")
    assert doc_meta["title"] == "Academic Regulations R22"
    assert doc_meta["doc_number"] == "VCE/ACAD/R22/2022"
    clauses = {c["clause"] for c in chunks}
    assert {"4.1", "4.2", "4.5", "6.2"} <= clauses
    clause_42 = next(c for c in chunks if c["clause"] == "4.2")
    assert "Attendance Requirements" in clause_42["section"]
