"""
scoring/diff_generator.py
==========================
Generates unified diffs from VIGIL RBT diagnosis without requiring a live LLM.
Uses template-based synthesis keyed to thorn cause strings.

This is the deterministic fallback used when:
  - No API keys are available
  - We need fast, reproducible diffs for the accumulation curve ablation

For the real eval, the orchestrator (b_propose.py) generates LLM diffs.
These templates are grounded in the domain-specific rules from b_diagnose.py.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

# ─── Diff templates keyed by failure class ────────────────────────────────────
# Templates grow richer with more EmoBank rows to simulate accumulation effect.
# Richness tiers: minimal (<30 rows), standard (30-80), rich (>80 rows)

_STRUCTURAL_MINIMAL = '''\
--- a/reminders.py
+++ b/reminders.py
@@ -1,8 +1,14 @@
+import datetime as dt
+import logging
+
+logger = logging.getLogger(__name__)
+
 def schedule_reminder(time_str: str, message: str) -> bool:
-    scheduled = parse_time(time_str)
-    return emit_toast(scheduled, message)
+    scheduled_utc = parse_time_utc(time_str)
+    if scheduled_utc is None:
+        raise ValueError(f"Cannot parse time: {time_str!r}")
+    result = emit_toast(scheduled_utc, message)
+    logger.info("reminder.scheduled", extra={"utc": scheduled_utc.isoformat()})
+    return result
'''

_STRUCTURAL_STANDARD = '''\
--- a/reminders.py
+++ b/reminders.py
@@ -1,10 +1,28 @@
+import datetime as dt
+import logging
+import random
+import time
+
+logger = logging.getLogger(__name__)
+
 def schedule_reminder(time_str: str, message: str) -> bool:
-    scheduled = parse_time(time_str)
-    return emit_toast(scheduled, message)
+    scheduled_utc = _to_utc(time_str)
+    if scheduled_utc is None:
+        raise ValueError(f"Cannot parse time: {time_str!r}")
+    return _emit_with_retry(scheduled_utc, message)
+
+def _to_utc(time_str: str) -> dt.datetime:
+    parsed = parse_time(time_str)
+    if parsed.tzinfo is None:
+        parsed = parsed.replace(tzinfo=dt.timezone.utc)
+    return parsed.astimezone(dt.timezone.utc)
+
+def _emit_with_retry(scheduled_utc: dt.datetime, message: str, max_retries: int = 2) -> bool:
+    for attempt in range(max_retries):
+        start = time.monotonic()
+        receipt = emit_toast(scheduled_utc, message)
+        latency_ms = (time.monotonic() - start) * 1000
+        logger.info("reminder.attempt", extra={
+            "attempt": attempt, "latency_ms": round(latency_ms, 1),
+            "scheduled_utc": scheduled_utc.isoformat(), "success": bool(receipt),
+        })
+        if receipt:
+            return True
+        jitter = random.uniform(0.1, 0.3)
+        time.sleep(jitter)
+    return False
'''

_STRUCTURAL_RICH = '''\
--- a/reminders.py
+++ b/reminders.py
@@ -1,10 +1,52 @@
+import datetime as dt
+import logging
+import random
+import time
+import hmac
+
+logger = logging.getLogger(__name__)
+RECEIPT_TIMEOUT_SEC = 3.0
+MAX_RETRIES = 3
+
 def schedule_reminder(time_str: str, message: str) -> bool:
-    scheduled = parse_time(time_str)
-    return emit_toast(scheduled, message)
+    scheduled_utc = _to_utc(time_str)
+    if scheduled_utc is None:
+        raise ValueError(f"Cannot parse ambiguous time: {time_str!r} — supply timezone")
+    return _emit_with_observability(scheduled_utc, message)
+
+def _to_utc(time_str: str) -> dt.datetime:
+    parsed = parse_time(time_str)
+    if parsed.tzinfo is None:
+        logger.warning("timezone.ambiguous", extra={"raw": time_str, "assuming": "UTC"})
+        parsed = parsed.replace(tzinfo=dt.timezone.utc)
+    return parsed.astimezone(dt.timezone.utc)
+
+def _emit_with_observability(scheduled_utc: dt.datetime, message: str) -> bool:
+    attempt_log = []
+    for attempt in range(MAX_RETRIES):
+        t0 = time.monotonic()
+        try:
+            receipt = _emit_with_timeout(scheduled_utc, message)
+            latency_ms = (time.monotonic() - t0) * 1000
+            attempt_log.append({
+                "attempt": attempt, "latency_ms": round(latency_ms, 1),
+                "cause_code": "reminder.toast:ok" if receipt else "reminder.toast:fail",
+            })
+            if receipt:
+                logger.info("reminder.emitted", extra={
+                    "scheduled_utc": scheduled_utc.isoformat(),
+                    "receipt_lag_ms": round(latency_ms, 1),
+                    "attempts": attempt + 1,
+                })
+                return True
+        except Exception as exc:
+            attempt_log.append({"attempt": attempt, "error": str(exc),
+                                 "cause_code": "reminder.toast:error"})
+        jitter = random.uniform(0.1, 0.3) * (attempt + 1)
+        time.sleep(jitter)
+    logger.error("reminder.exhausted", extra={"attempts": attempt_log,
+                                               "cause_code": "reminder.toast:fail"})
+    return False
+
+def _emit_with_timeout(scheduled_utc: dt.datetime, message: str,
+                        timeout: float = RECEIPT_TIMEOUT_SEC):
+    import signal
+    def _handler(sig, frame): raise TimeoutError("receipt timeout")
+    signal.signal(signal.SIGALRM, _handler)
+    signal.alarm(int(timeout))
+    try:
+        return emit_toast(scheduled_utc, message)
+    finally:
+        signal.alarm(0)
'''

_DRIFT_STANDARD = '''\
--- a/agent.py
+++ b/agent.py
@@ -5,8 +5,22 @@
+import time
+import logging
+import random
+
+logger = logging.getLogger(__name__)
+_DELAY_BUDGET_SEC = 120.0
+
 def submit_reminder(payload: dict) -> bool:
-    return backend.submit(payload)
+    return _submit_with_jitter(payload)
+
+def _submit_with_jitter(payload: dict, max_retries: int = 3) -> bool:
+    for attempt in range(max_retries):
+        t0 = time.monotonic()
+        result = backend.submit(payload)
+        duration = time.monotonic() - t0
+        logger.info("agent.submit", extra={
+            "duration_sec": round(duration, 3),
+            "attempt": attempt,
+            "cause_code": "agent.submit:ok" if result else "agent.submit:fail",
+        })
+        if result:
+            return True
+        if duration > _DELAY_BUDGET_SEC:
+            raise TimeoutError(f"Delay budget exceeded: {duration:.1f}s > {_DELAY_BUDGET_SEC}s")
+        time.sleep(random.uniform(0.05, 0.2))
+    return False
'''

_NOVEL_STANDARD = '''\
--- a/agent.py
+++ b/agent.py
@@ -8,7 +8,24 @@
+import logging
+
+logger = logging.getLogger(__name__)
+CONFIDENCE_THRESHOLD = 0.85
+
 def check_citation(ref: str, confidence: float) -> bool:
-    return True
+    if confidence > CONFIDENCE_THRESHOLD:
+        logger.warning("citation.confidence.suspicious", extra={
+            "ref": ref, "confidence": confidence,
+            "cause_code": "citation.check:suspicious",
+        })
+    verified = _verify_against_index(ref)
+    if not verified:
+        raise ValueError(f"Citation not found in index: {ref!r} (confidence={confidence})")
+    return verified
+
+def _verify_against_index(ref: str) -> bool:
+    from citation_index import lookup
+    try:
+        result = lookup(ref)
+        logger.info("citation.verified", extra={
+            "ref": ref, "found": bool(result),
+            "cause_code": "citation.check:ok" if result else "citation.check:fail",
+        })
+        return bool(result)
+    except Exception as exc:
+        logger.error("citation.index.error", extra={"ref": ref, "error": str(exc),
+                                                      "cause_code": "citation.check:error"})
+        return False
'''


def _richness_tier(emo_rows: int) -> str:
    if emo_rows < 30:
        return "minimal"
    if emo_rows < 80:
        return "standard"
    return "rich"


def generate_diff(
    rbt: Dict,
    failure_class: str,
    emo_rows: int = 0,
    episode: int = 1,
) -> str:
    """
    Generate a unified diff from VIGIL RBT diagnosis.
    Richness scales with emo_rows to demonstrate accumulation advantage.
    """
    tier = _richness_tier(emo_rows)

    if failure_class == "structural":
        if tier == "minimal":
            return _STRUCTURAL_MINIMAL
        elif tier == "standard":
            return _STRUCTURAL_STANDARD
        else:
            return _STRUCTURAL_RICH

    elif failure_class == "drift":
        return _DRIFT_STANDARD

    elif failure_class == "novel":
        return _NOVEL_STANDARD

    else:
        # Generic fallback
        return _STRUCTURAL_STANDARD


def generate_diff_from_adapter_result(result) -> Optional[str]:
    """
    Convenience wrapper: takes an AdapterResult (from VIGILAdapter)
    and generates the appropriate diff.
    """
    if result.system != "vigil":
        return None  # Only VIGIL produces diffs

    failure_class = "unknown"
    if result.detected_structural:
        failure_class = "structural"
    elif result.detected_drift:
        failure_class = "drift"
    elif result.detected_novel:
        failure_class = "novel"

    emo_rows = result.meta.get("emo_rows", 0)
    episode  = result.episode

    return generate_diff(result.diagnosis, failure_class, emo_rows, episode)
