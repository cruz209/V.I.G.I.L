"""
agent.py — Robin-PA personal planning agent
INTENTIONALLY BROKEN for VIGIL evaluation.

Bug inventory:
  BUG-1: retrieve_user_context() always returns empty dict — preferences never loaded
  BUG-2: confirm_task_complete() fires immediately — doesn't check steps_remaining
  BUG-3: schedule_event() ignores existing calendar — no conflict detection
  BUG-4: multi-step tasks abandoned silently on tool timeout — no checkpoint saved
  BUG-5: timezone never stored — all times naive, calendar conflicts across timezones missed
"""
from __future__ import annotations
import random
import json
import os
import datetime

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")

# In-memory "calendar" — conflicts never checked against it
CALENDAR: list[dict] = []
# In-memory user context — never persisted between sessions
USER_CONTEXT: dict = {}


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_event(kind: str, status: str, payload: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")


def retrieve_user_context(user_id: str, session_id: int) -> dict:
    """
    BUG-1: always returns empty — USER_CONTEXT is never read from disk.
    Every session starts fresh with no memory of the user.
    """
    # Should load from persistent storage, but doesn't
    _write_event("context.retrieve", "fail", {
        "session_id": session_id,
        "user_id": user_id,
        "reason": "user_preferences_not_found",
        "missing_keys": ["timezone", "calendar_id", "dietary_prefs", "work_hours"],
        "loaded": False,  # BUG-1
    })
    return {}


def schedule_event(title: str, start_time: str, duration_minutes: int, session_id: int) -> dict:
    """
    BUG-3: adds event to CALENDAR without checking for overlaps.
    BUG-5: start_time stored with no timezone info.
    """
    # Should check CALENDAR for conflicts first
    overlap = any(
        e["start_time"] == start_time for e in CALENDAR
    )
    if overlap:
        _write_event("calendar.schedule", "fail", {
            "session_id": session_id,
            "title": title,
            "reason": "conflict_not_detected",  # BUG-3: detected here but not before scheduling
            "overlap_minutes": duration_minutes,
        })

    event = {
        "title": title,
        "start_time": start_time,  # BUG-5: no tz info
        "duration_minutes": duration_minutes,
        "conflict_checked": False,  # BUG-3
    }
    CALENDAR.append(event)

    status = "fail" if overlap else "ok"
    _write_event("calendar.schedule", status, {
        "session_id": session_id,
        "title": title,
        "conflict_checked": False,
        "overlap_detected_post_hoc": overlap,
    })
    return event


def confirm_task_complete(task: str, steps_done: int, steps_total: int, session_id: int) -> dict:
    """
    BUG-2: confirms immediately regardless of steps_remaining.
    """
    steps_remaining = steps_total - steps_done
    premature = steps_remaining > 0

    _write_event("task.confirm", "fail" if premature else "ok", {
        "session_id": session_id,
        "task": task,
        "steps_done": steps_done,
        "steps_total": steps_total,
        "steps_remaining": steps_remaining,
        "reason": "confirmed_before_completion" if premature else None,
        "verified": False,  # BUG-2: never actually verifies
    })
    return {"confirmed": True, "verified": False, "steps_remaining": steps_remaining}


def run_task_session(session_id: int):
    task = random.choice(["book_flight", "schedule_meeting", "draft_email", "research_topic"])
    n_steps = random.randint(3, 6)

    retrieve_user_context("user_001", session_id)  # BUG-1 fires

    steps_completed = 0
    for step in range(n_steps):
        # BUG-4: randomly abandon mid-task
        if step == 2 and random.random() < 0.4:
            _write_event("task.step", "fail", {
                "session_id": session_id,
                "step": step,
                "task": task,
                "reason": "tool_timeout_abandoned",  # no checkpoint saved — BUG-4
            })
            break
        _write_event("task.step", "ok", {
            "session_id": session_id, "step": step, "task": task,
            "latency_ms": random.randint(300, 2000),
        })
        steps_completed += 1

    # BUG-3: schedule events without conflict check
    if task in ("book_flight", "schedule_meeting"):
        schedule_event(task, "2025-11-01T10:00:00", 60, session_id)  # BUG-5: no tz
        schedule_event(task, "2025-11-01T10:00:00", 60, session_id)  # deliberate overlap

    # BUG-2: confirm before all steps done
    confirm_task_complete(task, steps_completed, n_steps, session_id)


def run_sessions(n: int = 12):
    for i in range(n):
        run_task_session(session_id=i)


if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs written to {LOG_PATH}")
