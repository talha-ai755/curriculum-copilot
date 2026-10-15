#!/usr/bin/env python3
"""
import_existing.py — rebuild the registry entries for the courses ingested before the registry
existed, from registry_seed.json (committed: curriculum metadata + module PDF paths/hashes, no
textbook content). data/app.db is gitignored, so this seed is what restores those 10 base
curricula (version 1, pointing at their existing data/<course>_nodes.jsonl files -- nothing is
copied) if the database is ever lost or on a fresh checkout. Idempotent: registered curricula
are skipped.

The seed was exported from the original migration, which read the per-course
submit_batch_<course>.py scripts (MODULES + COURSE_NODES_PATH) those courses were built with;
the scripts are gone now that ingest.py + the registry replace them.

Usage: python3 import_existing.py [--user admin]
"""
import argparse
import json
import os
import sys

import registry

ROOT = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(ROOT, "registry_seed.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="admin")
    args = ap.parse_args()

    with registry.connect() as conn:
        for c in json.load(open(SEED)):
            if registry.get_curriculum(conn, c["id"]):
                print(f"skip {c['id']}: already registered", file=sys.stderr)
                continue
            nodes = os.path.join(ROOT, c["v1_nodes_path"])
            if not os.path.exists(nodes):
                print(f"skip {c['id']}: {c['v1_nodes_path']} not found", file=sys.stderr)
                continue
            registry.create_curriculum(conn, c["id"], c["name"], c["subject"],
                                       c["publisher_profile"], c["framework_id"], args.user,
                                       citation_slug=c["citation_slug"])
            version = registry.add_version(
                conn, c["id"], args.user, status="ready", nodes_path=nodes,
                rels_path=nodes.replace("_nodes.jsonl", "_relationships.jsonl"),
                notes="imported from the pre-registry pipeline")
            for i, m in enumerate(c["modules"], 1):
                registry.add_module(conn, c["id"], version, m["module"], i, m["pdf_path"],
                                    m["pdf_sha256"])
            print(f"registered {c['id']} v{version}: {c['name']}, {len(c['modules'])} modules",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
