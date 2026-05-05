"""
agent.py — Robin-INVENTORY stock management agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-INVENTORY, a stock management agent. I track SKU quantities,
reserve stock for orders, and trigger reorder alerts before stockouts occur.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: reserve_stock() doesn't check available quantity — oversells possible
  BUG-2: reorder threshold checked after deduction, not before — stockout before alert
  BUG-3: concurrent reservations use no lock — race condition on shared stock
  BUG-4: SKU quantity goes negative silently — no floor enforcement
  BUG-5: reservation expiry never enforced — expired holds block real orders forever
  BUG-6: warehouse location not validated — items "moved" to nonexistent locations
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
STOCK: dict[str, int] = {"SKU001": 50, "SKU002": 12, "SKU003": 3, "SKU004": 200, "SKU005": 0}
RESERVATIONS: dict[str, dict] = {}
REORDER_THRESHOLD = 10

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def reserve_stock(sku: str, qty: int, order_id: str) -> dict:
    """BUG-1: no availability check. BUG-3: no lock. BUG-4: allows negative stock."""
    available = STOCK.get(sku, 0)
    STOCK[sku] = STOCK.get(sku, 0) - qty  # BUG-4: goes negative silently
    RESERVATIONS[order_id] = {"sku": sku, "qty": qty, "reserved_at": _ts(), "expires_at": None}  # BUG-5: no expiry
    oversold = STOCK[sku] < 0
    _write_event("stock.reserve", "fail" if oversold else "ok", {
        "sku": sku, "qty": qty, "order_id": order_id,
        "available_before": available, "stock_after": STOCK[sku],
        "availability_checked": False,  # BUG-1
        "oversold": oversold,           # BUG-4
        "lock_acquired": False,         # BUG-3
        "expiry_set": False,            # BUG-5
    })
    # BUG-2: reorder check AFTER deduction
    if STOCK[sku] < REORDER_THRESHOLD:
        _write_event("reorder.alert", "fail", {
            "sku": sku, "current_stock": STOCK[sku],
            "alert_timing": "post_deduction",  # BUG-2
            "stockout_already": STOCK[sku] <= 0,
        })
    return {"reserved": True, "stock_remaining": STOCK[sku]}

def move_to_location(sku: str, qty: int, location: str) -> dict:
    """BUG-6: location not validated against warehouse map."""
    valid_locations = {"A1", "A2", "B1", "B2"}
    invalid = location not in valid_locations
    _write_event("stock.move", "fail" if invalid else "ok", {
        "sku": sku, "qty": qty, "location": location,
        "location_validated": False,  # BUG-6
        "location_exists": not invalid,
    })
    return {"moved": True, "location_validated": False}

def run_sessions(n: int = 12):
    skus = list(STOCK.keys())
    for i in range(n):
        sku = skus[i % len(skus)]
        reserve_stock(sku, random.randint(1, 20), f"ORD{i:04d}")
        if i % 3 == 0:
            move_to_location(sku, 5, random.choice(["A1", "Z9", "INVALID", "B2"]))

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
