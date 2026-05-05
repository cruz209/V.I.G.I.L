"""
vigil-swe-eval
==============
VIGIL real-world evaluation harness.

Modules:
    swe_adapter  — Convert SWE-agent trajectories to VIGIL JSONL schema
    swe_runner   — End-to-end SWE-bench evaluation (baseline → VIGIL → patched)
    ablation     — Ablation study: Full VIGIL vs No Decay vs Counter Only
    baselines    — Baseline comparison: VIGIL vs Rule-Based vs Threshold monitors
    cli          — Command-line interface (vigil-swe command)
"""

__version__ = "0.1.0"
__author__ = "Christopher Cruz"

from vigil_swe_eval.swe_adapter import SWEAgentAdapter, traj_to_vigil_events
from vigil_swe_eval.ablation import run_ablation
from vigil_swe_eval.baselines import run_baseline_comparison, ThresholdMonitor, RuleBasedMonitor

__all__ = [
    "SWEAgentAdapter",
    "traj_to_vigil_events",
    "run_ablation",
    "run_baseline_comparison",
    "ThresholdMonitor",
    "RuleBasedMonitor",
]
