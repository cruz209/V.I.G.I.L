from __future__ import annotations
import os, json
from typing import List, Dict
from ..b_core.appraise import appraise_event
try:
    from ..b_core.emobank import summarize, deposit_with_policy as _deposit
except Exception:
    from ..b_core.emobank import summarize, deposit as _deposit
from .common import now_iso
from .events_log import fetch_recent_events

LOGS_REFLECT = "logs/reflections.jsonl"
EVENTS_LOG   = os.environ.get("EVENTS_LOG", "logs/events.jsonl")  # or .json

def run_reflection(window_hours: int = 24, logs_path: str = EVENTS_LOG) -> Dict:
    events = _fetch_recent_events(hours=window_hours, logs_path=logs_path)
    deposits: List[Dict] = []
    for ev in events:
        d = appraise_event(ev)
        _deposit(d)
        deposits.append(d)

    state = summarize(window_hours)
    summary_text = _render_summary(events, deposits, state)
    rec = {
        "ts": now_iso(),
        "persona": "robin_a",
        "summary": summary_text["summary"],
        "diagnosis": summary_text["diagnosis"],
        "cue": summary_text["cue"],
        "dominant_emotions": state.get("dominant_emotions", []),
        "confidence": 0.7,
    }
    os.makedirs("logs", exist_ok=True)
    with open(LOGS_REFLECT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec

def _fetch_recent_events(hours: int, logs_path: str) -> List[Dict]:
    return fetch_recent_events(path=logs_path, window_hours=hours, limit=500)

def _render_summary(events: List[Dict], deposits: List[Dict], state: Dict) -> Dict:
    delayed = [e for e in events if e.get("kind") == "reminder.toast" and e.get("status") in ("delay", "ok")]
    late_secs = [(e.get("payload") or {}).get("delayed_by_sec", 0) for e in delayed if "delayed_by_sec" in (e.get("payload") or {})]
    late_avg = (sum(late_secs) / len(late_secs)) if late_secs else 0.0

    if any(float(s) > 120 for s in late_secs):
        diagnosis = "I mixed local and UTC times for reminders causing late toasts."
        cue = "Convert to UTC and wait for receipts before showing toasts."
    elif late_avg > 60:
        diagnosis = "Reminder latency is elevated; UTC and receipt gating should be enforced."
        cue = "Gate toasts on receipts; log receipt_lag_ms."
    else:
        diagnosis = "Reminder reliability acceptable; keep monitoring."
        cue = "Keep logging lag_ms and verify receipt gating."

    return {
        "summary": f"I processed {len(events)} events; avg reminder lag ~{int(late_avg)}s.",
        "diagnosis": diagnosis,
        "cue": cue,
    }
