#!/usr/bin/env python3
"""
build_batch_g8_course.py — poll the Grade 8 extraction batch (submit_batch_g8.py), collect
results per module, merge each module, and build the Course "Grade 8 Math" graph.

materials_needed is overridden with real_content.find_real_materials() the same way as Algebra I
(verified this session that Grade 8's PDFs use the identical Pacing Guide/TOC format).

Writes grade8_nodes.jsonl/grade8_relationships.jsonl -- a SEPARATE course graph, additive to the
existing Algebra I graph (shares the same teks_math_case.json standards backbone; content node
IDs are namespaced by course_name="Grade 8 Math" via build_graph()'s `scope`, so no collision with
Algebra I's "Module 1"/"Topic 1"/etc. even though the ordinal names repeat).

Usage: python3 build_batch_g8_course.py
"""
import json
import sys
import time

import anthropic

import pdf_to_kg as p2k
import real_content as rc

STATE_PATH = "/tmp/batch_g8_state.json"
DATA_DIR = "/home/talha/Downloads/sibme_curriculum_rag/curriculum-copilot/data"
CASE_PATH = "/home/talha/Downloads/sibme_curriculum_rag/curriculum-copilot/teks_math_case.json"
MODULES = ["Module 1", "Module 2", "Module 3", "Module 4", "Module 5"]
BASE = "/home/talha/Downloads/SM G8: Teacher Editions/Teacher Edition by Module/"
MODULE_PDFS = {
    "Module 1": BASE + "Eighth Grade Math Teacher Edition, Volume 1 Module 1.pdf",
    "Module 2": BASE + "Eighth Grade Math Teacher Edition, Volume 1 Module 2.pdf",
    "Module 3": BASE + "Eighth Grade Math Teacher Edition, Volume 2 Module 3.pdf",
    "Module 4": BASE + "Eighth Grade Math Teacher Edition, Volume 2 Module 4.pdf",
    "Module 5": BASE + "Eighth Grade Math Teacher Edition, Volume 2 Module 5.pdf",
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

    print("Reading real docling page text per module (for sourceText, see build_graph's "
          "docstring)...", file=sys.stderr)
    docling_pages_list = []
    for module in MODULES:
        pages = p2k.docling_page_text(MODULE_PDFS[module])
        print(f"  {module}: {len(pages)} pages", file=sys.stderr)
        docling_pages_list.append(pages)

    chunk_lists = [chunks_by_module[m] for m in MODULES]
    nodes, rels = p2k.combine_modules(chunk_lists, "Texas", "TEKS", case,
                                       course_name="Grade 8 Math",
                                       real_materials_list=real_materials_list,
                                       docling_pages_list=docling_pages_list)

    n_lc = sum(1 for n in nodes if n["labels"][0] == "LearningComponent")
    n_supp = sum(1 for n in nodes if n["labels"][0] == "LearningComponent"
                 and n["properties"].get("componentRole") in
                 ("Assessment Summary", "Pacing Guide", "Self-Reflection", "Overview",
                  "Discourse Questions", "Differentiation Strategy"))
    print(f"Built graph: {len(nodes)} nodes, {len(rels)} rels "
          f"({n_lc} LearningComponent, {n_supp} from supplementary)", file=sys.stderr)

    with open(f"{DATA_DIR}/grade8_nodes.jsonl", "w") as f:
        for n in nodes:
            f.write(json.dumps(n) + "\n")
    with open(f"{DATA_DIR}/grade8_relationships.jsonl", "w") as f:
        for r in rels:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(nodes)} nodes / {len(rels)} rels to {DATA_DIR}/grade8_*.jsonl",
          file=sys.stderr)


if __name__ == "__main__":
    main()
