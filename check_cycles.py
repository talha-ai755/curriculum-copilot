#!/usr/bin/env python3
"""
check_cycles.py — detect directed cycles (and self-loops) in the hierarchy/progression edges of
every course in data/.

Why this exists: check_orphans.py catches disconnected nodes, but nothing catches a cycle. A cycle
on `hasPart`/`hasChild` makes `MATCH (c:Course)-[:hasPart*]->(n)` blow up or return duplicates, and
a cycle on `buildsTowards` makes a "what comes before X" answer circular (A builds towards B builds
towards A). Idea borrowed from K12-KGraph's check_cycles.py (DAG validation on prerequisite edges).

Detection only -- nothing is modified. Exit code 1 if any cycle is found.

Usage:
  python3 check_cycles.py                       # all courses, all checked edge types
  python3 check_cycles.py --course data/grade6_nodes.jsonl
  python3 check_cycles.py --types buildsTowards
"""
import argparse
import json
import sys
from collections import defaultdict

from backfill_page_labels import COURSES

DEFAULT_TYPES = ("hasPart", "hasChild", "buildsTowards")


def find_cycles(rels, edge_type, max_cycles=50):
    """Return up to max_cycles cycles (each a list of node ids, first == last) for one edge type.
    Iterative three-colour DFS so deep hierarchies can't hit the recursion limit."""
    graph = defaultdict(set)
    for r in rels:
        if r.get("label") == edge_type:
            graph[r["source_identifier"]].add(r["target_identifier"])

    WHITE, GREY, BLACK = 0, 1, 2
    colour = defaultdict(int)
    cycles = []
    for root in list(graph):
        if colour[root] != WHITE:
            continue
        stack = [(root, iter(graph[root]))]
        path = [root]
        colour[root] = GREY
        while stack and len(cycles) < max_cycles:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                colour[node] = BLACK
                stack.pop()
                path.pop()
            elif colour[nxt] == GREY:
                cycles.append(path[path.index(nxt):] + [nxt])
            elif colour[nxt] == WHITE:
                colour[nxt] = GREY
                stack.append((nxt, iter(graph.get(nxt, ()))))
                path.append(nxt)
        if len(cycles) >= max_cycles:
            break
    return cycles


def label_of(by_id, ident):
    n = by_id.get(ident)
    if not n:
        return ident
    p = n["properties"]
    return p.get("name") or p.get("code") or (p.get("description") or "")[:40] or ident


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--course", help="Check a single data/*_nodes.jsonl path only")
    parser.add_argument("--types", nargs="+", default=list(DEFAULT_TYPES),
                        help=f"Edge types to check (default: {' '.join(DEFAULT_TYPES)})")
    args = parser.parse_args()

    courses = COURSES if not args.course else {args.course: COURSES[args.course]}
    total = 0
    for nodes_path in courses:
        rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
        with open(nodes_path) as f:
            by_id = {n["identifier"]: n for n in map(json.loads, f)}
        with open(rels_path) as f:
            rels = [json.loads(l) for l in f]

        course_total = 0
        for edge_type in args.types:
            cycles = find_cycles(rels, edge_type)
            course_total += len(cycles)
            for c in cycles:
                print(f"  [{edge_type}] cycle: " + " -> ".join(label_of(by_id, i) for i in c),
                      file=sys.stderr)
        total += course_total
        print(f"{nodes_path}: {course_total or 'no'} cycle(s)", file=sys.stderr)

    if total:
        sys.exit(1)


if __name__ == "__main__":
    main()
