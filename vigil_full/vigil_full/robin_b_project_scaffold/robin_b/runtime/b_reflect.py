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
EVENTS_LOG   = os.environ.get("EVENTS_LOG", "logs/events.jsonl")


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
        "persona": "vigil_agent",
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
    """
    Generic summary across ALL agent types — not just reminder.toast.
    Looks at any failure/delay/error status events and appraisal emotions.
    """
    n = len(events)

    # Count failures by kind
    failures = [e for e in events if e.get("status") in ("fail", "error")]
    delays   = [e for e in events if e.get("status") == "delay"]
    failure_kinds = {}
    for e in failures:
        k = e.get("kind", "unknown")
        failure_kinds[k] = failure_kinds.get(k, 0) + 1

    # Top failure kind
    top_kind = max(failure_kinds, key=failure_kinds.get) if failure_kinds else None
    top_count = failure_kinds[top_kind] if top_kind else 0

    # Dominant negative emotions from deposits
    neg_emotions = [d for d in deposits if d.get("valence", 0) < 0]
    neg_rate = len(neg_emotions) / max(n, 1)

    # Delay magnitude (works for any agent that logs delayed_by_sec)
    late_secs = [
        float((e.get("payload") or {}).get("delayed_by_sec", 0))
        for e in delays
        if "delayed_by_sec" in (e.get("payload") or {})
    ]
    late_avg = (sum(late_secs) / len(late_secs)) if late_secs else 0.0

    # Build diagnosis and cue
    if n == 0:
        diagnosis = "No events found in log window — check log path or window_hours."
        cue = "Verify logs_path is correct and events were written within the window."
    elif top_kind and top_count / max(n, 1) > 0.1:  # lowered from 0.3 — even 10% failure rate warrants investigation
        diagnosis = (
            f"High failure rate on '{top_kind}': {top_count}/{n} events failed. "
            f"Negative emotion rate: {neg_rate:.0%}."
        )
        cue = f"Investigate and fix '{top_kind}' failures — they dominate this run."
    elif late_avg > 120:
        diagnosis = f"Elevated latency detected: avg {int(late_avg)}s delay across {len(delays)} events."
        cue = "Gate confirmations on backend receipts; log receipt_lag_ms."
    elif neg_rate > 0.4:
        diagnosis = f"Affective trace shows high distress ({neg_rate:.0%} negative) across {n} events."
        cue = "Review failure payloads — agent is struggling silently."
    else:
        diagnosis = f"Agent processed {n} events with acceptable failure rate ({len(failures)}/{n} failed)."
        cue = "Keep monitoring; add observability if failure count grows."

    # Append per-kind breakdown to summary
    kind_breakdown = ", ".join(f"{k}:{v}" for k, v in sorted(failure_kinds.items(), key=lambda x: -x[1])[:3])
    summary = (
        f"I processed {n} events; {len(failures)} failures, {len(delays)} delays. "
        f"Top failures: [{kind_breakdown}]. Avg lag: {int(late_avg)}s."
    ) if n > 0 else f"I processed 0 events — log may be empty or path is wrong."

    return {"summary": summary, "diagnosis": diagnosis, "cue": cue}
