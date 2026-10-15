#!/usr/bin/env python3
"""
backfill_topic_citation.py — precompute a reliable "click here to start reading this Topic/
Module" citation for every LessonGrouping node, stored directly as `citationSourcePdfKey` +
`citationPdfPage` properties -- so the chat agent can just read them instead of re-deriving a
representative child live via Cypher every time.

Why this exists: the live query pattern the agent was told to use (`OPTIONAL MATCH (t)-
[:hasPart*1..4]->(lc:LearningComponent) ... ORDER BY lc.pdfPage LIMIT 1`, i.e. "earliest page
among all descendants") is CORRECT for most topics, but a subset of topics also have a shared
module-level front-matter summary blurb linked in as a `hasPart` descendant with an anomalously
low pdfPage (e.g. Grade 6 "Decimals" picks up a "6 SESSIONS ... TEKS: 6.2C..." blurb at pdfPage 5,
when its real dedicated "TOPIC 3 OVERVIEW" page -- and all its actual lesson content -- doesn't
start until pdfPage 287). Neo4j doesn't store description/sourceText (Qdrant-only, see
semantic_index.py), so this can't be fixed by pattern-matching the blurb's text live in Cypher --
but it CAN be fixed structurally: the blurb page is always an isolated outlier, tens to hundreds
of pages away from the topic's real page cluster, while genuine topic-start pages always have
lesson content clustered immediately after them. Confirmed live across all 4 courses (51 topics):
6 topics have a >20-page gap between the naive earliest-page pick and the real content cluster
(Grade 6 Decimals; Grade 7 Multiple Representations of Equations, Drawing Inferences; Grade 8 Line
and Angle Relationships, The Pythagorean Theorem, Volume of Curved Figures) -- Algebra I has none.

Algorithm: sort a grouping node's descendant LearningComponent pdfPages (distinct), walk from the
lowest; if the gap to the next distinct page exceeds GAP_THRESHOLD, that page is an isolated
outlier -- skip it and keep walking. The first page with a nearby "neighbor" (real content
clustered around it) wins. Falls back to the plain minimum if every page turns out isolated (short
topics with few pages) -- never returns nothing for a topic that has any linked content at all.

Runs for every LessonGrouping (both groupLevel="0" Module and groupLevel="1" Topic), not just the
6 known-bad ones -- hardcoding those 6 names would leave the exact same bug for any topic in a
future grade's ingestion. Purely deterministic, no PDF re-read, no Claude call, reuses
backfill_page_labels.py's COURSES auto-discovery.

Usage:
  python3 backfill_topic_citation.py            # dry run, prints a diff summary only
  python3 backfill_topic_citation.py --write     # overwrite data/*_nodes.jsonl in place
"""
import argparse
import json
import sys
from collections import defaultdict

from backfill_page_labels import COURSES
from snapshot import snapshot_course

GAP_THRESHOLD = 20


def _pick_representative(children):
    """children: [(pdfPage, sourcePdfKey, identifier), ...] (may repeat pdfPage across modules
    sharing page numbers -- caller already scopes to one topic's own descendants, so that's fine).
    Returns (pdfPage, sourcePdfKey) for the first page that isn't an isolated outlier, or None."""
    if not children:
        return None
    distinct_pages = sorted(set(p for p, _, _ in children))
    chosen = distinct_pages[-1]
    for i, pg in enumerate(distinct_pages):
        if i == len(distinct_pages) - 1:
            chosen = pg
            break
        if distinct_pages[i + 1] - pg <= GAP_THRESHOLD:
            chosen = pg
            break
    for p, key, _ in children:
        if p == chosen:
            return (p, key)
    return None


def compute_citations(nodes, rels):
    """{grouping_node_identifier: (pdfPage, sourcePdfKey)} for every LessonGrouping."""
    by_id = {n["identifier"]: n for n in nodes}
    children = defaultdict(list)
    for r in rels:
        if r.get("label") == "hasPart":
            children[r["source_identifier"]].append(r["target_identifier"])

    def descendants_lc(node_id, depth=4):
        result = []
        frontier = [node_id]
        for _ in range(depth):
            nxt = []
            for nid in frontier:
                for cid in children.get(nid, []):
                    cnode = by_id.get(cid)
                    if not cnode:
                        continue
                    if cnode["labels"][0] == "LearningComponent":
                        key = cnode["properties"].get("sourcePdfKey")
                        pg = cnode["properties"].get("pdfPage")
                        # pg == 0 is a real, confirmed placeholder for "extraction never got a
                        # page number" (verified: Grade 8 "Financial Literacy: Your Financial
                        # Future" has one, sourcePdfKey set but pageLabel==""), not a genuine
                        # page 1-of-the-PDF -- never a valid citation target on its own.
                        if key and pg:
                            result.append((pg, key, cid))
                    nxt.append(cid)
            frontier = nxt
        return result

    result = {}
    for n in nodes:
        if n["labels"][0] != "LessonGrouping":
            continue
        picked = _pick_representative(descendants_lc(n["identifier"]))
        if picked:
            result[n["identifier"]] = picked
    return result


def apply(nodes, rels):
    """Stamps citationSourcePdfKey/citationPdfPage onto every LessonGrouping node in place.
    Same (nodes, rels) -> stats-dict shape as backfill_sibling_labels.apply()/
    backfill_page_rollups.apply(), so build_course_from_batch() can call this the same way as
    the other in-memory post-processing steps for new courses, not just as a standalone backfill
    for already-built ones."""
    by_id = {n["identifier"]: n for n in nodes}
    citations = compute_citations(nodes, rels)
    filled, corrected = 0, 0
    for ident, (pdf_page, source_key) in citations.items():
        p = by_id[ident]["properties"]
        old_page = p.get("citationPdfPage")
        p["citationSourcePdfKey"] = source_key
        p["citationPdfPage"] = pdf_page
        if old_page is None:
            filled += 1
        elif old_page != pdf_page:
            corrected += 1
    return {"stamped": len(citations), "filled": filled, "corrected": corrected}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Overwrite data/*_nodes.jsonl in place (default: dry run)")
    args = parser.parse_args()

    for jsonl_path in COURSES:
        rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
        print(f"\n=== {jsonl_path} ===", file=sys.stderr)
        with open(jsonl_path) as f:
            nodes = [json.loads(l) for l in f]
        with open(rels_path) as f:
            rels = [json.loads(l) for l in f]

        stats = apply(nodes, rels)
        print(f"  stamped citationSourcePdfKey/citationPdfPage on {stats['stamped']} LessonGrouping "
              f"node(s) ({stats['filled']} new, {stats['corrected']} changed from a prior run)",
              file=sys.stderr)

        if args.write:
            snapshot_course(jsonl_path, "backfill_topic_citation")
            with open(jsonl_path, "w") as f:
                for n in nodes:
                    f.write(json.dumps(n) + "\n")
            print(f"  wrote {jsonl_path}", file=sys.stderr)

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl in place.", file=sys.stderr)
        print("(Reloading Neo4j is a separate, explicit step.)", file=sys.stderr)


if __name__ == "__main__":
    main()
