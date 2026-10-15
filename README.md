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

1. **Neo4j (local).** The app *loads* curricula into the Neo4j that `NEO4J_URI` points at, on
   start-up and whenever something is published — keep it on the local one:
   ```bash
   docker compose up -d          # Neo4j on bolt://localhost:7687, password changeme-local-pw
   ```
2. **`.env`** (copy `.env.example`): `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (search embeddings),
   `NEO4J_URI=bolt://localhost:7687` (+ user/password), `ADMIN_USERS=<your name>,...`.
3. **Install and run:**
   ```bash
   pip install -r requirements.txt
   python3 import_existing.py    # only on a fresh checkout / empty data/app.db: registers the
                                 # existing courses from registry_seed.json
   streamlit run app.py          # http://localhost:8501
   ```
   The first start prepares the library: the local search index gets each point's curriculum
   scope (a copy is kept at `data/qdrant_storage.before-scopes`, no re-embedding) and every base
   curriculum is loaded into Neo4j.

### Using the app

Sign in with your name (sidebar). Everything else happens in three pages:

| Page | Who | What |
|---|---|---|
| **Chat** (`app.py`) | everyone | Pick a curriculum — one of yours or a base — and ask; answers come only from that curriculum and cite the book's pages. "🔎 How this was answered" shows the tool calls. |
| **My Curriculum** | everyone | Your own curricula, visible only to you: copy a base or start empty, add modules / topics / lessons from any base of the same subject, add your own modules/topics, rename / move / reorder / remove, edit lesson details and activity text, add or remove standards, undo/redo, standards coverage (gaps / overlaps), upgrade to a newer base version, **Save & publish** (makes the chat use your latest version). |
| **Library** | admins (`ADMIN_USERS`) | Base curricula: add one from its module PDFs (subject, standards framework, publisher layout), submit extraction (Claude Batch API), build (background), **Publish to chat**; update modules with new PDFs — "Analyze changes" shows pages/lessons changed and the number of extraction calls before anything is paid for. |
| **Page Inspector** | admins | QA: a PDF page next to what Neo4j and the search index hold for it. |

New subjects need no code: a subject profile (`profiles/subjects/<id>.yaml`), a standards
framework (`frameworks/<id>/`), and for unknown publishers the generic layout profile. How the
pipeline works step by step is in [flow.md](flow.md).

## Standards / model

- Default model: `claude-opus-4-8` (change `MODEL` in `pdf_to_kg.py`).
- Prerequisites come from the curriculum's standards framework (math: TEKS → Common Core
  crosswalk → `buildsTowards`).
- The Chat answers are **grounded** through tool calls, not a stuffed-context prompt: Claude
  writes its own Cypher against Neo4j and/or searches Qdrant semantically, and is told to say
  so plainly (rather than invent content) when both tools come up empty.

## Attribution

The bundled TEKS frameworks (`frameworks/teks_math`, `frameworks/teks_ela`) is derived from a Learning Commons
knowledge graph provided under **CC BY-4.0**; TEKS standards courtesy of the Texas Education
Agency via 1EdTech CASE. Curriculum PDFs are **not** included (copyright).
