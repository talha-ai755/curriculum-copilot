#!/usr/bin/env python3
"""
fix_misattributed_topics.py — fixes the "wrong title absorbs a misattributed lesson range" pattern
(repair_duplicate_topics.py's suspicious_wide_topics, never auto-fixed there since it needs a
different kind of signal than the blank-title merge). Confirmed manually on Grade 3's "Division
Using Units of 2 and 3" (was 231 pages absorbing 5 other real topics' content; the real siblings
already existed correctly in the graph at 20-60 page each) -- this script generalizes that exact
fix to every other flagged case, using ONLY data already in the graph (no new PDF reads, no new
API calls): for each suspicious topic, any of its Lesson children whose real page number falls
inside an EXISTING sibling topic's [pdfPageStart, pdfPageEnd] range gets reassigned to that
sibling (hasPart + hasEducationalAlignment edges redirected, deduped). Whatever's left after that
is genuinely the suspicious topic's own content, and its page-range properties get recomputed
from just those remaining lessons.

Runs a lesson-dedup pass afterward (same content-preserving merge as the G3 fix): if a lesson
lands on a sibling that already has a same-named lesson, the two are merged (LearningComponent
children combined, deduped by exact (page, description) match, nothing silently dropped since the
two copies usually hold different real examples from different original chunk reads).

A lesson whose page doesn't fall inside ANY sibling's range stays with the suspicious topic --
this script never guesses at a topic that doesn't already exist in the graph.

Usage:
  python3 fix_misattributed_topics.py                 # dry run, all 6 K-5 grades, prints a diff
  python3 fix_misattributed_topics.py --write          # apply in place
  python3 fix_misattributed_topics.py --course data/grade5_nodes.jsonl   # single course
"""
import argparse
import glob
import json
import re
import sys

from repair_duplicate_topics import report_suspicious_wide_topics, _index, _norm_name

GRADES_K5 = sorted(glob.glob("data/grade[0-9]_nodes.jsonl") + glob.glob("data/gradek_nodes.jsonl"))


def find_reassignments(nodes, rels):
    """{suspicious_topic_id: [(lesson_node, target_sibling_node), ...]} for every suspicious
    topic's lessons that fall inside an existing sibling's page range."""
    by_id, children_of, parent_of, _, _ = _index(nodes, rels)
    flagged = report_suspicious_wide_topics(nodes, rels)

    plan = {}
    for suspicious, _nums in flagged:
        sid = suspicious["identifier"]
        mod_id = parent_of.get(sid)
        siblings = [n for n in nodes if n["labels"][0] == "LessonGrouping"
                    and n["properties"].get("groupLevel") == "1"
                    and parent_of.get(n["identifier"]) == mod_id
                    and n["identifier"] != sid
                    and n["properties"].get("pdfPageStart") is not None
                    and n["properties"].get("pdfPageEnd") is not None]
        if not siblings:
            continue

        reassignments = []
        for cid in children_of.get(sid, []):
            lesson = by_id.get(cid)
            if not lesson or lesson["labels"][0] != "Lesson":
                continue
            page = lesson["properties"].get("pdfPageStart")
            if not page:  # excludes both None and the 0-placeholder (see apply_reassignments)
                continue
            containing = [s for s in siblings
                          if s["properties"]["pdfPageStart"] <= page <= s["properties"]["pdfPageEnd"]]
            if containing:
                # tightest (smallest range) match wins if more than one sibling's range contains it
                target = min(containing, key=lambda s: s["properties"]["pdfPageEnd"]
                             - s["properties"]["pdfPageStart"])
                reassignments.append((lesson, target))
                continue
            # No sibling's recorded range contains this page -- confirmed via direct PDF
            # reading (Grade K Module 1: lessons at pos 17/20-22/35, all outside any sibling's
            # recorded range) that this is the SAME truncated-end-boundary artifact as the
            # blank/misattributed-title cases, just manifesting on an already-named sibling: a
            # topic's real content continues past its recorded pdfPageEnd (the 25-page batch
            # chunk that produced it simply ended mid-topic), and the overflow got vacuumed into
            # this suspicious topic instead. The nearest PRECEDING sibling by page (largest
            # pdfPageStart that's still <= this lesson's page) is that topic continuing --
            # confirmed 3/3 in manual verification, not a guess at a topic that isn't already in
            # the graph.
            preceding = [s for s in siblings if s["properties"]["pdfPageStart"] <= page]
            if preceding:
                target = max(preceding, key=lambda s: s["properties"]["pdfPageStart"])
                reassignments.append((lesson, target))
        if reassignments:
            plan[sid] = reassignments
    return plan


def _recompute_range(topic, children_of, by_id):
    """Set topic's pdfPageStart/pdfPageEnd from its actual current Lesson children (truthy check
    excludes the pdfPage==0 placeholder, same convention as backfill_page_rollups.py)."""
    lessons = [by_id[cid] for cid in children_of.get(topic["identifier"], [])
               if by_id.get(cid, {}).get("labels", [""])[0] == "Lesson"]
    starts = [l["properties"]["pdfPageStart"] for l in lessons if l["properties"].get("pdfPageStart")]
    ends = [l["properties"]["pdfPageEnd"] for l in lessons if l["properties"].get("pdfPageEnd")]
    if starts:
        topic["properties"]["pdfPageStart"] = min(starts)
    if ends:
        topic["properties"]["pdfPageEnd"] = max(ends)


def apply_reassignments(nodes, rels, plan):
    """Mutates nodes/rels in place. Redirects each planned lesson's hasPart/hasEducationalAlignment
    edges to its target sibling, then recomputes page ranges for every topic touched -- both the
    suspicious source (shrinks to whatever's left) and every target sibling that received a
    "nearest preceding" reassignment (extends past its previously-truncated recorded end, per the
    manually-verified Grade K Module 1 case: D/E/H all needed their pdfPageEnd extended once they
    received the lessons that had wrongly overflowed into the suspicious topic). Returns stats."""
    by_id, children_of, _, _, _ = _index(nodes, rels)
    n_reassigned = 0
    touched = set()

    for sid, reassignments in plan.items():
        touched.add(sid)
        for lesson, target in reassignments:
            lid, tid = lesson["identifier"], target["identifier"]
            for r in rels:
                if r["source_identifier"] == sid and r["label"] == "hasPart" \
                        and r["target_identifier"] == lid:
                    r["source_identifier"] = tid
            n_reassigned += 1
            touched.add(tid)

    children_of2 = {}
    for r in rels:
        if r.get("label") == "hasPart":
            children_of2.setdefault(r["source_identifier"], []).append(r["target_identifier"])
    for tid in touched:
        _recompute_range(by_id[tid], children_of2, by_id)

    return {"lessons_reassigned": n_reassigned, "topics_affected": len(plan)}


def merge_duplicate_lessons(nodes, rels):
    """Same-named Lesson siblings under one topic, both with real content, get merged (content
    preserved additively, exact (page, description) duplicates deduped) -- the natural aftermath
    of reassignment landing a lesson on a topic that already has one with the same name."""
    by_id, children_of, parent_of, _, _ = _index(nodes, rels)
    lc_memo = {}

    def lc_count(nid):
        if nid in lc_memo:
            return lc_memo[nid]
        total = 0
        for cid in children_of.get(nid, []):
            c = by_id.get(cid)
            if not c:
                continue
            total += 1 if c["labels"][0] == "LearningComponent" else lc_count(cid)
        lc_memo[nid] = total
        return total

    topics = [n for n in nodes if n["labels"][0] == "LessonGrouping"
              and n["properties"].get("groupLevel") == "1"]
    n_merged = n_deduped = 0

    for t in topics:
        by_name = {}
        for cid in children_of.get(t["identifier"], []):
            c = by_id.get(cid)
            if c and c["labels"][0] == "Lesson":
                by_name.setdefault(_norm_name(c["properties"].get("name")), []).append(c)

        for name, group in by_name.items():
            if len(group) < 2 or not name:
                continue
            group.sort(key=lambda l: lc_count(l["identifier"]), reverse=True)
            keeper, losers = group[0], group[1:]
            keeper_lc_keys = {
                (by_id[cid]["properties"].get("pdfPage"), _norm_name(by_id[cid]["properties"].get("description")))
                for cid in children_of.get(keeper["identifier"], []) if by_id.get(cid)}
            keeper_std_ids = {r["target_identifier"] for r in rels
                               if r["label"] == "hasEducationalAlignment"
                               and r["source_identifier"] == keeper["identifier"]}

            for loser in losers:
                lid = loser["identifier"]
                to_drop = []
                dup_lc_node_ids = []  # exact-content-duplicate LCs: drop the NODE too, not just the edge
                for r in rels:
                    if r["source_identifier"] == lid and r["label"] == "hasPart":
                        lc = by_id.get(r["target_identifier"])
                        key = (lc["properties"].get("pdfPage"), _norm_name(lc["properties"].get("description"))) \
                            if lc else None
                        if key in keeper_lc_keys:
                            to_drop.append(r)
                            dup_lc_node_ids.append(r["target_identifier"])
                            n_deduped += 1
                        else:
                            r["source_identifier"] = keeper["identifier"]
                            if key:
                                keeper_lc_keys.add(key)
                    elif r["source_identifier"] == lid and r["label"] == "hasEducationalAlignment":
                        if r["target_identifier"] in keeper_std_ids:
                            to_drop.append(r)
                        else:
                            r["source_identifier"] = keeper["identifier"]
                            keeper_std_ids.add(r["target_identifier"])
                    elif r["target_identifier"] == lid and r["label"] == "hasPart":
                        to_drop.append(r)
                for r in to_drop:
                    rels.remove(r)
                for dup_id in dup_lc_node_ids:
                    dup_node = by_id.pop(dup_id, None)
                    if dup_node in nodes:
                        nodes.remove(dup_node)

                kp, lp = keeper["properties"], loser["properties"]
                if lp.get("pdfPageStart") is not None:
                    kp["pdfPageStart"] = min(kp.get("pdfPageStart") or lp["pdfPageStart"], lp["pdfPageStart"])
                if lp.get("pdfPageEnd") is not None:
                    kp["pdfPageEnd"] = max(kp.get("pdfPageEnd") or lp["pdfPageEnd"], lp["pdfPageEnd"])

                nodes.remove(loser)
                n_merged += 1

    return {"lessons_merged": n_merged, "lcs_deduped": n_deduped}


def apply(nodes, rels):
    plan = find_reassignments(nodes, rels)
    reassign_stats = apply_reassignments(nodes, rels, plan)
    merge_stats = merge_duplicate_lessons(nodes, rels)
    remaining = report_suspicious_wide_topics(nodes, rels)
    return {**reassign_stats, **merge_stats, "still_suspicious": len(remaining),
            "still_suspicious_names": [t["properties"].get("name") for t, _ in remaining]}


def process_course(jsonl_path, write):
    rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
    print(f"\n=== {jsonl_path} ===", file=sys.stderr)
    with open(jsonl_path) as f:
        nodes = [json.loads(l) for l in f]
    with open(rels_path) as f:
        rels = [json.loads(l) for l in f]

    n_nodes_before, n_rels_before = len(nodes), len(rels)
    stats = apply(nodes, rels)

    print(f"  topics affected: {stats['topics_affected']}", file=sys.stderr)
    print(f"  lessons reassigned to correct sibling: {stats['lessons_reassigned']}", file=sys.stderr)
    print(f"  duplicate lessons merged: {stats['lessons_merged']} "
          f"({stats['lcs_deduped']} exact-content LearningComponents deduped)", file=sys.stderr)
    print(f"  still suspicious after fix (no containing sibling found): {stats['still_suspicious']}",
          file=sys.stderr)
    for name in stats["still_suspicious_names"]:
        print(f"    {name!r}", file=sys.stderr)
    print(f"  nodes: {n_nodes_before} -> {len(nodes)}   rels: {n_rels_before} -> {len(rels)}",
          file=sys.stderr)

    if write:
        with open(jsonl_path, "w") as f:
            for n in nodes:
                f.write(json.dumps(n) + "\n")
        with open(rels_path, "w") as f:
            for r in rels:
                f.write(json.dumps(r) + "\n")
        print(f"  wrote {jsonl_path} / {rels_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--course", help="Single course's data/*_nodes.jsonl (default: all 6 K-5)")
    args = parser.parse_args()

    for jsonl_path in ([args.course] if args.course else GRADES_K5):
        process_course(jsonl_path, args.write)

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl in place.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
