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
import argparse, hashlib, json, re, sys, uuid
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
              "enum": ["assessment_summary", "pacing_guide", "self_reflection", "overview",
                       "discourse_questions", "differentiation_strategy", "other"]}

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
    "Summary table, a Pacing Guide, a Self-Reflection prompt, a Module/Topic "
    "Overview narrative, a 'QUESTIONS TO SUPPORT DISCOURSE' box of facilitation "
    "prompts, or a 'DIFFERENTIATION STRATEGY'/'Just in Time Support'/'Challenge "
    "Opportunity' sidebar), capture it in `supplementary` instead of skipping it — "
    "give its type, page, page_label, the topic_number it belongs to (or '' if it's "
    "module-level, not tied to one topic), and its text close to verbatim. Don't "
    "duplicate content you already captured as a learning_outcome/assessment — "
    "`supplementary` is for content that has nowhere else to go.\n\n"
    "PAGE NUMBERS: each page's text below is labeled with its real, absolute PDF page "
    "number, e.g. '=== PDF PAGE 211 ===' immediately before that page's text. When you "
    "report `page` (or `page_start`/`page_end` for a topic/lesson's span), COPY that "
    "exact number from the label — never count, compute, or guess it. Also read the "
    "small printed page label in the page's own footer/header (e.g. '21', '4B', "
    "'IL-1E', distinct from the PDF page number) and report it verbatim as "
    "page_label.\n\n"
    "page_label IS NOT OPTIONAL — this applies to every learning_outcome, every "
    "assessment, AND every supplementary item alike, with no exceptions for whichever "
    "category feels like a catch-all. Every single page in these books has one printed "
    "(next to the 'MODULE N • TOPIC N • LESSON N' running header/footer, sometimes "
    "before it, sometimes after) — a blank page_label should be rare, not the default. "
    "Before leaving it blank, look again: the label is small but it is there. Treat "
    "this with the same seriousness as the exact-page-number rule above and the "
    "verbatim-source_text rule — a page_label you didn't bother to find is exactly as "
    "unusable as one you invented.\n\n"
    "Only report what actually appears on these pages. Use exact standard codes. If "
    "a field is absent, return an empty string, 0, or empty list as appropriate — "
    "never invent content."
)


def docling_page_text(pdf_path):
    """Convert a PDF via docling and group its extracted item text by absolute PDF page number.
    Returns {page_no (int): text (str)}. Lazy-imports docling so files that only need
    build_page_batches()/extract_from_text() (fed pre-reconstructed text some other way, e.g.
    from an already-indexed Qdrant collection) don't need docling installed.

    NOT used anywhere in the active pipeline (as of the pypdfium2 switch -- both Claude's batch
    extraction input and the real_pages verbatim-sourceText override use pdfium_page_text()
    instead). Kept as a standalone utility for comparison/debugging: confirmed live that docling
    silently drops content on dense/tabular page layouts (e.g. Pacing Guide tables) -- one page
    returned 290 chars via this function where the real page has 2004 via pdfium_page_text(), no
    error or signal anything was missing."""
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


def pdfium_page_text(pdf_path):
    """{page_no (1-indexed int): text (str)} via pypdfium2 (Google's PDFium) -- the source for
    BOTH Claude's batch extraction input (see batch_pipeline.cached_page_text()) and the
    real_pages verbatim-sourceText override (see build_graph()'s docstring). NOT docling_page_
    text() -- confirmed live against docling on the same pages: complete (no silently-dropped
    table/list content), fast (~11s for a 365-page module), same library Page Inspector already
    uses for rendering."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    try:
        pages = {}
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                pages[i + 1] = textpage.get_text_range() or ""
            finally:
                textpage.close()
                page.close()
        return pages
    finally:
        doc.close()


def build_page_batches(page_text, batch_size=25, overlap=3):
    """page_text: {pdf_page (int) -> text} for ONE module, from pdfium_page_text(). Yields
    (first_page, last_page, labeled_text) — labeled_text has every page's real number inline
    ('=== PDF PAGE N ===') so Claude only ever has to copy a number it was already given, never
    count/guess one. Batches are plain text, not base64 PDF -- much cheaper per token, so bigger
    batches than the old 15-page PDF-chunk convention are practical.

    `overlap`: the last `overlap` pages of the PREVIOUS batch are repeated at the START of this
    one (as extra context only -- `first_page`/`last_page` still describe the non-overlapping
    "core" window, so custom_id/state tracking is unaffected). Fixes a confirmed live bug: with
    overlap=0, a topic whose heading page fell in batch N but whose lesson content continued into
    batch N+1 gave batch N+1 no way to see that heading at all -- it either extracted a
    blank-titled phantom topic for its own real content (see merge()'s _reconcile_blank_topics)
    or, worse, misattributed that content to a different topic title actually visible in its own
    window (Grade 3 Module 3: 23 real multiplication lessons ended up titled "Division Using
    Units of 2 and 3" this way). A few pages of leading context is enough for Claude to recognize
    "this continues the topic introduced just before" without re-processing whole extra chunks.
    merge()'s outcome dedup (keyed on (page, outcome) -- see the `seen_outcome_keys` loop) is
    what keeps this overlap from creating duplicate LearningComponent nodes for the shared pages."""
    pages = sorted(page_text)
    for start in range(0, len(pages), batch_size):
        core = pages[start:start + batch_size]
        overlap_pages = pages[max(0, start - overlap):start]
        window = overlap_pages + core
        labeled_text = "\n\n".join(
            f"=== PDF PAGE {p} ===\n{page_text[p]}" for p in window)
        yield core[0], core[-1], labeled_text


def lesson_units(pages, lesson_of):
    """Split the sorted page list into contiguous units, one per lesson, using the running-header
    ranges from lesson_pages.lesson_ranges(lesson_of). Untagged pages BETWEEN lessons (topic
    openers, lesson intros, pacing guides) join the NEXT lesson's unit, so a topic's heading always
    travels with its first lesson; untagged pages after the last lesson (assessments, answer keys)
    form a trailing unit."""
    import lesson_pages
    owner = {}
    for key, (lo, hi) in sorted(lesson_pages.lesson_ranges(lesson_of).items(), key=lambda kv: kv[1]):
        for p in range(lo, hi + 1):
            owner.setdefault(p, key)
    units, current, current_key, pending = [], [], None, []
    for p in pages:
        key = owner.get(p)
        if key is None:
            pending.append(p)
        elif key == current_key:
            current += pending + [p]
            pending = []
        else:
            if current:
                units.append(current)
            current, current_key, pending = pending + [p], key, []
    if current:
        units.append(current)
    if pending:
        units.append(pending)
    return units


def build_lesson_batches(page_text, lesson_of, max_pages=30, overlap=3):
    """Like build_page_batches(), but batch edges fall on LESSON boundaries instead of every
    `batch_size` pages: whole lessons (see lesson_units()) are packed into batches of at most
    `max_pages`, and only a lesson longer than that on its own is split. Fixed 25-page windows
    split 572 of 1,068 lessons across batches (measured on every course's PDFs) -- the root of the
    blank-title topics, misattributed lesson ranges and truncated page ranges that
    _reconcile_blank_topics() and the repair scripts exist to clean up. Same yield shape and
    `overlap` leading-context semantics as build_page_batches(); falls back to it when the PDF has
    no recognizable lesson headers (`lesson_of` empty)."""
    if not lesson_of:
        yield from build_page_batches(page_text, batch_size=25, overlap=overlap)
        return
    import lesson_pages
    pages = sorted(page_text)
    # pages inside a lesson's header range -- not every tagged page: front matter can carry
    # stray "Lesson N:" lines (a table of contents) outside any lesson's range
    tagged = {p for lo, hi in lesson_pages.lesson_ranges(lesson_of).values()
              for p in range(lo, hi + 1)}
    units = []
    for unit in lesson_units(pages, lesson_of):
        # A lesson plus its intro pages over the limit: give the intro (e.g. a module's ~20 pages
        # of front matter before Lesson 1) its own unit rather than splitting the lesson itself --
        # the lesson's batch still sees the last `overlap` intro pages as leading context.
        k = next((i for i, p in enumerate(unit) if p in tagged), 0)
        if len(unit) > max_pages and k and len(unit) - k <= max_pages:
            units += [unit[:k], unit[k:]]
        else:
            units.append(unit)
    # Never two topics in one batch: a batch holding the end of Topic D and the start of Topic E
    # only shows E's heading, and the model filed Topic D's last lesson under E (Grade 3 Module 1
    # test). A batch with no heading at all is fine -- merge() reattaches its lessons by number.
    topic_of = lesson_pages.topic_of_pages(page_text, lesson_of)

    def unit_topic(unit):
        return next((topic_of.get(p) for p in unit if p in tagged), None)

    batches, batch, batch_topic = [], [], None
    for unit in units:
        topic = unit_topic(unit)
        if len(unit) > max_pages:
            if batch:
                batches.append(batch)
            batches += [unit[i:i + max_pages] for i in range(0, len(unit), max_pages)]
            batch, batch_topic = [], None
        elif batch and (len(batch) + len(unit) > max_pages
                        or (topic and batch_topic and topic != batch_topic)):
            batches.append(batch)
            batch, batch_topic = list(unit), topic
        else:
            batch += unit
            batch_topic = batch_topic or topic
    if batch:
        batches.append(batch)
    index = {p: i for i, p in enumerate(pages)}
    for core in batches:
        start = index[core[0]]
        window = pages[max(0, start - overlap):start] + core
        labeled_text = "\n\n".join(f"=== PDF PAGE {p} ===\n{page_text[p]}" for p in window)
        yield core[0], core[-1], labeled_text


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
                     real_materials_list=None, real_pages_list=None, source_pdf_keys=None):
    """Merge several modules' chunk-lists into ONE deduped graph. Each list is the chunks for
    one PDF/module. Shared standards collapse by ID; optionally add a Course that hasPart-links
    every module.

    `real_materials_list`, if given, is a list parallel to `chunk_lists` -- one
    real_content.find_real_materials()-shaped dict (or None) per module, forwarded to
    build_graph() to override materials_needed with the Pacing Guide's literal text.

    `real_pages_list`, if given, is a list parallel to `chunk_lists` -- one
    pdfium_page_text()-shaped {page: text} dict (or None) per module, forwarded to build_graph()
    to override sourceText with the real page text where that's unambiguous (see build_graph's
    docstring). NOT docling_page_text() -- see that function's docstring for why (silently drops
    content on dense/tabular layouts; confirmed live, not tolerable for a field promised verbatim).

    `source_pdf_keys`, if given, is a list parallel to `chunk_lists` -- one citation.
    pdf_key_for_module()-produced string (or None) per module, stamped as `sourcePdfKey` onto
    every LearningComponent/Assessment node from that module (see citation.py)."""
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
        real_pages = real_pages_list[i] if real_pages_list else None
        source_pdf_key = source_pdf_keys[i] if source_pdf_keys else None
        n, r = build_graph(module, topics, standards, assessments, jurisdiction, framework, case,
                           real_materials=real_materials, real_pages=real_pages,
                           source_pdf_key=source_pdf_key,
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


def _fold_topic(dst, src):
    """Fold `src`'s accumulated topic data into `dst` in place -- same first-non-empty/
    last-non-empty/union semantics merge() already uses when folding a raw per-chunk topic dict,
    just applied topic-dict-to-topic-dict (see _reconcile_blank_topics)."""
    dst["title"] = dst["title"] or src["title"]
    dst["sessions"] = dst["sessions"] or src["sessions"]
    dst["teks"].update(src["teks"])
    dst["readiness"].update(src["readiness"])
    dst["outcomes"].extend(src["outcomes"])
    dst["page_start_label"] = dst["page_start_label"] or src["page_start_label"]
    dst["page_end_label"] = src["page_end_label"] or dst["page_end_label"]
    if dst["page_start"] is None:
        dst["page_start"] = src["page_start"]
    elif src["page_start"] is not None:
        dst["page_start"] = min(dst["page_start"], src["page_start"])
    if src["page_end"] is not None:
        dst["page_end"] = max(dst["page_end"], src["page_end"]) if dst["page_end"] is not None \
            else src["page_end"]
    for lkey, l in src["lessons"].items():
        if lkey not in dst["lessons"]:
            dst["lessons"][lkey] = l
            continue
        dl = dst["lessons"][lkey]
        dl["title"] = dl["title"] or l["title"]
        dl["teks"].update(l["teks"])
        dl["outcomes"].extend(l["outcomes"])
        for m in l["materials_needed"]:
            if m not in dl["materials_needed"]:
                dl["materials_needed"].append(m)
        dl["page_start_label"] = dl["page_start_label"] or l["page_start_label"]
        dl["page_end_label"] = l["page_end_label"] or dl["page_end_label"]
        if dl["page_start"] is None:
            dl["page_start"] = l["page_start"]
        if l["page_end"] is not None:
            dl["page_end"] = l["page_end"]


def _reconcile_blank_topics(topics):
    """Fix a chunking artifact: each 25-page batch is extracted independently (see
    batch_pipeline.submit_course_batch's build_page_batches(batch_size=25)), so a Topic whose
    real content spans more than one chunk only shows its title/number heading in whichever
    chunk happens to contain that heading page -- a LATER chunk that picks up the same topic
    mid-stream (no heading visible in its own 25-page window) has no way to know the topic's
    number/title, and merge()'s `key = t["number"] or t["title"]` then keys that chunk's real
    lesson content under a brand-new blank-title topic entry instead of folding it into the real
    one. Confirmed live: Grade 3 Module 3's "Multiplication and the Meaning of the Factors"
    (page 18) had a same-module blank-title phantom starting page 23, carrying 177 real
    LearningComponent descendants that belonged under the named topic.

    Fix: for every blank-title topic in this module, try three signals in order of reliability:
      1. EXACT topic-number match against a named (real-title) topic (handles the case where the
         continuation chunk still echoed the same topic number even without the title).
      2. LESSON-NUMBER CONTINUITY: a continuation chunk's lessons pick up right after the real
         topic's own last-known lesson number (e.g. blank topic's lessons start at 5, and exactly
         one named topic in this module tops out at lesson 4) -- a much stronger signal than raw
         page proximity, confirmed necessary live: Grade 3 Module 3 had two named topics starting
         only ~2-5 pages apart ("Multiplication and the Meaning of the Factors" and "Multiplication
         Using Units of 2 and 3", both page ~21), so nearest-page-start alone picked the wrong one
         for a blank topic whose real lessons continued the FIRST one's numbering, not the second's.
      3. PAGE PROXIMITY fallback: whichever named topic's page_start is closest below the blank
         topic's own page_start (a continuation always starts later than its real topic's own
         heading page, never earlier) -- only used when lesson numbers aren't available/numeric on
         either side.
    Folds via _fold_topic (same first-non-empty/union semantics as the rest of merge(), not a
    guess) then drops the blank key entirely. A blank topic with no named topic to attach to at
    all (e.g. genuinely the module's very first topic, extraction never captured its heading
    anywhere) is left as its own entry -- reconciling it would require inventing a title, which is
    worse than leaving it honestly unnamed."""
    def _lesson_nums(t):
        nums = [_numeric_label(l.get("number")) for l in t["lessons"].values()]
        return [n for n in nums if n is not None]

    named = [(k, t) for k, t in topics.items() if t["title"]]
    blank_keys = [k for k, t in topics.items() if not t["title"]]
    for bkey in blank_keys:
        blank = topics[bkey]
        # 0. LESSON-LEVEL match first: a blank-topic lesson whose number already exists under
        #    exactly one named topic is that same lesson continuing past a batch edge -- fold it
        #    there on its own. The whole-topic rules below put a batch's lessons 4-6 under Topic A
        #    (ends at Lesson 3) when Topic B already held Lesson 4 from the previous batch
        #    (Grade 3 Module 1 lesson-aligned extraction test).
        for lkey, lesson in list(blank["lessons"].items()):
            num = _numeric_label(lesson.get("number"))
            owners = [t for k, t in named if num is not None
                      and num in [_numeric_label(l.get("number")) for l in t["lessons"].values()]]
            if len(owners) == 1:
                _fold_topic(owners[0], {
                    "title": "", "sessions": "", "teks": set(), "readiness": set(),
                    "outcomes": lesson["outcomes"], "page_start_label": "", "page_end_label": "",
                    "page_start": None, "page_end": None,
                    "lessons": {next(lk for lk, l in owners[0]["lessons"].items()
                                     if _numeric_label(l.get("number")) == num): lesson}})
                del blank["lessons"][lkey]
        if not blank["lessons"]:  # all reattached (its "outcomes" is just their aggregate)
            del topics[bkey]
            continue
        target = None
        if blank["number"]:
            target = next((t for k, t in named if t["number"] == blank["number"]), None)
        if target is None:
            blank_nums = _lesson_nums(blank)
            if blank_nums:
                blank_min = min(blank_nums)
                continuations = []
                for k, t in named:
                    named_nums = _lesson_nums(t)
                    if named_nums and max(named_nums) < blank_min:
                        continuations.append((max(named_nums), t))
                if continuations:
                    target = max(continuations, key=lambda x: x[0])[1]
        if target is None and blank["page_start"] is not None:
            candidates = [t for k, t in named if t["page_start"] is not None
                          and t["page_start"] <= blank["page_start"]]
            if candidates:
                target = max(candidates, key=lambda t: t["page_start"])
        if target is not None:
            _fold_topic(target, blank)
            del topics[bkey]


def _drop_overlap(chunk):
    """Copy of `chunk` without what it extracted from its leading overlap pages, if the caller
    recorded its core page range as chunk["_core_pages"] = [first, last]. Overlap pages are sent
    as context only, but the model extracts them too -- and in a batch that opens a new topic,
    the previous lesson's last pages then get filed under the NEW topic (Grade 3 Module 1:
    Lesson 6 showed up under Topic C). The previous batch extracts those pages as its own core,
    so nothing is lost. Lessons/topics left with no outcomes by this are dropped; ones that never
    had outcomes (e.g. a TOC listing) are kept."""
    core = chunk.get("_core_pages")
    if not core:
        return chunk
    first, last = core

    def in_core(page):
        return page is None or first <= page <= last

    out = dict(chunk)
    topics = []
    for t in chunk.get("topics", []):
        lessons = []
        for l in t.get("lessons", []):
            outcomes = [o for o in l.get("learning_outcomes", []) if in_core(o.get("page"))]
            if l.get("learning_outcomes") and not outcomes:
                continue
            lessons.append({**l, "learning_outcomes": outcomes})
        if t.get("lessons") and not lessons:
            continue
        topics.append({**t, "lessons": lessons})
    out["topics"] = topics
    out["assessments"] = [a for a in chunk.get("assessments", []) if in_core(a.get("page"))]
    out["supplementary"] = [s for s in chunk.get("supplementary", []) if in_core(s.get("page"))]
    return out


def merge(chunks):
    """Fold per-batch extractions into one curriculum, deduping by key. Page numbers (`page`/
    `page_start`/`page_end`) are now real, Claude-echoed absolute page numbers -- no more
    separate pdf-index math needed, just first-non-empty/last-non-empty folding same as labels.
    A chunk carrying "_core_pages" has its overlap-page content dropped first (_drop_overlap)."""
    module = {"number": "", "title": "", "teks": set()}
    topics, standards, assessments = {}, {}, {}
    supplementary = []
    for chunk_index, c in enumerate(chunks):
        c = _drop_overlap(c)
        if c.get("module_number"):
            module["number"] = c["module_number"]
            module["title"] = module["title"] or c.get("module_title", "")
        module["teks"].update(c.get("module_teks", []))
        for t in c.get("topics", []):
            # A batch that starts mid-topic reports it with no number or title. Each such batch
            # gets its own key: sharing one "" key pooled every batch's headless lessons into a
            # single fake topic, which _reconcile_blank_topics() then attached as one unit.
            key = t["number"] or t["title"] or f"_blank_{chunk_index}"
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
                # the model sometimes echoes the topic itself as a content-less "lesson"
                # (Grade 3 Module 1: lesson "C" titled "Multiplication Using Units of 2 and 3")
                if not l.get("learning_outcomes") and (
                        (l.get("number") and l.get("number") == t.get("number"))
                        or (l.get("title") and l.get("title") == t.get("title"))):
                    continue
                lkey = l.get("number") or l.get("title") or "_default"
                curl = cur["lessons"].setdefault(lkey, {
                    "number": l.get("number", ""), "title": l.get("title", ""),
                    "teks": set(), "materials_needed": [], "outcomes": [],
                    "page_start_label": "", "page_end_label": "",
                    "page_start": None, "page_end": None})
                curl["title"] = curl["title"] or l.get("title", "")
                curl["teks"].update(l.get("teks", []))
                # dedup by (page, outcome text): with build_page_batches()'s chunk overlap, the
                # same page can legitimately be re-extracted by two neighboring batches -- without
                # this, a shared overlap page would produce two identical LearningComponent nodes.
                seen_outcome_keys = {(o.get("page"), o.get("outcome")) for o in curl["outcomes"]}
                new_outcomes = []
                for o in l.get("learning_outcomes", []):
                    key = (o.get("page"), o.get("outcome"))
                    if key not in seen_outcome_keys:
                        seen_outcome_keys.add(key)
                        new_outcomes.append(o)
                curl["outcomes"].extend(new_outcomes)
                cur["outcomes"].extend(new_outcomes)  # flat aggregate, display-only
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
    _reconcile_blank_topics(topics)
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
            # confidence/rationale: only present on inferred associations (see
            # infer_missing_crosswalk.py) -- carried through as edge properties so the agent can
            # tell a real, curated crosswalk edge apart from an AI-inferred candidate.
            extra = {k: a[k] for k in ("confidence", "rationale") if a.get(k)}
            parsed.append((a.get("associationType"), o, d, extra))
    return {"framework": {"uuid": doc.get("identifier"),
                          "title": doc.get("title") or "Standards Framework"},
            "by_uuid": by_uuid, "assocs": parsed,
            "code_to_uuid": code_to_uuid, "code_to_stmt": code_to_stmt}


# CASE association types -> our edge labels (isChildOf handled specially as hasChild)
#
# "isStandardAlignedTo_inferred" (AI-inferred TEKS<->CC crosswalk candidates from
# infer_missing_crosswalk.py) is deliberately NOT mapped here. Those 151 candidates were
# generated, loaded as possibleStandardAlignment edges, and then explicitly rejected/deleted by
# product decision (the user doesn't trust AI-inferred crosswalks) -- omitting the mapping is a
# structural guardrail so no future build can silently re-materialize that edge type as a graph
# edge, even if a stale copy of teks_math_case.json (or any other CASE source) ever contains
# these records again. If genuinely revisited, add the mapping back deliberately, not by default.
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
                scope="", supplementary=(), real_materials=None, real_pages=None,
                source_pdf_key=None):
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
    a way titles aren't: a topic's Pacing Guide (the one place "Materials needed:" is
    printed) sits on its own early page, while a lesson's own activity content runs many pages
    later — batched extraction sends those to separate, independent Claude calls, so whichever
    batch is extracting the lesson's activities often never sees the Pacing Guide line it's
    supposed to copy, and falls back to guessing from the activity content instead. Titles don't
    have this problem (a lesson's title appears on its own TOC page, in the same batch as its
    Pacing Guide), so they stay Claude-sourced.

    `real_pages` (optional): {pdf_page (int): text (str)}, this module's real page text from
    pdfium_page_text() -- NOT docling_page_text() (confirmed live: docling silently drops content
    on dense/tabular layouts, e.g. one Pacing Guide page returned 290 chars where the real page
    has 2004, no signal anything was missing -- not tolerable for a field promised verbatim).
    LearningComponents' `sourceText` is Claude's own regenerated transcription of the page, only
    ever *instructed* to be verbatim, not guaranteed -- confirmed unreliable (paraphrased,
    duplicated, or real content silently replaced by a generic label). When a page has exactly one
    LearningComponent on it, sourceText is overridden with the real page text instead of trusting
    Claude's copy. Pages shared by 2+ LearningComponents are left on Claude's text -- pdfium_page_
    text() only returns whole-page text, and there's no reliable way to split one page's text
    between several activities without guessing which words belong to which.

    `source_pdf_key` (optional, from citation.pdf_key_for_module()): this module's portable PDF
    citation key, stamped as `sourcePdfKey` on every LearningComponent/Assessment node so a
    consumer can build a real citation URL (citation.citation_url()) from it plus that node's own
    pdfPage -- no other lookup needed."""
    nodes, rels = [], []
    real_materials = real_materials or {}

    # Count how many LearningComponents will land on each PDF page, module-wide -- needed below
    # to decide whether a page's real text can safely override a single node's sourceText, or
    # whether the page is shared by siblings and must keep Claude's per-node text (pdfium_page_
    # text() only returns whole-page text, so a shared page has no reliable way to split it).
    _page_component_count = {}
    for t in topics.values():
        for l in t.get("lessons", {}).values():
            for outcome in l.get("outcomes", []):
                p = outcome.get("page")
                if p is not None:
                    _page_component_count[p] = _page_component_count.get(p, 0) + 1
    for sup in supplementary:
        p = sup.get("page")
        if p is not None:
            _page_component_count[p] = _page_component_count.get(p, 0) + 1

    def _resolve_source_text(page, claude_text):
        """Real page text (pdfium_page_text()) when this page has exactly one LearningComponent
        on it (unambiguous which text belongs to it) and it's covered; otherwise Claude's own
        text, unchanged -- never overridden with a blank."""
        if real_pages and page is not None and _page_component_count.get(page) == 1:
            return real_pages.get(page) or claude_text
        return claude_text

    def nid(kind, key):
        base = f"{scope}|{kind}|{key}" if scope else f"{kind}|{key}"
        return "yo:" + str(uuid.uuid5(NS, base))

    used_content_ids = set()

    def content_id(page, *texts):
        """Stable LearningComponent id from module + normalized text -- NOT the item's position
        in the extracted list (every re-extraction renumbers it) and NOT its page (a PDF update
        that inserts or removes pages shifts every later page: with the page in the key, a
        2-page insertion kept only 60 of 341 ids). Unchanged content keeps its id across
        re-extractions, re-batching, page shifts and repairs that move it to another lesson, so
        user edits, hand fixes and the version diff keep matching it. The same text appearing
        again in the module gets an occurrence suffix. `page` stays a node property."""
        digest = hashlib.sha1(" ".join(" ".join((t or "").lower().split())
                                        for t in texts).encode()).hexdigest()[:16]
        key, n = f"{module['number']}:{digest}", 1
        while key in used_content_ids:
            n += 1
            key = f"{module['number']}:{digest}:{n}"
        used_content_ids.add(key)
        return nid("LearningComponent", key)

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
        # readinessStandard is only ever stamped when true. Every course's build_graph() call
        # emits ALL standards in the shared framework as nodes (not just the ones it teaches), so
        # a standard genuinely marked readiness by one course's Pacing Guide (e.g. Algebra I's
        # A.3C) would otherwise get silently overwritten to false by a different course (e.g.
        # Grade 8) that also touches the same shared node but never saw it marked bold in ITS OWN
        # book. Omitting the key when false means Neo4j's `SET n += props` never erases a `true`
        # another course already set -- a positive, observed signal should never be clobbered by
        # another course's mere absence of evidence.
        props = dict(description=stmt or code or "", code=code,
                     jurisdiction=juris or jurisdiction, academicSubject="Mathematics",
                     caseIdentifierUUID=uid)
        if code in readiness:
            props["readinessStandard"] = "true"
        node(uid, "StandardsFrameworkItem", **props)

    # ── standards backbone ───────────────────────────────────────────────────
    if case:
        fw_id = case["framework"]["uuid"] or case_uuid("__root__")
        node(fw_id, "StandardsFramework", name=case["framework"]["title"],
             academicSubject="Mathematics", jurisdiction=jurisdiction, caseIdentifierUUID=fw_id)
        for uid, it in case["by_uuid"].items():          # full framework from the CASE file
            ensure_std(uid, it["code"], it["statement"], it.get("jurisdiction"))
        for atype, o, d, extra in case["assocs"]:         # hierarchy + cross-grade links
            if o not in emitted_std or d not in emitted_std:
                continue
            if atype == "isChildOf":
                rel("hasChild", d, "StandardsFrameworkItem", o, "StandardsFrameworkItem",
                    s_key="caseIdentifierUUID", t_key="caseIdentifierUUID")
            elif atype in _ASSOC_EDGE:
                rel(_ASSOC_EDGE[atype], o, "StandardsFrameworkItem", d, "StandardsFrameworkItem",
                    s_key="caseIdentifierUUID", t_key="caseIdentifierUUID", **extra)
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
         groupLevel="0", ordinalName=f"Module {module['number']}", position=module["number"],
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
                lc_id = content_id(outcome.get("page"), outcome.get("outcome", ""),
                                   outcome.get("source_text", ""))
                node(lc_id, "LearningComponent", description=outcome["outcome"],
                     sourceText=_resolve_source_text(outcome.get("page"),
                                                      outcome.get("source_text", "")),
                     componentRole=outcome.get("component_role", ""),
                     isCritical=bool(outcome.get("is_critical", False)),
                     lessonPhase=outcome.get("lesson_phase", ""),
                     pageLabel=outcome.get("page_label", ""), pdfPage=outcome.get("page"),
                     sourcePdfKey=source_pdf_key or "",
                     academicSubject="Mathematics")
                rel("hasPart", lesson_id, "Lesson", lc_id, "LearningComponent")
                for code in sorted(lesson_teks):
                    rel("supports", lc_id, "LearningComponent",
                        resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID")

    for (name, topic_no), a in assessments.items():
        asm_id = nid("Assessment", f"{module['number']}:{topic_no}:{name}")
        node(asm_id, "Assessment", name=name, academicSubject="Mathematics",
             curriculumLabel="Assessment",
             pageLabel=a.get("page_label", ""), pdfPage=a.get("page"),
             sourcePdfKey=source_pdf_key or "")
        # Same fallback as the supplementary loop below: a module-level assessment (e.g. "Module N
        # Assessment Summary") that Claude's extraction didn't tag with a specific topic_no would
        # otherwise get its node created but its hasPart edge silently skipped -- a real orphan
        # bug found live (7 such nodes across Grade 6/7, missing sourcePdfKey/citations because
        # nothing could resolve their parent module without this edge). Falling back to the
        # Module itself keeps every Assessment node reachable from the Course->Module hierarchy.
        if topic_no in topics:
            parent_id, parent_label = nid("Topic", f"{module['number']}:{topic_no}"), "LessonGrouping"
        else:
            parent_id, parent_label = mod_id, "LessonGrouping"
        rel("hasPart", parent_id, parent_label, asm_id, "Assessment")
        for code in sorted(a["teks"]):
            rel("hasEducationalAlignment", asm_id, "Assessment",
                resolve(code), "StandardsFrameworkItem", t_key="caseIdentifierUUID",
                alignmentType="assesses", curriculumAlignmentType="addressing")

    # Content that doesn't fit a lesson/learning-outcome/assessment (Assessment Summaries,
    # Pacing Guides, Self-Reflections, Overviews) -- reuses LearningComponent rather than a new
    # node type, so it gets embedded into Qdrant automatically with zero extra indexing code.
    _SUPP_ROLE = {"assessment_summary": "Assessment Summary", "pacing_guide": "Pacing Guide",
                  "self_reflection": "Self-Reflection", "overview": "Overview",
                  "discourse_questions": "Discourse Questions",
                  "differentiation_strategy": "Differentiation Strategy", "other": ""}
    for i, sup in enumerate(supplementary):
        supp_id = content_id(sup.get("page"), sup.get("type", ""), sup.get("text", ""))
        node(supp_id, "LearningComponent", description=sup.get("text", ""),
             sourceText=_resolve_source_text(sup.get("page"), sup.get("text", "")),
             componentRole=_SUPP_ROLE.get(sup.get("type", ""), ""),
             isCritical=False, lessonPhase="",
             pageLabel=sup.get("page_label", ""), pdfPage=sup.get("page"),
             sourcePdfKey=source_pdf_key or "",
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
    ap.add_argument("--pages-per-chunk", type=int, default=30,
                    help="Max pages per extraction call; calls end on lesson boundaries")
    ap.add_argument("--jurisdiction", default="Texas")
    ap.add_argument("--framework", default="TEKS")
    ap.add_argument("--case", help="path to a 1EdTech CASE framework JSON (optional)")
    ap.add_argument("--nodes-out", default="nodes.new.jsonl")
    ap.add_argument("--rels-out", default="relationships.new.jsonl")
    ap.add_argument("--extracted-out", default="curriculum_extracted.json")
    args = ap.parse_args()

    client = anthropic.Anthropic()
    print("Converting PDF via pypdfium2 (page-numbered text)...", flush=True)
    page_text = pdfium_page_text(args.pdf)
    print(f"  {len(page_text)} pages of text extracted", flush=True)
    chunks = []
    import lesson_pages
    _labels, lesson_of = lesson_pages.scan_pdf(args.pdf)
    for first, last, labeled_text in build_lesson_batches(page_text, lesson_of,
                                                          max_pages=args.pages_per_chunk):
        label = f"pages {first}-{last}"
        print(f"  extracting {label} ...", flush=True)
        try:
            chunk = extract_from_text(client, labeled_text, label)
            chunk["_core_pages"] = [first, last]  # see _drop_overlap()
            chunks.append(chunk)
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
