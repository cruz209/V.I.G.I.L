# robin_b/runtime/b_diagnose.py
from __future__ import annotations
from typing import Dict, List, Tuple
import math

# Tunables
POS = {"pride", "joy", "gratitude", "relief", "calm"}
NEG = {"frustration", "anxiety"}
BUD_VALENCE_MIN = 0.2   # weak positives → Buds
ROSE_INTENS_MIN = 0.5   # strong positives → Roses
THORN_INTENS_MIN = 0.4  # moderate/strong negatives → Thorns

def _score_bucket(entries: List[Dict]) -> float:
    # Simple sum of intensities
    return sum(float(e.get("intensity", 0.0)) for e in entries)

def roses_buds_thorns(recent_emotions: List[Dict], recent_events: List[Dict]) -> Dict:
    """
    Inputs:
      recent_emotions: latest EmoBank rows (raw or decayed snapshot feed)
      recent_events:   decoded JSON events (kind, status, payload...)
    Returns:
      {
        "roses":[...], "buds":[...], "thorns":[...],
        "diagnosis": "...",
        "prompt_rules_to_add":[str,...],
        "code_suggestions":[{"file":..., "summary":..., "hint":...}, ...]
      }
    """
    roses, buds, thorns = [], [], []

    for e in recent_emotions:
        emo = e.get("emotion","curiosity")
        I   = float(e.get("intensity",0.0))
        v   = float(e.get("valence",0.0))
        cause = e.get("cause","")

        if emo in POS and I >= ROSE_INTENS_MIN:
            roses.append({"cause": cause, "emotion": emo, "intensity": I})
        elif (emo in POS and v >= BUD_VALENCE_MIN) or (emo == "curiosity" and I >= 0.3):
            buds.append({"cause": cause, "emotion": emo, "intensity": I})
        elif emo in NEG and I >= THORN_INTENS_MIN:
            thorns.append({"cause": cause, "emotion": emo, "intensity": I})

    # Convert RBT into actions
    prompt_rules = []
    code_suggestions = []

    # Roses → codify & preserve
    if any("reminder.toast" in r["cause"] for r in roses):
        prompt_rules += [
          "Keep gating success toasts on receipt confirmation.",
          "Echo scheduled_utc after scheduling to confirm exact time."
        ]

    # Buds → grow (tighten weak positives)
    if buds:
        prompt_rules += [
          "After success with small lag, continue logging receipt_lag_ms and retry flag.",
          "When user time is ambiguous, restate the UTC timestamp and ask for confirmation."
        ]

    # Thorns → trim (diagnose & mitigate)
    any_delay = any("delay" in t["cause"] for t in thorns)
    any_fail  = any(("fail" in t["cause"]) or ("error" in t["cause"]) for t in thorns)

    if any_delay:
        prompt_rules += [
          "Convert all scheduled times to UTC before saving.",
          "Apply 100–300ms jitter before enqueue to reduce stampede.",
        ]
        code_suggestions.append({"file":"reminders.py",
                                 "summary":"UTC conversion + receipt wait + single retry with jitter.",
                                 "hint":"Add timezone-aware scheduling, await receipt ≤3s, then one retry."})
    if any_fail:
        prompt_rules += [
          "If a tool call fails, surface a brief apology and auto-retry once with exponential back-off."
        ]
        code_suggestions.append({"file":"tools/<name>.py",
                                 "summary":"Add bounded retry + structured error toasts.",
                                 "hint":"Wrap tool calls with try/except and emit toasts with error codes."})

    diag = f"Roses={len(roses)}, Buds={len(buds)}, Thorns={len(thorns)}."
    return {
        "roses": roses, "buds": buds, "thorns": thorns,
        "diagnosis": diag,
        "prompt_rules_to_add": dedupe_order(prompt_rules),
        "code_suggestions": code_suggestions
    }

def dedupe_order(xs: List[str]) -> List[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x); out.append(x)
    return out
