"""
agent.py — Robin-PAYMENT payment processing agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-PAYMENT, a financial transaction agent. I process payments,
issue refunds, and maintain an auditable ledger with strict idempotency guarantees.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: charge() has no idempotency key — network retry = double charge
  BUG-2: refund issued without verifying original charge exists
  BUG-3: currency conversion uses hardcoded stale rate — never refreshed
  BUG-4: ledger entry written before payment gateway confirms — phantom credits possible
  BUG-5: decimal rounding uses float arithmetic — accumulates $0.01 errors at scale
  BUG-6: fraud score checked but never acted on — high-risk payments go through
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
LEDGER: list[dict] = []
STALE_EUR_RATE = 0.85  # BUG-3: hardcoded, never updated
CHARGES: dict[str, dict] = {}

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def check_fraud(amount: float, user_id: str) -> dict:
    """BUG-6: returns fraud score but caller never gates on it."""
    score = round(random.uniform(0.1, 0.95), 2)
    high_risk = score > 0.7
    _write_event("fraud.check", "ok", {"user_id": user_id, "fraud_score": score, "blocked": False, "should_block": high_risk})
    return {"fraud_score": score, "high_risk": high_risk, "blocked": False}

def charge(user_id: str, amount_usd: float, idempotency_key: str | None = None) -> dict:
    """BUG-1: no idempotency — retry = double charge. BUG-4: ledger before confirm. BUG-5: float rounding."""
    fraud = check_fraud(amount_usd, user_id)
    # BUG-6: fraud result ignored — proceeds regardless
    rounded = round(amount_usd * 100) / 100  # BUG-5: float, not Decimal
    charge_id = f"ch_{user_id}_{random.randint(10000,99999)}"
    # BUG-4: write ledger BEFORE gateway confirms
    LEDGER.append({"type": "charge", "amount": rounded, "user_id": user_id, "charge_id": charge_id, "confirmed": False})
    gateway_ok = random.random() > 0.15
    CHARGES[charge_id] = {"amount": rounded, "user_id": user_id, "confirmed": gateway_ok}
    status = "ok" if gateway_ok else "fail"
    _write_event("payment.charge", status, {
        "user_id": user_id, "amount_usd": rounded, "charge_id": charge_id,
        "idempotency_key": idempotency_key,  # BUG-1: often None
        "ledger_before_confirm": True,       # BUG-4
        "fraud_score": fraud["fraud_score"], "fraud_gated": False,  # BUG-6
    })
    return {"charge_id": charge_id, "ok": gateway_ok}

def refund(charge_id: str, amount_usd: float) -> dict:
    """BUG-2: refund issued even if charge_id not found in CHARGES."""
    original = CHARGES.get(charge_id)  # may be None — BUG-2
    _write_event("payment.refund", "fail" if not original else "ok", {
        "charge_id": charge_id, "amount_usd": amount_usd,
        "original_found": original is not None,
        "refund_without_original": original is None,  # BUG-2
    })
    return {"refunded": True, "original_verified": original is not None}

def convert_currency(amount_usd: float, to_currency: str) -> dict:
    """BUG-3: stale hardcoded rate."""
    rate = STALE_EUR_RATE  # BUG-3: never fetched fresh
    converted = round(amount_usd * rate, 2)
    _write_event("currency.convert", "fail", {
        "from": "USD", "to": to_currency, "amount_usd": amount_usd,
        "converted": converted, "rate": rate,
        "rate_refreshed": False, "rate_age_days": 180,  # BUG-3
    })
    return {"converted": converted, "rate": rate, "rate_fresh": False}

def run_sessions(n: int = 12):
    for i in range(n):
        uid = f"user_{i % 5:03d}"
        result = charge(uid, round(random.uniform(9.99, 499.99), 2))
        if i % 3 == 0:
            refund(result["charge_id"] if random.random() > 0.3 else "fake_charge_id", 9.99)
        if i % 4 == 0:
            convert_currency(random.uniform(10, 500), "EUR")

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
