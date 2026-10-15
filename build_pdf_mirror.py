#!/usr/bin/env python3
"""
build_pdf_mirror.py — local stand-in for the (not-yet-set-up) S3/CloudFront PDF hosting.

Symlinks every discovered course's real module PDFs into data/pdf_mirror/<key>, where <key> is
whatever citation.pdf_key_for_module() currently produces (e.g. "bluebonnet/grade6/<basename>")
-- so this always mirrors the exact same shape stamped onto every node as `sourcePdfKey`, with no
separate layout to keep in sync by hand. Serve that directory with a plain static file server and
every citation link genuinely opens the right PDF at the right page, no different from what
CloudFront does against the real bucket -- only PDF_BASE_URL (env var) changes, not this directory
layout or any stored data.

Symlinks, not copies: these PDFs are large and this machine already has them; no need to
duplicate gigabytes on disk just to serve them locally.

Usage:
  python3 build_pdf_mirror.py
  python3 -m http.server 8899 --directory data/pdf_mirror   # separate terminal, keep it running
  # then set PDF_BASE_URL=http://localhost:8899 in .env
"""
import os
import sys

import citation
from backfill_page_labels import discover_courses

MIRROR_DIR = os.path.join(os.path.dirname(__file__), "data", "pdf_mirror")


def main():
    courses = discover_courses()
    if not courses:
        sys.exit("No courses discovered -- nothing to mirror.")

    total = 0
    for nodes_path, module_pdfs in courses.items():
        course_slug = os.path.basename(nodes_path).replace("_nodes.jsonl", "")
        print(f"=== {course_slug} ===", file=sys.stderr)
        for module_name, pdf_path in module_pdfs.items():
            if not os.path.exists(pdf_path):
                print(f"  SKIP {module_name}: source PDF not found at {pdf_path!r}", file=sys.stderr)
                continue
            key = citation.pdf_key_for_module(course_slug, pdf_path)
            link_path = os.path.join(MIRROR_DIR, key)
            # Directory derived from the key itself (not a separately hardcoded course_slug path)
            # so this stays correct if pdf_key_for_module()'s format ever adds/changes a prefix
            # again (it already has once this session) -- no second place to remember to update.
            os.makedirs(os.path.dirname(link_path), exist_ok=True)
            if os.path.islink(link_path) or os.path.exists(link_path):
                os.remove(link_path)
            os.symlink(os.path.abspath(pdf_path), link_path)
            print(f"  {module_name} -> {key}", file=sys.stderr)
            total += 1

    print(f"\nMirrored {total} PDF(s) into {MIRROR_DIR}/", file=sys.stderr)
    print("Serve it with: python3 -m http.server 8899 --directory data/pdf_mirror", file=sys.stderr)
    print("Then set PDF_BASE_URL=http://localhost:8899 in .env", file=sys.stderr)


if __name__ == "__main__":
    main()
