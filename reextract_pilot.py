#!/usr/bin/env python3
"""Pilot: re-extract a specific page range with smaller, real-time chunks to see the TRUE topic
segmentation, for comparison against what the original 25-page-batch pipeline produced. Not a
general tool -- validates the approach on Grade 3's confirmed "Division Using Units of 2 and 3"
misattribution case before deciding whether to scale to the other 20 suspicious topics."""
import sys

import anthropic
from dotenv import load_dotenv

import pdf_to_kg as p2k

load_dotenv()

PDF_PATH = ("/home/talha/Downloads/G3/M G3 Module 1: Properties of Multiplication and Division "
            "and Solving Problems with Units of 2-5 and 10/K-5 Math Grade 3 Module 1 Properties "
            "of Multiplication and Division and Solving Problems with Units of 2-5 and 10, "
            "Teacher Edition.pdf")
PAGE_START, PAGE_END = 23, 254
CHUNK_SIZE = 20

client = anthropic.Anthropic()

print("Reading PDF pages via pypdfium2...", file=sys.stderr)
page_text = p2k.pdfium_page_text(PDF_PATH)
pages_in_range = {p: t for p, t in page_text.items() if PAGE_START <= p <= PAGE_END}
print(f"  {len(pages_in_range)} pages in range {PAGE_START}-{PAGE_END}", file=sys.stderr)

chunks = []
sorted_pages = sorted(pages_in_range)
for i in range(0, len(sorted_pages), CHUNK_SIZE):
    batch_pages = sorted_pages[i:i + CHUNK_SIZE]
    labeled_text = "\n\n".join(f"=== PDF PAGE {p} ===\n{pages_in_range[p]}" for p in batch_pages)
    label = f"pages {batch_pages[0]}-{batch_pages[-1]}"
    print(f"  extracting {label}...", file=sys.stderr)
    chunk = p2k.extract_from_text(client, labeled_text, label)
    chunks.append(chunk)

print("\nMerging chunks...", file=sys.stderr)
module, topics, standards, assessments, supplementary = p2k.merge(chunks)

print(f"\n=== Re-extracted topic segmentation for pages {PAGE_START}-{PAGE_END} ===")
for key, t in sorted(topics.items(), key=lambda kv: kv[1].get("page_start") or 0):
    lesson_nums = sorted(l.get("number") for l in t["lessons"].values() if l.get("number"))
    print(f"  Topic {t['number']!r} {t['title']!r}: pages {t.get('page_start')}-{t.get('page_end')}, "
          f"lessons {lesson_nums}")
