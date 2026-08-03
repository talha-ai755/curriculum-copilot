#!/usr/bin/env python3
"""
submit_reextraction_batch.py — submit the full text-first re-extraction (all 5 modules) to the
Claude Batch API.

Reconstructs each module's page-numbered text from the already-built curriculum_raw_text Qdrant
collection (same grouping logic as build_content_index.py's build_page_text() — no need to
re-run docling), batches it via pdf_to_kg.build_page_batches(), and submits every batch across
all 5 modules as ONE Batch API call (plain text requests are small; no size-capping/bin-packing
needed, unlike the old base64-PDF batches).

Saves batch id + custom_id->(module, first, last) map to /tmp/reextraction_batch_state.json so
collect_reextraction_batch.py can poll and assemble results later.

Usage: python3 submit_reextraction_batch.py
"""
import json
import sys
from collections import defaultdict

import anthropic

import pdf_to_kg as p2k
import semantic_index as si

MODULES = ["Module 1", "Module 2", "Module 3", "Module 4", "Module 5"]
STATE_PATH = "/tmp/reextraction_batch_state.json"


def build_page_text_all_modules(client):
    """Group every curriculum_raw_text chunk by (module, pdf_page) -> concatenated text.
    Same logic as build_content_index.py's build_page_text(), split per module here."""
    page_text = defaultdict(lambda: defaultdict(list))
    offset = None
    while True:
        points, offset = client.scroll(collection_name=si.RAW_COLLECTION, limit=500,
                                        offset=offset, with_payload=True)
        for p in points:
            module = p.payload.get("module", "")
            page = p.payload.get("pdf_page")
            if module and page is not None:
                page_text[module][page].append(p.payload.get("text", ""))
        if offset is None:
            break
    return {mod: {page: " ".join(texts) for page, texts in pages.items()}
            for mod, pages in page_text.items()}


def main():
    qc = si.get_client()
    print("Reconstructing per-module page text from curriculum_raw_text...", file=sys.stderr)
    all_page_text = build_page_text_all_modules(qc)
    for mod in MODULES:
        print(f"  {mod}: {len(all_page_text.get(mod, {}))} pages", file=sys.stderr)

    requests = []
    custom_id_map = {}
    for module_name in MODULES:
        page_text = all_page_text.get(module_name, {})
        if not page_text:
            print(f"  ! no pages found for {module_name}, skipping", file=sys.stderr)
            continue
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
