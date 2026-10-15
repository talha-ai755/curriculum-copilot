#!/usr/bin/env python3
"""
views.py — each user's own curricula ("views"), built from one or more BASE curricula of the
same subject and changed only for that user.

A view stores pins ({curriculum_id: version}) and an ordered edit log (registry.view_ops):

  fork        {curriculum_id}                         copy a whole base course into the view
  include     {curriculum_id, base_id, parent, position}   a module / topic / lesson (with
                                                      everything under it) from any pinned base
  add_group   {key, kind: module|topic, name, parent, position}   the user's own module/topic
  remove      {key}                                   an item and everything under it
  move        {key, parent, position}                 move / reorder (parent may stay the same)
  edit        {key, field, value}                     any property, incl. content text
  add_alignment    {key, standard_id}                 align a lesson/topic to a standard
  remove_alignment {key, standard_id}

materialize() replays the log over the pinned base graphs into a self-contained graph: every
item becomes a node with a view-scoped id (scope 'view:<id>'), keeping a `baseIdentifier` and
`sourceCurriculum` so citations still point at the source book's PDF; standards stay shared.
Ops whose target no longer exists (after an upgrade, or a removed parent) are reported as
conflicts and skipped -- never guessed at.

Item keys: '<curriculum_id>:<base node identifier>' for items from a base, 'u:<id>' for the
user's own groupings, 'root' for the view's course.
"""
import copy
import functools
import json
import uuid

import registry

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://your-org.example/curriculum/views")
CONTENT_LABELS = ("LearningComponent", "Assessment")
STRUCTURE_LABELS = ("LessonGrouping", "Lesson")
CONTENT_FIELDS = ("description", "sourceText")
ALIGN_EDGES = ("hasEducationalAlignment", "supports")


# ── base graphs ────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=32)
def base_graph(nodes_path, rels_path):
    """Indexed base version graph (cached; don't mutate)."""
    nodes = {}
    with open(nodes_path) as f:
        for line in f:
            if line.strip():
                n = json.loads(line)
                nodes[n["identifier"]] = n
    children, align = {}, {}
    with open(rels_path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r["label"] == "hasPart":
                children.setdefault(r["source_identifier"], []).append(r["target_identifier"])
            elif r["label"] in ALIGN_EDGES:
                align.setdefault(r["source_identifier"], []).append(r)
    course = next(i for i, n in nodes.items() if n["labels"][0] == "Course")

    def order(i):
        p = nodes[i]["properties"]
        pos = str(p.get("position") or "")
        return (nodes[i]["labels"][0] in CONTENT_LABELS,
                int(pos) if pos.isdigit() else 10 ** 6,
                p.get("pdfPageStart") or p.get("pdfPage") or 0, i)

    for k in children:
        children[k] = sorted((c for c in children[k] if c in nodes), key=order)
    return {"nodes": nodes, "children": children, "align": align, "course": course}


def _base_for(conn, curriculum_id, version):
    v = registry.get_version(conn, curriculum_id, version)
    if not v or v["status"] != "ready":
        raise ValueError(f"{curriculum_id} v{version} is not a ready version")
    return base_graph(v["nodes_path"], v["rels_path"])


# ── views & ops (registry tables) ──────────────────────────────────────────
def create_view(conn, user, name, subject, pins, fork_from=None):
    """New view. `pins`: {curriculum_id: version} (all of `subject`). With `fork_from`, the view
    starts as a full copy of that base; otherwise empty."""
    for cid in pins:
        c = registry.get_curriculum(conn, cid)
        if not c or c["subject"] != subject:
            raise ValueError(f"{cid} is not a {subject} curriculum")
    view_id = uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO views VALUES (?,?,?,?,?,?,?)",
                 (view_id, user, name, subject, json.dumps(pins), registry.now(), registry.now()))
    if fork_from:
        add_op(conn, view_id, "fork", {"curriculum_id": fork_from})
    return view_id


def get_view(conn, view_id):
    row = conn.execute("SELECT * FROM views WHERE id=?", (view_id,)).fetchone()
    if not row:
        return None
    v = dict(row)
    v["pins"] = json.loads(v.pop("pins_json"))
    return v


def list_views(conn, user):
    return [get_view(conn, r["id"]) for r in
            conn.execute("SELECT id FROM views WHERE user=? ORDER BY updated_at DESC", (user,))]


def rename_view(conn, view_id, name):
    conn.execute("UPDATE views SET name=?, updated_at=? WHERE id=?", (name, registry.now(), view_id))


def delete_view(conn, view_id):
    conn.execute("DELETE FROM view_ops WHERE view_id=?", (view_id,))
    conn.execute("DELETE FROM views WHERE id=?", (view_id,))


def set_pin(conn, view_id, curriculum_id, version):
    """Pin (add or upgrade) a base curriculum of the view's subject at `version`."""
    view = get_view(conn, view_id)
    c = registry.get_curriculum(conn, curriculum_id)
    if not c or c["subject"] != view["subject"]:
        raise ValueError(f"{curriculum_id} is not a registered {view['subject']} curriculum")
    pins = {**view["pins"], curriculum_id: int(version)}
    conn.execute("UPDATE views SET pins_json=?, updated_at=? WHERE id=?",
                 (json.dumps(pins), registry.now(), view_id))


def add_op(conn, view_id, op, payload):
    """Append an edit; redo history after an undo is discarded (like any editor)."""
    conn.execute("DELETE FROM view_ops WHERE view_id=? AND undone=1", (view_id,))
    row = conn.execute("SELECT MAX(seq) AS s FROM view_ops WHERE view_id=?", (view_id,)).fetchone()
    seq = (row["s"] or 0) + 1
    conn.execute("INSERT INTO view_ops VALUES (?,?,?,?,0,?)",
                 (view_id, seq, op, json.dumps(payload), registry.now()))
    conn.execute("UPDATE views SET updated_at=? WHERE id=?", (registry.now(), view_id))
    return seq


def ops(conn, view_id, include_undone=False):
    q = "SELECT * FROM view_ops WHERE view_id=?" + ("" if include_undone else " AND undone=0")
    return [{**dict(r), "payload": json.loads(r["payload_json"])}
            for r in conn.execute(q + " ORDER BY seq", (view_id,))]


def undo(conn, view_id):
    row = conn.execute("SELECT MAX(seq) AS s FROM view_ops WHERE view_id=? AND undone=0",
                       (view_id,)).fetchone()
    if row["s"]:
        conn.execute("UPDATE view_ops SET undone=1 WHERE view_id=? AND seq=?", (view_id, row["s"]))
    return row["s"]


def redo(conn, view_id):
    row = conn.execute("SELECT MIN(seq) AS s FROM view_ops WHERE view_id=? AND undone=1",
                       (view_id,)).fetchone()
    if row["s"]:
        conn.execute("UPDATE view_ops SET undone=0 WHERE view_id=? AND seq=?", (view_id, row["s"]))
    return row["s"]


def new_group_key():
    return "u:" + uuid.uuid4().hex[:10]


def scope(view_id):
    return f"view:{view_id}"


# ── replay ─────────────────────────────────────────────────────────────────
def build_tree(conn, view, op_list=None):
    """Replay the view's ops. Returns (items, conflicts): items = {key: {"label", "base":
    (cid, version, base_id) | None, "props": {...}, "children": [keys], "parent": key,
    "edited": set(fields), "align_add": set, "align_remove": set}}, with items["root"] the
    view's course."""
    op_list = ops(conn, view["id"]) if op_list is None else op_list
    items = {"root": {"label": "Course", "base": None, "props": {"name": view["name"]},
                      "children": [], "parent": None, "edited": set(),
                      "align_add": set(), "align_remove": set()}}
    conflicts = []
    graphs = {}

    def graph(cid):
        if cid not in view["pins"]:
            raise KeyError(f"{cid} is not pinned in this view")
        if cid not in graphs:
            graphs[cid] = _base_for(conn, cid, view["pins"][cid])
        return graphs[cid]

    def insert(parent, key, position):
        kids = items[parent]["children"]
        pos = len(kids) if position is None else max(0, min(int(position), len(kids)))
        kids.insert(pos, key)
        items[key]["parent"] = parent

    def copy_subtree(cid, base_id, parent, position):
        g = graph(cid)
        if base_id not in g["nodes"]:
            raise KeyError(f"{base_id} not in {cid} v{view['pins'][cid]}")
        key = f"{cid}:{base_id}"
        if key in items:
            raise ValueError("already in this curriculum")

        def walk(bid, parent_key, pos):
            k = f"{cid}:{bid}"
            n = g["nodes"][bid]
            items[k] = {"label": n["labels"][0], "base": (cid, view["pins"][cid], bid),
                        "props": copy.deepcopy(n["properties"]), "children": [],
                        "parent": None, "edited": set(), "align_add": set(), "align_remove": set()}
            insert(parent_key, k, pos)
            for child in g["children"].get(bid, []):
                if f"{cid}:{child}" not in items:
                    walk(child, k, None)
        walk(base_id, parent, position)

    def drop(key):
        for child in list(items[key]["children"]):
            drop(child)
        parent = items[key]["parent"]
        if parent in items and key in items[parent]["children"]:
            items[parent]["children"].remove(key)
        del items[key]

    def need(key):
        if key not in items:
            raise KeyError(f"{key} is not in this curriculum")
        return items[key]

    for o in op_list:
        p = o["payload"]
        try:
            if o["op"] == "fork":
                g = graph(p["curriculum_id"])
                items["root"]["props"].setdefault("academicSubject",
                                                  g["nodes"][g["course"]]["properties"].get("academicSubject", ""))
                for module in g["children"].get(g["course"], []):
                    if f"{p['curriculum_id']}:{module}" not in items:
                        copy_subtree(p["curriculum_id"], module, "root", None)
            elif o["op"] == "include":
                parent = p.get("parent") or "root"
                need(parent)
                copy_subtree(p["curriculum_id"], p["base_id"], parent, p.get("position"))
            elif o["op"] == "add_group":
                parent = p.get("parent") or "root"
                need(parent)
                if p["key"] in items:
                    raise ValueError("duplicate key")
                level = "0" if p["kind"] == "module" else "1"
                items[p["key"]] = {"label": "LessonGrouping", "base": None,
                                   "props": {"name": p["name"], "groupName": p["kind"].title(),
                                             "groupLevel": level, "curriculumLabel": p["kind"].title()},
                                   "children": [], "parent": None, "edited": {"name"},
                                   "align_add": set(), "align_remove": set()}
                insert(parent, p["key"], p.get("position"))
            elif o["op"] == "remove":
                need(p["key"])
                if p["key"] == "root":
                    raise ValueError("cannot remove the course itself")
                drop(p["key"])
            elif o["op"] == "move":
                it, parent = need(p["key"]), p.get("parent") or "root"
                need(parent)
                k = parent
                while k:                       # no moving an item under itself
                    if k == p["key"]:
                        raise ValueError("cannot move an item inside itself")
                    k = items[k]["parent"]
                items[it["parent"]]["children"].remove(p["key"])
                insert(parent, p["key"], p.get("position"))
            elif o["op"] == "edit":
                it = need(p["key"])
                it["props"][p["field"]] = p["value"]
                it["edited"].add(p["field"])
            elif o["op"] == "add_alignment":
                it = need(p["key"])
                it["align_add"].add(p["standard_id"])
                it["align_remove"].discard(p["standard_id"])
            elif o["op"] == "remove_alignment":
                it = need(p["key"])
                it["align_remove"].add(p["standard_id"])
                it["align_add"].discard(p["standard_id"])
            else:
                raise ValueError(f"unknown op {o['op']}")
        except (KeyError, ValueError) as e:
            conflicts.append({"seq": o["seq"], "op": o["op"], "payload": p, "reason": str(e).strip("'\"")})
    return items, conflicts, graphs


def node_id(view_id, key):
    return "yo:" + str(uuid.uuid5(NS, f"{view_id}|{key}"))


def materialize(conn, view_id):
    """(nodes, rels, report) for the view: a self-contained graph under scope 'view:<id>'.
    report = {"conflicts": [...], "edited_content": [node ids whose text changed],
              "base_of": {view node id: base node id}}."""
    view = get_view(conn, view_id)
    items, conflicts, graphs = build_tree(conn, view)
    sc = scope(view_id)
    nodes, rels, base_of, edited_content = [], [], {}, []

    def rel(label, s, s_label, t, t_label, **props):
        rid = str(uuid.uuid5(NS, f"{label}|{s}|{t}"))
        rels.append({"type": "relationship", "identifier": rid, "label": label,
                     "properties": {"identifier": rid, "relationshipType": label, **props},
                     "source_identifier": s, "source_labels": [s_label],
                     "target_identifier": t, "target_labels": [t_label]})

    def emit(key, seq):
        it = items[key]
        nid = node_id(view_id, key)
        props = {**it["props"], "identifier": nid, "scope": sc, "sequence": seq}
        if it["base"]:
            cid, ver, bid = it["base"]
            props.update(baseIdentifier=bid, sourceCurriculum=cid, sourceVersion=ver)
            base_of[nid] = bid
            if it["label"] in CONTENT_LABELS and it["edited"] & set(CONTENT_FIELDS):
                edited_content.append(nid)
        if key == "root":
            import profiles
            props.update(curriculumLabel="Course", viewOwner=view["user"])
            props.setdefault("academicSubject",
                             profiles.subject(view["subject"])["academic_subject"] or view["subject"])
        nodes.append({"type": "node", "identifier": nid, "labels": [it["label"]], "properties": props})
        if it["base"]:                          # standards alignments from the base, then edits
            cid, _ver, bid = it["base"]
            targets = {(r["label"], r["target_identifier"]): r for r in graphs[cid]["align"].get(bid, [])}
        else:
            targets = {}
        for (label, std), r in targets.items():
            if std not in it["align_remove"]:
                rel(label, nid, it["label"], std, "StandardsFrameworkItem",
                    **{k: v for k, v in r["properties"].items()
                       if k not in ("identifier", "relationshipType")})
        for std in it["align_add"]:
            if ("hasEducationalAlignment", std) not in targets:
                rel("hasEducationalAlignment", nid, it["label"], std, "StandardsFrameworkItem",
                    alignmentType="teaches", curriculumAlignmentType="addressing", addedBy="user")
        for i, child in enumerate(it["children"], 1):
            cid_node = emit(child, i)
            rel("hasPart", nid, it["label"], cid_node, items[child]["label"], position=i)
        return nid

    emit("root", 1)
    return nodes, rels, {"conflicts": conflicts, "edited_content": edited_content,
                         "base_of": base_of, "items": len(items)}


# ── helpers for the editor ─────────────────────────────────────────────────
def outline(conn, view_id):
    """Flat list for display: [{key, label, name, depth, base}] in view order (content leaves
    omitted), plus conflicts."""
    view = get_view(conn, view_id)
    items, conflicts, _ = build_tree(conn, view)
    out = []

    def walk(key, depth):
        it = items[key]
        if it["label"] in CONTENT_LABELS:
            return
        if key != "root":
            p = it["props"]
            title = p.get("name") or ""
            ordinal = p.get("ordinalName") or ""
            out.append({"key": key, "label": it["label"], "depth": depth,
                        "name": f"{ordinal}: {title}".strip(": ") if ordinal and it["label"] == "Lesson" else title,
                        "source": it["base"][0] if it["base"] else "yours",
                        "parent": it["parent"]})
        for child in it["children"]:
            walk(child, depth + 1)
    walk("root", -1)
    return out, conflicts


def base_outline(conn, curriculum_id, version):
    """[{base_id, label, name, depth}] for browsing a base when picking items to include."""
    g = _base_for(conn, curriculum_id, version)
    out = []

    def walk(bid, depth):
        n = g["nodes"][bid]
        if n["labels"][0] in CONTENT_LABELS:
            return
        if bid != g["course"]:
            p = n["properties"]
            name = p.get("name") or ""
            if n["labels"][0] == "Lesson" and p.get("ordinalName"):
                name = f"{p['ordinalName']}: {name}"
            elif p.get("ordinalName") and n["labels"][0] == "LessonGrouping":
                name = f"{p['ordinalName']}: {name}"
            out.append({"base_id": bid, "label": n["labels"][0], "name": name, "depth": depth})
        for child in g["children"].get(bid, []):
            walk(child, depth + 1)
    walk(g["course"], -1)
    return out


def coverage(conn, view_id):
    """Standards coverage of a (combined) view vs its sources: {"covered": {std: [sources]},
    "gaps": [std taught in a pinned base but not in the view], "overlaps": [std taught by lessons
    from 2+ sources]}. Standards are identifiers; resolve codes with the framework."""
    view = get_view(conn, view_id)
    items, _, graphs = build_tree(conn, view)
    covered = {}
    for it in items.values():
        if it["label"] != "Lesson":
            continue
        stds = set(it["align_add"])
        if it["base"]:
            cid, _v, bid = it["base"]
            stds |= {r["target_identifier"] for r in graphs[cid]["align"].get(bid, [])
                     if r["label"] == "hasEducationalAlignment"}
        stds -= it["align_remove"]
        for s in stds:
            covered.setdefault(s, set()).add(it["base"][0] if it["base"] else "yours")
    taught_by_sources = set()
    for cid, ver in view["pins"].items():
        g = _base_for(conn, cid, ver)
        for bid, rs in g["align"].items():
            if g["nodes"].get(bid, {}).get("labels", [""])[0] == "Lesson":
                taught_by_sources |= {r["target_identifier"] for r in rs
                                      if r["label"] == "hasEducationalAlignment"}
    return {"covered": {k: sorted(v) for k, v in covered.items()},
            "gaps": sorted(taught_by_sources - set(covered)),
            "overlaps": sorted(k for k, v in covered.items() if len(v - {"yours"}) > 1)}


def standard_codes(nodes_by_id, ids):
    return {i: (nodes_by_id.get(i, {}).get("properties", {}).get("code") or i) for i in ids}
