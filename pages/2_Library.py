"""
Library (admins) — the base curricula: add a new one from its module PDFs, extract it with the
Claude Batch API, build it, publish it to the chat, and update it with new PDF versions
(only changed pages are re-extracted; the cost is shown before anything is submitted).
Users' own curricula are built from these on the My Curriculum page.
"""
import os
import re
import subprocess
import sys

import streamlit as st

import frameworks
import ingest
import profiles
import publish
import registry
import ui_common as ui

st.set_page_config(page_title="Library", page_icon="📚", layout="wide")
st.title("📚 Library — base curricula")
user, is_admin = ui.sign_in()
if not is_admin:
    st.warning("Only admins manage base curricula (set ADMIN_USERS). "
               "Build your own curriculum on the My Curriculum page.")
    st.stop()
ui.prepare_library()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_RE = re.compile(r"\b(Module|Unit)\s*(\d+)", re.IGNORECASE)


def save_uploads(curriculum_id, files, tag):
    """Store uploaded PDFs under data/curricula/<id>/uploads/<tag>/ and return their paths."""
    d = os.path.join(registry.CURRICULA_DIR, curriculum_id, "uploads", tag)
    os.makedirs(d, exist_ok=True)
    paths = []
    for f in files:
        p = os.path.join(d, os.path.basename(f.name))
        with open(p, "wb") as out:
            out.write(f.getbuffer())
        paths.append(p)
    return paths


def guess_module(filename, i):
    m = _MODULE_RE.search(filename)
    return f"{m.group(1).title()} {int(m.group(2))}" if m else f"Module {i}"


def start_build(curriculum_id, version):
    """ingest.py build in a detached process (it waits for the batch, then builds); output in the
    version folder's build.log. Publishing happens back here (the local search index can only be
    opened by one process)."""
    vdir = registry.version_dir(curriculum_id, version)
    os.makedirs(vdir, exist_ok=True)
    log = open(os.path.join(vdir, "build.log"), "a")
    subprocess.Popen([sys.executable, os.path.join(ROOT, "ingest.py"), "build", curriculum_id,
                      str(version), "--user", user], cwd=ROOT, stdout=log, stderr=log,
                     start_new_session=True)
    with registry.connect() as conn:
        registry.set_version(conn, curriculum_id, version, status="building")


def log_tail(curriculum_id, version, n=15):
    p = os.path.join(registry.version_dir(curriculum_id, version), "build.log")
    if not os.path.exists(p):
        return ""
    return "".join(open(p, errors="replace").readlines()[-n:])


# ── existing base curricula ────────────────────────────────────────────────
@st.fragment(run_every=20)
def curricula_panel():
    with registry.connect() as conn:
        cur_list = registry.list_curricula(conn)
        rows = {c["id"]: registry.versions(conn, c["id"]) for c in cur_list}
        jobs = {c["id"]: registry.jobs(conn, c["id"], limit=5) for c in cur_list}
    if not cur_list:
        st.info("No base curricula yet — add one below.")
        return
    for c in cur_list:
        vs = rows[c["id"]]
        latest = vs[-1]
        badge = {"ready": "🟢", "building": "🟡", "extracting": "🟡", "draft": "⚪", "failed": "🔴"}
        with st.expander(f"{badge.get(latest['status'], '⚪')} {c['name']} — {c['subject']} · "
                         f"{len(vs)} version(s) · latest v{latest['version']} {latest['status']}"
                         + (" · published" if latest.get("published_at") else "")):
            st.caption(f"id `{c['id']}` · framework {c['framework_id']} · publisher {c['publisher_profile']}")
            for v in reversed(vs):
                st.markdown(f"**v{v['version']}** — {v['status']}"
                            + (f" · published {v['published_at'][:16]}" if v.get("published_at") else "")
                            + (f" · {v['notes']}" if v.get("notes") else ""))
                a, b, c3 = st.columns(3)
                k = f"{c['id']}_{v['version']}"
                if v["status"] == "draft":
                    with registry.connect() as conn:
                        n_mod = len(registry.modules(conn, c["id"], v["version"]))
                    if a.button(f"Submit extraction ({n_mod} module PDFs)", key="sub_" + k):
                        with st.spinner("Preparing lesson-aligned batches and submitting…"):
                            with registry.connect() as conn:
                                ingest.submit(conn, c["id"], v["version"], user)
                        st.rerun()
                if v["status"] == "extracting":
                    job = next((j for j in jobs[c["id"]] if j["version"] == v["version"]), None)
                    if job and job["batch_id"]:
                        try:
                            import anthropic
                            bt = anthropic.Anthropic().messages.batches.retrieve(job["batch_id"])
                            rc = bt.request_counts
                            a.write(f"Batch {bt.processing_status}: {rc.succeeded} done, "
                                    f"{rc.processing} running, {rc.errored} errors")
                        except Exception as e:
                            a.write(f"Batch status unavailable: {e}")
                    if b.button("Start build (runs in background)", key="bld_" + k):
                        start_build(c["id"], v["version"])
                        st.rerun()
                if v["status"] in ("building", "failed"):
                    tail = log_tail(c["id"], v["version"])
                    if tail:
                        st.code(tail, language=None)
                    if v["status"] == "failed" and b.button("Retry build", key="rb_" + k):
                        start_build(c["id"], v["version"])
                        st.rerun()
                if v["status"] == "ready" and not v.get("published_at"):
                    if a.button("Publish to chat", key="pub_" + k, type="primary"):
                        s = ui.stores()
                        with st.spinner("Loading into Neo4j and the search index (new text is embedded)…"):
                            with registry.connect() as conn:
                                stats = publish.publish_base(conn, c["id"], v["version"], s["driver"],
                                                             s["qdrant"], s["openai"])
                                registry.set_version(conn, c["id"], v["version"], published_at=registry.now())
                        st.success(f"Published v{v['version']}: {stats}")
                if v.get("diff_json"):
                    import json
                    d = json.loads(v["diff_json"])
                    c3.caption(f"vs v{d['against_version']}: +{len(d['added'])} / −{len(d['removed'])} "
                               f"/ ~{len(d['changed'])} nodes")


curricula_panel()

# ── new base curriculum ────────────────────────────────────────────────────
st.divider()
with st.expander("➕ New base curriculum"):
    subj_ids = list(profiles.subjects())
    c1, c2 = st.columns(2)
    name = c1.text_input("Name", placeholder="e.g. Algebra II")
    subject = c2.selectbox("Subject", subj_ids, format_func=lambda s: profiles.subjects()[s]["name"])
    fws = list(frameworks.all_frameworks())
    default_fw = profiles.subject(subject).get("default_framework")
    c3, c4 = st.columns(2)
    framework_id = c3.selectbox("Standards framework", fws,
                                index=fws.index(default_fw) if default_fw in fws else 0,
                                format_func=lambda f: frameworks.get(f)["name"])
    pubs = list(profiles.publishers())
    publisher = c4.selectbox("Publisher layout", pubs, index=pubs.index("generic"),
                             format_func=lambda p: profiles.publisher(p)["name"],
                             help="Bluebonnet layouts let the pipeline split work at lesson boundaries.")
    files = st.file_uploader("Module PDFs (teacher editions)", type=["pdf"], accept_multiple_files=True)
    if files:
        st.caption("Module names (edit if needed; the order here is the module order):")
        names = [st.text_input(f.name, value=guess_module(f.name, i), key=f"mn_{f.name}")
                 for i, f in enumerate(files, 1)]
    if st.button("Create curriculum", type="primary", disabled=not (name and files)):
        cid = registry.slugify(name)
        with registry.connect() as conn:
            if registry.get_curriculum(conn, cid):
                st.error(f"A curriculum with id {cid!r} already exists.")
                st.stop()
        with st.spinner("Saving PDFs…"):
            paths = save_uploads(cid, files, "v1")
            order = sorted(zip(names, paths), key=lambda np: int(re.sub(r"\D", "", np[0]) or 0))
            with registry.connect() as conn:
                ingest.create_base(conn, name.strip(), subject, publisher, framework_id,
                                   dict(order), user, curriculum_id=cid)
        st.success(f"Created {name} v1 — submit its extraction above.")
        st.rerun()

# ── update modules of an existing curriculum ───────────────────────────────
with st.expander("🔁 Update modules with new PDF versions"):
    with registry.connect() as conn:
        updatable = [c for c in registry.list_curricula(conn) if registry.latest_ready(conn, c["id"])]
    if not updatable:
        st.info("No built curriculum to update yet.")
    else:
        upd_names = {c["id"]: c for c in updatable}
        cur = upd_names[st.selectbox("Curriculum", list(upd_names), format_func=lambda i: upd_names[i]["name"])]
        with registry.connect() as conn:
            mods = [m["module"] for m in registry.modules(conn, cur["id"],
                                                          registry.latest_ready(conn, cur["id"])["version"])]
        up = st.file_uploader("New module PDFs", type=["pdf"], accept_multiple_files=True, key="upd_files")
        mapping = {}
        for i, f in enumerate(up or [], 1):
            guess = guess_module(f.name, i)
            opts = mods + ([guess] if guess not in mods else [])
            mapping[f.name] = st.selectbox(f"{f.name} replaces / adds", opts,
                                           index=opts.index(guess), key=f"map_{f.name}")
        if up and st.button("Analyze changes (no cost)"):
            with st.spinner("Comparing pages with the current version…"):
                paths = save_uploads(cur["id"], up, f"update-{registry.now()[:19].replace(':', '')}")
                new_pdfs = {mapping[os.path.basename(p)]: p for p in paths}
                with registry.connect() as conn:
                    st.session_state["plan"] = (cur["id"], ingest.plan_update(conn, cur["id"], new_pdfs))
        if st.session_state.get("plan") and st.session_state["plan"][0] == cur["id"]:
            plan = st.session_state["plan"][1]
            for module, rep in plan["modules"].items():
                if rep["status"] == "unchanged":
                    continue
                pages = rep.get("pages", {})
                st.markdown(f"**{module}** — {rep['status']}: pages changed {pages.get('changed', 0)}, "
                            f"added {pages.get('added', 0)}, removed {pages.get('removed', 0)} · "
                            f"lessons affected {[l[1] for l in rep.get('lessons_changed', [])]} · "
                            f"batches reused {len(rep.get('batches_reused', []))}, "
                            f"to extract {len(rep['batches_to_extract'])}")
                if not rep.get("has_saved_chunks", True):
                    st.caption("This module has no saved extraction results (imported before the "
                               "library existed), so all of its batches are extracted.")
            st.info(f"This update needs **{plan['calls']}** extraction call(s) (Claude Batch API).")
            if st.button(f"Create v{plan['base_version'] + 1} and submit {plan['calls']} call(s)", type="primary"):
                with st.spinner("Creating the version and submitting…"):
                    with registry.connect() as conn:
                        version, only = ingest.create_update_version(conn, cur["id"], user, plan)
                        ingest.submit(conn, cur["id"], version, user, only=only)
                    if plan["calls"] == 0:
                        start_build(cur["id"], version)
                del st.session_state["plan"]
                st.rerun()
