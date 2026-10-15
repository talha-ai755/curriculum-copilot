#!/usr/bin/env python3
"""
Page Inspector — QA tool: pick a course/module/PDF page and see, side by side, (1) the real
rendered PDF page, (2) what's actually on those node identifiers in LIVE Neo4j right now (a real
Cypher lookup, not the pre-load JSONL), and (3) what's actually indexed in Qdrant for the same
identifiers.

Built to catch exactly the class of bug found repeatedly this session -- a node's pageLabel/
sourceText/Qdrant entry silently drifting from what the real PDF page says -- by putting ground
truth (the PDF itself) next to both derived stores instead of trusting either on its own.

The Neo4j panel deliberately queries the live graph instead of re-showing the JSONL: graph_store.
load_graph() strips sourceText/description off every LearningComponent before writing it
(graph_store._BULK_TEXT_PROPS -- that text already lives in full in Qdrant, so a live Neo4j node
never has it), and a stale Neo4j (JSONL fixed but not reloaded) is itself a real bug class this
page should be able to catch. data/<course>_nodes.jsonl is still used to work out WHICH
identifiers belong on a given page (via hasPart-walk module scoping) -- only the properties shown
for each one are the live ones.

Courses/modules are auto-discovered the same way backfill_page_labels.py does (every
submit_batch_*.py's MODULES + COURSE_NODES_PATH), so a newly-ingested course shows up here with no
extra registration step.
"""
import json
import os
import random

from dotenv import load_dotenv
import pypdfium2 as pdfium
import streamlit as st

import graph_store as gs
import semantic_index as si
from backfill_page_labels import discover_courses, module_of_each_node

REVIEWED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "page_inspector_reviewed.json")
REVIEWED_PATH = os.path.normpath(REVIEWED_PATH)

load_dotenv()

st.set_page_config(page_title="Page Inspector", page_icon="🔍", layout="wide")
st.title("🔍 Page Inspector")
st.caption("Pick a PDF page and compare it against the graph nodes and Qdrant entries built from "
           "it — a QA view, not a teacher-facing one.")


@st.cache_resource(show_spinner=False)
def _open_pdf(pdf_path):
    return pdfium.PdfDocument(pdf_path)


@st.cache_resource(show_spinner="Loading course graph…")
def _load_course(nodes_path, rels_path, mtime):
    """Cached by (path, mtime) so a rebuilt course (new mtime) busts the cache automatically.
    cache_resource (not cache_data): these lists are read-only here, so avoid the deep-copy
    cache_data would do on every Streamlit rerun (every widget interaction)."""
    nodes, rels = gs.load_jsonl(nodes_path, rels_path)
    node_module = module_of_each_node(nodes, rels)
    by_id = {n["identifier"]: n for n in nodes}
    return nodes, rels, node_module, by_id


def _course_label(nodes_path):
    return os.path.basename(nodes_path).replace("_nodes.jsonl", "")


def _page_key(course_slug, module_name, page_num):
    return f"{course_slug}|{module_name}|{page_num}"


def _load_reviewed():
    """Set of page_key strings already marked reviewed -- a tiny local JSON file, not
    session_state, so "reviewed" survives across restarts/sessions (this is a QA sweep tool,
    meant to be worked through over multiple sittings)."""
    if not os.path.exists(REVIEWED_PATH):
        return set()
    with open(REVIEWED_PATH) as f:
        return set(json.load(f))


def _save_reviewed(reviewed):
    os.makedirs(os.path.dirname(REVIEWED_PATH), exist_ok=True)
    with open(REVIEWED_PATH, "w") as f:
        json.dump(sorted(reviewed), f, indent=2)


@st.cache_resource(show_spinner="Scanning all courses for content pages…")
def _all_content_pages(courses_key):
    """[(course_slug, nodes_path, module_name, page_num), ...] -- every distinct (module, page)
    combination, across EVERY discovered course, that has at least one LearningComponent on it
    (no point ever landing the random picker on a blank page). `courses_key` is a hashable tuple
    of (nodes_path, mtime) pairs so a course rebuild (new mtime) busts this cache automatically,
    same pattern as _load_course()."""
    pool = []
    for nodes_path, mtime in courses_key:
        rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
        if not os.path.exists(rels_path):
            continue
        nodes, rels, node_module, _ = _load_course(nodes_path, rels_path, mtime)
        course_slug = _course_label(nodes_path)
        seen = set()
        for n in nodes:
            if n["labels"][0] != "LearningComponent":
                continue
            page = n["properties"].get("pdfPage")
            module_name = node_module.get(n["identifier"])
            if page is None or not module_name:
                continue
            key = (module_name, page)
            if key not in seen:
                seen.add(key)
                pool.append((course_slug, nodes_path, module_name, page))
    return pool


def _qdrant_client():
    """si.get_shared_client() (not get_client()) -- local-mode QdrantClient holds an OS-level
    file lock, so this page must reuse the exact same cached client app.py's load_course()
    already opened rather than instantiating its own second one against the same storage path
    (confirmed live: doing so crashed with "already accessed by another instance of Qdrant
    client" the moment this page was opened alongside the main app)."""
    return si.get_shared_client()


@st.cache_resource(show_spinner=False)
def _neo4j_driver():
    """None if unreachable (same graceful-degradation pattern as app.py's load_course()) rather
    than crashing the whole page -- Neo4j being down shouldn't take out the PDF/Qdrant panels."""
    uri = os.environ.get("NEO4J_URI", gs.DEFAULT_URI)
    user = os.environ.get("NEO4J_USER", gs.DEFAULT_USER)
    password = os.environ.get("NEO4J_PASSWORD", gs.DEFAULT_PASSWORD)
    try:
        driver = gs.get_driver(uri, user, password)
        driver.verify_connectivity()
        return driver
    except Exception:
        return None


def _live_properties(driver, identifiers):
    """{identifier: {"labels": [...], "properties": {...}}} straight off LIVE Neo4j -- exactly
    what a real Cypher query returns right now, not the pre-load JSONL. An identifier absent from
    the result means either that node was never loaded (or loaded from an older JSONL, then the
    course rebuilt without a reload) or the connection just failed."""
    if driver is None or not identifiers:
        return {}
    try:
        rows = gs.run_cypher(
            driver,
            "UNWIND $ids AS id MATCH (n {identifier: id}) "
            "RETURN n.identifier AS identifier, labels(n) AS labels, properties(n) AS props",
            {"ids": identifiers})
    except Exception:
        return {}
    return {r["identifier"]: {"labels": r["labels"], "properties": r["props"]} for r in rows}


def _live_relationships(driver, identifiers):
    """{identifier: [{"rel", "dir" ("out"/"in"), "other_id", "other_labels", "other_name",
    "other_page_label", "other_pdf_page"}, ...]} -- every edge touching each node, both
    directions, straight off live Neo4j (e.g. hasPart up to its parent Lesson/Topic/Module,
    hasEducationalAlignment to a standard) -- including the OTHER node's own page, so you can see
    at a glance whether a related node sits on this same page or a different one entirely."""
    if driver is None or not identifiers:
        return {}
    # LearningComponent stores a single pdfPage/pageLabel; Lesson/LessonGrouping instead store a
    # RANGE (pdfPageStart/pdfPageEnd, pageStartLabel/pageEndLabel) since they span many pages --
    # coalesce onto pdfPage/pageLabel for the single-page case, carry the range fields separately
    # for the grouping case, so a related Lesson/Topic/Module's page renders instead of blank.
    _RETURN = (
        "m.identifier AS other_id, labels(m) AS other_labels, "
        "coalesce(m.name, m.ordinalName, m.code, '') AS other_name, "
        "m.pageLabel AS other_page_label, m.pdfPage AS other_pdf_page, "
        "m.pageStartLabel AS other_page_start_label, m.pageEndLabel AS other_page_end_label, "
        "m.pdfPageStart AS other_pdf_page_start, m.pdfPageEnd AS other_pdf_page_end")
    try:
        rows = gs.run_cypher(
            driver,
            "UNWIND $ids AS id MATCH (n {identifier: id})-[r]->(m) "
            f"RETURN id AS identifier, type(r) AS rel, 'out' AS dir, {_RETURN} "
            "UNION "
            "UNWIND $ids AS id MATCH (m)-[r]->(n {identifier: id}) "
            f"RETURN id AS identifier, type(r) AS rel, 'in' AS dir, {_RETURN}",
            {"ids": identifiers})
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["identifier"], []).append(r)
    return out


courses = discover_courses()  # {nodes_path: {module_name: pdf_path}}
# discover_courses() only requires MODULES + COURSE_NODES_PATH to be non-empty in a course's
# submit_batch_<name>.py -- true as soon as its PDFs are found on disk, well before that course
# has actually been built (e.g. Grade 6/7 here: PDFs discovered, batch still processing, no
# data/grade6_nodes.jsonl yet). Filter those out rather than crash on a missing file.
courses = {p: m for p, m in courses.items() if os.path.exists(p)}
if not courses:
    st.warning("No BUILT courses yet — a course needs data/<name>_nodes.jsonl to exist (run its "
               "build_batch_<name>_course.py), not just a submit_batch_<name>.py with MODULES.")
    st.stop()

courses_key = tuple(sorted((p, os.path.getmtime(p)) for p in courses))
all_pages = _all_content_pages(courses_key)
reviewed = _load_reviewed()
unreviewed_pool = [t for t in all_pages
                   if _page_key(t[0], t[2], t[3]) not in reviewed]


def _pick_random_page():
    """on_click callback -- runs BEFORE the script reruns, so session_state set here is what the
    course/module/page widgets below read as their value on the next run (the standard Streamlit
    pattern for a button that jumps a set of keyed widgets to a new value)."""
    pool = unreviewed_pool or all_pages  # everything reviewed -> fall back to sampling all again
    if not pool:
        return
    course_slug, nodes_path, module_name, page_num = random.choice(pool)
    st.session_state["pi_course"] = nodes_path
    st.session_state["pi_module"] = module_name
    st.session_state["pi_page"] = page_num


col_course, col_module, col_page = st.columns([2, 2, 1])
with col_course:
    nodes_path = st.selectbox("Course", sorted(courses), format_func=_course_label, key="pi_course")
module_pdfs = courses[nodes_path]
rels_path = nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")
if not os.path.exists(rels_path):
    st.error(f"{rels_path} not found — build this course fully before inspecting it.")
    st.stop()

with col_module:
    module_options = list(module_pdfs)
    if st.session_state.get("pi_module") not in module_options:
        st.session_state["pi_module"] = module_options[0]  # e.g. course just switched
    module_name = st.selectbox("Module", module_options, key="pi_module")
pdf_path = module_pdfs[module_name]
if not os.path.exists(pdf_path):
    st.error(f"PDF not found at {pdf_path!r} — this course's MODULES path is stale/unreachable "
             f"from this machine.")
    st.stop()

doc = _open_pdf(pdf_path)
n_pages = len(doc)
with col_page:
    if st.session_state.get("pi_page", 1) > n_pages:
        st.session_state["pi_page"] = 1  # e.g. module just switched to a shorter PDF
    page_num = st.number_input("PDF page", min_value=1, max_value=n_pages, step=1, key="pi_page",
                               help=f"1–{n_pages} (docling's 1-indexed pdfPage, same numbering "
                                    f"the graph stores)")

current_key = _page_key(_course_label(nodes_path), module_name, page_num)
# Force-resync the checkbox's value from the FRESH file state whenever the page we're looking at
# has actually changed -- a single reused widget `key` would otherwise show whatever it was last
# set to earlier in this session (Streamlit persists widget state by key across reruns), which is
# wrong the moment you revisit a page after its reviewed status changed some other way. Setting
# session_state[key] BEFORE creating the widget, guarded by a "last shown for" sentinel, is the
# standard way to force a widget to a fresh value on context change.
if st.session_state.get("_reviewed_shown_for") != current_key:
    st.session_state["reviewed_checkbox"] = current_key in reviewed
    st.session_state["_reviewed_shown_for"] = current_key

col_random, col_reviewed, col_progress = st.columns([1.3, 1, 2])
with col_random:
    st.button("🎲 Random unreviewed page", on_click=_pick_random_page,
               disabled=not all_pages, use_container_width=True,
               help="Jumps to a random page (across every course) that has real graph content "
                    "and hasn't been marked reviewed yet.")
with col_reviewed:
    is_reviewed = st.checkbox("✅ Reviewed", key="reviewed_checkbox")

# Apply BEFORE rendering the progress caption, so a click that toggles the checkbox updates the
# count on the same run instead of looking stale until the next rerun.
if is_reviewed and current_key not in reviewed:
    reviewed.add(current_key)
    _save_reviewed(reviewed)
elif not is_reviewed and current_key in reviewed:
    reviewed.discard(current_key)
    _save_reviewed(reviewed)

with col_progress:
    st.caption(f"{len(reviewed)}/{len(all_pages)} sampled pages marked reviewed across all courses")

nodes, rels, node_module, by_id = _load_course(nodes_path, rels_path, os.path.getmtime(nodes_path))

page_nodes = [
    n for n in nodes
    if n["labels"][0] == "LearningComponent"
    and n["properties"].get("pdfPage") == page_num
    and node_module.get(n["identifier"]) == module_name
]

qdrant_client = _qdrant_client()
payloads = si.get_payloads(qdrant_client, [n["identifier"] for n in page_nodes])

neo4j_driver = _neo4j_driver()
live_props = _live_properties(neo4j_driver, [n["identifier"] for n in page_nodes])
live_rels = _live_relationships(neo4j_driver, [n["identifier"] for n in page_nodes])

st.divider()
if neo4j_driver is None:
    st.warning("Neo4j unreachable right now (check NEO4J_URI/NEO4J_PASSWORD) — the Neo4j panel "
               "below can't show live data until it's reachable.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Nodes on this page (per JSONL)", len(page_nodes))
m2.metric("Present in live Neo4j", sum(1 for n in page_nodes if n["identifier"] in live_props))
m3.metric("Indexed in Qdrant", sum(1 for n in page_nodes if n["identifier"] in payloads))
mismatches = sum(
    1 for n in page_nodes
    if (live := live_props.get(n["identifier"]))
    and (pl := payloads.get(n["identifier"]))
    and (pl.get("pdf_page") != live["properties"].get("pdfPage")
         or pl.get("page_label") != live["properties"].get("pageLabel"))
)
m4.metric("Neo4j vs Qdrant mismatches", mismatches,
          delta=None if mismatches == 0 else "check below", delta_color="inverse")

col_pdf, col_graph, col_qdrant = st.columns([1.1, 1, 1])

with col_pdf:
    st.subheader(f"📄 {module_name}, page {page_num}")
    with st.spinner("Rendering page…"):
        image = doc[page_num - 1].render(scale=2.0).to_pil()
    st.image(image, width="stretch")

with col_graph:
    st.subheader(f"🕸️ Neo4j (live) — {len(page_nodes)}")
    if not page_nodes:
        st.info("No LearningComponent recorded on this page for this module.")
    for n in page_nodes:
        p_jsonl = n["properties"]  # JSONL: only used as a header fallback when live is missing
        live = live_props.get(n["identifier"])
        live_p = live["properties"] if live else {}
        header_role = (live_p.get("componentRole") if live else p_jsonl.get("componentRole")) \
            or "(lesson content)"
        header_page_label = (live_p.get("pageLabel") if live else p_jsonl.get("pageLabel")) \
            or "(no pageLabel)"
        with st.expander(f"{header_role} — {header_page_label}", expanded=False):
            st.caption(n["identifier"])
            if live is None:
                st.error("❌ NOT IN LIVE NEO4J — never loaded, loaded from a since-rebuilt JSONL, "
                          "or Neo4j is unreachable right now.")
            else:
                # Exact, unfiltered echo of what Neo4j returned for this node — no fields added,
                # none held back.
                st.json({"identifier": n["identifier"], "labels": live["labels"], **live_p})

                edges = live_rels.get(n["identifier"], [])
                st.markdown(f"**🔗 Relationships ({len(edges)})**")
                if not edges:
                    st.caption("No edges found on live Neo4j for this node.")
                for e in sorted(edges, key=lambda e: (e["dir"], e["rel"])):
                    arrow = "→" if e["dir"] == "out" else "←"
                    other_label = (e["other_labels"] or ["?"])[0]
                    other_page, start, end = (e.get("other_pdf_page"), e.get("other_pdf_page_start"),
                                               e.get("other_pdf_page_end"))
                    if other_page is not None:
                        label = e.get("other_page_label") or other_page
                        note = " (same page)" if other_page == page_num else f" (page {other_page})"
                        page_note = f"{label}{note}"
                    elif start is not None:
                        start_label = e.get("other_page_start_label") or start
                        end_label = e.get("other_page_end_label") or end
                        spans = end is None or start <= page_num <= end
                        note = " (spans this page)" if spans else f" (pages {start}–{end})"
                        page_note = f"{start_label}–{end_label}{note}" if end and end != start \
                            else f"{start_label}{note}"
                    else:
                        page_note = "—"  # e.g. a StandardsFrameworkItem genuinely has no page
                    st.caption(f"{arrow} **{e['rel']}** {arrow} `{other_label}` "
                               f"{e['other_name'] or e['other_id']} · page {page_note}")

with col_qdrant:
    st.subheader(f"🔎 Qdrant (curriculum_content)")
    if not page_nodes:
        st.info("Nothing to look up — no graph nodes on this page.")
    for n in page_nodes:
        pl = payloads.get(n["identifier"])
        live = live_props.get(n["identifier"])
        live_p = live["properties"] if live else {}
        if pl is None:
            st.error(f"❌ NOT INDEXED — {live_p.get('componentRole') or '(lesson content)'} "
                     f"({n['identifier']})")
            continue
        drift = live is not None and (
            pl.get("pdf_page") != live_p.get("pdfPage")
            or pl.get("page_label") != live_p.get("pageLabel"))
        badge = "⚠️ DRIFT" if drift else ("✅" if live is not None else "❓ (no live Neo4j to compare)")
        with st.expander(f"{badge} {pl.get('label')} — {pl.get('page_label') or '(no page_label)'}",
                          expanded=drift):
            st.caption(n["identifier"])
            if drift:
                st.warning(f"Qdrant: pdf_page={pl.get('pdf_page')!r}, "
                           f"page_label={pl.get('page_label')!r}  vs.  "
                           f"Neo4j: pdfPage={live_p.get('pdfPage')!r}, pageLabel={live_p.get('pageLabel')!r}")
            st.json(pl)
