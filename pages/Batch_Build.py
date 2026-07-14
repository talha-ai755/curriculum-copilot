#!/usr/bin/env python3
"""
Batch Build — ingest MANY curriculum PDFs at once via the Claude Batch API (~50% cheaper).

Flow: upload all module PDFs → submit the page-chunks as one or more size-capped batches
→ let them run (async) → build a single merged graph (modules deduped onto the shared TEKS
backbone, tied under a Course node). Batch state is saved to disk so you can close the tab.

Note: chunks are base64 PDFs, so a whole grade is hundreds of MB. A single batch request
can't carry that, so we split submissions into ~SIZE_CAP-sized batches automatically.
"""
import base64
import io
import json
import os

import streamlit as st

import pdf_to_kg as p2k

STATE_FILE = ".batch_state.json"
BUNDLED_CASE = "teks_math_case.json"
SIZE_CAP = 24_000_000                       # ~24 MB of base64 per batch request

st.set_page_config(page_title="Batch Build", page_icon="📦", layout="wide")
st.title("📦 Batch Build — many PDFs at once")
st.caption("Ingest a whole grade of modules through the Claude Batch API (half price). "
           "Submit once, come back when it's done, get one merged graph.")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except Exception:
            return None
    return None


def save_state(d):
    json.dump(d, open(STATE_FILE, "w"))


def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Claude API key", type="password",
                            value=os.environ.get("ANTHROPIC_API_KEY", ""))
    framework = st.text_input("Standards framework", value="TEKS")
    jurisdiction = st.text_input("Jurisdiction", value="Texas")
    pages_per_chunk = st.slider("Pages per chunk", 5, 30, 15)
    course_name = st.text_input("Course name (ties modules together)", value="Grade 7 Mathematics")
    use_bundled = st.checkbox("Use bundled TEKS framework", value=os.path.exists(BUNDLED_CASE))


def client():
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def load_case():
    if use_bundled and os.path.exists(BUNDLED_CASE):
        return p2k.load_case(json.load(open(BUNDLED_CASE)))
    return None


state = load_state()

# ── no active batch → upload + submit ───────────────────────────────────────
if not state:
    files = st.file_uploader("Curriculum PDFs (all modules)", type="pdf",
                             accept_multiple_files=True)
    if files:
        import pypdf
        per, total_chunks = [], 0
        for f in files:
            n = len(pypdf.PdfReader(io.BytesIO(f.getvalue())).pages)
            c = -(-n // pages_per_chunk)
            per.append((f.name, n, c)); total_chunks += c
        st.table({"file": [p[0] for p in per], "pages": [p[1] for p in per],
                  "chunks": [p[2] for p in per]})
        st.info(f"**{total_chunks} chunks** across {len(files)} PDFs → submitted as "
                f"size-capped batches (~50% cheaper than interactive).")

        if st.button("🚀 Submit", type="primary", disabled=not api_key,
                     use_container_width=True):
            from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
            from anthropic.types.messages.batch_create_params import Request
            cli = client()
            names, batch_ids = [f.name for f in files], []
            group, gsize, done = [], 0, 0     # a size-capped batch under construction
            prog = st.progress(0.0, "Splitting & submitting…")
            for pi, f in enumerate(files):
                reader = pypdf.PdfReader(io.BytesIO(f.getvalue()))
                n = len(reader.pages)
                for start in range(0, n, pages_per_chunk):
                    end = min(start + pages_per_chunk, n)
                    w = pypdf.PdfWriter()
                    for i in range(start, end):
                        w.add_page(reader.pages[i])
                    buf = io.BytesIO(); w.write(buf)
                    b64 = base64.standard_b64encode(buf.getvalue()).decode()
                    if group and gsize + len(b64) > SIZE_CAP:   # flush before adding
                        batch_ids.append(cli.messages.batches.create(requests=group).id)
                        group, gsize = [], 0
                    params = p2k.chunk_message_params(b64, f"pages {start + 1}-{end}")
                    group.append(Request(custom_id=f"{pi}_{start}_{end}",
                                         params=MessageCreateParamsNonStreaming(**params)))
                    gsize += len(b64); done += 1
                    prog.progress(done / total_chunks, f"prepared {done}/{total_chunks} chunks")
            if group:
                batch_ids.append(cli.messages.batches.create(requests=group).id)
            save_state({"batches": batch_ids, "names": names, "framework": framework,
                        "jurisdiction": jurisdiction, "course": course_name})
            st.success(f"Submitted {len(batch_ids)} batch(es), {total_chunks} requests. "
                       "Come back and refresh — most finish within an hour.")
            st.rerun()
    else:
        st.info("Upload the module PDFs (e.g. all 5 Grade 7 math modules) to begin.")

# ── active batch(es) → status + build ───────────────────────────────────────
else:
    st.write(f"**Active:** {len(state['batches'])} batch(es) · {len(state['names'])} PDFs")
    st.caption("Files: " + ", ".join(state["names"]))
    c1, c2, c3 = st.columns(3)
    refresh = c1.button("🔄 Refresh status", use_container_width=True)
    build = c2.button("🔨 Build graph", type="primary", use_container_width=True)
    if c3.button("🗑 Clear / new", use_container_width=True):
        clear_state(); st.rerun()

    if (refresh or build) and not api_key:
        st.warning("Enter your Claude API key in the sidebar.")
    elif refresh or build:
        cli = client()
        statuses, succ, proc, err = [], 0, 0, 0
        for bid in state["batches"]:
            b = cli.messages.batches.retrieve(bid)
            statuses.append(b.processing_status)
            succ += b.request_counts.succeeded; proc += b.request_counts.processing
            err += b.request_counts.errored
        all_ended = all(s == "ended" for s in statuses)
        st.write(f"**Status:** {sum(s=='ended' for s in statuses)}/{len(statuses)} batches ended · "
                 f"✅ {succ}  ⏳ {proc}  ❌ {err}")
        if not all_ended:
            st.info("Still processing — refresh again shortly.")
        elif build:
            with st.spinner("Collecting results and building the graph…"):
                by_pdf = [[] for _ in state["names"]]
                skipped = 0
                for bid in state["batches"]:
                    for res in cli.messages.batches.results(bid):
                        if res.result.type == "succeeded":
                            try:
                                data = p2k.parse_extraction(res.result.message)
                                by_pdf[int(res.custom_id.split("_")[0])].append(data)
                            except Exception:
                                skipped += 1
                        else:
                            skipped += 1
                nodes, rels = p2k.combine_modules(by_pdf, jurisdiction, framework,
                                                  load_case(), state.get("course"))
            st.session_state["batch_graph"] = (nodes, rels)
            if skipped:
                st.warning(f"{skipped} chunks failed and were skipped.")
            st.success(f"Built graph: {len(nodes)} nodes, {len(rels)} edges.")

# ── show / download the built graph ─────────────────────────────────────────
if "batch_graph" in st.session_state:
    from collections import Counter
    nodes, rels = st.session_state["batch_graph"]
    ids = {n["identifier"] for n in nodes}
    dang = sum(1 for r in rels for k in ("source_identifier", "target_identifier") if r[k] not in ids)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Nodes", len(nodes)); m2.metric("Edges", len(rels))
    m3.metric("Modules", sum(1 for n in nodes if n["labels"][0] == "LessonGrouping"
                             and n["properties"].get("groupName") == "Module"))
    m4.metric("Dangling", dang)
    st.write("Node types:", dict(Counter(n["labels"][0] for n in nodes)))
    st.write("Edge types:", dict(Counter(r["label"] for r in rels)))
    st.download_button("⬇ nodes.jsonl", "\n".join(json.dumps(n) for n in nodes),
                       file_name="nodes.jsonl", use_container_width=True)
    st.download_button("⬇ relationships.jsonl", "\n".join(json.dumps(r) for r in rels),
                       file_name="relationships.jsonl", use_container_width=True)
