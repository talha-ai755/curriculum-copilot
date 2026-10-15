#!/usr/bin/env python3
"""
backfill_pdf_citation.py — (re)stamp `sourcePdfKey` onto every LearningComponent/Assessment node,
for any course. Purely deterministic, no PDF re-read, no Claude call: citation.pdf_key_for_module(
course_slug, pdf_path) from each course's own submit_batch_<course>.py MODULES dict is exactly
what build_course_from_batch() would stamp on for a fresh build -- this just (re)applies that same
formula. Going forward, pdf_to_kg.py's build_graph()/combine_modules() do this at extraction time
via their own source_pdf_key parameter (see batch_pipeline.py), so newly-ingested courses get this
for free without needing this backfill -- this is for (re)correcting courses already built.

ALWAYS overwrites (not just fills blanks) -- same reasoning as backfill_page_labels.py's
deterministic map being authoritative: citation.py's key FORMAT itself has already changed once
this session (a bare "grade8/..." key, before any real S3 bucket existed, to "bluebonnet/grade8/
..." once the real bucket layout was confirmed), so a node's existing sourcePdfKey being non-empty
doesn't mean it's still correct -- only recomputing from the current formula does. Safe to re-run
any time citation.pdf_key_for_module()'s formula changes.

Reuses backfill_page_labels.py's course auto-discovery (COURSES) and module_of_each_node() --
same course-registration mechanism, same pdfPage-collides-across-modules handling.

Usage:
  python3 backfill_pdf_citation.py            # dry run, prints a diff summary only
  python3 backfill_pdf_citation.py --write    # overwrite data/*_nodes.jsonl in place
"""
import argparse
import json
import os
import sys

import citation
from backfill_page_labels import COURSES, module_of_each_node
from snapshot import snapshot_course


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Overwrite data/*_nodes.jsonl in place (default: dry run)")
    args = parser.parse_args()

    for jsonl_path, module_pdfs in COURSES.items():
        rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
        course_slug = os.path.basename(jsonl_path).replace("_nodes.jsonl", "")
        print(f"\n=== {jsonl_path} (course_slug={course_slug!r}) ===", file=sys.stderr)
        with open(jsonl_path) as f:
            nodes = [json.loads(l) for l in f]
        with open(rels_path) as f:
            rels = [json.loads(l) for l in f]

        print("  computing each node's parent module...", file=sys.stderr)
        node_module = module_of_each_node(nodes, rels)

        module_keys = {m: citation.pdf_key_for_module(course_slug, pdf_path)
                       for m, pdf_path in module_pdfs.items()}

        filled, corrected, unchanged, no_module = 0, 0, 0, 0
        for n in nodes:
            if n["labels"][0] not in ("LearningComponent", "Assessment"):
                continue
            p = n["properties"]
            module_name = node_module.get(n["identifier"])
            key = module_keys.get(module_name)
            if not key:
                no_module += 1
                continue
            old_key = p.get("sourcePdfKey")
            if old_key == key:
                unchanged += 1
                continue
            if not old_key:
                filled += 1
            else:
                corrected += 1
            p["sourcePdfKey"] = key

        print(f"  filled (was blank): {filled}, corrected (key format changed): {corrected}, "
              f"unchanged (already correct): {unchanged}, no known parent module: {no_module}",
              file=sys.stderr)

        if args.write:
            snapshot_course(jsonl_path, "backfill_pdf_citation")
            with open(jsonl_path, "w") as f:
                for n in nodes:
                    f.write(json.dumps(n) + "\n")
            print(f"  wrote {jsonl_path}", file=sys.stderr)

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl in place.", file=sys.stderr)
        print("(Reloading Neo4j + rebuilding the Qdrant index are separate, explicit steps.)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
