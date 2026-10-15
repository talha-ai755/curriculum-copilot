#!/usr/bin/env python3
"""
real_content.py — deterministic, regex-only extraction of module/topic/lesson titles and
per-lesson materials, straight from the source PDFs. No Claude involved: Claude's `title`/
`materials_needed` fields have been confirmed (this session) to sometimes paraphrase or fabricate,
while these values are formulaic, printed text (a table of contents / pacing guide), so a plain
parse is both cheaper and more reliable.

find_real_titles() also doubles as the authoritative list of which topics actually exist in a
module (from the module's own title-page TOC) -- any Topic build_graph() would otherwise build
that isn't in this list gets dropped as not real.

Uses pypdfium2 (already a project dependency, for Page Inspector's rendering), not pypdf/
pdfplumber: both were confirmed live to fail on Grade 6 Module 1 when reading many pages
sequentially from one session -- pypdf.PdfReader.extract_text() hung indefinitely (waited 5
minutes, never returned), and pdfplumber's per-page state grew unboundedly and got the process
OOM-killed at ~19.5GB RSS around page 300. Both are pure-Python PDF parsers (pdfplumber is built
on pdfminer.six); pypdfium2 wraps Google's PDFium (Chrome's production PDF engine) instead, and
extracts the SAME 365-page module's text in ~11s with ~220MB peak RSS -- no hang, no leak, ~40x
faster than even the memory-safe pdfplumber workaround (fresh reader per page, ~15min/module).
Text comes back with \\r\\n line endings rather than pypdf's \\n -- the regex patterns below all
consume it through _clean() (str.split() splits on any whitespace) before final use, so this
doesn't need special-casing.
"""
import re

import pypdfium2 as pdfium

# dot-leaders vary by page: TOC-style lesson lines use literal periods ("LESSON 1  .  .  .  5"),
# but the module's own title page renders its leader as a repeated PDF glyph decoded as the
# Unicode replacement character.
_LEADER = r"(?:[.�]\s*){4,}"

_MODULE_TITLE = re.compile(r"MODULE\s*\n\s*\d+\s*\n([\s\S]+?)\nTOPIC\s+1\b")
_MODULE_TOPIC_LINE = re.compile(
    r"TOPIC\s+(\d+)[\s  ]+([\s\S]+?)\s*" + _LEADER + r"\s*(\d+)")
_LESSON_LINE = re.compile(r"LESSON\s+(\d+)\s+([\s\S]+?)\s*" + _LEADER + r"\s*(\d+)")
_TOPIC_TOC_HEADER = re.compile(r"TOPIC\s+(\d+)\s*[••]\s*INTRODUCTION", re.IGNORECASE)

_TOPIC_HEADER = re.compile(
    r"TOPIC\s+(\d+)[^\n]{0,40}PACING GUIDE|PACING GUIDE[^\n]{0,40}TOPIC\s+(\d+)",
    re.IGNORECASE)
_MATERIALS_LINE = re.compile(r"Materials [Nn]eeded:\s*([\s\S]+?)(?:\n[A-Z0-9]+\.\d)")


def _clean(title):
    return " ".join(title.split())


def _page_text(page):
    """One page's text via pypdfium2's textpage API -- closed immediately after, same pattern
    Page_Inspector.py already uses for rendering (open, use, close), so no long-lived textpage
    handles accumulate across a multi-hundred-page document."""
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range() or ""
    finally:
        textpage.close()


def find_real_titles(pdf_path):
    """Returns {"module_title": str, "topics": {topic_number: {"title": str,
    "lessons": {lesson_number: title}}}} parsed straight from the PDF's own TOC pages.
    `topics` is also the authoritative real-topic list -- anything not a key here isn't real."""
    module_title = ""
    topics = {}

    doc = pdfium.PdfDocument(pdf_path)
    try:
        # page 1 (module's own title page): module title + the real list of topics
        page = doc[0]
        first_page_text = _page_text(page)
        page.close()
        m = _MODULE_TITLE.search(first_page_text)
        if m:
            module_title = _clean(m.group(1))
        for tnum, ttitle, _page in _MODULE_TOPIC_LINE.findall(first_page_text):
            topics[tnum] = {"title": _clean(ttitle), "lessons": {}}

        # each Topic's own divider/TOC page: real lesson titles, in order
        for i in range(len(doc)):
            page = doc[i]
            txt = _page_text(page)
            page.close()
            header = _TOPIC_TOC_HEADER.search(txt)
            lessons = _LESSON_LINE.findall(txt)
            if not header or len(lessons) < 1:
                continue
            tnum = header.group(1)
            if tnum not in topics:
                continue
            for lnum, ltitle, _page in lessons:
                topics[tnum]["lessons"][lnum] = _clean(ltitle)
    finally:
        doc.close()

    return {"module_title": module_title, "topics": topics}


def find_real_materials(pdf_path):
    """Returns {topic_number: [materials_list, ...]} -- one entry per lesson, in the order the
    Pacing Guide lists them (Pacing Guides are strictly lesson-ordered, so position = lesson
    order within that topic)."""
    from collections import defaultdict
    topic_pages = defaultdict(list)
    doc = pdfium.PdfDocument(pdf_path)
    try:
        for i in range(len(doc)):
            page = doc[i]
            txt = _page_text(page)
            page.close()
            if "PACING GUIDE" not in txt.upper():
                continue
            m = _TOPIC_HEADER.search(txt)
            if m:
                tnum = m.group(1) or m.group(2)
                topic_pages[tnum].append(txt)
    finally:
        doc.close()

    results = {}
    for tnum, pages in topic_pages.items():
        combined = "\n".join(pages)
        materials_lists = []
        pos = 0
        while True:
            m = _MATERIALS_LINE.search(combined, pos)
            if not m:
                break
            cleaned = " ".join(m.group(1).split()).rstrip(",").strip()
            materials = [] if cleaned.lower() == "none" else \
                [x.strip() for x in cleaned.split(",") if x.strip()]
            materials_lists.append(materials)
            pos = m.end()
        results[tnum] = materials_lists

    return results


if __name__ == "__main__":
    import json
    import sys
    path = sys.argv[1]
    titles = find_real_titles(path)
    materials = find_real_materials(path)
    print(json.dumps({"titles": titles, "materials": materials}, indent=1))
