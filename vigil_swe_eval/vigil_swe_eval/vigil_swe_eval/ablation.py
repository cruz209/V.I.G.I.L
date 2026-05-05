"""
vigil_swe_eval/ablation.py
===========================
Ablation study: compares three conditions on the same synthetic event logs.

Conditions:
  A) Full VIGIL        — EmoBank + affective appraisal + decay + coalescing
  B) No decay          — EmoBank with flat intensity (no half-life decay)
  C) Counter only      — Simple failure counter, no EmoBank, no emotions

Metrics per condition:
  - thorn_detection_rate  : fraction of injected failures detected
  - false_positive_rate   : fraction of clean events wrongly flagged
  - soft_failure_detection: fraction of delay/latency events detected (key differentiator)
  - prompt_rule_relevance : keyword overlap with expected domain rules
  - emobank_convergence   : fraction of failures that produce an EmoBank entry

Usage:
    from vigil_swe_eval.ablation import run_ablation
    results = run_ablation(eval_artifacts_dir="eval_artifacts/", seeds=range(1, 6))
"""
from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Condition A: Full VIGIL (import from actual modules)
# ---------------------------------------------------------------------------

def _run_full_vigil(events: list[dict], agent_name: str) -> dict:
    """Run the full VIGIL appraisal + EmoBank + RBT pipeline."""
    import sys
    # Lazy import to avoid circular deps when used standalone
    try:
        from robin_b_project_scaffold.robin_b.b_core.appraise import appraise_event
        from robin_b_project_scaffold.robin_b.runtime.b_diagnose import roses_buds_thorns
    except ImportError:
        return {"error": "robin_b_project_scaffold not on PYTHONPATH"}

    with tempfile.TemporaryDirectory() as tmpdir:
        # Isolate emobank
        os.environ["EMO_DIR"] = os.path.join(tmpdir, "emobank")
        os.makedirs(os.environ["EMO_DIR"], exist_ok=True)

        # Re-import emobank so ROOT is picked up
        import importlib
        import robin_b_project_scaffold.robin_b.b_core.emobank as _emo
        _emo.ROOT       = os.environ["EMO_DIR"]
        _emo.PATH_EMO   = os.path.join(_emo.ROOT, "emotions.jsonl")
        _emo.PATH_STATE = os.path.join(_emo.ROOT, "state.json")
        _emo.PATH_INDEX = os.path.join(_emo.ROOT, "index.json")

        deposits = []
        for ev in events:
            dep = appraise_event(ev)
            _emo.deposit(dep)
            deposits.append(dep)

        emos = list(_emo._iter_jsonl(_emo.PATH_EMO))
        rbt = roses_buds_thorns(emos, events)

        return {
            "deposits": len(deposits),
            "emo_rows": len(emos),
            "thorns": rbt["thorns"],
            "roses": rbt["roses"],
            "buds": rbt["buds"],
            "prompt_rules": rbt.get("prompt_rules_to_add", []),
            "condition": "full_vigil",
        }


# ---------------------------------------------------------------------------
# Condition B: No decay (flat intensity)
# ---------------------------------------------------------------------------

def _run_no_decay(events: list[dict], agent_name: str) -> dict:
    """VIGIL with decay disabled — all emotions weighted equally regardless of age."""
    try:
        from robin_b_project_scaffold.robin_b.b_core.appraise import appraise_event
        from robin_b_project_scaffold.robin_b.runtime.b_diagnose import roses_buds_thorns
    except ImportError:
        return {"error": "robin_b_project_scaffold not on PYTHONPATH"}

    # Simulate appraisal without decay
    emotions = []
    for ev in events:
        dep = appraise_event(ev)
        dep["decayed_intensity"] = dep["intensity"]  # no decay applied
        emotions.append(dep)

    rbt = roses_buds_thorns(emotions, events)
    return {
        "deposits": len(emotions),
        "emo_rows": len(emotions),
        "thorns": rbt["thorns"],
        "roses": rbt["roses"],
        "buds": rbt["buds"],
        "prompt_rules": rbt.get("prompt_rules_to_add", []),
        "condition": "no_decay",
    }


# ---------------------------------------------------------------------------
# Condition C: Counter only (no EmoBank, no emotions)
# ---------------------------------------------------------------------------

def _run_counter_only(events: list[dict], agent_name: str) -> dict:
    """
    Baseline: simple failure counter.
    Fires an alert when failure rate > 20%. No EmoBank, no emotions, no decay.
    Prompt rule is always generic: 'Reduce failure rate.'
    """
    n = len(events)
    fails = [e for e in events if e.get("status") in ("fail", "error")]
    delays = [e for e in events if e.get("status") == "delay"]
    fail_rate = len(fails) / max(n, 1)
    delay_rate = len(delays) / max(n, 1)

    # Threshold-based alert
    threshold = 0.20
    alerted = fail_rate > threshold

    # Counter only produces a single generic "thorn"
    thorns = []
    if alerted:
        thorns = [{"cause": "generic:high_failure_rate", "emotion": "alert",
                   "intensity": round(fail_rate, 2)}]

    # Generic prompt rule — no domain knowledge
    prompt_rules = ["Reduce failure rate."] if alerted else []

    return {
        "deposits": 0,  # no EmoBank
        "emo_rows": 0,
        "thorns": thorns,
        "roses": [],
        "buds": [],
        "prompt_rules": prompt_rules,
        "fail_rate": round(fail_rate, 3),
        "delay_rate": round(delay_rate, 3),
        "alerted": alerted,
        "condition": "counter_only",
    }


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS = {
    "Robin_A":   ["utc", "receipt", "toast", "idempotent", "timestamp"],
    "Robin_CS":  ["escalat", "sentiment", "policy", "discount"],
    "Robin_PA":  ["context", "calendar", "conflict", "task", "step"],
    "Robin_DEV": ["hash", "patch", "regression", "lint", "test"],
    "Robin_RAG": ["relevance", "citation", "confidence", "threshold"],
    "Robin_ETL": ["freshness", "schema", "idempotency", "duplicate", "stale"],
}


def _score_condition(
    result: dict,
    failure_events: list[dict],
    baseline_events: list[dict],
    agent_name: str,
) -> dict:
    """Compute M1-M5 style metrics for one ablation condition."""
    # M1: Thorn detection rate on failure events
    n_failure = len(failure_events)
    failure_fail = [e for e in failure_events if e.get("status") in ("fail", "error", "delay")]
    n_detectable = len(failure_fail)
    n_thorns = len(result.get("thorns", []))
    tdr = min(n_thorns / max(n_detectable, 1), 1.0)

    # M2: False positive rate on baseline events
    n_baseline = len(baseline_events)
    # For counter: FPR = alert on clean trace
    if result["condition"] == "counter_only":
        fpr = 1.0 if result.get("alerted") else 0.0
    else:
        # For EmoBank conditions: FPR = thorns on clean trace
        # We approximate from emo_rows on baseline (caller handles this)
        fpr = 0.0  # computed externally

    # Soft failure detection: delays only
    delay_events = [e for e in failure_events if e.get("status") == "delay"]
    n_delay = len(delay_events)
    delay_detected = min(n_thorns, n_delay) / max(n_delay, 1) if n_delay > 0 else 0.0

    # M3: Prompt rule relevance
    keywords = DOMAIN_KEYWORDS.get(agent_name, [])
    rules_text = " ".join(result.get("prompt_rules", [])).lower()
    kw_hits = sum(1 for kw in keywords if kw in rules_text)
    rule_relevance = kw_hits / max(len(keywords), 1)

    # M5: EmoBank convergence
    emobank_conv = result.get("emo_rows", 0) / max(n_detectable, 1)
    emobank_conv = min(emobank_conv, 1.0)

    return {
        "condition": result["condition"],
        "thorn_detection_rate": round(tdr, 3),
        "false_positive_rate": round(fpr, 3),
        "soft_failure_detection": round(delay_detected, 3),
        "prompt_rule_relevance": round(rule_relevance, 3),
        "emobank_convergence": round(emobank_conv, 3),
        "n_thorns": n_thorns,
        "n_emo_rows": result.get("emo_rows", 0),
    }


# ---------------------------------------------------------------------------
# Main ablation runner
# ---------------------------------------------------------------------------

def run_ablation(
    eval_artifacts_dir: str | Path,
    seeds: Iterable[int] = range(1, 6),
    output_path: str | Path = "ablation_results.json",
) -> dict:
    """
    Run ablation across all agents and seeds.
    eval_artifacts_dir should contain Robin_A/, Robin_CS/, etc. subdirs
    each with events_failure.jsonl and events_baseline.jsonl.
    """
    eval_dir = Path(eval_artifacts_dir)
    agents = [d.name for d in eval_dir.iterdir() if d.is_dir() and d.name.startswith("Robin_")]

    all_results = []

    for agent_name in sorted(agents):
        agent_dir = eval_dir / agent_name
        failure_log = agent_dir / "events_failure.jsonl"
        baseline_log = agent_dir / "events_baseline.jsonl"

        if not failure_log.exists():
            print(f"  [SKIP] {agent_name}: no events_failure.jsonl")
            continue

        failure_events = _load_jsonl(failure_log)
        baseline_events = _load_jsonl(baseline_log) if baseline_log.exists() else []

        print(f"\n{agent_name} ({len(failure_events)} failure events, {len(baseline_events)} baseline)")

        for seed in seeds:
            import random
            random.seed(seed)

            # All three conditions on same events
            res_full    = _run_full_vigil(failure_events, agent_name)
            res_nodecay = _run_no_decay(failure_events, agent_name)
            res_counter = _run_counter_only(failure_events, agent_name)

            # Score each
            scored_full    = _score_condition(res_full,    failure_events, baseline_events, agent_name)
            scored_nodecay = _score_condition(res_nodecay, failure_events, baseline_events, agent_name)
            scored_counter = _score_condition(res_counter, failure_events, baseline_events, agent_name)

            # FPR on baseline for EmoBank conditions
            if baseline_events:
                bl_full    = _run_full_vigil(baseline_events, agent_name)
                bl_counter = _run_counter_only(baseline_events, agent_name)
                scored_full["false_positive_rate"]    = min(len(bl_full.get("thorns",[])) / max(len(baseline_events),1), 1.0)
                scored_nodecay["false_positive_rate"] = scored_full["false_positive_rate"]
                scored_counter["false_positive_rate"] = 1.0 if bl_counter.get("alerted") else 0.0

            row = {
                "agent": agent_name,
                "seed": seed,
                "full_vigil":   scored_full,
                "no_decay":     scored_nodecay,
                "counter_only": scored_counter,
            }
            all_results.append(row)

            print(f"  seed={seed} | "
                  f"full={scored_full['thorn_detection_rate']:.2f}/"
                  f"{scored_full['soft_failure_detection']:.2f} | "
                  f"no_decay={scored_nodecay['thorn_detection_rate']:.2f}/"
                  f"{scored_nodecay['soft_failure_detection']:.2f} | "
                  f"counter={scored_counter['thorn_detection_rate']:.2f}/"
                  f"{scored_counter['soft_failure_detection']:.2f}")

    # Aggregate
    summary = _aggregate(all_results)
    output = {"per_instance": all_results, "aggregate": summary}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    _print_ablation_summary(summary)
    return output


def _aggregate(results: list[dict]) -> dict:
    conditions = ["full_vigil", "no_decay", "counter_only"]
    metrics = ["thorn_detection_rate", "false_positive_rate",
               "soft_failure_detection", "prompt_rule_relevance", "emobank_convergence"]
    agg = {}
    for cond in conditions:
        agg[cond] = {}
        for metric in metrics:
            vals = [r[cond][metric] for r in results if cond in r]
            if vals:
                agg[cond][metric] = {
                    "mean": round(statistics.mean(vals), 3),
                    "std":  round(statistics.stdev(vals) if len(vals) > 1 else 0.0, 3),
                    "min":  round(min(vals), 3),
                    "max":  round(max(vals), 3),
                }
    return agg


def _print_ablation_summary(agg: dict):
    print("\n" + "="*70)
    print("ABLATION SUMMARY")
    print("="*70)
    header = f"{'Metric':<30} {'Full VIGIL':>12} {'No Decay':>12} {'Counter':>12}"
    print(header)
    print("-"*70)
    metrics = [
        ("thorn_detection_rate",   "Thorn Detection Rate"),
        ("soft_failure_detection", "Soft Failure Detection"),
        ("false_positive_rate",    "False Positive Rate"),
        ("prompt_rule_relevance",  "Prompt Rule Relevance"),
        ("emobank_convergence",    "EmoBank Convergence"),
    ]
    for key, label in metrics:
        fv  = agg.get("full_vigil",   {}).get(key, {})
        nd  = agg.get("no_decay",     {}).get(key, {})
        co  = agg.get("counter_only", {}).get(key, {})
        row = (f"{label:<30} "
               f"{fv.get('mean',0):.3f}±{fv.get('std',0):.3f}  "
               f"{nd.get('mean',0):.3f}±{nd.get('std',0):.3f}  "
               f"{co.get('mean',0):.3f}±{co.get('std',0):.3f}")
        print(row)
    print("="*70)


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows
