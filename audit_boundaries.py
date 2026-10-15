#!/usr/bin/env python3
"""One-off audit script (not part of the pipeline) -- compares stored pageStartLabel/pageEndLabel
on Module/Topic LessonGrouping nodes against printed labels read directly off the PDFs, using the
same extraction method as backfill_page_labels.py. Read-only: writes nothing back to the JSONL.
"""
import glob
import json
import os
import re
import sys

import pypdfium2 as pdfium

COURSES = {
    "grade6": "data/grade6_nodes.jsonl",
    "grade7": "data/grade7_nodes.jsonl",
    "grade8": "data/grade8_nodes.jsonl",
    "algebra1": "data/algebra1_nodes.jsonl",
}
PDF_DIR = "data/pdf_mirror/bluebonnet/{course}"


def _page_text(page):
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range() or ""
    finally:
        textpage.close()


_LABEL_FIRST = re.compile(r"(?:^|\n)\s*(\S{1,7})\s+MODULE\s+\d+", re.IGNORECASE)
_LABEL_LAST = re.compile(r"MODULE\s+\d+[^\n]*?[•·][^\n]*?[•·][^\n]*?\s(\S{1,7})\s*(?:\n|\Z)", re.IGNORECASE)
_LABEL_LAST_SIMPLE = re.compile(r"MODULE\s+\d+[^\n]{0,80}?\s(\S{1,7})\s*(?:\n|\Z)", re.IGNORECASE)
_VALID_LABEL = re.compile(r"^(\d[\dA-Z]{0,4}|IL-\d[A-Z]?)$")
_HEADER_MOD_TOPIC = re.compile(r"MODULE\s+(\d+)\s*[•·]\s*TOPIC\s+(\d+)", re.IGNORECASE)
_TOPIC_SUMMARY = re.compile(r"TOPIC\s+SUMMARY", re.IGNORECASE)
_TOPIC_INTRO = re.compile(r"\bINTRODUCTION\b", re.IGNORECASE)


def extract_label(block):
    for pattern in (_LABEL_FIRST, _LABEL_LAST, _LABEL_LAST_SIMPLE):
        m = pattern.search(block)
        if m and _VALID_LABEL.match(m.group(1).strip()):
            return m.group(1).strip()
    return None


def read_module_pdf(pdf_path):
    """Returns list of dicts per page: {pdf_page, label, module, topic, is_summary, is_intro, raw_head}"""
    pages = []
    doc = pdfium.PdfDocument(pdf_path)
    try:
        for i in range(len(doc)):
            page = doc[i]
            txt = _page_text(page)
            page.close()
            head = txt[:500]
            tail = txt[-1000:]
            label = extract_label(head) or extract_label(tail)
            mt = _HEADER_MOD_TOPIC.search(head) or _HEADER_MOD_TOPIC.search(tail)
            pages.append({
                "pdf_page": i + 1,
                "label": label,
                "module": int(mt.group(1)) if mt else None,
                "topic": int(mt.group(2)) if mt else None,
                "is_summary": bool(_TOPIC_SUMMARY.search(head) or _TOPIC_SUMMARY.search(tail)),
                "is_intro": bool(_TOPIC_INTRO.search(head[:200])),
                "raw_head": head[:120].replace("\n", "\\n"),
            })
    finally:
        doc.close()
    return pages


def discover_module_pdfs(course):
    d = PDF_DIR.format(course=course)
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*.pdf"))):
        m = re.search(r"Module\s+(\d+)", os.path.basename(p), re.IGNORECASE)
        if m:
            out[int(m.group(1))] = p
    return out


def load_course(nodes_path):
    with open(nodes_path) as f:
        nodes = [json.loads(l) for l in f]
    rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
    with open(rels_path) as f:
        rels = [json.loads(l) for l in f]
    return nodes, rels


def main():
    for course, nodes_path in COURSES.items():
        print(f"\n{'='*80}\n{course}\n{'='*80}")
        nodes, rels = load_course(nodes_path)
        modules = [n for n in nodes if n["labels"][0] == "LessonGrouping" and n["properties"].get("groupLevel") == "0"]
        topics = [n for n in nodes if n["labels"][0] == "LessonGrouping" and n["properties"].get("groupLevel") == "1"]
        lessons = [n for n in nodes if n["labels"][0] == "Lesson"]

        module_pdfs = discover_module_pdfs(course)
        print(f"modules in graph: {len(modules)}, topics: {len(topics)}, lessons: {len(lessons)}, pdfs found: {sorted(module_pdfs)}")

        # map topic node -> its module number, via hasPart parent walk
        by_id = {n["identifier"]: n for n in nodes}
        parent_of = {r["target_identifier"]: r["source_identifier"] for r in rels if r["label"] == "hasPart"}

        def module_num_of(identifier):
            cur, seen = identifier, set()
            while cur in parent_of and cur not in seen:
                seen.add(cur)
                cur = parent_of[cur]
                n = by_id.get(cur)
                if n and n["labels"][0] == "LessonGrouping" and n["properties"].get("groupLevel") == "0":
                    om = n["properties"].get("ordinalName", "")
                    m = re.search(r"\d+", om)
                    return int(m.group()) if m else None
            return None

        # Check Lesson 0 presence per course
        lesson0 = [l for l in lessons if str(l["properties"].get("position")) in ("00", "0")]
        print(f"Lesson 0 orientation node present: {'YES' if lesson0 else 'NO'}"
              + (f" ({lesson0[0]['properties'].get('name')})" if lesson0 else ""))

        for mod_num, pdf_path in sorted(module_pdfs.items()):
            print(f"\n  --- Module {mod_num}: {os.path.basename(pdf_path)} ---")
            pages = read_module_pdf(pdf_path)
            # true topic extents: group pages by (module, topic) header, find min/max pdf_page
            # and check for INTRODUCTION / TOPIC SUMMARY pages just outside the "A"-suffixed range
            by_topic = {}
            for p in pages:
                if p["module"] == mod_num and p["topic"]:
                    by_topic.setdefault(p["topic"], []).append(p)

            topics_this_module = [t for t in topics if module_num_of(t["identifier"]) == mod_num]
            for t in sorted(topics_this_module, key=lambda x: x["properties"].get("ordinalName", "")):
                tp = t["properties"]
                on = tp.get("ordinalName", "?")
                topic_num_m = re.search(r"\d+", on)
                topic_num = int(topic_num_m.group()) if topic_num_m else None
                stored_start_label = tp.get("pageStartLabel")
                stored_end_label = tp.get("pageEndLabel")
                stored_start_pdf = tp.get("pdfPageStart")
                stored_end_pdf = tp.get("pdfPageEnd")

                tpages = by_topic.get(topic_num, [])
                if not tpages:
                    print(f"    {on} ({tp.get('name')}): NO PDF PAGES MATCHED HEADER 'MODULE {mod_num} • TOPIC {topic_num}' -- cannot verify")
                    continue
                true_min_pdf = min(p["pdf_page"] for p in tpages)
                true_max_pdf = max(p["pdf_page"] for p in tpages)
                true_start_label = next((p["label"] for p in tpages if p["pdf_page"] == true_min_pdf), None)
                true_end_label = next((p["label"] for p in tpages if p["pdf_page"] == true_max_pdf), None)

                start_ok = stored_start_pdf == true_min_pdf
                end_ok = stored_end_pdf == true_max_pdf
                flag = []
                if not start_ok:
                    flag.append(f"START stored_pdfPage={stored_start_pdf}({stored_start_label}) true_pdfPage={true_min_pdf}({true_start_label})")
                if not end_ok:
                    flag.append(f"END stored_pdfPage={stored_end_pdf}({stored_end_label}) true_pdfPage={true_max_pdf}({true_end_label})")
                status = "OK" if (start_ok and end_ok) else " | ".join(flag)
                print(f"    {on} ({tp.get('name')}): {status}")


if __name__ == "__main__":
    main()
