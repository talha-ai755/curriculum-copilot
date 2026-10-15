#!/usr/bin/env python3
"""
changes.py — what changed between two versions of a module PDF, which extraction batches can be
reused, and what changed between two built graph versions.

  page_hashes(page_text)          {page: hash of its normalized text}
  align_pages(old, new)           page-level diff that survives inserted/removed pages (difflib)
  plan_module_update(...)         change report + which new batches reuse a saved chunk
  remap_chunk(chunk, page_map)    a saved chunk with its page numbers moved to the new PDF
  diff_graphs(old, new)           added / removed / changed nodes by (stable) identifier

The plan is computed before any paid call, so the admin sees the cost first. A new batch reuses
a saved chunk only when every page of it is unchanged AND the saved chunk covered exactly those
pages (same lesson boundaries) -- otherwise it is re-extracted.
"""
import difflib
import hashlib
import re

import lesson_pages
import pdf_to_kg as p2k


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").replace("\r", "")).strip().lower()


def page_hashes(page_text):
    return {p: hashlib.sha1(_norm(t).encode()).hexdigest() for p, t in page_text.items()}


def align_pages(old_hashes, new_hashes):
    """{new_page: old_page} for unchanged pages, plus the changed/new pages of the new PDF and the
    removed pages of the old one. 'changed' = a new page sitting where old pages were replaced."""
    old_pages, new_pages = sorted(old_hashes), sorted(new_hashes)
    sm = difflib.SequenceMatcher(a=[old_hashes[p] for p in old_pages],
                                 b=[new_hashes[p] for p in new_pages], autojunk=False)
    page_map, changed, added, removed = {}, [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                page_map[new_pages[j1 + k]] = old_pages[i1 + k]
        elif tag == "replace":
            changed += new_pages[j1:j2]
            removed += old_pages[i1:i2]
        elif tag == "insert":
            added += new_pages[j1:j2]
        elif tag == "delete":
            removed += old_pages[i1:i2]
    return {"page_map": page_map, "changed": changed, "added": added, "removed": removed}


def _lessons_on(pages, lesson_of):
    """Lessons whose header range holds any of `pages`; a page inside no range (an inserted page
    without a recognizable header, or one that splits a lesson's run) is attributed to the
    lesson(s) on its neighbouring pages, so a change is never reported as touching no lesson."""
    ranges = lesson_pages.lesson_ranges(lesson_of)

    def owners(p):
        return {key for key, (lo, hi) in ranges.items() if lo <= p <= hi}

    hit = set()
    for p in pages:
        found = owners(p)
        if not found:
            found = owners(p - 1) | owners(p + 1)
        hit |= found
    return sorted(hit, key=lambda k: (k[0], int(k[1]) if k[1].isdigit() else 0))


def plan_module_update(old_page_text, new_page_text, new_lesson_of, old_lesson_of, saved_chunks,
                       max_pages=30):
    """Change report for one module + which of the NEW batches can reuse a saved chunk.
    `saved_chunks`: the old version's chunks for this module (each with `_core_pages`), or []
    when none were saved (imported v1) -- then every batch must be extracted."""
    al = align_pages(page_hashes(old_page_text), page_hashes(new_page_text))
    saved_by_core = {tuple(c["_core_pages"]): c for c in saved_chunks}
    reuse, extract = [], []
    for first, last, _text in p2k.build_lesson_batches(new_page_text, new_lesson_of,
                                                       max_pages=max_pages):
        core = [p for p in sorted(new_page_text) if first <= p <= last]
        mapped = [al["page_map"].get(p) for p in core]
        old_core = (mapped[0], mapped[-1]) if None not in mapped else None
        contiguous = old_core and mapped == list(range(old_core[0], old_core[1] + 1))
        if contiguous and old_core in saved_by_core:
            reuse.append({"core": (first, last), "old_core": old_core})
        else:
            extract.append({"core": (first, last)})
    touched_new = al["changed"] + al["added"]
    return {
        "pages": {"old": len(old_page_text), "new": len(new_page_text),
                  "unchanged": len(al["page_map"]), "changed": len(al["changed"]),
                  "added": len(al["added"]), "removed": len(al["removed"])},
        "lessons_changed": _lessons_on(touched_new, new_lesson_of),
        "lessons_removed_pages": _lessons_on(al["removed"], old_lesson_of),
        "batches_reused": reuse, "batches_to_extract": extract,
        "page_map": al["page_map"], "has_saved_chunks": bool(saved_chunks),
    }


_PAGE_KEYS = ("page", "page_start", "page_end")


def remap_chunk(chunk, page_map_old_to_new):
    """Copy of a saved chunk with every page number (outcomes, lessons, topics, assessments,
    supplementary, _core_pages) moved from the old PDF's numbering to the new one's."""
    def fix(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in _PAGE_KEYS and isinstance(v, int) and v in page_map_old_to_new:
                    out[k] = page_map_old_to_new[v]
                elif k == "_core_pages":
                    out[k] = [page_map_old_to_new.get(v, v) for v in v]
                else:
                    out[k] = fix(v)
            return out
        if isinstance(obj, list):
            return [fix(x) for x in obj]
        return obj
    return fix(chunk)


_VOLATILE = {"identifier"}


def diff_graphs(old_nodes, new_nodes):
    """{"added": [...], "removed": [...], "changed": [{id, label, name, fields}]} by identifier.
    Meaningful only when both versions were built with stable content ids."""
    old = {n["identifier"]: n for n in old_nodes}
    new = {n["identifier"]: n for n in new_nodes}

    def brief(n):
        p = n["properties"]
        return {"id": n["identifier"], "label": n["labels"][0],
                "name": p.get("name") or p.get("code") or (p.get("description") or "")[:80]}

    changed = []
    for i in old.keys() & new.keys():
        a, b = old[i]["properties"], new[i]["properties"]
        fields = sorted(k for k in a.keys() | b.keys() if k not in _VOLATILE and a.get(k) != b.get(k))
        if fields:
            changed.append({**brief(new[i]), "fields": fields})
    return {"added": [brief(new[i]) for i in new.keys() - old.keys()],
            "removed": [brief(old[i]) for i in old.keys() - new.keys()],
            "changed": changed}
