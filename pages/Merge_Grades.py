#!/usr/bin/env python3
"""
Merge Grades — combine several grades' graphs into one deduped graph.

Upload the nodes.jsonl + relationships.jsonl from each grade you built; this unions them,
dedupes by ID (shared standards collapse), and gives you one combined graph where the grades
connect across each other via the progression edges already present in each.
"""
import json
from collections import Counter

import streamlit as st

import merge_graphs as mg

st.set_page_config(page_title="Merge Grades", page_icon="🔗", layout="wide")
st.title("🔗 Merge Grades")
st.caption("Combine the graphs from different grades (or modules) into one. Shared standards "
           "dedupe automatically, and the cross-grade progression edges connect them.")

files = st.file_uploader(
    "Upload nodes.jsonl + relationships.jsonl from each grade (any order, multiple)",
    type=["jsonl", "json", "txt"], accept_multiple_files=True)

if files and st.button("🔗 Merge", type="primary", use_container_width=True):
    nodes, rels = mg.merge_records(f.getvalue().splitlines() for f in files)
    st.session_state["merged"] = (nodes, rels)

if "merged" in st.session_state:
    nodes, rels = st.session_state["merged"]
    dang = mg.dangling_count(nodes, rels)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Nodes", len(nodes)); c2.metric("Edges", len(rels))
    c3.metric("Courses (grades)", sum(1 for n in nodes.values() if n["labels"][0] == "Course"))
    c4.metric("Dangling", dang)
    (st.success if dang == 0 else st.error)(
        f"Integrity: {dang} dangling endpoints" + (" ✓" if dang == 0 else " ✗"))

    st.write("Node types:", dict(Counter(n["labels"][0] for n in nodes.values())))
    st.write("Edge types:", dict(Counter(r["label"] for r in rels.values())))
    courses = [n["properties"].get("name") for n in nodes.values() if n["labels"][0] == "Course"]
    if courses:
        st.write("Grades in this graph:", ", ".join(sorted(courses)))

    st.download_button("⬇ merged nodes.jsonl", "\n".join(json.dumps(n) for n in nodes.values()),
                       file_name="nodes.jsonl", use_container_width=True)
    st.download_button("⬇ merged relationships.jsonl",
                       "\n".join(json.dumps(r) for r in rels.values()),
                       file_name="relationships.jsonl", use_container_width=True)
elif not files:
    st.info("Build each grade on the Batch Build page, download its two files, then upload "
            "them all here to combine.")
