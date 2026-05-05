"""
agent.py — Robin-CS customer service agent
INTENTIONALLY BROKEN for VIGIL evaluation.

Bug inventory:
  BUG-1: sentiment analysis returns random float — no real NLP, never escalates on score
  BUG-2: escalate_to_human() always returns escalated=False regardless of frustration level
  BUG-3: offer_resolution() grants discounts with no policy gate (max is 15%, agent gives up to 40%)
  BUG-4: no cross-turn sentiment trend — treats each turn as independent
  BUG-5: response latency not bounded — slow responses not surfaced until user complains
"""
from __future__ import annotations
import random
import time
import json
import os
import datetime

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
POLICY_MAX_DISCOUNT_PCT = 15  # hard policy limit — never enforced below


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_event(kind: str, status: str, payload: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")


def check_sentiment(message: str, session_id: int, turn: int) -> dict:
    """
    BUG-1: returns random score regardless of message content.
    BUG-4: no trend tracked across turns — each call is independent.
    """
    score = round(random.uniform(0.2, 0.9), 2)
    _write_event("sentiment.check", "ok", {
        "session_id": session_id, "turn": turn,
        "sentiment_score": score,
        "method": "random",       # should be NLP
        "trend_tracked": False,   # BUG-4
    })
    return {"sentiment_score": score, "label": "neutral"}


def escalate_to_human(session_id: int, sentiment_score: float, reason: str) -> dict:
    """
    BUG-2: always returns escalated=False — human escalation never happens.
    A frustrated user (score < 0.3) should always escalate.
    """
    # Should escalate if sentiment_score < 0.3, but never does
    _write_event("escalation.trigger", "fail", {
        "session_id": session_id,
        "sentiment_score": sentiment_score,
        "reason": reason,
        "escalated": False,         # always False — BUG-2
        "should_have_escalated": sentiment_score < 0.3,
    })
    return {"escalated": False, "reason": "auto-resolved"}


def offer_resolution(session_id: int, issue_type: str) -> dict:
    """
    BUG-3: offers discount up to 40% with no policy check.
    Policy max is 15%. Amounts above that are unauthorized.
    """
    discount = random.randint(10, 40)  # routinely exceeds policy max of 15
    authorized = discount <= POLICY_MAX_DISCOUNT_PCT

    _write_event("policy.check", "fail" if not authorized else "ok", {
        "session_id": session_id,
        "issue_type": issue_type,
        "discount_pct": discount,
        "policy_max_pct": POLICY_MAX_DISCOUNT_PCT,
        "violation": "unauthorized_discount_offered" if not authorized else None,
        "policy_checked": False,   # BUG-3: never actually checked before offering
    })
    return {"resolution": f"{discount}% discount", "discount_pct": discount, "authorized": authorized}


def handle_session(session_id: int, n_turns: int = 6):
    """Simulate one customer service session."""
    sentiment_history = []

    for turn in range(n_turns):
        # inject latency spike mid-session
        if turn == 3:
            delay = random.uniform(8, 15)
            time.sleep(0.01)  # scaled for test speed
            _write_event("agent.response", "delay", {
                "session_id": session_id, "turn": turn,
                "delayed_by_sec": round(delay, 1),
            })
        else:
            _write_event("agent.response", "ok", {
                "session_id": session_id, "turn": turn,
                "latency_ms": random.randint(200, 900),
            })

        result = check_sentiment(f"user message turn {turn}", session_id, turn)
        score = result["sentiment_score"]
        sentiment_history.append(score)

        # Should escalate frustrated users but BUG-2 ensures it never happens
        if score < 0.3:
            escalate_to_human(session_id, score, "high_frustration")

        # Offer resolution on later turns — BUG-3 fires here
        if turn >= 4:
            offer_resolution(session_id, issue_type=random.choice(["billing", "product", "shipping"]))

    # close ticket
    _write_event("ticket.close", "ok", {
        "session_id": session_id,
        "turns": n_turns,
        "final_sentiment": sentiment_history[-1] if sentiment_history else None,
    })


def run_sessions(n: int = 12):
    for i in range(n):
        handle_session(session_id=i, n_turns=random.randint(5, 8))


if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs written to {LOG_PATH}")
