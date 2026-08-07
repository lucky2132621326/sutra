"""
AgentX engine — drop-in backend brain. Swap providers or prompts only in the
ADAPTATION ZONE below; everything past it is plumbing that shouldn't need to
change on hackathon day.
"""
import os
import io
import json
import uuid

from dotenv import load_dotenv

load_dotenv()

# ============================================================
# ADAPTATION ZONE — this is the ONLY block to edit on hackathon day.
# Everything below this zone (call_llm, ingest, retrieve, run_query,
# process_upload) is domain-agnostic plumbing — don't touch it.
#
# Workflow once the problem statement drops (9:40 AM, 24 July):
#   1. Read the problem statement, decide what "domain" we're in
#      (e.g. "Legal Contract Analyzer", "Hospital Triage Assistant").
#   2. Rewrite DOMAIN_NAME, SYSTEM_PROMPT, OUTPUT_STYLE, and
#      ANALYSIS_INSTRUCTIONS below to match. Nothing else needs to change —
#      run_query() and process_upload() already reference these constants.
#   3. Re-run `python test_engine.py all <sample-file-for-new-domain>` to
#      confirm the new prompt behaves sanely before wiring up the frontend.
#   4. If the new domain needs a genuinely different LLM behavior (e.g. a
#      stricter output schema), that's the only case where you'd touch
#      QUERY_JSON_INSTRUCTIONS further down — but for 90% of problem
#      statements, editing just these four constants is enough.
# ============================================================

# One line describing what this engine is for right now. Shows up in the
# UI/logs if the frontend wants to display it — otherwise purely documentation.
DOMAIN_NAME = "General Assistant"

# The core persona + task instructions. This is what makes the engine feel
# specialized for whatever problem statement we get. Be specific: name the
# domain, the kind of questions it'll face, and any constraints (e.g. "only
# answer from the provided context", "flag anything you're unsure about").
SYSTEM_PROMPT = """You are a helpful, precise assistant. Answer the user's
question using the provided context when available. Be concise."""

# How responses should read — tone, length, formatting. Gets appended to the
# JSON instructions the LLM receives, so keep it short and concrete.
OUTPUT_STYLE = "Plain, concise prose. No unnecessary preamble."

# What "reasoning" should look like in run_query()'s response. Tune this if
# judges/demo want to see more (or less) of the "how" behind each answer.
ANALYSIS_INSTRUCTIONS = """When answering, briefly explain your reasoning —
what information you used and why it supports the answer."""
# ============================================================
# END ADAPTATION ZONE
# ============================================================


# Provider priority: Gemini first (cheap/fast for hackathon demo credits),
# Anthropic second, OpenAI third. Only providers with a set env key are tried.
GEMINI_MODEL = "gemini-flash-latest"
ANTHROPIC_MODEL = "claude-opus-4-8"
OPENAI_MODEL = "gpt-4o-mini"


def _parse_json_response(text):
    """Parse the first JSON value in text, tolerating trailing junk or
    markdown fences that some providers occasionally emit even in JSON mode.
    """
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.JSONDecoder().raw_decode(text.strip())[0]


def _call_gemini(system, messages, json_mode):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    contents = [
        types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["content"])])
        for m in messages
    ]

    config = types.GenerateContentConfig(system_instruction=system)
    if json_mode:
        config.response_mime_type = "application/json"

    response = client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
    text = response.text
    return _parse_json_response(text) if json_mode else text


def _call_anthropic(system, messages, json_mode):
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    effective_system = system
    if json_mode:
        effective_system += "\n\nRespond with valid JSON only. No prose, no markdown fences."

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        system=effective_system,
        messages=messages,
    )
    text = next(b.text for b in response.content if b.type == "text")
    return _parse_json_response(text) if json_mode else text


def _call_openai(system, messages, json_mode):
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system}] + messages,
        **kwargs,
    )
    text = response.choices[0].message.content
    return _parse_json_response(text) if json_mode else text


def call_llm(system, messages, json_mode=False):
    """Call the LLM with automatic provider fallback.

    system: system prompt string
    messages: list of {"role": "user"|"assistant", "content": str}
    json_mode: if True, ask for and parse a JSON response

    Returns a str (or dict if json_mode=True). Raises RuntimeError if every
    configured provider fails.
    """
    providers = []
    if os.environ.get("GEMINI_API_KEY"):
        providers.append(("gemini", _call_gemini))
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append(("anthropic", _call_anthropic))
    if os.environ.get("OPENAI_API_KEY"):
        providers.append(("openai", _call_openai))

    if not providers:
        raise RuntimeError("No LLM provider API keys are set (GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY).")

    errors = []
    for name, fn in providers:
        try:
            return fn(system, messages, json_mode)
        except Exception as e:
            errors.append(f"{name}: {e}")

    raise RuntimeError("All LLM providers failed:\n" + "\n".join(errors))


# ============================================================
# RAG: chunk -> embed locally -> ChromaDB
# ============================================================
CHROMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
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
    extracted text so callers (process_upload) can reuse it for summarizing.
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

    Returns [] when nothing has been ingested yet, so run_query works
    with or without uploaded documents.
    """
    collection = _get_collection()
    if collection.count() == 0:
        return []
    query_embedding = _get_embedder().encode([query], show_progress_bar=False).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=min(top_k, collection.count()))
    return results["documents"][0]


# ============================================================
# Endpoints — exact contract shapes (see CLAUDE.md § API contract)
# ============================================================
QUERY_JSON_INSTRUCTIONS = """Respond with a JSON object with exactly these keys:
- "result": your direct answer to the user, following the OUTPUT_STYLE below
- "reasoning": a brief explanation of how you derived the answer (what context you used, if any)
- "data": a JSON object with any structured data relevant to the answer (empty object {} if none)

Output style: %s""" % OUTPUT_STYLE


def run_query(user_input, context=None):
    """RAG retrieve -> SYSTEM_PROMPT -> LLM -> contract-shaped dict.

    Returns { "result": str, "reasoning": str, "data": obj, "status": "ok"|"error" }
    """
    try:
        chunks = retrieve(user_input)
        context_block = (
            "Relevant context from uploaded documents:\n\n" + "\n\n---\n\n".join(chunks)
            if chunks else "No uploaded documents are relevant to this query."
        )

        system = f"{SYSTEM_PROMPT}\n\n{ANALYSIS_INSTRUCTIONS}\n\n{QUERY_JSON_INSTRUCTIONS}"
        user_message = f"{context_block}\n\n"
        if context:
            user_message += f"Additional context: {json.dumps(context)}\n\n"
        user_message += f"User question: {user_input}"

        parsed = call_llm(system, [{"role": "user", "content": user_message}], json_mode=True)

        return {
            "result": parsed.get("result", ""),
            "reasoning": parsed.get("reasoning", ""),
            "data": parsed.get("data", {}),
            "status": "ok",
        }
    except Exception as e:
        return {"result": f"Something went wrong: {e}", "reasoning": "", "data": {}, "status": "error"}


def process_upload(file, filename=None):
    """Extract text, ingest to ChromaDB, return a 2-sentence LLM summary.

    file: binary file object (or path)
    filename: required when file is a file object (used for type detection)

    Returns { "file_id": str, "summary": str, "status": "ok"|"error" }
    """
    try:
        result = ingest(file, filename=filename)

        summary = call_llm(
            "You summarize documents in exactly two sentences. No preamble, no markdown.",
            [{"role": "user", "content": f"Summarize this document:\n\n{result['text'][:8000]}"}],
        )

        return {"file_id": result["file_id"], "summary": summary.strip(), "status": "ok"}
    except Exception as e:
        return {"file_id": "", "summary": f"Upload failed: {e}", "status": "error"}
