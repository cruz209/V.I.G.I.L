# robin_b/b_core/appraise.py
from __future__ import annotations
from typing import Dict, Any
from ..runtime.common import clamp

POSITIVE_STATUS = {"ok", "success", "completed", "resolved", "delivered"}

def _positive_magnitude(p: Dict[str, Any]) -> float:
    """
    Numeric-only celebration:
      - improvement_pct: 0–50% → 0.3–0.9 intensity
      - saved_ms: 0–2000 ms → 0.3–0.9 intensity
      - latency_ms < baseline_ms → maps the % improvement 0–50% → 0.3–0.9
    """
    if not p:
        return 0.0
    if "improvement_pct" in p:
        try:
            imp = float(p["improvement_pct"])
            return clamp(0.3 + clamp(imp, 0.0, 50.0)/50.0 * 0.6, 0.2, 0.95)
        except Exception:
            pass
    if "saved_ms" in p:
        try:
            saved = float(p["saved_ms"])
            return clamp(0.3 + clamp(saved, 0.0, 2000.0)/2000.0 * 0.6, 0.2, 0.95)
        except Exception:
            pass
    if "latency_ms" in p and "baseline_ms" in p:
        try:
            lat = float(p["latency_ms"]); base = float(p["baseline_ms"])
            if base > 0 and lat < base:
                imp = (base - lat)/base * 100.0
                return clamp(0.3 + clamp(imp, 0.0, 50.0)/50.0 * 0.6, 0.2, 0.95)
        except Exception:
            pass
    return 0.0

def ev_severity(ev: Dict) -> float:
    p = ev.get("payload", {}) or {}

    # Positive numeric magnitude wins if present
    mag = _positive_magnitude(p)
    if mag > 0:
        return mag

    # Delay severity (10 min → 1.0)
    if "delayed_by_sec" in p:
        d = float(p["delayed_by_sec"])
        return clamp(d / 600.0, 0.0, 1.0)

    # Fail-family events are strong
    status = ev.get("status")
    if status in ("fail", "timeout", "error"):
        return 0.9

    # User complaint is strong but slightly less than fail
    if status == "complaint":
        return 0.8

    # Default small impact
    return 0.3

def appraise_event(ev: Dict) -> Dict:
    """
    Deterministic, self-contained appraisal (no keywords, no external agent).
    Emits positive emotions for genuine successes or measured improvements.
    """
    kind = ev.get("kind", "")
    status = ev.get("status", "")
    p = ev.get("payload", {}) or {}

    # Baseline
    emotion, valence, energy = "curiosity", 0.4, 0.5

    # Negative / risk states
    if status in ("fail","timeout") or "error" in kind:
        emotion, valence, energy = "frustration", -0.7, 0.8
    elif status == "delay" or "delay" in kind:
        emotion, valence, energy = "anxiety", -0.6, 0.6

    # Positive: explicit success or measurable improvement
    elif status in POSITIVE_STATUS:
        if _positive_magnitude(p) > 0:
            # measurable uplift → pride (achievement)
            emotion, valence, energy = "pride", 0.85, 0.6
        else:
            # routine success → relief (it worked)
            emotion, valence, energy = "relief", 0.6, 0.3

    intensity = clamp(ev_severity(ev), 0.2, 0.95)

    # Nudge floor for strong positives so they register in mood
    if emotion in ("pride",) and intensity < 0.5:
        intensity = 0.5

    return {
        "emotion": emotion,
        "intensity": intensity,
        "valence": valence,
        "cause": f"{kind}:{status}",
        "appraisal_summary": f"I felt {emotion} due to {kind} ({status}).",
        "confidence": 0.7
    }
