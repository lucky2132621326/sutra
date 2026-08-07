"""
RAG store — chunk -> embed locally -> ChromaDB.

Extracted unchanged (internals) from the original engine.py. This is the
Knowledge Agent's retrieval layer: ingest() for document upload, retrieve()
for top-k similarity search at query time.
"""
import os
import io
import uuid
from pathlib import Path

CHROMA_DIR = str(Path(__file__).resolve().parents[3] / "chroma_db")
COLLECTION_NAME = "agentx_docs"
EMBED_MODEL = "all-MiniLM-L6-v2"  # small + fast; downloads once (~90MB), then cached
CHUNK_SIZE = 800        # characters per chunk
CHUNK_OVERLAP = 150     # characters shared between neighboring chunks
TOP_K = 4               # chunks returned per query

# Lazy singletons — model load takes seconds, so do it once per process.
_embedder = None
_collection = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = client.get_or_create_collection(COLLECTION_NAME)
    return _collection


def _chunk_text(text):
    """Split text into overlapping character chunks, preferring paragraph breaks."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        if end < len(text):
            # cut at the last paragraph/sentence/space boundary inside the window
            window = text[start:end]
            for sep in ("\n\n", "\n", ". ", " "):
                cut = window.rfind(sep)
                if cut > CHUNK_SIZE // 2:
                    end = start + cut + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return [c for c in chunks if c]


def _extract_text(source, filename):
    """Return plain text from a path or binary file object (txt / pdf / csv)."""
    ext = os.path.splitext(filename)[1].lower()

    if isinstance(source, (str, os.PathLike)):
        data = open(source, "rb").read()
    else:
        data = source.read()

    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    if ext == ".csv":
        import pandas as pd
        df = pd.read_csv(io.BytesIO(data))
        # Row-per-line "col: value" text so retrieval can match on any field
        lines = [f"Columns: {', '.join(df.columns)}"]
        for _, row in df.iterrows():
            lines.append("; ".join(f"{col}: {row[col]}" for col in df.columns))
        return "\n".join(lines)

    # default: treat as utf-8 text (.txt, .md, code files, ...)
    return data.decode("utf-8", errors="replace")


def ingest(source, filename=None):
    """Extract text from a txt/pdf/csv file, chunk, embed, store in ChromaDB.

    source: file path (str) or binary file object
    filename: required when source is a file object (used for type detection)

    Returns {"file_id": str, "chunks": int, "text": str} — text is the full
    extracted text so callers can reuse it for summarizing.
    """
    if filename is None:
        if not isinstance(source, (str, os.PathLike)):
            raise ValueError("filename is required when source is a file object")
        filename = os.path.basename(str(source))

    text = _extract_text(source, filename)
    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError(f"No text could be extracted from {filename}")

    file_id = uuid.uuid4().hex[:12]
    embeddings = _get_embedder().encode(chunks, show_progress_bar=False).tolist()
    _get_collection().add(
        ids=[f"{file_id}-{i}" for i in range(len(chunks))],
        embeddings=embeddings,
        documents=chunks,
        metadatas=[{"file_id": file_id, "filename": filename, "chunk": i} for i in range(len(chunks))],
    )
    return {"file_id": file_id, "chunks": len(chunks), "text": text}


def retrieve(query, top_k=TOP_K):
    """Return the top_k most relevant chunk strings for a query.

    Returns [] when nothing has been ingested yet, so callers work with or
    without uploaded documents.
    """
    collection = _get_collection()
    if collection.count() == 0:
        return []
    query_embedding = _get_embedder().encode([query], show_progress_bar=False).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=min(top_k, collection.count()))
    return results["documents"][0]
