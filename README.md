# Curriculum → Knowledge Graph Copilot

Turn a curriculum PDF into a standards-aligned **knowledge graph**, then let teachers and
coaches **ask questions** grounded in it. Reads the PDF with Claude, aligns each lesson to
its standards (TEKS), and builds a graph in the [Learning Commons](https://www.learningcommons.org/)
schema (`nodes.jsonl` + `relationships.jsonl`).

Built for the **Bluebonnet Learning** (Texas) math curriculum, but the framework and
jurisdiction are configurable, so it generalizes to other publishers/states.

## What it does

```
PDF (content)  ─┐
                ├─► Claude reads pages ─► structured JSON ─► deterministic graph builder ─► nodes + edges
CASE (framework)┘         (the "eyes")                              (plain code)
                                                                         │
                                                          💬 Chat: Claude answers teacher
                                                             questions grounded in the graph
```

- **Claude** does two jobs: **reads** the PDF into structure, and **answers** questions.
- **Everything else** — IDs (deterministic UUID5), graph edges, standards matching — is plain,
  repeatable Python (no guessing).

## Run locally

Chat is backed by Neo4j (the graph) + Qdrant (semantic search over lesson content) — start
Neo4j first:

```bash
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/changeme-local-pw neo4j:5
# or: docker compose up -d
```

Then:

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501.

- **Demo mode** (default ON) builds a graph from bundled Module 2 data with **no API key** —
  good for a first look.
- For a real PDF: switch **Input** to "Upload PDF" in the sidebar, paste a **Claude API key**,
  upload a curriculum **PDF**, and (optionally) tick **"use bundled TEKS Mathematics framework"**.
- To skip extraction and load an already-built graph directly, switch **Input** to "Load
  existing graph (JSONL)" and point it at a `nodes.jsonl`/`relationships.jsonl` pair.
- Every "Build graph" click loads the result into Neo4j (`MERGE`d by `identifier`, so rebuilding
  never duplicates) and indexes lesson/activity content into an in-memory Qdrant collection.
- Open the **💬 Chat** tab to test teacher questions — Claude gets both a `graph_query` (Cypher
  against Neo4j) and a `semantic_search` (Qdrant) tool and decides itself which to call; the
  "🔎 How this was answered" expander under each answer shows what it actually did.

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io) → **New app** → pick this repo,
   main file `app.py`.
3. (Optional) In the app's **Settings → Secrets**, add your key so users don't have to paste it:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. Deploy. The bundled `teks_math_case.json` ships with the app, so the framework works
   out of the box.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI — upload/load, build, **Chat** (agent tool-use loop), download |
| `pdf_to_kg.py` | LLM PDF → graph extractor (`load_case`, `build_graph`, `extract_chunk`); now also captures page-level metadata (`page_label`, `pdf_page`, `lesson_phase`, `materials_needed`) |
| `graph_store.py` | Neo4j load (`MERGE`-by-`identifier`, idempotent) + read-only Cypher execution |
| `semantic_index.py` | Qdrant (in-memory, FastEmbed) indexing + semantic search over lesson/activity content |
| `build_kg.py` | Hand-authored graph-builder reference (no LLM) |
| `case_from_graph.py` | Utility: extract a TEKS CASE file from an existing Learning Commons graph (needs the big `nodes.jsonl`/`relationships.jsonl`, which are **not** in this repo) |
| `teks_math_case.json` | Bundled Texas math TEKS framework (1,247 standards) |

## Standards / model

- Default model: `claude-opus-4-8` (change `MODEL` in `pdf_to_kg.py`).
- The Chat answers are **grounded** through tool calls, not a stuffed-context prompt: Claude
  writes its own Cypher against Neo4j and/or searches Qdrant semantically, and is told to say
  so plainly (rather than invent content) when both tools come up empty.

## Attribution

The bundled TEKS framework (`teks_math_case.json`) is derived from a Learning Commons
knowledge graph provided under **CC BY-4.0**; TEKS standards courtesy of the Texas Education
Agency via 1EdTech CASE. Curriculum PDFs are **not** included (copyright).
