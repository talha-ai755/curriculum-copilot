#!/usr/bin/env python3
"""
profiles.py — subject and publisher profiles, so a new subject or publisher is configuration
(profiles/subjects/<id>.yaml, profiles/publishers/<id>.yaml), not code.

Subject profile: name, academic_subject, default_framework, extraction prompt, lesson phases and
supplementary-content types (the extraction schema's enums), materials_from_pacing_guide.
Publisher profile: header_format -- 'bluebonnet' (lesson_pages.py reads running headers for
lesson-aligned batches and page ranges) or 'none' (fixed 25-page batches).
"""
import copy
import functools
import os

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles")


def _load_dir(kind):
    out = {}
    d = os.path.join(ROOT, kind)
    for f in sorted(os.listdir(d)):
        if f.endswith(".yaml"):
            out[f[:-5]] = {**(yaml.safe_load(open(os.path.join(d, f))) or {}), "id": f[:-5]}
    return out


@functools.lru_cache(maxsize=None)
def subjects():
    return _load_dir("subjects")


@functools.lru_cache(maxsize=None)
def publishers():
    return _load_dir("publishers")


def subject(subject_id):
    return subjects().get(subject_id) or subjects()["generic"]


def publisher(publisher_id):
    return publishers().get(publisher_id) or publishers()["generic"]


def header_format(publisher_id):
    return publisher(publisher_id).get("header_format", "none")


@functools.lru_cache(maxsize=None)
def extraction(subject_id):
    """{"system": prompt, "schema": structured-output schema, "supp_roles": {type: role}} for one
    subject. Math returns exactly pdf_to_kg.SYSTEM / EXTRACTION_SCHEMA."""
    import pdf_to_kg as p2k
    prof = subject(subject_id)
    system = (p2k.SYSTEM if prof["prompt"] == "builtin_math"
              else open(os.path.join(ROOT, "prompts", prof["prompt"])).read())
    schema = copy.deepcopy(p2k.EXTRACTION_SCHEMA)
    lesson_props = schema["properties"]["topics"]["items"]["properties"]["lessons"]["items"]["properties"]
    lesson_props["learning_outcomes"]["items"]["properties"]["lesson_phase"]["enum"] = \
        list(prof["lesson_phases"]) + [""]
    schema["properties"]["supplementary"]["items"]["properties"]["type"]["enum"] = \
        list(prof["supplementary_types"])
    return {"system": system, "schema": schema, "supp_roles": dict(prof["supplementary_types"])}
