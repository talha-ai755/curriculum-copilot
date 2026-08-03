#!/usr/bin/env python3
"""
Streamlit chat UI for the Algebra I curriculum copilot.

Auto-loads the final, persisted Algebra I knowledge graph (5 modules, Course->Module->Topic->
Lesson->LearningComponent/Assessment, LC schema) into Neo4j + Qdrant (both on-disk, so this
survives restarts) and lets a teacher chat with it. The model decides itself whether to query
the graph, search semantically, or both — see AGENT_SYSTEM_TEMPLATE / TOOLS below.

Needs a Claude API key (sidebar, or set ANTHROPIC_API_KEY) and a reachable Neo4j
(docker compose up -d in this directory starts one with the right credentials by default).
"""
import json
import os

import streamlit as st

import graph_store as gs
import pdf_to_kg as p2k
import semantic_index as si

NODES_PATH = os.path.join(os.path.dirname(__file__), "data", "algebra1_nodes.jsonl")
RELS_PATH = os.path.join(os.path.dirname(__file__), "data", "algebra1_relationships.jsonl")


def _default_api_key():
    """Key from Streamlit secrets (cloud) or env var (local), else blank."""
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


AGENT_SYSTEM_TEMPLATE = (
    "You are a curriculum copilot for teachers and instructional coaches, answering "
    "questions grounded in a Learning-Commons-schema curriculum knowledge graph stored in "
    "Neo4j, with a Qdrant semantic-search index over the same lesson content for fuzzy/open "
    "questions. Every node has an `identifier` property that is the SAME value in both "
    "stores — a semantic_search hit's identifier is exactly what you MATCH on in Cypher.\n\n"
    "You have three tools:\n"
    "- graph_query(cypher): runs a READ-ONLY Cypher query you write yourself against Neo4j, "
    "using the schema below. Go straight here for questions naming an explicit standard code "
    "or topic. Never write CREATE/MERGE/DELETE/SET/REMOVE — the query will be rejected; if it "
    "is, or Neo4j errors, read the error and try a corrected query. NOTE: LearningComponent's "
    "sourceText/description are NOT Neo4j properties (kept only in the search index to avoid "
    "storing the same text twice) — a Cypher RETURN of those fields will come back null; use "
    "get_content(identifiers) for the real text once you have the identifier.\n"
    "  PAGE-NUMBER LOOKUPS FOR A NAMED LESSON/TOPIC/MODULE — use graph_query, not "
    "semantic_search: a Lesson/Topic/Module's own pageStartLabel/pageEndLabel (or a Lesson's "
    "pdfPageStart/pdfPageEnd) is the ONE authoritative page range for that node. Its content is "
    "typically scattered across several child LearningComponents, each with its OWN pageLabel "
    "for wherever that specific activity happens to fall — semantic_search ranks those by "
    "similarity to your query, not by position, so its top hit's page is essentially arbitrary "
    "and is NOT the lesson's starting page. If asked 'what page is <named lesson/topic> on', "
    "MATCH that Lesson/LessonGrouping node directly and read its own pageStartLabel/"
    "pageEndLabel — don't infer it from a semantic_search hit's page_label.\n"
    "- semantic_search(query, limit): searches by meaning over one point per lesson "
    "activity/assessment, each carrying the REAL, VERBATIM text of its source PDF page (not a "
    "paraphrase or summary). Use it first for open, fuzzy questions ('how do I teach X'). Each "
    "hit includes its parent Lesson/Topic name, page_label/pdf_page/lesson_phase, and its "
    "identifier — the parent name alone is often enough to cite; follow up with graph_query "
    "using the identifier only if the question needs standards, sibling activities, or "
    "progression (buildsTowards/hasStandardAlignment) context. Content that doesn't fit a "
    "regular lesson activity (Assessment Summaries, Pacing Guides, Self-Reflections, Module/"
    "Topic Overviews) is indexed too, tagged via componentRole (e.g. \"Assessment Summary\") — "
    "search for those directly when a question needs that kind of content.\n"
    "- get_content(identifiers): a direct, deterministic fetch of one or more nodes' real text "
    "by identifier — NOT a search, no ranking involved. Use this whenever graph_query has found "
    "a LearningComponent (by page, by traversal, by any structural match) and you need its "
    "actual content, since graph_query itself never returns sourceText/description.\n\n"
    "AMBIGUOUS ORDINALS — CRITICAL: `ordinalName` (e.g. \"Topic 1\", \"Lesson 3\") is only "
    "unique WITHIN its parent — every module has its own \"Topic 1\", every topic has its own "
    "\"Lesson 1\", \"Lesson 2\", etc. There are 5 different \"Topic 1\" nodes in this graph "
    "alone, one per module, with completely different content. NEVER filter on `ordinalName` "
    "(or a bare lesson/topic number the user mentions) without ALSO matching through its parent "
    "Module/Topic to pin down exactly which one — e.g. don't write "
    "`MATCH (t:LessonGrouping {{ordinalName:'Topic 1'}})` alone; if the question doesn't specify "
    "which module, either ask which one, or return/consider all matches explicitly rather than "
    "silently picking one. Getting this wrong doesn't error — it silently returns a real node "
    "with real content that is nonetheless the WRONG one, which is worse than an empty result.\n\n"
    "PROGRESSION QUESTIONS ('what does this build on', 'what comes before/after this standard', "
    "'what grade-level skill leads here'): this course's standards are TEKS-coded (e.g. A.5B), "
    "and TEKS standards were NEVER given a direct buildsTowards edge in the source framework — "
    "checking buildsTowards on a TEKS code alone will always come back empty, even when a real "
    "progression exists. The actual progression data lives on the Common Core side and is reached "
    "by a two-hop query: TEKS code -> hasStandardAlignment -> Common Core code -> buildsTowards -> "
    "prerequisite/successor Common Core code. Concretely:\n"
    "  MATCH (a:StandardsFrameworkItem {{code:'A.5B'}})-[:hasStandardAlignment]->(cc)\n"
    "  MATCH (cc)<-[:buildsTowards]-(prereq) RETURN prereq.code, prereq.description\n"
    "(swap the buildsTowards direction to find what a standard leads to instead of what it builds "
    "on). Try this two-hop pattern BEFORE concluding no progression exists — only say there's no "
    "progression data if both the direct buildsTowards check AND this hasStandardAlignment hop "
    "come back empty. The prerequisite/successor you find will usually only have a Common Core "
    "code (no TEKS crosswalk back), so cite it as-is and describe what it covers.\n\n"
    "VISUALIZATION: whenever a graph_query result is a CHAIN of 2+ linked hops (a progression/"
    "prerequisite path, a standards-alignment crosswalk, or any 'walk from X through Y to Z' "
    "traversal), shape the RETURN so each row is one edge in the chain with exactly these column "
    "names: from_id, from_label, relationship, to_id, to_label (optionally from_desc, to_desc for "
    "hover detail). One row per hop — e.g. the A.5B two-hop progression above is 2 rows: one for "
    "the hasStandardAlignment edge, one for the buildsTowards edge. The app auto-detects this "
    "shape and renders an interactive diagram beneath your answer, so use it for any progression "
    "or multi-hop question — not just when explicitly asked to visualize.\n\n"
    "Always cite the specific Topic/standard code AND page (page_label, when present in a "
    "tool result) your answer is grounded in. If all tools come up empty or error, say so "
    "plainly rather than inventing curriculum content. Be concise and practical, like you're "
    "helping a teacher between classes.\n\n"
    "GRAPH SCHEMA:\n{schema}"
)

TOOLS = [
    {
        "name": "graph_query",
        "description": "Run a read-only Cypher query against the Neo4j curriculum graph.",
        "input_schema": {
            "type": "object",
            "properties": {"cypher": {"type": "string",
                                       "description": "A read-only Cypher query "
                                                       "(MATCH/RETURN/WHERE/WITH only)."}},
            "required": ["cypher"],
        },
    },
    {
        "name": "semantic_search",
        "description": "Semantic search over the real, verbatim text of lesson activities, "
                        "assessments, and supplementary page content (Assessment Summaries, "
                        "Pacing Guides, Self-Reflections, Overviews).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "Max hits to return (default 25)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_content",
        "description": "Fetch the real verbatim text (sourceText/description) for one or more "
                        "LearningComponent identifiers you already have (e.g. from graph_query) "
                        "-- a direct lookup, not a search. graph_query results never include this "
                        "text (kept only in this index to avoid storing it twice); call this "
                        "whenever you need a specific node's actual content, not just its "
                        "structural properties.",
        "input_schema": {
            "type": "object",
            "properties": {
                "identifiers": {"type": "array", "items": {"type": "string"},
                                 "description": "One or more node `identifier` values."},
            },
            "required": ["identifiers"],
        },
    },
]


def run_tool(name, tool_input, driver, qdrant_client, openai_client):
    """Dispatch one tool call. Never raises — errors come back as {"error": ...} so the model
    sees them as the tool result and can react (e.g. revise a bad Cypher query itself)."""
    try:
        if name == "graph_query":
            if driver is None:
                return {"error": "Neo4j is not connected."}
            return gs.run_cypher(driver, tool_input["cypher"])
        if name == "semantic_search":
            if qdrant_client is None:
                return {"error": "Qdrant is not available."}
            if openai_client is None:
                return {"error": "OPENAI_API_KEY not set — semantic_search needs it to embed "
                                 "the query (the index was built with OpenAI embeddings)."}
            return si.search(qdrant_client, openai_client, tool_input["query"],
                              tool_input.get("limit", 25))
        if name == "get_content":
            if qdrant_client is None:
                return {"error": "Qdrant is not available."}
            return si.get_content(qdrant_client, tool_input["identifiers"])
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)}


def answer_with_tools(client, system, question, driver, qdrant_client, openai_client,
                       max_turns=6):
    """Bounded Claude tool-use loop: the model decides itself whether/how many times to call
    graph_query, semantic_search, and get_content. Returns (answer_text, transcript) where
    transcript is a list of {"tool", "input", "result"} for display."""
    messages = [{"role": "user", "content": question}]
    transcript = []
    for _ in range(max_turns):
        resp = client.messages.create(model=p2k.MODEL, max_tokens=1500, system=system,
                                       tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return next((b.text for b in resp.content if b.type == "text"), ""), transcript
        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            result = run_tool(block.name, block.input, driver, qdrant_client, openai_client)
            transcript.append({"tool": block.name, "input": block.input, "result": result})
            tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                  "content": json.dumps(result, default=str)})
        messages.append({"role": "user", "content": tool_results})
    return "⚠️ Reached the tool-call limit without a final answer.", transcript


@st.cache_resource(show_spinner="Loading the Algebra I graph into Neo4j + Qdrant…")
def load_course():
    """Cached at the process level — runs once per server lifetime, every browser session
    after the first gets it for free. Neo4j graph load is cheap and stays idempotent (MERGE) so
    it's fine to redo every launch. The Qdrant curriculum_content collection is built ONCE by
    build_content_index.py — OpenAI embeddings cost money per call, so this just opens the
    already-built on-disk collection rather than re-embedding on every app start."""
    nodes, rels = gs.load_jsonl(NODES_PATH, RELS_PATH)
    driver, schema_doc = None, None
    try:
        driver = gs.get_driver(os.environ.get("NEO4J_URI", gs.DEFAULT_URI),
                                os.environ.get("NEO4J_USER", gs.DEFAULT_USER),
                                os.environ.get("NEO4J_PASSWORD", gs.DEFAULT_PASSWORD))
        gs.load_graph(driver, nodes, rels)
        schema_doc = gs.schema_description(nodes, rels)
    except Exception:
        driver = None
    qc = si.get_client()
    openai_client = None
    if os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI
        openai_client = OpenAI()
    return driver, schema_doc, qc, openai_client


# ── Page ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Algebra I Curriculum Copilot", page_icon="🧭", layout="wide")
st.title("🧭 Algebra I Curriculum Copilot")
st.caption("Ask questions grounded in the real Bluebonnet Algebra I curriculum (5 modules, "
           "standards-aligned) — the model cites the specific Topic/Lesson, standard, and page.")

with st.sidebar:
    api_key = st.text_input("Claude API key", type="password", value=_default_api_key(),
                            help="Or set ANTHROPIC_API_KEY as an env var / Streamlit secret.")

driver, schema_doc, qdrant_client, openai_client = load_course()
if driver is None:
    st.warning("Neo4j unavailable — chat will rely on semantic search only. "
               "(`docker compose up -d` in this directory to start it.)")
if openai_client is None:
    st.info("OPENAI_API_KEY not set — semantic_search is unavailable (the index was built "
            "with OpenAI embeddings); graph_query still works.")
elif not qdrant_client.collection_exists(si.COLLECTION):
    st.warning("curriculum_content isn't built yet — run `python3 build_content_index.py` "
               "once, then restart. semantic_search will error until then.")

examples = [
    "Which topics cover TEKS A.3C?",
    "How do I teach function notation?",
    "What's the crux of the Quadratic Formula lesson?",
    "Give me practice problems for solving linear systems.",
]
picked = st.pills("Try an example:", examples, selection_mode="single") \
    if hasattr(st, "pills") else None
q = st.chat_input("Ask about Algebra I…")
question = q or picked


def _show_transcript(transcript):
    with st.expander("🔎 How this was answered"):
        for step in transcript:
            st.markdown(f"**{step['tool']}**`({step['input']})`")
            st.json(step["result"])


_CHAIN_KEYS = {"from": ("from_id", "from_code", "from_name", "from_label"),
               "to": ("to_id", "to_code", "to_name", "to_label"),
               "rel": ("relationship", "rel", "relationship_type")}


def _extract_chain_edges(transcript):
    """Pull (from_label, relationship, to_label, from_title, to_title) tuples out of any
    graph_query result shaped as a chain per the system prompt's VISUALIZATION contract."""
    def pick(row, keys):
        for k in keys:
            if row.get(k):
                return str(row[k])
        return None

    edges = []
    for step in transcript:
        if step["tool"] != "graph_query" or not isinstance(step["result"], list):
            continue
        for row in step["result"]:
            if not isinstance(row, dict):
                continue
            f, t = pick(row, _CHAIN_KEYS["from"]), pick(row, _CHAIN_KEYS["to"])
            if f and t:
                r = pick(row, _CHAIN_KEYS["rel"]) or ""
                edges.append((f, r, t, row.get("from_desc") or "", row.get("to_desc") or ""))
    return edges


def _render_chain_graph(edges):
    """Interactive node-link diagram for a chain of standards/lessons the model returned in the
    VISUALIZATION shape (deduped by label; one Edge per hop)."""
    from streamlit_agraph import Node, Edge, Config, agraph
    seen, nodes = {}, []
    for f, _, t, f_desc, t_desc in edges:
        for label, desc in ((f, f_desc), (t, t_desc)):
            if label not in seen:
                seen[label] = True
                nodes.append(Node(id=label, label=label, title=desc or label, size=22))
    agraph_edges = [Edge(source=f, target=t, label=r) for f, r, t, _, _ in edges]
    agraph(nodes=nodes, edges=agraph_edges,
           config=Config(width=700, height=350, directed=True, physics=True, hierarchical=False))


st.session_state.setdefault("chat", [])
for role, msg, transcript in st.session_state["chat"]:
    with st.chat_message(role):
        st.markdown(msg)
        if transcript:
            _show_transcript(transcript)

if question:
    with st.chat_message("user"):
        st.markdown(question)
    if not api_key:
        with st.chat_message("assistant"):
            st.warning("Enter a Claude API key in the sidebar to ask questions.")
    else:
        import anthropic
        system = AGENT_SYSTEM_TEMPLATE.format(
            schema=schema_doc or "(Neo4j not loaded — graph_query will error; rely on "
                                  "semantic_search instead.)")
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    ans, transcript = answer_with_tools(
                        anthropic.Anthropic(api_key=api_key), system, question,
                        driver, qdrant_client, openai_client)
                except Exception as e:
                    ans, transcript = f"⚠️ {e}", []
            st.markdown(ans)
            chain_edges = _extract_chain_edges(transcript)
            if chain_edges:
                st.caption("Progression / relationship chain found in the graph:")
                _render_chain_graph(chain_edges)
            if transcript:
                _show_transcript(transcript)
        st.session_state["chat"] += [("user", question, None),
                                     ("assistant", ans, transcript)]
