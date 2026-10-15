#!/usr/bin/env python3
"""
repair_duplicate_topics.py — consolidated cleanup pass for the two known extraction artifacts
found across all 6 K-5 grades (see BATCH_INGESTION_REPORT.md): blank-titled phantom Topic nodes
with real content, and empty duplicate Lesson nodes whose real content lives under a same-named
sibling elsewhere. Both were already-built (and paid-for) Batch API results by the time this was
written -- no re-extraction, purely a graph-level repair of data/*_nodes.jsonl / *_relationships.jsonl.

Deliberately does NOT attempt to fix the third known issue (a real, correctly-named topic
absorbing a misattributed lesson range from a neighboring topic, e.g. Grade 3's "Division Using
Units of 2 and 3" repeatedly absorbing early multiplication lessons) -- that requires knowing
which lessons genuinely belong to which topic, a semantic judgment this script has no way to make
from the graph data alone (the whole point of a topic name is what the source PDF actually says
at that page, and the mislabeled case is exactly where the graph's own topic name is already
wrong). Flagged for manual review via the --report-suspicious flag instead of guessed at.

Fix 1 -- blank-title Topic merge: for every LessonGrouping node with groupLevel=='1' and an empty
name, whose lessons carry real LearningComponent content, merge it into the best-matching named
sibling Topic in the same Module: exact topic-number match, else lesson-number continuity (blank
topic's lowest lesson number picks up right after the named topic's highest), else nearest
page-start below it -- same priority order as pdf_to_kg.py's _reconcile_blank_topics(), just
applied to the already-built graph (nodes+rels) instead of merge()'s pre-graph topics dict.
Merging redirects the blank topic's hasPart (lesson children) and hasEducationalAlignment
(standards) edges onto the target, folds page-range properties (min start / max end), removes the
blank topic's own incoming hasPart edge from its Module, and deletes the blank topic node.

Fix 2 -- empty duplicate Lesson removal: for every Lesson node with zero LearningComponent
descendants, if a DIFFERENT Lesson node exists in the same course with a whitespace-normalized
matching name AND has real content, delete the empty one -- it contributes nothing (confirmed
live in Grade 3 and Grade 5: these are phantom copies of the real lesson, not unique content).
Any hasEducationalAlignment edges on the empty lesson are redirected onto the real sibling first
(deduped) so a standard code isn't silently lost if it only appeared on the phantom copy.

Runs Fix 1 before Fix 2 so any newly-co-located duplicate lessons created by a topic merge (two
lessons with the same position number, one real one empty, now under the same parent) get caught
by Fix 2's course-wide name-match scan afterward.

Usage:
  python3 repair_duplicate_topics.py                    # dry run, all 6 grades, prints a diff
  python3 repair_duplicate_topics.py --write             # apply in place
  python3 repair_duplicate_topics.py --course data/grade3_nodes.jsonl   # single course
"""
import argparse
import glob
import json
import re
import sys

GRADES_K5 = sorted(p for p in glob.glob("data/grade[0-9]_nodes.jsonl")
                    + glob.glob("data/gradek_nodes.jsonl"))

_LABEL_NUM = re.compile(r"^(\d+)")


def _numeric_label(label):
    m = _LABEL_NUM.match(str(label or ""))
    return int(m.group(1)) if m else None


def _norm_name(s):
    s = re.sub(r"_{2,}", "___", (s or "").strip())  # fill-in-the-blank runs transcribed with a
    # different underscore count across independent extraction passes (confirmed live: "___" vs
    # "____" in the same lesson title) shouldn't block an otherwise-identical name match
    return re.sub(r"\s+", " ", s)


def _index(nodes, rels):
    by_id = {n["identifier"]: n for n in nodes}
    children_of, parent_of, rels_by_source, rels_by_target = {}, {}, {}, {}
    for r in rels:
        rels_by_source.setdefault(r["source_identifier"], []).append(r)
        rels_by_target.setdefault(r["target_identifier"], []).append(r)
        if r["label"] == "hasPart":
            children_of.setdefault(r["source_identifier"], []).append(r["target_identifier"])
            parent_of[r["target_identifier"]] = r["source_identifier"]
    return by_id, children_of, parent_of, rels_by_source, rels_by_target


def _lc_descendants(node_id, children_of, by_id, memo):
    if node_id in memo:
        return memo[node_id]
    result = []
    for cid in children_of.get(node_id, []):
        c = by_id.get(cid)
        if c is None:
            continue
        if c["labels"][0] == "LearningComponent":
            result.append(c)
        else:
            result.extend(_lc_descendants(cid, children_of, by_id, memo))
    memo[node_id] = result
    return result


def _lesson_nums(topic_id, children_of, by_id):
    nums = []
    for cid in children_of.get(topic_id, []):
        c = by_id.get(cid)
        if c and c["labels"][0] == "Lesson":
            n = _numeric_label(c["properties"].get("position"))
            if n is not None:
                nums.append(n)
    return nums


def find_blank_topic_merges(nodes, rels):
    """[(blank_topic_node, target_topic_node), ...] -- blank topics matched to a real sibling in
    the same Module, using the same 3-tier priority as pdf_to_kg.py's _reconcile_blank_topics()."""
    by_id, children_of, parent_of, _, _ = _index(nodes, rels)
    lc_memo = {}

    topics_by_module = {}
    for n in nodes:
        if n["labels"][0] == "LessonGrouping" and n["properties"].get("groupLevel") == "1":
            mod_id = parent_of.get(n["identifier"])
            topics_by_module.setdefault(mod_id, []).append(n)

    matches = []
    for mod_id, topics in topics_by_module.items():
        blanks = [t for t in topics if not t["properties"].get("name")]
        named = [t for t in topics if t["properties"].get("name")]
        for blank in blanks:
            if not _lc_descendants(blank["identifier"], children_of, by_id, lc_memo):
                continue  # truly empty blank topic -- nothing worth merging, leave as-is
            target = None
            blank_num = blank["properties"].get("position")
            if blank_num:
                target = next((t for t in named if t["properties"].get("position") == blank_num),
                              None)
            if target is None:
                blank_nums = _lesson_nums(blank["identifier"], children_of, by_id)
                if blank_nums:
                    blank_min = min(blank_nums)
                    candidates = []
                    for t in named:
                        named_nums = _lesson_nums(t["identifier"], children_of, by_id)
                        if named_nums and max(named_nums) < blank_min:
                            candidates.append((max(named_nums), t))
                    if candidates:
                        target = max(candidates, key=lambda x: x[0])[1]
            if target is None:
                blank_start = blank["properties"].get("pdfPageStart")
                if blank_start is not None:
                    candidates = [t for t in named
                                  if t["properties"].get("pdfPageStart") is not None
                                  and t["properties"]["pdfPageStart"] <= blank_start]
                    if candidates:
                        target = max(candidates, key=lambda t: t["properties"]["pdfPageStart"])
            if target is not None:
                matches.append((blank, target))
    return matches


def apply_topic_merges(nodes, rels, matches):
    """Mutates nodes/rels in place: redirects the blank topic's hasPart/hasEducationalAlignment
    edges onto its target, folds page-range properties, drops the blank's own incoming hasPart
    edge and the blank node itself. Returns the list of node identifiers removed."""
    removed_ids = []
    target_existing_stds = {}  # target_id -> set of StandardsFrameworkItem identifiers already linked

    for blank, target in matches:
        bid, tid = blank["identifier"], target["identifier"]

        if tid not in target_existing_stds:
            target_existing_stds[tid] = {
                r["target_identifier"] for r in rels
                if r["label"] == "hasEducationalAlignment" and r["source_identifier"] == tid}

        to_drop = []
        for r in rels:
            if r["source_identifier"] == bid and r["label"] == "hasPart":
                r["source_identifier"] = tid
            elif r["source_identifier"] == bid and r["label"] == "hasEducationalAlignment":
                if r["target_identifier"] in target_existing_stds[tid]:
                    to_drop.append(r)  # target already has this standard linked -- drop the dup
                else:
                    r["source_identifier"] = tid
                    target_existing_stds[tid].add(r["target_identifier"])
            elif r["target_identifier"] == bid and r["label"] == "hasPart":
                to_drop.append(r)  # the blank topic's own incoming edge from its Module
        for r in to_drop:
            rels.remove(r)

        tp, bp = target["properties"], blank["properties"]
        b_start, t_start = bp.get("pdfPageStart"), tp.get("pdfPageStart")
        if b_start is not None:
            tp["pdfPageStart"] = min(t_start, b_start) if t_start is not None else b_start
            tp["pageStartLabel"] = (bp.get("pageStartLabel") or tp.get("pageStartLabel")) \
                if tp["pdfPageStart"] == b_start else tp.get("pageStartLabel")
        b_end, t_end = bp.get("pdfPageEnd"), tp.get("pdfPageEnd")
        if b_end is not None:
            tp["pdfPageEnd"] = max(t_end, b_end) if t_end is not None else b_end
            tp["pageEndLabel"] = (bp.get("pageEndLabel") or tp.get("pageEndLabel")) \
                if tp["pdfPageEnd"] == b_end else tp.get("pageEndLabel")

        nodes.remove(blank)
        removed_ids.append(bid)

    return removed_ids


def find_empty_lesson_duplicates(nodes, rels):
    """[(empty_lesson_node, real_sibling_node), ...] -- Lesson nodes with 0 LearningComponent
    descendants that have a name-matching, content-bearing sibling elsewhere in the same course."""
    by_id, children_of, parent_of, _, _ = _index(nodes, rels)
    lc_memo = {}

    lessons = [n for n in nodes if n["labels"][0] == "Lesson"]
    by_name = {}
    for l in lessons:
        by_name.setdefault(_norm_name(l["properties"].get("name")), []).append(l)

    matches = []
    for l in lessons:
        if _lc_descendants(l["identifier"], children_of, by_id, lc_memo):
            continue  # has real content, not a duplicate to remove
        name = _norm_name(l["properties"].get("name"))
        if not name:
            continue  # no name to match on -- can't safely identify a sibling
        siblings = [s for s in by_name.get(name, []) if s["identifier"] != l["identifier"]
                    and _lc_descendants(s["identifier"], children_of, by_id, lc_memo)]
        if siblings:
            matches.append((l, siblings[0]))
    return matches


def apply_lesson_removals(nodes, rels, matches):
    """Mutates nodes/rels in place: redirects the empty lesson's hasEducationalAlignment edges
    onto its real sibling (deduped), drops its own incoming hasPart edge, deletes the node."""
    removed_ids = []
    target_existing_stds = {}

    for empty, real in matches:
        eid, rid = empty["identifier"], real["identifier"]
        if rid not in target_existing_stds:
            target_existing_stds[rid] = {
                r["target_identifier"] for r in rels
                if r["label"] == "hasEducationalAlignment" and r["source_identifier"] == rid}

        to_drop = []
        for r in rels:
            if r["source_identifier"] == eid and r["label"] == "hasEducationalAlignment":
                if r["target_identifier"] in target_existing_stds[rid]:
                    to_drop.append(r)
                else:
                    r["source_identifier"] = rid
                    target_existing_stds[rid].add(r["target_identifier"])
            elif r["target_identifier"] == eid and r["label"] == "hasPart":
                to_drop.append(r)
        for r in to_drop:
            rels.remove(r)

        nodes.remove(empty)
        removed_ids.append(eid)

    return removed_ids


def report_suspicious_wide_topics(nodes, rels):
    """Detection-only: named topics whose lesson-number span looks too wide to be one real
    topic (the "Division Using Units of 2 and 3" pattern) -- printed for manual review, never
    auto-fixed since there's no reliable signal for where the true split point is."""
    by_id, children_of, parent_of, _, _ = _index(nodes, rels)
    topics = [n for n in nodes if n["labels"][0] == "LessonGrouping"
              and n["properties"].get("groupLevel") == "1" and n["properties"].get("name")]
    flagged = []
    for t in topics:
        nums = sorted(_lesson_nums(t["identifier"], children_of, by_id))
        if nums and (len(nums) > 15 or (nums[-1] - nums[0]) > 15):
            flagged.append((t, nums))
    return flagged


def apply(nodes, rels):
    """Runs both fixes on one course's nodes/rels, in place. Returns a stats dict."""
    topic_matches = find_blank_topic_merges(nodes, rels)
    merged_topic_ids = apply_topic_merges(nodes, rels, topic_matches)

    lesson_matches = find_empty_lesson_duplicates(nodes, rels)
    removed_lesson_ids = apply_lesson_removals(nodes, rels, lesson_matches)

    suspicious = report_suspicious_wide_topics(nodes, rels)

    return {
        "topics_merged": [(b["properties"].get("pdfPageStart"), t["properties"].get("name"))
                           for b, t in topic_matches],
        "lessons_removed": [l["properties"].get("name") for l, _ in lesson_matches],
        "suspicious_wide_topics": [(t["properties"].get("name"), nums) for t, nums in suspicious],
        "merged_topic_ids": merged_topic_ids,
        "removed_lesson_ids": removed_lesson_ids,
    }


def process_course(jsonl_path, write):
    rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
    print(f"\n=== {jsonl_path} ===", file=sys.stderr)
    with open(jsonl_path) as f:
        nodes = [json.loads(l) for l in f]
    with open(rels_path) as f:
        rels = [json.loads(l) for l in f]

    n_nodes_before, n_rels_before = len(nodes), len(rels)
    stats = apply(nodes, rels)

    print(f"  blank topics merged: {len(stats['topics_merged'])}", file=sys.stderr)
    for page, target_name in stats["topics_merged"]:
        print(f"    blank@page{page} -> {target_name!r}", file=sys.stderr)
    print(f"  empty duplicate lessons removed: {len(stats['lessons_removed'])}", file=sys.stderr)
    for name in stats["lessons_removed"][:10]:
        print(f"    {name!r}", file=sys.stderr)
    if len(stats["lessons_removed"]) > 10:
        print(f"    ... and {len(stats['lessons_removed']) - 10} more", file=sys.stderr)
    print(f"  suspicious wide topics (NOT auto-fixed, needs manual review): "
          f"{len(stats['suspicious_wide_topics'])}", file=sys.stderr)
    for name, nums in stats["suspicious_wide_topics"]:
        print(f"    {name!r}: lesson#s {nums}", file=sys.stderr)
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

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                         help="Overwrite data/*_nodes.jsonl and *_relationships.jsonl in place "
                              "(default: dry run)")
    parser.add_argument("--course", help="Process a single course's data/*_nodes.jsonl path only "
                         "(default: all 6 K-5 grades)")
    args = parser.parse_args()

    courses = [args.course] if args.course else GRADES_K5
    for jsonl_path in courses:
        process_course(jsonl_path, args.write)

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl in place.",
              file=sys.stderr)
        print("(Reloading the live Neo4j from the corrected JSONL is a separate, explicit step.)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
