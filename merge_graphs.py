#!/usr/bin/env python3
"""
merge_graphs.py — Combine several grades' graphs into ONE deduped graph.

Each grade you build gives a nodes.jsonl + relationships.jsonl. Because every grade uses
the same framework and deterministic UUID5 IDs, merging is just a union deduped by
`identifier`: shared standards collapse to one copy, and the cross-grade progression edges
(already in each grade) connect the grades automatically. No dangling is introduced.

Usage:
    python merge_graphs.py g7_nodes.jsonl g7_rels.jsonl g6_nodes.jsonl g6_rels.jsonl
    # files can be given in any order — nodes vs relationships are detected by `type`.
Outputs (override with --out-prefix):
    merged_nodes.jsonl, merged_relationships.jsonl
"""
import argparse
import json


def merge_records(line_sources):
    """line_sources: iterable of iterables-of-lines (str or bytes). Returns (nodes, rels)
    dicts keyed by identifier — first occurrence wins (records are byte-identical anyway)."""
    nodes, rels = {}, {}
    for lines in line_sources:
        for line in lines:
            if isinstance(line, bytes):
                line = line.decode("utf-8", "ignore")
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if o.get("type") == "node":
                nodes.setdefault(o["identifier"], o)
            elif o.get("type") == "relationship":
                rels.setdefault(o["identifier"], o)
    return nodes, rels


def dangling_count(nodes, rels):
    ids = set(nodes)
    return sum(1 for r in rels.values()
               for k in ("source_identifier", "target_identifier") if r[k] not in ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="nodes.jsonl / relationships.jsonl files (any order)")
    ap.add_argument("--out-prefix", default="merged")
    args = ap.parse_args()

    nodes, rels = merge_records(open(p) for p in args.files)
    with open(f"{args.out_prefix}_nodes.jsonl", "w") as f:
        for n in nodes.values():
            f.write(json.dumps(n) + "\n")
    with open(f"{args.out_prefix}_relationships.jsonl", "w") as f:
        for r in rels.values():
            f.write(json.dumps(r) + "\n")

    import collections
    print(f"merged {len(args.files)} files")
    print(f"  nodes: {len(nodes)}  |  relationships: {len(rels)}")
    print(f"  node types: {dict(collections.Counter(n['labels'][0] for n in nodes.values()))}")
    print(f"  dangling endpoints: {dangling_count(nodes, rels)}")
    print(f"  wrote {args.out_prefix}_nodes.jsonl + {args.out_prefix}_relationships.jsonl")


if __name__ == "__main__":
    main()
