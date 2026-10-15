#!/usr/bin/env python3
"""
graph_bench.py — generate a question set WITH ground-truth answers straight from the knowledge
graph, and optionally score the real answer_with_tools() agent against it.

Why this exists: test_question_set.py runs hand-written questions and just prints the answers for a
human to eyeball. There is no automatic pass/fail, so a regression in the graph or agent is only
noticed by reading transcripts. Idea borrowed from K12-KGraph's K12-Bench, which derives questions
from graph queries so the expected answer is known by construction.

Question families (all derived from data/*_nodes.jsonl + *_relationships.jsonl):
  prereq   "What standards build towards <code>?"          expected = sources of buildsTowards
  neighbor "Which standards does the lesson <name> support?"  expected = lesson -> hasPart* -> LC
                                                              -> supports -> standard codes
  locate   "Which module/topic is the lesson <name> in?"   expected = names of hasPart ancestors

Scoring is deliberately simple and deterministic: recall = fraction of expected items (standard
codes or names, case-insensitive) that appear in the answer text; a question passes at recall 1.0.

Usage:
  python3 graph_bench.py --out graph_bench.jsonl            # generate only, no API calls
  python3 graph_bench.py --run --limit 20                   # generate + score against the agent
  python3 graph_bench.py --families prereq locate --per-family 10 --seed 1
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict

from backfill_page_labels import COURSES


def load_all():
    nodes, rels = [], []
    for nodes_path in COURSES:
        with open(nodes_path) as f:
            nodes += [json.loads(l) for l in f]
        with open(nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")) as f:
            rels += [json.loads(l) for l in f]
    return nodes, rels


def build_questions(nodes, rels, families, per_family, rng):
    by_id = {n["identifier"]: n for n in nodes}
    children, parents = defaultdict(list), defaultdict(list)
    supports, builds_in = defaultdict(set), defaultdict(set)
    for r in rels:
        s, t = r["source_identifier"], r["target_identifier"]
        if r["label"] == "hasPart":
            children[s].append(t)
            parents[t].append(s)
        elif r["label"] == "supports":
            supports[s].add(t)
        elif r["label"] == "buildsTowards":
            builds_in[t].add(s)

    def code(i):
        return (by_id.get(i, {}).get("properties", {}).get("code") or "").strip()

    def name(i):
        return (by_id.get(i, {}).get("properties", {}).get("name") or "").strip()

    out = {f: [] for f in families}

    if "prereq" in families:
        for tgt, srcs in builds_in.items():
            exp = sorted({code(s) for s in srcs if code(s)})
            if code(tgt) and exp:
                out["prereq"].append({
                    "question": f"What standards build towards {code(tgt)}?",
                    "expected": exp})

    lessons = [n for n in nodes if n["labels"][0] == "Lesson" and name(n["identifier"])]
    # Lesson names repeat across grades; only ask about names that are unique so the
    # ground truth is unambiguous.
    counts = defaultdict(int)
    for n in lessons:
        counts[name(n["identifier"])] += 1
    lessons = [n for n in lessons if counts[name(n["identifier"])] == 1]

    if "neighbor" in families:
        for n in lessons:
            stack, seen, std = list(children[n["identifier"]]), set(), set()
            while stack:
                c = stack.pop()
                if c in seen:
                    continue
                seen.add(c)
                stack.extend(children[c])
                std |= {code(s) for s in supports[c] if code(s)}
            if std:
                out["neighbor"].append({
                    "question": f"Which standards does the lesson \"{name(n['identifier'])}\" "
                                f"support?",
                    "expected": sorted(std)})

    if "locate" in families:
        for n in lessons:
            anc, cur, seen = [], n["identifier"], set()
            while parents[cur] and cur not in seen:
                seen.add(cur)
                cur = parents[cur][0]
                if name(cur) and by_id[cur]["labels"][0] in ("LessonGrouping", "Module", "Topic"):
                    anc.append(name(cur))
            if anc:
                out["locate"].append({
                    "question": f"What module or topic is the lesson "
                                f"\"{name(n['identifier'])}\" part of?",
                    "expected": sorted(set(anc))})

    qs = []
    for fam in families:
        pool = out[fam]
        rng.shuffle(pool)
        qs += [dict(q, family=fam) for q in pool[:per_family]]
    return qs


def score(answer, expected):
    a = answer.lower()
    hit = [e for e in expected if e.lower() in a]
    return len(hit) / len(expected), hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--families", nargs="+", default=["prereq", "neighbor", "locate"],
                    choices=["prereq", "neighbor", "locate"])
    ap.add_argument("--per-family", type=int, default=15)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="graph_bench.jsonl")
    ap.add_argument("--run", action="store_true", help="Score the live agent (uses API keys)")
    ap.add_argument("--limit", type=int, help="Max questions to run when --run")
    args = ap.parse_args()

    nodes, rels = load_all()
    qs = build_questions(nodes, rels, args.families, args.per_family, random.Random(args.seed))
    with open(args.out, "w") as f:
        for q in qs:
            f.write(json.dumps(q) + "\n")
    print(f"Wrote {len(qs)} questions to {args.out}", file=sys.stderr)
    if not args.run:
        return

    import anthropic
    from openai import OpenAI
    import agent
    import graph_store as gs
    import semantic_index as si

    all_nodes, all_rels = agent.load_all_courses()
    driver = gs.get_driver(gs.DEFAULT_URI, gs.DEFAULT_USER, gs.DEFAULT_PASSWORD)
    system = agent.build_system_prompt(gs.schema_description(all_nodes, all_rels),
                                       os.environ.get("PDF_BASE_URL"))
    qc, oc, client = si.get_client(), OpenAI(), anthropic.Anthropic()

    per_fam = defaultdict(list)
    for q in qs[: args.limit]:
        try:
            answer, _ = agent.answer_with_tools(client, system, q["question"], driver, qc, oc)
        except Exception as e:
            print(f"ERROR {q['question']}: {e}", file=sys.stderr)
            per_fam[q["family"]].append(0.0)
            continue
        recall, hit = score(answer, q["expected"])
        per_fam[q["family"]].append(recall)
        print(f"[{q['family']}] recall={recall:.2f} ({len(hit)}/{len(q['expected'])}) "
              f"{q['question']}", file=sys.stderr)
    driver.close()

    print("\nfamily    n   mean recall   pass@1.0", file=sys.stderr)
    for fam, rs in per_fam.items():
        print(f"{fam:<9} {len(rs):<3} {sum(rs)/len(rs):>10.2f} {sum(r == 1 for r in rs)/len(rs):>10.2f}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
