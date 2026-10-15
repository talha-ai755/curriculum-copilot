#!/usr/bin/env python3
"""
submit_batch_g8.py — submit the Grade 8 module PDFs to the Claude Batch API, under the same
text-first pipeline (docling page-numbered text -> Claude) already proven for Algebra I, including
the discourse_questions/differentiation_strategy supplementary-type fix.

Thin per-course config over batch_pipeline.py's shared submit logic (see that module's docstring
for why this stays a real per-course file rather than one generic script: backfill_page_labels.py
auto-discovers new courses by importing every submit_batch_*.py here and reading MODULES +
COURSE_NODES_PATH).

Usage: python3 submit_batch_g8.py
"""
import batch_pipeline as bp

BASE = "/home/talha/Downloads/SM G8: Teacher Editions/Teacher Edition by Module/"
MODULES = {
    "Module 1": BASE + "Eighth Grade Math Teacher Edition, Volume 1 Module 1.pdf",
    "Module 2": BASE + "Eighth Grade Math Teacher Edition, Volume 1 Module 2.pdf",
    "Module 3": BASE + "Eighth Grade Math Teacher Edition, Volume 2 Module 3.pdf",
    "Module 4": BASE + "Eighth Grade Math Teacher Edition, Volume 2 Module 4.pdf",
    "Module 5": BASE + "Eighth Grade Math Teacher Edition, Volume 2 Module 5.pdf",
}
COURSE_NODES_PATH = "data/grade8_nodes.jsonl"  # lets backfill_page_labels.py auto-discover MODULES
STATE_PATH = "data/batch_state/batch_g8_state.json"

if __name__ == "__main__":
    bp.submit_course_batch("g8", MODULES, STATE_PATH, custom_id_prefix="G8")
