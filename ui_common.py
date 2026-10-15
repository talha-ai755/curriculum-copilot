"""
ui_common.py — shared pieces of the Streamlit pages: the user-name sign-in (MVP, no password),
admin check, one shared Neo4j / Qdrant / OpenAI connection per server process, the one-time
library preparation, and the list of curricula a user can work with.
"""
import os
import shutil

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

import agent                 # noqa: E402
import graph_store as gs     # noqa: E402
import registry              # noqa: E402
import semantic_index as si  # noqa: E402
import views                 # noqa: E402


def admins():
    return {n.strip().lower() for n in os.environ.get("ADMIN_USERS", "").split(",") if n.strip()}


def sign_in():
    """Sidebar user-name box. Returns (user, is_admin); stops the page until a name is entered."""
    with st.sidebar:
        name = st.text_input("Your name", value=st.session_state.get("user", ""),
                             help="MVP sign-in: just your name. Your own curricula are kept under it.")
        name = (name or "").strip()
        if name:
            st.session_state["user"] = name
            admin = name.lower() in admins()
            st.caption(f"Signed in as **{name}**" + (" · admin" if admin else ""))
    if not name:
        st.info("Enter your name in the sidebar to start.")
        st.stop()
    return name, name.lower() in admins()


@st.cache_resource(show_spinner=False)
def stores():
    """{"driver", "qdrant", "openai", "neo4j_error"} -- shared by every page and session."""
    driver, err = None, ""
    try:
        driver = gs.get_driver(os.environ.get("NEO4J_URI", gs.DEFAULT_URI),
                               os.environ.get("NEO4J_USER", gs.DEFAULT_USER),
                               os.environ.get("NEO4J_PASSWORD", gs.DEFAULT_PASSWORD))
        driver.verify_connectivity()
    except Exception as e:     # app still runs; chat falls back to search only
        driver, err = None, str(e)
    openai_client = None
    if os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI
        openai_client = OpenAI()
    return {"driver": driver, "qdrant": si.get_shared_client(), "openai": openai_client,
            "neo4j_error": err}


def _unscoped_points(qdrant):
    from qdrant_client.models import Filter, IsEmptyCondition, PayloadField
    if not qdrant.collection_exists(si.COLLECTION):
        return 0
    return qdrant.count(si.COLLECTION, exact=True, count_filter=Filter(
        must=[IsEmptyCondition(is_empty=PayloadField(key="scope"))])).count


@st.cache_resource(show_spinner="Preparing the curriculum library (first start only takes longer)…")
def prepare_library():
    """Once per server process: tag an index built before scopes existed (after backing it up,
    no re-embedding), load every base curriculum into Neo4j, and mark versions whose content is
    already in both stores as published. Returns {"nodes", "rels", "schema"}."""
    s = stores()
    nodes, rels = agent.load_all_courses()
    qdrant = s["qdrant"]
    if _unscoped_points(qdrant):
        backup = si.DEFAULT_PATH + ".before-scopes"
        if not os.environ.get("QDRANT_HOST") and not os.path.exists(backup):
            shutil.copytree(si.DEFAULT_PATH, backup)
        si.tag_scopes(qdrant, {n["identifier"]: n["properties"]["scope"] for n in nodes})
    if s["driver"] is not None:
        gs.load_graph(s["driver"], nodes, rels)
    with registry.connect() as conn:
        for v in registry.published_versions(conn):
            if not v.get("published_at"):
                sc = registry.scope_of(v["id"], v["version"])
                if qdrant.collection_exists(si.COLLECTION) and qdrant.count(
                        si.COLLECTION, count_filter=si._scope_filter(sc)).count:
                    registry.set_version(conn, v["id"], v["version"], published_at=registry.now())
    return {"nodes": len(nodes), "rels": len(rels), "schema": gs.schema_description(nodes, rels)}


def curricula_for(user):
    """[{"label", "scope", "name", "kind": "view"|"base", "id"}] the user can chat with: their own
    curricula first, then every published base curriculum."""
    out = []
    with registry.connect() as conn:
        for v in views.list_views(conn, user):
            out.append({"label": f"✏️ {v['name']} (mine)", "scope": views.scope(v["id"]),
                        "name": v["name"], "kind": "view", "id": v["id"]})
        for c in registry.list_curricula(conn):
            v = registry.latest_published(conn, c["id"])
            if v:
                out.append({"label": f"📘 {c['name']} — base v{v['version']}",
                            "scope": registry.scope_of(c["id"], v["version"]),
                            "name": c["name"], "kind": "base", "id": c["id"]})
    return out


def ensure_view_published(view_id):
    """Publish a view that has never been published or has unpublished changes."""
    import publish
    s = stores()
    with registry.connect() as conn:
        view = views.get_view(conn, view_id)
        if views.has_unpublished_changes(view):
            report = publish.publish_view(conn, view_id, s["driver"], s["qdrant"], s["openai"])
            views.mark_published(conn, view_id)
            return report
    return None
