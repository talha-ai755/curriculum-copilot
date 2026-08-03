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
"""
import re

import pypdf

# dot-leaders vary by page: TOC-style lesson lines use literal periods ("LESSON 1  .  .  .  5"),
# but the module's own title page renders its leader as a repeated PDF glyph pypdf decodes as the
# Unicode replacement character.
_LEADER = r"(?:[.�]\s*){4,}"

_MODULE_TITLE = re.compile(r"MODULE\s*\n\s*\d+\s*\n([\s\S]+?)\nTOPIC\s+1\b")
_MODULE_TOPIC_LINE = re.compile(
    r"TOPIC\s+(\d+)[\s  ]+([\s\S]+?)\s*" + _LEADER + r"\s*(\d+)")
_LESSON_LINE = re.compile(r"LESSON\s+(\d+)\s+([\s\S]+?)\s*" + _LEADER + r"\s*(\d+)")
_TOPIC_TOC_HEADER = re.compile(r"TOPIC\s+(\d+)\s*[••]\s*INTRODUCTION", re.IGNORECASE)

_TOPIC_HEADER = re.compile(
    r"TOPIC\s+(\d+)[^\n]{0,40}PACING GUIDE|PACING GUIDE[^\n]{0,40}TOPIC\s+(\d+)",
    re.IGNORECASE)
_MATERIALS_LINE = re.compile(r"Materials [Nn]eeded:\s*([\s\S]+?)(?:\n[A-Z0-9]+\.\d)")


def _clean(title):
    return " ".join(title.split())


def find_real_titles(pdf_path):
    """Returns {"module_title": str, "topics": {topic_number: {"title": str,
    "lessons": {lesson_number: title}}}} parsed straight from the PDF's own TOC pages.
    `topics` is also the authoritative real-topic list -- anything not a key here isn't real."""
    r = pypdf.PdfReader(pdf_path)
    module_title = ""
    topics = {}

    # page 1 (module's own title page): module title + the real list of topics
    first_page_text = r.pages[0].extract_text() or ""
    m = _MODULE_TITLE.search(first_page_text)
    if m:
        module_title = _clean(m.group(1))
    for tnum, ttitle, _page in _MODULE_TOPIC_LINE.findall(first_page_text):
        topics[tnum] = {"title": _clean(ttitle), "lessons": {}}

    # each Topic's own divider/TOC page: real lesson titles, in order
    for page in r.pages:
        txt = page.extract_text() or ""
        header = _TOPIC_TOC_HEADER.search(txt)
        lessons = _LESSON_LINE.findall(txt)
        if not header or len(lessons) < 1:
            continue
        tnum = header.group(1)
        if tnum not in topics:
            continue
        for lnum, ltitle, _page in lessons:
            topics[tnum]["lessons"][lnum] = _clean(ltitle)

    return {"module_title": module_title, "topics": topics}


def find_real_materials(pdf_path):
    """Returns {topic_number: [materials_list, ...]} -- one entry per lesson, in the order the
    Pacing Guide lists them (Pacing Guides are strictly lesson-ordered, so position = lesson
    order within that topic)."""
    r = pypdf.PdfReader(pdf_path)
    from collections import defaultdict
    topic_pages = defaultdict(list)
    for page in r.pages:
        txt = page.extract_text() or ""
        if "PACING GUIDE" not in txt.upper():
            continue
        m = _TOPIC_HEADER.search(txt)
        if m:
            tnum = m.group(1) or m.group(2)
            topic_pages[tnum].append(txt)

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
