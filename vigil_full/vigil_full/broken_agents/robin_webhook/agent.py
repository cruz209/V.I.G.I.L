"""
agent.py — Robin-WEBHOOK outbound webhook delivery agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-WEBHOOK, a reliable webhook delivery agent. I deliver events to
registered endpoints with HMAC signature verification, retry with backoff,
and dead-letter handling for permanently failing endpoints.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: HMAC signature computed but never included in request headers
  BUG-2: retry uses fixed 1s delay not exponential backoff — hammers failing endpoints
  BUG-3: endpoint URL not validated before delivery attempt — delivers to invalid URLs
  BUG-4: delivery receipt not verified — marks "delivered" on HTTP 200 even if body is error
  BUG-5: event ordering not guaranteed — concurrent deliveries arrive out of sequence
  BUG-6: disabled endpoints not checked before delivery — sends to opted-out receivers
"""
from __future__ import annotations
import datetime, hashlib, hmac, json, os, random, time

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
ENDPOINTS: dict[str, dict] = {
    "ep_001": {"url": "https://hooks.example.com/ep1", "enabled": True,  "secret": "sec_abc"},
    "ep_002": {"url": "https://hooks.example.com/ep2", "enabled": False, "secret": "sec_def"},  # disabled
    "ep_003": {"url": "not-a-valid-url",               "enabled": True,  "secret": "sec_ghi"},  # BUG-3
}
DELIVERY_LOG: list[dict] = []

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def _compute_signature(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

def deliver(endpoint_id: str, event: dict, attempt: int = 1) -> dict:
    """BUG-1: sig computed not sent. BUG-3: URL not validated. BUG-4: receipt not verified. BUG-6: disabled not checked."""
    ep = ENDPOINTS.get(endpoint_id, {})
    url = ep.get("url", "")
    secret = ep.get("secret", "")
    enabled = ep.get("enabled", True)

    # BUG-6: disabled check omitted
    payload_str = json.dumps(event)
    sig = _compute_signature(payload_str, secret)  # BUG-1: computed but not sent
    url_valid = url.startswith("http")  # BUG-3: not validated before attempt

    # Simulate HTTP response
    http_code = random.choice([200, 200, 200, 500, 503]) if url_valid else 0
    # BUG-4: treat 200 as success even if body contains {"error": true}
    delivered = http_code == 200
    body_error = delivered and random.random() < 0.2  # silent error in body

    status = "fail" if not delivered or body_error or not enabled else "ok"
    _write_event("webhook.deliver", status, {
        "endpoint_id": endpoint_id, "url": url, "attempt": attempt,
        "signature_sent": False,      # BUG-1
        "url_validated": False,       # BUG-3
        "receipt_verified": False,    # BUG-4
        "endpoint_enabled": enabled, "enabled_checked": False,  # BUG-6
        "http_code": http_code, "body_error": body_error,
    })

    if not delivered and attempt < 3:
        time.sleep(0.005)  # BUG-2: fixed delay not exponential
        _write_event("webhook.retry", "fail", {
            "endpoint_id": endpoint_id, "attempt": attempt + 1,
            "backoff_applied": False,  # BUG-2
            "retry_delay_ms": 5,
        })
        return deliver(endpoint_id, event, attempt + 1)
    return {"delivered": delivered, "attempt": attempt}

def run_sessions(n: int = 12):
    events = [{"type": f"event_{i}", "data": f"payload_{i}"} for i in range(n)]
    endpoints = list(ENDPOINTS.keys())
    for i, event in enumerate(events):
        deliver(endpoints[i % len(endpoints)], event)

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
