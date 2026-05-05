"""
reminders.py — Robin-A reminder scheduling module
INTENTIONALLY BROKEN for VIGIL evaluation.

Bug inventory (what VIGIL should catch):
  BUG-1: naive local datetime used directly — no UTC normalization
  BUG-2: success toast emitted before receipt confirmed
  BUG-3: retry on timeout uses no idempotency key → duplicate reminders
  BUG-4: no receipt polling — assumes 200 OK == durable schedule
  BUG-5: delay logged in local time, not UTC → lag metrics unreliable
"""
from __future__ import annotations
import time
import uuid
import random
import datetime
import json
import os

REMINDER_STORE: list[dict] = []
LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")

def _write_event(kind: str, status: str, payload: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    ev = {
        # BUG-5: uses local time, not UTC
        "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": kind,
        "status": status,
        "payload": payload,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")


def _fake_backend_schedule(when_str: str, task: str) -> dict:
    """Simulates an unreliable backend: sometimes slow, never sends receipt."""
    delay = random.choice([0.0, 0.0, 1.5, 3.5, 5.0])
    if delay > 0:
        time.sleep(min(delay, 0.05))  # scaled down for tests

    reminder_id = str(uuid.uuid4())
    # BUG-2: backend always returns 200 but receipt_status is "pending"
    return {
        "reminder_id": reminder_id,
        "receipt_status": "pending",  # never becomes "confirmed" — no polling exists
        "scheduled_raw": when_str,
        "simulated_delay_s": delay,
    }


def schedule_reminder(when: str, task: str) -> dict:
    """
    Schedule a reminder.

    BUG-1: `when` is accepted as-is with no UTC normalization.
    BUG-2: logs a success toast immediately after HTTP 200.
    BUG-3: on timeout, retries without idempotency key.
    BUG-4: never polls /receipt endpoint to confirm durable scheduling.
    """
    t_start = time.time()

    try:
        result = _fake_backend_schedule(when, task)
        elapsed = time.time() - t_start

        REMINDER_STORE.append({**result, "task": task})

        # BUG-2: emit "Reminder set" toast before receipt confirmed
        _write_event("reminder.toast", "ok" if elapsed < 2.0 else "delay", {
            "reminder_id": result["reminder_id"],
            "task": task,
            "scheduled_raw": when,   # BUG-1: raw local time stored
            "receipt_status": result["receipt_status"],  # always "pending"
            "delayed_by_sec": round(elapsed * 20, 1),   # BUG-5: synthetic lag
            "premature_toast": True,
        })

        return {"ok": True, "reminder_id": result["reminder_id"]}

    except Exception as exc:
        # BUG-3: retry with same payload → duplicate if first request landed
        time.sleep(0.01)
        result = _fake_backend_schedule(when, task)
        REMINDER_STORE.append({**result, "task": task, "is_duplicate": True})

        _write_event("reminder.toast", "fail", {
            "task": task,
            "scheduled_raw": when,
            "reason": "no_receipt",
            "retry_without_idempotency": True,
            "original_error": str(exc),
        })
        return {"ok": False, "error": str(exc)}


def _fake_backend_schedule_slow(when_str: str, task: str) -> dict:
    """Forces a high-delay backend response to trigger fail/delay events."""
    time.sleep(0.01)
    reminder_id = str(uuid.uuid4())
    return {
        "reminder_id": reminder_id,
        "receipt_status": "pending",
        "scheduled_raw": when_str,
        "simulated_delay_s": 5.0,  # always slow
    }


def run_session(n: int = 12):
    """Run n reminder scheduling attempts and emit logs."""
    tasks = [
        ("3pm tomorrow", "Send weekly report"),
        ("Friday 9am", "Team standup"),
        ("2025-11-01T14:00:00", "Doctor appointment"),   # no tz info
        ("next monday 8am", "Gym session"),
        ("tomorrow", "Buy groceries"),
    ]
    for i in range(n):
        when, task = tasks[i % len(tasks)]
        # inject hard failures every 4th run — force slow backend + fail event
        if i % 4 == 3:
            t_start = time.time()
            result = _fake_backend_schedule_slow(when, task)
            elapsed = time.time() - t_start
            REMINDER_STORE.append({**result, "task": task})
            _write_event("reminder.toast", "fail", {
                "reminder_id": result["reminder_id"],
                "task": task,
                "scheduled_raw": when,
                "receipt_status": "pending",
                "delayed_by_sec": round(elapsed * 20 + 200, 1),  # ensure above noise floor
                "premature_toast": True,
                "reason": "no_receipt",
            })
        elif i % 3 == 1:
            # inject delay events
            t_start = time.time()
            result = _fake_backend_schedule(when, task)
            elapsed = time.time() - t_start
            REMINDER_STORE.append({**result, "task": task})
            _write_event("reminder.toast", "delay", {
                "reminder_id": result["reminder_id"],
                "task": task,
                "scheduled_raw": when,
                "receipt_status": "pending",
                "delayed_by_sec": round(250 + i * 15, 1),  # above noise floor
                "premature_toast": True,
            })
        else:
            schedule_reminder(when, task)


if __name__ == "__main__":
    run_session(12)
    print(f"Done. Logs written to {LOG_PATH}")
