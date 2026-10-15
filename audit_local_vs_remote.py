#!/usr/bin/env python3
"""Audit: is the remote Neo4j identical to the local one?
Reads creds from .env (remote values are the commented '# NEO4J_*' lines). Read-only on both sides.
Compares counts per label / rel type, then a per-node fingerprint (labels + all properties) and
per-rel fingerprint (type + endpoint fingerprints + properties)."""
import hashlib, json, re, sys
from collections import Counter
from neo4j import GraphDatabase

def parse_env(path=".env"):
    loc, rem = {}, {}
    for line in open(path):
        line = line.strip()
        m = re.match(r"^(#\s*)?(NEO4J_(?:URI|USER|PASSWORD))=(.*)$", line)
        if m:
            (rem if m.group(1) else loc)[m.group(2)] = m.group(3).strip()
    return loc, rem

def h(o): return hashlib.sha1(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()

def snapshot(cfg):
    d = GraphDatabase.driver(cfg["NEO4J_URI"], auth=(cfg["NEO4J_USER"], cfg["NEO4J_PASSWORD"]),
                             connection_timeout=10)
    d.verify_connectivity()
    with d.session() as s:
        nodes = {}
        for r in s.run("MATCH (n) RETURN elementId(n) AS id, labels(n) AS l, properties(n) AS p"):
            nodes[r["id"]] = h([sorted(r["l"]), r["p"]])
        rels = Counter()
        for r in s.run("MATCH (a)-[r]->(b) RETURN elementId(a) AS a, elementId(b) AS b, type(r) AS t, properties(r) AS p"):
            rels[h([nodes[r["a"]], r["t"], nodes[r["b"]], r["p"]])] += 1
        idx = sorted((r["name"], r["type"], str(r["labelsOrTypes"]), str(r["properties"]))
                     for r in s.run("SHOW INDEXES YIELD name,type,labelsOrTypes,properties WHERE type <> 'LOOKUP'"))
        con = sorted((str(r["labelsOrTypes"]), str(r["properties"]), r["type"])
                     for r in s.run("SHOW CONSTRAINTS YIELD labelsOrTypes,properties,type"))
        lab = {r["l"]: r["c"] for r in s.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS c")}
        rt = {r["t"]: r["c"] for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")}
    d.close()
    return dict(nodes=Counter(nodes.values()), n=len(nodes), rels=rels, nr=sum(rels.values()),
                idx=idx, con=con, lab=lab, rt=rt)

def main():
    import os
    loc, rem = parse_env()
    if os.environ.get("AUDIT_REMOTE_URI"):  # explicit target overrides .env (e.g. production)
        rem = dict(NEO4J_URI=os.environ["AUDIT_REMOTE_URI"], NEO4J_USER=os.environ.get("AUDIT_REMOTE_USER", "neo4j"),
                   NEO4J_PASSWORD=os.environ["AUDIT_REMOTE_PASSWORD"])
        loc = dict(NEO4J_URI=os.environ.get("AUDIT_LOCAL_URI", "bolt://localhost:7687"), NEO4J_USER="neo4j", NEO4J_PASSWORD=os.environ["AUDIT_LOCAL_PASSWORD"])
    if len(rem) < 3: sys.exit("remote NEO4J_* lines not found in .env")
    print(f"local : {loc['NEO4J_URI']}\nremote: {rem['NEO4J_URI']}")
    L = snapshot(loc)
    try: R = snapshot(rem)
    except Exception as e: sys.exit(f"cannot reach remote: {type(e).__name__}: {e}")
    ok = True
    def row(name, a, b):
        nonlocal ok
        same = a == b; ok &= same
        print(f"  [{'OK ' if same else 'DIFF'}] {name}: local={a} remote={b}")
    print("\nTotals"); row("nodes", L["n"], R["n"]); row("relationships", L["nr"], R["nr"])
    print("\nNodes per label")
    for k in sorted(set(L["lab"]) | set(R["lab"])): row(k, L["lab"].get(k, 0), R["lab"].get(k, 0))
    print("\nRels per type")
    for k in sorted(set(L["rt"]) | set(R["rt"])): row(k, L["rt"].get(k, 0), R["rt"].get(k, 0))
    print("\nContent fingerprints (labels+properties, ids ignored)")
    nl, nr_ = L["nodes"] - R["nodes"], R["nodes"] - L["nodes"]
    row("nodes only-in-local", sum(nl.values()), 0); row("nodes only-in-remote", sum(nr_.values()), 0)
    rl, rr = L["rels"] - R["rels"], R["rels"] - L["rels"]
    row("rels only-in-local", sum(rl.values()), 0); row("rels only-in-remote", sum(rr.values()), 0)
    print("\nSchema"); row("indexes", L["idx"], R["idx"]); row("constraints", L["con"], R["con"])
    print("\nRESULT:", "IDENTICAL" if ok else "DIFFERENCES FOUND"); sys.exit(0 if ok else 1)

main()
