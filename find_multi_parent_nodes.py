#!/usr/bin/env python3
"""
find_multi_parent_nodes.py — detect LearningComponent/Assessment nodes with MORE than one
incoming `hasPart` edge (the mirror-image bug to check_orphans.py's zero-parent check).

Why this exists: found live this session by walking hasPart edges while investigating what
looked like duplicate content in Grade 3 Module 7 -- it wasn't duplicated nodes, it was a single
node wired into the tree under two or three different parent topics/modules at once. 17 such
nodes across Grade 3 (5), Grade 4 (4), Grade 5 (8); no other course affected. In every case one
parent is the coarse whole-module LessonGrouping (pdf_to_kg.py's orphan-prevention fallback --
see build_graph()'s assessment/supplementary loops) and the other(s) are a specific narrower
topic. A hasPart hierarchy is meant to be a tree: every per-topic rollup that walks hasPart
(topic listings, backfill_page_rollups.py) double/triple-counts an affected node, and
semantic_index._parent_names() (a plain dict keyed by child identifier) silently picks whichever
edge iterates last, so the Qdrant "parent" payload field for these nodes is arbitrary.

Detection only -- deliberately does NOT try to guess which parent is correct. Checked live
whether "keep the specific topic, drop the module fallback" works by comparing each specific
topic's own pdfPageStart/pdfPageEnd against the node's pdfPage: it does NOT hold for any of the
17 cases (same systemic pdfPageEnd truncation issue as BATCH_INGESTION_REPORT.md), so the correct
parent has to be decided by reading the real PDF page, same as fix_misattributed_topics.py's
approach -- not automated here.

Usage:
  python3 find_multi_parent_nodes.py              # check all auto-discovered courses
  python3 find_multi_parent_nodes.py --course data/grade3_nodes.jsonl
"""
import argparse
import json
import sys
from collections import defaultdict

from backfill_page_labels import COURSES


def find_multi_parents(nodes, rels):
    """[(node, [parent_node, ...]), ...] for every LearningComponent/Assessment node with more
    than one incoming hasPart edge."""
    by_id = {n["identifier"]: n for n in nodes}
    parents_of = defaultdict(list)
    for r in rels:
        if r["label"] == "hasPart":
            parents_of[r["target_identifier"]].append(r["source_identifier"])

    flagged = []
    for n in nodes:
        if n["labels"][0] not in ("LearningComponent", "Assessment"):
            continue
        parent_ids = parents_of.get(n["identifier"], [])
        if len(parent_ids) > 1:
            parent_nodes = [by_id[p] for p in parent_ids if p in by_id]
            flagged.append((n, parent_nodes))
    return flagged


def report(nodes_path, flagged):
    if not flagged:
        return
    print(f"\nWARNING: {len(flagged)} node(s) in {nodes_path} with MORE THAN ONE hasPart "
          f"parent -- double/triple-counted in any topic rollup:", file=sys.stderr)
    for n, parents in flagged:
        p = n["properties"]
        text = p.get("description") or p.get("name") or ""
        print(f"  [{n['labels'][0]}] {n['identifier']} pdfPage={p.get('pdfPage')} "
              f"sourcePdfKey={p.get('sourcePdfKey', '')!r}", file=sys.stderr)
        print(f"      {text[:80]!r}", file=sys.stderr)
        for par in parents:
            pp = par["properties"]
            print(f"      parent: {par['labels'][0]} {pp.get('name')!r} "
                  f"range={pp.get('pdfPageStart')}-{pp.get('pdfPageEnd')}", file=sys.stderr)
    print("  (not auto-fixed -- the correct single parent must be determined by reading the "
          "real PDF page; see fix_multi_parent_nodes.py)", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--course", help="Check a single course's data/*_nodes.jsonl path only "
                         "(default: all auto-discovered courses)")
    args = parser.parse_args()

    courses = COURSES if not args.course else {args.course: COURSES[args.course]}
    total_flagged = 0
    for nodes_path in courses:
        rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
        with open(nodes_path) as f:
            nodes = [json.loads(l) for l in f]
        with open(rels_path) as f:
            rels = [json.loads(l) for l in f]

        flagged = find_multi_parents(nodes, rels)
        total_flagged += len(flagged)
        status = f"{len(flagged)} multi-parent node(s)" if flagged else "clean"
        print(f"{nodes_path}: {status}", file=sys.stderr)
        report(nodes_path, flagged)

    if total_flagged:
        sys.exit(1)


if __name__ == "__main__":
    main()
