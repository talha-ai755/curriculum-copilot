#!/usr/bin/env python3
"""
lesson_pages.py — every Lesson's real page range, read off the running header printed on each of
its pages, plus each page's printed label -- one pypdfium2 pass per module PDF.

Why: a Lesson's range used to be rolled up from its LearningComponents (backfill_page_rollups.py),
which ends the lesson at its last page that happens to carry one. Lessons' Sprint/Problem Set/
Exit Ticket/Homework/Template pages usually carry none, so the rollup cut them short -- scored
against the headers below on 1,030 lessons, only 11.8% got both start and end right (Grade 1
"Analyze and describe embedded numbers": real 28-40, rollup 28-32). The extraction's own ranges
weren't much better (46.7%): Grade 6-8/Algebra I lesson starts often land on the topic's Pacing
Guide pages instead. Every lesson page names its lesson in the running header, so read that:

  K-5:          "BLUEBONNET LEARNING K–5 MATH Lesson 1 Homework 1 • 1"  (numbered per module)
  6-8, Alg I:   "MODULE 1 • TOPIC 1 • LESSON 1"                         (numbered per topic)

The back-of-book Answer Key repeats each lesson's header (Grade 1 Module 1: Lesson 1 again at
pages 487-488), so Answer Key pages are skipped, and a lesson's range is its longest run of
tagged pages -- see lesson_ranges().
"""
import bisect
import functools
import re

import pypdfium2 as pdfium

from backfill_page_labels import extract_label

_K5_HEADER = re.compile(r"BLUEBONNET LEARNING K.5 MATH\s+Lesson\s+(\d+)\b")
# Text order varies page to page, so the K-5 header often comes out without its "Lesson N"
# ("BLUEBONNET LEARNING K–5 MATH 1 • 2") and the lesson is named instead on its own
# "Lesson 14: 169" / "Lesson 12: Solve word problems..." running line. Only searched in the
# header/footer zones (EDGE chars), never mid-page body text.
_K5_PAGE = re.compile(r"BLUEBONNET LEARNING K.5 MATH")
_K5_LINE = re.compile(r"(?:^|\n)\s*(?:\S{1,6}\s+)?Lesson\s+(\d+):")  # "21 Lesson 1: ..." too
# A lesson's opening page: a bare "Lesson 10" line, then "Objective: ...".
_K5_OPENER = re.compile(r"(?:^|\n)\s*(?:\S{1,6}\s+)?Lesson\s+(\d+)\s*\n")
_OBJECTIVE = re.compile(r"\bObjective\s*:")
EDGE = 300
_K5_PAGE_NUMBER = [re.compile(r"(?:^|\n)\s*(\d{1,3})\s+(?:Lesson|Topic|Module)\s+\w+"),   # "25 Lesson 1:"
                   re.compile(r"(?:Lesson|Topic)\s+\w+:\s*(\d{1,3})\s*(?:\n|$)")]        # "Lesson 14: 169"
# Some Grade 6 pages extract the header doubled and interleaved:
# "MODULE 1  MODULE 1•TOPIC 1  TOPIC 1•LESSON 1  LESSON 1"
_MS_HEADER = re.compile(r"MODULE\s*\d+(?:\W+MODULE\s*\d+)?\W{1,6}TOPIC\s*(\d+)(?:\W+TOPIC\s*\d+)?"
                        r"\W{1,6}LESSON\s*(\d+)\b", re.IGNORECASE)
_ANSWER_KEY = re.compile(r"\bAnswer Key\b", re.IGNORECASE)
MAX_GAP = 8


def _norm_num(value):
    """'01' -> '1', '' -> '' -- graph positions and printed headers pad differently."""
    s = str(value or "").strip()
    return s.lstrip("0") or ("0" if s else "")


@functools.lru_cache(maxsize=None)
def scan_pdf(pdf_path):
    """({pdfPage: printed label}, {pdfPage: (topic, lesson)}) for one module PDF. `topic` is ""
    for K-5 headers, which number lessons per module rather than per topic. Cached per process:
    the build calls this before and after its structural repairs. Callers must not mutate the
    returned dicts."""
    labels, lesson_of = {}, {}
    k5_pages, offset_votes = [], {}
    doc = pdfium.PdfDocument(pdf_path)
    try:
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                txt = (textpage.get_text_range() or "").replace("\r", "")
            finally:
                textpage.close()
                page.close()
            head, tail = txt[:500], txt[-1000:]
            label = extract_label(head) or extract_label(tail)
            if label:
                labels[i + 1] = label
            if _K5_PAGE.search(txt):
                k5_pages.append(i + 1)
                for rx in _K5_PAGE_NUMBER:
                    for zone in (txt[:EDGE], txt[-EDGE:]):
                        for n in rx.findall(zone):
                            off = i + 1 - int(n)
                            offset_votes[off] = offset_votes.get(off, 0) + 1
            if _ANSWER_KEY.search(txt[:EDGE]) or _ANSWER_KEY.search(txt[-EDGE:]):
                continue
            if _K5_PAGE.search(txt):
                # The "Lesson N:" running line wins over the header word: a lesson can print the
                # NEXT lesson's Sprint, headed "Lesson 20 Sprint" but running "Lesson 19: ..."
                # (Grade 2 Module 3 page 272) -- the running line names the containing lesson.
                m = _K5_LINE.search(txt[:EDGE]) or _K5_LINE.search(txt[-EDGE:])
                if not m and _OBJECTIVE.search(txt[:EDGE + 200]):
                    m = _K5_OPENER.search(txt[:EDGE])
                m = m or _K5_HEADER.search(txt[:EDGE]) or _K5_HEADER.search(txt[-EDGE:])
                if m:
                    lesson_of[i + 1] = ("", _norm_num(m.group(1)))
                continue
            m = _MS_HEADER.search(head[:200]) or _MS_HEADER.search(tail[-300:])
            if m:
                lesson_of[i + 1] = (_norm_num(m.group(1)), _norm_num(m.group(2)))
    finally:
        doc.close()
    # K-5 printed page numbers: extract_label() only knows the 6-8 footer ("<label> MODULE N"),
    # so it finds a label on few K-5 pages. Their number sits next to the running line instead
    # ("25 Lesson 1:", "Lesson 14: 169") and is the PDF page minus a fixed per-module offset
    # (3 in every K-5 module checked: 94-100% of votes; Claude's own extracted labels agree
    # ~99%). Fill unlabelled K-5 pages only when the module's votes clearly agree.
    total = sum(offset_votes.values())
    if total >= 10:  # short modules have few running lines (Grade 2 Module 1: 19 votes, all agreeing)
        off, n = max(offset_votes.items(), key=lambda kv: kv[1])
        if n / total >= 0.9:
            for p in k5_pages:
                if p not in labels and p - off >= 1:
                    labels[p] = str(p - off)
    return labels, lesson_of


def lesson_ranges(lesson_of):
    """{(topic, lesson): (first_page, last_page)} -- each lesson's LONGEST run of tagged pages
    (earliest on a tie). A run continues across up to MAX_GAP untagged pages -- facsimile
    student pages carry no running header at all (Grade 6 Module 1 Lesson 1: pages 42-45) -- as
    long as no OTHER lesson's page lies in between. Not the first run: a module overview's
    "Lesson 1: <title>" line tags a single front-matter page long before the lesson itself
    (confirmed: Grade 1 Module 1 overview pages 11-17)."""
    pages = {}
    for page, key in lesson_of.items():
        pages.setdefault(key, []).append(page)
    tagged = sorted(lesson_of)
    ranges = {}
    for key, ps in pages.items():
        ps.sort()
        runs = [[ps[0], ps[0], 1]]
        for p in ps[1:]:
            hi = runs[-1][1]
            other_between = any(hi < q < p and lesson_of[q] != key
                                for q in tagged[bisect.bisect_right(tagged, hi):
                                                bisect.bisect_left(tagged, p)])
            if p - hi > MAX_GAP + 1 or other_between:
                runs.append([p, p, 1])
            else:
                runs[-1][1] = p
                runs[-1][2] += 1
        lo, hi, _ = max(runs, key=lambda r: (r[2], -r[0]))
        ranges[key] = (lo, hi)
    return ranges


_K5_TOPIC_OPENER = re.compile(r"(?:^|\n)\s*(?:\S{1,6}\s+)?Topic\s+([A-H])\b")


def topic_of_pages(page_text, lesson_of):
    """{pdfPage: topic} -- the topic each page belongs to, carried forward from the last topic
    marker at or before it: the topic number in a 6-8/Algebra I running header, or a K-5 topic
    opener page ("21 Topic A: ..." / "Topic A 3 • 1" at the top). Pages before the first marker
    are absent."""
    topic_of, current = {}, None
    for p in sorted(page_text):
        if p in lesson_of and lesson_of[p][0]:
            current = lesson_of[p][0]
        else:
            m = _K5_TOPIC_OPENER.search((page_text[p] or "").replace("\r", "")[:EDGE])
            if m and _K5_PAGE.search(page_text[p] or ""):
                current = m.group(1)
        if current is not None:
            topic_of[p] = current
    return topic_of


def lookup(ranges, topic_position, lesson_position):
    """A graph Lesson's range: by (topic, lesson) for per-topic numbering, else by lesson alone
    for K-5's per-module numbering. None if the PDF has no header for it."""
    lesson = _norm_num(lesson_position)
    if not lesson:
        return None
    return ranges.get((_norm_num(topic_position), lesson)) or ranges.get(("", lesson))
