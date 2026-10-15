#!/usr/bin/env python3
"""
build_batch_g6_course.py — poll the Grade 6 extraction batch (submit_batch_g6.py), collect
results per module, merge each module, and build the Course "Grade 6 Math" graph.

Writes data/grade6_nodes.jsonl / data/grade6_relationships.jsonl -- a SEPARATE course graph,
additive to Algebra I / Grade 8 Math (shares the same teks_math_case.json standards backbone;
content node IDs are namespaced by course_name="Grade 6 Math" via build_graph()'s `scope`, so no
collision with other courses' "Module 1"/"Topic 1"/etc. even though ordinal names repeat).

Usage: python3 build_batch_g6_course.py
"""
import os

import batch_pipeline as bp
from submit_batch_g6 import MODULES, STATE_PATH

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

if __name__ == "__main__":
    bp.build_course_from_batch(STATE_PATH, MODULES, course_name="Grade 6 Math",
                                out_stem=f"{DATA_DIR}/grade6")
