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

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501.

- **Demo mode** (default ON) builds a graph from bundled Module 2 data with **no API key** —
  good for a first look.
- For a real PDF: turn Demo mode off, paste a **Claude API key** in the sidebar, upload a
  curriculum **PDF**, and (optionally) tick **"use bundled TEKS Mathematics framework"**.
- Open the **💬 Chat** tab to test teacher questions.

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
| `app.py` | Streamlit UI — upload, build, **Chat** (test), download |
| `pdf_to_kg.py` | LLM PDF → graph extractor (`load_case`, `build_graph`, `extract_chunk`) |
| `build_kg.py` | Hand-authored graph-builder reference (no LLM) |
| `case_from_graph.py` | Utility: extract a TEKS CASE file from an existing Learning Commons graph (needs the big `nodes.jsonl`/`relationships.jsonl`, which are **not** in this repo) |
| `teks_math_case.json` | Bundled Texas math TEKS framework (1,247 standards) |

## Standards / model

- Default model: `claude-opus-4-8` (change `MODEL` in `pdf_to_kg.py`).
- The Chat answers are **grounded** — if the graph has alignment but not the raw lesson text
  (e.g. bellwork problems, sentence frames), it says so rather than inventing content.

## Attribution

The bundled TEKS framework (`teks_math_case.json`) is derived from a Learning Commons
knowledge graph provided under **CC BY-4.0**; TEKS standards courtesy of the Texas Education
Agency via 1EdTech CASE. Curriculum PDFs are **not** included (copyright).
