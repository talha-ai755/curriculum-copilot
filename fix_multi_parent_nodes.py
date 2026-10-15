#!/usr/bin/env python3
"""
fix_multi_parent_nodes.py — one-off repair for the 17 multi-parent LearningComponent/Assessment
nodes found by find_multi_parent_nodes.py across Grade 3/4/5 (see that script's docstring for the
detection side of this bug).

Each of the 17 was individually resolved by reading the real PDF page (pypdfium2) and comparing
against the course's OTHER, correctly-single-parented content in the same neighborhood -- the
correct fix turned out to be one of three different things per node, not a single mechanical
rule (checked live: page-range containment against the specific-topic candidate's own
pdfPageStart/pdfPageEnd does NOT reliably pick the right one -- same truncated-range issue
BATCH_INGESTION_REPORT.md documents elsewhere):

  RESOLUTIONS["reparent"]: the node is real, uncaptured content (UDL/differentiation notes,
    templates, Student Debrief questions -- pdf_to_kg.py's supplementary categories) that belongs
    under an EXISTING, correctly-placed Lesson node one level down from either of its current
    (wrong) topic-level parents -- confirmed by reading the page and finding it's the same
    lesson/page-neighborhood as that Lesson's other, correctly-parented children. Drop every
    current hasPart edge, add exactly one new hasPart edge from the given Lesson.

  RESOLUTIONS["keep_module"]: neither existing specific-topic candidate matches the page's real
    content (e.g. Grade 4's page-13 node is generic Module Overview vocabulary, not tied to any
    one topic) -- drop the wrong specific-topic edge(s), keep the existing module-level
    (groupLevel="0") edge as-is. No new edge added.

  RESOLUTIONS["delete"]: the node's real text is a verbatim/near-verbatim duplicate of a
    LearningComponent that ALREADY exists, correctly single-parented, at the exact same PDF page
    (confirmed by comparing descriptions side by side -- e.g. Grade 4's page-57/58 nodes restate
    the same Exit Ticket/Homework content, down to the same example numbers, that the original
    extraction already captured elsewhere). Delete the node entirely (all its rels + itself) --
    nothing unique would be lost.

Usage:
  python3 fix_multi_parent_nodes.py              # dry run, prints the diff
  python3 fix_multi_parent_nodes.py --write       # apply in place to data/*_relationships.jsonl
                                                   # and data/*_nodes.jsonl (for deletions)
"""
import argparse
import json
import sys
from snapshot import snapshot_course

# {course nodes_path: [(node_id, resolution, extra), ...]}
# extra = correct parent identifier for "reparent", None otherwise.
RESOLUTIONS = {
    "data/grade3_nodes.jsonl": [
        # All 5 are the missing Sprint/Problem-Set/Exit-Ticket tail of Lesson 17, which already
        # exists correctly under "Attributes of Two- and Three-Dimensional Figures" with pages
        # 247-250 -- these cover 252-256, the rest of that same lesson.
        ("yo:541d93ec-02d7-5943-a9b3-56bb45768eed", "reparent", "yo:9ac5eadd-5a45-5ccb-bd36-67cc5d132ee5"),
        ("yo:4f9c34cf-80dd-5454-b706-d7c682f33c9b", "reparent", "yo:9ac5eadd-5a45-5ccb-bd36-67cc5d132ee5"),
        ("yo:f0eb7353-2dc3-5a72-ac6b-cad7934e8179", "reparent", "yo:9ac5eadd-5a45-5ccb-bd36-67cc5d132ee5"),
        ("yo:f2f8291a-cc0d-533e-bd49-ab05b9d15fe8", "reparent", "yo:9ac5eadd-5a45-5ccb-bd36-67cc5d132ee5"),
        ("yo:a9a98734-e0f3-53e4-a636-d01ccab441b0", "reparent", "yo:9ac5eadd-5a45-5ccb-bd36-67cc5d132ee5"),
    ],
    "data/grade4_nodes.jsonl": [
        # Module Overview vocabulary (page 13) isn't tied to any one topic -- keep the existing
        # module-level edge, drop the wrong "Input-Output Tables..." one.
        ("yo:9ba14b60-0eea-5fcb-bc7e-c12242ea350b", "keep_module", None),
        # Student Debrief for Lesson 3 (page 54) -- real content missing from the existing
        # Lesson 3 node ("Metric Unit Conversions" topic), which jumps from page 53 to 55.
        ("yo:ef760f2b-1fe9-5d5f-9b81-a23a39c4259a", "reparent", "yo:9b94357b-9dad-5326-9e81-a6e8f8def23c"),
        # Exit Ticket (page 57) and Homework (page 58) restate, in more words, the same content
        # the existing Lesson 3 node already has at those exact pages ("hot tub" / capacity
        # conversion homework) -- true duplicates, not missing content.
        ("yo:1514e9c1-9b10-562a-a6a9-f0a126f1c165", "delete", None),
        ("yo:437260e1-e8fd-5aba-882f-5c43a2ff6759", "delete", None),
    ],
    "data/grade5_nodes.jsonl": [
        # All 8 are UDL/differentiation notes, a lesson template, or Student Debrief questions --
        # pdf_to_kg.py's supplementary categories -- missing from their real Lesson's captured
        # content, confirmed by page-reading each one's neighborhood.
        # Lesson 10 "Multiply mixed number factors..." (pages 147-158, area model / distributive property)
        ("yo:705431b2-53cd-589a-861a-854c74996a98", "reparent", "yo:e1a87225-101e-512d-83b9-ca74687a8733"),
        ("yo:dbef549a-6dd4-5d69-855a-3293ed33eb92", "reparent", "yo:e1a87225-101e-512d-83b9-ca74687a8733"),
        # Lesson 14 "Define and construct triangles..." (pages 206-216)
        ("yo:db06634a-f4d0-55ab-83b6-9b5aec703149", "reparent", "yo:cb8752e7-813f-5415-92da-1a6b018cd0fd"),
        ("yo:3ecdc816-f0d8-557f-a9fa-7403085d0660", "reparent", "yo:cb8752e7-813f-5415-92da-1a6b018cd0fd"),
        ("yo:b1976d7f-e67a-51c1-bfc9-0f627b078c16", "reparent", "yo:cb8752e7-813f-5415-92da-1a6b018cd0fd"),
        # Lesson 15 "Draw trapezoids..." (pages 219-230)
        ("yo:22415973-f47e-5d8a-85de-08184341a9ef", "reparent", "yo:29f9e876-f1f1-5bf9-821e-d38ba85e12cf"),
        ("yo:25dfabf4-85d7-5e44-8ee6-f8ec68f49e53", "reparent", "yo:29f9e876-f1f1-5bf9-821e-d38ba85e12cf"),
        ("yo:18fca782-8207-5b7d-ae6a-27d0dc971989", "reparent", "yo:29f9e876-f1f1-5bf9-821e-d38ba85e12cf"),
    ],
}


def apply(nodes, rels, resolutions):
    """Apply one course's RESOLUTIONS to `nodes`/`rels` in place. Idempotent: re-running on an
    already-fixed course changes nothing, so batch_pipeline.build_course_from_batch() can run it
    on every build. Returns {"deleted": set of removed ids, "stale": [(node_id, resolution)]}.

    `stale` lists resolutions whose node isn't in this graph. Node ids are deterministic from the
    extraction, so a rebuild from the SAME batch results reproduces them and every resolution
    applies; a fresh re-extraction can produce different ids, and then these hand-made fixes no
    longer match anything -- reported, never silently skipped, so they can be redone by hand
    (a "delete" whose node is already gone is just already applied, not stale)."""
    by_id = {n["identifier"]: n for n in nodes}
    node_ids = set(by_id)
    to_delete_node_ids, stale = set(), []
    for node_id, resolution, extra in resolutions:
        if node_id not in node_ids:
            if resolution != "delete":
                stale.append((node_id, resolution))
            continue
        current_parents = [r["source_identifier"] for r in rels
                            if r["label"] == "hasPart" and r["target_identifier"] == node_id]
        if resolution == "delete":
            print(f"  DELETE {node_id} (duplicate of existing content, {len(current_parents)} "
                  f"hasPart edges + node removed)", file=sys.stderr)
            to_delete_node_ids.add(node_id)
            rels[:] = [r for r in rels if not (r["label"] == "hasPart"
                                                and r["target_identifier"] == node_id)]
            rels[:] = [r for r in rels if not (r["source_identifier"] == node_id
                                                or r["target_identifier"] == node_id)]
        elif resolution == "keep_module":
            if len(current_parents) <= 1:
                continue  # already applied
            # The module-level (groupLevel "0") parent, not whichever edge happens to come first.
            modules = [p for p in current_parents
                       if by_id.get(p, {}).get("properties", {}).get("groupLevel") == "0"]
            if len(modules) != 1:
                stale.append((node_id, resolution))
                continue
            keep_parent = modules[0]
            print(f"  KEEP_MODULE {node_id}: keep edge from {keep_parent!r}, drop "
                  f"{len(current_parents) - 1} other(s)", file=sys.stderr)
            rels[:] = [r for r in rels if not (r["label"] == "hasPart"
                                                and r["target_identifier"] == node_id
                                                and r["source_identifier"] != keep_parent)]
        elif resolution == "reparent":
            if current_parents == [extra]:
                continue  # already applied
            if extra not in node_ids:
                stale.append((node_id, resolution))
                continue
            print(f"  REPARENT {node_id}: drop {len(current_parents)} existing edge(s), "
                  f"add hasPart from {extra}", file=sys.stderr)
            rels[:] = [r for r in rels if not (r["label"] == "hasPart"
                                                and r["target_identifier"] == node_id)]
            rels.append({"type": "relationship",
                         "identifier": f"yo:fix-multiparent|{extra}|{node_id}",
                         "label": "hasPart",
                         "properties": {"identifier": f"yo:fix-multiparent|{extra}|{node_id}",
                                        "relationshipType": "hasPart"},
                         "source_identifier": extra, "source_labels": ["Lesson"],
                         "target_identifier": node_id, "target_labels": ["LearningComponent"]})
        else:
            raise ValueError(resolution)

    if to_delete_node_ids:
        nodes[:] = [n for n in nodes if n["identifier"] not in to_delete_node_ids]
    for node_id, resolution in stale:
        print(f"  STALE {resolution} {node_id}: node not in this graph (re-extracted since this "
              f"fix was written?) -- redo this fix by hand", file=sys.stderr)
    return {"deleted": to_delete_node_ids, "stale": stale}


def process_course(nodes_path, resolutions, write):
    rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
    with open(nodes_path) as f:
        nodes = [json.loads(l) for l in f]
    with open(rels_path) as f:
        rels = [json.loads(l) for l in f]

    print(f"\n=== {nodes_path} ===", file=sys.stderr)
    to_delete_node_ids = apply(nodes, rels, resolutions)["deleted"]

    if write:
        snapshot_course(nodes_path, "fix_multi_parent_nodes")
        with open(nodes_path, "w") as f:
            for n in nodes:
                f.write(json.dumps(n) + "\n")
        with open(rels_path, "w") as f:
            for r in rels:
                f.write(json.dumps(r) + "\n")
        print(f"  wrote {nodes_path} / {rels_path}", file=sys.stderr)

    return to_delete_node_ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                         help="Overwrite data/*_nodes.jsonl and *_relationships.jsonl in place "
                              "(default: dry run)")
    args = parser.parse_args()

    all_deleted = {}
    for nodes_path, resolutions in RESOLUTIONS.items():
        deleted = process_course(nodes_path, resolutions, args.write)
        all_deleted[nodes_path] = deleted

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl / "
              "*_relationships.jsonl in place.", file=sys.stderr)
    else:
        print(f"\nDeleted node identifiers (for live Neo4j/Qdrant cleanup): "
              f"{[i for ids in all_deleted.values() for i in ids]}", file=sys.stderr)


if __name__ == "__main__":
    main()
