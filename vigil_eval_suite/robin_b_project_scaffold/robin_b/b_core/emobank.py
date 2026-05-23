from __future__ import annotations
import os, json, time, math, hashlib, datetime as dt
from typing import Dict, List, Optional, Iterable, Tuple

# Minimal local clamp & now_iso; swap to runtime.common if you prefer.
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

# ===== Storage layout (no SQL) =====
# db/emobank/
#   emotions.jsonl           # append-only event-level emotions
#   state.json               # latest cached summary/mood snapshot
#   index.json               # episodic index (thread heads / small stats)
#
# Each emotions.jsonl line:
# { "ts": "...Z", "emotion": "frustration", "intensity": 0.7, "valence": -0.6,
#   "cause": "reminder.toast:delay", "confidence": 0.7, "episode": "<hash>" }

ROOT = os.environ.get("EMO_DIR", "db/emobank")
PATH_EMO = os.path.join(ROOT, "emotions.jsonl")
PATH_STATE = os.path.join(ROOT, "state.json")
PATH_INDEX = os.path.join(ROOT, "index.json")

# Policy tunables
COALESCE_MIN = 5 * 60        # 5 minutes: merge repeats
REBOUND_MIN  = 10 * 60       # 10 minutes: negative -> positive rebound window
NOISE_FLOOR  = 0.25          # skip deposits weaker than this unless sign-flip
HALF_LIFE_H  = 12.0          # default decay half-life

# Episode logic (contextual grouping)
def _episode_id(cause: str) -> str:
    # Stable hash of the cause string; can later include actor/kind if desired
    return hashlib.sha1((cause or "").encode()).hexdigest()[:12]

def _ensure_dirs():
    os.makedirs(ROOT, exist_ok=True)
    for p in (PATH_EMO, PATH_STATE, PATH_INDEX):
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                if p.endswith(".jsonl"):
                    pass  # leave empty
                else:
                    f.write("{}")

def _write_json(path: str, obj: Dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def _append_jsonl(path: str, obj: Dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _iter_jsonl(path: str) -> Iterable[Dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

def _parse_iso(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", ""))

# ===== Public API (compatible with old SQL version) =====

def deposit(dep: Dict) -> int:
    """
    Append one appraisal to contextual store. Returns a monotonic integer id
    equal to current line count (not stable across deletions; we don't delete).
    """
    _ensure_dirs()
    dep = dict(dep)
    dep.setdefault("ts", now_iso())
    dep.setdefault("confidence", 0.7)
    dep.setdefault("cause", "")
    dep.setdefault("emotion", "curiosity")
    dep.setdefault("intensity", 0.4)
    dep.setdefault("valence", 0.0)

    # attach episode id for contextual retrieval
    dep["episode"] = _episode_id(dep.get("cause", ""))

    # append
    _append_jsonl(PATH_EMO, dep)

    # update small index counters
    _update_index_with(dep)
    return _line_count(PATH_EMO)

def decay(half_life_hours: float = HALF_LIFE_H) -> int:
    """
    Applies exponential decay to intensities *in memory* for summary,
    not by rewriting the log (we keep raw; decay is virtual at read time).
    Returns the count of entries that would be updated if it were materialized.
    """
    # We don't rewrite emotions.jsonl; we compute decayed intensities on the fly.
    # For compatibility, return rough count == number of lines scanned.
    return _line_count(PATH_EMO)

def summarize(window_hours: int = 24) -> Dict:
    """
    Summarize recent emotional context over a rolling window (virtual decay).
    Returns: {mood, dominant_emotions, energy, stress, motivation, focus}
    """
    _ensure_dirs()
    since = dt.datetime.utcnow() - dt.timedelta(hours=window_hours)
    items: List[Tuple[str, float, float]] = []  # (emotion, decayed_I, valence)

    for row in _iter_jsonl(PATH_EMO):
        ts = row.get("ts")
        if not ts:
            continue
        t = _parse_iso(ts)
        if t < since:
            continue
        I = float(row.get("intensity", 0.0))
        val = float(row.get("valence", 0.0))
        # virtual decay from timestamp to now
        age_h = (dt.datetime.utcnow() - t).total_seconds() / 3600.0
        decayed_I = I * (0.5 ** (age_h / HALF_LIFE_H))
        items.append((row.get("emotion", "curiosity"), clamp(decayed_I, 0.0, 1.0), val))

    if not items:
        snap = {"mood": "calm", "dominant_emotions": [], "energy": 0.2, "stress": 0.1, "motivation": 0.5, "focus": 0.5}
        _write_json(PATH_STATE, snap)
        return snap

    # aggregate
    totals = {}
    for emo, I, _v in items:
        totals[emo] = totals.get(emo, 0.0) + I
    dom = sorted(totals.items(), key=lambda x: -x[1])[:3]
    dom_names = [d[0] for d in dom]

    # interpretive channels
    energy = clamp(sum(I * 0.6 for _e, I, _v in items) / len(items), 0.0, 1.0)
    stress = clamp(sum(I for e, I, _v in items if e in ("frustration", "anxiety")) / max(1, len(items)), 0.0, 1.0)
    motivation = clamp(sum(I for e, I, _v in items if e in ("pride", "curiosity", "determination")) / max(1, len(items)), 0.0, 1.0)
    focus = clamp(sum(I for e, I, _v in items if e in ("curiosity", "calm")) / max(1, len(items)), 0.0, 1.0)

    snap = {"mood": (dom_names[0] if dom_names else "calm"),
            "dominant_emotions": dom_names,
            "energy": energy, "stress": stress, "motivation": motivation, "focus": focus}
    _write_json(PATH_STATE, snap)
    return snap

# ===== Contextual policy (deposit_with_policy) =====

def last_emotion() -> Optional[Dict]:
    """
    Fast scan from the tail. For simplicity, read the file and take the last non-empty row.
    (If logs are large, you can keep a small tail cache/index in memory.)
    """
    _ensure_dirs()
    last = None
    for row in _iter_jsonl(PATH_EMO):
        last = row
    return last

def _line_count(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f if _.strip())

def _update_index_with(dep: Dict):
    try:
        idx = _read_json(PATH_INDEX)
    except Exception:
        idx = {}
    ep = dep.get("episode", "")
    bucket = idx.get(ep) or {"cause": dep.get("cause", ""), "count": 0, "last_ts": dep["ts"], "last_emotion": dep["emotion"]}
    bucket["count"] += 1
    bucket["last_ts"] = dep["ts"]
    bucket["last_emotion"] = dep["emotion"]
    idx[ep] = bucket
    _write_json(PATH_INDEX, idx)

def _read_json(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def deposit_with_policy(dep: Dict, weight: float = 1.0) -> Optional[int]:
    """
    Contextual update policy:
      • Skip noise: intensity < NOISE_FLOOR unless valence sign flips vs last entry.
      • Coalesce: if same emotion+cause within COALESCE_MIN → amplify last instead of new row.
      • Rebound: if last valence < 0 and this valence > 0 within REBOUND_MIN → add a small 'determination' shadow.
    Returns: new row id, or None if coalesced/skipped.
    """
    _ensure_dirs()
    dep = dict(dep)
    dep.setdefault("ts", now_iso())
    dep.setdefault("confidence", 0.7)
    dep.setdefault("cause", "")
    dep.setdefault("emotion", "curiosity")
    dep.setdefault("intensity", 0.4)
    dep.setdefault("valence", 0.0)

    # apply weighting before any checks
    dep["intensity"] = clamp(float(dep["intensity"]) * float(weight), 0.0, 1.0)

    prev = last_emotion()
    flip = False
    if prev is not None:
        flip = (prev.get("valence", 0.0) >= 0 > dep["valence"]) or (prev.get("valence", 0.0) <= 0 < dep["valence"])

    # 1) Skip tiny noise unless sign flip
    if dep["intensity"] < NOISE_FLOOR and not flip:
        return None

    # 2) Coalesce repeated emotion+cause within window
    if prev is not None and prev.get("emotion") == dep["emotion"] and prev.get("cause", "") == dep.get("cause", ""):
        try:
            age = (dt.datetime.utcnow() - _parse_iso(prev["ts"])).total_seconds()
            if age <= COALESCE_MIN:
                # Append synthetic amendment with boosted intensity (soft cap)
                boosted = dict(prev)
                boosted["intensity"] = clamp(prev.get("intensity", 0.0) + dep["intensity"] * 0.5, 0.0, 1.0)
                boosted["ts"] = now_iso()
                boosted["cause"] = dep.get("cause", boosted.get("cause", ""))
                boosted["episode"] = _episode_id(boosted.get("cause", ""))
                boosted["_amend"] = True
                _append_jsonl(PATH_EMO, boosted)
                _update_index_with(boosted)
                return None
        except Exception:
            pass

    # 3) Insert main deposit
    dep["episode"] = _episode_id(dep.get("cause", ""))
    _append_jsonl(PATH_EMO, dep)
    _update_index_with(dep)
    rid = _line_count(PATH_EMO)

    # 4) Rebound shadow: negative -> positive soon after
    if prev is not None and prev.get("valence", 0.0) < 0 and dep.get("valence", 0.0) > 0:
        try:
            age = (dt.datetime.utcnow() - _parse_iso(prev["ts"])).total_seconds()
            if age <= REBOUND_MIN:
                shadow = {
                    "ts": now_iso(),
                    "emotion": "determination",
                    "intensity": clamp(0.3 + dep["intensity"] * 0.2, 0.0, 1.0),
                    "valence": 0.4,
                    "cause": dep.get("cause", ""),
                    "confidence": 0.6,
                    "episode": _episode_id(dep.get("cause", "")),
                    "_shadow": True
                }
                _append_jsonl(PATH_EMO, shadow)
                _update_index_with(shadow)
        except Exception:
            pass

    return rid

# ===== Optional contextual recall helpers =====

def recall_recent(n: int = 20) -> List[Dict]:
    """Return the last n emotion entries (raw, not decayed)."""
    _ensure_dirs()
    buf = list(_iter_jsonl(PATH_EMO))
    return buf[-n:]

def recall_episode(cause: str, limit: int = 50) -> List[Dict]:
    """Return up to 'limit' entries belonging to the episode for this cause."""
    ep = _episode_id(cause)
    out: List[Dict] = []
    for row in _iter_jsonl(PATH_EMO):
        if row.get("episode") == ep:
            out.append(row)
    return out[-limit:]
