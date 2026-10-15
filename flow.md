# Pipeline flow

How a module PDF becomes the graph the copilot answers from, and which script owns each step.
The README covers setup; this file covers the order things run in and why.

```
 module PDFs ──► 1. submit ──► Claude Batch API ──► 2. build ──► data/<course>_*.jsonl
 (teacher eds)    pdfium text                        merge + graph     │   ▲
                  25-page batches                    post-process      │   └─ snapshot.py
                                                     checks            │      (before every write)
                                                                       ▼
                                          3. load ──► Neo4j (structure) + Qdrant (text)
                                                                       │
                                                                       ▼
                                          4. serve ──► app.py ──► agent.py tool loop ──► answer
```

## 1. Submit extraction — `submit_batch_<course>.py`

- Each course has a thin config: `MODULES` (module name → PDF path), `COURSE_NODES_PATH`,
  `STATE_PATH`. `backfill_page_labels.discover_courses()` finds every course through these files.
- `batch_pipeline.submit_course_batch()`:
  - reads each PDF's text per page with pypdfium2 (`pdf_to_kg.pdfium_page_text`, cached in
    `/tmp/page_text_cache`);
  - cuts it into batches that end on lesson boundaries (`pdf_to_kg.build_lesson_batches`, at most
    30 pages, lessons found from the running headers by `lesson_pages.py`; intro pages travel with
    the lesson after them), with a 3-page overlap, each page labelled `=== PDF PAGE N ===`. Fixed
    25-page windows split 572 of 1,068 lessons; this splits 124, 63 of them longer than 30 pages
    on their own. PDFs without recognizable headers fall back to fixed 25-page windows;
  - sends one Batch API request per batch with the structured-output schema
    (`pdf_to_kg.EXTRACTION_SCHEMA`);
  - saves the batch id and the `custom_id` map to `data/batch_state/` — keep these files: they
    are the only way to rebuild a course from its (paid-for) results.

## 2. Build the course — `build_batch_<course>_course.py`

`batch_pipeline.build_course_from_batch()` polls the batch, then runs, in order:

| Step | Code | What it does |
|---|---|---|
| Merge batches | `pdf_to_kg.merge` | Folds per-batch JSON into one module; dedupes overlap pages; reattaches blank-title topic continuations |
| Build graph | `pdf_to_kg.combine_modules` / `build_graph` | Deterministic UUID5 nodes and edges; aligns to the CASE standards (`teks_math_case.json`); overrides materials (`real_content.find_real_materials`) and single-node `sourceText` with real page text |
| Page labels | `backfill_page_labels.apply` | Overwrites every LearningComponent's `pageLabel` with the label printed on its PDF page |
| Sibling labels | `backfill_sibling_labels.apply` | Fills remaining blank labels from a same-page sibling |
| Page ranges | `backfill_page_rollups.apply(…, module_pdfs)` | Lesson ranges from the PDF's running headers (`lesson_pages.py`); Topics/Modules span their lessons and content |
| Structural repairs | `repair_duplicate_topics`, `fix_misattributed_topics`, `fix_multi_parent_nodes` | Blank-topic merges, empty duplicate lessons, misattributed lesson ranges, hand-verified multi-parent fixes. All idempotent |
| Page ranges again | `backfill_page_rollups.apply` | The repairs move lessons between topics (PDF scans are cached, not re-read) |
| Citations | `backfill_topic_citation.apply` | `citationSourcePdfKey` / `citationPdfPage` on every Module/Topic |
| Checks (report only) | `check_orphans`, `find_multi_parent_nodes`, `check_cycles`, dangling-edge count | Problems needing a person to read the PDF; never fail the build |
| Write | `snapshot.snapshot_course` then write | Previous files are snapshotted to `data/snapshots/` first |

Output: `data/<course>_nodes.jsonl` + `data/<course>_relationships.jsonl`. `data/` is gitignored
(verbatim teacher-edition text must not go into the public repo), so `snapshot.py` is its only
history: `python3 snapshot.py list | verify <name> | restore <name>`.

### How lesson page ranges are found (`lesson_pages.py`)

Every lesson page names its lesson in a running header or footer:

- K-5: `BLUEBONNET LEARNING K–5 MATH Lesson 1 Homework` or a `Lesson 14: 169` running line
  (the running line wins when both appear — a lesson can print the next lesson's Sprint);
  opener pages start with a bare `Lesson 10` line followed by `Objective:`.
- Grades 6-8 / Algebra I: `MODULE 1 • TOPIC 1 • LESSON 1` (sometimes doubled and interleaved).

Answer Key pages are skipped. A lesson's range is its longest run of tagged pages, continuing
across up to 8 untagged pages (facsimile student pages have no header) as long as no other
lesson's page lies in between. Lessons the headers don't cover keep their existing range if it
holds all their content, and only then fall back to the LearningComponent rollup.

## 3. Load the stores

| Store | Command | Notes |
|---|---|---|
| Neo4j | `app.py` on start-up (`graph_store.load_graph`, MERGE only) | Never deletes. Long text (`sourceText`, `description`) is kept out of Neo4j |
| Neo4j, exact mirror | `NEO4J_URI=bolt://localhost:7687 python3 reload_neo4j.py [--apply]` | Also deletes nodes/edges no longer in `data/`. Refuses any non-local `NEO4J_URI` |
| Qdrant | `python3 build_content_index.py` | Re-embeds every LearningComponent/Assessment with OpenAI; costs money per run |

## 4. Serve — `app.py` → `agent.py`

- `agent.answer_with_tools()` runs a bounded Claude tool loop (max 10 turns) with three tools:
  `graph_query` (read-only Cypher, guarded by `graph_store.is_read_only`), `semantic_search`
  (Qdrant), and `get_content` (fetch text by identifier).
- Citations are built from `sourcePdfKey` + `pdfPage` and `PDF_BASE_URL` (`citation.py`;
  `build_pdf_mirror.py` serves the PDFs locally).
- `pages/Page_Inspector.py` shows a PDF page next to its live Neo4j and Qdrant data, for QA.

## Maintenance on already-built courses

Run these against the existing `data/` files, without a rebuild. Each one is a dry run until
`--write` and snapshots first:

```
python3 backfill_page_rollups.py      # lesson/topic page ranges (reads PDFs)
python3 backfill_topic_citation.py    # module/topic citation targets
python3 repair_duplicate_topics.py    # + fix_misattributed_topics.py, fix_multi_parent_nodes.py
python3 check_orphans.py; python3 find_multi_parent_nodes.py; python3 check_cycles.py
python3 graph_bench.py [--run]        # questions with known answers; --run scores the agent
```

After any `--write`: reload Neo4j (`reload_neo4j.py --apply` locally) and, if text changed,
rebuild Qdrant.
