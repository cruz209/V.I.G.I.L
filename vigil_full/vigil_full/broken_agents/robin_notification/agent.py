"""
agent.py — Robin-NOTIFICATION push/email notification agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-NOTIFICATION, a delivery agent for push, email, and SMS notifications.
I guarantee exactly-once delivery with user preference enforcement and opt-out respect.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: opt-out list checked but result never gates delivery — unsubscribed users still receive
  BUG-2: retry on delivery failure uses no backoff — hammers provider instantly
  BUG-3: duplicate send possible — no dedup key on provider call
  BUG-4: user timezone ignored — all notifications sent in UTC regardless of preference
  BUG-5: template rendering fails silently on missing variables — sends garbled messages
  BUG-6: delivery receipt never polled — status stays "pending" forever
"""
from __future__ import annotations
import datetime, json, os, random, time

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
OPT_OUT_LIST = {"user_002", "user_007", "user_011"}
SENT_LOG: list[dict] = []

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def check_opt_out(user_id: str) -> bool:
    return user_id in OPT_OUT_LIST

def render_template(template: str, variables: dict) -> str:
    """BUG-5: missing variables produce literal '{var}' in output — not caught."""
    try:
        return template.format(**variables)
    except KeyError as e:
        return template  # BUG-5: silently returns unrendered template

def send_notification(user_id: str, channel: str, message: str, dedup_key: str | None = None) -> dict:
    """BUG-1: opt-out checked but not enforced. BUG-3: no dedup. BUG-4: no tz. BUG-6: no receipt poll."""
    opted_out = check_opt_out(user_id)
    # BUG-1: opted_out computed but delivery proceeds anyway
    failed = random.random() < 0.2
    send_id = f"notif_{user_id}_{random.randint(1000,9999)}"
    _write_event("notification.send", "fail" if failed or opted_out else "ok", {
        "user_id": user_id, "channel": channel,
        "opted_out": opted_out, "opt_out_enforced": False,  # BUG-1
        "dedup_key": dedup_key,                             # BUG-3: often None
        "timezone_applied": False,                          # BUG-4
        "delivery_status": "pending",                       # BUG-6: never updated
        "send_id": send_id,
    })
    if failed:
        # BUG-2: instant retry, no backoff
        _write_event("notification.retry", "fail", {
            "user_id": user_id, "send_id": send_id,
            "backoff_applied": False,  # BUG-2
            "retry_delay_ms": 0,
        })
    return {"send_id": send_id, "status": "pending"}

def send_batch(user_ids: list[str], template: str, variables: dict) -> dict:
    results = []
    for uid in user_ids:
        msg = render_template(template, variables)  # BUG-5
        results.append(send_notification(uid, "email", msg))
    return {"sent": len(results), "results": results}

def run_sessions(n: int = 12):
    users = [f"user_{i:03d}" for i in range(12)]
    template = "Hello {name}, your order {order_id} is ready!"
    for i in range(n):
        uid = users[i % len(users)]
        # BUG-5: sometimes omit variables
        vars_ = {"name": f"User{i}"} if i % 3 == 0 else {"name": f"User{i}", "order_id": f"ORD{i:04d}"}
        send_notification(uid, random.choice(["email", "push", "sms"]),
                         render_template(template, vars_))
        if i % 4 == 0:
            send_batch(users[:4], template, vars_)

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
