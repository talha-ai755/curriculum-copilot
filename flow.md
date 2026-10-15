# Pipeline flow

How a module PDF becomes the graph the copilot answers from, and which script owns each step.
The README covers setup; this file covers the order things run in and why.

```
 registry (data/app.db) ── curricula, versions, module PDFs, jobs
        │
 module PDFs ──► 1. submit ──► Claude Batch API ──► 2. build ──► data/curricula/<id>/v<n>/
 (teacher eds)    pdfium text                        merge + graph       nodes / relationships
                  lesson-aligned batches             post-process        extractions/ (raw chunks)
                                                     checks, diff
                                                                       ▼
                                          3. load ──► Neo4j (structure) + Qdrant (text), scoped
                                                                       │
                                                                       ▼
                                          4. serve ──► app.py ──► agent.py tool loop ──► answer
```

Ingestion is driven by `ingest.py` (CLI now; the Streamlit Library page in Phase 4):
`create` registers a base curriculum + version 1 with its module PDFs → `submit` → `build`.
An update is `plan_update` (change report + number of extraction calls, no cost) →
`create_update_version` → `submit(only=...)` → `build` (see "Updating a curriculum" below).
The 10 courses ingested before the registry existed are base version 1, pointing at their
original `data/<course>_*.jsonl` files (`import_existing.py` restores them from the committed
`registry_seed.json` if `data/app.db` is lost).

## 1. Submit extraction — `ingest.submit()`

- The curriculum's modules and PDFs come from the registry (`registry.py`);
  `backfill_page_labels.discover_courses()` lists the latest ready version of every base.
- `batch_pipeline.submit_course_batch()`:
  - reads each PDF's text per page with pypdfium2 (`pdf_to_kg.pdfium_page_text`, cached in
    `/tmp/page_text_cache`);
  - cuts it into batches that end on lesson boundaries (`pdf_to_kg.build_lesson_batches`, at most
    30 pages, lessons found from the running headers by `lesson_pages.py`; intro pages travel with
    the lesson after them), with a 3-page overlap, each page labelled `=== PDF PAGE N ===`. Fixed
    25-page windows split 572 of 1,068 lessons; this splits only the 63 lessons longer than 30
    pages on their own (front matter gets its own batch rather than splitting Lesson 1), and a batch never mixes
    two topics (`lesson_pages.topic_of_pages`). PDFs without recognizable headers fall back to fixed 25-page windows;
  - sends one Batch API request per batch with the structured-output schema
    (`pdf_to_kg.EXTRACTION_SCHEMA`);
  - saves the batch id and the `custom_id` map to the version folder (`batch_state.json`).

## 2. Build the course — `ingest.build()`

`batch_pipeline.collect_batch_chunks()` polls the batch; every raw chunk is saved under the
version's `extractions/` (`save_chunks`); `batch_pipeline.build_course()` then runs, in order:

| Step | Code | What it does |
|---|---|---|
| Merge batches | `pdf_to_kg.merge` | Batches in page order; drops what each batch extracted from its overlap pages (`_core_pages`); folds per-batch JSON into one module; reattaches lessons from headless batches to the topic that already has that lesson number, else by lesson-number continuity |
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

## 3. Publish to the stores — `publish.py`

Every curriculum lives in Neo4j and Qdrant under its own **scope**: `base:<id>@v<n>` for a base
curriculum version, `view:<id>` for a user's own curriculum, `framework:<id>` for the shared
standards.

| What | How | Notes |
|---|---|---|
| Base version | Library → **Publish to chat** (`publish.publish_base`) | Replaces the curriculum's previous version in both stores. Search vectors are reused for unchanged text (`semantic_index.index_scope`), so only new text is embedded |
| User's curriculum | My Curriculum → **Save & publish**, or automatically on first chat use (`publish.publish_view`) | `views.materialize` replays the user's edit log into a self-contained graph (view-scoped ids, citations still pointing at each source book); usually 0 embedding calls |
| App start-up | `ui_common.prepare_library` | Loads every base into Neo4j (MERGE); one-time scope tagging of an index built before scopes existed (backup at `data/qdrant_storage.before-scopes`) |
| Neo4j, exact local mirror | `NEO4J_URI=bolt://localhost:7687 python3 reload_neo4j.py [--apply]` | Also deletes nodes/edges no longer in the registry's graphs. Refuses any non-local `NEO4J_URI` |

## 4. Serve — `app.py` → `agent.py`

- The chat works on one curriculum at a time: `agent.build_system_prompt(..., curriculum=)`
  names it, every `graph_query` gets a `$scope` parameter the model must filter curriculum nodes
  by, and `semantic_search` is filtered to that scope in code.
- `agent.answer_with_tools()` runs a bounded Claude tool loop (max 10 turns) with three tools:
  `graph_query` (read-only Cypher, guarded by `graph_store.is_read_only`), `semantic_search`
  (Qdrant), and `get_content` (fetch text by identifier).
- Citations are built from `sourcePdfKey` + `pdfPage` and `PDF_BASE_URL` (`citation.py`;
  `build_pdf_mirror.py` serves the PDFs locally).
- `pages/3_Page_Inspector.py` (admins) shows a PDF page next to its live Neo4j and Qdrant data.

## Users' own curricula — `views.py`

A view pins base curricula of one subject at specific versions and stores an ordered edit log
(fork, include module/topic/lesson, own module/topic, remove, move, edit field or content text,
add/remove standards) with undo/redo. Edits that no longer apply (e.g. after upgrading a pinned
base) are listed as conflicts and skipped, never guessed. `views.coverage` compares the standards
the view teaches with those its source curricula teach (gaps / overlaps).

## Subjects and publishers — `profiles.py`, `frameworks.py`

`profiles/subjects/<id>.yaml`: academic subject, default framework, extraction prompt, lesson
phases and supplementary-content types (the schema's enums), Pacing-Guide materials parsing.
Math reproduces the original prompt exactly; other subjects use
`profiles/prompts/generic_teacher_edition.txt`. `profiles/publishers/<id>.yaml`: page-header
format (`bluebonnet` → lesson-aligned batches and header page ranges; `none` → fixed 25-page
batches). `frameworks/<id>/`: `meta.yaml` + a CASE file (`teks_math`, `teks_ela`).

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

## Updating a curriculum (new PDF version)

`ingest.plan_update(curriculum, {module: new_pdf})` works out what changed before any paid call
(`changes.py`): file hash (identical PDF → nothing to do), per-page text hashes aligned with
difflib (inserted/removed pages that shift numbering are handled), changed pages mapped to
lessons through the running headers, and per lesson-aligned batch whether a saved chunk can be
reused (page numbers remapped) or the batch must be re-extracted. The report lists pages and
lessons changed and the number of extraction calls. `create_update_version` copies the reusable
chunks into the new version, `submit(only=...)` extracts the rest, `build` merges both and stores
`changes.diff_graphs` (added / removed / changed nodes vs the previous version).
LearningComponent ids are stable (module + content hash, not list position or page), so unchanged
content keeps its id across versions. Courses imported as version 1 have no saved chunks: their
first update re-extracts every batch (the plan says so).
