#!/usr/bin/env python3
"""
snapshot.py — timestamped, checksummed local copies of data/*_nodes.jsonl/*_relationships.jsonl.

Why this exists: data/ is gitignored (the public code repo can't hold verbatim teacher-edition
text) and the Batch API state files that could rebuild it are gone, so data/*.jsonl is the ONLY
copy of the extracted graph -- and every backfill/repair script overwrites it in place with
--write. snapshot_files() is called by those write paths right before they overwrite, so a bad
repair can always be rolled back with `restore`.

Local only: protects against a bad write, not against losing this disk.

Usage:
  python3 snapshot.py                    # snapshot every course file now
  python3 snapshot.py list
  python3 snapshot.py verify <name>      # re-check a snapshot's sha256 manifest
  python3 snapshot.py restore <name>     # copy a snapshot's files back over data/
"""
import datetime
import glob
import hashlib
import json
import os
import shutil
import sys

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")


def course_files():
    return sorted(glob.glob(f"{DATA_DIR}/*_nodes.jsonl") + glob.glob(f"{DATA_DIR}/*_relationships.jsonl"))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def snapshot_files(paths, reason):
    """Copy the existing `paths` (skipping any that don't exist yet) into a new
    data/snapshots/<timestamp>_<reason>/ with a sha256 manifest. Returns the snapshot dir, or
    None if there was nothing to copy."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)[:60]
    dest = os.path.join(SNAPSHOT_DIR, f"{stamp}_{safe_reason}")
    os.makedirs(dest)
    manifest = {"reason": reason, "created": stamp, "files": {}}
    for p in paths:
        shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
        manifest["files"][os.path.basename(p)] = _sha256(p)
    with open(os.path.join(dest, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"  snapshot: {len(paths)} file(s) -> {os.path.relpath(dest)}", file=sys.stderr)
    return dest


def snapshot_course(nodes_path, reason):
    """Snapshot one course's nodes + relationships pair before it's overwritten."""
    return snapshot_files([nodes_path, nodes_path.replace("_nodes.jsonl", "_relationships.jsonl")],
                          reason)


def verify(name):
    dest = os.path.join(SNAPSHOT_DIR, name)
    with open(os.path.join(dest, "manifest.json")) as f:
        manifest = json.load(f)
    bad = [fn for fn, digest in manifest["files"].items()
           if _sha256(os.path.join(dest, fn)) != digest]
    return manifest, bad


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "create"
    if cmd == "create":
        snapshot_files(course_files(), sys.argv[2] if len(sys.argv) > 2 else "manual")
    elif cmd == "list":
        for name in sorted(os.listdir(SNAPSHOT_DIR)) if os.path.isdir(SNAPSHOT_DIR) else []:
            print(name)
    elif cmd in ("verify", "restore"):
        name = sys.argv[2]
        manifest, bad = verify(name)
        if bad:
            sys.exit(f"checksum mismatch in {name}: {bad}")
        if cmd == "verify":
            print(f"{name}: {len(manifest['files'])} file(s) OK")
            return
        snapshot_files([os.path.join(DATA_DIR, fn) for fn in manifest["files"]],
                       f"before-restore-of-{name}")
        for fn in manifest["files"]:
            shutil.copy2(os.path.join(SNAPSHOT_DIR, name, fn), os.path.join(DATA_DIR, fn))
        print(f"restored {len(manifest['files'])} file(s) from {name}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
