#!/usr/bin/env python3
"""
case_from_graph.py — Extract the Texas Mathematics TEKS from the existing
Learning-Commons graph (nodes.jsonl + relationships.jsonl) and write it as a
1EdTech CASE JSON that app.py / pdf_to_kg.py can load as the standards framework.

Why: the big graph already contains the full TEKS with real caseIdentifierUUIDs,
descriptions, hierarchy, and cross-grade links. Reusing those exact UUIDs means a
new Bluebonnet graph aligns to the SAME standard nodes as the Learning Commons
graph — so the two connect instead of duplicating standards.

The one conversion: the graph stores the official chapter code (111.27.b.4.D);
the textbook uses 7.4D. §111.<sec> is a grade, and the tail after '.b.' is the
knowledge-skill + expectation, so 111.27.b.4.D → grade 7 → "7.4D".

Usage:  python case_from_graph.py            # -> teks_math_case.json
"""
import json

CHAPTER = "111."          # Chapter 111 = Mathematics (110=ELA, 112=Sci, 113=SocSt)
OUT = "teks_math_case.json"


def human_code(statement_code, grade_level):
    """111.27.b.4.D + gradeLevel ["7"]  ->  "7.4D"  (blank if not a codeable node)."""
    try:
        grades = json.loads(grade_level) if grade_level else []
    except Exception:
        grades = []
    if len(grades) != 1:
        return ""                      # groupings / intros / multi-grade -> no simple code
    parts = statement_code.split(".")
    if "b" not in parts:
        return ""
    tail = parts[parts.index("b") + 1:]   # e.g. ["4","D"] or ["4"]
    if not tail:
        return ""
    return f"{grades[0]}.{''.join(tail)}"  # 7 + 4 + D -> "7.4D"


def main():
    # 1) Pass over nodes:
    #    - Texas math items (statementCode 111.*) -> textbook codes, the content side's targets
    #    - Common Core (Multi-State) items -> kept so the vertical "buildsTowards" chains and
    #      the Texas<->CCSS crosswalk have real nodes to reference.
    #    NOTE: node.identifier != caseIdentifierUUID for standards, and edges reference
    #    node.identifier -> keep an id2case map to translate edges.
    doc = None
    items = {}          # caseIdentifierUUID -> {code, statement, jurisdiction}
    id2case = {}        # node.identifier   -> caseIdentifierUUID
    tx_ids, ms_ids = set(), set()      # caseIdentifierUUIDs, by side
    with open("nodes.jsonl") as f:
        for line in f:
            o = json.loads(line); lab = o["labels"][0]; p = o["properties"]
            juris = p.get("jurisdiction")
            if lab == "StandardsFramework" and juris == "Texas" and "Mathematics" in p.get("name", ""):
                doc = {"identifier": p["caseIdentifierUUID"], "title": p.get("name", "TEKS Mathematics")}
                id2case[o["identifier"]] = p["caseIdentifierUUID"]
            elif lab == "StandardsFrameworkItem":
                uid = p["caseIdentifierUUID"]
                sc = p.get("statementCode", "")
                if juris == "Texas" and sc.startswith(CHAPTER):        # Texas math
                    items[uid] = {"code": human_code(sc, p.get("gradeLevel", "")),
                                  "statement": p.get("description", ""), "jurisdiction": "Texas"}
                    id2case[o["identifier"]] = uid; tx_ids.add(uid)
                elif juris == "Multi-State":                            # Common Core etc.
                    items[uid] = {"code": sc or p.get("humanCodingScheme", ""),
                                  "statement": p.get("description", ""), "jurisdiction": "Multi-State"}
                    id2case[o["identifier"]] = uid; ms_ids.add(uid)
    assert doc, "Texas Mathematics framework not found"

    # 2) Pass over edges. Collect the three kinds we need, keyed by caseIdentifierUUID.
    tx_hierarchy = []          # (child, parent)  within Texas math
    crosswalk = []             # (tx, ms)         Texas <-> Common Core (hasStandardAlignment)
    ms_builds = []             # (from, to)       Common Core buildsTowards (both Multi-State)
    cc_needed = set()          # Multi-State ids actually referenced (keep the graph small)
    with open("relationships.jsonl") as f:
        for line in f:
            o = json.loads(line); lab = o["label"]
            s = id2case.get(o["source_identifier"]); t = id2case.get(o["target_identifier"])
            if s is None or t is None:
                continue
            if lab == "hasChild" and s in tx_ids and t in tx_ids:
                tx_hierarchy.append((t, s))                            # child isChildOf parent
            elif lab == "hasStandardAlignment" and (
                    (s in tx_ids and t in ms_ids) or (s in ms_ids and t in tx_ids)):
                tx, ms = (s, t) if s in tx_ids else (t, s)
                crosswalk.append((tx, ms)); cc_needed.add(ms)
            elif lab == "buildsTowards" and s in ms_ids and t in ms_ids:
                ms_builds.append((s, t))

    # keep buildsTowards chains connected to the crosswalked CCSS nodes (grow to a fixpoint)
    changed = True
    kept_builds = []
    while changed:
        changed = False
        for e in ms_builds:
            if e in kept_builds:
                continue
            if e[0] in cc_needed or e[1] in cc_needed:
                kept_builds.append(e); cc_needed.update(e); changed = True

    # 3) Emit: Texas math items (always) + the CCSS items we actually reference.
    emit_ids = tx_ids | cc_needed
    assocs = ([{"associationType": "isChildOf",
                "originNodeURI": {"identifier": c}, "destinationNodeURI": {"identifier": p}}
               for c, p in tx_hierarchy]
              + [{"associationType": "isStandardAlignedTo",
                  "originNodeURI": {"identifier": tx}, "destinationNodeURI": {"identifier": ms}}
                 for tx, ms in crosswalk]
              + [{"associationType": "precedes",
                  "originNodeURI": {"identifier": a}, "destinationNodeURI": {"identifier": b}}
                 for a, b in kept_builds])

    case = {
        "CFDocument": doc,
        "CFItems": [{"identifier": uid, "humanCodingScheme": items[uid]["code"],
                     "fullStatement": items[uid]["statement"],
                     "jurisdiction": items[uid]["jurisdiction"]}
                    for uid in emit_ids],
        "CFAssociations": assocs,
    }
    json.dump(case, open(OUT, "w"))
    coded = sum(1 for uid in tx_ids if items[uid]["code"])
    print(f"wrote {OUT}")
    print(f"  {len(tx_ids)} Texas math standards ({coded} coded) + "
          f"{len(cc_needed)} Common Core standards referenced")
    print(f"  associations: {len(tx_hierarchy)} hasChild, {len(crosswalk)} crosswalk "
          f"(TX<->CCSS), {len(kept_builds)} buildsTowards (CCSS vertical)")


if __name__ == "__main__":
    main()
