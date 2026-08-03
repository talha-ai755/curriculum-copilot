#!/usr/bin/env python3
"""
build_raw_text_index.py — one-time build of a full-text (literal, verbatim) Qdrant index over
the 5 Algebra I module PDFs, using docling for PDF -> structured text with page provenance, and
OpenAI (text-embedding-3-small) for the embeddings.

This is SEPARATE from semantic_index.py's curriculum_content collection (which indexes Claude's
extracted, structured LearningComponent/Assessment/StandardsFrameworkItem text via FastEmbed) --
mixing raw full-text chunks into that collection was found to dilute fuzzy-search quality for
both, and the embedding models differ anyway. Run this script once (not on every Streamlit start
-- docling conversion is too slow to redo per launch); the resulting on-disk Qdrant collection
then sits alongside curriculum_content and app.py's load_course() just opens it, same client, two
collections (each queried with the embedding model that matches how it was built).

Needs OPENAI_API_KEY set in the environment (source your own key file; never printed/echoed here).

Usage: python3 build_raw_text_index.py
"""
import os
import sys
import time

from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from openai import OpenAI

import semantic_index as si

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("OPENAI_API_KEY not set")

PDF_DIR = ("/home/talha/Downloads/SM Algebra I: Teacher Editions/"
           "Teacher Edition By Module/")
MODULES = [
    ("Module 1", PDF_DIR + "Algebra I Math Teacher Edition, Volume 1 Module 1.pdf"),
    ("Module 2", PDF_DIR + "Algebra I Math Teacher Edition, Volume 1 Module 2.pdf"),
    ("Module 3", PDF_DIR + "Algebra I Math Teacher Edition, Volume 1 Module 3.pdf"),
    ("Module 4", PDF_DIR + "Algebra I Math Teacher Edition, Volume 2 Module 4.pdf"),
    ("Module 5", PDF_DIR + "Algebra I Math Teacher Edition, Volume 2 Module 5.pdf"),
]


def chunk_module(converter, chunker, module_name, path):
    """Convert one module PDF via docling, chunk it, return a list of
    {"text", "module", "pdf_page", "headings"} dicts."""
    t0 = time.time()
    result = converter.convert(path)
    doc = result.document
    print(f"  [{module_name}] converted in {time.time()-t0:.0f}s "
          f"({len(doc.pages)} pages)", file=sys.stderr)

    chunks = []
    for chunk in chunker.chunk(doc):
        doc_items = chunk.meta.doc_items
        pdf_page = doc_items[0].prov[0].page_no if doc_items and doc_items[0].prov else None
        chunks.append({
            "text": chunk.text,
            "module": module_name,
            "pdf_page": pdf_page,
            "headings": list(chunk.meta.headings or []),
        })
    print(f"  [{module_name}] chunked into {len(chunks)} pieces", file=sys.stderr)
    return chunks


def main():
    converter = DocumentConverter()
    chunker = HybridChunker()

    all_chunks = []
    for module_name, path in MODULES:
        all_chunks.extend(chunk_module(converter, chunker, module_name, path))

    print(f"\nTotal chunks across all 5 modules: {len(all_chunks)}", file=sys.stderr)

    client = si.get_client()
    openai_client = OpenAI()
    indexed = si.index_raw_chunks(client, openai_client, all_chunks)
    print(f"Indexed {indexed} chunks (OpenAI {si.OPENAI_EMBED_MODEL}) into Qdrant collection "
          f"'{si.RAW_COLLECTION}'", file=sys.stderr)


if __name__ == "__main__":
    main()
