#!/usr/bin/env python3
"""
batch_pipeline.py — shared submit/build logic for the text-first Claude Batch API ingestion
pipeline, factored out of submit_batch_g8.py/build_batch_g8_course.py and
submit_batch_v3.py/build_batch_v3_course.py once both courses turned out to need the exact same
pdfium-extract -> batch -> poll -> combine_modules() steps, differing only in course name, module
PDF paths, and output filenames.

build_course_from_batch() also runs the three deterministic post-processing fixes
(backfill_page_labels, backfill_sibling_labels, backfill_page_rollups) in-memory before writing
the course's *_nodes.jsonl/*_relationships.jsonl -- a newly-built course comes out already
corrected, with no separate manual backfill step. Those three scripts remain independently runnable
too (their standalone COURSES auto-discovery loop still works, e.g. to re-run all courses after a
regex fix), they're just no longer required after a fresh build.

A new course (e.g. Grade 6, Grade 7) needs two small files, not two ~130-line copies:

    submit_batch_g6.py:
        import batch_pipeline as bp
        BASE = ".../SM G6: Teacher Editions/Teacher Edition by Module/"
        MODULES = bp.discover_modules(BASE)  # {"Module 1": ".../Module 1.pdf", ...}, no need to
                                              # hand-type paths or know the module count up front
        COURSE_NODES_PATH = "data/grade6_nodes.jsonl"  # lets backfill_page_labels.py auto-discover
        STATE_PATH = "data/batch_state/batch_g6_state.json"
        if __name__ == "__main__":
            bp.submit_course_batch("g6", MODULES, STATE_PATH)

    build_batch_g6_course.py:
        import batch_pipeline as bp
        from submit_batch_g6 import MODULES, STATE_PATH
        BASE = ".../SM G6: Teacher Editions/Teacher Edition by Module/"
        MODULE_PDFS = MODULES  # same dict already has module -> pdf path
        if __name__ == "__main__":
            bp.build_course_from_batch(STATE_PATH, MODULE_PDFS, course_name="Grade 6 Math",
                                        out_stem="data/grade6")

Both submit_batch_g8.py/submit_batch_v3.py and build_batch_g8_course.py/build_batch_v3_course.py
have been rewired onto this module (same behavior, verified against their pre-refactor output
shape) -- they're the reference examples for a new course's two files.

Why two files and not one shared "run everything" script: submit and build are separate process
invocations on purpose (the batch takes hours; you submit, walk away, and poll/build later, often
in a different terminal session or after a resubmit). Why per-course files at all rather than one
generic parametrized script: backfill_page_labels.py's discover_courses() auto-detects every new
course by importing every submit_batch_*.py in this directory and reading its module-level
MODULES + COURSE_NODES_PATH -- a real file per course, however thin, is what makes that automatic.

Usage: not run directly -- imported by each course's submit_batch_<course>.py /
build_batch_<course>_course.py.
"""
import glob
import json
import os
import re
import sys
import time

import anthropic
from dotenv import load_dotenv

import citation
import pdf_to_kg as p2k
import real_content as rc

load_dotenv()  # reads .env into os.environ if present -- ANTHROPIC_API_KEY, etc, so
# submit_batch_*.py/build_batch_*_course.py work standalone (e.g. `python3 submit_batch_g5.py`)
# without requiring the shell to already have the key exported; app.py does the same for Streamlit.

CASE_PATH = os.path.join(os.path.dirname(__file__), "teks_math_case.json")
# Renamed from the old /tmp/docling_cache -- deliberately a fresh path, not just a fresh dir under
# the old name: any pre-existing /tmp/docling_cache/*.json is docling-sourced (pre-dates the
# pypdfium2 switch) and must NOT be picked up as a cache hit by cached_page_text() below, or a
# resubmit would silently feed Claude the same truncated text this fix exists to avoid.
CACHE_DIR = "/tmp/page_text_cache"
POLL_INTERVAL = 30

# The full set of non-lesson componentRole values a supplementary node can carry (pdf_to_kg.py's
# build_graph()._SUPP_ROLE) -- used only for the informational "n_supp" print line below, not for
# anything that affects the written graph.
SUPPLEMENTARY_ROLES = ("Assessment Summary", "Pacing Guide", "Self-Reflection", "Overview",
                        "Discourse Questions", "Differentiation Strategy")


def discover_modules(base_dir, pattern=r"Module\s+(\d+)", recursive=False):
    """{"Module N": pdf_path}, built by globbing every *.pdf directly under `base_dir` (or, with
    recursive=True, anywhere under it -- some sources put each module PDF in its own subfolder,
    e.g. Grade 5's "M G5 Module 1: .../<pdf>" layout, one level deeper than G6/7/8/Algebra I's
    flat "all module PDFs in one folder" layout) and pulling the module number out of each
    filename with `pattern` (case-insensitive) -- so a new course's module count/naming doesn't
    need to be hand-typed or guessed, and adding/renumbering module PDFs in that folder is picked
    up automatically on the next run.

    Sorts NUMERICALLY by the extracted module number (not lexicographically by filename -- "Module
    10" would otherwise sort before "Module 2"). Any *.pdf in the folder that doesn't match
    `pattern` is skipped with a warning printed to stderr, not silently dropped.
    """
    modules = {}
    glob_pattern = os.path.join(base_dir, "**", "*.pdf") if recursive else os.path.join(base_dir, "*.pdf")
    for pdf_path in sorted(glob.glob(glob_pattern, recursive=recursive)):
        m = re.search(pattern, os.path.basename(pdf_path), re.IGNORECASE)
        if not m:
            print(f"  WARNING: {pdf_path!r} doesn't match module-number pattern {pattern!r}, "
                  f"skipping", file=sys.stderr)
            continue
        modules[f"Module {int(m.group(1))}"] = pdf_path
    return dict(sorted(modules.items(), key=lambda kv: int(kv[0].split()[1])))


def cached_page_text(module_name, pdf_path, cache_tag):
    """pypdfium2's per-page text (see pdf_to_kg.pdfium_page_text()) is prompt-independent (literal
    page text) -- cache it per (course, module) so a resubmit (e.g. after a stuck/canceled batch)
    never has to redo the conversion. NOT docling: confirmed live that docling silently drops
    content on dense/tabular page layouts (a Pacing Guide page returned 290 chars where the real
    page has 2004) -- fine for a page whose only downstream use is the sourceText/materials_needed
    fields (both get a separate deterministic override that doesn't depend on this text), but not
    for what Claude reads to do the actual structured extraction (titles, standards, outcome
    details) -- Claude can't extract from content it never saw. Caching matters less for
    pypdfium2's own speed (~11s for a 365-page module vs. docling's ~10-15min) but is still cheap
    to keep, and a resubmit shouldn't re-pay even that."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = f"{CACHE_DIR}/{module_name.replace(' ', '_')}_{cache_tag}.json"
    if os.path.exists(cache_path):
        print(f"  {module_name}: using cached page text ({cache_path})", file=sys.stderr)
        with open(cache_path) as f:
            return {int(k): v for k, v in json.load(f).items()}
    page_text = p2k.pdfium_page_text(pdf_path)
    with open(cache_path, "w") as f:
        json.dump(page_text, f)
    return page_text


def submit_course_batch(cache_tag, modules, state_path, custom_id_prefix="", only=None):
    """Extract every module PDF's page text via pypdfium2 (pdf_to_kg.pdfium_page_text() -- NOT
    docling, see cached_page_text()'s docstring for why), batch pages, and submit ONE Batch API
    call spanning all modules. Saves batch id + custom_id -> (module, first, last) map to
    `state_path` so build_course_from_batch() can poll and assemble results later.

    `cache_tag`: short course tag (e.g. "g6") used to namespace the page-text cache so two
    courses' cached pages never collide.
    `custom_id_prefix`: prepended to each request's custom_id (e.g. "G6") -- only needs to be
    unique enough to be readable in the Anthropic console; doesn't affect parsing since
    custom_id_map (not the string itself) is what build_course_from_batch() keys off of.
    `only`: {module: {(first, last), ...}} -- submit just these batches (a module update: the
    rest reuse saved chunks, see changes.plan_module_update()). With nothing to submit, no API
    call is made and the state records batch_id None.
    """
    # Deferred, like the backfill imports in build_course_from_batch(): lesson_pages imports
    # backfill_page_labels, whose module-level discover_courses() imports every submit_batch_*.py,
    # which import this module -- a top-level import here is circular.
    import lesson_pages

    requests = []
    custom_id_map = {}
    for module_name, pdf_path in modules.items():
        if only is not None and not only.get(module_name):
            continue
        print(f"Converting {module_name} via pypdfium2...", file=sys.stderr)
        page_text = cached_page_text(module_name, pdf_path, cache_tag)
        print(f"  {module_name}: {len(page_text)} pages", file=sys.stderr)
        # Batch edges on lesson boundaries (running headers), not every 25 pages -- see
        # pdf_to_kg.build_lesson_batches(). max_pages=30 keeps batches no bigger on average than
        # the old 25-page windows (22.5 vs 24.1 pages), so output-token headroom is unchanged.
        _labels, lesson_of = lesson_pages.scan_pdf(pdf_path)
        for first, last, labeled_text in p2k.build_lesson_batches(page_text, lesson_of,
                                                                  max_pages=30):
            if only is not None and (first, last) not in only[module_name]:
                continue
            custom_id = f"{custom_id_prefix}{module_name.replace(' ', '')}_{first}_{last}"
            requests.append({
                "custom_id": custom_id,
                "params": p2k.text_message_params(labeled_text, f"pages {first}-{last}"),
            })
            custom_id_map[custom_id] = {"module": module_name, "first": first, "last": last}

    print(f"Total batch requests: {len(requests)}", file=sys.stderr)

    batch_id = None
    if requests:
        client = anthropic.Anthropic()
        batch = client.messages.batches.create(requests=requests)
        batch_id = batch.id
        print(f"Submitted batch {batch.id}, status={batch.processing_status}", file=sys.stderr)

    # data/batch_state/, not /tmp: the state files for every course built so far lived in /tmp
    # and were lost, so none of those courses can be rebuilt from their (paid-for) batch results.
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    with open(state_path, "w") as f:
        json.dump({"batch_id": batch_id, "custom_id_map": custom_id_map}, f)
    print(f"State saved to {state_path}", file=sys.stderr)
    return batch_id


def _wait_for_batch(client, batch_id):
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        print(f"  status={batch.processing_status} counts={batch.request_counts}",
              file=sys.stderr)
        if batch.processing_status == "ended":
            return batch
        time.sleep(POLL_INTERVAL)


def build_course_from_batch(state_path, module_pdfs, course_name, out_stem,
                             jurisdiction="Texas", framework="TEKS", case_path=CASE_PATH):
    """Poll the batch saved at `state_path`, collect results per module, merge, and build the
    Course graph (collect_batch_chunks() + build_course()).

    `module_pdfs`: {module name: pdf path}, same dict the submit step used (order is the order
    modules get combined/hasPart-linked in).
    `out_stem`: e.g. "data/grade6" -> writes data/grade6_nodes.jsonl + _relationships.jsonl, and
    "grade6" (its basename) becomes the course_slug half of every node's citation.pdf_key_for_
    module() key -- e.g. "grade6/Module 1.pdf" -- so it must stay stable for a course once chosen
    (rebuilding under a different out_stem changes every node's citation key)."""
    chunks_by_module, _errors = collect_batch_chunks(state_path, list(module_pdfs))
    return build_course(chunks_by_module, module_pdfs, course_name,
                        f"{out_stem}_nodes.jsonl", f"{out_stem}_relationships.jsonl",
                        course_slug=os.path.basename(out_stem), jurisdiction=jurisdiction,
                        framework=framework, case_path=case_path)


def save_chunks(chunks_by_module, extraction_root):
    """Keep every raw extraction result: <extraction_root>/<module>/chunk_<first>_<last>.json.
    They are the only way to rebuild a version without paying for extraction again, and a module
    update reuses the saved chunks of every page that didn't change."""
    for module, chunks in chunks_by_module.items():
        mdir = os.path.join(extraction_root, module.replace(" ", "_"))
        os.makedirs(mdir, exist_ok=True)
        for c in chunks:
            first, last = c["_core_pages"]
            with open(os.path.join(mdir, f"chunk_{first:04d}_{last:04d}.json"), "w") as f:
                json.dump(c, f)


def load_saved_chunks(extraction_root, module):
    """Saved chunks of one module, in page order (see save_chunks())."""
    mdir = os.path.join(extraction_root, module.replace(" ", "_"))
    chunks = [json.load(open(p)) for p in glob.glob(os.path.join(mdir, "chunk_*.json"))]
    return sorted(chunks, key=lambda c: c["_core_pages"][0])


def collect_batch_chunks(state_path, modules):
    """Poll the batch saved at `state_path` until it ends, then ({module: [chunk, ...] in page
    order}, [(custom_id, error), ...]). Each chunk carries `_core_pages`."""
    with open(state_path) as f:
        state = json.load(f)
    batch_id, custom_id_map = state["batch_id"], state["custom_id_map"]
    chunks_by_module = {m: [] for m in modules}
    errors = []
    if batch_id is None:   # nothing was submitted (every batch reused a saved chunk)
        return chunks_by_module, errors

    client = anthropic.Anthropic()
    print(f"Polling batch {batch_id}...", file=sys.stderr)
    _wait_for_batch(client, batch_id)

    for entry in client.messages.batches.results(batch_id):
        info = custom_id_map.get(entry.custom_id)
        if not info:
            continue
        module = info["module"]
        if entry.result.type != "succeeded":
            errors.append((entry.custom_id, entry.result.type))
            continue
        try:
            chunk = p2k.parse_extraction(entry.result.message)
            # lets merge() drop what the model extracted from the batch's leading overlap pages
            chunk["_core_pages"] = [info["first"], info["last"]]
            chunks_by_module[module].append(chunk)
        except Exception as e:
            errors.append((entry.custom_id, f"parse error: {e}"))

    for module in modules:
        # Batch results come back in arbitrary order, and merge() is order-sensitive (first/last
        # non-empty values win, blank-topic reattachment) -- always merge in page order.
        chunks_by_module[module].sort(key=lambda c: c["_core_pages"][0])
        print(f"  {module}: {len(chunks_by_module[module])} batches succeeded", file=sys.stderr)
    if errors:
        print(f"  {len(errors)} batch(es) failed:", file=sys.stderr)
        for cid, reason in errors:
            print(f"    {cid}: {reason}", file=sys.stderr)
    return chunks_by_module, errors


def build_course(chunks_by_module, module_pdfs, course_name, nodes_path, rels_path, course_slug,
                 jurisdiction="Texas", framework="TEKS", case_path=CASE_PATH):
    """Merge extraction chunks ({module: [chunk, ...]}, from a batch and/or saved chunks) and build
    the Course graph -- materials_needed overridden with real_content.find_real_materials() and
    sourceText overridden with real page text via pypdfium2 (p2k.pdfium_page_text() -- NOT
    docling; see build_graph()'s docstring for why) -- then run the post-processing, repairs and
    checks and write `nodes_path` / `rels_path`. `course_slug` is the citation-key segment
    (citation.pdf_key_for_module()) and must stay fixed for a curriculum."""
    modules = list(module_pdfs.keys())
    case = p2k.load_case(json.load(open(case_path)))
    print(f"Loaded CASE framework: {len(case['by_uuid'])} standards, "
          f"{len(case['assocs'])} links", file=sys.stderr)

    print("Parsing real materials_needed from each module's Pacing Guide pages...",
          file=sys.stderr)
    real_materials_list = []
    for module in modules:
        materials = rc.find_real_materials(module_pdfs[module])
        n_lessons = sum(len(v) for v in materials.values())
        print(f"  {module}: {len(materials)} topic(s), {n_lessons} lesson materials lists",
              file=sys.stderr)
        real_materials_list.append(materials)

    print("Reading real page text per module (for sourceText, via pypdfium2 -- see "
          "build_graph's docstring for why not docling here)...", file=sys.stderr)
    real_pages_list = []
    for module in modules:
        pages = p2k.pdfium_page_text(module_pdfs[module])
        print(f"  {module}: {len(pages)} pages", file=sys.stderr)
        real_pages_list.append(pages)

    source_pdf_keys = [citation.pdf_key_for_module(course_slug, module_pdfs[m]) for m in modules]

    chunk_lists = [chunks_by_module[m] for m in modules]
    nodes, rels = p2k.combine_modules(chunk_lists, jurisdiction, framework, case,
                                       course_name=course_name,
                                       real_materials_list=real_materials_list,
                                       real_pages_list=real_pages_list,
                                       source_pdf_keys=source_pdf_keys)

    n_lc = sum(1 for n in nodes if n["labels"][0] == "LearningComponent")
    n_supp = sum(1 for n in nodes if n["labels"][0] == "LearningComponent"
                 and n["properties"].get("componentRole") in SUPPLEMENTARY_ROLES)
    print(f"Built graph: {len(nodes)} nodes, {len(rels)} rels "
          f"({n_lc} LearningComponent, {n_supp} from supplementary)", file=sys.stderr)

    print("\nPost-processing (page labels, sibling labels, page rollups)...", file=sys.stderr)
    # Deferred imports: backfill_page_labels.py's own module-level `COURSES = discover_courses()`
    # re-imports every submit_batch_*.py in this directory -- including whichever one is currently
    # importing batch_pipeline to get here. Importing it at the top of this file would make that
    # re-import happen while batch_pipeline itself is still mid-load (a real circular-import
    # hazard); deferring it to inside this function means batch_pipeline is already fully loaded
    # by the time it happens.
    from backfill_page_labels import apply as apply_page_labels
    from backfill_sibling_labels import apply as apply_sibling_labels
    from backfill_page_rollups import apply as apply_page_rollups
    from backfill_topic_citation import apply as apply_topic_citation
    from check_cycles import find_cycles
    from check_orphans import find_orphans, report as report_orphans
    from find_multi_parent_nodes import find_multi_parents, report as report_multi_parents
    import fix_misattributed_topics
    import fix_multi_parent_nodes
    import repair_duplicate_topics
    from snapshot import snapshot_course

    os.makedirs(os.path.dirname(os.path.abspath(nodes_path)), exist_ok=True)

    print("  fixing page labels off the real PDFs...", file=sys.stderr)
    apply_page_labels(nodes, rels, module_pdfs)
    print("  rescuing blanks from same-page siblings...", file=sys.stderr)
    apply_sibling_labels(nodes, rels)
    print("  recomputing Lesson/LessonGrouping page ranges (running headers)...", file=sys.stderr)
    apply_page_rollups(nodes, rels, module_pdfs)
    # The structural repairs used to be run by hand with --write on the built files, so a rebuild
    # silently dropped them. All three are idempotent (no-ops on an already-repaired course,
    # verified on every course in data/), so they run on every build. They need the page ranges
    # above, and they move lessons between topics -- so ranges are recomputed after them too
    # (lesson_pages.scan_pdf() is cached, so the PDFs aren't read twice).
    print("  merging blank-title topics / removing empty duplicate lessons...", file=sys.stderr)
    repair_duplicate_topics.apply(nodes, rels)
    print("  reassigning misattributed lesson ranges...", file=sys.stderr)
    fix_misattributed_topics.apply(nodes, rels)
    print("  applying hand-verified multi-parent fixes...", file=sys.stderr)
    fix_multi_parent_nodes.apply(nodes, rels,
                                 fix_multi_parent_nodes.RESOLUTIONS.get(os.path.relpath(nodes_path), []))
    print("  recomputing page ranges after repairs...", file=sys.stderr)
    apply_page_rollups(nodes, rels, module_pdfs)
    print("  precomputing Module/Topic citation targets...", file=sys.stderr)
    apply_topic_citation(nodes, rels)
    # Detection-only safety nets -- never block the build: the right fix for each needs a person
    # reading the PDF page (see each script's docstring), not something to fail a build over.
    report_orphans(nodes_path, find_orphans(nodes, rels))
    report_multi_parents(nodes_path, find_multi_parents(nodes, rels))
    node_ids = {n["identifier"] for n in nodes}
    dangling = sum(1 for r in rels
                   if r["source_identifier"] not in node_ids or r["target_identifier"] not in node_ids)
    if dangling:
        print(f"  WARNING: {dangling} edge(s) point at a node not in this course (never loaded "
              f"into Neo4j)", file=sys.stderr)
    for edge_type in ("hasPart", "hasChild", "buildsTowards"):
        cycles = find_cycles(rels, edge_type)
        if cycles:
            print(f"  WARNING: {len(cycles)} {edge_type} cycle(s) -- run check_cycles.py for "
                  f"details", file=sys.stderr)

    snapshot_course(nodes_path, f"before-rebuild-{course_slug}")
    with open(nodes_path, "w") as f:
        for n in nodes:
            f.write(json.dumps(n) + "\n")
    with open(rels_path, "w") as f:
        for r in rels:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(nodes)} nodes / {len(rels)} rels to {nodes_path} / {rels_path}",
          file=sys.stderr)
    return nodes_path, rels_path
