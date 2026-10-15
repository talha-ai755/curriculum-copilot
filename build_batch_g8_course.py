#!/usr/bin/env python3
"""
build_batch_g8_course.py — poll the Grade 8 extraction batch (submit_batch_g8.py), collect
results per module, merge each module, and build the Course "Grade 8 Math" graph.

Writes grade8_nodes.jsonl/grade8_relationships.jsonl -- a SEPARATE course graph, additive to the
existing Algebra I graph (shares the same teks_math_case.json standards backbone; content node
IDs are namespaced by course_name="Grade 8 Math" via build_graph()'s `scope`, so no collision with
Algebra I's "Module 1"/"Topic 1"/etc. even though the ordinal names repeat).

Thin per-course config over batch_pipeline.py's shared build logic.

Usage: python3 build_batch_g8_course.py
"""
import os

import batch_pipeline as bp
from submit_batch_g8 import MODULES, STATE_PATH

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

if __name__ == "__main__":
    bp.build_course_from_batch(STATE_PATH, MODULES, course_name="Grade 8 Math",
                                out_stem=f"{DATA_DIR}/grade8")
