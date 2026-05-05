"""
vigil_swe_eval/swe_runner.py
=============================
End-to-end evaluation of VIGIL on SWE-bench Verified instances.

Pipeline per instance:
  1. Run SWE-agent (baseline) → collect trajectory + pass/fail
  2. Convert trajectory to VIGIL-schema JSONL
  3. Run VIGIL pipeline on the logs → get diagnosis + diff
  4. Apply VIGIL's prompt patch to the agent system prompt
  5. Re-run SWE-agent with patched prompt → collect new pass/fail
  6. Score: did patching improve resolve rate?

Usage:
    python -m vigil_swe_eval.swe_runner \
        --n-instances 50 \
        --model gpt-5.5 \
        --vigil-root /path/to/robin_b_project_scaffold \
        --output-dir swe_results/

Result schema (swe_results/results.json):
    {
      "instance_id": str,
      "baseline_resolved": bool,
      "patched_resolved": bool,
      "vigil_thorn_count": int,
      "vigil_diff_produced": bool,
      "improvement": bool,    # patched_resolved and not baseline_resolved
      "regression": bool,     # baseline_resolved and not patched_resolved
    }
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from vigil_swe_eval.swe_adapter import SWEAgentAdapter, traj_to_vigil_events


# ---------------------------------------------------------------------------
# Instance selection — uses SWE-bench Verified lite subset
# ---------------------------------------------------------------------------

LITE_INSTANCES = [
    # A curated subset of SWE-bench Verified instances covering diverse repos
    # These are known-resolvable instances that SWE-agent can sometimes solve
    "astropy__astropy-12907",
    "astropy__astropy-14182",
    "django__django-11099",
    "django__django-11179",
    "django__django-11239",
    "django__django-11422",
    "django__django-11564",
    "django__django-11620",
    "django__django-12113",
    "django__django-12125",
    "django__django-13230",
    "django__django-13447",
    "django__django-13768",
    "django__django-14016",
    "django__django-14017",
    "flask__flask-4992",
    "matplotlib__matplotlib-18869",
    "matplotlib__matplotlib-22711",
    "matplotlib__matplotlib-23314",
    "matplotlib__matplotlib-23562",
    "mwaskom__seaborn-2848",
    "mwaskom__seaborn-3010",
    "pallets__flask-4045",
    "psf__requests-2317",
    "psf__requests-3362",
    "psf__requests-3738",
    "pydata__xarray-3151",
    "pydata__xarray-4094",
    "pylint-dev__pylint-4551",
    "pylint-dev__pylint-5951",
    "pytest-dev__pytest-5103",
    "pytest-dev__pytest-5227",
    "pytest-dev__pytest-5413",
    "pytest-dev__pytest-6116",
    "pytest-dev__pytest-7168",
    "scikit-learn__scikit-learn-10297",
    "scikit-learn__scikit-learn-11040",
    "scikit-learn__scikit-learn-13142",
    "scikit-learn__scikit-learn-13241",
    "scikit-learn__scikit-learn-13439",
    "scikit-learn__scikit-learn-14983",
    "scikit-learn__scikit-learn-25570",
    "sphinx-doc__sphinx-8282",
    "sphinx-doc__sphinx-8506",
    "sympy__sympy-12171",
    "sympy__sympy-13043",
    "sympy__sympy-13177",
    "sympy__sympy-14308",
    "sympy__sympy-15011",
    "sympy__sympy-20639",
]


# ---------------------------------------------------------------------------
# VIGIL runner subprocess (mirrors _vigil_runner.py pattern from eval_artifacts)
# ---------------------------------------------------------------------------

VIGIL_RUNNER_SCRIPT = """
import sys, os, json, glob
from pathlib import Path

args = json.loads(sys.argv[1])
logs_path   = args["logs_path"]
prompt_path = args["prompt_path"]
repo_root   = args["repo_root"]
emo_dir     = args["emo_dir"]
out_dir     = args["out_dir"]
project_root = args["project_root"]

os.environ["EMO_DIR"] = emo_dir
os.makedirs(emo_dir, exist_ok=True)
os.makedirs(out_dir, exist_ok=True)
os.makedirs(os.path.join(out_dir, "proposals"), exist_ok=True)

import shutil
os.chdir(out_dir)
os.makedirs("logs", exist_ok=True)
shutil.copy(logs_path, "logs/events.jsonl")
os.makedirs("output/proposals", exist_ok=True)

sys.path.insert(0, project_root)

from robin_b_project_scaffold.robin_b.RobinBAgent.orchestrator import run_once, SESSION
SESSION["stage"] = "start"
SESSION["logs_path"] = "logs/events.jsonl"

result = run_once(
    logs_path="logs/events.jsonl",
    agent_prompt_path=prompt_path,
    repo_root=repo_root,
)

emo_file = os.path.join(emo_dir, "emotions.jsonl")
emo_rows = sum(1 for _ in open(emo_file)) if os.path.exists(emo_file) else 0
thorn_count = sum(
    1 for line in (open(emo_file) if os.path.exists(emo_file) else [])
    if '"frustration"' in line or '"anxiety"' in line
)

diffs = sorted(glob.glob("output/proposals/*.diff"))
diff_text = open(diffs[-1], encoding="utf-8").read() if diffs else ""
prompt_text = open("output/new_prompt.txt", encoding="utf-8").read() if os.path.exists("output/new_prompt.txt") else ""

print(json.dumps({
    "emo_rows": emo_rows,
    "thorn_count": thorn_count,
    "diff_produced": bool(diffs),
    "diff_text": diff_text,
    "new_prompt": prompt_text,
    "error": "",
}))
"""


def run_vigil_on_logs(
    logs_path: Path,
    prompt_path: Path,
    repo_root: Path,
    project_root: Path,
    out_dir: Path,
) -> dict:
    """Run VIGIL pipeline in a subprocess, return results dict."""
    emo_dir = out_dir / "db" / "emobank"
    emo_dir.mkdir(parents=True, exist_ok=True)

    args = {
        "logs_path":    str(logs_path.resolve()),
        "prompt_path":  str(prompt_path.resolve()),
        "repo_root":    str(repo_root.resolve()),
        "emo_dir":      str(emo_dir.resolve()),
        "out_dir":      str(out_dir.resolve()),
        "project_root": str(project_root.resolve()),
    }

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(VIGIL_RUNNER_SCRIPT)
        script_path = f.name

    try:
        proc = subprocess.run(
            [sys.executable, script_path, json.dumps(args)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            return {"emo_rows": 0, "thorn_count": 0, "diff_produced": False,
                    "diff_text": "", "new_prompt": "", "error": proc.stderr[-500:]}
        return json.loads(proc.stdout)
    except Exception as e:
        return {"emo_rows": 0, "thorn_count": 0, "diff_produced": False,
                "diff_text": "", "new_prompt": "", "error": str(e)}
    finally:
        os.unlink(script_path)


def check_resolved(traj_dir: Path, instance_id: str) -> bool:
    """Parse SWE-agent trajectory to determine if instance was resolved."""
    traj_files = list(traj_dir.rglob("*.traj"))
    if not traj_files:
        return False
    with open(traj_files[0], encoding="utf-8") as f:
        data = json.load(f)
    info = data.get("info", {})
    return (info.get("exit_status") == "submitted" and
            bool(info.get("submission", "")))


def run_swe_agent(
    instance_id: str,
    model: str,
    output_dir: Path,
    system_prompt: Optional[str] = None,
    run_label: str = "baseline",
) -> tuple[bool, Path]:
    """
    Run SWE-agent on one instance. Returns (resolved, traj_dir).
    If system_prompt is provided, writes it to a temp file and passes it.
    """
    traj_dir = output_dir / run_label / instance_id
    traj_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", "-m", "sweagent.run",
        "--instance-id", instance_id,
        "--model", model,
        "--output-dir", str(traj_dir),
        "--dataset", "princeton-nlp/SWE-bench_Verified",
        "--split", "test",
    ]

    prompt_file = None
    if system_prompt:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as pf:
            pf.write(system_prompt)
            prompt_file = pf.name
        cmd += ["--system-template", prompt_file]

    try:
        subprocess.run(cmd, timeout=600, capture_output=True)
    except subprocess.TimeoutExpired:
        pass
    finally:
        if prompt_file and os.path.exists(prompt_file):
            os.unlink(prompt_file)

    resolved = check_resolved(traj_dir, instance_id)
    return resolved, traj_dir


# ---------------------------------------------------------------------------
# SWE-bench VIGIL Prompt template
# ---------------------------------------------------------------------------

SWE_AGENT_BASE_PROMPT = """## BEGIN_CORE_IDENTITY
I am a software engineering agent. I analyze GitHub issues, explore codebases,
and produce minimal, correct patches that resolve the reported problem.
I follow test-driven development: I run existing tests before and after changes,
and I never submit without verifying my patch does not introduce regressions.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
Approach tasks methodically: read the issue carefully, explore relevant files,
make minimal targeted changes, verify with tests before submitting.
## END_ADAPTIVE_SECTION
"""


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def run_evaluation(
    n_instances: int,
    model: str,
    vigil_project_root: Path,
    output_dir: Path,
    instance_ids: Optional[list[str]] = None,
    skip_rerun: bool = False,
) -> list[dict]:
    """
    Full evaluation pipeline across N SWE-bench instances.
    Returns list of per-instance result dicts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    instances = (instance_ids or LITE_INSTANCES)[:n_instances]
    results = []

    print(f"\nVIGIL SWE-bench Evaluation")
    print(f"Instances: {len(instances)} | Model: {model}")
    print(f"Output: {output_dir}\n")

    for i, instance_id in enumerate(instances):
        print(f"[{i+1}/{len(instances)}] {instance_id}")
        inst_dir = output_dir / instance_id
        inst_dir.mkdir(exist_ok=True)

        result = {
            "instance_id": instance_id,
            "baseline_resolved": False,
            "patched_resolved": False,
            "vigil_thorn_count": 0,
            "vigil_emo_rows": 0,
            "vigil_diff_produced": False,
            "improvement": False,
            "regression": False,
            "error": "",
        }

        try:
            # ── Step 1: Baseline SWE-agent run ──────────────────────────────
            print(f"  [1/4] Baseline run...")
            baseline_resolved, baseline_traj_dir = run_swe_agent(
                instance_id, model, inst_dir,
                system_prompt=SWE_AGENT_BASE_PROMPT,
                run_label="baseline",
            )
            result["baseline_resolved"] = baseline_resolved
            print(f"        Baseline: {'RESOLVED' if baseline_resolved else 'FAILED'}")

            # ── Step 2: Convert trajectory to VIGIL JSONL ───────────────────
            print(f"  [2/4] Converting trajectory to VIGIL events...")
            traj_files = list(baseline_traj_dir.rglob("*.traj"))
            if traj_files:
                with open(traj_files[0], encoding="utf-8") as f:
                    traj_data = json.load(f)
                events = traj_to_vigil_events(traj_data, instance_id)
            else:
                events = [{
                    "ts": "2026-01-01T00:00:00Z",
                    "kind": "agent.submit",
                    "status": "fail",
                    "payload": {"instance_id": instance_id, "resolved": False},
                }]

            log_path = inst_dir / "events.jsonl"
            with open(log_path, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev) + "\n")
            print(f"        {len(events)} events written")

            # ── Step 3: Run VIGIL ────────────────────────────────────────────
            print(f"  [3/4] Running VIGIL pipeline...")
            prompt_path = inst_dir / "agent_prompt.txt"
            prompt_path.write_text(SWE_AGENT_BASE_PROMPT, encoding="utf-8")

            vigil_out_dir = inst_dir / "vigil_output"
            vigil_result = run_vigil_on_logs(
                logs_path=log_path,
                prompt_path=prompt_path,
                repo_root=vigil_project_root,
                project_root=vigil_project_root,
                out_dir=vigil_out_dir,
            )

            result["vigil_thorn_count"]  = vigil_result.get("thorn_count", 0)
            result["vigil_emo_rows"]     = vigil_result.get("emo_rows", 0)
            result["vigil_diff_produced"] = vigil_result.get("diff_produced", False)
            print(f"        Thorns={result['vigil_thorn_count']} "
                  f"EmoRows={result['vigil_emo_rows']} "
                  f"Diff={'yes' if result['vigil_diff_produced'] else 'no'}")

            # ── Step 4: Patched re-run (if VIGIL produced a new prompt) ─────
            if not skip_rerun and vigil_result.get("new_prompt"):
                print(f"  [4/4] Patched re-run...")
                patched_resolved, _ = run_swe_agent(
                    instance_id, model, inst_dir,
                    system_prompt=vigil_result["new_prompt"],
                    run_label="patched",
                )
                result["patched_resolved"] = patched_resolved
                print(f"        Patched: {'RESOLVED' if patched_resolved else 'FAILED'}")
            else:
                print(f"  [4/4] Skipping patched re-run (no new prompt or --skip-rerun)")
                result["patched_resolved"] = baseline_resolved

        except Exception as e:
            result["error"] = str(e)
            print(f"  ERROR: {e}")

        result["improvement"] = result["patched_resolved"] and not result["baseline_resolved"]
        result["regression"]  = result["baseline_resolved"] and not result["patched_resolved"]

        results.append(result)

        # Save incremental results
        results_path = output_dir / "results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        status = "↑ IMPROVEMENT" if result["improvement"] else \
                 "↓ REGRESSION" if result["regression"] else \
                 "= UNCHANGED"
        print(f"  → {status}\n")

    # ── Summary ──────────────────────────────────────────────────────────────
    _print_summary(results, output_dir)
    return results


def _print_summary(results: list[dict], output_dir: Path):
    n = len(results)
    baseline_res  = sum(r["baseline_resolved"] for r in results)
    patched_res   = sum(r["patched_resolved"] for r in results)
    improvements  = sum(r["improvement"] for r in results)
    regressions   = sum(r["regression"] for r in results)
    diffs_prod    = sum(r["vigil_diff_produced"] for r in results)
    avg_thorns    = sum(r["vigil_thorn_count"] for r in results) / max(n, 1)

    summary = {
        "n_instances": n,
        "baseline_resolve_rate": round(baseline_res / max(n, 1), 3),
        "patched_resolve_rate":  round(patched_res  / max(n, 1), 3),
        "improvements": improvements,
        "regressions":  regressions,
        "diff_production_rate": round(diffs_prod / max(n, 1), 3),
        "avg_thorns_per_instance": round(avg_thorns, 1),
    }

    print("\n" + "="*60)
    print("VIGIL SWE-bench Evaluation Summary")
    print("="*60)
    print(f"  Instances evaluated:     {n}")
    print(f"  Baseline resolve rate:   {summary['baseline_resolve_rate']:.1%}")
    print(f"  Patched resolve rate:    {summary['patched_resolve_rate']:.1%}")
    print(f"  Net improvements:        {improvements}")
    print(f"  Net regressions:         {regressions}")
    print(f"  Diff production rate:    {summary['diff_production_rate']:.1%}")
    print(f"  Avg thorns/instance:     {summary['avg_thorns_per_instance']:.1f}")
    print("="*60)

    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nFull results: {output_dir / 'results.json'}")
    print(f"Summary:      {summary_path}")
