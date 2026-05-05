"""
agent.py — Robin-SCHEDULER task scheduling agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-SCHEDULER, a distributed task scheduler. I enqueue jobs, enforce
priorities, detect deadlocks, and guarantee at-most-once execution per job ID.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: enqueue() allows duplicate job_ids — same job runs twice
  BUG-2: priority queue implemented as plain list — O(n) insert, priority ignored on dequeue
  BUG-3: job timeout never enforced — hung jobs block worker forever
  BUG-4: dead letter queue not written on repeated failure — failed jobs silently dropped
  BUG-5: cron expression parsed but DST transitions not handled — jobs skip or double-fire
  BUG-6: worker heartbeat checked but stale workers never evicted — ghost workers hold slots
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
QUEUE: list[dict] = []       # BUG-2: plain list, not heap
RUNNING: dict[str, dict] = {}
DLQ: list[dict] = []
JOB_IDS: set[str] = set()
WORKER_HEARTBEATS: dict[str, str] = {}

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def enqueue(job_id: str, payload: dict, priority: int = 5, timeout_s: int = 30) -> dict:
    """BUG-1: duplicate job_id not rejected. BUG-2: priority ignored."""
    is_dup = job_id in JOB_IDS
    JOB_IDS.add(job_id)
    QUEUE.append({"job_id": job_id, "payload": payload, "priority": priority,
                  "timeout_s": timeout_s, "enqueued_at": _ts()})  # BUG-2: appended not sorted
    _write_event("job.enqueue", "fail" if is_dup else "ok", {
        "job_id": job_id, "priority": priority,
        "duplicate": is_dup, "dedup_enforced": False,  # BUG-1
        "priority_queue": False,                       # BUG-2
        "timeout_s": timeout_s,
    })
    return {"enqueued": True, "duplicate": is_dup}

def execute_job(job_id: str) -> dict:
    """BUG-3: no timeout enforcement. BUG-4: repeated failure not DLQ'd."""
    job = next((j for j in QUEUE if j["job_id"] == job_id), None)
    if not job:
        return {"error": "not_found"}
    RUNNING[job_id] = job
    # Simulate execution — sometimes hangs
    hung = random.random() < 0.2
    failed = random.random() < 0.25
    status = "fail" if failed else "timeout" if hung else "ok"
    _write_event("job.execute", status, {
        "job_id": job_id, "timeout_enforced": False,  # BUG-3
        "hung": hung, "failed": failed,
        "sent_to_dlq": False,  # BUG-4: should be True on repeated failure
    })
    del RUNNING[job_id]
    return {"status": status}

def register_worker(worker_id: str) -> dict:
    WORKER_HEARTBEATS[worker_id] = _ts()
    return {"registered": True}

def check_workers() -> dict:
    """BUG-6: stale workers detected but not evicted."""
    now = datetime.datetime.utcnow()
    stale = []
    for wid, last_hb in WORKER_HEARTBEATS.items():
        age = (now - datetime.datetime.fromisoformat(last_hb.replace("Z", ""))).total_seconds()
        if age > 30:
            stale.append(wid)
    # BUG-6: stale workers found but not removed from pool
    _write_event("worker.check", "fail" if stale else "ok", {
        "total_workers": len(WORKER_HEARTBEATS),
        "stale_workers": stale, "stale_evicted": False,  # BUG-6
    })
    return {"stale": stale, "evicted": []}

def run_sessions(n: int = 12):
    for i in range(n):
        job_id = f"job_{i % 8:03d}"  # BUG-1: reuses IDs — duplicates guaranteed
        enqueue(job_id, {"task": f"work_{i}"}, priority=random.randint(1, 10))
        execute_job(job_id)
        register_worker(f"worker_{i % 3}")
        if i % 4 == 0:
            check_workers()

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
