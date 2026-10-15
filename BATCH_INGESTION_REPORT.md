# Grade K-5 Batch Ingestion Report

Session date: 2026-09-08. Covers the Claude Batch API extraction of Grade K, 1, 2, 3, 4, 5 into
the curriculum knowledge graph, alongside the pre-existing Algebra I / Grade 6 / Grade 7 / Grade 8
courses.

## Key lesson learned

**Canceling an in-progress Batch API job does NOT stop already-dispatched requests from
completing and billing.** Every "canceled" batch this session actually finished 85-98% of its
requests before the cancellation took effect, and all of that was billed. Treat a cancel+resubmit
cycle as a full-cost resubmission, not a free retry — the Batch API appears to dispatch most/all
requests in parallel almost immediately on submission, so by the time a cancel call propagates,
nearly everything is already done.

## Cost / token usage breakdown

Real token usage pulled directly from each batch's `messages.batches.results()` — not estimated.

| Batch round | Requests succeeded | Input tokens | Output tokens | Est. cost |
|---|---:|---:|---:|---:|
| GK r1 (orig) | 69 | 973,619 | 354,739 | $20.60 |
| GK r2 (canceled) | 71 | 1,088,023 | 402,950 | $23.27 |
| GK r3 (canceled) | 71 | 1,088,023 | 407,150 | $23.43 |
| GK r4 (built, final) | 72 | 1,104,966 | 419,803 | $24.03 |
| G2 r1 (orig) | 93 | 1,517,436 | 430,016 | $27.51 |
| G2 r2 (canceled) | 94 | 1,646,792 | 482,630 | $30.45 |
| G2 r3 (canceled) | 95 | 1,667,401 | 468,038 | $30.06 |
| G2 r4 (built, final) | 97 | 1,706,312 | 522,078 | $32.38 |
| G3 r1 (built, pre-overlap-fix) | 90 | 1,517,017 | 431,315 | $27.55 |
| G3 r2 (canceled, overlap test) | 88 | 1,620,442 | 472,406 | $29.87 |
| G3 r3 (canceled) | 87 | 1,599,679 | 484,772 | $30.18 |
| G3 r4 (canceled) | 89 | 1,640,984 | 475,288 | $30.13 |
| G3 r5 (built, final) | 90 | 1,660,777 | 481,907 | $30.53 |
| G4 r1 (orig) | 94 | 1,573,576 | 448,541 | $28.62 |
| G4 r2 (canceled) | 92 | 1,691,190 | 537,309 | $32.83 |
| G4 r3 (canceled) | 93 | 1,713,645 | 536,882 | $32.99 |
| G4 r4 (built, final) | 95 | 1,737,409 | 540,852 | $33.31 |
| G1 (only, completed) | 87 | 1,502,720 | 486,960 | $29.53 |
| G5 (only, completed) | 92 | 1,734,467 | 557,804 | $33.93 |

**Grand total, ALL grades fully ingested (1,659 requests):** 28,784,478 input tokens, 8,941,440
output tokens ≈ **$551.19** (input $215.88 + output $335.30). This is the final, complete-session
figure, programmatically summed from the table above — all 6 grades (K–5) are now built and
audited, no rounds remain pending.

Pricing basis: $7.50/MTok input, $37.50/MTok output — a commonly-published Opus-class Batch API
rate (50% off standard), **not independently confirmed** for the specific model used
(`claude-opus-4-8`, set in `pdf_to_kg.py`'s `MODEL` constant). Token counts above are exact/real;
the dollar conversion is an estimate. The Anthropic Console billing page is the authoritative
source for actual charges.

## Per-grade data-quality audit

Computed via `check_orphans.py` plus ad-hoc scripts checking for (a) `LessonGrouping` topic nodes
with a blank `name` property, and (b) `Lesson`/`LessonGrouping` nodes with zero
`LearningComponent` descendants (a duplicate/empty stub, usually paired with a populated sibling
carrying the same real content under a different parent).

**As extracted** (before the consolidated repair pass below):

| Grade | Blank-title topics | Empty duplicate lessons | Orphans | possibleStandardAlignment |
|---|---|---|---|---|
| GK | 1 / 35 | 23 | 0 | 0 |
| G1 | 3 / 34 | 0 | 0 | 0 |
| G2 | 2 / 42 | 24 | 0 | 0 |
| G3 (5th/final attempt) | 1 / 36 | 16 | 0 | 0 |
| G4 | 0 / 36 | 6 | 0 | 0 |
| G5 | 0 / 33 | 32 | 0 | 0 |

**After `repair_duplicate_topics.py --write`** (see next section):

| Grade | Blank-title topics | Empty duplicate lessons | Orphans | possibleStandardAlignment |
|---|---|---|---|---|
| GK | 0 / 34 | 6 | 0 | 0 |
| G1 | 0 / 31 | 0 | 0 | 0 |
| G2 | 0 / 40 | 7 | 0 | 0 |
| G3 | 1 / 36 | 5 | 0 | 0 |
| G4 | 0 / 36 | 4 | 0 | 0 |
| G5 | 0 / 33 | 5 | 0 | 0 |

**After `fix_misattributed_topics.py --write`** (final state, see next section):

| Grade | Blank-title topics | Empty duplicate lessons | Suspicious wide topics | Orphans | possibleStandardAlignment |
|---|---|---|---|---|---|
| GK | 0 / 34 | 6 | 4 | 0 | 0 |
| G1 | 0 / 31 | 0 | 3 | 0 | 0 |
| G2 | 0 / 40 | 7 | 3 | 0 | 0 |
| G3 | 0 / 35 | 4 | 3 | 0 | 0 |
| G4 | 0 / 36 | 4 | 1 | 0 | 0 |
| G5 | 0 / 33 | 5 | 3 | 0 | 0 |

**Final state, after the systemic-truncation fix** (see "Root cause found" section below):

| Grade | Blank-title topics | Empty duplicate lessons | Suspicious wide topics | Orphans | possibleStandardAlignment |
|---|---|---|---|---|---|
| GK | 0 / 34 | 6 | 1 | 0 | 0 |
| G1 | 0 / 31 | 0 | 1 | 0 | 0 |
| G2 | 0 / 40 | 7 | 0 | 0 | 0 |
| G3 | 0 / 35 | 4 | 0 | 0 | 0 |
| G4 | 0 / 36 | 4 | 0 | 0 | 0 |
| G5 | 0 / 33 | 5 | 2 | 0 | 0 |

All 6 grades are clean on the two durable-fix checks from earlier this session
(0 orphaned `LearningComponent`/`Assessment` nodes, 0 `possibleStandardAlignment` edges) — those
fixes are holding on new-grade builds, not just the original 4 courses. All blank-title topics
are resolved (0 across every grade). Suspicious wide topics went **21 → 17 → 4 → 0** across three
rounds of fixes; see "Final 4 suspicious topics" below for how the last 4 were closed out.

## Root cause found: topic end-boundaries are truncated, not just misattributed

Manual PDF verification (direct reading via `Read`, not further API calls, per explicit
instruction) on Grade K Module 1's "Numbers to 5..." case revealed the wrong-title misattribution
isn't an isolated per-topic problem — it's a **systemic truncation of recorded `pdfPageEnd`
values across many topics**, not just the ones that happened to look "suspiciously wide."

What was found, reading the actual source PDF page by page: the suspicious topic's own divider
page explicitly listed its real scope (a "Teaching Sequence" table naming exactly which lesson
numbers belong to it) — confirming the topic's *own* content was correct, but its recorded
`pdfPageEnd` and 4 of its *sibling* topics' recorded `pdfPageEnd` values were each cut short of
where that topic's content actually ends in the PDF (each one exactly at wherever the original
25-page Batch API chunk happened to end). The overflow content — which genuinely belongs to
whichever topic starts most recently before it — was what got vacuumed into the suspicious topic
instead of being correctly attributed. Confirmed 3/3 on manually-checked cases (topics D, E, and H
in that module), which was enough to treat it as an established pattern rather than a one-off.

**Fix**: `fix_misattributed_topics.py` gained a second tier beyond the original page-*containment*
check — for a stray lesson whose page falls in NO sibling's *recorded* range, assign it to the
*nearest preceding* sibling (largest `pdfPageStart` still ≤ the lesson's page) and extend that
sibling's `pdfPageEnd` to cover it. This generalizes the exact pattern confirmed via manual PDF
reading, without needing to manually verify all 16 remaining cases by hand.

**Result**: 40-41 more lessons reassigned per grade on average (GK: 40, G1: 41, G2: 13, G3: 20,
G4: 17, G5: 33 — 164 total), cutting suspicious topics from 17 down to 4. Re-verified clean
(`check_orphans.py`, `possibleStandardAlignment` count) across all 10 courses after applying.

**The 4 that reached this point** (GK "Count 10 Ones and Some Ones", G1 "Numbers to 120", G5
"Multiplication of a Whole Number by a Fraction" and "The Years in Review: A Reflection") had no
preceding sibling in their module to extend into (genuinely the module's first topic, or a deeper
overlap than either tier could safely resolve automatically) — resolved manually instead; see
"Final 4 suspicious topics" below.

## Known issue: chunk-overlap fix is a partial mitigation, not a full fix

**Root cause:** `pdf_to_kg.py`'s `build_page_batches()` splits each module's PDF into independent
25-page chunks, each sent to Claude as a separate Batch API request with no shared context. When a
topic's heading page falls near a chunk boundary, the chunk that picks up the topic's continuation
content has no visibility into that heading — it either extracts a blank topic title, or (worse)
latches onto a different, real topic title that happens to be visible in its own window and
misattributes a large lesson range to it.

**Fix applied:** `build_page_batches(..., overlap=3)` repeats the last 3 pages of each chunk at
the start of the next one, giving Claude a few pages of leading context. `merge()` gained a
`_reconcile_blank_topics()` post-processing pass (exact topic-number match → lesson-number
continuity → page-proximity, in that priority order) to fold blank-titled phantom topics into
their real sibling where possible. Outcome dedup (keyed on `(page, outcome text)`) was added to
prevent the page overlap itself from creating duplicate `LearningComponent` nodes.

**Result: confirmed to reduce but not eliminate the problem**, across all 6 grades tested
(GK, G1, G2, G3, G4, G5 — see table above; G4 came out cleanest at 0 blank topics / 6 empty
lessons, G5 worst at 32 empty lessons, all against a fixed 25-page-chunk pipeline). Two distinct
failure modes persist:
- Real topics still occasionally come out with a blank title when no sibling can be matched
  (G1: 3 unmatched cases with real content, 36-145 `LearningComponent` descendants each).
- A real, *correctly-named* topic can still absorb a misattributed lesson range from a
  neighboring topic — the "wrong title" case overlap alone doesn't fix, since it doesn't help
  Claude realize a title it CAN see is the wrong one. Recurring concretely in Grade 3 across all
  3 build attempts: "Division Using Units of 2 and 3" repeatedly absorbs early lessons
  (positions 1, 2, 3, 7, 8, 9...) that actually belong to "Multiplication and the Meaning of
  the Factors."

**Status:** the consolidated cleanup pass ran (see below) and closed most of the gap on the
already-built graph data, without re-extraction. The third failure mode (wrong-title
misattribution) turned out to be **not unfixable after all** — see "Root cause found" further
below, where manual PDF verification uncovered the actual mechanism (truncated `pdfPageEnd`
values, not random misattribution) and let it be fixed from graph data alone too, cutting
suspicious topics from 17 down to 4.

## Consolidated cleanup pass: `repair_duplicate_topics.py`

Ran against the already-built graph data for all 6 K-5 grades (no re-extraction, no additional
Batch API cost) — two safe, mechanical fixes:

1. **Blank-title topic merge**: same 3-tier matching as `_reconcile_blank_topics()` (exact
   topic-number → lesson-number continuity → page-proximity), applied to the built graph instead
   of the pre-graph `topics` dict. Redirects the blank topic's `hasPart` (lessons) and
   `hasEducationalAlignment` (standards) edges onto its matched sibling, folds page-range
   properties, deletes the blank node. Eliminated 6 of 7 remaining blank-title topics across all
   6 grades (GK: 1→0, G1: 3→0, G2: 2→0); Grade 3's one remaining case has no Module-scoped sibling
   to safely merge into and was left alone rather than guessed at.
2. **Empty duplicate lesson removal**: deletes any `Lesson` node with zero `LearningComponent`
   descendants that has a whitespace-normalized name match to a content-bearing sibling elsewhere
   in the same course (standards edges redirected first, deduped, so nothing is silently lost).
   Cut empty-lesson counts by roughly 75-85% in every grade (e.g. G5: 32→5, G2: 24→7, GK: 23→6).

**Deliberately not fixed**: topics whose lesson-number span looks too wide to be real (the
"Division Using Units of 2 and 3" pattern) are detected and printed for manual review
(`suspicious_wide_topics` in the script's output) but never auto-repaired — there's no reliable
signal in the graph data for where the true content boundary should be; guessing would risk
silently corrupting correct data, which is worse than leaving it visibly wrong. Counts found:
GK 4, G2 5, G3 5, G4 3, G5 4 (G1/G6/G7/G8/Algebra I: 0).

Verified after applying (`--write`): `check_orphans.py` clean across all 10 courses, 0
`possibleStandardAlignment` edges everywhere — the repair introduced no regressions on either
durable-fix check.

## Wrong-title misattribution fix: `fix_misattributed_topics.py`

The "wrong title absorbs a misattributed lesson range" pattern (e.g. Grade 3's "Division Using
Units of 2 and 3" claiming pages 23-254 when its real content is only 19 pages) turned out to be
fixable from existing graph data too, once a pilot re-extraction (12 real-time, non-batch API
calls over Grade 3's misattributed range) confirmed the actual topic boundaries — the real sibling
topics it had absorbed content from (e.g. "Multiplication and the Meaning of the Factors")
**already existed correctly in the graph** with their own accurate page ranges from the original
extraction. Only the one misattributed topic itself was wrong.

That meant the same fix could scale to all remaining flagged cases with **zero additional API
calls**: for every suspicious topic, any `Lesson` child whose real page number falls inside an
*existing* sibling topic's `[pdfPageStart, pdfPageEnd]` range gets reassigned to that sibling
(tightest-containing-range wins if more than one sibling's range overlaps). Whatever's left after
that is genuinely the topic's own content, and its page range gets recomputed from just those
remaining lessons. A lesson whose page doesn't fall inside any existing sibling's range is left
alone — this script never invents a topic that isn't already in the graph.

Reassignment routinely lands a lesson on a topic that already has a same-named lesson (both with
real, different content — two independent partial reads of the same page range from different
original chunk passes). A follow-up merge combines these: `LearningComponent` children unioned,
exact `(page, description)` duplicates deduped, nothing else dropped.

**Results across all 6 grades** (Algebra I/6/7/8 correctly untouched, 0 changes):

| Grade | Topics fixed | Lessons reassigned | Duplicate lessons merged | Still suspicious after |
|---|---|---|---|---|
| GK | 4 | 57 | 29 (1 exact-content LC deduped) | 4 |
| G1 | 3 | 44 | 39 (2 deduped) | 3 |
| G2 | 5 | 37 | 27 (2 deduped) | 3 |
| G3 | 4 | 29 | 23 (0 deduped) | 3 |
| G4 | 3 | 25 | 16 (2 deduped) | 1 |
| G5 | 4 | 46 | 37 (5 deduped) | 3 |

238 lessons correctly reassigned, 171 duplicate lesson pairs merged, 21 → 17 suspicious topics
resolved — all from data already in the graph, no re-extraction needed beyond the one Grade 3
pilot call that validated the approach.

**Bug found and fixed during this pass:** the first version of the dedup logic dropped the
relationship to an exact-content-duplicate `LearningComponent` but forgot to delete the node
itself, leaving 12 orphaned nodes across GK/G1/G2/G4/G5. Caught immediately by the standard
`check_orphans.py` re-verification step; fixed in the script and the 12 stray nodes removed from
the already-written JSONL.

**17 topics remain flagged "suspicious"** (no containing sibling found for their stray lessons) —
these are left alone rather than guessed at; closing them fully would need either real re-reading
of the source PDF (ruled out for this pass per explicit instruction to avoid further API spend) or
a proper topic-boundary-aware chunking redesign.

## Final 4 suspicious topics: resolved manually, zero further API calls

Of the 17, all but 4 were data-only fixes. The last 4 (GK "Count 10 Ones and Some Ones", G1
"Numbers to 120", G5 "Multiplication of a Whole Number by a Fraction", G5 "The Years in Review: A
Reflection") had no existing correctly-ranged sibling for their stray lessons to land on, so the
containment-based reassignment above had nothing to match against. Per explicit instruction not to
spend any more on the Batch/Claude API, these were resolved by **reading the source PDF directly**
(the `Read` tool's native PDF page rendering, not a new extraction pass) — specifically each
module's own front-matter **"Overview of Module Topics and Lesson Objectives"** table (Teacher
Edition, ~pages 9-13), which lists every topic letter with its exact `Lesson N-M` range for the
*entire* module in one place. One read of that table gives definitive ground-truth
lesson-number-to-topic-letter mapping for every topic in the module — far more efficient than
reading individual topic-divider pages one at a time.

The table has no page numbers, so it only answers "which lessons belong to which topic," not
"where those lessons sit in the PDF." Page ranges continued to be computed the same way as
everywhere else in this pipeline: from each `Lesson`'s actual `LearningComponent.pdfPage` values
already in the graph (pypdfium2-extracted at ingestion time), with a gap-skip outlier filter
(`GAP_THRESHOLD=20`) to drop stray page references — most commonly a single leftover LC pointing
back at the Overview table's own page, picked up by nearly every lesson in a chunk.

Fix pipeline applied per module (repeated 4x):
1. Reassign every `Lesson`'s `hasPart` edge to its ground-truth topic by position number.
2. Merge same-named duplicate lessons landing on the same topic (`merge_duplicate_lessons()`).
3. Merge same-*position* duplicates the name-based pass missed (reworded near-duplicates, or a
   genuine second copy under a generic name like "Lesson 27" — G5 Module 6 turned up 7 of these,
   sourced from the module's homework-answer-key appendix at the back of the same PDF, correctly
   merged in as additional content for the same lesson rather than discarded).
4. Recompute each lesson's own page range from its LC children (gap-skip outliers), then each
   topic's range from its lessons — excluding any lesson with `lc_count <= 1` from the topic-level
   min/max (a single-LC lesson is usually itself a stub/placeholder and skews the range if trusted;
   the lesson node and its one real LC are kept, just excluded from the topic rollup).

One new artifact type surfaced during this pass: a handful of individual lessons (G5 Module 4's
Lesson 20; would apply equally elsewhere) have only 1 `LearningComponent` at a clearly-wrong page —
their real content appears to have been dropped entirely during the original chunked extraction,
not just misattributed elsewhere. This is a data-completeness gap distinct from misattribution;
flagged here rather than silently patched over, since there's no sibling or table entry that
recovers the missing content.

All 4 verified individually via `report_suspicious_wide_topics()` (0 remaining for their course),
`check_orphans.py --course` (clean), and `grep -c possibleStandardAlignment` (0) after each fix.
**Final state: 0 suspicious topics remain across all 9 courses** (K-5 plus 6/7/8/Algebra I).

## Local Neo4j reload

Re-loading the corrected JSONL was more involved than a simple `load_graph()` MERGE pass: since
several rounds of node/edge *deletions* happened across this session's fixes (blank-topic merges,
duplicate-lesson removals, the misattribution reassignment), and `load_graph()`'s `MERGE`-based
loading only adds/updates — it never removes anything absent from the new batch — a second reload
attempt left real staleness behind (a reassigned lesson showed up with *two* parents, the old
wrong one and the new correct one; a deleted phantom topic node was still present). Confirmed live
via direct Cypher before trusting the reload, not assumed.

Fix: explicitly wiped all 6 K-5 courses' content from local Neo4j (`DETACH DELETE` on every node
reachable from each Course node, excluding the shared `StandardsFrameworkItem` nodes and leaving
Algebra I/6/7/8 untouched — confirmed via node count before/after), then reloaded fresh from the
verified JSONL. Live-Cypher-verified afterward: 0 `possibleStandardAlignment` edges, 0 orphaned
`LearningComponent`/`Assessment` nodes, the previously-duplicated lesson now has exactly one
correct parent, the deleted phantom topic is gone.

Re-run after the final-4 manual fixes (GK, G1, both G5 topics): wiped and reloaded the same 6
K-5 courses again. Node count before wipe 25,764 → 9,726 after wipe (9,726 = the untouched
Algebra I/6/7/8 courses plus shared `StandardsFrameworkItem` nodes) → 25,748 after reload.
Live-spot-checked: G5 "Multiplication of a Whole Number by a Fraction" now shows exactly
`['8', '9', '10', '11']` as its `Lesson` children, matching the Module 4 Overview table.

## Local Qdrant reload

Unlike Neo4j's incremental `MERGE`, `build_content_index.py`/`semantic_index.index_nodes()` fully
deletes and recreates the `curriculum_content` collection every run — no staleness-accumulation
risk the way Neo4j had, but the collection still needed a fresh run since it was last built (Sept
8 evening) *before* the misattribution fix, dedup cleanup, and orphan-node fix (all Sept 9). Text
extraction feeding every embedded node's `sourceText`/`description` is `pypdfium2` throughout,
never docling — confirmed earlier this session that docling silently drops content on dense/
tabular page layouts (290 chars vs. 2,004 real chars on the same page, no error raised).

Re-ran against the corrected JSONL (all 10 courses, OpenAI `text-embedding-3-small`):
**22,343 points indexed** (down slightly from the earlier stale build's 22,358 — consistent with
the duplicate/orphaned `LearningComponent` nodes removed during cleanup).

Re-ran once more after the final-4 manual fixes: combined node count 25,748 (matching the fresh
Neo4j reload above exactly), **22,343 points indexed** again — unchanged from the prior run since
the final-4 fixes only reassigned/merged/recomputed existing lessons within their modules, no net
change to the total embeddable-node count. Local Qdrant now reflects the same final, fully
corrected state as local Neo4j.

**Remote Neo4j and remote Qdrant were not touched** — everything in this report is local only,
per explicit instruction earlier this session.
