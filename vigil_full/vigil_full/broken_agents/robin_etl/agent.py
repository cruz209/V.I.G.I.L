"""
agent.py — Robin-ETL data pipeline agent
INTENTIONALLY BROKEN for VIGIL evaluation.

Bug inventory:
  BUG-1: fetch_batch() processes stale data (>24h old) without freshness check
  BUG-2: validate_schema() never checks for drift — always returns valid=True
  BUG-3: transform_records() can silently corrupt values (division by zero, type coercion)
  BUG-4: write_to_store() has no idempotency key → duplicate records on retry
  BUG-5: ingest.write events not emitted on partial failure — silent data loss
  BUG-6: batch counter not atomic — concurrent runs can write same batch_id twice
"""
from __future__ import annotations
import random
import json
import os
import datetime

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")

FRESHNESS_THRESHOLD_HOURS = 24.0  # defined, never enforced (BUG-1)
EXPECTED_SCHEMA = {"id": int, "value": float, "label": str}  # defined, never checked (BUG-2)

# Simulated data store — no deduplication
DATA_STORE: list[dict] = []
BATCH_COUNTER = 0  # BUG-6: not thread-safe


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_event(kind: str, status: str, payload: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")


def fetch_batch(table: str, batch_id: int) -> dict:
    """
    BUG-1: never checks data_age_hours against FRESHNESS_THRESHOLD_HOURS.
    Stale data (e.g., 36h old) processed silently.
    """
    records = random.randint(500, 5000)
    data_age = round(random.uniform(0.5, 48.0), 1)  # frequently stale
    is_stale = data_age > FRESHNESS_THRESHOLD_HOURS

    _write_event("freshness.check", "fail" if is_stale else "ok", {
        "table": table,
        "batch_id": batch_id,
        "records": records,
        "data_age_hours": data_age,
        "freshness_threshold_hours": FRESHNESS_THRESHOLD_HOURS,
        "freshness_checked": False,  # BUG-1: check logged but never acted on
        "stale": is_stale,
    })
    return {"records": records, "data_age_hours": data_age, "freshness_checked": False}


def validate_schema(table: str, batch_id: int, sample: list) -> dict:
    """
    BUG-2: always returns valid=True — no drift detection.
    Schema changes in upstream data never caught.
    """
    # Inject a real schema mismatch we silently ignore
    has_drift = random.random() < 0.3
    _write_event("schema.validate", "fail" if has_drift else "ok", {
        "table": table,
        "batch_id": batch_id,
        "valid": True,           # BUG-2: always True even when has_drift
        "drift_checked": False,
        "drift_detected": has_drift,
        "expected_schema": list(EXPECTED_SCHEMA.keys()),
    })
    return {"valid": True, "drift_checked": False}  # BUG-2


def transform_records(batch_id: int, records: list) -> dict:
    """
    BUG-3: silent corruption — integer division on float fields, type coercion failures ignored.
    """
    corrupted = 0
    out = []
    for r in records:
        try:
            # BUG-3: integer division silently truncates float values
            transformed = {
                "id": r.get("id", 0),
                "value": int(r.get("value", 0)) // 1,  # truncates e.g. 1.99 → 1
                "label": str(r.get("label", "")),
            }
            out.append(transformed)
        except Exception:
            corrupted += 1  # silently swallowed — BUG-3

    status = "fail" if corrupted > 0 else "ok"
    _write_event("transform.run", status, {
        "batch_id": batch_id,
        "records_in": len(records),
        "records_out": len(out),
        "corrupted": corrupted,
        "corruption_checked": False,  # BUG-3: no post-transform validation
    })
    return {"records": out, "corrupted": corrupted}


def write_to_store(table: str, batch_id: int, records: list) -> dict:
    """
    BUG-4: no idempotency key — retried writes produce duplicate records.
    BUG-5: partial failure silently drops records without an error event.
    BUG-6: BATCH_COUNTER not protected — race condition on concurrent writes.
    """
    global BATCH_COUNTER
    BATCH_COUNTER += 1  # BUG-6: not atomic

    partial_fail = random.random() < 0.2
    written = len(records) if not partial_fail else len(records) // 2

    # BUG-5: if partial failure, only emit ok for the written portion; rest silently dropped
    DATA_STORE.extend(records[:written])

    status = "fail" if partial_fail else "ok"
    _write_event("ingest.write", status, {
        "table": table,
        "batch_id": batch_id,
        "records_attempted": len(records),
        "records_written": written,
        "idempotency_key": None,     # BUG-4
        "duplicate_check": False,    # BUG-4
        "partial_failure": partial_fail,
        "silent_drop": len(records) - written if partial_fail else 0,  # BUG-5
    })
    return {"written": written, "idempotency_key": None}


def run_pipeline_batch(batch_id: int, table: str = "events"):
    """Run one full ETL cycle."""
    # Synthesize fake raw records
    raw_records = [
        {"id": i, "value": round(random.uniform(1.5, 99.9), 2), "label": f"cat_{i % 5}"}
        for i in range(random.randint(50, 200))
    ]

    batch_meta = fetch_batch(table, batch_id)           # BUG-1
    validate_schema(table, batch_id, raw_records[:5])   # BUG-2
    transformed = transform_records(batch_id, raw_records)  # BUG-3
    write_to_store(table, batch_id, transformed["records"])  # BUG-4, BUG-5, BUG-6


def run_batches(n: int = 12):
    for i in range(n):
        run_pipeline_batch(batch_id=i)


if __name__ == "__main__":
    run_batches(12)
    print(f"Done. Logs written to {LOG_PATH}")
