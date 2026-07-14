#!/usr/bin/env python3
"""
rescope_grade.py — fix an already-built grade graph so its CONTENT ids don't collide with
another grade's, WITHOUT re-running the extractor.

Older builds keyed content (Module/Topic/Assessment/LearningComponent) only by number, so
"Grade 6 Module 1" and "Grade 7 Module 1" got the same id and merged. This appends a per-grade
tag (e.g. ::g6) to every content node id (and the edges touching them). Standard nodes and
standard↔standard edges are left untouched, so the shared TEKS backbone and the cross-grade
progression still line up after merging.

Usage:
    python rescope_grade.py --tag g6 g6_nodes.jsonl g6_relationships.jsonl
    # -> g6_nodes.rescoped.jsonl  +  g6_relationships.rescoped.jsonl
Then merge the rescoped grade with the other grade's ORIGINAL files.
"""
import argparse
import json

CONTENT = {"Course", "LessonGrouping", "Lesson", "Activity", "Assessment", "LearningComponent"}


def rescope(nodes_lines, rels_lines, tag):
    suffix = "::" + tag
    content_ids, nodes = set(), []
    for line in nodes_lines:
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        if o["labels"][0] in CONTENT:
            content_ids.add(o["identifier"])
            o["identifier"] += suffix
            o["properties"]["identifier"] = o["identifier"]
        nodes.append(o)
    rels = []
    for line in rels_lines:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        s_c = r["source_identifier"] in content_ids
        t_c = r["target_identifier"] in content_ids
        if s_c:
            r["source_identifier"] += suffix
        if t_c:
            r["target_identifier"] += suffix
        if s_c or t_c:                       # keep this edge's id unique to the grade too
            r["identifier"] += suffix
            r["properties"]["identifier"] = r["identifier"]
        rels.append(r)
    return nodes, rels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="short grade tag, e.g. g6")
    ap.add_argument("nodes")
    ap.add_argument("relationships")
    args = ap.parse_args()
    nodes, rels = rescope(open(args.nodes), open(args.relationships), args.tag)
    nout = args.nodes.replace(".jsonl", "") + ".rescoped.jsonl"
    rout = args.relationships.replace(".jsonl", "") + ".rescoped.jsonl"
    with open(nout, "w") as f:
        for n in nodes:
            f.write(json.dumps(n) + "\n")
    with open(rout, "w") as f:
        for r in rels:
            f.write(json.dumps(r) + "\n")
    import collections
    cn = sum(1 for n in nodes if n["labels"][0] in CONTENT)
    print(f"rescoped {cn} content nodes with tag '{args.tag}'")
    print(f"  wrote {nout} + {rout}")


if __name__ == "__main__":
    main()
