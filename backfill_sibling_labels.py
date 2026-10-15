#!/usr/bin/env python3
"""
backfill_sibling_labels.py — fill in blank LearningComponent.pageLabel values by copying the
real label from a sibling LearningComponent that shares the exact same pdfPage (within the same
module).

Root cause: these teacher-edition PDFs often show a boxed inset (a reduced facsimile of the real
Student Edition page, carrying the real printed label, e.g. "911I") alongside surrounding
facilitator/teacher-note text on the SAME physical page. Extraction created separate
LearningComponent nodes for each region, but only attached the printed label to one of them --
confirmed live for "The Quadratic Formula" lesson (pdfPage 393: one node labeled "", a sibling at
the same pdfPage labeled "911I"). Scoping (live Neo4j, 2026-08-11) found 111 LearningComponent
nodes with a blank pageLabel; 108 of them (97%) have a same-pdfPage sibling with a real label.

Purely deterministic, no PDF re-read: for every (module, pdfPage) group of LearningComponent
nodes, if exactly one distinct non-empty pageLabel exists among the group, copy it onto every
blank sibling in that group. Groups with more than one distinct non-empty label are left alone
and reported as ambiguous (should not guess which one is right).

pdfPage collides across modules (each module is its own separate source PDF), so grouping must
be done within (module, pdfPage), not by bare pdfPage -- reuses module_of_each_node() from
backfill_page_labels.py, same as that script already does for the same reason.

Run AFTER this: re-run backfill_page_rollups.py, since several Lesson/LessonGrouping
pageStartLabel/pageEndLabel values currently roll up from what will now be a filled-in leaf.

Usage:
  python3 backfill_sibling_labels.py            # dry run, prints a diff summary only
  python3 backfill_sibling_labels.py --write    # overwrite data/*_nodes.jsonl in place
"""
import argparse
import glob
import json
import sys

from backfill_page_labels import module_of_each_node
from snapshot import snapshot_course

COURSES = sorted(p for p in glob.glob("data/*_nodes.jsonl") if ".v" not in p and "pre_" not in p)


def apply(nodes, rels):
    """Fill blank LearningComponent.pageLabel values from a same-(module,pdfPage) sibling's real
    label, for the ONE course `nodes`/`rels` belong to. Mutates `nodes` in place. Returns a stats
    dict (includes up to 15 sample rescues for logging).

    Factored out of process_course()'s body so batch_pipeline.build_course_from_batch() can run
    this as an in-memory step right after a course is built, not just as this file's standalone
    multi-course CLI."""
    print("  computing each node's parent module...", file=sys.stderr)
    node_module = module_of_each_node(nodes, rels)

    groups = {}
    for n in nodes:
        if n["labels"][0] != "LearningComponent" or n["properties"].get("pdfPage") is None:
            continue
        key = (node_module.get(n["identifier"]), n["properties"]["pdfPage"])
        groups.setdefault(key, []).append(n)

    rescued, ambiguous_skipped, unrescuable = 0, 0, 0
    samples = []

    for key, group in groups.items():
        blanks = [n for n in group if not n["properties"].get("pageLabel")]
        if not blanks:
            continue
        real_labels = {n["properties"]["pageLabel"] for n in group if n["properties"].get("pageLabel")}
        if not real_labels:
            unrescuable += len(blanks)
            continue
        if len(real_labels) > 1:
            ambiguous_skipped += len(blanks)
            continue

        label = next(iter(real_labels))
        for n in blanks:
            if len(samples) < 15:
                samples.append((key, n["properties"].get("componentRole"), label))
            n["properties"]["pageLabel"] = label
            rescued += 1

    print(f"  rescued (blank -> real sibling label): {rescued}", file=sys.stderr)
    print(f"  ambiguous (multiple distinct sibling labels, skipped): {ambiguous_skipped}", file=sys.stderr)
    print(f"  unrescuable (no sibling with any label): {unrescuable}", file=sys.stderr)

    print("\n  Sample rescues:", file=sys.stderr)
    for (module, pdfpage), role, label in samples:
        print(f"   module={module} pdfPage={pdfpage} componentRole={role!r}: '' -> {label!r}", file=sys.stderr)

    return {"rescued": rescued, "ambiguous_skipped": ambiguous_skipped,
            "unrescuable": unrescuable, "samples": samples}


def process_course(jsonl_path, write):
    rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
    print(f"\n=== {jsonl_path} ===", file=sys.stderr)
    with open(jsonl_path) as f:
        nodes = [json.loads(l) for l in f]
    with open(rels_path) as f:
        rels = [json.loads(l) for l in f]

    apply(nodes, rels)

    if write:
        snapshot_course(jsonl_path, "backfill_sibling_labels")
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
        print("(Then re-run backfill_page_rollups.py, then reload live Neo4j -- both separate steps.)", file=sys.stderr)


if __name__ == "__main__":
    main()
