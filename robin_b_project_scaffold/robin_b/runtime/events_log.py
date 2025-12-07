from __future__ import annotations
import os, json, datetime as dt
from typing import List, Dict

def fetch_recent_events(path: str = "logs/events.jsonl",
                        window_hours: int = 24,
                        limit: int = 500) -> List[Dict]:
    """
    Read recent events from a JSONL file. Each line:
      {"ts": "...Z", "kind": "...", "status": "...", "payload": {...}}
    Returns the newest <= limit events within window_hours.
    """
    since = dt.datetime.utcnow() - dt.timedelta(hours=window_hours)
    out: List[Dict] = []
    if not os.path.exists(path):
        return out

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                row = json.loads(s)
            except Exception:
                continue
            ts_raw = (row.get("ts") or "").replace("Z", "")
            try:
                t = dt.datetime.fromisoformat(ts_raw)
            except Exception:
                continue
            if t >= since:
                out.append(row)

    return out[-limit:]
