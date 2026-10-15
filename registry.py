#!/usr/bin/env python3
"""
registry.py — the curriculum library's source of truth: which base curricula exist, their
versions and module PDFs, ingestion jobs, and (Phase 3) each user's customized views.

SQLite at data/app.db (gitignored with the rest of data/). Replaces "a course is whatever
submit_batch_*.py files exist" (backfill_page_labels.discover_courses()) with explicit records,
so courses can be added and updated from the Streamlit app instead of by writing scripts.

A base curriculum has numbered versions; each version is an immutable graph
(nodes/relationships JSONL at `versions.nodes_path` / `rels_path`). The 10 courses ingested
before the registry existed are imported as version 1 pointing at their existing data/*.jsonl
files (see import_existing.py).
"""
import contextlib
import datetime
import hashlib
import json
import os
import re
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# Both overridable so tests can run against a scratch registry without touching data/app.db.
DB_PATH = os.environ.get("CURRICULUM_DB", os.path.join(DATA_DIR, "app.db"))
CURRICULA_DIR = os.environ.get("CURRICULUM_FILES", os.path.join(DATA_DIR, "curricula"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS curricula (
    id TEXT PRIMARY KEY,                -- slug, e.g. 'bluebonnet-grade-3-math'
    name TEXT NOT NULL,                 -- Course node name, e.g. 'Grade 3 Math'
    subject TEXT NOT NULL,              -- subject profile id, e.g. 'math'
    publisher_profile TEXT NOT NULL,    -- running-header profile, e.g. 'bluebonnet_k5'
    framework_id TEXT NOT NULL,         -- standards framework, e.g. 'teks_math'
    citation_slug TEXT NOT NULL,        -- PDF key segment: bluebonnet/<slug>/<file>.pdf -- fixed
                                        -- per curriculum, every node's sourcePdfKey depends on it
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    curriculum_id TEXT NOT NULL REFERENCES curricula(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,               -- 'draft' | 'extracting' | 'building' | 'ready' | 'failed'
    nodes_path TEXT,
    rels_path TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    notes TEXT DEFAULT '',
    diff_json TEXT DEFAULT '',          -- added/removed/changed nodes vs the previous version
    PRIMARY KEY (curriculum_id, version)
);
CREATE TABLE IF NOT EXISTS modules (
    curriculum_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    module TEXT NOT NULL,               -- 'Module 1'
    position INTEGER NOT NULL,
    pdf_path TEXT NOT NULL,
    pdf_sha256 TEXT NOT NULL,
    extraction_dir TEXT DEFAULT '',     -- saved raw extraction chunks ('' for imported v1)
    PRIMARY KEY (curriculum_id, version, module)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- 'ingest' | 'concepts' | ...
    curriculum_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    batch_id TEXT DEFAULT '',
    state TEXT NOT NULL,                -- 'pending' | 'submitted' | 'building' | 'done' | 'failed'
    log_path TEXT DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS views (
    id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    name TEXT NOT NULL,
    subject TEXT NOT NULL,
    pins_json TEXT NOT NULL,            -- {"<curriculum_id>": <version>, ...}
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS view_ops (
    view_id TEXT NOT NULL REFERENCES views(id),
    seq INTEGER NOT NULL,
    op TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (view_id, seq)
);
"""


def now():
    return datetime.datetime.now().isoformat(timespec="microseconds")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _migrate(conn):
    """Columns added after a database was first created."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(versions)")}
    if "published_at" not in cols:      # when this version was loaded into Neo4j + Qdrant
        conn.execute("ALTER TABLE versions ADD COLUMN published_at TEXT DEFAULT ''")
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(views)")}
    if "published_at" not in vcols:     # last time the view was published (vs. updated_at)
        conn.execute("ALTER TABLE views ADD COLUMN published_at TEXT DEFAULT ''")


@contextlib.contextmanager
def connect(db_path=None):
    db_path = db_path or DB_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── curricula & versions ───────────────────────────────────────────────────
def create_curriculum(conn, curriculum_id, name, subject, publisher_profile, framework_id, user,
                      citation_slug=None):
    conn.execute("INSERT INTO curricula VALUES (?,?,?,?,?,?,?,?)",
                 (curriculum_id, name, subject, publisher_profile, framework_id,
                  citation_slug or curriculum_id, user, now()))
    return curriculum_id


def get_curriculum(conn, curriculum_id):
    row = conn.execute("SELECT * FROM curricula WHERE id=?", (curriculum_id,)).fetchone()
    return dict(row) if row else None


def list_curricula(conn, subject=None):
    q, args = "SELECT * FROM curricula", ()
    if subject:
        q, args = q + " WHERE subject=?", (subject,)
    return [dict(r) for r in conn.execute(q + " ORDER BY subject, name", args)]


def version_dir(curriculum_id, version):
    return os.path.join(CURRICULA_DIR, curriculum_id, f"v{version}")


def add_version(conn, curriculum_id, user, status="draft", nodes_path=None, rels_path=None, notes=""):
    """Next version number for the curriculum. Paths default to data/curricula/<id>/v<n>/."""
    row = conn.execute("SELECT MAX(version) AS v FROM versions WHERE curriculum_id=?",
                       (curriculum_id,)).fetchone()
    version = (row["v"] or 0) + 1
    vdir = version_dir(curriculum_id, version)
    nodes_path = nodes_path or os.path.join(vdir, "nodes.jsonl")
    rels_path = rels_path or os.path.join(vdir, "relationships.jsonl")
    conn.execute("INSERT INTO versions (curriculum_id, version, status, nodes_path, rels_path, "
                 "created_by, created_at, notes) VALUES (?,?,?,?,?,?,?,?)",
                 (curriculum_id, version, status, nodes_path, rels_path, user, now(), notes))
    return version


def set_version(conn, curriculum_id, version, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE versions SET {cols} WHERE curriculum_id=? AND version=?",
                 (*fields.values(), curriculum_id, version))


def get_version(conn, curriculum_id, version):
    row = conn.execute("SELECT * FROM versions WHERE curriculum_id=? AND version=?",
                       (curriculum_id, version)).fetchone()
    return dict(row) if row else None


def versions(conn, curriculum_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM versions WHERE curriculum_id=? ORDER BY version", (curriculum_id,))]


def latest_published(conn, curriculum_id):
    """The version the chat answers from (loaded into Neo4j + Qdrant), or None."""
    row = conn.execute("SELECT * FROM versions WHERE curriculum_id=? AND status='ready' AND "
                       "published_at != '' ORDER BY version DESC LIMIT 1", (curriculum_id,)).fetchone()
    return dict(row) if row else None


def latest_ready(conn, curriculum_id):
    row = conn.execute("SELECT * FROM versions WHERE curriculum_id=? AND status='ready' "
                       "ORDER BY version DESC LIMIT 1", (curriculum_id,)).fetchone()
    return dict(row) if row else None


# ── modules ────────────────────────────────────────────────────────────────
def add_module(conn, curriculum_id, version, module, position, pdf_path, pdf_sha256=None,
               extraction_dir=""):
    conn.execute("INSERT OR REPLACE INTO modules VALUES (?,?,?,?,?,?,?)",
                 (curriculum_id, version, module, position, pdf_path,
                  pdf_sha256 or sha256_file(pdf_path), extraction_dir))


def modules(conn, curriculum_id, version):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM modules WHERE curriculum_id=? AND version=? ORDER BY position",
        (curriculum_id, version))]


def module_pdfs(conn, curriculum_id, version):
    """{module name: pdf path}, in module order -- the shape batch_pipeline and the backfill
    scripts already take."""
    return {m["module"]: m["pdf_path"] for m in modules(conn, curriculum_id, version)}


def published_versions(conn):
    """[{**curriculum, **version}] -- the latest ready version of every base curriculum."""
    out = []
    for c in list_curricula(conn):
        v = latest_ready(conn, c["id"])
        if v:
            out.append({**c, **v})
    return out


STANDARDS_LABELS = ("StandardsFramework", "StandardsFrameworkItem")


def scope_of(curriculum_id, version):
    return f"base:{curriculum_id}@v{version}"


def published_courses(conn):
    """{nodes_path: {module: pdf_path}} for the latest ready version of every base curriculum --
    the registry-backed replacement for backfill_page_labels.discover_courses()."""
    root = os.path.dirname(DATA_DIR)
    out = {}
    for c in list_curricula(conn):
        v = latest_ready(conn, c["id"])
        if v:
            path = v["nodes_path"]
            # repo-relative ('data/grade3_nodes.jsonl') like the pre-registry keys other code
            # looks up (e.g. fix_multi_parent_nodes.RESOLUTIONS); absolute when outside the repo
            if os.path.abspath(path).startswith(root + os.sep):
                path = os.path.relpath(path, root)
            out[path] = module_pdfs(conn, c["id"], v["version"])
    return out


# ── jobs ───────────────────────────────────────────────────────────────────
def create_job(conn, kind, curriculum_id, version, user, state="pending", detail=""):
    cur = conn.execute("INSERT INTO jobs (kind, curriculum_id, version, state, created_by, "
                       "created_at, updated_at, detail) VALUES (?,?,?,?,?,?,?,?)",
                       (kind, curriculum_id, version, state, user, now(), now(), detail))
    return cur.lastrowid


def update_job(conn, job_id, **fields):
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def jobs(conn, curriculum_id=None, limit=50):
    q, args = "SELECT * FROM jobs", ()
    if curriculum_id:
        q, args = q + " WHERE curriculum_id=?", (curriculum_id,)
    return [dict(r) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]


def dumps(obj):
    return json.dumps(obj, sort_keys=True)
