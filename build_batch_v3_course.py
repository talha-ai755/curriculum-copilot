#!/usr/bin/env python3
"""
build_batch_v3_course.py — poll the v3 re-extraction batch (submit_batch_v3.py, run under the
strengthened SYSTEM prompt with verbatim titles + verbatim/complete materials_needed), collect
results per module, merge each module, and build the final Course "Algebra I" graph.

Overwrites algebra1_nodes.jsonl/algebra1_relationships.jsonl -- the pre-v3 data is already backed
up as algebra1_nodes.v2.jsonl/algebra1_relationships.v2.jsonl.

Thin per-course config over batch_pipeline.py's shared build logic.

Usage: python3 build_batch_v3_course.py
"""
import os

import batch_pipeline as bp
from submit_batch_v3 import MODULES, STATE_PATH

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

if __name__ == "__main__":
    bp.build_course_from_batch(STATE_PATH, MODULES, course_name="Algebra I",
                                out_stem=f"{DATA_DIR}/algebra1")
