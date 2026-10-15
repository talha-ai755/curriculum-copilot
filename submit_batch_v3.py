#!/usr/bin/env python3
"""
submit_batch_v3.py — submit a FRESH full re-extraction (all 5 Algebra I modules) to the Claude
Batch API, under the strengthened SYSTEM prompt (verbatim titles + verbatim/complete
materials_needed).

Thin per-course config over batch_pipeline.py's shared submit logic (see that module's docstring
for why this stays a real per-course file rather than one generic script: backfill_page_labels.py
auto-discovers new courses by importing every submit_batch_*.py here and reading MODULES +
COURSE_NODES_PATH).

Usage: python3 submit_batch_v3.py
"""
import batch_pipeline as bp

BASE = "/home/talha/Downloads/SM Algebra I: Teacher Editions/Teacher Edition By Module/"
MODULES = {
    "Module 1": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 1.pdf",
    "Module 2": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 2.pdf",
    "Module 3": BASE + "Algebra I Math Teacher Edition, Volume 1 Module 3.pdf",
    "Module 4": BASE + "Algebra I Math Teacher Edition, Volume 2 Module 4.pdf",
    "Module 5": BASE + "Algebra I Math Teacher Edition, Volume 2 Module 5.pdf",
}
COURSE_NODES_PATH = "data/algebra1_nodes.jsonl"  # lets backfill_page_labels.py auto-discover MODULES
STATE_PATH = "/tmp/batch_v3_state.json"

if __name__ == "__main__":
    bp.submit_course_batch("algebra1", MODULES, STATE_PATH)
