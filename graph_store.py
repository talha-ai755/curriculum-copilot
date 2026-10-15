#!/usr/bin/env python3
"""
graph_store.py — Neo4j load + read-only query for the curriculum knowledge graph.

Loads the same (nodes, rels) lists pdf_to_kg.build_graph() produces (LC JSONL shape:
node = {"identifier", "labels": [label], "properties": {...}}, rel = {"label", "properties":
{...}, "source_identifier", "source_labels", "target_identifier", "target_labels"}) into
Neo4j, keyed by the same deterministic `identifier` used everywhere else in this pipeline —
so re-loading the same graph never duplicates anything (MERGE, not CREATE).

Run Neo4j locally:
    docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \\
        -e NEO4J_AUTH=neo4j/changeme-local-pw neo4j:5
"""
import json
import re

import neo4j
from neo4j import RoutingControl

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "changeme-local-pw"

# Bulky verbatim text deliberately excluded from Neo4j: every LearningComponent's sourceText/
# description already lives in full in Qdrant (semantic_index.index_nodes embeds exactly these
# fields), so keeping a second copy as a graph property would just duplicate ~43% of the node's
# size for no benefit. Fetch the real text via semantic_index.get_content(identifier) once
# graph_query has found the node you want it for.
_BULK_TEXT_PROPS = {"LearningComponent": {"sourceText", "description"}}


def _graph_properties(label, properties):
    drop = _BULK_TEXT_PROPS.get(label, ())
    return {k: v for k, v in properties.items() if k not in drop}

# Belt-and-suspenders guard in front of routing_=RoutingControl.READ (which already makes
# Neo4j itself reject write clauses in a read transaction) — reject obvious write Cypher
# before it's even sent, so the model gets a fast, clear error to react to.
_WRITE_KEYWORDS = ("CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP")
_WRITE_RE = re.compile(r"\b(" + "|".join(_WRITE_KEYWORDS) + r")\b", re.IGNORECASE)
# String literals, backtick identifiers and comments are blanked out before the keyword check --
# otherwise an ordinary read like `WHERE l.name CONTAINS 'Data Set'` or `CONTAINS 'create a
# table'` was refused as a write (confirmed: both were rejected before this).
_NON_CODE_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`|//[^\n]*|/\*.*?\*/",
                          re.DOTALL)


def get_driver(uri=None, user=None, password=None):
    return neo4j.GraphDatabase.driver(uri or DEFAULT_URI,
                                       auth=(user or DEFAULT_USER, password or DEFAULT_PASSWORD))


def ensure_constraints(driver, node_labels):
    """One uniqueness constraint per label so the MERGE-by-identifier loads below are indexed."""
    for label in node_labels:
        driver.execute_query(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:`{label}`) REQUIRE n.identifier IS UNIQUE")


def load_graph(driver, nodes, rels):
    """MERGE every node/edge by its deterministic identifier — safe to call repeatedly on the
    same graph (re-ingest never duplicates), same idempotency guarantee as the JSONL pipeline."""
    by_label = {}
    for n in nodes:
        label = n["labels"][0]
        by_label.setdefault(label, []).append(
            {"identifier": n["identifier"], "properties": _graph_properties(label, n["properties"])})
    ensure_constraints(driver, by_label.keys())
    for label, rows in by_label.items():
        driver.execute_query(
            f"UNWIND $rows AS row "
            f"MERGE (n:`{label}` {{identifier: row.identifier}}) SET n += row.properties",
            rows=rows)

    by_group = {}
    for r in rels:
        key = (r["label"], r["source_labels"][0], r["target_labels"][0])
        by_group.setdefault(key, []).append(
            {"s": r["source_identifier"], "t": r["target_identifier"], "props": r["properties"]})
    for (label, s_label, t_label), rows in by_group.items():
        driver.execute_query(
            f"UNWIND $rows AS row "
            f"MATCH (s:`{s_label}` {{identifier: row.s}}) "
            f"MATCH (t:`{t_label}` {{identifier: row.t}}) "
            f"MERGE (s)-[rel:`{label}`]->(t) SET rel += row.props",
            rows=rows)


def schema_description(nodes, rels):
    """Text description of the graph shape for the agent's system prompt — built from the
    in-memory lists already on hand, no extra Neo4j round-trip needed."""
    props_by_label = {}
    for n in nodes:
        label = n["labels"][0]
        props_by_label.setdefault(label, set()).update(_graph_properties(label, n["properties"]).keys())

    edge_shapes = {}
    for r in rels:
        edge_shapes.setdefault(r["label"], set()).add((r["source_labels"][0], r["target_labels"][0]))

    lines = ["Node labels and their properties:"]
    for label, props in sorted(props_by_label.items()):
        lines.append(f"  (:{label} {{{', '.join(sorted(props))}}})")
    lines.append("")
    lines.append("Relationship types and which node labels they connect:")
    for label, pairs in sorted(edge_shapes.items()):
        for s, t in sorted(pairs):
            lines.append(f"  (:{s})-[:{label}]->(:{t})")
    lines.append("")
    lines.append("Every node's `identifier` property is the join key shared with the Qdrant "
                  "semantic-search index — a semantic_search hit's `identifier` is the same "
                  "value you can MATCH on here to pull standards, parent topic, or progression "
                  "context around it.")
    lines.append("")
    lines.append("NOTE: LearningComponent's actual verbatim text (sourceText/description) is "
                  "NOT a Neo4j property — it lives only in Qdrant to avoid storing it twice. "
                  "Once graph_query finds a LearningComponent's identifier, call "
                  "get_content(identifiers) to fetch its real text (a direct lookup, not a "
                  "similarity search) rather than expecting sourceText/description in a Cypher "
                  "result.")
    return "\n".join(lines)


def is_read_only(cypher):
    return not _WRITE_RE.search(_NON_CODE_RE.sub("''", cypher))


def run_cypher(driver, cypher, params=None):
    """Run read-only Cypher. Raises (with a message meant to be shown back to the model as the
    tool result, not just logged) if the query isn't read-only or Neo4j itself errors."""
    if not is_read_only(cypher):
        raise ValueError(f"Refusing to run a non-read-only Cypher query: {cypher!r}")
    records, _, _ = driver.execute_query(cypher, parameters_=params or {},
                                          routing_=RoutingControl.READ)
    return [r.data() for r in records]


def load_jsonl(nodes_path, rels_path):
    """Load a pre-built (nodes, rels) pair straight from disk, skipping PDF extraction."""
    with open(nodes_path) as f:
        nodes = [json.loads(line) for line in f if line.strip()]
    with open(rels_path) as f:
        rels = [json.loads(line) for line in f if line.strip()]
    return nodes, rels
