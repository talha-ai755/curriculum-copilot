#!/usr/bin/env python3
"""
semantic_index.py — Qdrant semantic search over curriculum content, on-disk.

One collection, OpenAI-embedded (text-embedding-3-small), on-disk so it survives process
restarts: curriculum_content — one point per LearningComponent/Assessment graph node, keyed by
the same `identifier` graph_store.py loads into Neo4j -- a semantic_search hit's `identifier` is
exactly what you MATCH on in Cypher to pull standards/parent-topic/progression context.

The v2 extraction pipeline (pdf_to_kg.py, text-first via docling + Claude) gives every
LearningComponent/Assessment node real, verbatim `sourceText`/`description` directly and reliable
`pageLabel`/`pdfPage` (Claude echoes an already-known page number instead of counting one) -- so
there's no more page-label-matching fallback needed, and no more separate raw-text collection:
Assessment Summaries/Pacing Guides/Self-Reflections/Overviews are now real LearningComponent
nodes (via pdf_to_kg.py's `supplementary[]`), indexed here automatically like anything else.

This is also now the ONLY place a LearningComponent's actual sourceText/description lives --
graph_store.load_graph() deliberately excludes those two fields when loading Neo4j (see
graph_store._BULK_TEXT_PROPS), since they'd otherwise be duplicated in full on both stores for
no benefit (~43% of a LearningComponent node's size). Use search() to find content by meaning,
get_content() to fetch known identifiers' text directly (no embedding call, not a search).

Built once by build_content_index.py (not re-embedded on every app start -- OpenAI costs money
per call); `index_nodes` always clears the collection first, so re-running the build script is
idempotent rather than duplicating points.
"""
import os
import time

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

COLLECTION = "curriculum_content"
DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "data", "qdrant_storage")

OPENAI_EMBED_MODEL = "text-embedding-3-small"
OPENAI_EMBED_DIM = 1536
_EMBED_BATCH = 100  # texts per OpenAI embeddings API call
_UPSERT_BATCH = 200  # points per upsert call -- a single unbatched call reliably times out
                      # against a remote Qdrant (see index_nodes())
_UPSERT_MAX_RETRIES = 4

_CONTENT_FIELDS = {
    # label -> (description, source_text) pulled from the node's own properties. A LearningComponent
    # has both; an Assessment only ever has `name` (pdf_to_kg.py never gives it description/
    # sourceText) -- normalized onto description here so both labels follow the same two-field
    # shape, with no per-label special-casing needed anywhere downstream.
    "LearningComponent": lambda p: (p.get("description", ""), p.get("sourceText", "")),
    "Assessment": lambda p: (p.get("name", ""), ""),
}
# StandardsFrameworkItem intentionally excluded: found to dilute fuzzy content search (short,
# generic standard statements out-ranking genuinely relevant activity text on cosine similarity).


def _combined_text(payload):
    """description + " " + source_text -- the exact string that was embedded, reconstructed from
    the two stored fields rather than kept as a separate (redundant, driftable) third payload
    field. Cheap string join, no embedding call."""
    return " ".join(x for x in (payload.get("description", ""), payload.get("source_text", "")) if x)


def get_client(path=None, host=None, port=None, api_key=None):
    """Local on-disk client by default (path=), or a remote Qdrant server if host is given
    (explicitly, or via QDRANT_HOST env var) -- the two modes are mutually exclusive in the
    underlying qdrant-client library, so this picks one rather than trying both. Remote mode
    also respects QDRANT_PORT (default 6333) and QDRANT_API_KEY (optional, for a secured
    deployment) via env vars if not passed explicitly."""
    host = host or os.environ.get("QDRANT_HOST")
    if host:
        port = port or int(os.environ.get("QDRANT_PORT", 6333))
        api_key = api_key or os.environ.get("QDRANT_API_KEY") or None
        return QdrantClient(host=host, port=port, api_key=api_key)
    return QdrantClient(path=path or DEFAULT_PATH)


def _make_shared_client_getter():
    """Deferred import so plain (non-Streamlit) scripts importing this module never need
    streamlit installed/running -- only called the first time get_shared_client() runs."""
    import streamlit as st

    @st.cache_resource(show_spinner=False)
    def _cached(path, host, port, api_key):
        return get_client(path, host, port, api_key)

    return _cached


_shared_client_getter = None


def get_shared_client(path=None, host=None, port=None, api_key=None):
    """Streamlit entry point for get_client() -- local-mode QdrantClient holds an OS-level file
    lock on its storage directory, so two independently-constructed clients pointed at the same
    local path can't coexist in one process (confirmed live: app.py's load_course() and
    Page_Inspector.py's own client each used a separately-`@st.cache_resource`-decorated wrapper,
    so navigating between pages crashed with "already accessed by another instance"). Every
    Streamlit caller should go through this one function instead of calling get_client() directly
    so `st.cache_resource` gives them all the exact same client object, keyed by this function's
    identity -- shared across every page in a multipage app, not per-page. Non-Streamlit callers
    (build_content_index.py, one-shot backfill/audit scripts) should keep calling get_client()
    directly; they're short-lived processes with no lock-sharing concern."""
    global _shared_client_getter
    if _shared_client_getter is None:
        _shared_client_getter = _make_shared_client_getter()
    return _shared_client_getter(path, host, port, api_key)


def _parent_names(nodes, rels):
    """identifier -> "Lesson N: Title" (or "Topic N: Title" if there's no Lesson parent) for
    every node, by walking one hasPart edge backward. Grouping-only nodes (Lesson/Topic) are
    never indexed themselves, but a LearningComponent's parent name is worth attaching to its
    Qdrant payload so a hit is self-citing without a follow-up graph_query."""
    props = {n["identifier"]: n["properties"] for n in nodes}
    parent_of = {r["target_identifier"]: r["source_identifier"]
                 for r in rels if r["label"] == "hasPart"}

    def name_of(identifier):
        p = props.get(identifier, {})
        ordinal, title = p.get("ordinalName", ""), p.get("name", "")
        return f"{ordinal}: {title}".strip(": ") if (ordinal or title) else ""

    names = {}
    for n in nodes:
        parent_id = parent_of.get(n["identifier"])
        names[n["identifier"]] = name_of(parent_id) if parent_id else ""
    return names


def _embed_texts(openai_client, texts):
    """Batch texts through OpenAI's embeddings API (_EMBED_BATCH per call), return one vector
    per input text, same order."""
    vectors = []
    for i in range(0, len(texts), _EMBED_BATCH):
        batch = texts[i:i + _EMBED_BATCH]
        resp = openai_client.embeddings.create(model=OPENAI_EMBED_MODEL, input=batch)
        vectors.extend(d.embedding for d in resp.data)
    return vectors


def index_nodes(client, openai_client, nodes, rels=()):
    """Embed (OpenAI) and index every LearningComponent/Assessment node's real verbatim text.
    Returns the count indexed."""
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)  # idempotent: never duplicate points on re-index
    client.create_collection(COLLECTION,
                              vectors_config=VectorParams(size=OPENAI_EMBED_DIM,
                                                           distance=Distance.COSINE))

    parent_name = _parent_names(nodes, rels) if rels else {}
    documents, metadata = [], []
    for n in nodes:
        fields_fn = _CONTENT_FIELDS.get(n["labels"][0])
        if not fields_fn:
            continue
        description, source_text = fields_fn(n["properties"])
        text = " ".join(x for x in (description, source_text) if x)  # embedding input only, not stored
        if not text.strip():
            continue
        documents.append(text)
        metadata.append({
            "identifier": n["identifier"],
            "label": n["labels"][0],
            "description": description,
            "source_text": source_text,
            "parent": parent_name.get(n["identifier"], ""),
            "page_label": n["properties"].get("pageLabel", ""),
            "pdf_page": n["properties"].get("pdfPage"),
            "lesson_phase": n["properties"].get("lessonPhase", ""),
            # Portable PDF citation key (see citation.py) -- "" on nodes built before this field
            # existed, until backfill_pdf_citation.py stamps it on.
            "source_pdf_key": n["properties"].get("sourcePdfKey", ""),
        })
    if not documents:
        return 0
    vectors = _embed_texts(openai_client, documents)
    points = [PointStruct(id=i, vector=vec, payload=meta)
              for i, (vec, meta) in enumerate(zip(vectors, metadata))]
    # Batched with retry (same pattern as migrate_qdrant_to_remote.py) -- a single unbatched
    # upsert of the full point list works fine against the local on-disk client but reliably
    # times out over the network against a remote Qdrant (confirmed live: it left the freshly
    # (re)created collection empty, having already deleted the old one above).
    for i in range(0, len(points), _UPSERT_BATCH):
        batch = points[i:i + _UPSERT_BATCH]
        for attempt in range(1, _UPSERT_MAX_RETRIES + 1):
            try:
                client.upsert(collection_name=COLLECTION, points=batch)
                break
            except Exception:
                if attempt == _UPSERT_MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)
    return len(points)


def search(client, openai_client, query, limit=25):
    """Semantic search over the indexed content (OpenAI-embedded query vector). [] if the
    collection is empty/missing."""
    if not client.collection_exists(COLLECTION):
        return []
    query_vector = _embed_texts(openai_client, [query])[0]
    hits = client.query_points(collection_name=COLLECTION, query=query_vector, limit=limit).points
    return [{"identifier": h.payload["identifier"], "label": h.payload["label"],
             "text": _combined_text(h.payload), "score": h.score,
             "description": h.payload.get("description", ""),
             "source_text": h.payload.get("source_text", ""),
             "parent": h.payload.get("parent", ""),
             "page_label": h.payload.get("page_label", ""),
             "pdf_page": h.payload.get("pdf_page"),
             "lesson_phase": h.payload.get("lesson_phase", ""),
             "source_pdf_key": h.payload.get("source_pdf_key", "")}
            for h in hits]


def get_payloads(client, identifiers):
    """Like get_content(), but returns each point's FULL stored payload dict (identifier, label,
    description, source_text, parent, page_label, pdf_page, lesson_phase -- note: no `text` key;
    that's derived on read by _combined_text()/get_content(), never stored) instead of just its
    combined text -- for QA/inspection tooling that wants to compare Qdrant's own stored
    page_label/pdf_page against the graph's, not just fetch content. Returns {identifier: payload};
    identifiers with no indexed point are simply absent from the result."""
    if not identifiers or not client.collection_exists(COLLECTION):
        return {}
    flt = Filter(should=[FieldCondition(key="identifier", match=MatchValue(value=i))
                          for i in identifiers])
    points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=flt,
                               limit=len(identifiers), with_payload=True)
    return {p.payload["identifier"]: p.payload for p in points}


def get_content(client, identifiers):
    """Deterministic fetch of one or more nodes' real verbatim text by identifier -- a direct
    point lookup (payload filter, no embedding call), NOT a similarity search. This is how
    graph_query results (which no longer carry LearningComponent's sourceText/description --
    see graph_store._BULK_TEXT_PROPS) get their actual text: find the identifier structurally,
    then fetch its text here. Returns {identifier: text}; identifiers with no indexed point are
    simply absent from the result."""
    if not identifiers or not client.collection_exists(COLLECTION):
        return {}
    flt = Filter(should=[FieldCondition(key="identifier", match=MatchValue(value=i))
                          for i in identifiers])
    points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=flt,
                               limit=len(identifiers), with_payload=True)
    return {p.payload["identifier"]: _combined_text(p.payload) for p in points}
