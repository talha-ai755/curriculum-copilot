#!/usr/bin/env python3
"""
Streamlit chat UI for the curriculum copilot.

Auto-loads every persisted course's knowledge graph (Course->Module->Topic->Lesson->
LearningComponent/Assessment, LC schema; whatever course JSONL pairs currently exist in data/,
sharing one standards backbone) into Neo4j + Qdrant (both on-disk, so this survives restarts) and
lets a teacher chat with it. The model decides itself whether to query the graph, search
semantically, or both — see agent.py for the tool-use loop (AGENT_SYSTEM_TEMPLATE / TOOLS /
answer_with_tools).

Needs a Claude API key (sidebar, or set ANTHROPIC_API_KEY) and a reachable Neo4j
(docker compose up -d in this directory starts one with the right credentials by default).
"""
import os

from dotenv import load_dotenv
import streamlit as st

import graph_store as gs
import semantic_index as si
from agent import answer_with_tools, build_system_prompt, load_all_courses

load_dotenv()  # reads .env into os.environ if present -- so NEO4J_URI/QDRANT_HOST/etc take
                # effect from `streamlit run app.py` alone, no manual `source .env` needed


def _default_api_key():
    """Key from Streamlit secrets (cloud) or env var (local), else blank."""
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


@st.cache_resource(show_spinner="Loading all courses into Neo4j + Qdrant…")
def load_course():
    """Cached at the process level — runs once per server lifetime, every browser session
    after the first gets it for free. Neo4j graph load is cheap and stays idempotent (MERGE) so
    it's fine to redo every launch. The Qdrant curriculum_content collection is built ONCE by
    build_content_index.py — OpenAI embeddings cost money per call, so this just opens the
    already-built on-disk collection rather than re-embedding on every app start."""
    neo4j_uri = os.environ.get("NEO4J_URI", gs.DEFAULT_URI)
    neo4j_mode = "REMOTE" if os.environ.get("NEO4J_URI") else "local (default)"
    print(f"[stack] Neo4j: {neo4j_mode} -- {neo4j_uri}", flush=True)

    qdrant_host = os.environ.get("QDRANT_HOST")
    if qdrant_host:
        qdrant_port = os.environ.get("QDRANT_PORT", 6333)
        print(f"[stack] Qdrant: REMOTE -- {qdrant_host}:{qdrant_port}", flush=True)
    else:
        print(f"[stack] Qdrant: local (default) -- {si.DEFAULT_PATH}", flush=True)

    nodes, rels = load_all_courses()
    # Derived from whatever's actually loaded, never hardcoded -- a stale "Algebra I + Grade 8
    # Math" caption/placeholder survived multiple grades' worth of K-5 course additions this
    # session before anyone noticed, because nothing forced it to track the real course list.
    course_names = sorted(n["properties"]["name"] for n in nodes if n["labels"][0] == "Course")
    driver, schema_doc = None, None
    try:
        driver = gs.get_driver(neo4j_uri,
                                os.environ.get("NEO4J_USER", gs.DEFAULT_USER),
                                os.environ.get("NEO4J_PASSWORD", gs.DEFAULT_PASSWORD))
        gs.load_graph(driver, nodes, rels)
        schema_doc = gs.schema_description(nodes, rels)
        print(f"[stack] Neo4j connected OK -- {len(nodes)} nodes / {len(rels)} rels loaded", flush=True)
    except Exception as e:
        print(f"[stack] Neo4j connection FAILED: {e}", flush=True)
        driver = None
    qc = si.get_shared_client()
    print(f"[stack] Qdrant client mode: {type(qc._client).__name__}", flush=True)
    openai_client = None
    if os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI
        openai_client = OpenAI()
    return driver, schema_doc, qc, openai_client, course_names


# ── Page ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Curriculum Copilot", page_icon="🧭", layout="wide")
st.title("🧭 Curriculum Copilot")

with st.sidebar:
    api_key = st.text_input("Claude API key", type="password", value=_default_api_key(),
                            help="Or set ANTHROPIC_API_KEY as an env var / Streamlit secret.")

driver, schema_doc, qdrant_client, openai_client, course_names = load_course()
st.caption(f"Ask questions grounded in the real Bluebonnet curriculum "
           f"({', '.join(course_names) or 'no courses loaded'}, standards-aligned) — the model "
           f"cites the specific Topic/Lesson, standard, and page.")
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
q = st.chat_input("Ask about any Bluebonnet course…")
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
        system = build_system_prompt(schema_doc, os.environ.get("PDF_BASE_URL"))
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
