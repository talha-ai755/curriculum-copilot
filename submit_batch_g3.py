#!/usr/bin/env python3
"""
submit_batch_g3.py — submit the Grade 3 module PDFs to the Claude Batch API, same text-first
pipeline proven for Algebra I / Grade 8 / Grade 7 / Grade 6 / Grade 5 / Grade 4.

Same per-module-subfolder layout as G4/G5 ("M G3 Module 1: .../<pdf>"), so discovery uses
discover_modules(..., recursive=True). Non-module PDFs under BASE (Course-Level Documents,
Program-Level Resources) don't match the "Module N" filename pattern and are skipped with a
warning, same as any other non-matching PDF.

Usage: python3 submit_batch_g3.py
"""
import batch_pipeline as bp

BASE = "/home/talha/Downloads/G3/"
MODULES = bp.discover_modules(BASE, recursive=True)
COURSE_NODES_PATH = "data/grade3_nodes.jsonl"  # lets backfill_page_labels.py auto-discover MODULES
STATE_PATH = "data/batch_state/batch_g3_state.json"

if __name__ == "__main__":
    print(f"Discovered {len(MODULES)} module(s): {list(MODULES)}")
    bp.submit_course_batch("g3", MODULES, STATE_PATH, custom_id_prefix="G3")
