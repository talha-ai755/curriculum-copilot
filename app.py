#!/usr/bin/env python3
"""
Curriculum Copilot -- Streamlit app, chat page.

Sign in with your name (sidebar), pick a curriculum -- one of your own (built on the
"My Curriculum" page) or a base curriculum -- and ask questions; answers come only from that
curriculum (its scope in Neo4j + Qdrant) and cite the source book's pages. Admins manage base
curricula on the "Library" page. See agent.py for the tool-use loop.

Needs ANTHROPIC_API_KEY (or the sidebar box), OPENAI_API_KEY for semantic search, and a
reachable Neo4j (`docker compose up -d` in this directory starts a local one).
"""
import os

import streamlit as st

import semantic_index as si
import ui_common as ui
from agent import answer_with_tools, build_system_prompt


def _default_api_key():
    """Key from Streamlit secrets (cloud) or env var (local), else blank."""
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


# ── Page ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Curriculum Copilot", page_icon="🧭", layout="wide")
st.title("🧭 Curriculum Copilot")
user, is_admin = ui.sign_in()
stores = ui.stores()
library = ui.prepare_library()

options = ui.curricula_for(user)
with st.sidebar:
    if not options:
        st.warning("No curriculum is available yet." + (" Add one on the Library page." if is_admin
                                                          else " Ask an admin to add one."))
        st.stop()
    labels = [o["label"] for o in options]
    default = labels.index(st.session_state["chat_curriculum"]) \
        if st.session_state.get("chat_curriculum") in labels else 0
    picked_label = st.selectbox("Curriculum", labels, index=default,
                                help="Answers come only from this curriculum.")
    st.session_state["chat_curriculum"] = picked_label
    api_key = st.text_input("Claude API key", type="password", value=_default_api_key(),
                            help="Or set ANTHROPIC_API_KEY as an env var / Streamlit secret.")
current = options[labels.index(picked_label)]

st.caption(f"Answering from **{current['name']}** "
           f"({'your curriculum' if current['kind'] == 'view' else 'base curriculum'}) — "
           f"answers cite the specific topic/lesson, standard, and page.")
if stores["driver"] is None:
    st.warning("Neo4j unavailable — chat will rely on semantic search only. "
               "(`docker compose up -d` in this directory to start it.)")
if stores["openai"] is None:
    st.info("OPENAI_API_KEY not set — semantic_search is unavailable (the index was built "
            "with OpenAI embeddings); graph_query still works.")
elif not stores["qdrant"].collection_exists(si.COLLECTION):
    st.warning("The search index isn't built yet — run `python3 build_content_index.py` once.")

examples = [
    "What modules and topics are in this curriculum?",
    "Which lessons teach fractions, and on which pages?",
    "What standards does the first lesson cover?",
    "Give me practice problems from this curriculum for the first topic.",
]
picked = st.pills("Try an example:", examples, selection_mode="single") \
    if hasattr(st, "pills") else None
q = st.chat_input(f"Ask about {current['name']}…")
question = q or picked


def _show_transcript(transcript):
    with st.expander("🔎 How this was answered"):
        for step in transcript:
            if step["tool"] == "note":  # the model's own working note between tool calls
                st.markdown(f"_{step['input']}_")
                continue
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


history = st.session_state.setdefault("chats", {}).setdefault(current["scope"], [])
for role, msg, transcript in history:
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
        system = build_system_prompt(library["schema"], os.environ.get("PDF_BASE_URL"),
                                     curriculum={"name": current["name"], "scope": current["scope"]})
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    if current["kind"] == "view":
                        ui.ensure_view_published(current["id"])   # first use / unpublished edits
                    ans, transcript = answer_with_tools(
                        anthropic.Anthropic(api_key=api_key), system, question,
                        stores["driver"], stores["qdrant"], stores["openai"], scope=current["scope"])
                except Exception as e:
                    ans, transcript = f"⚠️ {e}", []
            st.markdown(ans)
            chain_edges = _extract_chain_edges(transcript)
            if chain_edges:
                st.caption("Progression / relationship chain found in the graph:")
                _render_chain_graph(chain_edges)
            if transcript:
                _show_transcript(transcript)
        history += [("user", question, None), ("assistant", ans, transcript)]
