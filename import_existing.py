#!/usr/bin/env python3
"""
import_existing.py — one-time migration: register the courses ingested before the registry
existed (every submit_batch_*.py with MODULES + COURSE_NODES_PATH) as base curricula, version 1.

Version 1 points at the existing data/<course>_nodes.jsonl / _relationships.jsonl files -- nothing
is copied or rewritten, so every existing tool keeps working. No raw extraction chunks exist for
these courses (the batch results were never saved), so their modules have no extraction_dir; a
later version re-extracts what it needs. Idempotent: courses already registered are skipped.

Usage: python3 import_existing.py [--user admin]
"""
import argparse
import json
import os
import sys

import registry
from backfill_page_labels import discover_courses

ROOT = os.path.dirname(os.path.abspath(__file__))


def course_name(nodes_path):
    with open(nodes_path) as f:
        for line in f:
            n = json.loads(line)
            if n["labels"][0] == "Course":
                return n["properties"]["name"]
    raise ValueError(f"no Course node in {nodes_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="admin")
    args = ap.parse_args()

    with registry.connect() as conn:
        for nodes_path, module_pdfs in discover_courses().items():
            abs_nodes = os.path.join(ROOT, nodes_path)
            if not os.path.exists(abs_nodes):
                print(f"skip {nodes_path}: not built", file=sys.stderr)
                continue
            name = course_name(abs_nodes)
            cid = registry.slugify(f"bluebonnet {name}")
            if registry.get_curriculum(conn, cid):
                print(f"skip {cid}: already registered", file=sys.stderr)
                continue
            first_pdf = next(iter(module_pdfs.values()))
            publisher = "bluebonnet_k5" if "K-5" in first_pdf else "bluebonnet_secondary"
            slug = os.path.basename(nodes_path).replace("_nodes.jsonl", "")   # 'grade3'
            registry.create_curriculum(conn, cid, name, "math", publisher, "teks_math", args.user,
                                       citation_slug=slug)
            version = registry.add_version(
                conn, cid, args.user, status="ready", nodes_path=abs_nodes,
                rels_path=abs_nodes.replace("_nodes.jsonl", "_relationships.jsonl"),
                notes="imported from the pre-registry pipeline")
            for i, (module, pdf) in enumerate(module_pdfs.items(), 1):
                registry.add_module(conn, cid, version, module, i, pdf)
            print(f"registered {cid} v{version}: {name}, {len(module_pdfs)} modules, {publisher}",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
