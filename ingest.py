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
import frameworks
import profiles
import registry

ROOT = os.path.dirname(os.path.abspath(__file__))


def create_base(conn, name, subject, publisher_profile, framework_id, module_pdfs, user,
                citation_slug=None, curriculum_id=None):
    """Register a base curriculum and its version 1 (draft). `module_pdfs`: {module: pdf path},
    in order. Returns (curriculum_id, version)."""
    frameworks.get(framework_id)   # raises for an unknown framework
    if publisher_profile not in profiles.publishers():
        raise ValueError(f"unknown publisher profile {publisher_profile!r}")
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


def submit(conn, curriculum_id, version, user, only=None):
    """Submit the version's modules to the Batch API -- all batches, or just `only`
    ({module: {(first, last), ...}}, from plan_update()). Returns the job id."""
    module_pdfs = registry.module_pdfs(conn, curriculum_id, version)
    cur = registry.get_curriculum(conn, curriculum_id)
    job_id = registry.create_job(conn, "ingest", curriculum_id, version, user)
    os.makedirs(registry.version_dir(curriculum_id, version), exist_ok=True)
    batch_id = bp.submit_course_batch(f"{curriculum_id}-v{version}", module_pdfs,
                                      _state_path(curriculum_id, version),
                                      custom_id_prefix=f"{curriculum_id[:20]}-v{version}-",
                                      only=only, subject_id=cur["subject"],
                                      header_format=profiles.header_format(cur["publisher_profile"]))
    registry.update_job(conn, job_id, batch_id=batch_id or "", state="submitted")
    registry.set_version(conn, curriculum_id, version, status="extracting")
    conn.commit()
    return job_id


# ── updates: a new version from new module PDFs, re-extracting only what changed ─────────
def plan_update(conn, curriculum_id, new_pdfs):
    """What a new version would change and cost -- no side effects, no API calls.
    `new_pdfs`: {module: new pdf path} for the modules being replaced or added. Returns
    {"base_version", "modules": {module: {...}}, "calls": n} (see changes.plan_module_update)."""
    import changes
    import lesson_pages
    import pdf_to_kg as p2k
    prev = registry.latest_ready(conn, curriculum_id)
    if not prev:
        raise ValueError(f"{curriculum_id} has no ready version to update")
    hf = profiles.header_format(registry.get_curriculum(conn, curriculum_id)["publisher_profile"])
    prev_mods = {m["module"]: m for m in registry.modules(conn, curriculum_id, prev["version"])}
    out, calls = {}, 0
    for module in list(prev_mods) + [m for m in new_pdfs if m not in prev_mods]:
        old = prev_mods.get(module)
        new_pdf = new_pdfs.get(module, old and old["pdf_path"])
        saved = (bp.load_saved_chunks(old["extraction_dir"], module)
                 if old and old["extraction_dir"] else [])
        new_sha = registry.sha256_file(new_pdf)
        new_pt = p2k.pdfium_page_text(new_pdf)
        _, new_lo = lesson_pages.scan_pdf(new_pdf, hf)
        if old and new_sha == old["pdf_sha256"] and saved:
            report = {"status": "unchanged", "reuse_all": True,
                      "batches_to_extract": [], "batches_reused": len(saved)}
        else:
            old_pt = p2k.pdfium_page_text(old["pdf_path"]) if old else {}
            old_lo = lesson_pages.scan_pdf(old["pdf_path"], hf)[1] if old else {}
            report = changes.plan_module_update(old_pt, new_pt, new_lo, old_lo, saved)
            report["status"] = ("new module" if not old else "unchanged PDF, no saved chunks"
                                if new_sha == old["pdf_sha256"] else "changed")
            report["reuse_all"] = False
        report.update(pdf_path=new_pdf, pdf_sha256=new_sha)
        calls += len(report["batches_to_extract"])
        out[module] = report
    return {"base_version": prev["version"], "modules": out, "calls": calls}


def create_update_version(conn, curriculum_id, user, plan):
    """Create the next version from a plan_update() result: register its modules, copy every
    reusable chunk into the new version (page numbers remapped to the new PDF) and return
    (version, only) where `only` is what submit() must still extract."""
    import changes
    prev = registry.get_version(conn, curriculum_id, plan["base_version"])
    prev_mods = {m["module"]: m for m in registry.modules(conn, curriculum_id, prev["version"])}
    version = registry.add_version(conn, curriculum_id, user, status="draft",
                                   notes=f"update of v{prev['version']}")
    extraction_root = os.path.join(registry.version_dir(curriculum_id, version), "extractions")
    only = {}
    for i, (module, rep) in enumerate(plan["modules"].items(), 1):
        registry.add_module(conn, curriculum_id, version, module, i, rep["pdf_path"],
                            rep["pdf_sha256"], extraction_dir=extraction_root)
        old = prev_mods.get(module)
        saved = (bp.load_saved_chunks(old["extraction_dir"], module)
                 if old and old["extraction_dir"] else [])
        if rep["reuse_all"]:
            bp.save_chunks({module: saved}, extraction_root)
            continue
        by_core = {tuple(c["_core_pages"]): c for c in saved}
        new_of_old = {o: n for n, o in rep["page_map"].items()}
        bp.save_chunks({module: [changes.remap_chunk(by_core[tuple(b["old_core"])], new_of_old)
                                 for b in rep["batches_reused"]]}, extraction_root)
        only[module] = {tuple(b["core"]) for b in rep["batches_to_extract"]}
    conn.commit()
    return version, only


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
        extraction_root = os.path.join(vdir, "extractions")
        fresh, errors = bp.collect_batch_chunks(_state_path(curriculum_id, version),
                                                list(module_pdfs))
        # chunks already in the version folder = reused from the previous version (updates)
        reused = {m: bp.load_saved_chunks(extraction_root, m) for m in module_pdfs}
        bp.save_chunks(fresh, extraction_root)
        chunks = {m: sorted(reused[m] + fresh[m], key=lambda c: c["_core_pages"][0])
                  for m in module_pdfs}
        fw = frameworks.get(cur["framework_id"])
        subj = profiles.subject(cur["subject"])
        bp.build_course(chunks, module_pdfs, cur["name"], ver["nodes_path"], ver["rels_path"],
                        course_slug=cur["citation_slug"], jurisdiction=fw["jurisdiction"],
                        framework=fw["label"], case_path=fw["case_path"],
                        academic_subject=subj["academic_subject"] or cur["subject"],
                        supp_roles=profiles.extraction(cur["subject"])["supp_roles"],
                        materials_from_pacing_guide=subj["materials_from_pacing_guide"],
                        header_format=profiles.header_format(cur["publisher_profile"]))
        notes = (f"{sum(len(c) for c in fresh.values())} batches extracted, "
                 f"{sum(len(c) for c in reused.values())} reused")
        if errors:
            notes += f"; {len(errors)} failed: " + ", ".join(cid for cid, _ in errors[:10])
        diff = _diff_with_previous(conn, curriculum_id, version, ver["nodes_path"])
        registry.set_version(conn, curriculum_id, version, status="ready", notes=notes,
                             diff_json=registry.dumps(diff) if diff else "")
        if job_id:
            registry.update_job(conn, job_id, state="done", detail=notes)
    except Exception:
        registry.set_version(conn, curriculum_id, version, status="failed")
        if job_id:
            registry.update_job(conn, job_id, state="failed", detail=traceback.format_exc()[-2000:])
        conn.commit()
        raise
    conn.commit()


def _diff_with_previous(conn, curriculum_id, version, nodes_path):
    """changes.diff_graphs() against the latest earlier ready version, or None for a first
    version."""
    import json
    import changes
    earlier = [v for v in registry.versions(conn, curriculum_id)
               if v["version"] < version and v["status"] == "ready"]
    if not earlier:
        return None
    load = lambda p: [json.loads(l) for l in open(p) if l.strip()]
    diff = changes.diff_graphs(load(earlier[-1]["nodes_path"]), load(nodes_path))
    diff["against_version"] = earlier[-1]["version"]
    return diff


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
