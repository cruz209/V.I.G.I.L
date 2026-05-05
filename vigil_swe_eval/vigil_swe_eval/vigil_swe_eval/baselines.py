"""
vigil_swe_eval/baselines.py
============================
Quantitative comparison of VIGIL against two baseline monitoring approaches:

  Baseline 1: Threshold Monitor
    - Fires alert when failure rate > configurable threshold
    - Produces generic "reduce failure rate" recommendation
    - No domain knowledge, no decay, no cause attribution

  Baseline 2: Rule-Based Monitor (LangSmith-style)
    - Predefined rules: if kind contains X and status=fail, fire alert Y
    - Human-authored rules, no learning, no affective substrate
    - Closer to commercial observability tools

  VIGIL: Full pipeline as implemented

Comparison dimensions:
  1. Soft failure detection rate (delays, premature confirmations)
  2. Remediation specificity (does output name the actual file/function?)
  3. False positive rate on clean traces
  4. Cross-domain generalization (same system, different agent types)
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Baseline 1: Threshold Monitor
# ---------------------------------------------------------------------------

class ThresholdMonitor:
    """
    Fires when failure rate exceeds threshold. Generic output only.
    """

    def __init__(self, threshold: float = 0.20):
        self.threshold = threshold

    def analyze(self, events: list[dict]) -> dict:
        n = len(events)
        if n == 0:
            return {"alerted": False, "diagnosis": "No events.", "rules": [], "files": []}

        fails  = sum(1 for e in events if e.get("status") in ("fail", "error"))
        delays = sum(1 for e in events if e.get("status") == "delay")
        rate   = fails / n
        alerted = rate > self.threshold

        return {
            "alerted": alerted,
            "fail_rate": round(rate, 3),
            "diagnosis": f"Failure rate {rate:.0%} {'exceeds' if alerted else 'is below'} threshold {self.threshold:.0%}.",
            "rules": ["Reduce failure rate."] if alerted else [],
            "files": [],      # no file targeting
            "specificity": 0, # no domain knowledge
        }


# ---------------------------------------------------------------------------
# Baseline 2: Rule-Based Monitor (LangSmith-style)
# ---------------------------------------------------------------------------

RULE_LIBRARY = [
    # Each rule: (kind_pattern, status, alert_label, rule_text, target_file)
    (r"reminder\.toast",    "fail",  "premature_toast",     "Gate toasts on receipt.",          "reminders.py"),
    (r"reminder\.toast",    "delay", "reminder_latency",    "Log receipt_lag_ms.",              "reminders.py"),
    (r"policy\.check",      "fail",  "policy_violation",    "Enforce policy ceiling.",          "agent.py"),
    (r"escalation",         "fail",  "missed_escalation",   "Escalate on low sentiment.",       "agent.py"),
    (r"context\.retrieve",  "fail",  "context_loss",        "Load user context at start.",      "agent.py"),
    (r"calendar\.schedule", "fail",  "calendar_conflict",   "Check conflicts before schedule.", "agent.py"),
    (r"syntax\.check",      "fail",  "lint_failure",        "Fix syntax before patching.",      "agent.py"),
    (r"patch\.apply",       "fail",  "patch_conflict",      "Verify file hash before patch.",   "agent.py"),
    (r"retrieval\.fetch",   "fail",  "low_relevance",       "Filter by relevance threshold.",   "agent.py"),
    (r"citation\.check",    "fail",  "fake_citation",       "Verify citations via lookup.",     "agent.py"),
    (r"freshness\.check",   "fail",  "stale_data",          "Reject stale batches.",            "agent.py"),
    (r"schema\.validate",   "fail",  "schema_drift",        "Validate schema before transform.","agent.py"),
    (r"token\.validate",    "fail",  "expired_token",       "Check token expiry.",              "agent.py"),
    (r"payment\.charge",    "fail",  "charge_failure",      "Add idempotency key.",             "agent.py"),
    (r"notification\.send", "fail",  "delivery_failure",    "Check opt-out before send.",       "agent.py"),
    (r"stock\.reserve",     "fail",  "oversell",            "Check stock before reserving.",    "agent.py"),
    (r"webhook\.deliver",   "fail",  "webhook_failure",     "Include HMAC signature.",          "agent.py"),
    (r"event\.track",       "fail",  "tracking_failure",    "Mask PII before storing.",         "agent.py"),
    (r"job\.enqueue",       "fail",  "duplicate_job",       "Reject duplicate job IDs.",        "agent.py"),
    (r"alert\.fire",        "fail",  "alert_storm",         "Add cooldown to alerts.",          "agent.py"),
    (r"intent\.classify",   "fail",  "low_confidence",      "Reject below confidence threshold.","agent.py"),
    (r"media\.transcode",   "fail",  "transcode_failure",   "Verify checksum after transcode.", "agent.py"),
    (r"recommendation",     "fail",  "bad_recommendation",  "Filter blocklisted items.",        "agent.py"),
    (r"cache\.get",         "fail",  "stale_cache",         "Enforce TTL on reads.",            "agent.py"),
    (r"agent\.response",    "delay", "response_latency",    "Bound response latency.",          "agent.py"),
    (r"tool\.call",         "fail",  "tool_hallucination",  "Validate tool call schema.",       "agent.py"),
    (r"test\.run",          "fail",  "test_regression",     "Compare against baseline results.","agent.py"),
]


class RuleBasedMonitor:
    """
    Human-authored rules that fire on specific (kind, status) patterns.
    Analogous to LangSmith/Langfuse custom evaluators or Datadog monitors.
    """

    def __init__(self):
        self.rules = [
            (re.compile(pattern), status, label, rule, file_)
            for pattern, status, label, rule, file_ in RULE_LIBRARY
        ]

    def analyze(self, events: list[dict]) -> dict:
        fired = []
        files = set()

        for ev in events:
            kind   = ev.get("kind", "")
            status = ev.get("status", "")
            for pattern, rule_status, label, rule_text, target_file in self.rules:
                if pattern.search(kind) and status == rule_status:
                    fired.append({"label": label, "rule": rule_text, "file": target_file})
                    files.add(target_file)
                    break  # one rule per event

        # Deduplicate fired rules by label
        seen = set()
        unique_fired = []
        for f in fired:
            if f["label"] not in seen:
                seen.add(f["label"])
                unique_fired.append(f)

        alerted = len(unique_fired) > 0
        return {
            "alerted": alerted,
            "n_rules_fired": len(unique_fired),
            "diagnosis": f"{len(unique_fired)} rule(s) fired." if alerted else "No rules fired.",
            "rules": [f["rule"] for f in unique_fired],
            "files": list(files),
            "specificity": len(files),  # number of distinct files targeted
            "fired_details": unique_fired,
        }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS = {
    "Robin_A":   ["utc", "receipt", "toast", "idempotent", "timestamp"],
    "Robin_CS":  ["escalat", "sentiment", "policy", "discount"],
    "Robin_PA":  ["context", "calendar", "conflict", "task", "step"],
    "Robin_DEV": ["hash", "patch", "regression", "lint", "test"],
    "Robin_RAG": ["relevance", "citation", "confidence", "threshold"],
    "Robin_ETL": ["freshness", "schema", "idempotency", "duplicate", "stale"],
}


def _score(result: dict, failure_events: list[dict], baseline_events: list[dict],
           agent_name: str, vigil_result: Optional[dict] = None) -> dict:
    n_fail = len(failure_events)
    n_base = len(baseline_events)

    # Soft failure detection (delays specifically)
    delay_events = [e for e in failure_events if e.get("status") == "delay"]
    n_delay = len(delay_events)

    # For VIGIL: soft failures detected = thorns with anxiety cause
    # For baselines: use rules fired that match delay patterns
    if vigil_result:
        thorns = vigil_result.get("thorns", [])
        soft_detected = sum(1 for t in thorns if "delay" in t.get("cause","").lower()
                           or t.get("emotion") == "anxiety")
        tdr = min(len(thorns) / max(n_fail * 0.5, 1), 1.0)  # ~50% of events are detectable
    else:
        rules_text = " ".join(result.get("rules", [])).lower()
        soft_detected = sum(1 for e in delay_events
                           if any(kw in rules_text for kw in ["latency", "delay", "lag"]))
        tdr = min(result.get("n_rules_fired", 1 if result.get("alerted") else 0) / max(n_fail * 0.5, 1), 1.0)

    soft_rate = soft_detected / max(n_delay, 1)

    # FPR on baseline
    if vigil_result:
        fpr = 0.0  # computed separately
    else:
        base_result = ThresholdMonitor().analyze(baseline_events) if result.get("fail_rate") is not None \
                      else RuleBasedMonitor().analyze(baseline_events)
        fpr = 1.0 if base_result.get("alerted") else 0.0

    # Specificity: does it name a real file?
    files = result.get("files", []) or (vigil_result or {}).get("code_suggestions", [])
    specificity = min(len(files) / 1.0, 1.0)

    # Rule relevance
    keywords = DOMAIN_KEYWORDS.get(agent_name, [])
    rules_text = " ".join(result.get("rules", [])).lower()
    kw_hits = sum(1 for kw in keywords if kw in rules_text)
    rule_relevance = kw_hits / max(len(keywords), 1)

    return {
        "thorn_detection_rate": round(tdr, 3),
        "soft_failure_detection": round(soft_rate, 3),
        "false_positive_rate": round(fpr, 3),
        "prompt_rule_relevance": round(rule_relevance, 3),
        "specificity": round(specificity, 3),
    }


# ---------------------------------------------------------------------------
# Main comparison runner
# ---------------------------------------------------------------------------

def run_baseline_comparison(
    eval_artifacts_dir: str | Path,
    vigil_scores: Optional[dict] = None,
    output_path: str | Path = "baseline_comparison.json",
) -> dict:
    """
    Compare VIGIL against ThresholdMonitor and RuleBasedMonitor on all agents.

    vigil_scores: optional pre-computed VIGIL scores keyed by agent name.
    If not provided, only baselines are computed.
    """
    eval_dir = Path(eval_artifacts_dir)
    agents = sorted(d.name for d in eval_dir.iterdir()
                    if d.is_dir() and d.name.startswith("Robin_"))

    threshold_mon = ThresholdMonitor(threshold=0.20)
    rulebased_mon = RuleBasedMonitor()

    all_results = []

    print("\nBaseline Comparison")
    print("="*70)
    print(f"{'Agent':<12} {'VIGIL TDR':>10} {'RuleMon TDR':>12} {'Threshold TDR':>14} "
          f"{'VIGIL Soft':>11} {'RuleMon Soft':>13}")
    print("-"*70)

    for agent_name in agents:
        agent_dir = eval_dir / agent_name
        failure_log  = agent_dir / "events_failure.jsonl"
        baseline_log = agent_dir / "events_baseline.jsonl"

        if not failure_log.exists():
            continue

        failure_events  = _load_jsonl(failure_log)
        baseline_events = _load_jsonl(baseline_log) if baseline_log.exists() else []

        thresh_result = threshold_mon.analyze(failure_events)
        rule_result   = rulebased_mon.analyze(failure_events)

        thresh_scored = _score(thresh_result, failure_events, baseline_events, agent_name)
        rule_scored   = _score(rule_result,   failure_events, baseline_events, agent_name)

        # VIGIL scores from pre-computed results or score.json
        vigil_scored = {}
        score_json = agent_dir / "score.json"
        if score_json.exists():
            with open(score_json, encoding="utf-8") as f:
                sj = json.load(f)
            vigil_scored = {
                "thorn_detection_rate": sj.get("thorn_detection_rate", 0),
                "soft_failure_detection": sj.get("emobank_convergence", 0),  # proxy
                "false_positive_rate": sj.get("false_positive_rate", 0),
                "prompt_rule_relevance": sj.get("rule_relevance_score", 0),
                "specificity": sj.get("code_accuracy_score", 0),
            }
        elif vigil_scores and agent_name in vigil_scores:
            vigil_scored = vigil_scores[agent_name]

        row = {
            "agent": agent_name,
            "vigil":       vigil_scored,
            "rule_based":  rule_scored,
            "threshold":   thresh_scored,
        }
        all_results.append(row)

        print(f"{agent_name:<12} "
              f"{vigil_scored.get('thorn_detection_rate',0):>10.3f} "
              f"{rule_scored['thorn_detection_rate']:>12.3f} "
              f"{thresh_scored['thorn_detection_rate']:>14.3f} "
              f"{vigil_scored.get('soft_failure_detection',0):>11.3f} "
              f"{rule_scored['soft_failure_detection']:>13.3f}")

    print("="*70)

    # Aggregate
    summary = _aggregate_comparison(all_results)
    output = {"per_agent": all_results, "aggregate": summary}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    _print_comparison_summary(summary)
    return output


def _aggregate_comparison(results: list[dict]) -> dict:
    systems = ["vigil", "rule_based", "threshold"]
    metrics = ["thorn_detection_rate", "soft_failure_detection",
               "false_positive_rate", "prompt_rule_relevance", "specificity"]
    agg = {}
    for sys_ in systems:
        agg[sys_] = {}
        for metric in metrics:
            vals = [r[sys_].get(metric, 0) for r in results if sys_ in r and r[sys_]]
            if vals:
                agg[sys_][metric] = {
                    "mean": round(statistics.mean(vals), 3),
                    "std":  round(statistics.stdev(vals) if len(vals) > 1 else 0.0, 3),
                }
    return agg


def _print_comparison_summary(agg: dict):
    print("\nAggregate Comparison")
    print("="*70)
    metrics = [
        ("thorn_detection_rate",   "Thorn Detection Rate"),
        ("soft_failure_detection", "Soft Failure Detection"),
        ("false_positive_rate",    "False Positive Rate (↓)"),
        ("prompt_rule_relevance",  "Prompt Rule Relevance"),
        ("specificity",            "Remediation Specificity"),
    ]
    header = f"{'Metric':<30} {'VIGIL':>12} {'Rule-Based':>12} {'Threshold':>12}"
    print(header)
    print("-"*70)
    for key, label in metrics:
        v  = agg.get("vigil",      {}).get(key, {})
        r  = agg.get("rule_based", {}).get(key, {})
        t  = agg.get("threshold",  {}).get(key, {})
        print(f"{label:<30} "
              f"{v.get('mean',0):.3f}±{v.get('std',0):.3f}  "
              f"{r.get('mean',0):.3f}±{r.get('std',0):.3f}  "
              f"{t.get('mean',0):.3f}±{t.get('std',0):.3f}")
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
