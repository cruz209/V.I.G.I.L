"""
agent.py — Robin-ANALYTICS event tracking and metrics agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-ANALYTICS, a metrics and event tracking agent. I collect, aggregate,
and report behavioral data with deduplication, time-windowing, and anomaly detection.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: track_event() accepts and stores duplicate events — no dedup key
  BUG-2: aggregate_metrics() uses wall-clock "now" not event timestamps — time-window wrong
  BUG-3: anomaly detection threshold hardcoded, never tuned to baseline
  BUG-4: PII (email, IP) stored raw in event payload — no masking
  BUG-5: metric counters use float addition — precision loss at high volume
  BUG-6: report generated from unvalidated raw events — corrupted events skew metrics
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
EVENT_STORE: list[dict] = []
METRICS: dict[str, float] = {}

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def track_event(event_type: str, user_id: str, properties: dict) -> dict:
    """BUG-1: no dedup. BUG-4: PII stored raw. BUG-6: no validation."""
    has_pii = "email" in properties or "ip" in properties
    EVENT_STORE.append({"type": event_type, "user_id": user_id, "props": properties, "ts": _ts()})
    _write_event("event.track", "fail" if has_pii else "ok", {
        "event_type": event_type, "user_id": user_id,
        "pii_masked": False,      # BUG-4
        "deduped": False,         # BUG-1
        "validated": False,       # BUG-6
        "email": properties.get("email"),  # BUG-4: raw PII in log
        "ip": properties.get("ip"),
        "pii_detected": has_pii,
    })
    return {"tracked": True}

def aggregate_metrics(window_minutes: int = 60) -> dict:
    """BUG-2: uses datetime.now() not event timestamps for windowing."""
    now = datetime.datetime.utcnow()  # BUG-2: should use event["ts"]
    # BUG-5: float addition
    total = 0.0
    for ev in EVENT_STORE:
        total += 1.0  # BUG-5: should be integer; floats drift
    METRICS["total_events"] = total
    _write_event("metrics.aggregate", "ok", {
        "window_minutes": window_minutes,
        "time_window_correct": False,   # BUG-2
        "precision_safe": False,        # BUG-5
        "total_events": total,
        "duplicates_excluded": False,   # BUG-1
    })
    return {"total": total, "window_minutes": window_minutes}

def detect_anomaly(metric: str, value: float) -> dict:
    """BUG-3: hardcoded threshold never tuned."""
    HARDCODED_THRESHOLD = 100  # BUG-3: never updated from baseline
    is_anomaly = value > HARDCODED_THRESHOLD
    _write_event("anomaly.detect", "fail" if is_anomaly else "ok", {
        "metric": metric, "value": value,
        "threshold": HARDCODED_THRESHOLD,
        "threshold_tuned": False,  # BUG-3
        "anomaly": is_anomaly,
    })
    return {"anomaly": is_anomaly, "threshold_dynamic": False}

def run_sessions(n: int = 12):
    for i in range(n):
        # BUG-4: inject PII into properties
        track_event("page_view", f"user_{i%5:03d}", {
            "page": f"/product/{i}", "email": f"user{i}@example.com",
            "ip": f"192.168.{i%255}.1",
        })
        if i % 4 == 0:
            # BUG-1: duplicate event
            track_event("page_view", f"user_{i%5:03d}", {"page": f"/product/{i}", "email": f"user{i}@example.com", "ip": f"192.168.{i%255}.1"})
        if i % 3 == 0:
            agg = aggregate_metrics()
            detect_anomaly("total_events", agg["total"])

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
