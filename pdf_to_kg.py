#!/usr/bin/env python3
"""
pdf_to_kg.py — LLM-based curriculum knowledge-graph extractor.

Reads a curriculum PDF (any publisher/layout), uses Claude to recover its
structure into normalized JSON, then builds a Learning-Commons-style graph
(nodes.new.jsonl + relationships.new.jsonl) with the same schema as the
existing graph.

Why an LLM reader: a PDF is just ink on a page — there is no reliable rule that
locates "the lessons" or "the standards" across publishers. Claude reads each
page (natively, no OCR/text-scraping) and returns a fixed JSON shape, so the
same script handles TEKS, CCSS, IM, etc. without per-publisher regex.

Pipeline:  PDF ─(split into page chunks)→ Claude structured extraction
           → merge chunks → deterministic UUID5 graph → JSONL

Auth: needs Claude API access. Either export ANTHROPIC_API_KEY, or run
`ant auth login` once (the SDK picks up the profile automatically).

Usage:
    python pdf_to_kg.py "Seventh Grade Math Teacher Edition, Volume 1 Module 2 (1).pdf"
    python pdf_to_kg.py mybook.pdf --pages-per-chunk 15 --jurisdiction Texas
"""
import argparse, base64, io, json, sys, uuid
import anthropic
import pypdf

MODEL = "claude-opus-4-8"          # most capable; do not downgrade silently
NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://your-org.example/curriculum")
PROVIDER, AUTHOR = "Your Org", "Your Curriculum Team"
LICENSE = "https://creativecommons.org/licenses/by/4.0/"
ATTRIB = "Knowledge Graph generated from PDF by Your Org."

# ── The fixed shape Claude fills in for each page-chunk ──────────────────────
# json_schema structured output: every object needs additionalProperties:false
# and lists all keys in `required`; no min/maxLength, no recursion.
def _obj(props):
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}
_str = {"type": "string"}
_strs = {"type": "array", "items": {"type": "string"}}

EXTRACTION_SCHEMA = _obj({
    "module_number": _str,           # "" if this chunk has no module header
    "module_title": _str,
    "module_teks": _strs,            # standard codes the module addresses
    "topics": {"type": "array", "items": _obj({
        "number": _str,
        "title": _str,
        "teks": _strs,               # all standard codes this topic teaches
        "readiness_teks": _strs,     # subset marked "readiness"/bold
        "sessions": _str,
        "learning_outcomes": _strs,  # "Students solve..." bullet lines
    })},
    "standards": {"type": "array", "items": _obj({
        "code": _str,                # e.g. "7.4D"
        "description": _str,         # text of the standard, "" if not printed
    })},
    "assessments": {"type": "array", "items": _obj({
        "name": _str,
        "topic_number": _str,
        "teks": _strs,
    })},
})

SYSTEM = (
    "You extract the structure of a K-12 curriculum document into JSON. "
    "Read the given PDF pages and pull out: the module (if a module title page "
    "or overview is present), its topics, the standard codes each topic and "
    "assessment aligns to (e.g. TEKS like 7.4D, or CCSS like 7.SP.A.1), which "
    "codes are marked as 'readiness' or bold, the learning-outcome bullets "
    "('Students ...'), and any standard descriptions printed. Only report what "
    "actually appears on these pages. Use exact standard codes. If a field is "
    "absent, return an empty string or empty list — never invent content."
)


def split_pdf(path, pages_per_chunk):
    """Yield (first_page, last_page, base64_pdf) for each page-range chunk."""
    reader = pypdf.PdfReader(path)
    n = len(reader.pages)
    for start in range(0, n, pages_per_chunk):
        end = min(start + pages_per_chunk, n)
        writer = pypdf.PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        yield start + 1, end, base64.standard_b64encode(buf.getvalue()).decode()


def chunk_message_params(b64, page_label):
    """The Messages-API params for one extraction chunk — shared by streaming and Batch."""
    return dict(
        model=MODEL, max_tokens=16000, thinking={"type": "adaptive"}, system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf", "data": b64}},
            {"type": "text", "text":
                f"Extract the curriculum structure from these pages ({page_label})."},
        ]}])


def parse_extraction(msg):
    """Pull the structured JSON out of a completed message (streaming or batch result)."""
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


def extract_chunk(client, b64, page_label):
    """One structured-extraction call over a page-range chunk (interactive/streaming)."""
    with client.messages.stream(**chunk_message_params(b64, page_label)) as stream:
        return parse_extraction(stream.get_final_message())


def combine_modules(chunk_lists, jurisdiction, framework, case, course_name=None):
    """Merge several modules' chunk-lists into ONE deduped graph. Each list is the chunks for
    one PDF/module. Shared standards collapse by ID; optionally add a Course that hasPart-links
    every module."""
    nodes, rels, seen_n, seen_r = [], [], set(), set()
    module_ids = []
    for chunks in chunk_lists:
        if not chunks:
            continue
        module, topics, standards, assessments = merge(chunks)
        if case:                                   # enrich descriptions from the framework
            for t in topics.values():
                for c in t["teks"]:
                    if not standards.get(c):
                        stt = case["code_to_stmt"].get(_norm_code(c))
                        if stt:
                            standards[c] = stt
        n, r = build_graph(module, topics, standards, assessments, jurisdiction, framework, case,
                           scope=course_name or "")
        for x in n:
            if x["identifier"] not in seen_n:
                seen_n.add(x["identifier"]); nodes.append(x)
        for x in r:
            if x["identifier"] not in seen_r:
                seen_r.add(x["identifier"]); rels.append(x)
        mid = next((x["identifier"] for x in n if x["labels"][0] == "LessonGrouping"
                    and x["properties"].get("groupName") == "Module"), None)
        if mid:
            module_ids.append(mid)

    if course_name and module_ids:
        cid = "yo:" + str(uuid.uuid5(NS, f"Course|{course_name}"))
        nodes.append({"type": "node", "identifier": cid, "labels": ["Course"],
                      "properties": {"identifier": cid, "name": course_name, "provider": PROVIDER,
                                     "author": AUTHOR, "license": LICENSE,
                                     "attributionStatement": ATTRIB, "inLanguage": "en-US",
                                     "academicSubject": "Mathematics", "curriculumLabel": "Course"}})
        for mid in module_ids:
            rid = str(uuid.uuid5(NS, f"hasPart|{cid}|{mid}"))
            if rid not in seen_r:
                seen_r.add(rid)
                rels.append({"type": "relationship", "identifier": rid, "label": "hasPart",
                             "properties": {"identifier": rid, "relationshipType": "hasPart",
                                            "provider": PROVIDER, "sourceEntity": "Course",
                                            "targetEntity": "LessonGrouping",
                                            "sourceEntityKey": "identifier",
                                            "targetEntityKey": "identifier"},
                             "source_identifier": cid, "source_labels": ["Course"],
                             "target_identifier": mid, "target_labels": ["LessonGrouping"]})
    return nodes, rels


def merge(chunks):
    """Fold per-chunk extractions into one curriculum, deduping by key."""
    module = {"number": "", "title": "", "teks": set()}
    topics, standards, assessments = {}, {}, {}
    for c in chunks:
        if c.get("module_number"):
            module["number"] = c["module_number"]
            module["title"] = module["title"] or c.get("module_title", "")
        module["teks"].update(c.get("module_teks", []))
        for t in c.get("topics", []):
            key = t["number"] or t["title"]
            cur = topics.setdefault(key, {"number": t["number"], "title": t["title"],
                                          "teks": set(), "readiness": set(),
                                          "sessions": "", "outcomes": []})
            cur["title"] = cur["title"] or t["title"]
            cur["sessions"] = cur["sessions"] or t.get("sessions", "")
            cur["teks"].update(t.get("teks", []))
            cur["readiness"].update(t.get("readiness_teks", []))
            cur["outcomes"].extend(t.get("learning_outcomes", []))
        for s in c.get("standards", []):
            if s["code"]:
                standards.setdefault(s["code"], s.get("description", ""))
        for a in c.get("assessments", []):
            key = (a["name"], a.get("topic_number", ""))
            assessments.setdefault(key, {"name": a["name"],
                                         "topic_number": a.get("topic_number", ""),
                                         "teks": set()}).update()
            assessments[key]["teks"].update(a.get("teks", []))
    return module, topics, standards, assessments


# ── CASE framework loader (the real standards backbone) ──────────────────────
def _norm_code(c):
    """Normalize a standard code so '7.4(D)', '7.4 D', '7.4D' all compare equal."""
    return (c or "").upper().replace(" ", "").replace("(", "").replace(")", "")


def load_case(data):
    """Parse a 1EdTech CASE (CFPackage) JSON into the bits build_graph needs.

    Standard shape: {CFDocument:{}, CFItems:[...], CFAssociations:[...]}.
    CFItem   → identifier (UUID), humanCodingScheme ('7.4D'), fullStatement.
    CFAssociation → associationType, originNodeURI.identifier, destinationNodeURI.identifier.
    """
    doc = data.get("CFDocument") or {}
    items = data.get("CFItems") or data.get("CFItem") or []
    assocs = data.get("CFAssociations") or data.get("CFAssociation") or []
    by_uuid, code_to_uuid, code_to_stmt = {}, {}, {}
    for it in items:
        uid = it.get("identifier")
        if not uid:
            continue
        code, stmt = it.get("humanCodingScheme") or "", it.get("fullStatement") or ""
        by_uuid[uid] = {"code": code, "statement": stmt,
                        "jurisdiction": it.get("jurisdiction") or ""}
        if code:
            code_to_uuid[_norm_code(code)] = uid
            code_to_stmt[_norm_code(code)] = stmt
    parsed = []
    for a in assocs:
        o = (a.get("originNodeURI") or {}).get("identifier")
        d = (a.get("destinationNodeURI") or {}).get("identifier")
        if o and d:
            parsed.append((a.get("associationType"), o, d))
    return {"framework": {"uuid": doc.get("identifier"),
                          "title": doc.get("title") or "Standards Framework"},
            "by_uuid": by_uuid, "assocs": parsed,
            "code_to_uuid": code_to_uuid, "code_to_stmt": code_to_stmt}


# CASE association types -> our edge labels (isChildOf handled specially as hasChild)
_ASSOC_EDGE = {"isRelatedTo": "relatesTo", "isPeerOf": "relatesTo",
               "precedes": "buildsTowards", "isPrerequisiteFor": "buildsTowards",
               "isStandardAlignedTo": "hasStandardAlignment",   # cross-framework crosswalk
               "exemplar": "hasReference"}


# ── Graph builder (deterministic UUID5, matches the existing graph schema) ────
def build_graph(module, topics, standards, assessments, jurisdiction, framework, case=None,
                scope=""):
    """Build nodes/edges. If `case` (a loaded CASE file) is given, the standards side is the
    real framework — full descriptions, hierarchy (hasChild), and cross-grade links — and
    lessons attach to those real standard nodes by matching codes. Without it, standards are
    synthesized from the bare codes seen in the PDF.

    `scope` namespaces the CONTENT ids (Module/Topic/Assessment/LearningComponent) so the same
    module number in different grades/courses doesn't collide when graphs are merged. Standards
    ids are never scoped — they stay shared across grades."""
    nodes, rels = [], []

    def nid(kind, key):
        base = f"{scope}|{kind}|{key}" if scope else f"{kind}|{key}"
        return "yo:" + str(uuid.uuid5(NS, base))

    def case_uuid(code):
        return str(uuid.uuid5(NS, f"CASE|{framework}|{code}"))

    def node(identifier, label, **props):
        props.update(identifier=identifier, provider=PROVIDER, author=AUTHOR,
                     license=LICENSE, attributionStatement=ATTRIB, inLanguage="en-US")
        nodes.append({"type": "node", "identifier": identifier,
                      "labels": [label], "properties": props})

    def rel(label, s_id, s_label, t_id, t_label, s_key="identifier",
            t_key="identifier", **props):
        ident = str(uuid.uuid5(NS, f"{label}|{s_id}|{t_id}"))
        props.update(identifier=ident, relationshipType=label, provider=PROVIDER,
                     sourceEntity=s_label, targetEntity=t_label,
                     sourceEntityKey=s_key, targetEntityKey=t_key)
        rels.append({"type": "relationship", "identifier": ident, "label": label,
                     "properties": props,
                     "source_identifier": s_id, "source_labels": [s_label],
                     "target_identifier": t_id, "target_labels": [t_label]})

    readiness = set().union(*(t["readiness"] for t in topics.values())) if topics else set()
    emitted_std = set()

    def ensure_std(uid, code="", stmt="", juris=None):
        if uid in emitted_std:
            return
        emitted_std.add(uid)
        node(uid, "StandardsFrameworkItem", description=stmt or code or "", code=code,
             jurisdiction=juris or jurisdiction, academicSubject="Mathematics",
             readinessStandard=str(code in readiness).lower(), caseIdentifierUUID=uid)

    # ── standards backbone ───────────────────────────────────────────────────
    if case:
        fw_id = case["framework"]["uuid"] or case_uuid("__root__")
        node(fw_id, "StandardsFramework", name=case["framework"]["title"],
             academicSubject="Mathematics", jurisdiction=jurisdiction, caseIdentifierUUID=fw_id)
        for uid, it in case["by_uuid"].items():          # full framework from the CASE file
            ensure_std(uid, it["code"], it["statement"], it.get("jurisdiction"))
        for atype, o, d in case["assocs"]:               # hierarchy + cross-grade links
            if o not in emitted_std or d not in emitted_std:
                continue
            if atype == "isChildOf":
                rel("hasChild", d, "StandardsFrameworkItem", o, "StandardsFrameworkItem",
                    s_key="caseIdentifierUUID", t_key="caseIdentifierUUID")
            elif atype in _ASSOC_EDGE:
                rel(_ASSOC_EDGE[atype], o, "StandardsFrameworkItem", d, "StandardsFrameworkItem",
                    s_key="caseIdentifierUUID", t_key="caseIdentifierUUID")
    else:
        fw_id = case_uuid("__root__")
        node(fw_id, "StandardsFramework", name=f"{framework} Standards",
             academicSubject="Mathematics", jurisdiction=jurisdiction, caseIdentifierUUID=fw_id)

    def resolve(code):
        """PDF code -> standard node's caseIdentifierUUID (real if in CASE, else synthesized)."""
        uid = case["code_to_uuid"].get(_norm_code(code)) if case else None
        if uid:
            return uid
        uid = case_uuid(code)                            # code not in framework: synthesize + attach
        if uid not in emitted_std:
            ensure_std(uid, code, standards.get(code, ""))
            rel("hasChild", fw_id, "StandardsFramework", uid, "StandardsFrameworkItem",
                s_key="caseIdentifierUUID", t_key="caseIdentifierUUID")
        return uid

    # ── content side + the bridge (alignments now resolve to real standards) ──
    mod_id = nid("Module", module["number"] or module["title"] or "module")
    node(mod_id, "LessonGrouping", name=module["title"], groupName="Module",
         groupLevel="0", ordinalName=f"Module {module['number']}",
         academicSubject="Mathematics", curriculumLabel="Module")
    for code in sorted(module["teks"]):
        rel("hasEducationalAlignment", mod_id, "LessonGrouping",
            resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID",
            alignmentType="teaches", curriculumAlignmentType="addressing")

    for key, t in topics.items():
        top_id = nid("Topic", f"{module['number']}:{key}")
        node(top_id, "LessonGrouping", name=t["title"], groupName="Topic",
             groupLevel="1", ordinalName=f"Topic {t['number']}", position=t["number"],
             academicSubject="Mathematics", curriculumLabel="Topic")
        rel("hasPart", mod_id, "LessonGrouping", top_id, "LessonGrouping")
        for code in sorted(t["teks"]):
            rel("hasEducationalAlignment", top_id, "LessonGrouping",
                resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID",
                alignmentType="teaches", curriculumAlignmentType="addressing")
        for i, outcome in enumerate(t["outcomes"]):
            lc_id = nid("LearningComponent", f"{module['number']}:{key}:{i}")
            node(lc_id, "LearningComponent", description=outcome, academicSubject="Mathematics")
            rel("hasPart", top_id, "LessonGrouping", lc_id, "LearningComponent")
            for code in sorted(t["teks"]):
                rel("supports", lc_id, "LearningComponent",
                    resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID")

    for (name, topic_no), a in assessments.items():
        asm_id = nid("Assessment", f"{module['number']}:{topic_no}:{name}")
        node(asm_id, "Assessment", name=name, academicSubject="Mathematics",
             curriculumLabel="Assessment")
        if topic_no in topics:
            rel("hasPart", nid("Topic", f"{module['number']}:{topic_no}"),
                "LessonGrouping", asm_id, "Assessment")
        for code in sorted(a["teks"]):
            rel("hasEducationalAlignment", asm_id, "Assessment",
                resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID",
                alignmentType="assesses", curriculumAlignmentType="addressing")
    return nodes, rels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages-per-chunk", type=int, default=15)
    ap.add_argument("--jurisdiction", default="Texas")
    ap.add_argument("--framework", default="TEKS")
    ap.add_argument("--case", help="path to a 1EdTech CASE framework JSON (optional)")
    ap.add_argument("--nodes-out", default="nodes.new.jsonl")
    ap.add_argument("--rels-out", default="relationships.new.jsonl")
    ap.add_argument("--extracted-out", default="curriculum_extracted.json")
    args = ap.parse_args()

    client = anthropic.Anthropic()
    chunks = []
    for first, last, b64 in split_pdf(args.pdf, args.pages_per_chunk):
        label = f"pages {first}-{last}"
        print(f"  extracting {label} ...", flush=True)
        try:
            chunks.append(extract_chunk(client, b64, label))
        except Exception as e:
            print(f"    ! chunk {label} failed: {e}", file=sys.stderr)
    module, topics, standards, assessments = merge(chunks)
    print(f"merged: 1 module, {len(topics)} topics, {len(standards)} standards, "
          f"{len(assessments)} assessments")

    case = None
    if args.case:
        case = load_case(json.load(open(args.case)))
        print(f"loaded CASE framework '{case['framework']['title']}': "
              f"{len(case['by_uuid'])} standards, {len(case['assocs'])} links")
        # enrich extracted standards with official statements + report code coverage
        used = {c for t in topics.values() for c in t["teks"]} | set(module["teks"])
        matched = 0
        for code in used:
            uid = case["code_to_uuid"].get(_norm_code(code))
            if uid:
                matched += 1
                standards.setdefault(code, case["code_to_stmt"].get(_norm_code(code), ""))
        print(f"  matched {matched}/{len(used)} curriculum codes to the framework")

    # persist the intermediate extraction (jsonify sets)
    json.dump({"module": {**module, "teks": sorted(module["teks"])},
               "topics": {k: {**v, "teks": sorted(v["teks"]),
                              "readiness": sorted(v["readiness"])}
                          for k, v in topics.items()},
               "standards": standards,
               "assessments": {f"{k[0]}|{k[1]}": {**v, "teks": sorted(v["teks"])}
                               for k, v in assessments.items()}},
              open(args.extracted_out, "w"), indent=1)

    nodes, rels = build_graph(module, topics, standards, assessments,
                              args.jurisdiction, args.framework, case)
    with open(args.nodes_out, "w") as f:
        for n in nodes:
            f.write(json.dumps(n) + "\n")
    with open(args.rels_out, "w") as f:
        for r in rels:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(nodes)} nodes -> {args.nodes_out}")
    print(f"wrote {len(rels)} edges -> {args.rels_out}")


if __name__ == "__main__":
    main()
