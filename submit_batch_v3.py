#!/usr/bin/env python3
"""
submit_batch_v3.py — submit a FRESH full re-extraction (all 5 modules) to the Claude Batch API,
under the strengthened SYSTEM prompt (verbatim titles + verbatim/complete materials_needed).

Reconstructs each module's page-numbered text directly via docling (curriculum_raw_text, the
earlier Qdrant collection this was cached in, has since been retired) -- so this re-runs docling
on all 5 PDFs, then batches via pdf_to_kg.build_page_batches() and submits every batch across all
5 modules as ONE Batch API call.

Saves batch id + custom_id->(module, first, last) map to /tmp/batch_v3_state.json so
build_batch_v3_course.py can poll and assemble results later.

Usage: python3 submit_batch_v3.py
"""
import json
import sys

import anthropic

import pdf_to_kg as p2k

BASE = "/home/talha/Downloads/SM Algebra I: Teacher Editions/Teacher Edition By Module/"
MODULES = {
    "Module 1": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 1.pdf",
    "Module 2": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 2.pdf",
    "Module 3": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 3.pdf",
    "Module 4": BASE + "Algebra I Math Teacher Edition, Volume 2 Module 4.pdf",
    "Module 5": BASE + "Algebra I Math Teacher Edition, Volume 2 Module 5.pdf",
}
STATE_PATH = "/tmp/batch_v3_state.json"


def main():
    requests = []
    custom_id_map = {}
    for module_name, pdf_path in MODULES.items():
        print(f"Converting {module_name} via docling...", file=sys.stderr)
        page_text = p2k.docling_page_text(pdf_path)
        print(f"  {module_name}: {len(page_text)} pages", file=sys.stderr)
        for first, last, labeled_text in p2k.build_page_batches(page_text, batch_size=25):
            custom_id = f"{module_name.replace(' ', '')}_{first}_{last}"
            requests.append({
                "custom_id": custom_id,
                "params": p2k.text_message_params(labeled_text, f"pages {first}-{last}"),
            })
            custom_id_map[custom_id] = {"module": module_name, "first": first, "last": last}

    print(f"Total batch requests: {len(requests)}", file=sys.stderr)

    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=requests)
    print(f"Submitted batch {batch.id}, status={batch.processing_status}", file=sys.stderr)

    with open(STATE_PATH, "w") as f:
        json.dump({"batch_id": batch.id, "custom_id_map": custom_id_map}, f)
    print(f"State saved to {STATE_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
