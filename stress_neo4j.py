#!/usr/bin/env python3
"""Read-only Neo4j load test. Ramps concurrency; stops if error rate >5% or p95 >5s.
Env: STRESS_URI, STRESS_PASSWORD, STRESS_STEP_SECS (default 20), STRESS_LEVELS (default 1,5,10,25,50,100)"""
import os, random, statistics as st, threading, time
from neo4j import GraphDatabase, RoutingControl

URI, PW = os.environ["STRESS_URI"], os.environ["STRESS_PASSWORD"]
SECS = int(os.environ.get("STRESS_STEP_SECS", 20))
LEVELS = [int(x) for x in os.environ.get("STRESS_LEVELS", "1,5,10,25,50,100").split(",")]
d = GraphDatabase.driver(URI, auth=("neo4j", PW), max_connection_pool_size=max(LEVELS) + 5,
                         connection_acquisition_timeout=30)
d.verify_connectivity()
ids = [r["i"] for r in d.execute_query("MATCH (n:LearningComponent) RETURN n.identifier AS i LIMIT 2000")[0]]
codes = [r["c"] for r in d.execute_query("MATCH (n:StandardsFrameworkItem) WHERE n.code<>'' RETURN n.code AS c LIMIT 500")[0]]

QUERIES = {
  "count":      lambda: ("MATCH (n) RETURN count(n) AS c", {}),
  "by_id":      lambda: ("MATCH (n {identifier:$i}) RETURN n", {"i": random.choice(ids)}),
  "by_code":    lambda: ("MATCH (s:StandardsFrameworkItem {code:$c})-[:hasChild]->(k) RETURN k.code", {"c": random.choice(codes)}),
  "2hop":       lambda: ("MATCH (l:Lesson)-[:hasPart]->(lc:LearningComponent)-[:supports]->(x) RETURN l.name, count(x) LIMIT 25", {}),
  "contains":   lambda: ("MATCH (t:LessonGrouping|Lesson) WHERE t.name CONTAINS $s RETURN t.name LIMIT 25", {"s": random.choice(["Linear","Fraction","Equation","Number"])}),
  "ping":       lambda: ("RETURN 1", {}),
  "heavy_agg":  lambda: ("MATCH (a)-[r:supports]->(b) RETURN labels(a)[0] AS la, labels(b)[0] AS lb, count(r) AS c", {}),
  "heavy_path": lambda: ("MATCH p=(c:Course)-[:hasPart|hasChild*1..4]->(x) RETURN count(p) AS c", {}),
  "prereqs":    lambda: ("MATCH (a:StandardsFrameworkItem {code:$c})-[:hasStandardAlignment]->(cc)<-[:buildsTowards]-(p) RETURN p.code LIMIT 25", {"c": random.choice(codes)}),
}
import json
WEIGHTS = json.loads(os.environ.get("STRESS_WEIGHTS", '{"count": 1, "by_id": 4, "by_code": 3, "2hop": 1, "contains": 2, "prereqs": 2}'))
names = [n for n, w in WEIGHTS.items() for _ in range(w)]

def worker(stop, lat, errs):
    while time.time() < stop:
        q, p = QUERIES[random.choice(names)]()
        t = time.perf_counter()
        try:
            d.execute_query(q, p, routing_=RoutingControl.READ); lat.append(time.perf_counter() - t)
        except Exception as e:
            errs.append(type(e).__name__)

print(f"target {URI}  step {SECS}s  read-only")
print(f"{'conc':>5} {'req/s':>8} {'p50ms':>7} {'p95ms':>7} {'p99ms':>7} {'max ms':>7} {'errors':>7}")
for c in LEVELS:
    lat, errs = [], []; stop = time.time() + SECS
    ts = [threading.Thread(target=worker, args=(stop, lat, errs)) for _ in range(c)]
    [t.start() for t in ts]; [t.join() for t in ts]
    n = len(lat) + len(errs)
    q = st.quantiles(lat, n=100) if len(lat) > 100 else [max(lat or [0])] * 99
    p95 = q[94] * 1000
    print(f"{c:>5} {len(lat)/SECS:>8.1f} {st.median(lat or [0])*1000:>7.1f} {p95:>7.1f} {q[98]*1000:>7.1f} {max(lat or [0])*1000:>7.1f} {len(errs):>7}", flush=True)
    if errs: print("      error kinds:", {k: errs.count(k) for k in set(errs)})
    if n and len(errs) / n > 0.05 or p95 > 5000:
        print("STOP: error rate >5% or p95 >5s -- breaking point reached"); break
d.close()
