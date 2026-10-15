#!/usr/bin/env python3
"""
backfill_page_rollups.py — fix pageStartLabel/pageEndLabel/pdfPageStart/pdfPageEnd on every
Lesson and LessonGrouping (Module/Topic) node.

Root cause (confirmed via a 132-node scoping pass -- only 3/132 nodes had fully correct labels):
pdf_to_kg.py's build_graph()/combine_modules() roll these fields up from each Topic's/Lesson's
raw extraction dict using "first non-empty wins" (start) / "last non-empty wins" (end) --
correct in principle ONLY if children are encountered in true page order, which extraction
doesn't guarantee. Worse, backfill_page_labels.py later corrected every LearningComponent leaf's
pageLabel directly off the real PDF (pdfPage -> printed label, deterministic) but never
recomputed these Lesson/LessonGrouping rollups afterward -- so they've been stale relative to the
now-correct leaf data ever since.

SECOND root cause, found later (same session as backfill_topic_citation.py): naive true-min/max
by pdfPage is ALSO wrong for a subset of Topics that have a shared module-front-matter summary
blurb linked in as a hasPart descendant with a misleadingly low pdfPage, tens to hundreds of
pages before the topic's real content (e.g. Grade 6 "Decimals": a descendant at pdfPage 5 whose
real content and dedicated overview page don't start until pdfPage 287). A true min() picks that
isolated outlier every time. Fixed the same way as backfill_topic_citation.py's
_pick_representative(): sort distinct descendant pdfPages, walk from the extreme (lowest for
start, highest for end), and skip any page whose nearest neighbor in the walk direction is more
than GAP_THRESHOLD pages away -- an isolated single page surrounded by a big gap is the
front-matter blurb, not real content. Verified this file's confirmed-bad topics before this fix:
Decimals (pageStartLabel "2C" -> should be "160A"), Multiple Representations of Equations,
Drawing Inferences, Line and Angle Relationships, The Pythagorean Theorem, Volume of Curved
Figures -- the exact same 6 topics backfill_topic_citation.py already fixed for
citationSourcePdfKey/citationPdfPage; this file fixes the OLDER, separate pageStartLabel/
pageEndLabel properties so the two stop disagreeing.

Fix here is purely deterministic, no PDF re-read needed: for every Lesson/LessonGrouping node,
walk hasPart forward (any depth, straight to LearningComponent leaves -- never through an
intermediate Lesson/Topic's own rollup, to avoid compounding whatever error existed at that
level) and take the outlier-skipped min/max pdfPage descendant's pageLabel/pdfPage as the new
rollup value. LearningComponent.pdfPage is confirmed reliable (it's docling's own echoed page
index, never hand-copied); pageLabel is reliable post backfill_page_labels.py.

Usage:
  python3 backfill_page_rollups.py            # dry run, prints a diff summary only
  python3 backfill_page_rollups.py --write    # overwrite data/*_nodes.jsonl in place
"""
import argparse
import glob
import json
import os
import sys
from snapshot import snapshot_course

import lesson_pages
from backfill_page_labels import COURSES as COURSE_PDFS, module_of_each_node

COURSES = sorted(p for p in glob.glob("data/*_nodes.jsonl") if ".v" not in p and "pre_" not in p)

GAP_THRESHOLD = 20


def _pick_extreme(descendants, reverse):
    """descendants: [LearningComponent node, ...], each with a real pdfPage. Returns the node at
    the extreme (lowest pdfPage if reverse=False, highest if reverse=True) that isn't an isolated
    outlier -- i.e. whose nearest neighbor in the walk direction is within GAP_THRESHOLD pages.
    Falls back to the true extreme if every page turns out isolated (short lists). Same algorithm
    as backfill_topic_citation.py's _pick_representative(), generalized to also walk from the
    high end for pageEndLabel/pdfPageEnd."""
    by_page = {}
    for d in descendants:
        by_page.setdefault(d["properties"]["pdfPage"], d)
    distinct_pages = sorted(by_page.keys(), reverse=reverse)
    chosen = distinct_pages[-1]
    for i, pg in enumerate(distinct_pages):
        if i == len(distinct_pages) - 1:
            chosen = pg
            break
        if abs(distinct_pages[i + 1] - pg) <= GAP_THRESHOLD:
            chosen = pg
            break
    return by_page[chosen]


def learning_component_descendants(node_id, children_of, by_id, memo):
    """All LearningComponent nodes reachable from node_id via hasPart, any depth."""
    if node_id in memo:
        return memo[node_id]
    result = []
    for child_id in children_of.get(node_id, []):
        child = by_id.get(child_id)
        if child is None:
            continue
        if child["labels"][0] == "LearningComponent":
            result.append(child)
        else:
            result.extend(learning_component_descendants(child_id, children_of, by_id, memo))
    memo[node_id] = result
    return result


def _lesson_descendants(node_id, children_of, by_id):
    """Every Lesson reachable from node_id via hasPart (a Module reaches its Topics' Lessons)."""
    out, stack, seen = [], list(children_of.get(node_id, [])), set()
    while stack:
        cid = stack.pop()
        if cid in seen or cid not in by_id:
            continue
        seen.add(cid)
        if by_id[cid]["labels"][0] == "Lesson":
            out.append(by_id[cid])
        elif by_id[cid]["labels"][0] == "LessonGrouping":
            stack.extend(children_of.get(cid, []))
    return out


def apply(nodes, rels, module_pdfs=None, header_format="bluebonnet"):
    """Recompute pageStartLabel/pageEndLabel/pdfPageStart/pdfPageEnd on every Lesson/LessonGrouping
    in `nodes`, for the ONE course `nodes`/`rels` belong to. Mutates `nodes` in place. Returns a
    stats dict (includes up to 15 sample diffs for logging).

    Lessons, in order of reliability (scored against lesson_pages.py's running headers on 1,030
    lessons -- start AND end exact: header 100% by construction, existing extraction range 46.7%,
    LearningComponent rollup 11.8%):
      1. `module_pdfs` given ({module name: pdf path}) and the PDF's running headers name this
         lesson -> the header range (lesson_pages.py).
      2. the lesson's existing range, if it covers all its LearningComponent pages.
      3. the LearningComponent rollup -- it ends a lesson at its last LearningComponent page, so
         it misses trailing Problem Set/Homework/Template pages; only a last resort.
    Topics/Modules (LessonGrouping) span their own LearningComponents (outlier-skipped, see
    _pick_extreme) AND all their Lessons' ranges, so they're never narrower than their lessons.

    Factored out of process_course()'s body so batch_pipeline.build_course_from_batch() can run
    this as an in-memory step right after a course is built, not just as this file's standalone
    multi-course CLI."""
    by_id = {n["identifier"]: n for n in nodes}
    children_of, parent_of = {}, {}
    for r in rels:
        if r["label"] == "hasPart":
            children_of.setdefault(r["source_identifier"], []).append(r["target_identifier"])
            parent_of[r["target_identifier"]] = r["source_identifier"]

    header_ranges, page_labels = {}, {}
    if module_pdfs:
        for module_name, pdf_path in module_pdfs.items():
            print(f"  reading {module_name} running headers...", file=sys.stderr)
            page_labels[module_name], lesson_of = lesson_pages.scan_pdf(pdf_path, header_format)
            header_ranges[module_name] = lesson_pages.lesson_ranges(lesson_of)
        node_module = module_of_each_node(nodes, rels)

    memo = {}
    changed_start_label = changed_end_label = changed_start_pdf = changed_end_pdf = 0
    no_descendants = 0
    source_counts = {"header": 0, "kept": 0, "rollup": 0}
    samples = []
    new_ranges = {}  # identifier -> (start_pdf, start_label, end_pdf, end_label), Lessons first

    def lc_label_on(descendants, page):
        return next((d["properties"].get("pageLabel", "") for d in descendants
                     if d["properties"]["pdfPage"] == page and d["properties"].get("pageLabel")), "")

    ordered = sorted((n for n in nodes if n["labels"][0] in ("Lesson", "LessonGrouping")),
                     key=lambda n: n["labels"][0] != "Lesson")
    for n in ordered:
        p = n["properties"]
        descendants = [
            d for d in learning_component_descendants(n["identifier"], children_of, by_id, memo)
            # pdfPage == 0 is a confirmed placeholder for "extraction never got a page number"
            # (verified: Grade 8 "Financial Literacy: Your Financial Future" has one, real
            # sourcePdfKey but pageLabel==""), not a genuine page 1-of-the-PDF -- same exclusion
            # as backfill_topic_citation.py's compute_citations(), needed here too since a naive
            # min() would otherwise pick it as pdfPageStart=0 with an empty pageStartLabel.
            if d["properties"].get("pdfPage")
        ]
        if n["labels"][0] == "Lesson":
            header = None
            if module_pdfs:
                module_name = node_module.get(n["identifier"])
                topic = by_id.get(parent_of.get(n["identifier"]), {}).get("properties", {})
                header = lesson_pages.lookup(header_ranges.get(module_name, {}),
                                             topic.get("position"), p.get("position"))
            if header:
                labels = page_labels[module_name]
                new_ranges[n["identifier"]] = (
                    header[0], labels.get(header[0]) or lc_label_on(descendants, header[0]),
                    header[1], labels.get(header[1]) or lc_label_on(descendants, header[1]))
                source_counts["header"] += 1
                continue
            if descendants:
                lo = _pick_extreme(descendants, reverse=False)["properties"]["pdfPage"]
                hi = _pick_extreme(descendants, reverse=True)["properties"]["pdfPage"]
                s, e = p.get("pdfPageStart"), p.get("pdfPageEnd")
                if s and e and s <= lo and e >= hi:
                    new_ranges[n["identifier"]] = (s, p.get("pageStartLabel", ""),
                                                   e, p.get("pageEndLabel", ""))
                    source_counts["kept"] += 1
                    continue
        if not descendants and n["labels"][0] == "Lesson":
            no_descendants += 1
            continue

        candidates = []  # (pdfPage, pageLabel)
        if descendants:
            for reverse in (False, True):
                d = _pick_extreme(descendants, reverse=reverse)["properties"]
                candidates.append((d["pdfPage"], d.get("pageLabel", "")))
        if n["labels"][0] == "LessonGrouping":
            for lesson in _lesson_descendants(n["identifier"], children_of, by_id):
                r = new_ranges.get(lesson["identifier"])
                if r:
                    candidates += [(r[0], r[1]), (r[2], r[3])]
        else:
            source_counts["rollup"] += 1
        if not candidates:
            no_descendants += 1
            continue
        start = min(candidates, key=lambda c: c[0])
        end = max(candidates, key=lambda c: c[0])
        new_ranges[n["identifier"]] = (start[0], start[1], end[0], end[1])

    for n in ordered:
        if n["identifier"] not in new_ranges:
            continue
        p = n["properties"]
        new_start_pdf, new_start_label, new_end_pdf, new_end_label = new_ranges[n["identifier"]]

        row_changed = []
        if p.get("pageStartLabel", "") != new_start_label:
            changed_start_label += 1
            row_changed.append(("pageStartLabel", p.get("pageStartLabel", ""), new_start_label))
        if p.get("pageEndLabel", "") != new_end_label:
            changed_end_label += 1
            row_changed.append(("pageEndLabel", p.get("pageEndLabel", ""), new_end_label))
        if p.get("pdfPageStart") != new_start_pdf:
            changed_start_pdf += 1
            row_changed.append(("pdfPageStart", p.get("pdfPageStart"), new_start_pdf))
        if p.get("pdfPageEnd") != new_end_pdf:
            changed_end_pdf += 1
            row_changed.append(("pdfPageEnd", p.get("pdfPageEnd"), new_end_pdf))

        if row_changed and len(samples) < 15:
            samples.append((n["labels"][0], p.get("name"), row_changed))

        p["pageStartLabel"] = new_start_label
        p["pageEndLabel"] = new_end_label
        p["pdfPageStart"] = new_start_pdf
        p["pdfPageEnd"] = new_end_pdf

    total = sum(1 for n in nodes if n["labels"][0] in ("Lesson", "LessonGrouping"))
    print(f"  {total} Lesson/LessonGrouping nodes checked", file=sys.stderr)
    print(f"  pageStartLabel changed: {changed_start_label}", file=sys.stderr)
    print(f"  pageEndLabel changed:   {changed_end_label}", file=sys.stderr)
    print(f"  pdfPageStart changed:   {changed_start_pdf}", file=sys.stderr)
    print(f"  pdfPageEnd changed:     {changed_end_pdf}", file=sys.stderr)
    print(f"  no LearningComponent descendants with pdfPage (left untouched): {no_descendants}", file=sys.stderr)
    print(f"  Lesson ranges from: running header {source_counts['header']}, existing range kept "
          f"{source_counts['kept']}, LearningComponent rollup {source_counts['rollup']}",
          file=sys.stderr)

    print("\n  Sample diffs:", file=sys.stderr)
    for labels, name, row_changed in samples:
        print(f"   [{labels}] {name!r}:", file=sys.stderr)
        for field, old, new in row_changed:
            print(f"      {field}: {old!r} -> {new!r}", file=sys.stderr)

    return {"changed_start_label": changed_start_label, "changed_end_label": changed_end_label,
            "changed_start_pdf": changed_start_pdf, "changed_end_pdf": changed_end_pdf,
            "no_descendants": no_descendants, "sources": source_counts, "samples": samples}


def process_course(jsonl_path, write):
    rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
    print(f"\n=== {jsonl_path} ===", file=sys.stderr)
    with open(jsonl_path) as f:
        nodes = [json.loads(l) for l in f]
    with open(rels_path) as f:
        rels = [json.loads(l) for l in f]

    # PDFs come from the course's registry modules; a course without them (or with
    # unreachable PDFs) still gets the graph-only fallbacks.
    module_pdfs = COURSE_PDFS.get(jsonl_path)
    apply(nodes, rels, module_pdfs if module_pdfs and all(os.path.exists(p) for p in
                                                          module_pdfs.values()) else None)

    if write:
        snapshot_course(jsonl_path, "backfill_page_rollups")
        with open(jsonl_path, "w") as f:
            for n in nodes:
                f.write(json.dumps(n) + "\n")
        print(f"\n  wrote {jsonl_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Overwrite data/*_nodes.jsonl in place (default: dry run)")
    args = parser.parse_args()

    for jsonl_path in COURSES:
        process_course(jsonl_path, args.write)

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl in place.", file=sys.stderr)
        print("(Reloading the live Neo4j from the corrected JSONL is a separate, explicit step.)", file=sys.stderr)


if __name__ == "__main__":
    main()
