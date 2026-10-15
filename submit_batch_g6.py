#!/usr/bin/env python3
"""
submit_batch_g6.py — submit the Grade 6 module PDFs to the Claude Batch API, same text-first
pipeline proven for Algebra I / Grade 8.

TODO before running: place the Grade 6 Teacher Edition module PDFs under BASE below (filenames
must contain "Module <N>", matching the existing G8/Algebra I naming convention -- see
batch_pipeline.discover_modules()). Verify the printed module list/page counts on first run look
right before trusting the batch submission.

Usage: python3 submit_batch_g6.py
"""
import batch_pipeline as bp

BASE = "/home/talha/Downloads/SM G6: Teacher Editions/Teacher Edition by Module/"  # TODO: verify
MODULES = bp.discover_modules(BASE)
COURSE_NODES_PATH = "data/grade6_nodes.jsonl"  # lets backfill_page_labels.py auto-discover MODULES
STATE_PATH = "data/batch_state/batch_g6_state.json"

if __name__ == "__main__":
    print(f"Discovered {len(MODULES)} module(s): {list(MODULES)}")
    bp.submit_course_batch("g6", MODULES, STATE_PATH, custom_id_prefix="G6")
