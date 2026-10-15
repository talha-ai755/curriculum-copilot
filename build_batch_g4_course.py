#!/usr/bin/env python3
"""
build_batch_g4_course.py — poll the Grade 4 extraction batch (submit_batch_g4.py), collect
results per module, merge each module, and build the Course "Grade 4 Math" graph.

Writes data/grade4_nodes.jsonl / data/grade4_relationships.jsonl -- a SEPARATE course graph,
additive to the other courses (shares the same teks_math_case.json standards backbone; content
node IDs are namespaced by course_name="Grade 4 Math" via build_graph()'s `scope`, so no collision
with other courses' "Module 1"/"Topic 1"/etc. even though ordinal names repeat).

Usage: python3 build_batch_g4_course.py
"""
import os

import batch_pipeline as bp
from submit_batch_g4 import MODULES, STATE_PATH

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

if __name__ == "__main__":
    bp.build_course_from_batch(STATE_PATH, MODULES, course_name="Grade 4 Math",
                                out_stem=f"{DATA_DIR}/grade4")
