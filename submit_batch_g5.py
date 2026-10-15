#!/usr/bin/env python3
"""
submit_batch_g5.py — submit the Grade 5 module PDFs to the Claude Batch API, same text-first
pipeline proven for Algebra I / Grade 8 / Grade 6 / Grade 7.

Unlike G6/7/8's flat "all module PDFs in one folder" layout, G5's source puts each module PDF in
its own subfolder ("M G5 Module 1: Place Value and Decimals/<pdf>") -- so discovery uses
discover_modules(..., recursive=True). Non-module PDFs under BASE (Course-Level Documents,
Program-Level Resources) don't match the "Module N" filename pattern and are skipped with a
warning, same as any other non-matching PDF.

Usage: python3 submit_batch_g5.py
"""
import batch_pipeline as bp

BASE = "/home/talha/Downloads/G5/"
MODULES = bp.discover_modules(BASE, recursive=True)
COURSE_NODES_PATH = "data/grade5_nodes.jsonl"  # lets backfill_page_labels.py auto-discover MODULES
STATE_PATH = "data/batch_state/batch_g5_state.json"

if __name__ == "__main__":
    print(f"Discovered {len(MODULES)} module(s): {list(MODULES)}")
    bp.submit_course_batch("g5", MODULES, STATE_PATH, custom_id_prefix="G5")
