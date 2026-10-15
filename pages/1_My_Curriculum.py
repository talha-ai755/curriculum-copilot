"""
My Curriculum — build and edit your own curricula from the base curricula, without changing
them for anyone else. Every change is an entry in your curriculum's edit log (views.py), so it
can be undone, and it survives upgrading to a newer version of a base curriculum.
"""
import streamlit as st

import frameworks
import pdf_to_kg as p2k
import registry
import ui_common as ui
import views

st.set_page_config(page_title="My Curriculum", page_icon="✏️", layout="wide")
st.title("✏️ My Curriculum")
user, is_admin = ui.sign_in()
ui.prepare_library()


def rerun():
    st.rerun()


def op(view_id, name, payload):
    with registry.connect() as conn:
        views.add_op(conn, view_id, name, payload)
    rerun()


def indent(depth):
    return " " * max(depth, 0)


def framework_for(conn, view):
    cid = next(iter(view["pins"]), None)
    return registry.get_curriculum(conn, cid)["framework_id"] if cid else None


# ── pick or create a curriculum ────────────────────────────────────────────
with registry.connect() as conn:
    mine = views.list_views(conn, user)
    bases = [c for c in registry.list_curricula(conn) if registry.latest_ready(conn, c["id"])]

with st.expander("➕ New curriculum", expanded=not mine):
    if not bases:
        st.info("No base curricula yet — an admin adds them on the Library page.")
    else:
        subjects = sorted({b["subject"] for b in bases})
        c1, c2 = st.columns(2)
        new_name = c1.text_input("Name", placeholder="e.g. My Grade 3 Math")
        subject = c2.selectbox("Subject", subjects)
        same = {b["id"]: b["name"] for b in bases if b["subject"] == subject}
        start = st.radio("Start from", ["A copy of a base curriculum", "Empty"], horizontal=True)
        fork = None
        if start.startswith("A copy"):
            fork = st.selectbox("Base curriculum to copy", list(same), format_func=lambda k: same.get(k, k))
        sources = st.multiselect(
            "Base curricula you can add modules / lessons from",
            list(same), default=[fork] if fork else [], format_func=lambda k: same.get(k, k),
            help="You can add more later. Only curricula of the same subject can be combined.")
        if st.button("Create", type="primary", disabled=not (new_name and (sources or fork))):
            with registry.connect() as conn:
                pins = {cid: registry.latest_ready(conn, cid)["version"]
                        for cid in set(sources) | ({fork} if fork else set())}
                vid = views.create_view(conn, user, new_name.strip(), subject, pins, fork_from=fork)
            st.session_state["view_id"] = vid
            rerun()

if not mine:
    st.stop()

ids = [v["id"] for v in mine]
sel = st.session_state.get("view_id") if st.session_state.get("view_id") in ids else ids[0]
view_id = st.selectbox("Your curricula", ids, index=ids.index(sel),
                       format_func=lambda i: next(v["name"] for v in mine if v["id"] == i))
st.session_state["view_id"] = view_id

with registry.connect() as conn:
    view = views.get_view(conn, view_id)
    outline, conflicts = views.outline(conn, view_id)
    items, _, _ = views.build_tree(conn, view)
    history = views.ops(conn, view_id, include_undone=True)
    fw_id = framework_for(conn, view)
    pin_info = []
    for cid, ver in view["pins"].items():
        c = registry.get_curriculum(conn, cid)
        latest = registry.latest_ready(conn, cid)
        pin_info.append((cid, c["name"], ver, latest["version"] if latest else ver))
    addable = [b for b in bases if b["subject"] == view["subject"] and b["id"] not in view["pins"]]

# ── header: publish state, rename, delete, sources ─────────────────────────
h1, h2, h3, h4 = st.columns([3, 1, 1, 1])
dirty = views.has_unpublished_changes(view)
h1.markdown(f"**{view['name']}** · {view['subject']} · "
            + ("🟠 unpublished changes" if dirty else "🟢 published — the chat uses this version"))
if h2.button("💾 Save & publish", type="primary", disabled=not dirty, use_container_width=True):
    with st.spinner("Publishing your curriculum to the chat (unchanged text is reused)…"):
        report = ui.ensure_view_published(view_id)
    st.success(f"Published · {report['index']['embedded']} new/edited texts embedded"
               if report else "Already published")
if h3.button("↶ Undo", disabled=not any(not o["undone"] for o in history), use_container_width=True):
    with registry.connect() as conn:
        views.undo(conn, view_id)
    rerun()
if h4.button("↷ Redo", disabled=not any(o["undone"] for o in history), use_container_width=True):
    with registry.connect() as conn:
        views.redo(conn, view_id)
    rerun()

with st.expander("⚙️ Curriculum settings: name, sources, upgrades, delete"):
    c1, c2 = st.columns([3, 1])
    nn = c1.text_input("Rename", value=view["name"], key=f"rename_{view_id}")
    if c2.button("Rename", disabled=nn.strip() in ("", view["name"])):
        with registry.connect() as conn:
            views.rename_view(conn, view_id, nn.strip())
        rerun()
    st.markdown("**Source curricula**")
    for cid, name, ver, latest in pin_info:
        a, b = st.columns([3, 2])
        a.write(f"{name} — v{ver}" + (f" (v{latest} available)" if latest > ver else ""))
        if latest > ver:
            key = f"upg_{view_id}_{cid}"
            if b.button(f"Check upgrade to v{latest}", key=key):
                with registry.connect() as conn:
                    preview = {**view, "pins": {**view["pins"], cid: latest}}
                    _, conf = views.build_tree(conn, preview)[:2]
                st.session_state[key + "_conf"] = conf
            if key + "_conf" in st.session_state:
                conf = st.session_state[key + "_conf"]
                if conf:
                    st.warning(f"{len(conf)} of your changes no longer match the new version and "
                               f"would be skipped: " + "; ".join(f"{c['op']} ({c['reason']})" for c in conf[:5]))
                else:
                    st.success("All your changes still apply to the new version.")
                if st.button(f"Upgrade {name} to v{latest}", key=key + "_go"):
                    with registry.connect() as conn:
                        views.set_pin(conn, view_id, cid, latest)
                    del st.session_state[key + "_conf"]
                    rerun()
    if addable:
        addable_names = {b["id"]: b["name"] for b in addable}
        extra = st.selectbox("Add another source curriculum", [""] + list(addable_names),
                             format_func=lambda i: addable_names.get(i, "—"))
        if extra and st.button("Add source"):
            with registry.connect() as conn:
                views.set_pin(conn, view_id, extra, registry.latest_ready(conn, extra)["version"])
            rerun()
    st.divider()
    if st.checkbox("I want to delete this curriculum"):
        if st.button("Delete permanently", type="secondary"):
            import publish
            s = ui.stores()
            publish.unpublish_view(view_id, s["driver"], s["qdrant"])
            with registry.connect() as conn:
                views.delete_view(conn, view_id)
            st.session_state.pop("view_id", None)
            rerun()

if conflicts:
    st.warning(f"{len(conflicts)} change(s) could not be applied and are skipped: " +
               "; ".join(f"#{c['seq']} {c['op']}: {c['reason']}" for c in conflicts[:6]))

targets = {"root": "(top level)"}
targets.update({o["key"]: indent(o["depth"]) + o["name"][:80] for o in outline
                if o["label"] == "LessonGrouping"})
by_key = {o["key"]: o for o in outline}

left, right = st.columns(2)

# ── left: add from a base curriculum ───────────────────────────────────────
with left:
    st.subheader("Add from a base curriculum")
    pins_by_id = {p[0]: p for p in pin_info}
    src_id = st.selectbox("Source", list(pins_by_id),
                          format_func=lambda c: f"{pins_by_id[c][1]} (v{pins_by_id[c][2]})")
    with registry.connect() as conn:
        base_items = {b["base_id"]: b for b in views.base_outline(conn, src_id, pins_by_id[src_id][2])}
    present = {o["key"] for o in outline}

    def base_label(bid):
        b = base_items[bid]
        mark = "✅ " if f"{src_id}:{bid}" in present else ""
        return f"{indent(b['depth'])}{mark}{b['name'][:90]}"

    choice = st.selectbox("Module / topic / lesson", list(base_items), format_func=base_label)
    target = st.selectbox("Put it under", list(targets), format_func=lambda k: targets.get(k, k), key="add_target")
    where = st.radio("Position", ["At the end", "At the start"], horizontal=True, key="add_pos")
    already = f"{src_id}:{choice}" in present
    if st.button("➕ Add to my curriculum", disabled=already,
                 help="Already in your curriculum" if already else None):
        op(view_id, "include", {"curriculum_id": src_id, "base_id": choice,
                                "parent": target, "position": 0 if where == "At the start" else None})
    with st.form("new_group", clear_on_submit=True):
        st.markdown("**Your own module or topic**")
        g1, g2 = st.columns(2)
        gname = g1.text_input("Name")
        gkind = g2.selectbox("Kind", ["topic", "module"])
        gparent = st.selectbox("Under", list(targets), format_func=lambda k: targets.get(k, k))
        if st.form_submit_button("Create") and gname.strip():
            op(view_id, "add_group", {"key": views.new_group_key(), "kind": gkind,
                                      "name": gname.strip(), "parent": gparent})

# ── right: your curriculum ─────────────────────────────────────────────────
with right:
    st.subheader("Your curriculum")
    st.caption(f"{sum(o['label'] == 'Lesson' for o in outline)} lessons · "
               f"{sum(o['label'] == 'LessonGrouping' for o in outline)} modules/topics")
    if not outline:
        st.info("Empty — add modules, topics or lessons from the left.")
        st.stop()
    st.markdown("\n".join(
        f"{indent(o['depth'])}{'📁' if o['label'] == 'LessonGrouping' else '📄'} {o['name'][:90]}"
        + ("" if o["source"] == "yours" else f" <span style='color:gray;font-size:0.8em'>· {o['source']}</span>")
        + "  " for o in outline[:400]), unsafe_allow_html=True)
    if len(outline) > 400:
        st.caption(f"… {len(outline) - 400} more")

    st.divider()
    item_key = st.selectbox("Select an item to change", list(by_key),
                            format_func=lambda k: f"{indent(by_key[k]['depth'])}{by_key[k]['name'][:90]}",
                            key="sel_item")
    item = by_key[item_key]
    siblings = [o["key"] for o in outline if o["parent"] == item["parent"]]
    i = siblings.index(item["key"])
    b1, b2, b3 = st.columns(3)
    if b1.button("⬆ Move up", disabled=i == 0, use_container_width=True):
        op(view_id, "move", {"key": item["key"], "parent": item["parent"], "position": i - 1})
    if b2.button("⬇ Move down", disabled=i == len(siblings) - 1, use_container_width=True):
        op(view_id, "move", {"key": item["key"], "parent": item["parent"], "position": i + 1})
    if b3.button("🗑 Remove", use_container_width=True):
        op(view_id, "remove", {"key": item["key"]})
    m1, m2 = st.columns([3, 1])
    dest = m1.selectbox("Move into", [k for k in targets if k != item["key"]],
                        format_func=lambda k: targets.get(k, k), key="move_dest")
    if m2.button("Move", use_container_width=True):
        op(view_id, "move", {"key": item["key"], "parent": dest, "position": None})

    it = items[item["key"]]
    with st.expander("✏️ Edit details", expanded=True):
        n1, n2 = st.columns([3, 1])
        new_title = n1.text_input("Title", value=it["props"].get("name", ""), key=f"title_{item['key']}")
        if n2.button("Save title", key=f"st_{item['key']}") and new_title != it["props"].get("name", ""):
            op(view_id, "edit", {"key": item["key"], "field": "name", "value": new_title})
        if it["label"] == "Lesson":
            mats = it["props"].get("materialsNeeded") or []
            mats_txt = st.text_input("Materials (comma separated)",
                                     value=", ".join(mats) if isinstance(mats, list) else str(mats),
                                     key=f"mat_{item['key']}")
            if st.button("Save materials", key=f"sm_{item['key']}"):
                op(view_id, "edit", {"key": item["key"], "field": "materialsNeeded",
                                     "value": [m.strip() for m in mats_txt.split(",") if m.strip()]})
            lcs = [k for k in it["children"] if items[k]["label"] == "LearningComponent"]
            if lcs:
                lc = st.selectbox("Activity / content", lcs, key=f"lc_{item['key']}",
                                  format_func=lambda k: (items[k]["props"].get("description") or "")[:100])
                txt = st.text_area("Text", value=items[lc]["props"].get("description", ""),
                                   key=f"lct_{lc}", height=120)
                if st.button("Save text", key=f"slc_{lc}") and txt != items[lc]["props"].get("description", ""):
                    op(view_id, "edit", {"key": lc, "field": "description", "value": txt})

    if it["label"] in ("Lesson", "LessonGrouping") and fw_id:
        with st.expander("🎯 Standards"):
            case = frameworks.load_case(fw_id)
            base_al = set()
            if it["base"]:
                cid, ver, bid = it["base"]
                with registry.connect() as conn:
                    g = views._base_for(conn, cid, ver)
                base_al = {r["target_identifier"] for r in g["align"].get(bid, [])
                           if r["label"] == "hasEducationalAlignment"}
            current_al = (base_al | it["align_add"]) - it["align_remove"]
            code = lambda u: case["by_uuid"].get(u, {}).get("code") or u[:8]
            for u in sorted(current_al, key=code):
                s1, s2 = st.columns([4, 1])
                s1.write(f"**{code(u)}** — {case['by_uuid'].get(u, {}).get('statement', '')[:120]}")
                if s2.button("Remove", key=f"ra_{item['key']}_{u}"):
                    op(view_id, "remove_alignment", {"key": item["key"], "standard_id": u})
            a1, a2 = st.columns([3, 1])
            new_code = a1.text_input("Add a standard by code (e.g. 3.4E)", key=f"ac_{item['key']}")
            if a2.button("Add", key=f"aa_{item['key']}") and new_code.strip():
                uid = case["code_to_uuid"].get(p2k._norm_code(new_code))
                if uid:
                    op(view_id, "add_alignment", {"key": item["key"], "standard_id": uid})
                else:
                    st.error(f"No standard {new_code!r} in {frameworks.get(fw_id)['name']}")

# ── coverage & history ─────────────────────────────────────────────────────
c1, c2 = st.columns(2)
with c1.expander("📊 Standards coverage (combined curricula)"):
    with registry.connect() as conn:
        cov = views.coverage(conn, view_id)
    case = frameworks.load_case(fw_id) if fw_id else {"by_uuid": {}}
    code = lambda u: case["by_uuid"].get(u, {}).get("code") or u[:8]
    st.write(f"**{len(cov['covered'])}** standards taught in your curriculum.")
    st.write(f"**Gaps** — taught by your source curricula but not by yours ({len(cov['gaps'])}): "
             + ", ".join(sorted(code(u) for u in cov["gaps"])[:150]))
    st.write(f"**Overlaps** — taught by lessons from 2+ sources ({len(cov['overlaps'])}): "
             + ", ".join(sorted(code(u) for u in cov["overlaps"])[:150]))
with c2.expander(f"🕘 History ({sum(not o['undone'] for o in history)} changes)"):
    for o in reversed(history[-60:]):
        st.caption(f"{'~~' if o['undone'] else ''}#{o['seq']} {o['op']} "
                   f"{ {k: (str(v)[:40]) for k, v in o['payload'].items() if k != 'key'} }"
                   f"{'~~ (undone)' if o['undone'] else ''}")
