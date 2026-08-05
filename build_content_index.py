#!/usr/bin/env python3
"""
build_content_index.py — one-time OpenAI embedding + index of curriculum_content from every
course's *_nodes.jsonl/*_relationships.jsonl pair (currently: Algebra I + Grade 8 Math).

The v2 extraction pipeline (pdf_to_kg.py, text-first via docling + Claude) already gives every
LearningComponent/Assessment node real, verbatim text and reliable page numbers directly — no
page-label-matching/fallback logic needed here anymore, just embed and index what's already on
each node.

Courses share the same standards backbone (StandardsFrameworkItem nodes, same identifiers), so
nodes/rels are deduped by identifier when combining -- otherwise the shared standards would get
indexed twice (harmless for search results themselves, but wasteful embedding cost and duplicate
points for the same content).

Needs OPENAI_API_KEY set. Usage: python3 build_content_index.py
"""
import glob
import os
import re
import sys

from openai import OpenAI

import graph_store as gs
import semantic_index as si

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not set")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_all_courses():
    """Every course is a `<name>_nodes.jsonl` + `<name>_relationships.jsonl` pair in data/ (skips
    the .v1/.v2 backup files, which end in a version suffix before .jsonl)."""
    nodes_by_id, rels_by_id = {}, {}
    node_paths = sorted(p for p in glob.glob(f"{DATA_DIR}/*_nodes.jsonl")
                         if not re.search(r"\.v\d+\.jsonl$", p))
    for nodes_path in node_paths:
        rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
        if not os.path.exists(rels_path):
            continue
        nodes, rels = gs.load_jsonl(nodes_path, rels_path)
        print(f"  {os.path.basename(nodes_path)}: {len(nodes)} nodes, {len(rels)} rels",
              file=sys.stderr)
        for n in nodes:
            nodes_by_id[n["identifier"]] = n
        for r in rels:
            rels_by_id[r["identifier"]] = r
    return list(nodes_by_id.values()), list(rels_by_id.values())


def main():
    print("Loading all course JSONL pairs...", file=sys.stderr)
    nodes, rels = load_all_courses()
    print(f"Combined (deduped by identifier): {len(nodes)} nodes, {len(rels)} rels",
          file=sys.stderr)

    client = si.get_client()
    openai_client = OpenAI()

    print("Embedding and indexing curriculum_content (OpenAI)...", file=sys.stderr)
    indexed = si.index_nodes(client, openai_client, nodes, rels)
    print(f"Indexed {indexed} points into '{si.COLLECTION}'", file=sys.stderr)


if __name__ == "__main__":
    main()
