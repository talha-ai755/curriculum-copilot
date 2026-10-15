#!/usr/bin/env python3
"""
build_content_index.py — one-time OpenAI embedding + index of curriculum_content from every
course's *_nodes.jsonl/*_relationships.jsonl pair currently in data/ (whatever's been ingested).

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
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

import semantic_index as si
from agent import load_all_courses

load_dotenv()

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not set")

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
