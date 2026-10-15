# Standards-Progression Compliance Report
## Full TEKS Mathematics Curriculum vs. the Learning Commons SAP Coherence Map

| | |
|---|---|
| **Corpus** | Full TEKS Mathematics curriculum — 18 courses, Kindergarten through Algebra II / Precalculus / Statistics (TEKS / Texas) |
| **Authority of record** | Learning Commons MCP — Student Achievement Partners (SAP) Coherence Map |
| **Date** | 2026-09-21 |
| **Scope** | Every standards-progression connection the curriculum graph exposes across all 18 courses, both directions |

---

## Verdict

**228 of 229 progression-bearing crosswalks confirmed — 99.6% match, 1 discrepancy found.**

Across all 18 TEKS math courses, 198 distinct Common Core codes carry a real crosswalk from a TEKS standard. Of the 258 TEKS→Common Core crosswalk rows this produces, 228 have progression data that matches Learning Commons exactly in both directions, 29 have no progression data on either side (consistent, reported honestly as such), and 1 shows a genuine mismatch — detailed in §6.

---

## 1. How this was verified

1. For every TEKS standard in all 18 courses, we followed the real crosswalk (`hasStandardAlignment` / `isStandardAlignedTo` in the source CASE package) to its Common Core code — the only progression tier the graph carries.
2. Each of the 198 distinct Common Core codes was queried against Learning Commons with `find_standards_progression_from_standard` in both `backward` and `forward` directions.
3. The **set** of codes Learning Commons returned was compared to the set our graph stores (`buildsTowards`/`precedes` edges) — order-independent, requiring an exact membership match in both directions. An empty result on their side had to line up with an empty set on ours.
4. All 198 codes were checked **exhaustively — no sampling**. The underlying CASE identifiers confirm both systems draw from the same source data, so this is a true equality check, not an approximation.

---

## 2. Coverage: total → progression → remaining, by course

Progression coverage is intentionally bounded to standards that carry a real, verified crosswalk into the Coherence Map. The remainder have no progression data — reported honestly as such rather than filled with a guess.

| Course | Total TEKS standards | With progression data | Share | Remaining — no crosswalk | Share |
|---|--:|--:|--:|--:|--:|
| Kindergarten (K) | 43 | 7 | 16.3% | 36 | 83.7% |
| Grade 1 (1) | 59 | 25 | 42.4% | 34 | 57.6% |
| Grade 2 (2) | 61 | 14 | 23.0% | 47 | 77.0% |
| Grade 3 (3) | 62 | 18 | 29.0% | 44 | 71.0% |
| Grade 4 (4) | 63 | 22 | 34.9% | 41 | 65.1% |
| Grade 5 (5) | 54 | 12 | 22.2% | 42 | 77.8% |
| Grade 6 (6) | 72 | 14 | 19.4% | 58 | 80.6% |
| Grade 7 (7) | 61 | 18 | 29.5% | 43 | 70.5% |
| Grade 8 (8) | 63 | 17 | 27.0% | 46 | 73.0% |
| Algebraic Reasoning (AR) | 41 | 0 | 0.0% | 41 | 100.0% |
| Algebra I (A) | 68 | 31 | 45.6% | 37 | 54.4% |
| Algebra II (2A) | 63 | 28 | 44.4% | 35 | 55.6% |
| Geometry (G) | 62 | 23 | 37.1% | 39 | 62.9% |
| Precalculus (P) | 62 | 0 | 0.0% | 62 | 100.0% |
| Advanced Quantitative Reasoning (AQR) | 47 | 0 | 0.0% | 47 | 100.0% |
| Mathematical Models with Applications (MMA) | 49 | 0 | 0.0% | 49 | 100.0% |
| Discrete Mathematics for Problem Solving (DM) | 72 | 0 | 0.0% | 72 | 100.0% |
| Statistics (S) | 51 | 0 | 0.0% | 51 | 100.0% |
| **Total (18 courses)** | **1053** | **229** | **21.7%** | **824** | **78.3%** |

*198 distinct Common Core codes are crosswalked across all courses; some TEKS standards crosswalk to more than one Common Core code, producing 258 total crosswalk rows.*

---

## 3. Kindergarten (K) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| K.2G | K.CC.C.6 | 0 | 2 | ✅ Confirmed |
| K.3B | K.OA.A.2 | 1 | 5 | ✅ Confirmed |
| K.6A | 2.G.A.1 | 1 | 1 | ✅ Confirmed |
| K.6B | 2.G.A.1 | 1 | 1 | ✅ Confirmed |
| K.6E | K.G.A.2 | 0 | 3 | ✅ Confirmed |
| K.7A | K.MD.A.1 | 0 | 1 | ✅ Confirmed |
| K.7B | K.MD.A.2 | 1 | 2 | ✅ Confirmed |

## 4. Grade 1 (1) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 1.2C | 1.NBT.A.1 | 1 | 1 | ✅ Confirmed |
| 1.2G | 1.NBT.B.3 | 2 | 0 | ✅ Confirmed |
| 1.3B | 1.OA.A.1 | 1 | 3 | ✅ Confirmed |
| 1.3C | K.OA.A.4 | 1 | 1 | ✅ Confirmed |
| 1.3D | 1.OA.C.6 | 7 | 2 | ✅ Confirmed |
| 1.4A | 2.MD.C.8 | — | — | — no progression |
| 1.5A | 1.NBT.A.1 | 1 | 1 | ✅ Confirmed |
| 1.5B | 1.NBT.A.1 | 1 | 1 | ✅ Confirmed |
| 1.5B | 2.NBT.A.2 | 0 | 1 | ✅ Confirmed |
| 1.5C | 1.NBT.C.5 | 1 | 2 | ✅ Confirmed |
| 1.5E | 1.OA.D.7 | 0 | 3 | ✅ Confirmed |
| 1.5F | 1.OA.D.8 | 1 | 2 | ✅ Confirmed |
| 1.5G | 1.OA.B.3 | 1 | 7 | ✅ Confirmed |
| 1.6A | 1.G.A.1 | 2 | 1 | ✅ Confirmed |
| 1.6B | 1.G.A.1 | 2 | 1 | ✅ Confirmed |
| 1.6C | 1.G.A.1 | 2 | 1 | ✅ Confirmed |
| 1.6C | 2.G.A.1 | 1 | 1 | ✅ Confirmed |
| 1.6D | 2.G.A.1 | 1 | 1 | ✅ Confirmed |
| 1.6E | 2.G.A.1 | 1 | 1 | ✅ Confirmed |
| 1.6F | 1.G.A.2 | 1 | 3 | ✅ Confirmed |
| 1.6F | K.G.B.6 | 3 | 1 | ✅ Confirmed |
| 1.6G | 1.G.A.3 | 1 | 1 | ✅ Confirmed |
| 1.6G | 2.G.A.3 | 2 | 1 | ✅ Confirmed |
| 1.7A | 2.MD.A.1 | 1 | 3 | ✅ Confirmed |
| 1.7B | 1.MD.A.2 | 1 | 1 | ✅ Confirmed |
| 1.7C | 2.MD.A.2 | 2 | 1 | ✅ Confirmed |
| 1.7E | 1.MD.B.3 | 0 | 1 | ✅ Confirmed |
| 1.8A | 1.MD.C.4 | 3 | 1 | ✅ Confirmed |
| 1.8B | 2.MD.D.10 | 1 | 0 | ✅ Confirmed |

## 5. Grade 2 (2) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 2.10B | 2.MD.D.10 | 1 | 0 | ✅ Confirmed |
| 2.3A | 2.G.A.3 | 2 | 1 | ✅ Confirmed |
| 2.3C | 2.G.A.3 | 2 | 1 | ✅ Confirmed |
| 2.4A | 2.OA.B.2 | 1 | 1 | ✅ Confirmed |
| 2.5A | 2.MD.C.8 | — | — | — no progression |
| 2.6A | 3.OA.A.1 | 2 | 8 | ✅ Confirmed |
| 2.6B | 3.OA.A.2 | 1 | 5 | ✅ Confirmed |
| 2.8A | 2.G.A.1 | 1 | 1 | ✅ Confirmed |
| 2.8C | 2.G.A.1 | 1 | 1 | ✅ Confirmed |
| 2.9A | 2.MD.A.1 | 1 | 3 | ✅ Confirmed |
| 2.9B | 2.MD.A.2 | 2 | 1 | ✅ Confirmed |
| 2.9C | 2.MD.B.6 | 0 | 1 | ✅ Confirmed |
| 2.9D | 2.MD.A.1 | 1 | 3 | ✅ Confirmed |
| 2.9F | 3.MD.C.7.a | 1 | 2 | ✅ Confirmed |

## 6. Grade 3 (3) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 3.2C | 3.NBT.A.1 | 1 | 1 | ✅ Confirmed |
| 3.3A | 3.NF.A.2.a | — | — | — no progression |
| 3.3A | 3.NF.A.2.b | — | — | — no progression |
| 3.3E | 3.G.A.2 | 1 | 0 | ✅ Confirmed |
| 3.3G | 3.NF.A.3.a | — | — | — no progression |
| 3.3H | 3.NF.A.3.d | — | — | — no progression |
| 3.4F | 3.OA.C.7 | 2 | 4 | ✅ Confirmed |
| 3.4J | 3.OA.B.6 | 0 | 5 | ✅ Confirmed |
| 3.4K | 3.OA.A.3 | 2 | 4 | ✅ Confirmed |
| 3.5D | 3.OA.A.4 | 0 | 1 | ✅ Confirmed |
| 3.6B | 3.G.A.1 | 1 | 2 | ✅ Confirmed |
| 3.6C | 3.MD.C.7.b | 1 | 3 | ✅ Confirmed |
| 3.6D | 3.MD.C.7.d | 2 | 0 | ✅ Confirmed |
| 3.6E | 2.G.A.3 | 2 | 1 | ✅ Confirmed |
| 3.7B | 3.MD.D.8 | 1 | 1 | ✅ Confirmed |
| 3.7C | 3.MD.A.1 | — | — | — no progression |
| 3.7E | 3.MD.A.2 | 0 | 1 | ✅ Confirmed |
| 3.8A | 3.MD.B.3 | — | — | — no progression |
| 3.8B | 3.MD.B.3 | — | — | — no progression |

## 7. Grade 4 (4) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 4.2A | 4.NBT.A.1 | 1 | 6 | ✅ Confirmed |
| 4.2A | 5.NBT.A.1 | 4 | 6 | ✅ Confirmed |
| 4.2F | 4.NF.C.7 | 2 | 2 | ✅ Confirmed |
| 4.2G | 4.NF.C.6 | 0 | 3 | ✅ Confirmed |
| 4.3A | 4.NF.B.4.a | 3 | 1 | ✅ Confirmed |
| 4.3B | 4.NF.B.3.b | 6 | 4 | ✅ Confirmed |
| 4.3D | 4.NF.A.2 | 1 | 2 | ✅ Confirmed |
| 4.3E | 4.NF.B.3.d | 4 | 1 | ✅ Confirmed |
| 4.3F | 5.NF.A.2 | 2 | 1 | ✅ Confirmed |
| 4.4D | 4.NBT.B.5 | 5 | 2 | ✅ Confirmed |
| 4.5A | 4.OA.A.3 | 3 | 1 | ✅ Confirmed |
| 4.5B | 4.OA.C.5 | 1 | 1 | ✅ Confirmed |
| 4.5D | 4.MD.A.3 | 3 | 2 | ✅ Confirmed |
| 4.6A | 4.G.A.1 | 1 | 1 | ✅ Confirmed |
| 4.6B | 4.G.A.3 | 1 | 0 | ✅ Confirmed |
| 4.6C | 4.G.A.2 | 1 | 1 | ✅ Confirmed |
| 4.6D | 4.G.A.2 | 1 | 1 | ✅ Confirmed |
| 4.7B | 4.MD.C.5.a | — | — | — no progression |
| 4.7B | 4.MD.C.5.b | — | — | — no progression |
| 4.7C | 4.MD.C.6 | 1 | 0 | ✅ Confirmed |
| 4.7D | 4.MD.C.6 | 1 | 0 | ✅ Confirmed |
| 4.8A | 4.MD.A.1 | 2 | 3 | ✅ Confirmed |
| 4.8B | 4.MD.A.1 | 2 | 3 | ✅ Confirmed |
| 4.8C | 4.MD.A.2 | 7 | 4 | ✅ Confirmed |

## 8. Grade 5 (5) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 5.2A | 5.NBT.A.3.a | — | — | — no progression |
| 5.2B | 5.NBT.A.3.b | — | — | — no progression |
| 5.3B | 5.NBT.B.5 | 3 | 2 | ✅ Confirmed |
| 5.3E | 5.NBT.B.7 | 5 | 2 | ✅ Confirmed |
| 5.3H | 5.NF.A.1 | 5 | 4 | ✅ Confirmed |
| 5.3L | 5.NF.B.7.a | — | — | — no progression |
| 5.3L | 5.NF.B.7.b | — | — | — no progression |
| 5.4B | 4.OA.A.3 | 3 | 1 | ✅ Confirmed |
| 5.4F | 5.OA.A.1 | 0 | 1 | ✅ Confirmed |
| 5.5 | 5.G.B.4 | 1 | 0 | ✅ Confirmed |
| 5.6A | 5.MD.C.3.a | — | — | — no progression |
| 5.6A | 5.MD.C.3.b | — | — | — no progression |
| 5.8A | 5.G.A.1 | 1 | 2 | ✅ Confirmed |
| 5.8C | 5.G.A.2 | 1 | 3 | ✅ Confirmed |
| 5.8C | 5.OA.B.3 | 1 | 3 | ✅ Confirmed |

## 9. Grade 6 (6) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 6.10B | 6.EE.B.5 | 1 | 4 | ✅ Confirmed |
| 6.12A | 6.SP.B.4 | 1 | 1 | ✅ Confirmed |
| 6.12B | 6.SP.A.2 | 1 | 7 | ✅ Confirmed |
| 6.12C | 6.SP.B.5.c | — | — | — no progression |
| 6.2C | 6.NS.C.6.c | 2 | 3 | ✅ Confirmed |
| 6.2D | 6.NS.C.7.b | 1 | 2 | ✅ Confirmed |
| 6.2E | 5.NF.B.3 | 6 | 2 | ✅ Confirmed |
| 6.4B | 6.RP.A.3 | 0 | 3 | ✅ Confirmed |
| 6.4C | 6.RP.A.1 | 4 | 2 | ✅ Confirmed |
| 6.4D | 6.RP.A.2 | 4 | 5 | ✅ Confirmed |
| 6.5B | 6.RP.A.3.c | 1 | 0 | ✅ Confirmed |
| 6.6B | 6.EE.C.9 | 1 | 0 | ✅ Confirmed |
| 6.7C | 6.EE.A.4 | 5 | 1 | ✅ Confirmed |
| 6.7D | 6.EE.A.3 | 5 | 1 | ✅ Confirmed |

## 10. Grade 7 (7) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 7.10A | 7.EE.B.4.a | 3 | 3 | ✅ Confirmed |
| 7.10A | 7.EE.B.4.b | 3 | 0 | ✅ Confirmed |
| 7.10B | 7.EE.B.4.b | 3 | 0 | ✅ Confirmed |
| 7.11B | 6.EE.B.5 | 1 | 4 | ✅ Confirmed |
| 7.11C | 7.G.B.5 | 1 | 2 | ✅ Confirmed |
| 7.12B | 7.SP.A.2 | 1 | 2 | ✅ Confirmed |
| 7.12C | 7.SP.B.4 | 2 | 2 | ✅ Confirmed |
| 7.3A | 7.NS.A.1.d | 3 | 2 | ✅ Confirmed |
| 7.3A | 7.NS.A.2.c | 2 | 1 | ✅ Confirmed |
| 7.3B | 7.EE.B.3 | 1 | 1 | ✅ Confirmed |
| 7.3B | 7.NS.A.3 | 5 | 3 | ✅ Confirmed |
| 7.4B | 7.RP.A.1 | 1 | 3 | ✅ Confirmed |
| 7.4C | 7.RP.A.2.b | — | — | — no progression |
| 7.4D | 7.RP.A.3 | 2 | 5 | ✅ Confirmed |
| 7.4E | 6.RP.A.3.d | 1 | 0 | ✅ Confirmed |
| 7.5C | 7.G.A.1 | 2 | 2 | ✅ Confirmed |
| 7.6A | 7.SP.C.8.b | — | — | — no progression |
| 7.6B | 7.SP.C.8.c | — | — | — no progression |
| 7.6F | 7.SP.A.2 | 1 | 2 | ✅ Confirmed |
| 7.9B | 7.G.B.4 | 1 | 4 | ✅ Confirmed |
| 7.9D | 6.G.A.4 | 1 | 1 | ✅ Confirmed |

## 11. Grade 8 (8) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 8.10C | 8.G.A.3 | 2 | 3 | ✅ Confirmed |
| 8.11A | 8.SP.A.1 | 1 | 3 | ✅ Confirmed |
| 8.2B | 8.NS.A.2 | — | — | — no progression |
| 8.4A | 8.EE.B.6 | 3 | 5 | ✅ Confirmed |
| 8.4B | 8.EE.B.5 | 1 | 2 | ✅ Confirmed |
| 8.4C | 8.F.B.4 | 2 | 8 | ✅ Confirmed |
| 8.5A | 7.RP.A.2.c | — | — | — no progression |
| 8.5F | 7.RP.A.2.a | — | — | — no progression |
| 8.5G | 8.F.A.1 | 1 | 5 | ✅ Confirmed |
| 8.5I | 8.EE.B.6 | 3 | 5 | ✅ Confirmed |
| 8.6C | 8.G.B.6 | 1 | 1 | ✅ Confirmed |
| 8.7A | 8.G.C.9 | 1 | 5 | ✅ Confirmed |
| 8.7B | 7.G.B.6 | 3 | 3 | ✅ Confirmed |
| 8.7C | 8.G.B.6 | 1 | 1 | ✅ Confirmed |
| 8.7C | 8.G.B.7 | 0 | 4 | ✅ Confirmed |
| 8.7D | 8.G.B.8 | 2 | 5 | ✅ Confirmed |
| 8.8D | 8.G.A.5 | 2 | 4 | ✅ Confirmed |
| 8.9 | 8.EE.C.8.b | — | — | — no progression |

## 12. Algebra I (A) — per-standard confirmation

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

## 13. Algebra II (2A) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| 2A.2A | HSF-IF.C.7 | — | — | — no progression |
| 2A.2A | HSF-IF.C.7.b | 1 | 1 | ✅ Confirmed |
| 2A.2A | HSF-IF.C.7.d | 1 | 1 | ✅ Confirmed |
| 2A.2A | HSF-IF.C.7.e | 1 | 5 | ✅ Confirmed |
| 2A.2B | HSF-BF.B.4 | 3 | 1 | ✅ Confirmed |
| 2A.2C | HSF-BF.B.4 | 3 | 1 | ✅ Confirmed |
| 2A.2C | HSF-BF.B.4.c | — | — | — no progression |
| 2A.2D | HSF-BF.B.4 | 3 | 1 | ✅ Confirmed |
| 2A.2D | HSF-BF.B.4.b | — | — | — no progression |
| 2A.3A | HSA-CED.A.3 | 4 | 0 | ✅ Confirmed |
| 2A.3C | HSA-REI.C.7 | 5 | 0 | ⚠️ DISCREPANCY |
| 2A.3E | HSA-CED.A.3 | 4 | 0 | ✅ Confirmed |
| 2A.4B | HSG-GPE.A.2 | 2 | 0 | ✅ Confirmed |
| 2A.4C | HSF-BF.B.3 | 6 | 1 | ✅ Confirmed |
| 2A.4D | HSF-IF.C.8.a | 1 | 0 | ✅ Confirmed |
| 2A.4F | HSA-REI.A.2 | 2 | 1 | ✅ Confirmed |
| 2A.4F | HSA-REI.B.4.b | 3 | 4 | ✅ Confirmed |
| 2A.5A | HSF-BF.B.3 | 6 | 1 | ✅ Confirmed |
| 2A.5D | HSF-LE.A.4 | 0 | 1 | ✅ Confirmed |
| 2A.6A | HSF-BF.B.3 | 6 | 1 | ✅ Confirmed |
| 2A.6B | HSA-REI.A.2 | 2 | 1 | ✅ Confirmed |
| 2A.6C | HSF-BF.B.3 | 6 | 1 | ✅ Confirmed |
| 2A.6D | HSA-CED.A.1 | 6 | 0 | ✅ Confirmed |
| 2A.6G | HSF-BF.B.3 | 6 | 1 | ✅ Confirmed |
| 2A.6H | HSA-CED.A.1 | 6 | 0 | ✅ Confirmed |
| 2A.6H | HSA-CED.A.2 | 4 | 0 | ✅ Confirmed |
| 2A.6I | HSA-REI.A.2 | 2 | 1 | ✅ Confirmed |
| 2A.7A | HSN-CN.A.2 | 2 | 3 | ✅ Confirmed |
| 2A.7B | HSA-APR.A.1 | 2 | 2 | ✅ Confirmed |
| 2A.7C | HSA-APR.D.6 | 1 | 2 | ✅ Confirmed |
| 2A.7D | HSA-APR.B.2 | 2 | 2 | ✅ Confirmed |
| 2A.7E | HSA-SSE.A.2 | 1 | 6 | ✅ Confirmed |
| 2A.7F | HSA-APR.D.7 | 3 | 0 | ✅ Confirmed |
| 2A.7G | HSN-RN.A.2 | 1 | 3 | ✅ Confirmed |
| 2A.8A | HSS-ID.B.6.a | 0 | 1 | ✅ Confirmed |
| 2A.8A | HSS-ID.B.6.b | 2 | 1 | ✅ Confirmed |

## 14. Geometry (G) — per-standard confirmation

| TEKS | Common Core | Prereqs | Successors | Learning Commons |
|---|---|--:|--:|---|
| G.10A | HSG-GMD.B.4 | 1 | 1 | ✅ Confirmed |
| G.11D | HSG-GMD.A.3 | 2 | 0 | ✅ Confirmed |
| G.12A | HSG-C.A.2 | 1 | 1 | ✅ Confirmed |
| G.12D | HSF-TF.A.1 | 2 | 1 | ✅ Confirmed |
| G.12E | HSG-GPE.A.1 | 3 | 2 | ✅ Confirmed |
| G.13A | HSS-CP.B.9 | 1 | 0 | ✅ Confirmed |
| G.13C | HSS-CP.A.2 | 2 | 0 | ✅ Confirmed |
| G.13C | HSS-CP.B.8 | 1 | 0 | ✅ Confirmed |
| G.13D | HSS-CP.B.6 | 3 | 1 | ✅ Confirmed |
| G.13E | HSS-CP.A.5 | — | — | — no progression |
| G.2A | HSG-GPE.B.6 | 1 | 0 | ✅ Confirmed |
| G.3A | HSG-CO.A.2 | 5 | 2 | ✅ Confirmed |
| G.3A | HSG-CO.A.5 | 4 | 2 | ✅ Confirmed |
| G.3C | HSG-CO.A.5 | 4 | 2 | ✅ Confirmed |
| G.5A | HSG-CO.B.7 | 1 | 1 | ✅ Confirmed |
| G.5A | HSG-CO.B.8 | 3 | 2 | ✅ Confirmed |
| G.5B | HSG-CO.D.12 | 1 | 3 | ✅ Confirmed |
| G.6A | HSG-CO.C.9 | 3 | 4 | ✅ Confirmed |
| G.6B | HSG-CO.B.8 | 3 | 2 | ✅ Confirmed |
| G.6C | HSG-CO.B.6 | 2 | 3 | ✅ Confirmed |
| G.6D | HSG-CO.C.10 | 3 | 3 | ✅ Confirmed |
| G.6D | HSG-SRT.B.4 | 3 | 0 | ✅ Confirmed |
| G.6E | HSG-CO.C.11 | 2 | 1 | ✅ Confirmed |
| G.7A | HSG-SRT.A.2 | 2 | 3 | ✅ Confirmed |
| G.7B | HSG-SRT.B.4 | 3 | 0 | ✅ Confirmed |
| G.8A | HSG-SRT.B.4 | 3 | 0 | ✅ Confirmed |
| G.8A | HSG-SRT.B.5 | 2 | 3 | ✅ Confirmed |
| G.9A | HSG-SRT.C.8 | 3 | 2 | ✅ Confirmed |

---

## 15. Discrepancy detail

### 2A.3C → HSA-REI.C.7

- Our graph's local edges: prerequisites = ['8.EE.C.8', 'HSA-REI.B.4.b', 'HSA-REI.C.6', 'HSA-REI.D.10', 'HSG-GPE.A.1'], successors = (none)
- Learning Commons: prerequisites = (none), successors = ['8.EE.C.8', 'HSA-REI.B.4.b', 'HSA-REI.C.6', 'HSA-REI.D.10', 'HSG-GPE.A.1']

This is a **direction inversion**, not a missing/extra edge: the same five codes appear on both sides, just swapped between prerequisite and successor. Notably, Learning Commons' own data is internally inconsistent here — querying `HSA-REI.C.6` (one of the five) independently shows `HSA-REI.C.7` as ITS successor, while querying `HSA-REI.C.7` shows `HSA-REI.C.6` as ITS successor too. Both codes claim to precede each other in the live Coherence Map data, which is the root cause of the mismatch rather than an error in our graph's `precedes` edges.

---

## 16. Compliance conclusion

- **Total distinct Common Core progression codes across all 18 courses: 198.**
- **228 of 229 confirmed compliant** with the Learning Commons SAP Coherence Map, in both the backward (prerequisite) and forward (successor) directions.
- **Discrepancies found: 1** (2A.3C→HSA-REI.C.7), traced to a bidirectional inconsistency in the Learning Commons source data itself (see §6), not a defect in our graph.
- Standards with **no crosswalk** (824 of 1053 total TEKS standards) are reported honestly as "no data," consistent with never surfacing an unverified progression as if it were authoritative.
- Six courses — Advanced Quantitative Reasoning, Algebraic Reasoning, Discrete Mathematics for Problem Solving, Mathematical Models with Applications, Precalculus, and Statistics — currently carry zero Common Core crosswalk edges in the source data, so 100% of their standards fall in the "no progression data" category. This is a data-coverage gap upstream of this audit, not a compliance failure.

**Our standards-progression data is compliant with Learning Commons across the full curriculum**, with one flagged discrepancy attributable to source-data inconsistency on the Learning Commons side.

---

*Verification performed against the live Learning Commons knowledge-graph MCP server (`kg.mcp.learningcommons.org`) and the curriculum-copilot production graph (`teks_math_case.json`). Progression counts read from `isStandardAlignedTo`/`hasStandardAlignment` → `precedes`/`buildsTowards` edges; each of the 198 distinct Common Core codes confirmed by exact set match against `find_standards_progression_from_standard`, both directions, no sampling.*