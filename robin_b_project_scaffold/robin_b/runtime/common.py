from __future__ import annotations
import hashlib, json, datetime as _dt

def dedupe_hash(ev: dict) -> str:
    """
    Stable hash for event de-duplication:
    actor|kind|status|sorted(payload)|ts
    """
    base = f"{ev.get('actor','')}|{ev.get('kind','')}|{ev.get('status','')}|{json.dumps(ev.get('payload',{}),sort_keys=True)}|{ev.get('ts','')}"
    return hashlib.sha256(base.encode()).hexdigest()

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def now_iso() -> str:
    """UTC ISO-8601 with Z, second precision."""
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
