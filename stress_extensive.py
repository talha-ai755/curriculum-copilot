#!/usr/bin/env python3
"""Extensive Neo4j stress suite (multi-process so the test client isn't the bottleneck).
Phases: A per-query profile · B multi-process ramp · C spike & recovery · D soak · E writes (isolated :StressTest
label, cleaned up + verified) · F connection storm.  Env: STRESS_URI, STRESS_PASSWORD, STRESS_SCALE (time factor),
STRESS_PHASES (e.g. ABCDEF), STRESS_OUT (json path)."""
import json, os, random, statistics as st, threading, time, multiprocessing as mp
from neo4j import GraphDatabase, RoutingControl

URI, PW = os.environ["STRESS_URI"], os.environ["STRESS_PASSWORD"]
SCALE = float(os.environ.get("STRESS_SCALE", 1)); PHASES = os.environ.get("STRESS_PHASES", "ABCDEF")
OUT = os.environ.get("STRESS_OUT", "stress_results.json"); NPROC = min(8, os.cpu_count() or 4)
PAD = "x" * 200

READ = {
  "count":    lambda c: ("MATCH (n) RETURN count(n) AS c", {}),
  "by_id":    lambda c: ("MATCH (n {identifier:$i}) RETURN n", {"i": random.choice(c["ids"])}),
  "by_code":  lambda c: ("MATCH (s:StandardsFrameworkItem {code:$c})-[:hasChild]->(k) RETURN k.code", {"c": random.choice(c["codes"])}),
  "two_hop":  lambda c: ("MATCH (l:Lesson)-[:hasPart]->(lc:LearningComponent)-[:supports]->(x) RETURN l.name, count(x) LIMIT 25", {}),
  "contains": lambda c: ("MATCH (t:LessonGrouping|Lesson) WHERE t.name CONTAINS $s RETURN t.name LIMIT 25", {"s": random.choice(["Linear","Fraction","Equation","Number"])}),
  "prereqs":  lambda c: ("MATCH (a:StandardsFrameworkItem {code:$c})-[:hasStandardAlignment]->(cc)<-[:buildsTowards]-(p) RETURN p.code LIMIT 25", {"c": random.choice(c["codes"])}),
  "agg_supports": lambda c: ("MATCH (a)-[r:supports]->(b) RETURN labels(a)[0] AS la, labels(b)[0] AS lb, count(r) AS c", {}),
  "path_4hop":    lambda c: ("MATCH p=(c:Course)-[:hasPart|hasChild*1..4]->(x) RETURN count(p) AS c", {}),
  "ping":     lambda c: ("RETURN 1", {}),
}
MIX = {"count": 1, "by_id": 4, "by_code": 3, "two_hop": 1, "contains": 2, "prereqs": 2}
def write_q(c): return ("UNWIND range(1,10) AS i CREATE (:StressTest {run:$r, i:i, ts:timestamp(), pad:$p})", {"r": random.randint(0, 10**9), "p": PAD})

def worker(a):
    """One process: `threads` closed-loop clients for `secs`. Returns [(t_rel, latency_s, kind, err|None)]."""
    threads, secs, weights, ctx, t0 = a
    d = GraphDatabase.driver(URI, auth=("neo4j", PW), max_connection_pool_size=threads + 5,
                             connection_acquisition_timeout=60)
    names = [n for n, w in weights.items() for _ in range(w)]; out = []; lock = threading.Lock()
    end = time.time() + secs
    def run():
        loc = []
        while time.time() < end:
            k = random.choice(names)
            q, p = write_q(ctx) if k == "write" else READ[k](ctx)
            t = time.perf_counter()
            try:
                d.execute_query(q, p, routing_=RoutingControl.WRITE if k == "write" else RoutingControl.READ)
                loc.append((time.time() - t0, time.perf_counter() - t, k, None))
            except Exception as e:
                loc.append((time.time() - t0, time.perf_counter() - t, k, type(e).__name__))
        with lock: out.extend(loc)
    ts = [threading.Thread(target=run) for _ in range(threads)]
    [t.start() for t in ts]; [t.join() for t in ts]; d.close(); return out

POOL = None
def step(clients, secs, weights, ctx):
    secs = max(5, int(secs * SCALE)); procs = min(NPROC, max(1, clients // 25)); per = [clients // procs + (1 if i < clients % procs else 0) for i in range(procs)]
    t0 = time.time()
    res = POOL.map(worker, [(n, secs, weights, ctx, t0) for n in per])
    return [s for r in res for s in r], secs

def stats(samples, secs):
    ok = [s[1] for s in samples if not s[3]]; er = [s[3] for s in samples if s[3]]; n = len(samples)
    q = st.quantiles(ok, n=100) if len(ok) > 100 else [max(ok or [0])] * 99
    return dict(rps=round(len(ok) / secs, 1), p50=round(st.median(ok or [0]) * 1000), p95=round(q[94] * 1000),
                p99=round(q[98] * 1000), max=round(max(ok or [0]) * 1000), errors=len(er), err_pct=round(100 * len(er) / max(n, 1), 2),
                err_kinds={k: er.count(k) for k in set(er)})

R = {}
def show(title, rows, cols):
    print(f"\n== {title} =="); print(" ".join(f"{c:>12}" for c in cols))
    for r in rows: print(" ".join(f"{str(r.get(c, '')):>12}" for c in cols), flush=True)

if __name__ == "__main__":
    mp.set_start_method("fork"); POOL = mp.Pool(NPROC)
    d = GraphDatabase.driver(URI, auth=("neo4j", PW)); d.verify_connectivity()
    ctx = {"ids": [r["i"] for r in d.execute_query("MATCH (n:LearningComponent) RETURN n.identifier AS i LIMIT 3000")[0]],
           "codes": [r["c"] for r in d.execute_query("MATCH (n:StandardsFrameworkItem) WHERE n.code<>'' RETURN n.code AS c LIMIT 500")[0]]}
    base_n = d.execute_query("MATCH (n) RETURN count(n) AS c")[0][0]["c"]; base_r = d.execute_query("MATCH ()-[r]->() RETURN count(r) AS c")[0][0]["c"]
    R["baseline"] = dict(nodes=base_n, rels=base_r)
    print(f"target {URI} · {NPROC} procs · scale {SCALE} · baseline {base_n} nodes / {base_r} rels", flush=True)

    if "A" in PHASES:
        rows = []
        for k in READ:
            for c in (1, 50):
                s, secs = step(c, 10 if c == 1 else 15, {k: 1}, ctx); r = stats(s, secs); r.update(query=k, clients=c); rows.append(r)
        R["A_per_query"] = rows; show("A per-query profile", rows, ["query", "clients", "rps", "p50", "p95", "p99", "max", "errors"])

    if "B" in PHASES:
        rows = []
        for c in (50, 100, 200, 400, 800, 1200, 1600):
            s, secs = step(c, 30, MIX, ctx); r = stats(s, secs); r["clients"] = c; rows.append(r)
            print(f"  ramp {c}: {r['rps']} rps p95 {r['p95']}ms err {r['errors']}", flush=True)
            if r["err_pct"] > 5 or r["p95"] > 15000: print("  STOP ramp"); break
        R["B_ramp"] = rows; show("B multi-process ramp (mixed reads)", rows, ["clients", "rps", "p50", "p95", "p99", "max", "errors", "err_pct"])

    if "C" in PHASES:
        rows = []
        for name, c, secs in (("before", 10, 20), ("spike", 400, 30), ("after", 10, 30)):
            s, sc = step(c, secs, MIX, ctx); r = stats(s, sc); r.update(phase=name, clients=c); rows.append(r)
        R["C_spike"] = rows; show("C spike & recovery", rows, ["phase", "clients", "rps", "p50", "p95", "p99", "max", "errors"])

    if "D" in PHASES:
        s, secs = step(100, 480, MIX, ctx); win = []
        for w in range(0, secs, 30):
            part = [x for x in s if w <= x[0] < w + 30]
            if part: r = stats(part, min(30, secs - w)); r["t"] = f"{w}-{w+30}s"; win.append(r)
        R["D_soak"] = {"overall": stats(s, secs), "windows": win, "secs": secs}
        show(f"D soak, 100 clients, {secs}s, per-30s windows", win, ["t", "rps", "p50", "p95", "p99", "max", "errors"])
        print("  overall:", R["D_soak"]["overall"])

    if "E" in PHASES:
        rows = []
        try:
            for c in (1, 10, 50, 100, 200):
                s, secs = step(c, 20, {"write": 1}, ctx); r = stats(s, secs); r.update(clients=c, mode="write 10 nodes/tx", nodes_per_s=round(r["rps"] * 10)); rows.append(r)
            for c in (100, 200):
                s, secs = step(c, 30, {**{k: v * 4 for k, v in MIX.items()}, "write": 5}, ctx); r = stats(s, secs); r.update(clients=c, mode="mixed 80r/20w")
                w = [x for x in s if x[2] == "write" and not x[3]]; r["write_p95"] = round((st.quantiles([x[1] for x in w], n=100)[94] if len(w) > 100 else 0) * 1000); rows.append(r)
        finally:
            R["E_writes"] = rows; show("E writes (:StressTest only)", rows, ["mode", "clients", "rps", "nodes_per_s", "p50", "p95", "p99", "errors"])
            wrote = d.execute_query("MATCH (n:StressTest) RETURN count(n) AS c")[0][0]["c"]
            with d.session() as ss: ss.run("MATCH (n:StressTest) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
            n = d.execute_query("MATCH (n) RETURN count(n) AS c")[0][0]["c"]; r_ = d.execute_query("MATCH ()-[r]->() RETURN count(r) AS c")[0][0]["c"]
            R["E_cleanup"] = dict(created=wrote, nodes_after=n, rels_after=r_, restored=(n == base_n and r_ == base_r))
            print("  cleanup:", R["E_cleanup"], flush=True)

    if "F" in PHASES:
        rows = []
        for c in (10, 50, 200):
            lat, errs = [], []; dur = max(5, 15 * SCALE); end = time.time() + dur; lk = threading.Lock()
            def storm():
                while time.time() < end:
                    t = time.perf_counter()
                    try:
                        dd = GraphDatabase.driver(URI, auth=("neo4j", PW), connection_timeout=20); dd.execute_query("RETURN 1"); dd.close()
                        with lk: lat.append(time.perf_counter() - t)
                    except Exception as e:
                        with lk: errs.append(type(e).__name__)
            ts = [threading.Thread(target=storm) for _ in range(c)]; [t.start() for t in ts]; [t.join() for t in ts]
            q = st.quantiles(lat, n=100) if len(lat) > 100 else [max(lat or [0])] * 99
            rows.append(dict(clients=c, conn_per_s=round(len(lat) / dur, 1), p50=round(st.median(lat or [0]) * 1000), p95=round(q[94] * 1000), errors=len(errs)))
        R["F_conn_storm"] = rows; show("F connection storm (new connection + auth + query each time)", rows, ["clients", "conn_per_s", "p50", "p95", "errors"])

    json.dump(R, open(OUT, "w"), indent=1); print("\nsaved", OUT)
