#!/usr/bin/env python3
"""
frameworks.py — the standards frameworks a curriculum can be aligned to.

Each framework is a folder frameworks/<id>/ with meta.yaml (name, subject, jurisdiction, label,
case_file) and a 1EdTech CASE JSON (CFDocument/CFItems/CFAssociations) loaded by
pdf_to_kg.load_case(). Adding a subject's standards = adding a folder; no code change.
"""
import functools
import json
import os

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frameworks")


@functools.lru_cache(maxsize=None)
def all_frameworks():
    """{id: meta dict (with 'id' and absolute 'case_path')}"""
    out = {}
    for fid in sorted(os.listdir(ROOT)):
        meta_path = os.path.join(ROOT, fid, "meta.yaml")
        if os.path.exists(meta_path):
            meta = yaml.safe_load(open(meta_path)) or {}
            meta["id"] = fid
            meta["case_path"] = os.path.join(ROOT, fid, meta["case_file"])
            out[fid] = meta
    return out


def get(framework_id):
    try:
        return all_frameworks()[framework_id]
    except KeyError:
        raise ValueError(f"unknown framework {framework_id!r}; known: {sorted(all_frameworks())}")


def case_path(framework_id):
    return get(framework_id)["case_path"]


@functools.lru_cache(maxsize=None)
def load_case(framework_id):
    """pdf_to_kg.load_case() of the framework's CASE file (cached; don't mutate)."""
    import pdf_to_kg as p2k
    with open(case_path(framework_id)) as f:
        return p2k.load_case(json.load(f))
