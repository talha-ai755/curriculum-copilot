#!/usr/bin/env python3
"""
build_batch_v3_course.py — poll the v3 re-extraction batch (submit_batch_v3.py, run under the
strengthened SYSTEM prompt with verbatim titles + verbatim/complete materials_needed), collect
results per module, merge each module, and build the final Course "Algebra I" graph.

materials_needed is overridden with real_content.find_real_materials() (a plain regex parse of
each module's own Pacing Guide pages) rather than trusting Claude's value: Claude's batched
extraction sends a topic's Pacing Guide page (early in the module) and its lessons' own activity
content (often much later, in a different, independent batch) to separate calls, so whichever
batch extracts a lesson's activities frequently never sees the Pacing Guide line it's supposed to
copy, and guesses instead. Titles don't have this problem (a lesson's title is on the same
TOC page as its own Pacing Guide) and stay Claude-sourced.

Overwrites algebra1_nodes.jsonl/algebra1_relationships.jsonl -- the pre-v3 data is already backed
up as algebra1_nodes.v2.jsonl/algebra1_relationships.v2.jsonl.

Usage: python3 build_batch_v3_course.py
"""
import json
import sys
import time

import anthropic

import pdf_to_kg as p2k
import real_content as rc

STATE_PATH = "/tmp/batch_v3_state.json"
DATA_DIR = "/home/talha/Downloads/sibme_curriculum_rag/curriculum-copilot/data"
CASE_PATH = "/home/talha/Downloads/sibme_curriculum_rag/curriculum-copilot/teks_math_case.json"
MODULES = ["Module 1", "Module 2", "Module 3", "Module 4", "Module 5"]
BASE = "/home/talha/Downloads/SM Algebra I: Teacher Editions/Teacher Edition By Module/"
MODULE_PDFS = {
    "Module 1": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 1.pdf",
    "Module 2": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 2.pdf",
    "Module 3": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 3.pdf",
    "Module 4": BASE + "Algebra I Math Teacher Edition, Volume 2 Module 4.pdf",
    "Module 5": BASE + "Algebra I Math Teacher Edition, Volume 2 Module 5.pdf",
}

POLL_INTERVAL = 30


def wait_for_batch(client, batch_id):
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        print(f"  status={batch.processing_status} counts={batch.request_counts}",
              file=sys.stderr)
        if batch.processing_status == "ended":
            return batch
        time.sleep(POLL_INTERVAL)


def main():
    with open(STATE_PATH) as f:
        state = json.load(f)
    batch_id, custom_id_map = state["batch_id"], state["custom_id_map"]

    client = anthropic.Anthropic()
    print(f"Polling batch {batch_id}...", file=sys.stderr)
    wait_for_batch(client, batch_id)

    chunks_by_module = {m: [] for m in MODULES}
    errors = []
    for entry in client.messages.batches.results(batch_id):
        info = custom_id_map.get(entry.custom_id)
        if not info:
            continue
        module = info["module"]
        if entry.result.type != "succeeded":
            errors.append((entry.custom_id, entry.result.type))
            continue
        try:
            chunk = p2k.parse_extraction(entry.result.message)
            chunks_by_module[module].append(chunk)
        except Exception as e:
            errors.append((entry.custom_id, f"parse error: {e}"))

    for module in MODULES:
        print(f"  {module}: {len(chunks_by_module[module])} batches succeeded", file=sys.stderr)
    if errors:
        print(f"  {len(errors)} batch(es) failed:", file=sys.stderr)
        for cid, reason in errors:
            print(f"    {cid}: {reason}", file=sys.stderr)

    case = p2k.load_case(json.load(open(CASE_PATH)))
    print(f"Loaded CASE framework: {len(case['by_uuid'])} standards, "
          f"{len(case['assocs'])} links", file=sys.stderr)

    print("Parsing real materials_needed from each module's Pacing Guide pages...",
          file=sys.stderr)
    real_materials_list = []
    for module in MODULES:
        materials = rc.find_real_materials(MODULE_PDFS[module])
        n_lessons = sum(len(v) for v in materials.values())
        print(f"  {module}: {len(materials)} topic(s), {n_lessons} lesson materials lists",
              file=sys.stderr)
        real_materials_list.append(materials)

    chunk_lists = [chunks_by_module[m] for m in MODULES]
    nodes, rels = p2k.combine_modules(chunk_lists, "Texas", "TEKS", case,
                                       course_name="Algebra I",
                                       real_materials_list=real_materials_list)

    n_lc = sum(1 for n in nodes if n["labels"][0] == "LearningComponent")
    n_supp = sum(1 for n in nodes if n["labels"][0] == "LearningComponent"
                 and n["properties"].get("componentRole") in
                 ("Assessment Summary", "Pacing Guide", "Self-Reflection", "Overview"))
    print(f"Built graph: {len(nodes)} nodes, {len(rels)} rels "
          f"({n_lc} LearningComponent, {n_supp} from supplementary)", file=sys.stderr)

    with open(f"{DATA_DIR}/algebra1_nodes.jsonl", "w") as f:
        for n in nodes:
            f.write(json.dumps(n) + "\n")
    with open(f"{DATA_DIR}/algebra1_relationships.jsonl", "w") as f:
        for r in rels:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(nodes)} nodes / {len(rels)} rels to {DATA_DIR}/algebra1_*.jsonl",
          file=sys.stderr)


if __name__ == "__main__":
    main()
