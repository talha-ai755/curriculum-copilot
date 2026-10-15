#!/usr/bin/env python3
"""
citation.py — turn a node's (sourcePdfKey, pdfPage) into a clickable citation URL.

Graph/Qdrant only ever store the PORTABLE half: `sourcePdfKey`, a relative path like
"bluebonnet/grade8/Eighth Grade Math Teacher Edition, Volume 1 Module 1.pdf" -- never a full URL.
The full URL is built here, at read time, by prepending PDF_BASE_URL (an env var) -- so pointing
this at the real CloudFront distribution later (instead of a local dev server) is a one-line env
change, not a backfill across every already-ingested node. Same pattern as NEO4J_URI/QDRANT_HOST.

The "bluebonnet/" prefix matches the real S3 bucket layout devops actually used
(s3://sibme-curriculums/bluebonnet/<course>/<file>.pdf, confirmed live against the CloudFront
distribution) -- not a guess; this key format needs to keep matching the bucket's real prefix if
that ever changes again (it already changed once this session, from a bare "grade8/..." key
before any real bucket existed, to this).

Local dev: run build_pdf_mirror.py once (symlinks every course's real PDFs into
data/pdf_mirror/bluebonnet/<course_slug>/<basename>.pdf), serve it with
`python3 -m http.server 8899 --directory data/pdf_mirror`, and set
PDF_BASE_URL=http://localhost:8899 in .env. Production: point PDF_BASE_URL at the CloudFront
domain -- no other change needed, same key convention either way.
"""
import os
import urllib.parse


def pdf_key_for_module(course_slug, pdf_path):
    """The portable citation key for one module's PDF -- "bluebonnet/<course_slug>/<basename>.pdf"
    (see module docstring for why the "bluebonnet/" prefix). Stamped onto every LearningComponent/
    Assessment node built from that module."""
    return f"bluebonnet/{course_slug}/{os.path.basename(pdf_path)}"


def citation_url(source_pdf_key, pdf_page, base_url=None):
    """Full clickable URL for one node's page, or None if there's nothing to build it from
    (no key stamped on this node -- e.g. it predates the citation backfill -- or PDF_BASE_URL
    isn't configured). `#page=N` is the standard PDF-open-at-page fragment, honored by browser
    PDF viewers and most PDF.js-based viewers alike."""
    base_url = base_url if base_url is not None else os.environ.get("PDF_BASE_URL", "")
    if not base_url or not source_pdf_key:
        return None
    encoded_key = urllib.parse.quote(source_pdf_key)
    url = f"{base_url.rstrip('/')}/{encoded_key}"
    if pdf_page is not None:
        url += f"#page={pdf_page}"
    return url
