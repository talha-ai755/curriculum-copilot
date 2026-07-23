#!/usr/bin/env python3
"""
merge_official_progression.py — Build the app's framework file from the OFFICIAL Texas
Gateway TEKS CASE package as the canonical base, with the Learning Commons progression
layer (Common Core crosswalk + vertical buildsTowards + non-directed relatesTo) merged
in by UUID.

Why: the official file is complete + canonical (1,515 math standards, incl. HS courses) but
has only hierarchy — no cross-grade progression. Learning Commons uses the SAME UUIDs and
adds the Common Core bridge. Merging by UUID gives both: canonical standards + vertical nav.

Inputs (already downloaded):
  teks_math_OFFICIAL.json           official CASE package (base)
  nodes.jsonl / relationships.jsonl Learning Commons graph (progression source)
Output:
  teks_math_case.json               official base + progression (what the app loads)
"""
import argparse
import json


# Texas HS course TEKS live under "c" (Knowledge and skills), not "b" (Introduction) like
# grade-level TEKS, and use a fixed letter prefix instead of a numeric grade. Textbooks cite
# these as e.g. "A.6A" for Algebra I -- confirmed against real Algebra I Teacher Edition PDFs.
COURSE_PREFIX = {
    "111.39": "A",      # Algebra I
    "111.40": "2A",     # Algebra II
    "111.41": "G",      # Geometry
    "111.42": "P",      # Precalculus
    "111.43": "MMA",    # Mathematical Models with Applications
    "111.44": "AQR",    # Advanced Quantitative Reasoning
    "111.46": "DM",     # Discrete Mathematics for Problem Solving
    "111.47": "S",      # Statistics
    "111.48": "AR",     # Algebraic Reasoning
    # 111.45 Independent Study: no dedicated per-item TEKS, skipped.
    # 111.29-111.31 (2025 Advanced Math pathway): no confirmed textbook-code
    # convention found yet, skipped rather than guessed.
}


def textbook_code(official_hcs, grade):
    """111.27.b.4.D + grade '7' -> '7.4D'.  111.39.c.6.A -> 'A.6A' (course, via COURSE_PREFIX).
    '' if not convertible."""
    parts = official_hcs.split(".")
    if "b" in parts and grade:
        tail = parts[parts.index("b") + 1:]      # ['4','D'] or ['4']
        return f"{grade}.{''.join(tail)}" if tail else ""
    if "c" in parts:
        prefix = COURSE_PREFIX.get(".".join(parts[:2]))
        if prefix:
            tail = parts[parts.index("c") + 1:]
            return f"{prefix}.{''.join(tail)}" if tail else ""
    return ""


def one_grade(gl):
    try:
        g = json.loads(gl)
        return g[0] if len(g) == 1 else ""
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--official", default="teks_math_OFFICIAL.json",
                    help="official Texas Gateway CASE package for the subject")
    ap.add_argument("--out", default="teks_math_case.json", help="merged framework output")
    args = ap.parse_args()
    OFFICIAL, OUT = args.official, args.out

    # ── 1. OFFICIAL base: TEKS items + hierarchy (canonical, complete) ──────────
    off = json.load(open(OFFICIAL))
    doc = {"identifier": off["CFDocument"]["identifier"],
           "title": off["CFDocument"].get("title", "TEKS Mathematics")}
    tx_items = {it["identifier"]: {"official_code": it.get("humanCodingScheme", ""),
                                   "statement": it.get("fullStatement", "")}
                for it in off["CFItems"] if it.get("identifier")}
    tx_hierarchy = [(a["originNodeURI"]["identifier"], a["destinationNodeURI"]["identifier"])
                    for a in off["CFAssociations"] if a.get("associationType") == "isChildOf"
                    and a.get("originNodeURI") and a.get("destinationNodeURI")]
    tx_uuids = set(tx_items)

    # ── 2. Learning Commons: grade tags (for code conversion), CCSS items, and the
    #        node.identifier -> caseIdentifierUUID map to translate LC edges ──────
    grade_by_uuid = {}          # official TEKS uuid -> grade ('7')
    ms_items = {}               # CCSS caseIdentifierUUID -> {code, statement}
    id2case = {}                # LC node.identifier -> caseIdentifierUUID
    with open("nodes.jsonl") as f:
        for line in f:
            o = json.loads(line)
            if o["labels"][0] != "StandardsFrameworkItem":
                continue
            p = o["properties"]; cu = p.get("caseIdentifierUUID")
            if not cu:
                continue
            id2case[o["identifier"]] = cu
            if p.get("jurisdiction") == "Texas" and cu in tx_uuids:
                grade_by_uuid[cu] = one_grade(p.get("gradeLevel", ""))
            elif p.get("jurisdiction") == "Multi-State":
                ms_items[cu] = {"code": p.get("statementCode") or p.get("humanCodingScheme", ""),
                                "statement": p.get("description", "")}

    # ── 3. Learning Commons edges: crosswalk (TX<->CCSS) + CCSS vertical/peer links ──
    # buildsTowards = directional progression; relatesTo = non-directed conceptual link
    # (https://docs.learningcommons.org/knowledge-graph/graph-reference/learning-progressions).
    crosswalk, ms_edges, cc_needed = [], [], set()
    ms_uuids = set(ms_items)
    with open("relationships.jsonl") as f:
        for line in f:
            o = json.loads(line); lab = o["label"]
            s = id2case.get(o["source_identifier"]); t = id2case.get(o["target_identifier"])
            if s is None or t is None:
                continue
            if lab == "hasStandardAlignment" and (
                    (s in tx_uuids and t in ms_uuids) or (s in ms_uuids and t in tx_uuids)):
                tx, ms = (s, t) if s in tx_uuids else (t, s)
                crosswalk.append((tx, ms)); cc_needed.add(ms)
            elif lab in ("buildsTowards", "relatesTo") and s in ms_uuids and t in ms_uuids:
                ms_edges.append((lab, s, t))
    # keep only CCSS vertical/peer links connected to the crosswalked nodes (grow to fixpoint)
    kept_edges, changed = [], True
    while changed:
        changed = False
        for e in ms_edges:
            if e not in kept_edges and (e[1] in cc_needed or e[2] in cc_needed):
                kept_edges.append(e); cc_needed.update((e[1], e[2])); changed = True
    kept_builds = [(s, t) for lab, s, t in kept_edges if lab == "buildsTowards"]
    kept_relates = [(s, t) for lab, s, t in kept_edges if lab == "relatesTo"]

    # ── 4. Emit merged CASE: official TEKS (codes converted) + referenced CCSS ───
    cfitems = []
    for uid, it in tx_items.items():
        cfitems.append({"identifier": uid,
                        "humanCodingScheme": textbook_code(it["official_code"],
                                                           grade_by_uuid.get(uid, "")),
                        "fullStatement": it["statement"], "jurisdiction": "Texas"})
    for uid in cc_needed:
        it = ms_items[uid]
        cfitems.append({"identifier": uid, "humanCodingScheme": it["code"],
                        "fullStatement": it["statement"], "jurisdiction": "Multi-State"})

    assocs = ([{"associationType": "isChildOf",
                "originNodeURI": {"identifier": c}, "destinationNodeURI": {"identifier": p}}
               for c, p in tx_hierarchy]
              + [{"associationType": "isStandardAlignedTo",
                  "originNodeURI": {"identifier": tx}, "destinationNodeURI": {"identifier": ms}}
                 for tx, ms in crosswalk]
              + [{"associationType": "precedes",
                  "originNodeURI": {"identifier": a}, "destinationNodeURI": {"identifier": b}}
                 for a, b in kept_builds]
              + [{"associationType": "isRelatedTo",
                  "originNodeURI": {"identifier": a}, "destinationNodeURI": {"identifier": b}}
                 for a, b in kept_relates])

    json.dump({"CFDocument": doc, "CFItems": cfitems, "CFAssociations": assocs}, open(OUT, "w"))
    coded = sum(1 for it in cfitems if it["jurisdiction"] == "Texas" and it["humanCodingScheme"])
    print(f"wrote {OUT}")
    print(f"  base (OFFICIAL): {len(tx_items)} TEKS standards ({coded} with textbook codes)")
    print(f"  merged progression: {len(cc_needed)} Common Core standards, "
          f"{len(crosswalk)} crosswalk + {len(kept_builds)} buildsTowards + "
          f"{len(kept_relates)} relatesTo")
    print(f"  hierarchy from official: {len(tx_hierarchy)} isChildOf")


if __name__ == "__main__":
    main()
