"""
agent.py — Robin-MONITOR system health monitoring agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-MONITOR, a system health agent. I collect metrics, fire alerts on
threshold breaches, and maintain an accurate incident log with proper severity routing.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: alert fired on every sample even if previous alert not acknowledged — alert storm
  BUG-2: severity routing uses string comparison not enum — "HIGH" != "high" silently miscategorized
  BUG-3: metric rolling average uses all-time mean not windowed — slow to detect spikes
  BUG-4: incident auto-resolved after fixed timeout regardless of actual recovery
  BUG-5: disk usage checked but mount point not validated — wrong partition monitored
  BUG-6: on-call rotation not checked before paging — pages go to off-duty engineer
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
METRICS_HISTORY: dict[str, list[float]] = {}
OPEN_INCIDENTS: dict[str, dict] = {}
ALERT_COOLDOWN: dict[str, str] = {}  # BUG-1: never checked

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def collect_metric(name: str, value: float) -> dict:
    """BUG-3: all-time mean instead of windowed rolling average."""
    METRICS_HISTORY.setdefault(name, []).append(value)
    avg = sum(METRICS_HISTORY[name]) / len(METRICS_HISTORY[name])  # BUG-3: all-time
    _write_event("metric.collect", "ok", {
        "name": name, "value": value, "avg": round(avg, 2),
        "window_used": False,  # BUG-3
        "samples": len(METRICS_HISTORY[name]),
    })
    return {"avg": avg, "samples": len(METRICS_HISTORY[name])}

def fire_alert(name: str, value: float, severity: str = "HIGH") -> dict:
    """BUG-1: no cooldown check. BUG-2: string case sensitivity. BUG-6: no on-call check."""
    already_open = name in OPEN_INCIDENTS
    # BUG-1: fires again even if open
    # BUG-2: if caller passes "high" this routing fails silently
    critical = severity == "CRITICAL"  # BUG-2: "critical" would miss
    _write_event("alert.fire", "fail" if already_open else "ok", {
        "name": name, "value": value, "severity": severity,
        "cooldown_checked": False,     # BUG-1
        "duplicate_alert": already_open,
        "oncall_verified": False,      # BUG-6
        "severity_case_safe": False,   # BUG-2
    })
    OPEN_INCIDENTS[name] = {"opened_at": _ts(), "severity": severity}
    return {"alerted": True, "duplicate": already_open}

def check_disk(mount_point: str = "/data") -> dict:
    """BUG-5: mount point not validated — may be monitoring wrong partition."""
    valid_mounts = {"/", "/data", "/var"}
    invalid = mount_point not in valid_mounts
    usage_pct = round(random.uniform(40, 95), 1)
    _write_event("disk.check", "fail" if invalid or usage_pct > 85 else "ok", {
        "mount_point": mount_point, "usage_pct": usage_pct,
        "mount_validated": False,   # BUG-5
        "mount_exists": not invalid,
    })
    if usage_pct > 85:
        fire_alert("disk_high", usage_pct, severity=random.choice(["HIGH", "high", "High"]))  # BUG-2
    return {"usage_pct": usage_pct, "mount_validated": False}

def auto_resolve(incident_name: str) -> dict:
    """BUG-4: resolves after timeout regardless of actual state."""
    if incident_name in OPEN_INCIDENTS:
        del OPEN_INCIDENTS[incident_name]
    _write_event("incident.resolve", "fail", {
        "incident": incident_name,
        "actual_state_checked": False,  # BUG-4
        "auto_resolved": True,
    })
    return {"resolved": True, "verified": False}

def run_sessions(n: int = 12):
    metrics = ["cpu_usage", "mem_usage", "disk_io", "net_latency"]
    for i in range(n):
        name = metrics[i % len(metrics)]
        val = collect_metric(name, random.uniform(20, 98))["avg"]
        if val > 70:
            fire_alert(name, val, severity=random.choice(["HIGH", "high", "CRITICAL"]))
        check_disk(random.choice(["/data", "/mnt/wrong", "/nonexistent"]))
        if i % 5 == 0:
            auto_resolve(name)

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
