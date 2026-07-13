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
    # 1) framework doc = the Texas Mathematics StandardsFramework.
    #    NOTE: for standards, node.identifier != caseIdentifierUUID, and edges reference the
    #    node.identifier — so keep an identifier -> caseIdentifierUUID map to translate edges.
    doc = None
    items = {}                         # caseIdentifierUUID -> {code, statement}
    id2case = {}                       # node.identifier   -> caseIdentifierUUID
    with open("nodes.jsonl") as f:
        for line in f:
            o = json.loads(line); lab = o["labels"][0]; p = o["properties"]
            if p.get("jurisdiction") != "Texas":
                continue
            if lab == "StandardsFramework" and "Mathematics" in p.get("name", ""):
                doc = {"identifier": p["caseIdentifierUUID"], "title": p.get("name", "TEKS Mathematics")}
                id2case[o["identifier"]] = p["caseIdentifierUUID"]
            elif lab == "StandardsFrameworkItem" and p.get("statementCode", "").startswith(CHAPTER):
                uid = p["caseIdentifierUUID"]
                items[uid] = {"code": human_code(p.get("statementCode", ""), p.get("gradeLevel", "")),
                              "statement": p.get("description", "")}
                id2case[o["identifier"]] = uid
    assert doc, "Texas Mathematics framework not found"
    ids = set(items)

    # 2) edges among those items -> CASE associations (mapped back to CASE vocab)
    EDGE_TO_ASSOC = {"hasStandardAlignment": "isRelatedTo", "relatesTo": "isRelatedTo",
                     "buildsTowards": "precedes"}
    assocs = []
    with open("relationships.jsonl") as f:
        for line in f:
            o = json.loads(line); lab = o["label"]
            s = id2case.get(o["source_identifier"])
            t = id2case.get(o["target_identifier"])
            if s is None or t is None or s not in ids or t not in ids:
                continue
            if lab == "hasChild":
                # graph: parent -hasChild-> child  =>  CASE: child isChildOf parent
                assocs.append({"associationType": "isChildOf",
                               "originNodeURI": {"identifier": t},
                               "destinationNodeURI": {"identifier": s}})
            elif lab in EDGE_TO_ASSOC:
                assocs.append({"associationType": EDGE_TO_ASSOC[lab],
                               "originNodeURI": {"identifier": s},
                               "destinationNodeURI": {"identifier": t}})

    case = {
        "CFDocument": doc,
        "CFItems": [{"identifier": uid, "humanCodingScheme": v["code"],
                     "fullStatement": v["statement"]} for uid, v in items.items()],
        "CFAssociations": assocs,
    }
    json.dump(case, open(OUT, "w"))
    coded = sum(1 for v in items.values() if v["code"])
    print(f"wrote {OUT}")
    print(f"  {len(items)} math standards ({coded} with a textbook-style code), "
          f"{len(assocs)} associations")


if __name__ == "__main__":
    main()
