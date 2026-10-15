#!/usr/bin/env python3
"""
check_orphans.py — detect LearningComponent/Assessment nodes with NO incoming `hasPart` edge at
all (completely disconnected from the Course->Module->Topic->Lesson hierarchy).

Why this exists: found live this session, twice, by manual Neo4j spelunking, not by any automated
check -- 4 Assessment nodes in Algebra I/Grade 8 (e.g. "Module 1 Assessment Summary") with no
`hasPart` edge at all, and a SEPARATE, previously-undetected 7 more in Grade 6/7. Root cause was
`pdf_to_kg.py`'s assessment-linking loop creating the node unconditionally but only adding its
hasPart edge when the extraction happened to tag it with a matching topic_no -- now fixed with a
module-level fallback (see build_graph()), but this check exists as a safety net for whatever
OTHER way a node could end up disconnected in a future grade's extraction, not just this one
specific root cause. An orphaned node is silently invisible to any graph_query traversal starting
from Course/Module/Topic (MATCH (c:Course)-[:hasPart*]->(n) never finds it), and
backfill_pdf_citation.py's module_of_each_node() can't resolve its parent module either -- so it
can end up with a stale or missing sourcePdfKey indefinitely, exactly what happened here.

Detection only -- deliberately does NOT try to guess/fix a parent. Inferring the correct parent
from a node's own name (e.g. "Module 4 Assessment Summary" -> Module 4) is a judgment call best
made by a human reading the actual name/content, not a heuristic worth automating blindly.

Usage:
  python3 check_orphans.py              # check all auto-discovered courses, dry-run report only
  python3 check_orphans.py --course grade6_nodes.jsonl   # check a single course
"""
import argparse
import json
import sys

from backfill_page_labels import COURSES


def find_orphans(nodes, rels):
    """[node, ...] for every LearningComponent/Assessment node with no incoming hasPart edge."""
    has_incoming = {r["target_identifier"] for r in rels if r.get("label") == "hasPart"}
    return [n for n in nodes
            if n["labels"][0] in ("LearningComponent", "Assessment")
            and n["identifier"] not in has_incoming]


def report(nodes_path, orphans):
    if not orphans:
        return
    print(f"\nWARNING: {len(orphans)} orphaned node(s) in {nodes_path} -- no incoming hasPart "
          f"edge, invisible to any Course/Module/Topic traversal:", file=sys.stderr)
    for n in orphans:
        p = n["properties"]
        print(f"  [{n['labels'][0]}] {n['identifier']} name={p.get('name')!r} "
              f"sourcePdfKey={p.get('sourcePdfKey', '')!r} pdfPage={p.get('pdfPage')}",
              file=sys.stderr)
    print("  (not auto-fixed -- inspect each node's name/content, find the intended parent "
          "Module/Topic, and add the missing hasPart edge by hand)", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--course", help="Check a single course's data/*_nodes.jsonl path only "
                         "(default: all auto-discovered courses)")
    args = parser.parse_args()

    courses = COURSES if not args.course else {args.course: COURSES[args.course]}
    total_orphans = 0
    for nodes_path in courses:
        rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
        with open(nodes_path) as f:
            nodes = [json.loads(l) for l in f]
        with open(rels_path) as f:
            rels = [json.loads(l) for l in f]

        orphans = find_orphans(nodes, rels)
        total_orphans += len(orphans)
        status = f"{len(orphans)} orphan(s)" if orphans else "clean"
        print(f"{nodes_path}: {status}", file=sys.stderr)
        report(nodes_path, orphans)

    if total_orphans:
        sys.exit(1)


if __name__ == "__main__":
    main()
