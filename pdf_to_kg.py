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
import argparse, json, re, sys, uuid
import anthropic

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
_int = {"type": "integer"}
_bool = {"type": "boolean"}
_phase = {"type": "string",
          "enum": ["getting_started", "develop", "demonstrate", "practice", "assessment", ""]}
_supp_type = {"type": "string",
              "enum": ["assessment_summary", "pacing_guide", "self_reflection", "overview", "other"]}

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
        "page_start_label": _str,    # printed footer label of the topic's first page, e.g. "3"
        "page_end_label": _str,      # printed footer label of the topic's last page, e.g. "20"
        "page_start": _int,          # REAL absolute PDF page number of the topic's first page --
                                      # copy this from the "=== PDF PAGE N ===" label above that
                                      # page's text, never compute/guess it
        "page_end": _int,            # same, for the topic's last page
        "lessons": {"type": "array", "items": _obj({
            "number": _str,           # "1", "2", "3"... from a "LESSON N" header WITHIN this
                                       # topic ("INTRODUCTION LESSON" -> number ""). Never a
                                       # new topic -- see SYSTEM for the topic-vs-lesson rule.
            "title": _str,
            "teks": _strs,            # standard codes THIS lesson teaches (e.g. from a Pacing
                                       # Guide row) -- falls back to the topic's teks if unlisted
            "materials_needed": _strs,  # e.g. ["Glue Sticks", "Scissors"] from the Pacing Guide
            "page_start_label": _str,
            "page_end_label": _str,
            "page_start": _int,
            "page_end": _int,
            "learning_outcomes": {"type": "array", "items": _obj({
                "outcome": _str,          # "Students solve..." summary bullet
                "source_text": _str,      # the actual instructional text/example/problem this
                                           # outcome is drawn from, verbatim from the page
                "component_role": _str,   # "bellwork"|"exit_ticket"|"ell_support"|"practice"|
                                           # "instruction"|"" if not a distinct labeled component
                "is_critical": _bool,     # starred/marked must-do in the source
                "lesson_phase": _phase,   # the lesson-phase tag on the page, distinct from
                                           # component_role (e.g. "Talk the Talk" is "demonstrate")
                "page_label": _str,       # printed footer label of the page this came from, e.g.
                                           # "21", "4B", "IL-1E" -- read it verbatim, never invent
                "page": _int,             # REAL absolute PDF page number -- copy from the
                                           # "=== PDF PAGE N ===" label, never compute/guess it
            })},
        })},
    })},
    "standards": {"type": "array", "items": _obj({
        "code": _str,                # e.g. "7.4D"
        "description": _str,         # text of the standard, "" if not printed
    })},
    "assessments": {"type": "array", "items": _obj({
        "name": _str,
        "topic_number": _str,
        "teks": _strs,
        "page_label": _str,          # printed footer label of the page this came from
        "page": _int,                # REAL absolute PDF page number, copied not computed
    })},
    "supplementary": {"type": "array", "items": _obj({
        "type": _supp_type,           # what kind of page content this is
        "page": _int,                 # REAL absolute PDF page number, copied not computed
        "page_label": _str,
        "topic_number": _str,         # "" if module-level rather than a specific topic
        "text": _str,                 # the actual content, close to verbatim
    })},
})

SYSTEM = (
    "You extract the structure of a K-12 curriculum document into JSON. "
    "Read the given pages and pull out: the module (if a module title page "
    "or overview is present), its topics, and within each topic its lessons. "
    "\n\nTOPIC vs LESSON — this distinction matters, get it right: a TOPIC is a major "
    "unit-level heading (e.g. a 'TOPIC 1'/'TOPIC 2' divider page, or a bold banner "
    "naming the unit) — there are usually only 2-5 per module. A LESSON is a smaller "
    "heading INSIDE a topic (e.g. 'LESSON 1', 'LESSON 2', 'LESSON 3'... or an "
    "'INTRODUCTION LESSON') — a topic's own overview/TOC page will usually list its "
    "lessons (e.g. 'LESSON 1 ... 5', 'LESSON 2 ... 21', 'LESSON 3 ... 37'). A LESSON "
    "header is NEVER a new topic — put it in the CURRENT (most recently seen) topic's "
    "lessons array, keyed by its lesson number, even if that topic started in an "
    "earlier batch you don't have anymore (just re-emit the same topic number/title "
    "you'd use for it, and the caller will merge batches of the same topic together).\n\n"
    "TITLES MUST BE VERBATIM — this applies to module_title and every topic/lesson title: "
    "COPY the exact heading as printed on the page (its own dedicated title text, e.g. from a "
    "module/topic divider page or a 'LESSON N <Title>' table-of-contents line), character for "
    "character. Do NOT paraphrase, summarize, or invent a title that merely describes the "
    "topic/lesson in your own words — if you can't find the literal printed title, use '' "
    "rather than making one up. This is exactly as strict as the source_text rule below.\n\n"
    "MATERIALS_NEEDED MUST BE VERBATIM AND COMPLETE — this is a transcription task, not a "
    "guess: a lesson's Pacing Guide row has a 'Materials Needed:' line listing exactly what's "
    "required (or the word 'None'). Copy ONLY the items literally printed there, one list "
    "entry per item exactly as written (e.g. 'Expression Cards', not 'Algebra Tiles' or any "
    "other item that sounds plausible for the topic but isn't printed on this specific row). "
    "Do NOT add items you'd expect a lesson like this to need, do NOT combine materials from "
    "a different lesson's row, and do NOT infer materials from what an activity describes — "
    "only what the Materials Needed line itself says. If it says 'None' or is blank, return "
    "an empty list — never pad it.\n\n"
    "For each topic give: its own standard codes (teks), which are marked 'readiness'/"
    "bold, sessions, and its overall page span. For each lesson within it give: its "
    "own number/title, the standard codes THIS lesson teaches (a Topic's Pacing Guide "
    "table usually lists this per-lesson row, e.g. 'Lesson 1 ... TEKS: A.3C, A.7A' — "
    "use '' / empty list if a lesson's codes aren't separately given, the caller falls "
    "back to the topic's codes), materials_needed per the rule above, its own page span, "
    "and its learning outcomes. For each learning outcome, give: a short summary bullet ('Students "
    "...'); the source_text (the actual instructional text, example, or problem "
    "printed on the page that the outcome is drawn from — copy it close to verbatim, "
    "not a paraphrase); component_role if the page labels this item as a distinct "
    "activity type — e.g. 'Bellwork', 'Exit Ticket', 'Talk the Talk', an ELL/language-"
    "support box, or similar (use '' if it's just regular instructional content with "
    "no such label); is_critical (true only if the page marks this item with a "
    "star, 'critical', 'must-do', or similar emphasis marker — otherwise false); "
    "and lesson_phase if the page tags it with a phase like 'Getting Started', "
    "'Develop', or 'Demonstrate' (distinct from component_role — e.g. a 'Talk the "
    "Talk' component is usually phase 'demonstrate'; use '' if no phase tag is shown). "
    "Also give the standard codes any assessment (e.g. 'End of Topic Assessment') "
    "aligns to, and any standard descriptions printed.\n\n"
    "EVERY PAGE SHOULD BE ACCOUNTED FOR in your output. If a page's content doesn't "
    "fit a lesson/learning-outcome/assessment (e.g. a Module or Topic Assessment "
    "Summary table, a Pacing Guide, a Self-Reflection prompt, or a Module/Topic "
    "Overview narrative), capture it in `supplementary` instead of skipping it — give "
    "its type, page, page_label, the topic_number it belongs to (or '' if it's "
    "module-level, not tied to one topic), and its text close to verbatim. Don't "
    "duplicate content you already captured as a learning_outcome/assessment — "
    "`supplementary` is for content that has nowhere else to go.\n\n"
    "PAGE NUMBERS: each page's text below is labeled with its real, absolute PDF page "
    "number, e.g. '=== PDF PAGE 211 ===' immediately before that page's text. When you "
    "report `page` (or `page_start`/`page_end` for a topic/lesson's span), COPY that "
    "exact number from the label — never count, compute, or guess it. Also read the "
    "small printed page label in the page's own footer/header (e.g. '21', '4B', "
    "'IL-1E', distinct from the PDF page number) and report it verbatim as "
    "page_label; never invent a label if none is visible.\n\n"
    "Only report what actually appears on these pages. Use exact standard codes. If "
    "a field is absent, return an empty string, 0, or empty list as appropriate — "
    "never invent content."
)


def docling_page_text(pdf_path):
    """Convert a PDF via docling and group its extracted item text by absolute PDF page number.
    Returns {page_no (int): text (str)}. Lazy-imports docling so files that only need
    build_page_batches()/extract_from_text() (fed pre-reconstructed text some other way, e.g.
    from an already-indexed Qdrant collection) don't need docling installed."""
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()
    doc = converter.convert(pdf_path).document
    pages = {}
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None)
        text = getattr(item, "text", "") or ""
        if prov and text:
            pages.setdefault(prov[0].page_no, []).append(text)
    return {p: " ".join(t) for p, t in pages.items()}


def build_page_batches(page_text, batch_size=25):
    """page_text: {pdf_page (int) -> text} for ONE module (already reconstructed from docling,
    e.g. via build_content_index.py's build_page_text() grouping curriculum_raw_text chunks by
    page). Yields (first_page, last_page, labeled_text) — labeled_text has every page's real
    number inline ('=== PDF PAGE N ===') so Claude only ever has to copy a number it was already
    given, never count/guess one. Batches are plain text, not base64 PDF -- much cheaper per
    token, so bigger batches than the old 15-page PDF-chunk convention are practical."""
    pages = sorted(page_text)
    for start in range(0, len(pages), batch_size):
        batch_pages = pages[start:start + batch_size]
        labeled_text = "\n\n".join(
            f"=== PDF PAGE {p} ===\n{page_text[p]}" for p in batch_pages)
        yield batch_pages[0], batch_pages[-1], labeled_text


def text_message_params(labeled_text, page_range_label):
    """The Messages-API params for one extraction batch — shared by streaming and Batch."""
    return dict(
        model=MODEL, max_tokens=16000, thinking={"type": "adaptive"}, system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "text", "text":
                f"Extract the curriculum structure from these pages ({page_range_label}):\n\n"
                f"{labeled_text}"},
        ]}])


def parse_extraction(msg):
    """Pull the structured JSON out of a completed message (streaming or batch result)."""
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


def extract_from_text(client, labeled_text, page_range_label):
    """One structured-extraction call over a page-labeled text batch (interactive/streaming).
    No page-index math needed afterward -- Claude reports real page numbers directly, since
    they were already given inline in labeled_text."""
    with client.messages.stream(**text_message_params(labeled_text, page_range_label)) as stream:
        return parse_extraction(stream.get_final_message())


def combine_modules(chunk_lists, jurisdiction, framework, case, course_name=None,
                     real_materials_list=None):
    """Merge several modules' chunk-lists into ONE deduped graph. Each list is the chunks for
    one PDF/module. Shared standards collapse by ID; optionally add a Course that hasPart-links
    every module.

    `real_materials_list`, if given, is a list parallel to `chunk_lists` -- one
    real_content.find_real_materials()-shaped dict (or None) per module, forwarded to
    build_graph() to override materials_needed with the Pacing Guide's literal text."""
    nodes, rels, seen_n, seen_r = [], [], set(), set()
    module_ids = []
    for i, chunks in enumerate(chunk_lists):
        if not chunks:
            continue
        module, topics, standards, assessments, supplementary = merge(chunks)
        if case:                                   # enrich descriptions from the framework
            for t in topics.values():
                for c in t["teks"]:
                    if not standards.get(c):
                        stt = case["code_to_stmt"].get(_norm_code(c))
                        if stt:
                            standards[c] = stt
        real_materials = real_materials_list[i] if real_materials_list else None
        n, r = build_graph(module, topics, standards, assessments, jurisdiction, framework, case,
                           real_materials=real_materials,
                           scope=course_name or "", supplementary=supplementary)
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


def jsonify_topic(v):
    """A topic dict (from merge()) has sets nested inside it (its own `teks`/`readiness`, and
    each lesson's `teks`) -- convert them all to sorted lists so the topic is JSON-dumpable."""
    return {**v, "teks": sorted(v["teks"]), "readiness": sorted(v["readiness"]),
            "lessons": {lk: {**l, "teks": sorted(l["teks"])} for lk, l in v["lessons"].items()}}


def merge(chunks):
    """Fold per-batch extractions into one curriculum, deduping by key. Page numbers (`page`/
    `page_start`/`page_end`) are now real, Claude-echoed absolute page numbers -- no more
    separate pdf-index math needed, just first-non-empty/last-non-empty folding same as labels."""
    module = {"number": "", "title": "", "teks": set()}
    topics, standards, assessments = {}, {}, {}
    supplementary = []
    for c in chunks:
        if c.get("module_number"):
            module["number"] = c["module_number"]
            module["title"] = module["title"] or c.get("module_title", "")
        module["teks"].update(c.get("module_teks", []))
        for t in c.get("topics", []):
            key = t["number"] or t["title"]
            cur = topics.setdefault(key, {"number": t["number"], "title": t["title"],
                                          "teks": set(), "readiness": set(),
                                          "sessions": "", "outcomes": [], "lessons": {},
                                          "page_start_label": "", "page_end_label": "",
                                          "page_start": None, "page_end": None})
            cur["title"] = cur["title"] or t["title"]
            cur["sessions"] = cur["sessions"] or t.get("sessions", "")
            cur["teks"].update(t.get("teks", []))
            cur["readiness"].update(t.get("readiness_teks", []))
            cur["page_start_label"] = cur["page_start_label"] or t.get("page_start_label", "")
            cur["page_end_label"] = t.get("page_end_label", "") or cur["page_end_label"]
            if cur["page_start"] is None:
                cur["page_start"] = t.get("page_start")
            if t.get("page_end") is not None:
                cur["page_end"] = t.get("page_end")

            # `lessons` is the real (new) schema; a flat `learning_outcomes` directly on the
            # topic (e.g. the bundled SAMPLE_CHUNK demo data, predating the lessons[] nesting)
            # is treated as one implicit unnamed lesson so old-shape callers still work.
            lessons_in = t.get("lessons") or ([{"number": "", "title": "", "teks": [],
                                                "materials_needed": [],
                                                "learning_outcomes": t["learning_outcomes"]}]
                                              if t.get("learning_outcomes") else [])
            for l in lessons_in:
                lkey = l.get("number") or l.get("title") or "_default"
                curl = cur["lessons"].setdefault(lkey, {
                    "number": l.get("number", ""), "title": l.get("title", ""),
                    "teks": set(), "materials_needed": [], "outcomes": [],
                    "page_start_label": "", "page_end_label": "",
                    "page_start": None, "page_end": None})
                curl["title"] = curl["title"] or l.get("title", "")
                curl["teks"].update(l.get("teks", []))
                curl["outcomes"].extend(l.get("learning_outcomes", []))
                cur["outcomes"].extend(l.get("learning_outcomes", []))  # flat aggregate, display-only
                for m in l.get("materials_needed", []):
                    if m not in curl["materials_needed"]:
                        curl["materials_needed"].append(m)
                curl["page_start_label"] = curl["page_start_label"] or l.get("page_start_label", "")
                curl["page_end_label"] = l.get("page_end_label", "") or curl["page_end_label"]
                if curl["page_start"] is None:
                    curl["page_start"] = l.get("page_start")
                if l.get("page_end") is not None:
                    curl["page_end"] = l.get("page_end")
        for s in c.get("standards", []):
            if s["code"]:
                standards.setdefault(s["code"], s.get("description", ""))
        for a in c.get("assessments", []):
            key = (a["name"], a.get("topic_number", ""))
            cur_a = assessments.setdefault(key, {"name": a["name"],
                                         "topic_number": a.get("topic_number", ""),
                                         "teks": set(), "page_label": "", "page": None})
            cur_a["teks"].update(a.get("teks", []))
            cur_a["page_label"] = cur_a["page_label"] or a.get("page_label", "")
            if cur_a["page"] is None:
                cur_a["page"] = a.get("page")
        seen_supp = {(s["type"], s["page"], s["topic_number"]) for s in supplementary}
        for sup in c.get("supplementary", []):
            supp_key = (sup["type"], sup.get("page"), sup.get("topic_number", ""))
            if supp_key not in seen_supp:
                supplementary.append(sup)
                seen_supp.add(supp_key)
    return module, topics, standards, assessments, supplementary


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
_LABEL_NUM = re.compile(r"(\d+)")


def _numeric_label(label):
    m = _LABEL_NUM.match(label or "")
    return int(m.group(1)) if m else None


def _fix_backward_range(d):
    """Swap page_start_label/page_end_label and page_start/page_end in place if backward --
    these drift independently during merge()'s per-field folding, so a swap on one doesn't
    imply a swap on the other."""
    s_lbl, e_lbl = _numeric_label(d.get("page_start_label")), _numeric_label(d.get("page_end_label"))
    if s_lbl is not None and e_lbl is not None and e_lbl < s_lbl:
        d["page_start_label"], d["page_end_label"] = d["page_end_label"], d["page_start_label"]
    s_pdf, e_pdf = d.get("page_start"), d.get("page_end")
    if s_pdf and e_pdf and e_pdf < s_pdf:   # 0/None treated as "missing", never swapped
        d["page_start"], d["page_end"] = d["page_end"], d["page_start"]


def build_graph(module, topics, standards, assessments, jurisdiction, framework, case=None,
                scope="", supplementary=(), real_materials=None):
    """Build nodes/edges. If `case` (a loaded CASE file) is given, the standards side is the
    real framework — full descriptions, hierarchy (hasChild), and cross-grade links — and
    lessons attach to those real standard nodes by matching codes. Without it, standards are
    synthesized from the bare codes seen in the PDF.

    `scope` namespaces the CONTENT ids (Module/Topic/Assessment/LearningComponent) so the same
    module number in different grades/courses doesn't collide when graphs are merged. Standards
    ids are never scoped — they stay shared across grades.

    `real_materials` (optional, from real_content.find_real_materials()): {topic_number:
    [materials_list, ...]}, one list per lesson in that topic's Pacing Guide order. Overrides
    Claude's per-lesson materials_needed. This exists because materials_needed is unreliable in
    a way titles/sourceText aren't: a topic's Pacing Guide (the one place "Materials needed:" is
    printed) sits on its own early page, while a lesson's own activity content runs many pages
    later — batched extraction sends those to separate, independent Claude calls, so whichever
    batch is extracting the lesson's activities often never sees the Pacing Guide line it's
    supposed to copy, and falls back to guessing from the activity content instead. Titles don't
    have this problem (a lesson's title appears on its own TOC page, in the same batch as its
    Pacing Guide), so they stay Claude-sourced."""
    nodes, rels = [], []
    real_materials = real_materials or {}

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
        _fix_backward_range(t)
        top_id = nid("Topic", f"{module['number']}:{key}")
        node(top_id, "LessonGrouping", name=t["title"], groupName="Topic",
             groupLevel="1", ordinalName=f"Topic {t['number']}", position=t["number"],
             academicSubject="Mathematics", curriculumLabel="Topic",
             pageStartLabel=t.get("page_start_label", ""), pageEndLabel=t.get("page_end_label", ""),
             pdfPageStart=t.get("page_start"), pdfPageEnd=t.get("page_end"))
        rel("hasPart", mod_id, "LessonGrouping", top_id, "LessonGrouping")
        for code in sorted(t["teks"]):
            rel("hasEducationalAlignment", top_id, "LessonGrouping",
                resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID",
                alignmentType="teaches", curriculumAlignmentType="addressing")

        # Pacing Guides list lessons strictly in page order, so zip real_materials positionally
        # by numeric lesson order (not dict insertion order, which merge() doesn't guarantee).
        lesson_keys_sorted = sorted(t.get("lessons", {}),
                                     key=lambda k: (_numeric_label(k) is None, _numeric_label(k) or 0))
        materials_for_topic = real_materials.get(key, [])
        for i, lkey in enumerate(lesson_keys_sorted):
            l = t["lessons"][lkey]
            _fix_backward_range(l)
            lesson_id = nid("Lesson", f"{module['number']}:{key}:{lkey}")
            lesson_teks = l["teks"] or t["teks"]   # fall back to the topic's codes if unlisted
            materials = materials_for_topic[i] if i < len(materials_for_topic) \
                else l.get("materials_needed", [])
            node(lesson_id, "Lesson", name=l["title"], ordinalName=f"Lesson {l['number']}",
                 position=l["number"], academicSubject="Mathematics", curriculumLabel="Lesson",
                 materialsNeeded=materials,
                 pageStartLabel=l.get("page_start_label", ""), pageEndLabel=l.get("page_end_label", ""),
                 pdfPageStart=l.get("page_start"), pdfPageEnd=l.get("page_end"))
            rel("hasPart", top_id, "LessonGrouping", lesson_id, "Lesson")
            for code in sorted(lesson_teks):
                rel("hasEducationalAlignment", lesson_id, "Lesson",
                    resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID",
                    alignmentType="teaches", curriculumAlignmentType="addressing")
            for i, outcome in enumerate(l["outcomes"]):
                lc_id = nid("LearningComponent", f"{module['number']}:{key}:{lkey}:{i}")
                node(lc_id, "LearningComponent", description=outcome["outcome"],
                     sourceText=outcome.get("source_text", ""),
                     componentRole=outcome.get("component_role", ""),
                     isCritical=bool(outcome.get("is_critical", False)),
                     lessonPhase=outcome.get("lesson_phase", ""),
                     pageLabel=outcome.get("page_label", ""), pdfPage=outcome.get("page"),
                     academicSubject="Mathematics")
                rel("hasPart", lesson_id, "Lesson", lc_id, "LearningComponent")
                for code in sorted(lesson_teks):
                    rel("supports", lc_id, "LearningComponent",
                        resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID")

    for (name, topic_no), a in assessments.items():
        asm_id = nid("Assessment", f"{module['number']}:{topic_no}:{name}")
        node(asm_id, "Assessment", name=name, academicSubject="Mathematics",
             curriculumLabel="Assessment",
             pageLabel=a.get("page_label", ""), pdfPage=a.get("page"))
        if topic_no in topics:
            rel("hasPart", nid("Topic", f"{module['number']}:{topic_no}"),
                "LessonGrouping", asm_id, "Assessment")
        for code in sorted(a["teks"]):
            rel("hasEducationalAlignment", asm_id, "Assessment",
                resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID",
                alignmentType="assesses", curriculumAlignmentType="addressing")

    # Content that doesn't fit a lesson/learning-outcome/assessment (Assessment Summaries,
    # Pacing Guides, Self-Reflections, Overviews) -- reuses LearningComponent rather than a new
    # node type, so it gets embedded into Qdrant automatically with zero extra indexing code.
    _SUPP_ROLE = {"assessment_summary": "Assessment Summary", "pacing_guide": "Pacing Guide",
                  "self_reflection": "Self-Reflection", "overview": "Overview", "other": ""}
    for i, sup in enumerate(supplementary):
        supp_id = nid("LearningComponent", f"{module['number']}:supp:{i}")
        node(supp_id, "LearningComponent", description=sup.get("text", ""),
             sourceText=sup.get("text", ""),
             componentRole=_SUPP_ROLE.get(sup.get("type", ""), ""),
             isCritical=False, lessonPhase="",
             pageLabel=sup.get("page_label", ""), pdfPage=sup.get("page"),
             academicSubject="Mathematics")
        topic_no = sup.get("topic_number", "")
        if topic_no and topic_no in topics:
            parent_id = nid("Topic", f"{module['number']}:{topic_no}")
            parent_label = "LessonGrouping"
        else:
            parent_id, parent_label = mod_id, "LessonGrouping"
        rel("hasPart", parent_id, parent_label, supp_id, "LearningComponent")

    return nodes, rels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages-per-chunk", type=int, default=25)
    ap.add_argument("--jurisdiction", default="Texas")
    ap.add_argument("--framework", default="TEKS")
    ap.add_argument("--case", help="path to a 1EdTech CASE framework JSON (optional)")
    ap.add_argument("--nodes-out", default="nodes.new.jsonl")
    ap.add_argument("--rels-out", default="relationships.new.jsonl")
    ap.add_argument("--extracted-out", default="curriculum_extracted.json")
    args = ap.parse_args()

    client = anthropic.Anthropic()
    print("Converting PDF via docling (page-numbered text)...", flush=True)
    page_text = docling_page_text(args.pdf)
    print(f"  {len(page_text)} pages of text extracted", flush=True)
    chunks = []
    for first, last, labeled_text in build_page_batches(page_text, args.pages_per_chunk):
        label = f"pages {first}-{last}"
        print(f"  extracting {label} ...", flush=True)
        try:
            chunks.append(extract_from_text(client, labeled_text, label))
        except Exception as e:
            print(f"    ! batch {label} failed: {e}", file=sys.stderr)
    module, topics, standards, assessments, supplementary = merge(chunks)
    print(f"merged: 1 module, {len(topics)} topics, {len(standards)} standards, "
          f"{len(assessments)} assessments, {len(supplementary)} supplementary entries")

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
               "topics": {k: jsonify_topic(v) for k, v in topics.items()},
               "standards": standards,
               "assessments": {f"{k[0]}|{k[1]}": {**v, "teks": sorted(v["teks"])}
                               for k, v in assessments.items()}},
              open(args.extracted_out, "w"), indent=1)

    nodes, rels = build_graph(module, topics, standards, assessments,
                              args.jurisdiction, args.framework, case,
                              supplementary=supplementary)
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
