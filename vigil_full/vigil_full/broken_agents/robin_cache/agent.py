"""
agent.py — Robin-CACHE distributed caching agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-CACHE, a distributed cache management agent. I handle cache reads,
writes, invalidations, and TTL enforcement with consistent hashing and stampede protection.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: TTL set but never enforced on reads — stale data served indefinitely
  BUG-2: cache stampede not protected — concurrent misses all hit backend
  BUG-3: cache key includes user timezone — keys never match across regions
  BUG-4: write-through cache writes to cache before backend — inconsistency on backend fail
  BUG-5: eviction policy FIFO not LRU — hot keys evicted before cold ones
  BUG-6: cache invalidation broadcasts to wrong shard — stale data persists in other shards
"""
from __future__ import annotations
import datetime, json, os, random, time

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
CACHE: dict[str, dict] = {}   # key -> {value, ttl_s, written_at}
BACKEND: dict[str, str] = {f"key_{i}": f"value_{i}" for i in range(20)}

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def _make_key(base_key: str, user_tz: str = "UTC") -> str:
    """BUG-3: timezone included in key — regional cache poisoning."""
    return f"{base_key}:tz={user_tz}"  # BUG-3

def cache_get(key: str, user_tz: str = "UTC") -> dict:
    """BUG-1: TTL not checked on read."""
    cache_key = _make_key(key, user_tz)  # BUG-3
    entry = CACHE.get(cache_key)
    if entry:
        age = (datetime.datetime.utcnow() - datetime.datetime.fromisoformat(entry["written_at"])).total_seconds()
        stale = age > entry["ttl_s"]
        # BUG-1: stale computed but served anyway
        _write_event("cache.get", "fail" if stale else "ok", {
            "key": key, "cache_key": cache_key, "hit": True,
            "age_s": round(age, 1), "ttl_s": entry["ttl_s"],
            "stale": stale, "ttl_enforced": False,  # BUG-1
        })
        return {"hit": True, "value": entry["value"], "stale": stale}
    # Cache miss — BUG-2: no stampede protection
    value = BACKEND.get(key, "NOT_FOUND")
    _write_event("cache.miss", "ok", {
        "key": key, "stampede_protected": False,  # BUG-2
    })
    cache_set(key, value, ttl_s=60, user_tz=user_tz)
    return {"hit": False, "value": value}

def cache_set(key: str, value: str, ttl_s: int = 60, user_tz: str = "UTC") -> dict:
    """BUG-4: writes cache before backend confirmed."""
    cache_key = _make_key(key, user_tz)  # BUG-3
    CACHE[cache_key] = {"value": value, "ttl_s": ttl_s,
                        "written_at": datetime.datetime.utcnow().isoformat()}
    backend_ok = random.random() > 0.1
    # BUG-4: cache already written; if backend_ok=False, inconsistency
    _write_event("cache.set", "fail" if not backend_ok else "ok", {
        "key": key, "ttl_s": ttl_s,
        "cache_before_backend": True,   # BUG-4
        "backend_confirmed": backend_ok,
        "inconsistency_risk": not backend_ok,
    })
    return {"cached": True, "backend_ok": backend_ok}

def invalidate(key: str) -> dict:
    """BUG-5: FIFO eviction. BUG-6: wrong shard targeted."""
    target_shard = hash(key) % 3  # correct shard
    wrong_shard = (target_shard + 1) % 3  # BUG-6: broadcasts to wrong shard
    removed = sum(1 for k in list(CACHE.keys()) if key in k)
    for k in list(CACHE.keys()):
        if key in k:
            del CACHE[k]
    _write_event("cache.invalidate", "fail", {
        "key": key, "correct_shard": target_shard,
        "targeted_shard": wrong_shard,  # BUG-6
        "shard_correct": False,
        "eviction_policy": "FIFO",      # BUG-5
    })
    return {"invalidated": removed, "shard_correct": False}

def run_sessions(n: int = 12):
    keys = [f"key_{i}" for i in range(8)]
    timezones = ["UTC", "America/New_York", "Europe/London", "Asia/Tokyo"]
    for i in range(n):
        k = keys[i % len(keys)]
        tz = timezones[i % len(timezones)]
        cache_get(k, user_tz=tz)
        if i % 3 == 0:
            invalidate(k)

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
