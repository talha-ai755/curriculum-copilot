#!/usr/bin/env python3
"""
reload_neo4j.py — make a LOCAL Neo4j exactly match data/*.jsonl: MERGE everything in, then delete
whatever's left over from earlier loads (nodes a repair script removed, edges it redirected --
graph_store.load_graph() alone never deletes, so those stayed live, e.g. a lesson moved to its
correct topic still showing up under the old one too).

Local only, on purpose: refuses any NEO4J_URI whose host isn't localhost/127.0.0.1, so it can't
prune a shared or production database by accident. app.py does NOT prune on startup for the same
reason -- it loads into whatever NEO4J_URI points at.

Usage:
  python3 reload_neo4j.py              # dry run: load nothing, report what's stale
  python3 reload_neo4j.py --apply      # load + delete stale nodes/edges
"""
import argparse
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

import graph_store as gs
from agent import load_all_courses

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Load and prune (default: dry run)")
    args = parser.parse_args()

    uri = os.environ.get("NEO4J_URI", gs.DEFAULT_URI)
    host = urlparse(uri).hostname
    if host not in LOCAL_HOSTS:
        sys.exit(f"Refusing: NEO4J_URI host {host!r} is not local. This script only touches a "
                 f"local Neo4j -- override NEO4J_URI for this one command, e.g. "
                 f"NEO4J_URI=bolt://localhost:7687 python3 reload_neo4j.py")

    nodes, rels = load_all_courses()
    print(f"data/: {len(nodes)} nodes, {len(rels)} rels", file=sys.stderr)
    driver = gs.get_driver(uri, os.environ.get("NEO4J_USER", gs.DEFAULT_USER),
                           os.environ.get("NEO4J_PASSWORD", gs.DEFAULT_PASSWORD))
    try:
        if args.apply:
            gs.load_graph(driver, nodes, rels)
        stale_nodes, stale_edges = gs.find_stale(driver, nodes, rels)
        print(f"stale in {uri}: {len(stale_nodes)} node(s), {len(stale_edges)} edge(s)",
              file=sys.stderr)
        if args.apply:
            gs.prune_stale(driver, stale_nodes, stale_edges)
            left = gs.find_stale(driver, nodes, rels)
            print(f"pruned; stale now: {len(left[0])} node(s), {len(left[1])} edge(s)",
                  file=sys.stderr)
        else:
            print("Dry run only -- pass --apply to load and prune.", file=sys.stderr)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
