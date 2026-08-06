#!/usr/bin/env python3
"""
migrate_neo4j_to_remote.py — load this repo's data/*.jsonl (both courses, already the portable
export format graph_store.py reads/writes all session) into a remote Neo4j instance instead of
localhost. Wipes the target first, then loads fresh -- same wipe-and-reload pattern used all
session locally, just pointed elsewhere.

Run this from a machine that can reach the target Neo4j's bolt port -- this dev machine can't
reach a private VPC IP directly (confirmed earlier: 10.0.136.27 timed out), so either run this
on the EC2 instance itself (after copying data/*.jsonl there), or from wherever has real network
access to it (VPN, bastion, same VPC).

Usage:
  NEO4J_URI=bolt://<ec2-ip>:7687 NEO4J_PASSWORD='<the password from setup_neo4j_ec2.sh>' \\
    python3 migrate_neo4j_to_remote.py
"""
import os
import sys

import agent
import graph_store as gs


def main():
    uri = os.environ.get("NEO4J_URI")
    if not uri:
        sys.exit("Set NEO4J_URI (e.g. bolt://<ec2-ip>:7687) and NEO4J_PASSWORD first. Aborting.")
    user = os.environ.get("NEO4J_USER", gs.DEFAULT_USER)
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        sys.exit("Set NEO4J_PASSWORD (the one setup_neo4j_ec2.sh was run with). Aborting.")

    print(f"Loading data/*.jsonl (all courses) ...", file=sys.stderr)
    nodes, rels = agent.load_all_courses()
    print(f"  {len(nodes)} nodes, {len(rels)} rels", file=sys.stderr)

    print(f"Connecting to {uri} ...", file=sys.stderr)
    driver = gs.get_driver(uri, user, password)

    print("Wiping target graph (if any existing data) ...", file=sys.stderr)
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")

    print("Loading ...", file=sys.stderr)
    gs.load_graph(driver, nodes, rels)

    count = gs.run_cypher(driver, "MATCH (n) RETURN count(n) AS c")[0]["c"]
    rel_count = gs.run_cypher(driver, "MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
    print(f"\nDone: {count} nodes / {rel_count} rels loaded onto {uri}", file=sys.stderr)
    if count != len(nodes):
        print("WARNING: node count doesn't match source -- investigate before trusting this.",
              file=sys.stderr)
    driver.close()


if __name__ == "__main__":
    main()
