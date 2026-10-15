#!/usr/bin/env python3
"""
submit_batch_gk.py — submit the Grade K module PDFs to the Claude Batch API, same text-first
pipeline proven for Algebra I / Grade 8 through Grade 1.

Same per-module-subfolder layout as G1-G5 ("M K Module 1: .../<pdf>"), so discovery uses
discover_modules(..., recursive=True). Non-module PDFs under BASE (Course-Level Documents,
Program-Level Resources) don't match the "Module N" filename pattern and are skipped with a
warning, same as any other non-matching PDF.

Usage: python3 submit_batch_gk.py
"""
import batch_pipeline as bp

BASE = "/home/talha/Downloads/GK/"
MODULES = bp.discover_modules(BASE, recursive=True)
COURSE_NODES_PATH = "data/gradek_nodes.jsonl"  # lets backfill_page_labels.py auto-discover MODULES
STATE_PATH = "/tmp/batch_gk_state.json"

if __name__ == "__main__":
    print(f"Discovered {len(MODULES)} module(s): {list(MODULES)}")
    bp.submit_course_batch("gk", MODULES, STATE_PATH, custom_id_prefix="GK")
