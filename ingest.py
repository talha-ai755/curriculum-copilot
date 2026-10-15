#!/usr/bin/env python3
"""
ingest.py — registry-driven ingestion of a base curriculum version: create → submit → build.
Replaces the per-course submit_batch_<course>.py / build_batch_<course>_course.py pairs; the
Streamlit Library page (Phase 4) calls these same functions.

  create_base()  register a new base curriculum + version 1 (status 'draft') with its module PDFs
  submit()       send the version's modules to the Claude Batch API (lesson-aligned batches);
                 batch state saved in the version folder; job recorded; status 'extracting'
  build()        wait for the batch, save every raw chunk under the version's extractions/,
                 build the graph into the version folder (labels, page ranges, repairs,
                 citations, checks), status 'ready'

Files: data/curricula/<id>/v<n>/{batch_state.json, extractions/<module>/chunk_*.json,
nodes.jsonl, relationships.jsonl}.

CLI:
  python3 ingest.py create --name "Algebra II" --subject math --publisher bluebonnet_secondary \\
      --framework teks_math --pdf "Module 1=/path/m1.pdf" --pdf "Module 2=/path/m2.pdf"
  python3 ingest.py submit <curriculum_id> <version>       # paid: Batch API
  python3 ingest.py build <curriculum_id> <version>        # waits for the batch, then builds
"""
import argparse
import os
import sys
import traceback

import batch_pipeline as bp
import registry

ROOT = os.path.dirname(os.path.abspath(__file__))
# Phase 2 replaces this with the frameworks/ registry.
FRAMEWORK_FILES = {"teks_math": os.path.join(ROOT, "teks_math_case.json")}
FRAMEWORK_META = {"teks_math": {"jurisdiction": "Texas", "framework": "TEKS"}}


def create_base(conn, name, subject, publisher_profile, framework_id, module_pdfs, user,
                citation_slug=None, curriculum_id=None):
    """Register a base curriculum and its version 1 (draft). `module_pdfs`: {module: pdf path},
    in order. Returns (curriculum_id, version)."""
    if framework_id not in FRAMEWORK_FILES:
        raise ValueError(f"unknown framework {framework_id!r}")
    curriculum_id = curriculum_id or registry.slugify(name)
    if registry.get_curriculum(conn, curriculum_id):
        raise ValueError(f"curriculum {curriculum_id!r} already exists -- add a version instead")
    registry.create_curriculum(conn, curriculum_id, name, subject, publisher_profile, framework_id,
                               user, citation_slug=citation_slug)
    version = registry.add_version(conn, curriculum_id, user, status="draft")
    vdir = registry.version_dir(curriculum_id, version)
    for i, (module, pdf) in enumerate(module_pdfs.items(), 1):
        registry.add_module(conn, curriculum_id, version, module, i, pdf,
                            extraction_dir=os.path.join(vdir, "extractions"))
    return curriculum_id, version


def _state_path(curriculum_id, version):
    return os.path.join(registry.version_dir(curriculum_id, version), "batch_state.json")


def submit(conn, curriculum_id, version, user):
    """Submit every module of the version to the Batch API. Returns the job id."""
    module_pdfs = registry.module_pdfs(conn, curriculum_id, version)
    job_id = registry.create_job(conn, "ingest", curriculum_id, version, user)
    os.makedirs(registry.version_dir(curriculum_id, version), exist_ok=True)
    batch_id = bp.submit_course_batch(f"{curriculum_id}-v{version}", module_pdfs,
                                      _state_path(curriculum_id, version),
                                      custom_id_prefix=f"{curriculum_id[:20]}-v{version}-")
    registry.update_job(conn, job_id, batch_id=batch_id, state="submitted")
    registry.set_version(conn, curriculum_id, version, status="extracting")
    conn.commit()
    return job_id


def build(conn, curriculum_id, version, job_id=None):
    """Wait for the version's batch, save its chunks, build the graph, mark the version ready.
    Marks the version (and job) 'failed' on any error and re-raises."""
    cur = registry.get_curriculum(conn, curriculum_id)
    ver = registry.get_version(conn, curriculum_id, version)
    module_pdfs = registry.module_pdfs(conn, curriculum_id, version)
    vdir = registry.version_dir(curriculum_id, version)
    try:
        registry.set_version(conn, curriculum_id, version, status="building")
        if job_id:
            registry.update_job(conn, job_id, state="building")
        conn.commit()
        chunks, errors = bp.collect_batch_chunks(_state_path(curriculum_id, version),
                                                 list(module_pdfs))
        bp.save_chunks(chunks, os.path.join(vdir, "extractions"))
        meta = FRAMEWORK_META[cur["framework_id"]]
        bp.build_course(chunks, module_pdfs, cur["name"], ver["nodes_path"], ver["rels_path"],
                        course_slug=cur["citation_slug"], jurisdiction=meta["jurisdiction"],
                        framework=meta["framework"], case_path=FRAMEWORK_FILES[cur["framework_id"]])
        notes = f"{sum(len(c) for c in chunks.values())} batches"
        if errors:
            notes += f"; {len(errors)} failed: " + ", ".join(cid for cid, _ in errors[:10])
        registry.set_version(conn, curriculum_id, version, status="ready", notes=notes)
        if job_id:
            registry.update_job(conn, job_id, state="done", detail=notes)
    except Exception:
        registry.set_version(conn, curriculum_id, version, status="failed")
        if job_id:
            registry.update_job(conn, job_id, state="failed", detail=traceback.format_exc()[-2000:])
        conn.commit()
        raise
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--subject", required=True)
    c.add_argument("--publisher", required=True)
    c.add_argument("--framework", required=True)
    c.add_argument("--pdf", action="append", required=True, help='"Module 1=/path/to.pdf"')
    c.add_argument("--user", default="admin")
    for name in ("submit", "build"):
        s = sub.add_parser(name)
        s.add_argument("curriculum_id")
        s.add_argument("version", type=int)
        s.add_argument("--user", default="admin")
    args = ap.parse_args()

    with registry.connect() as conn:
        if args.cmd == "create":
            pdfs = dict(p.split("=", 1) for p in args.pdf)
            cid, v = create_base(conn, args.name, args.subject, args.publisher, args.framework,
                                 pdfs, args.user)
            print(f"created {cid} v{v} ({len(pdfs)} modules) -- next: ingest.py submit {cid} {v}")
        elif args.cmd == "submit":
            job = submit(conn, args.curriculum_id, args.version, args.user)
            print(f"submitted job {job} -- next: ingest.py build {args.curriculum_id} {args.version}")
        else:
            job = next((j["id"] for j in registry.jobs(conn, args.curriculum_id)
                        if j["version"] == args.version and j["state"] == "submitted"), None)
            build(conn, args.curriculum_id, args.version, job)
            print(f"built {args.curriculum_id} v{args.version}")


if __name__ == "__main__":
    sys.exit(main())
