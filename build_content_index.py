#!/usr/bin/env python3
"""
build_content_index.py — one-time OpenAI embedding + index of curriculum_content from the
current algebra1_nodes.jsonl/algebra1_relationships.jsonl.

The v2 extraction pipeline (pdf_to_kg.py, text-first via docling + Claude) already gives every
LearningComponent/Assessment node real, verbatim text and reliable page numbers directly — no
page-label-matching/fallback logic needed here anymore, just embed and index what's already on
each node.

Needs OPENAI_API_KEY set. Usage: python3 build_content_index.py
"""
import os
import sys

from openai import OpenAI

import graph_store as gs
import semantic_index as si

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not set")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def main():
    nodes, rels = gs.load_jsonl(f"{DATA_DIR}/algebra1_nodes.jsonl",
                                 f"{DATA_DIR}/algebra1_relationships.jsonl")
    client = si.get_client()
    openai_client = OpenAI()

    print("Embedding and indexing curriculum_content (OpenAI)...", file=sys.stderr)
    indexed = si.index_nodes(client, openai_client, nodes, rels)
    print(f"Indexed {indexed} points into '{si.COLLECTION}'", file=sys.stderr)


if __name__ == "__main__":
    main()
