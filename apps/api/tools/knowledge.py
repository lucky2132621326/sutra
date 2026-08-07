"""Knowledge Agent tools: RAG search over institutional documents.

Searches the clause-aware policy collection built by
apps/api/rag/ingest_docs.py, so every citation carries a document title,
document number, clause number, and page — which is what makes an answer
checkable rather than merely plausible.

Falls back to the generic document store (rag/store.py, used for ad-hoc
uploads) when the policy collection is empty, so the tool still works before
any policy docs are ingested.
"""
from apps.api.rag.ingest_docs import _get_policy_collection
from apps.api.rag.store import _get_collection, _get_embedder
from apps.api.tools.models import Citation, DocumentSpan, PolicySearchResult

# Below this cosine similarity the agent must abstain rather than answer from
# weakly-related text. HEURISTIC, not tuned against a labelled set — see the
# limitations note in the status report.
NO_CONTEXT_THRESHOLD = 0.35


def _score_from_distance(distance: float) -> float:
    """Chroma returns squared-L2 over normalised vectors; for unit vectors
    cos_sim = 1 - d^2/2, and `distances` is already that squared value."""
    return max(0.0, 1.0 - distance / 2)


def search_policy(query: str, k: int = 4) -> PolicySearchResult:
    collection = _get_policy_collection()
    use_fallback = collection.count() == 0
    if use_fallback:
        collection = _get_collection()
        if collection.count() == 0:
            return PolicySearchResult(query=query, citations=[], no_relevant_context=True)

    query_embedding = _get_embedder().encode([query], show_progress_bar=False).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=min(k, collection.count()))

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    citations = []
    for doc, meta, dist in zip(docs, metas, distances):
        citations.append(Citation(
            text=doc,
            doc_title=meta.get("doc_title") or meta.get("filename", "unknown"),
            doc_number=meta.get("doc_number", ""),
            clause=str(meta.get("clause", meta.get("chunk", ""))),
            page=int(meta.get("page", 1) or 1),
            score=round(_score_from_distance(dist), 3),
        ))

    top_score = citations[0].score if citations else 0.0
    return PolicySearchResult(
        query=query, citations=citations, no_relevant_context=top_score < NO_CONTEXT_THRESHOLD,
    )


def get_document_span(doc_title: str, clause: str) -> DocumentSpan:
    """Fetch the exact text of one clause, for the citation drawer."""
    collection = _get_policy_collection()
    if collection.count() > 0:
        got = collection.get(where={"clause": str(clause)})
        for doc, meta in zip(got["documents"], got["metadatas"]):
            if doc_title.lower() in (meta.get("doc_title", "") or "").lower():
                return DocumentSpan(
                    doc_title=meta.get("doc_title", doc_title),
                    doc_number=meta.get("doc_number", ""),
                    clause=str(meta.get("clause", clause)), text=doc,
                )

    generic = _get_collection()
    if generic.count() > 0:
        got = generic.get(where={"filename": doc_title})
        for doc, meta in zip(got["documents"], got["metadatas"]):
            if str(meta.get("chunk")) == str(clause):
                return DocumentSpan(doc_title=doc_title, doc_number=meta.get("file_id", ""),
                                     clause=str(clause), text=doc)

    return DocumentSpan(doc_title=doc_title, doc_number="", clause=str(clause), text="")
