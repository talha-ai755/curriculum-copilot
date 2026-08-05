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

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

COLLECTION = "curriculum_content"
DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "data", "qdrant_storage")

OPENAI_EMBED_MODEL = "text-embedding-3-small"
OPENAI_EMBED_DIM = 1536
_EMBED_BATCH = 100  # texts per OpenAI embeddings API call

_INDEXABLE_LABELS = {
    "LearningComponent": lambda p: " ".join(
        x for x in (p.get("description", ""), p.get("sourceText", "")) if x),
    "Assessment": lambda p: p.get("name", ""),
}
# StandardsFrameworkItem intentionally excluded: found to dilute fuzzy content search (short,
# generic standard statements out-ranking genuinely relevant activity text on cosine similarity).


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
        text_fn = _INDEXABLE_LABELS.get(n["labels"][0])
        if not text_fn:
            continue
        text = text_fn(n["properties"])
        if not text.strip():
            continue
        documents.append(text)
        metadata.append({
            "identifier": n["identifier"],
            "label": n["labels"][0],
            "text": text,
            "parent": parent_name.get(n["identifier"], ""),
            "page_label": n["properties"].get("pageLabel", ""),
            "pdf_page": n["properties"].get("pdfPage"),
            "lesson_phase": n["properties"].get("lessonPhase", ""),
        })
    if not documents:
        return 0
    vectors = _embed_texts(openai_client, documents)
    points = [PointStruct(id=i, vector=vec, payload=meta)
              for i, (vec, meta) in enumerate(zip(vectors, metadata))]
    client.upsert(collection_name=COLLECTION, points=points)
    return len(points)


def search(client, openai_client, query, limit=25):
    """Semantic search over the indexed content (OpenAI-embedded query vector). [] if the
    collection is empty/missing."""
    if not client.collection_exists(COLLECTION):
        return []
    query_vector = _embed_texts(openai_client, [query])[0]
    hits = client.query_points(collection_name=COLLECTION, query=query_vector, limit=limit).points
    return [{"identifier": h.payload["identifier"], "label": h.payload["label"],
             "text": h.payload["text"], "score": h.score,
             "parent": h.payload.get("parent", ""),
             "page_label": h.payload.get("page_label", ""),
             "pdf_page": h.payload.get("pdf_page"),
             "lesson_phase": h.payload.get("lesson_phase", "")}
            for h in hits]


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
    return {p.payload["identifier"]: p.payload["text"] for p in points}
