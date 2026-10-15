#!/usr/bin/env python3
"""
publish.py — put a curriculum into the stores the chat answers from (Neo4j + Qdrant), each
under its own scope, without touching any other curriculum:

  publish_base(version)  a base curriculum's version, scope 'base:<id>@v<n>'; replaces the
                         curriculum's previous version (users' views are self-contained copies,
                         so they don't depend on it)
  publish_view(view)     a user's curriculum, scope 'view:<id>'; republished after every save
  unpublish_view(view)

Qdrant vectors are reused whenever the text is unchanged (semantic_index.index_scope), so a
publish only pays OpenAI for new or edited text.
"""
import json

import graph_store as gs
import registry
import semantic_index as si
import views


def _load(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def base_scoped_graph(conn, curriculum_id, version):
    cur = registry.get_curriculum(conn, curriculum_id)
    ver = registry.get_version(conn, curriculum_id, version)
    nodes, rels = _load(ver["nodes_path"]), _load(ver["rels_path"])
    sc = registry.scope_of(curriculum_id, version)
    for n in nodes:
        n["properties"]["scope"] = (f"framework:{cur['framework_id']}"
                                    if n["labels"][0] in registry.STANDARDS_LABELS else sc)
    return nodes, rels, sc


def publish_base(conn, curriculum_id, version, driver=None, qdrant=None, openai_client=None):
    nodes, rels, sc = base_scoped_graph(conn, curriculum_id, version)
    stats = {}
    if driver is not None:
        gs.replace_scope(driver, sc, nodes, rels, prefix=f"base:{curriculum_id}@v")
    if qdrant is not None:
        stats = si.index_scope(qdrant, openai_client, nodes, rels, sc)
        for v in registry.versions(conn, curriculum_id):       # older versions' points
            if v["version"] != version:
                si.delete_scope(qdrant, registry.scope_of(curriculum_id, v["version"]))
    return stats


def publish_view(conn, view_id, driver=None, qdrant=None, openai_client=None):
    """Materialize and publish one view. Returns the materialize report + index stats."""
    nodes, rels, report = views.materialize(conn, view_id)
    sc = views.scope(view_id)
    if driver is not None:
        gs.replace_scope(driver, sc, nodes, rels)
    if qdrant is not None:
        report["index"] = si.index_scope(qdrant, openai_client, nodes, rels, sc,
                                         vector_source=report["base_of"])
    return report


def unpublish_view(view_id, driver=None, qdrant=None):
    sc = views.scope(view_id)
    if driver is not None:
        gs.delete_scope(driver, scope=sc)
    if qdrant is not None:
        si.delete_scope(qdrant, sc)
