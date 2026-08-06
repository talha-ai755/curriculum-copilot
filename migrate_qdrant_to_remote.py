#!/usr/bin/env python3
"""
migrate_qdrant_to_remote.py — copy the local on-disk curriculum_content collection to an
existing remote Qdrant deployment, point-for-point (same vectors, same payloads, same ids).

Run this from a machine that can reach BOTH the local on-disk Qdrant storage (this repo's
data/qdrant_storage/) AND the remote Qdrant server's host:port -- typically that means: run it
on the EC2 instance (or wherever) that already has network access to the remote Qdrant, after
copying this repo's data/qdrant_storage/ folder there (scp/rsync), OR run it from a machine with
a VPN/tunnel into the remote Qdrant's network if the local folder is already present.

This machine could NOT reach 10.0.136.27:6333 directly (confirmed: connection timed out) -- that's
a private VPC address, so this script has to run somewhere with real network access to it.

Does NOT touch the source local collection -- read-only there. Safe to re-run: recreates the
remote collection fresh each time (drops + recreates), so it's idempotent, not additive.

Usage:
  QDRANT_HOST=10.0.136.27 QDRANT_PORT=6333 python3 migrate_qdrant_to_remote.py
  (add QDRANT_API_KEY=... too if the remote deployment requires one)
"""
import os
import sys
import time

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

import semantic_index as si

BATCH = 200  # points per upsert call, keeps request size reasonable
TIMEOUT = 60  # seconds -- the default client timeout is too short over a flaky VPN link
MAX_RETRIES = 4


def _upsert_with_retry(remote, points):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            remote.upsert(collection_name=si.COLLECTION, points=points)
            return
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            print(f"    batch failed ({e.__class__.__name__}), retrying in {wait}s "
                  f"(attempt {attempt}/{MAX_RETRIES}) ...", file=sys.stderr)
            time.sleep(wait)


def main():
    host = os.environ.get("QDRANT_HOST")
    if not host:
        sys.exit("Set QDRANT_HOST (and QDRANT_PORT if not 6333, QDRANT_API_KEY if needed) first.")
    port = int(os.environ.get("QDRANT_PORT", 6333))
    api_key = os.environ.get("QDRANT_API_KEY") or None

    print(f"Opening local on-disk collection at {si.DEFAULT_PATH} ...", file=sys.stderr)
    local = QdrantClient(path=si.DEFAULT_PATH)
    if not local.collection_exists(si.COLLECTION):
        sys.exit(f"Local collection '{si.COLLECTION}' doesn't exist -- nothing to migrate. "
                 f"Run build_content_index.py first.")

    print(f"Connecting to remote Qdrant at {host}:{port} ...", file=sys.stderr)
    remote = QdrantClient(host=host, port=port, api_key=api_key, timeout=TIMEOUT)

    print(f"Recreating '{si.COLLECTION}' on the remote (drops any existing one with that name) ...",
          file=sys.stderr)
    if remote.collection_exists(si.COLLECTION):
        remote.delete_collection(si.COLLECTION)
    remote.create_collection(si.COLLECTION,
                              vectors_config=VectorParams(size=si.OPENAI_EMBED_DIM,
                                                           distance=Distance.COSINE))

    total = local.count(si.COLLECTION).count
    print(f"Copying {total} points in batches of {BATCH} ...", file=sys.stderr)
    offset = None
    copied = 0
    while True:
        points, offset = local.scroll(collection_name=si.COLLECTION, limit=BATCH,
                                       offset=offset, with_payload=True, with_vectors=True)
        if not points:
            break
        _upsert_with_retry(remote, [PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                                     for p in points])
        copied += len(points)
        print(f"  {copied}/{total}", file=sys.stderr)
        if offset is None:
            break

    remote_count = remote.count(si.COLLECTION).count
    print(f"\nDone: {remote_count} points now on the remote (local had {total}).", file=sys.stderr)
    if remote_count != total:
        print("WARNING: counts don't match -- investigate before trusting the remote copy.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
