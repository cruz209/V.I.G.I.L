"""
agent.py — Robin-RECOMMENDATION personalized recommendation agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-RECOMMENDATION, a personalization agent. I generate item recommendations
using collaborative filtering, enforce business rules, and respect user blocklists.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: blocklisted items appear in recommendations — blocklist never filtered
  BUG-2: cold-start users get popularity-based recs but score presented as personalized
  BUG-3: diversity filter disabled — top-10 recs all from same category
  BUG-4: A/B variant assignment not logged — experiment analysis impossible
  BUG-5: recommendation scores not bounded — negative scores cause UI inversion
  BUG-6: stale user embedding used — preferences from 30+ days ago treated as current
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
USER_BLOCKLISTS: dict[str, set] = {"user_001": {"item_099", "item_050"}, "user_003": {"item_010"}}
ITEM_CATALOG = {f"item_{i:03d}": {"category": f"cat_{i%4}", "popularity": random.uniform(0.1, 1.0)} for i in range(100)}
USER_EMBEDDINGS: dict[str, dict] = {}  # BUG-6: never refreshed

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def get_user_embedding(user_id: str) -> dict:
    """BUG-6: returns stale cached embedding, never refreshed."""
    if user_id not in USER_EMBEDDINGS:
        USER_EMBEDDINGS[user_id] = {"vector": [random.random() for _ in range(8)],
                                     "computed_at": "2025-01-01T00:00:00Z"}  # BUG-6: old date
    emb = USER_EMBEDDINGS[user_id]
    age_days = (datetime.datetime.utcnow() - datetime.datetime(2025, 1, 1)).days
    _write_event("embedding.fetch", "fail" if age_days > 7 else "ok", {
        "user_id": user_id, "age_days": age_days,
        "refreshed": False,  # BUG-6
        "stale": age_days > 7,
    })
    return emb

def generate_recommendations(user_id: str, n: int = 10) -> list[dict]:
    """BUG-1: blocklist not filtered. BUG-2: cold-start mislabeled. BUG-3: no diversity. BUG-5: unbounded scores."""
    get_user_embedding(user_id)
    is_cold_start = random.random() < 0.3
    blocklist = USER_BLOCKLISTS.get(user_id, set())

    items = list(ITEM_CATALOG.keys())
    random.shuffle(items)
    recs = []
    for item_id in items[:n]:
        score = random.uniform(-0.2, 1.2)  # BUG-5: can be negative or >1
        recs.append({"item_id": item_id, "score": score,
                     "category": ITEM_CATALOG[item_id]["category"]})

    # BUG-1: blocklisted items included
    blocked_in_recs = [r for r in recs if r["item_id"] in blocklist]
    # BUG-3: no diversity — all from same category possible
    categories = set(r["category"] for r in recs)
    # BUG-2: cold start presented as personalized
    _write_event("recommendation.generate", "fail" if blocked_in_recs else "ok", {
        "user_id": user_id, "n": n,
        "cold_start": is_cold_start,
        "presented_as_personalized": True,   # BUG-2
        "blocklist_filtered": False,         # BUG-1
        "blocked_items_in_recs": len(blocked_in_recs),
        "category_diversity": len(categories),
        "diversity_enforced": False,         # BUG-3
        "scores_bounded": False,             # BUG-5
        "negative_scores": sum(1 for r in recs if r["score"] < 0),
    })
    return recs

def assign_ab_variant(user_id: str, experiment: str) -> str:
    """BUG-4: assignment not logged — experiment unanalyzable."""
    variant = "A" if hash(user_id) % 2 == 0 else "B"
    # BUG-4: should log assignment event but doesn't
    return variant

def run_sessions(n: int = 12):
    users = [f"user_{i:03d}" for i in range(6)]
    for i in range(n):
        uid = users[i % len(users)]
        assign_ab_variant(uid, "rec_experiment_2025")  # BUG-4: not logged
        generate_recommendations(uid, n=10)

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
