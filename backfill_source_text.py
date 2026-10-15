#!/usr/bin/env python3
"""
backfill_source_text.py — deterministic fix for LearningComponent.sourceText: it's supposed to be
verbatim page text, but two separate unreliability bugs have been confirmed by direct comparison
against real PDF pages:

1. Claude's own regenerated transcription (only ever *instructed* to be verbatim, never
   mechanically copied) -- sometimes genuinely verbatim, sometimes duplicated with `description`,
   and in one case real content (an Essential Question) was replaced entirely with a generic
   label instead of being quoted.
2. docling's own extraction (pdf_to_kg.docling_page_text(), used by build_graph()/combine_modules()
   at extraction time to override #1 above) silently drops content on dense/tabular page layouts
   -- confirmed live on a Pacing Guide page: docling returned 290 chars where the real page has
   2004, no error or signal anything was missing. Affects ~11-14% of single-node pages, evenly
   across every course checked (Algebra I, Grade 8, Grade 6, Grade 7) -- a systematic docling
   limitation on this layout style, not a one-PDF quirk.

Fix: for any PDF page that has exactly ONE LearningComponent on it (no ambiguity about which
node the page's text belongs to), overwrite sourceText with pypdfium2's real, deterministically
extracted whole-page text (pdf_to_kg.pdfium_page_text() -- NOT docling, per #2 above). Pages
shared by 2+ LearningComponents are left on Claude's text -- pdfium_page_text() only returns
whole-page text, and splitting one page's text between several sibling activities can't be done
reliably without guessing which words belong to which. Purely deterministic where it acts -- no
re-extraction, no Claude call; only re-reads the source PDFs.

Reuses backfill_page_labels.py's course auto-discovery (COURSES) and module_of_each_node() --
same course-registration mechanism, same pdfPage-collides-across-modules handling.

Going forward, pdf_to_kg.py's build_graph()/combine_modules() already do this at extraction time
via their own real_pages parameter (pdfium-sourced, not docling), so newly-ingested courses get
this for free without needing this backfill script -- this is for re-correcting courses already
built before that fix landed.

Usage:
  python3 backfill_source_text.py            # dry run, prints a diff summary only
  python3 backfill_source_text.py --write    # overwrite data/*_nodes.jsonl in place
"""
import argparse
import json
import sys

import pdf_to_kg as p2k
from backfill_page_labels import COURSES, module_of_each_node


def apply(nodes, rels, module_pdfs):
    """Overwrite sourceText on every unambiguous (single-LearningComponent-per-page) node in
    `nodes`, for the ONE course `nodes`/`rels` belong to, with pypdfium2's real page text.
    Mutates `nodes` in place. Returns a stats dict."""
    print("  computing each node's parent module...", file=sys.stderr)
    node_module = module_of_each_node(nodes, rels)

    lc_nodes = [n for n in nodes if n["labels"][0] == "LearningComponent"
                and n["properties"].get("pdfPage") is not None]

    # (module, pdfPage) -> count of LearningComponents on that exact page, so a shared page
    # is never overwritten (no reliable way to know which node owns which words on it).
    page_counts = {}
    for n in lc_nodes:
        key = (node_module.get(n["identifier"]), n["properties"]["pdfPage"])
        page_counts[key] = page_counts.get(key, 0) + 1

    # build one pdfium {page: text} map per module PDF
    module_pages = {}
    for module_name, pdf_path in module_pdfs.items():
        print(f"  reading {module_name} PDF with pypdfium2...", file=sys.stderr)
        module_pages[module_name] = p2k.pdfium_page_text(pdf_path)
        print(f"    {len(module_pages[module_name])} pages", file=sys.stderr)

    overwritten, unchanged, shared_skipped, no_real_text, no_module = 0, 0, 0, 0, 0
    for n in lc_nodes:
        module_name = node_module.get(n["identifier"])
        if not module_name or module_name not in module_pages:
            no_module += 1
            continue
        key = (module_name, n["properties"]["pdfPage"])
        if page_counts[key] > 1:
            shared_skipped += 1
            continue
        real_text = module_pages[module_name].get(n["properties"]["pdfPage"])
        if not real_text:
            no_real_text += 1
            continue
        if n["properties"].get("sourceText") == real_text:
            unchanged += 1
            continue
        n["properties"]["sourceText"] = real_text
        overwritten += 1

    stats = {"overwritten": overwritten, "unchanged": unchanged, "shared_skipped": shared_skipped,
              "no_real_text": no_real_text, "no_module": no_module}
    print(f"  overwritten with real pdfium text: {overwritten}, unchanged (already matched): "
          f"{unchanged}, left alone (shared page): {shared_skipped}, left alone (no pdfium text "
          f"for that page): {no_real_text}, no known parent module: {no_module}", file=sys.stderr)
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Overwrite data/*_nodes.jsonl in place (default: dry run)")
    args = parser.parse_args()

    for jsonl_path, module_pdfs in COURSES.items():
        rels_path = jsonl_path.replace("_nodes.jsonl", "_relationships.jsonl")
        print(f"\n=== {jsonl_path} ===", file=sys.stderr)
        with open(jsonl_path) as f:
            nodes = [json.loads(l) for l in f]
        with open(rels_path) as f:
            rels = [json.loads(l) for l in f]

        apply(nodes, rels, module_pdfs)

        if args.write:
            with open(jsonl_path, "w") as f:
                for n in nodes:
                    f.write(json.dumps(n) + "\n")
            print(f"  wrote {jsonl_path}", file=sys.stderr)

    if not args.write:
        print("\nDry run only -- pass --write to overwrite data/*_nodes.jsonl in place.", file=sys.stderr)
        print("(Reloading Neo4j + rebuilding the Qdrant index are separate, explicit steps.)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
