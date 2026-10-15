#!/usr/bin/env python3
"""
backfill_page_labels.py — deterministic fix for Claude's unreliable page_label reads: it reliably
captures pdfPage (the real, docling-echoed page number) but often leaves page_label BLANK for
supplementary/non-standard content (Discourse Questions, Differentiation Strategy, Essential
Ideas boxes, etc.), and occasionally reports a WRONG page_label -- a real label copied from a
different page than the one it's actually describing (e.g. a node genuinely on printed page 511
self-reporting "433G", which collided with an unrelated node genuinely on 433G).

Fix: read every page's printed label directly off the real PDF (same header/footer pattern
verified all session: "<label> MODULE N..." on verso pages, "MODULE N...<label>" on recto pages),
build a pdfPage -> label map per module, and OVERWRITE every LearningComponent's pageLabel with
that deterministic value whenever its pdfPage has a known label -- not just filling blanks. The
PDF-read map is authoritative; Claude's self-reported label is not trusted either way. Purely
deterministic -- no re-extraction, no Claude call.

COURSES is auto-discovered (see discover_courses() below) from every submit_batch_*.py in this
directory that declares MODULES (module name -> pdf path) and COURSE_NODES_PATH (its
data/*_nodes.jsonl output) -- both already exist in those scripts for the Batch API submission
itself, so a newly-ingested course needs no separate registration here: write its
submit_batch_<course>.py the same way as the existing ones (MODULES + COURSE_NODES_PATH already
required for extraction), and this script picks it up automatically on its next run.
Safe to re-run on already-fixed courses too -- every run recomputes every label from the PDF
regardless of the node's current value, so it's idempotent. Cheap (a few minutes of PDF reading,
no API calls) -- worth running as a standard last step every time a new course is ingested.

Uses pypdfium2 (already a project dependency, for Page Inspector's rendering), not pypdf/
pdfplumber: both were confirmed live to fail on Grade 6 Module 1 when reading many pages
sequentially from one session -- pypdf.PdfReader.extract_text() hung indefinitely (waited 5
minutes, never returned), and pdfplumber's per-page state grew unboundedly and got the process
OOM-killed at ~19.5GB RSS around page 300. Both are pure-Python PDF parsers (pdfplumber is built
on pdfminer.six); pypdfium2 wraps Google's PDFium (Chrome's production PDF engine) instead, and
extracts the same 365-page module's text in ~11s with ~220MB peak RSS -- no hang, no leak. See
real_content.py's module docstring for the same finding (this build step's sibling PDF-reading
module). Text comes back with \\r\\n line endings rather than pypdf's \\n; the label regexes below
only ever match `\\S`/`\\s` boundaries and `\\n`/`\\Z` anchors, both unaffected by an extra `\\r`
immediately before the `\\n`, and the final capture is `.strip()`-ed before validation either way.

Usage: python3 backfill_page_labels.py
"""
import argparse
import glob
import importlib.util
import json
import re
import sys

import pypdfium2 as pdfium
from snapshot import snapshot_course


def _page_text(page):
    """One page's text via pypdfium2's textpage API -- closed immediately after, so no long-lived
    textpage handles accumulate across a multi-hundred-page document."""
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range() or ""
    finally:
        textpage.close()


def discover_courses():
    """{course's data/*_nodes.jsonl path: {module name: pdf path}}, built by importing every
    submit_batch_*.py in this directory and reading its MODULES + COURSE_NODES_PATH -- so a new
    course is picked up automatically as soon as its ingestion script exists, with no separate
    registration step here to remember."""
    courses = {}
    for script_path in sorted(glob.glob("submit_batch_*.py")):
        spec = importlib.util.spec_from_file_location(script_path[:-3], script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        nodes_path = getattr(mod, "COURSE_NODES_PATH", None)
        module_pdfs = getattr(mod, "MODULES", None)
        if nodes_path and module_pdfs:
            courses[nodes_path] = module_pdfs
    return courses


COURSES = discover_courses()

_LABEL_FIRST = re.compile(r"(?:^|\n)\s*(\S{1,7})\s+MODULE\s+\d+", re.IGNORECASE)
_LABEL_LAST = re.compile(r"MODULE\s+\d+[^\n]*?[•·][^\n]*?[•·][^\n]*?\s(\S{1,7})\s*(?:\n|\Z)", re.IGNORECASE)
_LABEL_LAST_SIMPLE = re.compile(r"MODULE\s+\d+[^\n]{0,80}?\s(\S{1,7})\s*(?:\n|\Z)", re.IGNORECASE)
# A real printed page label always starts with a digit (e.g. "141", "15G", "433G", "911C") or is
# a front-matter "IL-<n>" code -- never a plain word. Pages carrying a "Notes" margin-box heading
# right above the real MODULE/TOPIC header line (Topic Summary, Self-Reflection pages) were
# fooling _LABEL_FIRST into capturing "Notes" itself, and fooling _LABEL_LAST into capturing the
# literal word "SUMMARY" (it happens to fit \S{1,7}) instead of the real label on the other side
# of that line. Validating the shape rejects both instead of returning a confident wrong answer.
_VALID_LABEL = re.compile(r"^(\d[\dA-Z]{0,4}|IL-\d[A-Z]?)$")


def extract_label(first_line_block):
    for pattern in (_LABEL_FIRST, _LABEL_LAST, _LABEL_LAST_SIMPLE):
        m = pattern.search(first_line_block)
        if m and _VALID_LABEL.match(m.group(1).strip()):
            return m.group(1).strip()
    return None


def build_page_label_map(pdf_path):
    """{pdfPage (1-indexed, matching docling's page_no): printed label}"""
    labels = {}
    doc = pdfium.PdfDocument(pdf_path)
    try:
        for i in range(len(doc)):
            page = doc[i]
            txt = _page_text(page)
            page.close()
            # Search both ends of the page text, not just the head. Most pages open with the
            # "<label> MODULE N..." header within the first ~500 chars (some have leftover body
            # text from the previous section first -- one such page had it starting at character
            # 432, why this is 500 and not 250). But dense, facilitator-note-heavy pages instead
            # carry the "MODULE N...LESSON N  <label>" footer at the very END of the extracted
            # text (confirmed live: a 2612-char page had its real label ("879A") sitting at
            # character 2608 -- nowhere near a 500-char head window). Checking the last 1000 chars
            # too catches these for free, with no PDF-structure assumptions needed either way.
            label = extract_label(txt[:500]) or extract_label(txt[-1000:])
            if label:
                labels[i + 1] = label
    finally:
        doc.close()
    return labels


def module_of_each_node(nodes, rels):
    """identifier -> module ordinalName ('Module 1'..'Module 5'), by walking hasPart backward.
    pdfPage collides across modules (each is its own separate source PDF), so we MUST know which
    module a node belongs to before trusting a page-label lookup -- confirmed earlier this
    session that a bare pdfPage number is shared by over a dozen unrelated nodes."""
    by_id = {n["identifier"]: n for n in nodes}
    parent_of = {r["target_identifier"]: r["source_identifier"] for r in rels if r["label"] == "hasPart"}

    def find_module(identifier):
        cur, seen = identifier, set()
        while cur in parent_of and cur not in seen:
            seen.add(cur)
            cur = parent_of[cur]
            n = by_id.get(cur)
            if n and n["labels"][0] == "LessonGrouping" and n["properties"].get("groupLevel") == "0":
                return n["properties"].get("ordinalName")
        return None

    return {n["identifier"]: find_module(n["identifier"]) for n in nodes}


def apply(nodes, rels, module_pdfs):
    """Overwrite pageLabel on every LearningComponent in `nodes` with the deterministic PDF-read
    value (see module docstring), given `module_pdfs` ({module name: pdf path}) for the ONE course
    `nodes`/`rels` belong to. Mutates `nodes` in place. Returns a stats dict.

    Factored out of main()'s per-course loop so batch_pipeline.build_course_from_batch() can run
    this as an in-memory step right after a course is built, not just as this file's standalone
    multi-course CLI."""
    print("  computing each node's parent module...", file=sys.stderr)
    node_module = module_of_each_node(nodes, rels)

    # build one page->label map per module PDF
    module_maps = {}
    for module_name, pdf_path in module_pdfs.items():
        print(f"  reading {module_name} PDF...", file=sys.stderr)
        module_maps[module_name] = build_page_label_map(pdf_path)
        print(f"    {len(module_maps[module_name])} page labels found", file=sys.stderr)

    # Overwrite EVERY LearningComponent's pageLabel with the deterministic PDF-read value
    # whenever one exists for its exact pdfPage -- not just filling blanks. Claude's own
    # page_label reads are sometimes wrong, not just missing (e.g. a node genuinely on p.511
    # self-reported "433G", a real label that belongs to a different page entirely) -- the
    # deterministic map is authoritative, so it should win over whatever Claude wrote either way.
    filled, corrected, unresolved, no_module = 0, 0, 0, 0
    for n in nodes:
        p = n["properties"]
        if n["labels"][0] != "LearningComponent" or p.get("pdfPage") is None:
            continue
        module_name = node_module.get(n["identifier"])
        if not module_name or module_name not in module_maps:
            no_module += 1
            continue
        label = module_maps[module_name].get(p["pdfPage"])
        if not label:
            unresolved += 1
            continue
        if not p.get("pageLabel"):
            filled += 1
        elif p["pageLabel"] != label:
            corrected += 1
        p["pageLabel"] = label

    stats = {"filled": filled, "corrected": corrected, "unresolved": unresolved,
              "no_module": no_module}
    print(f"  filled (was blank): {filled}, corrected (was wrong): {corrected}, "
          f"unresolved (no label found on that exact page): {unresolved}, "
          f"no known parent module: {no_module}", file=sys.stderr)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Overwrite data/*_nodes.jsonl in place (default: dry run)")
    args = parser.parse_args()

    for jsonl_path, module_pdfs in COURSES.items():
        rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
        print(f"\n=== {jsonl_path} ===", file=sys.stderr)
        with open(jsonl_path) as f:
            nodes = [json.loads(l) for l in f]
        with open(rels_path) as f:
            rels = [json.loads(l) for l in f]

        apply(nodes, rels, module_pdfs)

        if args.write:
            snapshot_course(jsonl_path, "backfill_page_labels")
            with open(jsonl_path, "w") as f:
                for n in nodes:
                    f.write(json.dumps(n) + "\n")
            print(f"  wrote {jsonl_path}", file=sys.stderr)

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl in place.", file=sys.stderr)


if __name__ == "__main__":
    main()
