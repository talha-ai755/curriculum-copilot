#!/usr/bin/env python3
"""
test_question_set.py — run every question from Test_Question_Set.docx through the real
answer_with_tools() loop (same logic app.py uses), against the live combined graph of every
course currently in data/ (the QUESTIONS list below was originally written against Algebra I +
Grade 8 Math, so most questions target those two, but the graph loaded is whatever's actually
ingested). Logs each question, the final answer, and the full tool-call transcript to stdout so
results can be reviewed and spot-checked against the real PDFs.

Usage: python3 test_question_set.py
"""
import json
import os
import sys

import anthropic
from openai import OpenAI

import agent
import graph_store as gs
import semantic_index as si

QUESTIONS = [
    "What does this course build toward in later grades?",
    "What standards comes before and after Linear Equations in this course?",
    "What Grade 8 skill leads into this course's linear equations content?",
    "What's the prerequisite for solving quadratic equations?",
    "Show me everything under Module 4, top to bottom.",
    "What's the full hierarchy above the 'Exponential Function Modeling (Assignment)' lesson?",
    "If I've finished Linear Equations, how should I pace the rest of the course. Break any "
        "single topic into a day-by-day calendar with the specific activities and TEKS per session?",
    "I'm on Module 2 — what's a reasonable plan for Modules 3, 4, and 5?",
    "After the 'Solving Linear Equations' lesson (Module 3, Topic 1), what comes next in the "
        "course, in order?",
    "Which Grade 8 skills does A.5B build on?",
    "What do I need to teach before A.9D?",
    "What's the learning progression before A.7A?",
    "Can you find the prerequisite standard for A.3C automatically, or is there no data for that?",
    "From A.5B to A.5C, what changes?",
    "Give me the lessons required to progress from A.5A to A.5C.",
    "What standards does the Quadratic Formula lesson connect to?",
    "What lessons connect to A.3C?",
    "How do I teach slope using a real-world context?",
    "Find something about explaining why a negative times a negative is positive.",
    "Search for 'Talk the Talk' style activities about exponential decay.",
    "Find the activity about: 'Investigating Rational Root Bisection.'",
    "What's the best-matching content for 'students discover a pattern by cutting up shapes'?",
    "Show me only the 'develop' phase activities about slope.",
    "What Overview-type content exists for Module 3?",
    "Find Pacing Guide content specifically for Topic 2 of any module.",
    "Search only within pages 400-500 for anything about systems of equations.",
    "Give me every Self-Reflection entry in the whole course.",
    "Find 'getting started' activities that mention graphing, but only in Module 2.",
    "Give me the name of all modules, their topic names and their lessons.",
    "Give me all the standards that are attached with Exponential Equations for growth and decay.",
    "Give me the prerequisite standard of \"Lesson 1: Exponential Equations for Growth and Decay\".",
    "What is mentioned on page 533?",
    "Give me all the prerequisites from the previous grades for Algebra 1.",
    "Starting from A.5B, what's connected to it — lessons, standards, everything?",
]


def main():
    print("Loading combined graph (every course currently in data/)...", file=sys.stderr)
    nodes, rels = agent.load_all_courses()
    driver = gs.get_driver(gs.DEFAULT_URI, gs.DEFAULT_USER, gs.DEFAULT_PASSWORD)
    schema_doc = gs.schema_description(nodes, rels)
    qc = si.get_client()
    openai_client = OpenAI()
    client = anthropic.Anthropic()
    system = agent.build_system_prompt(schema_doc, os.environ.get("PDF_BASE_URL"))
    print(f"Loaded: {len(nodes)} nodes, {len(rels)} rels. Running {len(QUESTIONS)} questions...\n",
          file=sys.stderr)

    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n{'='*100}\nQ{i}: {q}\n{'='*100}")
        try:
            answer, transcript = agent.answer_with_tools(client, system, q, driver, qc, openai_client)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        print(f"\n--- ANSWER ---\n{answer}")
        print(f"\n--- TOOL CALLS ({len(transcript)}) ---")
        for t in transcript:
            print(f"  [{t['tool']}] input={json.dumps(t['input'])[:200]}")
            result_str = json.dumps(t['result'], default=str)
            print(f"    -> {result_str[:300]}")

    driver.close()
    print("\n\nDONE", file=sys.stderr)


if __name__ == "__main__":
    main()
