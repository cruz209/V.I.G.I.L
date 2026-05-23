from __future__ import annotations
import glob, os, re
from typing import List, Dict, Iterable

HOTSPOT_RULES = [
    {"pattern": r"\bdatetime\b|\btime\.time\(\)|\bpendulum\b|\barrow\b",
     "hint": "Time handling present; verify timezone awareness.", "file_like": "reminders.py"},
    {"pattern": r"\bscheduler\.enqueue_at\(",
     "hint": "Scheduling path; consider UTC + receipt gating + jitter.", "file_like": "reminders.py"},
    {"pattern": r"\breceipt[s]?\b|scheduled_utc",
     "hint": "Toast reliability signals present; ensure gating by receipt.", "file_like": "reminders.py"},
    {"pattern": r"BEGIN_ADAPTIVE_SECTION",
     "hint": "Prompt adaptive block found; safe to update.", "file_like": "agent.py"},
    {"pattern": r"\bexcept\s+(Exception|TimeoutError)",
     "hint": "Exception handling; consider bounded retry/backoff.", "file_like": ""},
]

def _iter_files(glob_expr: str) -> Iterable[str]:
    # Supports ** recursion if provided; default to recursive search where used
    return glob.glob(glob_expr, recursive=True)

def review_codebase(paths_glob: str) -> List[Dict]:
    findings: List[Dict] = []
    seen = set()
    for path in _iter_files(paths_glob):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            continue
        for rule in HOTSPOT_RULES:
            if re.search(rule["pattern"], txt):
                key = (path, rule["hint"])
                if key in seen:
                    continue
                seen.add(key)
                # include a tiny preview (first matching line) to speed triage
                preview = ""
                for i, line in enumerate(txt.splitlines(), 1):
                    if re.search(rule["pattern"], line):
                        preview = f"L{i}: {line.strip()[:160]}"
                        break
                findings.append({"path": path, "hint": rule["hint"], "preview": preview})
    findings.sort(key=lambda x: (x["path"], x["hint"]))
    return findings
