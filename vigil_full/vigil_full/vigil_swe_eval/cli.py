"""
vigil_swe_eval/cli.py
======================
Command-line interface for the VIGIL SWE-bench evaluation suite.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def cmd_swe(args):
    from vigil_swe_eval.swe_runner import run_evaluation
    results = run_evaluation(
        n_instances=args.n_instances,
        model=args.model,
        vigil_project_root=Path(args.vigil_root),
        output_dir=Path(args.output_dir),
        instance_ids=args.instances if args.instances else None,
        skip_rerun=args.skip_rerun,
    )
    print(f"\nDone. {len(results)} instances evaluated.")


def cmd_ablation(args):
    from vigil_swe_eval.ablation import run_ablation
    run_ablation(
        eval_artifacts_dir=args.eval_artifacts,
        seeds=list(args.seeds) if args.seeds else list(range(1, 6)),
        output_path=args.output,
    )


def cmd_baseline(args):
    from vigil_swe_eval.baselines import run_baseline_comparison
    run_baseline_comparison(
        eval_artifacts_dir=args.eval_artifacts,
        output_path=args.output,
    )


def cmd_all(args):
    from vigil_swe_eval.ablation import run_ablation
    from vigil_swe_eval.baselines import run_baseline_comparison

    print("="*60 + "\nStep 1: Ablation study\n" + "="*60)
    run_ablation(eval_artifacts_dir=args.eval_artifacts, seeds=list(range(1,6)), output_path="ablation_results.json")

    print("\n" + "="*60 + "\nStep 2: Baseline comparison\n" + "="*60)
    run_baseline_comparison(eval_artifacts_dir=args.eval_artifacts, output_path="baseline_comparison.json")

    if args.vigil_root and args.n_instances > 0:
        from vigil_swe_eval.swe_runner import run_evaluation
        print("\n" + "="*60 + "\nStep 3: SWE-bench\n" + "="*60)
        run_evaluation(
            n_instances=args.n_instances, model=args.model,
            vigil_project_root=Path(args.vigil_root), output_dir=Path(args.output_dir),
        )


def cmd_swe_from_logs(args):
    """Run VIGIL pipeline on pre-downloaded VIGIL JSONL logs."""
    import json, os
    from pathlib import Path
    from vigil_swe_eval.swe_runner import _run_vigil, SWE_SYSTEM_PROMPT

    logs_dir   = Path(args.logs_dir)
    vigil_root = Path(args.vigil_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log_files = sorted(logs_dir.glob("*.jsonl"))
    if not log_files:
        print(f"No .jsonl files found in {logs_dir}")
        return

    print(f"\nRunning VIGIL on {len(log_files)} log files from {logs_dir}")
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
            project_root=vigil_root.resolve(),
            out_dir=inst_dir / "vigil_output",
        )

        result = {
            "instance_id":        instance_id,
            "vigil_thorn_count":  vigil_result.get("thorn_count", 0),
            "vigil_emo_rows":     vigil_result.get("emo_rows", 0),
            "vigil_diff_produced":vigil_result.get("diff_produced", False),
            "error":              vigil_result.get("error", ""),
        }
        results.append(result)
        print(f"  Thorns={result['vigil_thorn_count']} "
              f"EmoRows={result['vigil_emo_rows']} "
              f"Diff={'yes' if result['vigil_diff_produced'] else 'no'}")

    with open(output_dir / "vigil_results.json", "w") as f:
        json.dump(results, f, indent=2)

    diffs  = sum(r["vigil_diff_produced"] for r in results)
    thorns = sum(r["vigil_thorn_count"] for r in results)
    print(f"\nDone. {len(results)} instances processed.")
    print(f"Diffs produced: {diffs}/{len(results)} ({diffs/max(len(results),1):.1%})")
    print(f"Total thorns:   {thorns} (avg {thorns/max(len(results),1):.1f}/instance)")
    print(f"Results: {output_dir}/vigil_results.json")


def main():
    parser = argparse.ArgumentParser(prog="vigil-swe", description="VIGIL SWE-bench Evaluation Suite")
    sub = parser.add_subparsers(dest="command", required=True)

    p_swe = sub.add_parser("swe", help="Run VIGIL on SWE-bench instances")
    p_swe.add_argument("--n-instances", type=int, default=50)
    p_swe.add_argument("--model", default="gpt-5.5")
    p_swe.add_argument("--vigil-root", required=True)
    p_swe.add_argument("--output-dir", default="swe_results/")
    p_swe.add_argument("--instances", nargs="*")
    p_swe.add_argument("--skip-rerun", action="store_true")
    p_swe.set_defaults(func=cmd_swe)

    p_abl = sub.add_parser("ablation", help="Run ablation study")
    p_abl.add_argument("--eval-artifacts", required=True)
    p_abl.add_argument("--seeds", type=int, nargs="+", default=list(range(1, 6)))
    p_abl.add_argument("--output", default="ablation_results.json")
    p_abl.set_defaults(func=cmd_ablation)

    p_base = sub.add_parser("baseline", help="Compare VIGIL against baselines")
    p_base.add_argument("--eval-artifacts", required=True)
    p_base.add_argument("--output", default="baseline_comparison.json")
    p_base.set_defaults(func=cmd_baseline)

    p_all = sub.add_parser("all", help="Run ablation + baseline + SWE-bench")
    p_all.add_argument("--eval-artifacts", required=True)
    p_all.add_argument("--n-instances", type=int, default=0)
    p_all.add_argument("--model", default="gpt-5.5")
    p_all.add_argument("--vigil-root", default=None)
    p_all.add_argument("--output-dir", default="swe_results/")
    p_all.set_defaults(func=cmd_all)

    p_logs = sub.add_parser("swe-from-logs", help="Run VIGIL on pre-downloaded log files")
    p_logs.add_argument("--logs-dir", required=True)
    p_logs.add_argument("--vigil-root", required=True)
    p_logs.add_argument("--output-dir", default="swe_vigil_results/")
    p_logs.add_argument("--n", type=int, default=9999)
    p_logs.set_defaults(func=cmd_swe_from_logs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()