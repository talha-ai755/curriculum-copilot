#!/usr/bin/env python3
"""
Streamlit UI for the curriculum knowledge-graph pipeline.

Upload a curriculum PDF → Claude extracts its structure → a Learning-Commons-style
graph is built (nodes + relationships) → view stats, an interactive diagram, the
raw extraction, and download the JSONL.

Run:  streamlit run app.py     (from the learning_outcome/ directory)

Needs a Claude API key for the real run (paste it in the sidebar, or set
ANTHROPIC_API_KEY). "Demo mode" builds the graph from bundled sample data with no
API call, so you can try the UI without a key.
"""
import io
import json
import os

import streamlit as st

import pdf_to_kg as p2k

# Sample extraction (real Module 2 content) — powers Demo mode, no API needed.
SAMPLE_CHUNK = {
    "module_number": "2", "module_title": "Applying Proportionality",
    "module_teks": ["7.4C", "7.4D", "7.5A", "7.5C", "7.13A", "7.13B",
                    "7.13C", "7.13D", "7.13E", "7.13F"],
    "topics": [
        {"number": "1", "title": "Proportional Relationships",
         "teks": ["7.4C", "7.4D", "7.5A", "7.13A", "7.13E", "7.13F"],
         "readiness_teks": ["7.4D"], "sessions": "18",
         "learning_outcomes": [
             "Students solve multi-step problems that involve percent increase and percent decrease and financial literacy problems.",
             "Students use proportional thinking to determine scale factors and create scale drawings.",
             "Students identify constants of proportionality."]},
        {"number": "2", "title": "Financial Literacy: Interest and Budgets",
         "teks": ["7.4D", "7.13B", "7.13C", "7.13D", "7.13E"],
         "readiness_teks": ["7.4D"], "sessions": "8",
         "learning_outcomes": [
             "Students identify the components of a personal budget.",
             "Students construct a net worth statement using a financial assets and liabilities record."]},
    ],
    "standards": [{"code": "7.4D", "description": "Solve problems involving ratios, rates, and percents"}],
    "assessments": [
        {"name": "End of Topic Assessment", "topic_number": "1",
         "teks": ["7.4C", "7.4D", "7.5A", "7.5C", "7.13A", "7.13F"]},
        {"name": "End of Topic Assessment", "topic_number": "2",
         "teks": ["7.4D", "7.13B", "7.13C", "7.13E"]},
    ],
}


BUNDLED_CASE = "teks_math_case.json"   # ships with the repo: full Texas math TEKS


def _default_api_key():
    """Key from Streamlit secrets (cloud) or env var (local), else blank."""
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


def to_jsonl(records):
    return "\n".join(json.dumps(r) for r in records)


def graph_context(module, topics, standards, assessments):
    """Compact, readable serialization of the graph for grounding Claude's answers."""
    lines = [f"MODULE {module['number']}: {module['title']}",
             f"Module-level standards: {', '.join(sorted(module['teks'])) or '—'}", "", "TOPICS:"]
    for t in topics.values():
        readi = t["readiness"]
        codes = ", ".join((c + " (readiness)" if c in readi else c) for c in sorted(t["teks"]))
        lines.append(f"- Topic {t['number']}: {t['title']}  ({t['sessions']} sessions)")
        lines.append(f"    Teaches standards: {codes or '—'}")
        for o in t["outcomes"]:
            lines.append(f"    • {o}")
    if assessments:
        lines.append("\nASSESSMENTS:")
        for (name, topic_no), a in assessments.items():
            lines.append(f"- {name} (Topic {topic_no}) assesses: {', '.join(sorted(a['teks'])) or '—'}")
    if standards:
        lines.append("\nSTANDARD DESCRIPTIONS:")
        for code, desc in sorted(standards.items()):
            if desc:
                lines.append(f"- {code}: {desc}")
    return "\n".join(lines)


ASK_SYSTEM = (
    "You are a curriculum copilot for teachers and instructional coaches. Answer the "
    "question using ONLY the knowledge graph provided below — the module, its topics, the "
    "standards each topic teaches, learning outcomes, and assessments. Ground every claim in "
    "it: cite the specific Topic and standard code(s) you used. If the graph covers lesson "
    "structure and standards alignment but not the detail asked for (e.g. the actual problem "
    "text, sentence frames, or exit-ticket items), say what the graph DOES tell you and note "
    "that the full lesson materials would be needed for the rest — never invent specifics. "
    "Be concise and practical, like you're helping a teacher between classes."
)


def answer_question(client, context, question):
    with client.messages.stream(
        model=p2k.MODEL, max_tokens=1500, thinking={"type": "adaptive"},
        system=ASK_SYSTEM,
        messages=[{"role": "user", "content":
                   f"KNOWLEDGE GRAPH:\n{context}\n\nQUESTION: {question}"}],
    ) as stream:
        msg = stream.get_final_message()
    return next(b.text for b in msg.content if b.type == "text")


def prior_grade_paths(nodes, rels):
    """Walk the graph's progression edges to find, for each standard the module teaches, the
    earlier-grade standard it builds on: TX -hasStandardAlignment-> CCSS -buildsTowards->
    CCSS -hasStandardAlignment-> TX(lower grade). Returns [(from_code, to_grade, to_code,
    to_desc)]."""
    import collections
    N = {n["identifier"]: n["properties"] for n in nodes}
    align, nxt, taught = collections.defaultdict(set), collections.defaultdict(set), set()
    for r in rels:
        s, t = r["source_identifier"], r["target_identifier"]
        if r["label"] == "hasStandardAlignment":
            align[s].add(t); align[t].add(s)
        elif r["label"] == "buildsTowards":
            nxt[s].add(t); nxt[t].add(s)                 # walk the chain either direction
        elif r["label"] == "hasEducationalAlignment" and r["target_labels"][0] == "StandardsFrameworkItem":
            taught.add(t)

    def grade(uid):
        c = (N.get(uid, {}).get("code") or "").split(".")[0]
        return int(c) if c.isdigit() else None

    out, seen = [], set()
    for tx in taught:
        p = N.get(tx, {})
        g = grade(tx)
        if p.get("jurisdiction") != "Texas" or g is None:
            continue
        for ms in align[tx]:                             # TX -> CCSS (same grade)
            for ms2 in nxt[ms]:                          # CCSS -> CCSS (adjacent grade)
                for tx2 in align[ms2]:                   # CCSS -> back to TX
                    q, g2 = N.get(tx2, {}), grade(tx2)
                    if q.get("jurisdiction") == "Texas" and g2 is not None and g2 < g:
                        key = (p.get("code"), q.get("code"))
                        if key not in seen:
                            seen.add(key)
                            out.append((p.get("code"), g2, q.get("code"), q.get("description", "")))
    return sorted(out)


# ── Page ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Curriculum → Knowledge Graph", page_icon="🧭", layout="wide")
st.title("🧭 Curriculum → Knowledge Graph")
st.caption("Upload a curriculum PDF. Claude reads it, extracts the structure, and builds a "
           "standards-aligned knowledge graph in the Learning-Commons schema.")

with st.sidebar:
    st.header("Settings")
    demo = st.toggle("Demo mode (no API — sample Module 2)", value=True,
                     help="Build the graph from bundled sample data so you can try the UI "
                          "without a Claude API key.")
    api_key = st.text_input("Claude API key", type="password",
                            value=_default_api_key(),
                            help="Needed for real PDF extraction AND for asking questions. "
                                 "Or set ANTHROPIC_API_KEY as an env var / Streamlit secret.")
    framework = st.text_input("Standards framework", value="TEKS")
    jurisdiction = st.text_input("Jurisdiction", value="Texas")
    pages_per_chunk = st.slider("Pages per extraction chunk", 5, 30, 15, disabled=demo)
    st.divider()
    case_file = st.file_uploader(
        "Standards framework — CASE JSON (optional)", type="json",
        help="Official standards file (e.g. TEKS exported as a 1EdTech CASE document). "
             "Without it, standards are just the bare codes printed in the PDF — no "
             "descriptions and no cross-grade links.")
    use_bundled = st.checkbox("…or use bundled TEKS Mathematics framework",
                              value=os.path.exists(BUNDLED_CASE),
                              disabled=not os.path.exists(BUNDLED_CASE),
                              help="Ships with the app — the full Texas math TEKS "
                                   "(1,247 standards). Ignored if you upload a file above.")

pdf_file = None
if not demo:
    pdf_file = st.file_uploader("Curriculum PDF", type="pdf")
    st.info("Each ~%d-page chunk is sent to Claude for structured extraction. "
            "A 250-page book is ~17 chunks." % pages_per_chunk)
else:
    st.info("**Demo mode** is on — click *Build graph* to use bundled Module 2 data. "
            "Turn it off in the sidebar to upload your own PDF (needs an API key).")

run = st.button("🔨 Build graph", type="primary", use_container_width=True)


def run_pipeline():
    if demo:
        chunks = [SAMPLE_CHUNK]
    else:
        if not pdf_file:
            st.error("Upload a PDF first, or switch on Demo mode.")
            return None
        if not api_key:
            st.error("Enter a Claude API key (or set ANTHROPIC_API_KEY).")
            return None
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        reader_bytes = pdf_file.read()
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(reader_bytes))
        n = len(reader.pages)
        chunks, prog = [], st.progress(0.0, "Starting…")
        import base64
        starts = list(range(0, n, pages_per_chunk))
        for idx, start in enumerate(starts):
            end = min(start + pages_per_chunk, n)
            writer = pypdf.PdfWriter()
            for i in range(start, end):
                writer.add_page(reader.pages[i])
            buf = io.BytesIO(); writer.write(buf)
            b64 = base64.standard_b64encode(buf.getvalue()).decode()
            label = f"pages {start + 1}-{end}"
            prog.progress((idx) / len(starts), f"Extracting {label} …")
            try:
                chunks.append(p2k.extract_chunk(client, b64, label))
            except Exception as e:
                st.warning(f"Chunk {label} failed: {e}")
        prog.progress(1.0, "Extraction complete")

    case = None
    if case_file is not None:
        try:
            case = p2k.load_case(json.load(case_file))
        except Exception as e:
            st.warning(f"Couldn't read the CASE file (ignoring it): {e}")
    elif use_bundled and os.path.exists(BUNDLED_CASE):
        with open(BUNDLED_CASE) as fh:
            case = p2k.load_case(json.load(fh))

    module, topics, standards, assessments = p2k.merge(chunks)

    if case:  # enrich extracted standards with official descriptions
        for t in topics.values():
            for code in t["teks"]:
                if not standards.get(code):
                    stmt = case["code_to_stmt"].get(p2k._norm_code(code))
                    if stmt:
                        standards[code] = stmt

    nodes, rels = p2k.build_graph(module, topics, standards, assessments,
                                  jurisdiction, framework, case)
    return module, topics, standards, assessments, nodes, rels, case


if run:
    result = run_pipeline()
    if result:
        st.session_state["result"] = result

if "result" in st.session_state:
    module, topics, standards, assessments, nodes, rels, case = st.session_state["result"]

    from collections import Counter
    ncount, ecount = Counter(n["labels"][0] for n in nodes), Counter(r["label"] for r in rels)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Nodes", len(nodes))
    c2.metric("Edges", len(rels))
    c3.metric("Standards", ncount.get("StandardsFrameworkItem", 0))
    c4.metric("Topics", ncount.get("LessonGrouping", 0))

    # dangling-endpoint integrity check
    ids = {n["identifier"] for n in nodes}
    dangling = sum(1 for r in rels for k in ("source_identifier", "target_identifier")
                   if r[k] not in ids)
    (st.success if dangling == 0 else st.error)(
        f"Graph integrity: {len(rels)} edges, {dangling} dangling endpoints"
        + (" ✓" if dangling == 0 else " ✗"))

    # framework coverage: how many curriculum codes matched the official standards
    codes_used = sorted({c for t in topics.values() for c in t["teks"]} | set(module["teks"]))
    if case:
        matched = [c for c in codes_used if p2k._norm_code(c) in case["code_to_uuid"]]
        st.info(f"📐 Framework **{case['framework']['title']}** loaded — "
                f"**{len(matched)}/{len(codes_used)}** curriculum codes matched to official "
                f"standards ({len(case['by_uuid'])} standards, {len(case['assocs'])} links in the file).")
    else:
        st.info("No framework file loaded — standards are bare codes from the PDF (no "
                "descriptions, no cross-grade links). Upload a CASE JSON in the sidebar to enrich them.")

    tab_chat, tab_extract, tab_download = st.tabs(["💬 Chat", "📋 Extraction", "⬇ Download"])

    priors = prior_grade_paths(nodes, rels)   # vertical alignment via the Common Core bridge

    with tab_chat:
        st.caption("Test the copilot — ask questions the way a teacher would. Answers are "
                   "grounded in the graph you just built. Needs a Claude API key (sidebar).")

        if priors:
            with st.expander(f"🔻 Prior-grade connections ({len(priors)}) — what each skill builds on"):
                st.caption("Traced through the Common Core bridge: TEKS → CCSS → earlier-grade "
                           "CCSS → earlier-grade TEKS.")
                for fc, g2, tc, td in priors:
                    st.markdown(f"**{fc}** builds on **Grade {g2} · {tc}** — "
                                f"{(td[:90] + '…') if len(td) > 90 else td}")
        else:
            st.caption("💡 Load the bundled TEKS framework (sidebar) to unlock prior-grade "
                       "connections.")

        with st.expander("🔎 What the assistant can see (grounding context)"):
            st.code(graph_context(module, topics, standards, assessments), language="text")
        examples = [
            "Which topics cover TEKS 7.4D?",
            "How was 7.4D taught in a prior grade?",
            "My students bombed the Topic 1 assessment — what standards should I re-teach?",
            "What is the most critical takeaway of Topic 1?",
        ]
        picked = st.pills("Try an example:", examples, selection_mode="single") \
            if hasattr(st, "pills") else None
        q = st.chat_input("Ask about this curriculum…")
        question = q or picked

        st.session_state.setdefault("chat", [])
        for role, msg in st.session_state["chat"]:
            with st.chat_message(role):
                st.markdown(msg)

        if question:
            with st.chat_message("user"):
                st.markdown(question)
            if not api_key:
                with st.chat_message("assistant"):
                    st.warning("Enter a Claude API key in the sidebar to ask questions.")
            else:
                import anthropic
                ctx = graph_context(module, topics, standards, assessments)
                if priors:
                    ctx += "\n\nPRIOR-GRADE LINKS (how each standard connects to earlier grades):\n" \
                        + "\n".join(f"- {fc} builds on Grade {g2} {tc}: {td}"
                                    for fc, g2, tc, td in priors)
                with st.chat_message("assistant"):
                    with st.spinner("Thinking…"):
                        try:
                            ans = answer_question(anthropic.Anthropic(api_key=api_key), ctx, question)
                        except Exception as e:
                            ans = f"⚠️ {e}"
                    st.markdown(ans)
                st.session_state["chat"] += [("user", question), ("assistant", ans)]

    with tab_extract:
        st.subheader(f"Module {module['number']}: {module['title']}")
        rows = [{"Topic": f"{t['number']}. {t['title']}",
                 "Sessions": t["sessions"],
                 "TEKS taught": ", ".join(sorted(t["teks"])),
                 "Learning outcomes": len(t["outcomes"])}
                for t in topics.values()]
        st.dataframe(rows, use_container_width=True)
        st.markdown("**Standards seen:** " + ", ".join(sorted(
            set(standards) | set(module["teks"])
            | {c for t in topics.values() for c in t["teks"]})))
        with st.expander("Raw extraction JSON"):
            st.json({"module": {**module, "teks": sorted(module["teks"])},
                     "topics": {k: {**v, "teks": sorted(v["teks"]),
                                    "readiness": sorted(v["readiness"])}
                                for k, v in topics.items()}})

    with tab_download:
        st.download_button("nodes.new.jsonl", to_jsonl(nodes),
                           file_name="nodes.new.jsonl", use_container_width=True)
        st.download_button("relationships.new.jsonl", to_jsonl(rels),
                           file_name="relationships.new.jsonl", use_container_width=True)
        st.caption("Same schema as the existing graph — append these to nodes.jsonl / "
                   "relationships.jsonl to grow the map.")
else:
    st.divider()
    st.markdown("👈 Configure in the sidebar, then click **Build graph**.")
