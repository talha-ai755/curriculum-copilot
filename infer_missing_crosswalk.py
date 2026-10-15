#!/usr/bin/env python3
"""
DEPRECATED -- DO NOT RERUN WITHOUT EXPLICIT USER REQUEST. The 151 candidate associations this
script previously generated were loaded as possibleStandardAlignment edges, then explicitly
rejected and removed by product decision (the user doesn't trust AI-inferred crosswalks) --
deleted from Neo4j, from every course's *_relationships.jsonl, and from teks_math_case.json's
CFAssociations directly. pdf_to_kg.py's _ASSOC_EDGE mapping no longer converts
"isStandardAlignedTo_inferred" into a graph edge at all, so even if this script were rerun, its
output would be inert on the next graph build -- but rerunning it would still re-pollute
teks_math_case.json with rejected data, so don't. Left in place for its documented method
(grade-band-constrained candidate pool), not as a script to actually execute.

infer_missing_crosswalk.py — propose candidate TEKS<->Common Core crosswalk edges for TEKS
standards the real Learning Commons crosswalk never covered (confirmed: only 258 of 1,515 TEKS
standards have a real hasStandardAlignment edge; web search confirmed no better authoritative
TEKS<->CCSS source exists to substitute in).

Scope: only TEKS standards relevant to this app's courses -- grades 6-8 and Algebra I -- that
currently have zero hasStandardAlignment edge, excluding ones that can't plausibly be matched:
personal financial literacy (Texas-only content, no CCSS equivalent), bare "process standards"
umbrella items, and parent/strand-header codes with no letter suffix (redundant with their own
lettered children, which are matched individually instead).

For each target, Claude picks the best match from a GRADE-BAND-CONSTRAINED candidate pool (not
all 429 CC codes) -- narrowing the pool is what keeps this from being a vague semantic-similarity
guess; forcing a choice from a small, plausible set makes a wrong pick much less likely, and the
model can still say "no real match" when nothing fits.

Output: written as NEW CFAssociations records, associationType "isStandardAlignedTo_inferred"
(pdf_to_kg.py maps this to a distinct possibleStandardAlignment edge -- never merged with the
real hasStandardAlignment edges), each carrying confidence + rationale. Appended into
teks_math_case.json directly (all endpoints already exist as CFItems; only new associations are
added) so the next graph reload picks them up automatically for both courses.

Usage: python3 infer_missing_crosswalk.py
"""
import json
import re
import sys

import anthropic

import pdf_to_kg as p2k

CASE_PATH = "teks_math_case.json"
RELEVANT_GRADES = {"6", "7", "8", "A"}
# TEKS grade -> plausible CC grade-number pool (adjacent bands, since a TEKS standard's nearest
# CCSS equivalent isn't always the exact same grade number). Algebra I has no CC grade number at
# all -- CCSS organizes HS content by conceptual category (HSA-*, HSF-*, HSN-*, HSG-*, HSS-*)
# instead, confirmed against the real data (e.g. HSA-REI.B.3).
CC_POOL_GRADES = {"6": {"5", "6", "7"}, "7": {"6", "7", "8"}, "8": {"7", "8"}, "A": {"HS"}}


def grade_bucket(code):
    m = re.match(r"^([A-Za-z]+|\d+)\.", code or "")
    return m.group(1) if m else code


def cc_grade_bucket(code):
    """CCSS grade-numbered codes start '6.', '7.', etc; HS codes start 'HSA-', 'HSF-', etc."""
    if code.startswith("HS"):
        return "HS"
    m = re.match(r"^(\d+)\.", code or "")
    return m.group(1) if m else None


def pick_targets(items):
    texas = [i for i in items if i.get("jurisdiction") == "Texas" and i.get("humanCodingScheme")]
    aligned = set()
    return [i for i in texas if grade_bucket(i["humanCodingScheme"]) in RELEVANT_GRADES]


def is_matchable(item):
    code, stmt = item["humanCodingScheme"], item["fullStatement"].lower()
    if re.match(r"^[6-8]\.\d+$", code) or re.match(r"^A\.\d+$", code):
        return False   # parent/strand-header, no letter suffix -- its own children are matched
    if re.match(r"^[6-8A]\.1[A-J]?$", code) and "process standards" in stmt:
        return False   # generic mathematical-process umbrella, not a content skill
    if any(w in stmt for w in ("financial literacy", "credit", "checking account", "debit card",
                                "loan", "insurance", "net worth", "payroll")):
        return False   # Texas-only personal financial literacy content, no CCSS equivalent
    return True


SYSTEM = (
    "You are a curriculum standards expert matching a Texas TEKS math standard to its closest "
    "Common Core (CCSS-M) equivalent, from a constrained candidate list only. Judge by the "
    "actual mathematical skill/concept required, not surface wording -- a match must cover the "
    "same specific skill at the same rigor (e.g. 'solve one-variable equations with variables on "
    "both sides using rational coefficients' is NOT the same as a general 'solve equations' "
    "standard if the general one lacks that specific constraint). If more than one candidate is "
    "a genuine partial match, you may return more than one. If NONE of the candidates cover the "
    "same skill, say so -- a wrong match is worse than no match. "
    "Respond with ONLY a JSON object: "
    '{"matches": [{"code": "<candidate code>", "confidence": "high|medium|low", '
    '"rationale": "<one sentence, cite what specific skill overlaps>"}], "no_match_reason": '
    '"<if matches is empty, why>"}'
)


def build_prompt(teks_item, candidates):
    cand_lines = "\n".join(f"- {c['humanCodingScheme']}: {c['fullStatement']}" for c in candidates)
    return (
        f"TEKS standard {teks_item['humanCodingScheme']}: {teks_item['fullStatement']}\n\n"
        f"Candidate Common Core standards:\n{cand_lines}\n\n"
        "Which candidate(s), if any, cover the same skill?"
    )


def main():
    data = json.load(open(CASE_PATH))
    items = data["CFItems"]
    by_uuid = {i["identifier"]: i for i in items}
    cc_items = [i for i in items if i.get("jurisdiction") == "Multi-State"]

    already_aligned = set()
    for a in data["CFAssociations"]:
        if a.get("associationType") == "isStandardAlignedTo":
            src = a["originNodeURI"]["identifier"]
            already_aligned.add(src)

    targets = [i for i in pick_targets(items)
               if i["identifier"] not in already_aligned and is_matchable(i)]
    print(f"{len(targets)} target TEKS standards to match", file=sys.stderr)

    client = anthropic.Anthropic()
    new_assocs = []
    for n, teks in enumerate(targets, 1):
        grade = grade_bucket(teks["humanCodingScheme"])
        pool_grades = CC_POOL_GRADES[grade]
        candidates = [c for c in cc_items if cc_grade_bucket(c["humanCodingScheme"]) in pool_grades]
        if not candidates:
            continue
        try:
            resp = client.messages.create(
                model=p2k.MODEL, max_tokens=1024, system=SYSTEM,
                messages=[{"role": "user", "content": build_prompt(teks, candidates)}])
            text = next((b.text for b in resp.content if b.type == "text"), "{}")
            # the model sometimes prefixes the JSON with a reasoning sentence despite the system
            # prompt saying not to -- pull out the {...} object itself rather than assume it's
            # the whole (possibly fenced) response.
            m = re.search(r"\{.*\}", text, re.DOTALL)
            result = json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"  [{n}/{len(targets)}] {teks['humanCodingScheme']}: ERROR {e}", file=sys.stderr)
            continue

        matches = result.get("matches") or []
        code_to_item = {c["humanCodingScheme"]: c for c in candidates}
        found = []
        for m in matches:
            cc_item = code_to_item.get(m.get("code"))
            if not cc_item:
                continue
            new_assocs.append({
                "associationType": "isStandardAlignedTo_inferred",
                "originNodeURI": {"identifier": teks["identifier"]},
                "destinationNodeURI": {"identifier": cc_item["identifier"]},
                "confidence": m.get("confidence", ""),
                "rationale": m.get("rationale", ""),
            })
            found.append(f"{m.get('code')} ({m.get('confidence')})")
        status = ", ".join(found) if found else f"no match ({result.get('no_match_reason', '')[:60]})"
        print(f"  [{n}/{len(targets)}] {teks['humanCodingScheme']}: {status}", file=sys.stderr)

    data["CFAssociations"].extend(new_assocs)
    json.dump(data, open(CASE_PATH, "w"))
    print(f"\nAdded {len(new_assocs)} inferred crosswalk edges to {CASE_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
