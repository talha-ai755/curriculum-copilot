#!/usr/bin/env python3
"""
build_batch_g1_course.py — poll the Grade 1 extraction batch (submit_batch_g1.py), collect
results per module, merge each module, and build the Course "Grade 1 Math" graph.

Writes data/grade1_nodes.jsonl / data/grade1_relationships.jsonl -- a SEPARATE course graph,
additive to the other courses (shares the same teks_math_case.json standards backbone; content
node IDs are namespaced by course_name="Grade 1 Math" via build_graph()'s `scope`, so no collision
with other courses' "Module 1"/"Topic 1"/etc. even though ordinal names repeat).

Usage: python3 build_batch_g1_course.py
"""
import os

import batch_pipeline as bp
from submit_batch_g1 import MODULES, STATE_PATH

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

if __name__ == "__main__":
    bp.build_course_from_batch(STATE_PATH, MODULES, course_name="Grade 1 Math",
                                out_stem=f"{DATA_DIR}/grade1")
