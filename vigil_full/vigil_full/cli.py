"""
vigil_swe_eval/cli.py
======================
Command-line interface for the VIGIL SWE-bench evaluation suite.

Commands:
    vigil-swe swe      Run VIGIL on SWE-bench instances
    vigil-swe ablation Run ablation study (Full VIGIL vs No Decay vs Counter)
    vigil-swe baseline Run baseline comparison (VIGIL vs RuleMon vs Threshold)
    vigil-swe all      Run everything

Usage:
    vigil-swe swe --n-instances 50 --model gpt-5.5 --vigil-root ./robin_b_project_scaffold
    vigil-swe ablation --eval-artifacts ./eval_artifacts --seeds 1 2 3 4 5
    vigil-swe baseline --eval-artifacts ./eval_artifacts
    vigil-swe all --eval-artifacts ./eval_artifacts --n-instances 50
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_swe(args):
    from vigil_swe_eval.swe_runner import run_evaluation, LITE_INSTANCES

    instance_ids = args.instances if args.instances else None
    results = run_evaluation(
        n_instances=args.n_instances,
        model=args.model,
        vigil_project_root=Path(args.vigil_root),
        output_dir=Path(args.output_dir),
        instance_ids=instance_ids,
        skip_rerun=args.skip_rerun,
    )
    print(f"\nDone. {len(results)} instances evaluated.")


def cmd_ablation(args):
    from vigil_swe_eval.ablation import run_ablation

    seeds = list(args.seeds) if args.seeds else list(range(1, 6))
    run_ablation(
        eval_artifacts_dir=args.eval_artifacts,
        seeds=seeds,
        output_path=args.output,
    )


def cmd_baseline(args):
    from vigil_swe_eval.baselines import run_baseline_comparison

    run_baseline_comparison(
        eval_artifacts_dir=args.eval_artifacts,
        output_path=args.output,
    )


def cmd_all(args):
    print("="*60)
    print("Step 1: Ablation study")
    print("="*60)
    from vigil_swe_eval.ablation import run_ablation
    run_ablation(
        eval_artifacts_dir=args.eval_artifacts,
        seeds=list(range(1, 6)),
        output_path="ablation_results.json",
    )

    print("\n" + "="*60)
    print("Step 2: Baseline comparison")
    print("="*60)
    from vigil_swe_eval.baselines import run_baseline_comparison
    run_baseline_comparison(
        eval_artifacts_dir=args.eval_artifacts,
        output_path="baseline_comparison.json",
    )

    if args.vigil_root and args.n_instances > 0:
        print("\n" + "="*60)
        print("Step 3: SWE-bench real evaluation")
        print("="*60)
        from vigil_swe_eval.swe_runner import run_evaluation
        run_evaluation(
            n_instances=args.n_instances,
            model=args.model,
            vigil_project_root=Path(args.vigil_root),
            output_dir=Path(args.output_dir),
        )


def main():
    parser = argparse.ArgumentParser(
        prog="vigil-swe",
        description="VIGIL SWE-bench Evaluation Suite",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── swe ──────────────────────────────────────────────────────────────────
    p_swe = sub.add_parser("swe", help="Run VIGIL on SWE-bench instances")
    p_swe.add_argument("--n-instances", type=int, default=50)
    p_swe.add_argument("--model", default="gpt-5.5")
    p_swe.add_argument("--vigil-root", required=True,
                       help="Path to robin_b_project_scaffold/")
    p_swe.add_argument("--output-dir", default="swe_results/")
    p_swe.add_argument("--instances", nargs="*",
                       help="Specific instance IDs (overrides --n-instances)")
    p_swe.add_argument("--skip-rerun", action="store_true",
                       help="Skip patched re-run (faster, no improvement metric)")
    p_swe.set_defaults(func=cmd_swe)

    # ── ablation ─────────────────────────────────────────────────────────────
    p_abl = sub.add_parser("ablation", help="Run ablation study")
    p_abl.add_argument("--eval-artifacts", required=True,
                       help="Path to eval_artifacts/ dir with Robin_X/ subdirs")
    p_abl.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 6)))
    p_abl.add_argument("--output", default="ablation_results.json")
    p_abl.set_defaults(func=cmd_ablation)

    # ── baseline ─────────────────────────────────────────────────────────────
    p_base = sub.add_parser("baseline", help="Compare VIGIL against baselines")
    p_base.add_argument("--eval-artifacts", required=True,
                        help="Path to eval_artifacts/ dir with Robin_X/ subdirs")
    p_base.add_argument("--output", default="baseline_comparison.json")
    p_base.set_defaults(func=cmd_baseline)

    # ── all ───────────────────────────────────────────────────────────────────
    p_all = sub.add_parser("all", help="Run ablation + baseline + SWE-bench")
    p_all.add_argument("--eval-artifacts", required=True)
    p_all.add_argument("--n-instances", type=int, default=0,
                       help="SWE-bench instances (0 = skip SWE-bench)")
    p_all.add_argument("--model", default="gpt-5.5")
    p_all.add_argument("--vigil-root", default=None)
    p_all.add_argument("--output-dir", default="swe_results/")
    p_all.set_defaults(func=cmd_all)


    # ── swe-from-logs ────────────────────────────────────────────────────────
    p_logs = sub.add_parser('swe-from-logs', help='Run VIGIL on pre-downloaded log files')
    p_logs.add_argument('--logs-dir', required=True, help='Dir with .jsonl VIGIL event files')
    p_logs.add_argument('--vigil-root', required=True, help='Path to robin_b_project_scaffold/')
    p_logs.add_argument('--output-dir', default='swe_vigil_results/')
    p_logs.add_argument('--n', type=int, default=9999, help='Max instances to process')
    p_logs.set_defaults(func=cmd_swe_from_logs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()


def cmd_swe_from_logs(args):
    """Run VIGIL pipeline on pre-downloaded VIGIL JSONL logs."""
    import json, glob, subprocess, sys, tempfile, os
    from pathlib import Path

    logs_dir    = Path(args.logs_dir)
    vigil_root  = Path(args.vigil_root)
    output_dir  = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log_files = sorted(logs_dir.glob("*.jsonl"))
    if not log_files:
        print(f"No .jsonl files found in {logs_dir}")
        return

    print(f"\nRunning VIGIL on {len(log_files)} log files from {logs_dir}")

    from vigil_swe_eval.swe_runner import _run_vigil, SWE_SYSTEM_PROMPT
    results = []

    for i, log_path in enumerate(log_files[:args.n]):
        instance_id = log_path.stem
        print(f"\n[{i+1}/{min(len(log_files), args.n)}] {instance_id}")

        inst_dir = output_dir / instance_id
        inst_dir.mkdir(exist_ok=True)

        prompt_path = inst_dir / "agent_prompt.txt"
        prompt_path.write_text(SWE_SYSTEM_PROMPT, encoding="utf-8")

        vigil_result = _run_vigil(
            logs_path=log_path.resolve(),
            prompt_path=prompt_path.resolve(),
            repo_root=vigil_root.resolve(),
            project_root=vigil_root.resolve().parent,  # ← parent, not vigil_root itself
            out_dir=inst_dir / "vigil_output",
        )
        result = {
            "instance_id": instance_id,
            "vigil_thorn_count":   vigil_result.get("thorn_count", 0),
            "vigil_emo_rows":      vigil_result.get("emo_rows", 0),
            "vigil_diff_produced": vigil_result.get("diff_produced", False),
            "error": vigil_result.get("error", ""),
        }
        results.append(result)

        print(f"  Thorns={result['vigil_thorn_count']} "
              f"EmoRows={result['vigil_emo_rows']} "
              f"Diff={'yes' if result['vigil_diff_produced'] else 'no'}")

    with open(output_dir / "vigil_results.json", "w") as f:
        json.dump(results, f, indent=2)

    diffs = sum(r["vigil_diff_produced"] for r in results)
    thorns = sum(r["vigil_thorn_count"] for r in results)
    print(f"\nDone. {len(results)} instances processed.")
    print(f"Diffs produced: {diffs}/{len(results)} ({diffs/max(len(results),1):.1%})")
    print(f"Total thorns:   {thorns} (avg {thorns/max(len(results),1):.1f}/instance)")
    print(f"Results: {output_dir}/vigil_results.json")
