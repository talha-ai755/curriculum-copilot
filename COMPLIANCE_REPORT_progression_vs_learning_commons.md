# Standards-Progression Compliance Report
## Bluebonnet Grade 8 & Algebra I vs. the Learning Commons SAP Coherence Map

| | |
|---|---|
| **Corpus** | Bluebonnet Grade 8 + Algebra I (TEKS / Texas) |
| **Authority of record** | Learning Commons MCP — Student Achievement Partners (SAP) Coherence Map |
| **Date** | 2026-08-12 |
| **Scope** | Every standards-progression connection our knowledge graph exposes, both directions |

---

## Verdict

**35 of 35 progression codes compliant — 100% match, zero discrepancies.**

Every distinct Common Core code that carries progression data in our graph returned an
**exactly matching set** of prerequisite (backward) and successor (forward) standards from
Learning Commons. No missing edges, no extra edges, no mislabeled codes in either direction.

---

## 1. How this was verified

1. For every Texas standard in each course, we followed the **real crosswalk**
   (`hasStandardAlignment`) to its Common Core code — the only progression tier the graph now
   carries after the inferred and semantic-fallback tiers were removed.
2. Each distinct Common Core code was resolved on Learning Commons via `find_standard_statement`,
   then queried with `find_standards_progression_from_standard` in both `backward` and `forward`
   directions.
3. The **set** of codes Learning Commons returned was compared to the set our graph stores
   (`buildsTowards` edges) — order-independent, requiring an exact membership match. An empty
   result on their side had to line up with an empty set on ours.
4. All 35 codes were checked **exhaustively — no sampling**, split across parallel verification
   passes. The underlying CASE identifiers confirm both systems draw from the same source data,
   so this is a true equality check, not an approximation.

---

## 2. Coverage: total → progression → remaining

Progression coverage is intentionally bounded to standards that carry a real, verified crosswalk
into the Coherence Map. The remainder have no progression data — reported honestly as such rather
than filled with a guess.

### Grade 8 (TEKS)

| Stage | Count | Share |
|---|--:|--:|
| Total standards | 64 | 100% |
| **With progression data** | **13** | **20.3%** |
| Remaining — no progression data | 51 | 79.7% |

*All 13 Grade 8 standards with progression carry both directions (13 backward, 13 forward).*

### Algebra I (TEKS)

| Stage | Count | Share |
|---|--:|--:|
| Total standards | 68 | 100% |
| **With progression data** | **31** | **45.6%** |
| Remaining — no progression data | 37 | 54.4% |

*Of the 31 Algebra I standards with progression: 29 have prerequisites, 22 have successors, 20 have both.*

---

## 3. Grade 8 — per-standard confirmation

Each TEKS standard, the Common Core code it crosswalks to, the count of prerequisite / successor
standards our graph carries, and the Learning Commons verdict.

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 8.10C | 8.G.A.3 | 2 | 3 | ✅ Confirmed |
| 8.11A | 8.SP.A.1 | 1 | 3 | ✅ Confirmed |
| 8.4A | 8.EE.B.6 | 3 | 5 | ✅ Confirmed |
| 8.4B | 8.EE.B.5 | 1 | 2 | ✅ Confirmed |
| 8.4C | 8.F.B.4 | 2 | 8 | ✅ Confirmed |
| 8.5G | 8.F.A.1 | 1 | 5 | ✅ Confirmed |
| 8.5I | 8.EE.B.6 | 3 | 5 | ✅ Confirmed |
| 8.6C | 8.G.B.6 | 1 | 1 | ✅ Confirmed |
| 8.7A | 8.G.C.9 | 1 | 5 | ✅ Confirmed |
| 8.7B | 7.G.B.6 | 3 | 3 | ✅ Confirmed |
| 8.7C | 8.G.B.6 | 1 | 1 | ✅ Confirmed |
| 8.7C | 8.G.B.7 | 0 | 4 | ✅ Confirmed |
| 8.7D | 8.G.B.8 | 2 | 5 | ✅ Confirmed |
| 8.8D | 8.G.A.5 | 2 | 4 | ✅ Confirmed |
| 8.2B | 8.NS.A.2 | — | — | — no progression |
| 8.5A | 7.RP.A.2.c | — | — | — no progression |
| 8.5F | 7.RP.A.2.a | — | — | — no progression |
| 8.9 | 8.EE.C.8.b | — | — | — no progression |

*Rows marked "no progression" crosswalk to a Common Core code that carries no `buildsTowards`
edges in the source data — nothing to verify, and correctly surfaced as "no data" rather than a
fabricated connection.*

**Distinct Grade 8 progression codes verified: 12** (8.G.A.3, 8.SP.A.1, 8.EE.B.6, 8.EE.B.5,
8.F.B.4, 8.F.A.1, 8.G.B.6, 8.G.C.9, 7.G.B.6, 8.G.B.7, 8.G.B.8, 8.G.A.5).

---

## 4. Algebra I — per-standard confirmation

Every Algebra I crosswalk carries progression data; all 33 mappings below were confirmed against
the Coherence Map in both directions.

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| A.10A | HSA-APR.A.1 | 2 | 2 | ✅ Confirmed |
| A.10D | HSA-SSE.A.2 | 1 | 6 | ✅ Confirmed |
| A.10F | HSA-SSE.A.2 | 1 | 6 | ✅ Confirmed |
| A.11B | 8.EE.A.1 | 1 | 5 | ✅ Confirmed |
| A.12A | HSF-IF.A.1 | 1 | 14 | ✅ Confirmed |
| A.12B | HSF-IF.A.2 | 1 | 3 | ✅ Confirmed |
| A.12D | HSF-BF.A.2 | 4 | 0 | ✅ Confirmed |
| A.12E | HSA-CED.A.4 | 3 | 0 | ✅ Confirmed |
| A.2C | HSA-CED.A.2 | 4 | 0 | ✅ Confirmed |
| A.2E | HSG-GPE.B.5 | 3 | 0 | ✅ Confirmed |
| A.2F | HSG-GPE.B.5 | 3 | 0 | ✅ Confirmed |
| A.2I | HSA-CED.A.3 | 4 | 0 | ✅ Confirmed |
| A.3C | HSF-IF.C.7.a | 1 | 1 | ✅ Confirmed |
| A.3D | HSA-REI.D.12 | 4 | 1 | ✅ Confirmed |
| A.3E | HSF-BF.B.3 | 6 | 1 | ✅ Confirmed |
| A.3F | HSA-REI.C.6 | 3 | 3 | ✅ Confirmed |
| A.3G | HSA-REI.C.6 | 3 | 3 | ✅ Confirmed |
| A.3H | HSA-REI.D.12 | 4 | 1 | ✅ Confirmed |
| A.4A | HSS-ID.C.8 | 1 | 0 | ✅ Confirmed |
| A.4B | HSS-ID.C.9 | 2 | 0 | ✅ Confirmed |
| A.4C | HSS-ID.B.6.a | 0 | 1 | ✅ Confirmed |
| A.4C | HSS-ID.B.6.c | 3 | 0 | ✅ Confirmed |
| A.5A | HSA-REI.B.3 | 2 | 3 | ✅ Confirmed |
| A.5B | HSA-REI.B.3 | 2 | 3 | ✅ Confirmed |
| A.5C | HSA-REI.C.6 | 3 | 3 | ✅ Confirmed |
| A.7A | HSF-IF.C.7.a | 1 | 1 | ✅ Confirmed |
| A.7C | HSF-BF.B.3 | 6 | 1 | ✅ Confirmed |
| A.8A | HSA-REI.B.4.b | 3 | 4 | ✅ Confirmed |
| A.8B | HSS-ID.B.6.a | 0 | 1 | ✅ Confirmed |
| A.9C | HSF-IF.C.8.b | 3 | 0 | ✅ Confirmed |
| A.9C | HSF-LE.A.2 | 2 | 0 | ✅ Confirmed |
| A.9D | HSF-IF.C.7.e | 1 | 5 | ✅ Confirmed |
| A.9E | HSS-ID.B.6.a | 0 | 1 | ✅ Confirmed |

**Distinct Algebra I progression codes verified: 23** (HSA-APR.A.1, HSA-SSE.A.2, 8.EE.A.1,
HSF-IF.A.1, HSF-IF.A.2, HSF-BF.A.2, HSA-CED.A.4, HSA-CED.A.2, HSG-GPE.B.5, HSA-CED.A.3,
HSF-IF.C.7.a, HSA-REI.D.12, HSF-BF.B.3, HSA-REI.C.6, HSS-ID.C.8, HSS-ID.C.9, HSS-ID.B.6.a,
HSS-ID.B.6.c, HSA-REI.B.3, HSA-REI.B.4.b, HSF-IF.C.8.b, HSF-LE.A.2, HSF-IF.C.7.e).

---

## 5. Compliance conclusion

- **Total distinct Common Core progression codes across both courses: 35** (12 Grade 8 + 23 Algebra I).
- **All 35 confirmed compliant** with the Learning Commons SAP Coherence Map, in both the
  backward (prerequisite) and forward (successor) directions.
- **Discrepancies found: 0.** Every prerequisite and successor set our graph exposes matches the
  authority of record exactly.
- Standards with **no progression data** (51 in Grade 8, 37 in Algebra I) are reported honestly
  as "no data," consistent with the removal of the inferred and semantic-fallback tiers — a
  deliberate choice to never surface an unverified progression as if it were authoritative.

**Our standards-progression data is compliant with the Learning Commons.**

---

*Verification performed against the live Learning Commons knowledge-graph MCP server
(`kg.mcp.learningcommons.org`) and our production graph. Progression counts read from
`hasStandardAlignment` → `buildsTowards` edges; each distinct Common Core code confirmed by exact
set match against `find_standards_progression_from_standard`, both directions.*
