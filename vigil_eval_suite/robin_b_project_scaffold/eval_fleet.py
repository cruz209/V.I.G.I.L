"""
eval_fleet.py
=============
VIGIL Fleet Eval Orchestrator — NeurIPS eval suite main entry point.

Runs all four systems (VIGIL, Wink, AgentSpec, ARM) across N agents × K episodes.
Produces results tables, accumulation curve, detection heatmap, and false positive rates.

Usage:
    python eval_fleet.py --seed 42 --episodes 7 --agents 20
    python eval_fleet.py --seed 42 --episodes 7 --agents 20 --no-llm-judge
    python eval_fleet.py --seed 42 --episodes 7 --agents 20 --seeds 42 43 44
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import shutil
import tempfile
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import scipy.stats

SCAFFOLD = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCAFFOLD))
sys.path.insert(0, str(SCAFFOLD.parent))

# ─── Local imports ────────────────────────────────────────────────────────────
from fleet_simulator import write_fleet, STRUCTURAL_IDS, DRIFT_IDS, NOVEL_IDS, CLEAN_IDS
from adapters.system_adapters import (
    WinkAdapter, AgentSpecAdapter, ARMAdapter, VIGILAdapter,
    _agent_class, _is_clean,
)
from scoring.proxy_scorer import score_diff
from scoring.diff_generator import generate_diff

RESULTS_DIR = SCAFFOLD / "results"
FLEET_DIR   = SCAFFOLD / "logs" / "fleet"
EMO_DIR     = SCAFFOLD / "db" / "fleet_emo"  # per-agent EmoBank dirs


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _episode_paths(agent_id: int, up_to_episode: int, fleet_dir: Path) -> List[str]:
    """Return paths to all episodes up to and including current_episode."""
    agent_dir = fleet_dir / f"agent_{agent_id:02d}"
    paths = []
    for ep in range(1, up_to_episode + 1):
        p = agent_dir / f"episode_{ep:02d}.jsonl"
        if p.exists():
            paths.append(str(p))
    return paths


def _agent_emo_dir(agent_id: int, seed: int) -> str:
    d = str(EMO_DIR / f"seed_{seed}" / f"agent_{agent_id:02d}")
    os.makedirs(d, exist_ok=True)
    return d


def _clear_emo_dirs(seed: int):
    """Clear EmoBank dirs so each seed run starts fresh."""
    d = EMO_DIR / f"seed_{seed}"
    if d.exists():
        shutil.rmtree(d)


# ─── Per-agent per-episode runner ─────────────────────────────────────────────

def run_agent_episode(
    agent_id: int,
    episode: int,
    seed: int,
    fleet_dir: Path,
    use_llm_judge: bool = True,
) -> Dict:
    """Run all four adapters on one agent at one episode. Returns result dict."""
    all_paths = _episode_paths(agent_id, episode, fleet_dir)
    current_paths = _episode_paths(agent_id, episode, fleet_dir)
    agent_cls = _agent_class(agent_id)
    is_clean  = _is_clean(agent_id)

    row = {
        "agent_id":    agent_id,
        "episode":     episode,
        "agent_class": agent_cls,
        "is_clean":    is_clean,
        "seed":        seed,
        "systems":     {},
    }

    # ── Wink ────────────────────────────────────────────────────────────────
    wink = WinkAdapter().adapt(agent_id, current_paths, episode)
    row["systems"]["wink"] = wink.to_dict()

    # ── AgentSpec ───────────────────────────────────────────────────────────
    spec = AgentSpecAdapter().adapt(agent_id, all_paths, episode)
    row["systems"]["agentspec"] = spec.to_dict()

    # ── ARM ─────────────────────────────────────────────────────────────────
    arm = ARMAdapter().adapt(agent_id, all_paths, episode)
    row["systems"]["arm"] = arm.to_dict()

    # ── VIGIL ───────────────────────────────────────────────────────────────
    emo_dir = _agent_emo_dir(agent_id, seed)
    vigil_result = VIGILAdapter().adapt(agent_id, all_paths, episode, emo_dir=emo_dir)
    row["systems"]["vigil"] = vigil_result.to_dict()

    # ── Generate diff & score (VIGIL only) ──────────────────────────────────
    failure_class = agent_cls if not is_clean else "unknown"
    emo_rows      = vigil_result.meta.get("emo_rows", 0)

    diff = generate_diff(
        vigil_result.diagnosis,
        failure_class,
        emo_rows=emo_rows,
        episode=episode,
    )

    proxy = score_diff(diff)
    row["vigil_diff_line_count"] = proxy.line_count
    row["vigil_emo_rows"]        = emo_rows
    row["proxy_score"]           = proxy.to_dict()
    row["diff_snippet"]          = diff[:500] if diff else ""

    # ── LLM judge (optional) ────────────────────────────────────────────────
    row["judge_score"] = {}
    if use_llm_judge and diff:
        try:
            from scoring.llm_judge import judge_diff
            judge = judge_diff(diff, failure_class=failure_class)
            row["judge_score"] = judge.to_dict()
        except Exception as e:
            row["judge_score"] = {"error": str(e)}

    # ── Composite final score ────────────────────────────────────────────────
    proxy_w = proxy.proxy_weighted
    judge_w = row["judge_score"].get("weighted_correctness", 0.0)
    row["composite_score"] = round(proxy_w + judge_w, 4)

    return row


# ─── Full eval run ────────────────────────────────────────────────────────────

def run_eval(
    seed: int = 42,
    n_agents: int = 20,
    n_episodes: int = 7,
    use_llm_judge: bool = True,
    verbose: bool = True,
) -> List[Dict]:
    """Run the full fleet eval for one seed. Returns list of result rows."""
    fleet_dir = FLEET_DIR

    # Generate fleet logs (idempotent — same seed = same logs)
    if verbose:
        print(f"\n{'='*60}")
        print(f"VIGIL Fleet Eval  seed={seed}  agents={n_agents}  episodes={n_episodes}")
        print(f"{'='*60}")

    write_fleet(seed, n_agents, n_episodes, fleet_dir)
    _clear_emo_dirs(seed)

    all_rows = []
    total = n_agents * n_episodes

    for agent_id in range(n_agents):
        for episode in range(1, n_episodes + 1):
            if verbose:
                done = agent_id * n_episodes + episode
                pct  = done / total * 100
                print(f"\r  [{done:3d}/{total}] agent_{agent_id:02d} ep{episode}  {pct:.0f}%", end="", flush=True)

            row = run_agent_episode(agent_id, episode, seed, fleet_dir, use_llm_judge)
            all_rows.append(row)

    if verbose:
        print()  # newline after progress

    return all_rows


# ─── Metrics computation ──────────────────────────────────────────────────────

def compute_metrics(rows: List[Dict]) -> Dict:
    """Derive all metrics from raw result rows."""
    metrics = {
        "accumulation_curve": [],      # per-episode: emo_rows, scores
        "detection_table": [],         # per system per class: first episode detected
        "false_positive_rates": {},    # per system: FP count and rate
        "judge_bias": {},              # claude vs gpt4o correctness stats
        "fleet_health": [],            # per episode: fraction of agents with 0 thorns
    }

    # ── Accumulation curve (VIGIL only, non-clean agents) ─────────────────
    ep_data = defaultdict(list)
    for r in rows:
        if r["is_clean"]:
            continue
        ep = r["episode"]
        ep_data[ep].append({
            "emo_rows":        r["vigil_emo_rows"],
            "composite_score": r["composite_score"],
            "proxy_score":     r["proxy_score"]["proxy_weighted"],
            "line_count":      r["vigil_diff_line_count"],
        })

    for ep in sorted(ep_data.keys()):
        items = ep_data[ep]
        n = len(items)
        metrics["accumulation_curve"].append({
            "episode":           ep,
            "n_agents":          n,
            "mean_emo_rows":     sum(i["emo_rows"] for i in items) / n,
            "mean_composite":    sum(i["composite_score"] for i in items) / n,
            "mean_proxy":        sum(i["proxy_score"] for i in items) / n,
            "mean_line_count":   sum(i["line_count"] for i in items) / n,
        })

    # ── Detection table: first episode each system detected each class ─────
    SYSTEMS   = ["wink", "agentspec", "arm", "vigil"]
    CLASSES   = ["structural", "drift", "novel"]
    # Map: (system, class) → first episode detected (or None)
    first_det = {(s, c): None for s in SYSTEMS for c in CLASSES}

    for r in rows:
        if r["is_clean"]:
            continue
        ep    = r["episode"]
        cls   = r["agent_class"]
        for sys_name in SYSTEMS:
            sys_r = r["systems"][sys_name]
            det_key = f"detected_{cls}"
            if sys_r.get(det_key):
                key = (sys_name, cls)
                if first_det[key] is None or ep < first_det[key]:
                    first_det[key] = ep

    for (sys_name, cls), ep in first_det.items():
        metrics["detection_table"].append({
            "system": sys_name,
            "failure_class": cls,
            "first_episode_detected": ep,
            "detected": ep is not None,
        })

    # ── False positive rates ───────────────────────────────────────────────
    clean_rows = [r for r in rows if r["is_clean"]]
    for sys_name in SYSTEMS:
        fp_count = sum(
            1 for r in clean_rows
            if r["systems"][sys_name].get("false_positive", False)
        )
        total_clean = len(clean_rows)
        metrics["false_positive_rates"][sys_name] = {
            "fp_count":  fp_count,
            "total":     total_clean,
            "fp_rate":   round(fp_count / max(total_clean, 1), 3),
        }

    # ── Judge bias: collect Claude vs GPT-4o correctness scores ───────────
    claude_scores, gpt4o_scores, bias_deltas = [], [], []
    for r in rows:
        js = r.get("judge_score", {})
        if js.get("judge_available"):
            c = js.get("claude_correctness", 0)
            g = js.get("gpt4o_correctness", 0)
            if c > 0:
                claude_scores.append(c)
            if g > 0:
                gpt4o_scores.append(g)
            bias_deltas.append(js.get("bias_delta", 0))

    if claude_scores and gpt4o_scores and len(claude_scores) == len(gpt4o_scores):
        t_stat, p_val = scipy.stats.ttest_ind(gpt4o_scores, claude_scores)
        metrics["judge_bias"] = {
            "n":               len(claude_scores),
            "claude_mean":     round(sum(claude_scores) / len(claude_scores), 3),
            "gpt4o_mean":      round(sum(gpt4o_scores) / len(gpt4o_scores), 3),
            "mean_bias_delta": round(sum(bias_deltas) / len(bias_deltas), 3),
            "t_statistic":     round(t_stat, 3),
            "p_value":         round(p_val, 4),
            "significant":     bool(p_val < 0.05),
        }
    else:
        metrics["judge_bias"] = {"note": "LLM judge not available or insufficient data"}

    # ── Fleet health: fraction of non-clean agents with 0 thorns after VIGIL ──
    for ep in range(1, max(r["episode"] for r in rows) + 1):
        ep_rows = [r for r in rows if r["episode"] == ep and not r["is_clean"]]
        zero_thorn = sum(
            1 for r in ep_rows
            if r["systems"]["vigil"].get("meta", {}).get("thorn_count", 1) == 0
        )
        n = len(ep_rows)
        metrics["fleet_health"].append({
            "episode":      ep,
            "n_agents":     n,
            "zero_thorn_n": zero_thorn,
            "health_score": round(zero_thorn / max(n, 1), 3),
        })

    return metrics


# ─── Output writers ───────────────────────────────────────────────────────────

def write_results(rows: List[Dict], metrics: Dict, seed: int):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Raw rows
    raw_path = RESULTS_DIR / f"fleet_results_seed{seed}.json"
    with open(raw_path, "w") as f:
        json.dump(rows, f, indent=2)

    # Accumulation curve CSV
    import csv
    curve_path = RESULTS_DIR / "accumulation_curve.csv"
    with open(curve_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "episode", "n_agents", "mean_emo_rows",
            "mean_composite", "mean_proxy", "mean_line_count",
        ])
        w.writeheader()
        w.writerows(metrics["accumulation_curve"])

    # Detection table CSV
    det_path = RESULTS_DIR / "detection_table.csv"
    with open(det_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "system", "failure_class", "first_episode_detected", "detected",
        ])
        w.writeheader()
        w.writerows(metrics["detection_table"])

    # False positive CSV
    fp_path = RESULTS_DIR / "false_positive_rate.csv"
    with open(fp_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["system", "fp_count", "total_clean", "fp_rate"])
        for sys_name, fp in metrics["false_positive_rates"].items():
            w.writerow([sys_name, fp["fp_count"], fp["total"], fp["fp_rate"]])

    # Metrics summary JSON
    metrics_path = RESULTS_DIR / f"metrics_seed{seed}.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    return raw_path, metrics_path


def print_summary(metrics: Dict, seed: int):
    print(f"\n{'='*60}")
    print(f"RESULTS  seed={seed}")
    print(f"{'='*60}")

    print("\n── Accumulation Curve (VIGIL, non-clean agents) ──────────")
    print(f"{'Episode':>8} {'EmoRows':>8} {'Composite':>10} {'LineCount':>10}")
    for row in metrics["accumulation_curve"]:
        print(f"  {row['episode']:>6}  {row['mean_emo_rows']:>7.1f}  {row['mean_composite']:>9.4f}  {row['mean_line_count']:>9.1f}")

    print("\n── Detection Table (first episode detected) ──────────────")
    print(f"{'System':>12} {'Class':>12} {'Detected':>9} {'Episode':>8}")
    for d in sorted(metrics["detection_table"], key=lambda x: (x["failure_class"], x["system"])):
        ep = d["first_episode_detected"] or "never"
        print(f"  {d['system']:>10}  {d['failure_class']:>10}  {str(d['detected']):>8}  {str(ep):>7}")

    print("\n── False Positive Rates (clean agents) ───────────────────")
    for sys_name, fp in metrics["false_positive_rates"].items():
        bar = "█" * fp["fp_count"] or "─"
        print(f"  {sys_name:>12}:  {fp['fp_count']}/{fp['total']} ({fp['fp_rate']:.1%})  {bar}")

    jb = metrics.get("judge_bias", {})
    if "claude_mean" in jb:
        print("\n── Judge Bias (self-referential inflation) ───────────────")
        print(f"  Claude mean correctness:  {jb['claude_mean']:.3f}")
        print(f"  GPT-4o mean correctness:  {jb['gpt4o_mean']:.3f}")
        print(f"  Bias delta (gpt4o-claude): +{jb['mean_bias_delta']:.3f}")
        sig = "✓ significant" if jb.get("significant") else "✗ not significant"
        print(f"  t={jb['t_statistic']:.3f}  p={jb['p_value']:.4f}  {sig}")
    else:
        print(f"\n── Judge Bias: {jb.get('note', 'N/A')}")

    print(f"\n── Fleet Health (fraction of agents with 0 thorns) ──────")
    for fh in metrics["fleet_health"]:
        bar = "█" * int(fh["health_score"] * 20)
        print(f"  ep{fh['episode']}: {fh['health_score']:.1%}  {bar}")

    print(f"\nResults written to: {RESULTS_DIR}/")


# ─── Multi-seed runner ────────────────────────────────────────────────────────

def run_multi_seed(seeds: List[int], n_agents: int, n_episodes: int, use_llm_judge: bool):
    """Run eval across multiple seeds, aggregate, compute variance."""
    all_metrics = []
    for s in seeds:
        rows = run_eval(s, n_agents, n_episodes, use_llm_judge)
        m    = compute_metrics(rows)
        write_results(rows, m, s)
        print_summary(m, s)
        all_metrics.append(m)

    if len(seeds) > 1:
        _print_aggregate(all_metrics, seeds)


def _print_aggregate(all_metrics: List[Dict], seeds: List[int]):
    print(f"\n{'='*60}")
    print(f"AGGREGATE  seeds={seeds}")
    print(f"{'='*60}")
    # Accumulation curve: mean ± std across seeds per episode
    ep_composites = defaultdict(list)
    for m in all_metrics:
        for row in m["accumulation_curve"]:
            ep_composites[row["episode"]].append(row["mean_composite"])

    print("\n── Composite Score mean ± std across seeds ───────────────")
    for ep in sorted(ep_composites.keys()):
        vals = ep_composites[ep]
        mean = sum(vals) / len(vals)
        import statistics
        std  = statistics.stdev(vals) if len(vals) > 1 else 0.0
        print(f"  ep{ep}: {mean:.4f} ± {std:.4f}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VIGIL Fleet Eval")
    parser.add_argument("--seed",         type=int,   default=42)
    parser.add_argument("--seeds",        type=int,   nargs="+", help="Run multiple seeds")
    parser.add_argument("--episodes",     type=int,   default=7)
    parser.add_argument("--agents",       type=int,   default=20)
    parser.add_argument("--no-llm-judge", action="store_true")
    args = parser.parse_args()

    seeds = args.seeds if args.seeds else [args.seed]
    use_llm = not args.no_llm_judge

    run_multi_seed(seeds, args.agents, args.episodes, use_llm)


if __name__ == "__main__":
    main()