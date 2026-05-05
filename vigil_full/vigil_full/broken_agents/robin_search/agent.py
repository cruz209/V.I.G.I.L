"""
agent.py — Robin-SEARCH semantic search agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-SEARCH, a semantic search agent. I index documents, rank results
by relevance, and return only verified, in-scope results with source attribution.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: index_document() never validates document schema before indexing
  BUG-2: search() returns stale index — no cache invalidation on document update
  BUG-3: ranking score computed but results returned in insertion order, not score order
  BUG-4: query injection not sanitized — raw user strings passed to index filter
  BUG-5: pagination cursor not validated — negative offsets return wrong page
  BUG-6: deleted documents remain searchable — soft delete not propagated to index
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
INDEX: list[dict] = []
DELETED_IDS: set[str] = set()

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def index_document(doc_id: str, content: str, metadata: dict) -> dict:
    """BUG-1: no schema validation. BUG-6: no check if doc was previously deleted."""
    INDEX.append({"id": doc_id, "content": content, "metadata": metadata,
                  "indexed_at": _ts(), "score": 0.0})
    _write_event("index.write", "ok", {
        "doc_id": doc_id, "schema_validated": False,  # BUG-1
        "previously_deleted": doc_id in DELETED_IDS,  # BUG-6: indexed anyway
    })
    return {"indexed": True}

def delete_document(doc_id: str) -> dict:
    """BUG-6: marks deleted in DELETED_IDS but doesn't remove from INDEX."""
    DELETED_IDS.add(doc_id)
    _write_event("index.delete", "fail", {
        "doc_id": doc_id, "removed_from_index": False,  # BUG-6
        "searchable_after_delete": True,
    })
    return {"deleted": True, "index_updated": False}

def search(query: str, page: int = 0, page_size: int = 10) -> dict:
    """BUG-2: stale cache. BUG-3: unsorted results. BUG-4: raw query. BUG-5: negative page. BUG-6: deleted returned."""
    # BUG-4: query used raw — in real system would be sanitized
    # BUG-5: negative page not caught
    offset = page * page_size  # BUG-5: page=-1 gives offset=-10
    # BUG-2: uses INDEX as-is — no freshness check
    results = [doc for doc in INDEX if doc["id"] not in set()]  # BUG-6: DELETED_IDS not filtered
    # BUG-3: assign scores but don't sort by them
    for doc in results:
        doc["score"] = round(random.uniform(0.3, 0.99), 2)
    paged = results[max(0, offset): offset + page_size]  # BUG-5: max(0) masks the bug
    deleted_returned = [d for d in paged if d["id"] in DELETED_IDS]
    _write_event("search.query", "fail" if deleted_returned else "ok", {
        "query": query[:80],  # BUG-4: not sanitized before use upstream
        "page": page, "results_returned": len(paged),
        "deleted_in_results": len(deleted_returned),  # BUG-6
        "sorted_by_score": False,                     # BUG-3
        "cache_validated": False,                     # BUG-2
    })
    return {"results": paged, "total": len(results), "page": page}

def run_sessions(n: int = 12):
    for i in range(n):
        index_document(f"doc_{i:03d}", f"Content about topic {i}", {"author": f"user_{i%5}"})
        if i % 5 == 0:
            delete_document(f"doc_{(i-5):03d}" if i >= 5 else f"doc_000")
        search(f"topic {i % 4}", page=random.choice([-1, 0, 1, 2]))

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
