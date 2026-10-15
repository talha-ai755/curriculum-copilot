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
import sys

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


def apply(nodes, rels):
    """Recompute pageStartLabel/pageEndLabel/pdfPageStart/pdfPageEnd on every Lesson/LessonGrouping
    in `nodes`, for the ONE course `nodes`/`rels` belong to. Mutates `nodes` in place. Returns a
    stats dict (includes up to 15 sample diffs for logging).

    Factored out of process_course()'s body so batch_pipeline.build_course_from_batch() can run
    this as an in-memory step right after a course is built, not just as this file's standalone
    multi-course CLI."""
    by_id = {n["identifier"]: n for n in nodes}
    children_of = {}
    for r in rels:
        if r["label"] == "hasPart":
            children_of.setdefault(r["source_identifier"], []).append(r["target_identifier"])

    memo = {}
    changed_start_label = changed_end_label = changed_start_pdf = changed_end_pdf = 0
    no_descendants = 0
    samples = []

    for n in nodes:
        if n["labels"][0] not in ("Lesson", "LessonGrouping"):
            continue
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
        if not descendants:
            no_descendants += 1
            continue

        min_child = _pick_extreme(descendants, reverse=False)
        max_child = _pick_extreme(descendants, reverse=True)

        new_start_label = min_child["properties"].get("pageLabel", "")
        new_end_label = max_child["properties"].get("pageLabel", "")
        new_start_pdf = min_child["properties"]["pdfPage"]
        new_end_pdf = max_child["properties"]["pdfPage"]

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

    print("\n  Sample diffs:", file=sys.stderr)
    for labels, name, row_changed in samples:
        print(f"   [{labels}] {name!r}:", file=sys.stderr)
        for field, old, new in row_changed:
            print(f"      {field}: {old!r} -> {new!r}", file=sys.stderr)

    return {"changed_start_label": changed_start_label, "changed_end_label": changed_end_label,
            "changed_start_pdf": changed_start_pdf, "changed_end_pdf": changed_end_pdf,
            "no_descendants": no_descendants, "samples": samples}


def process_course(jsonl_path, write):
    rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
    print(f"\n=== {jsonl_path} ===", file=sys.stderr)
    with open(jsonl_path) as f:
        nodes = [json.loads(l) for l in f]
    with open(rels_path) as f:
        rels = [json.loads(l) for l in f]

    apply(nodes, rels)

    if write:
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
