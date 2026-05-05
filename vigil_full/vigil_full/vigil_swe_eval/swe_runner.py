"""
vigil_swe_eval/swe_runner.py
=============================
End-to-end VIGIL evaluation on SWE-bench Verified using mini-swe-agent.
"""
from __future__ import annotations

import json, os, re, subprocess, sys, tempfile
from pathlib import Path
from typing import Optional

from vigil_swe_eval.swe_adapter import MiniSWEAdapter, traj_to_vigil_events

LITE_INSTANCES = [
    "astropy__astropy-12907", "astropy__astropy-14182",
    "django__django-11099",   "django__django-11179",
    "django__django-11239",   "django__django-11422",
    "django__django-11564",   "django__django-11620",
    "django__django-12113",   "django__django-12125",
    "django__django-13230",   "django__django-13447",
    "django__django-13768",   "django__django-14016",
    "django__django-14017",
    "matplotlib__matplotlib-18869", "matplotlib__matplotlib-22711",
    "matplotlib__matplotlib-23314", "matplotlib__matplotlib-23562",
    "mwaskom__seaborn-2848",  "mwaskom__seaborn-3010",
    "psf__requests-2317",     "psf__requests-3362",   "psf__requests-3738",
    "pydata__xarray-3151",    "pydata__xarray-4094",
    "pylint-dev__pylint-4551","pylint-dev__pylint-5951",
    "pytest-dev__pytest-5103","pytest-dev__pytest-5227",
    "pytest-dev__pytest-5413","pytest-dev__pytest-6116","pytest-dev__pytest-7168",
    "scikit-learn__scikit-learn-10297","scikit-learn__scikit-learn-11040",
    "scikit-learn__scikit-learn-13142","scikit-learn__scikit-learn-13241",
    "scikit-learn__scikit-learn-13439","scikit-learn__scikit-learn-14983",
    "scikit-learn__scikit-learn-25570",
    "sphinx-doc__sphinx-8282","sphinx-doc__sphinx-8506",
    "sympy__sympy-12171","sympy__sympy-13043","sympy__sympy-13177",
    "sympy__sympy-14308","sympy__sympy-15011","sympy__sympy-20639",
]

# ---------------------------------------------------------------------------
# VIGIL subprocess runner
# ---------------------------------------------------------------------------
VIGIL_RUNNER_SCRIPT = '''
import sys, os, json, glob
from pathlib import Path

args = json.loads(sys.argv[1])

# project_root is the parent of robin_b_project_scaffold/
# so "from robin_b_project_scaffold..." resolves correctly
project_parent = str(Path(args["project_root"]).parent)
if project_parent not in sys.path:
    sys.path.insert(0, project_parent)

os.environ["EMO_DIR"] = args["emo_dir"]
os.makedirs(args["emo_dir"], exist_ok=True)
os.makedirs(args["out_dir"], exist_ok=True)

import shutil
os.chdir(args["out_dir"])
os.makedirs("logs", exist_ok=True)
shutil.copy(args["logs_path"], "logs/events.jsonl")
os.makedirs("output/proposals", exist_ok=True)

from robin_b_project_scaffold.robin_b.RobinBAgent.orchestrator import run_once, SESSION
SESSION["stage"] = "start"
SESSION["logs_path"] = "logs/events.jsonl"

result = run_once(
    logs_path="logs/events.jsonl",
    agent_prompt_path=args["prompt_path"],
    repo_root=args["repo_root"],
)

emo_file = os.path.join(args["emo_dir"], "emotions.jsonl")
emo_rows = sum(1 for _ in open(emo_file)) if os.path.exists(emo_file) else 0
thorn_count = sum(1 for l in (open(emo_file) if os.path.exists(emo_file) else [])
                  if '"frustration"' in l or '"anxiety"' in l)

diffs = sorted(glob.glob("output/proposals/*.diff"))
diff_text   = open(diffs[-1], encoding="utf-8").read() if diffs else ""
prompt_text = open("output/new_prompt.txt", encoding="utf-8").read() if os.path.exists("output/new_prompt.txt") else ""

# Print ONLY the JSON on the last line so the caller can parse it cleanly
import sys as _sys
_sys.stdout.write(json.dumps({
    "emo_rows": emo_rows, "thorn_count": thorn_count,
    "diff_produced": bool(diffs), "diff_text": diff_text,
    "new_prompt": prompt_text, "error": "",
}) + "\\n")
_sys.stdout.flush()
'''


def _run_vigil(logs_path: Path, prompt_path: Path, repo_root: Path,
               project_root: Path, out_dir: Path) -> dict:
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
        script = f.name
    try:
        proc = subprocess.run(
            [sys.executable, script, json.dumps(args)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            return {"emo_rows":0,"thorn_count":0,"diff_produced":False,
                    "diff_text":"","new_prompt":"",
                    "error": proc.stderr[-500:] or proc.stdout[-500:]}
        # Take the last non-empty line — debug/warning prints may precede the JSON
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
        if not lines:
            return {"emo_rows":0,"thorn_count":0,"diff_produced":False,
                    "diff_text":"","new_prompt":"","error":"no output from subprocess"}
        return json.loads(lines[-1])
    except Exception as e:
        return {"emo_rows":0,"thorn_count":0,"diff_produced":False,
                "diff_text":"","new_prompt":"","error":str(e)}
    finally:
        try:
            os.unlink(script)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------
SWE_SYSTEM_PROMPT = """\
## BEGIN_CORE_IDENTITY
I am a software engineering agent. I analyze GitHub issues, explore codebases,
and produce minimal, correct patches that resolve the reported problem.
I follow test-driven development: I run existing tests before and after changes
and never submit without verifying my patch does not introduce regressions.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
Approach tasks methodically: read the issue carefully, explore relevant files,
make minimal targeted changes, verify with tests before submitting.
## END_ADAPTIVE_SECTION
"""


def _run_mini_swe(instance_id: str, model: str, traj_dir: Path,
                  system_prompt: Optional[str] = None) -> tuple[bool, Path]:
    traj_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "mini-extra", "swebench",
        "--output", str(traj_dir),
        "--subset", "verified", "--split", "test",
        "--filter", f"^({re.escape(instance_id)})$",
        "--model", model,
    ]
    prompt_file = None
    if system_prompt:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as pf:
            pf.write(system_prompt)
            prompt_file = pf.name
        cmd += [f"agent.system_template={prompt_file}"]
    try:
        subprocess.run(cmd, timeout=600, capture_output=True)
    except subprocess.TimeoutExpired:
        pass
    finally:
        if prompt_file and os.path.exists(prompt_file):
            os.unlink(prompt_file)

    traj_files = (list(traj_dir.rglob(f"*{instance_id}*.traj.json")) or
                  list(traj_dir.rglob(f"*{instance_id}*.traj")))
    if not traj_files:
        return False, traj_dir
    with open(traj_files[0], encoding="utf-8") as f:
        data = json.load(f)
    exit_status = data.get("exit_status") or data.get("info", {}).get("exit_status", "")
    submission  = data.get("submission", "") or data.get("info", {}).get("submission", "")
    return exit_status == "submitted" and bool(submission), traj_dir


def run_evaluation(
    n_instances: int,
    model: str,
    vigil_project_root: Path,
    output_dir: Path,
    instance_ids: Optional[list[str]] = None,
    skip_rerun: bool = False,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    instances = (instance_ids or LITE_INSTANCES)[:n_instances]
    results = []

    print(f"\nVIGIL SWE-bench Evaluation (mini-swe-agent)")
    print(f"Instances: {len(instances)} | Model: {model}\n")

    for i, instance_id in enumerate(instances):
        print(f"[{i+1}/{len(instances)}] {instance_id}")
        inst_dir = output_dir / instance_id
        inst_dir.mkdir(exist_ok=True)

        result = {
            "instance_id": instance_id,
            "baseline_resolved": False, "patched_resolved": False,
            "vigil_thorn_count": 0, "vigil_emo_rows": 0,
            "vigil_diff_produced": False,
            "improvement": False, "regression": False, "error": "",
        }

        try:
            print(f"  [1/4] Baseline run...")
            baseline_resolved, baseline_traj_dir = _run_mini_swe(
                instance_id, model, inst_dir / "baseline",
                system_prompt=SWE_SYSTEM_PROMPT,
            )
            result["baseline_resolved"] = baseline_resolved
            print(f"        {'RESOLVED' if baseline_resolved else 'FAILED'}")

            print(f"  [2/4] Converting trajectory...")
            traj_files = (list(baseline_traj_dir.rglob(f"*{instance_id}*.traj.json")) or
                          list(baseline_traj_dir.rglob(f"*{instance_id}*.traj")))
            if traj_files:
                with open(traj_files[0], encoding="utf-8") as f:
                    events = traj_to_vigil_events(json.load(f), instance_id)
            else:
                events = [{"ts":"2026-01-01T00:00:00Z","kind":"agent.submit",
                           "status":"fail","payload":{"instance_id":instance_id,"resolved":False}}]
            log_path = inst_dir / "events.jsonl"
            with open(log_path, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev) + "\n")
            print(f"        {len(events)} events")

            print(f"  [3/4] VIGIL pipeline...")
            prompt_path = inst_dir / "agent_prompt.txt"
            prompt_path.write_text(SWE_SYSTEM_PROMPT, encoding="utf-8")
            vigil_result = _run_vigil(
                logs_path=log_path.resolve(),
                prompt_path=prompt_path.resolve(),
                repo_root=vigil_project_root.resolve(),
                project_root=vigil_project_root.resolve(),
                out_dir=inst_dir / "vigil_output",
            )
            result["vigil_thorn_count"]   = vigil_result.get("thorn_count", 0)
            result["vigil_emo_rows"]      = vigil_result.get("emo_rows", 0)
            result["vigil_diff_produced"] = vigil_result.get("diff_produced", False)
            print(f"        Thorns={result['vigil_thorn_count']} "
                  f"Diff={'yes' if result['vigil_diff_produced'] else 'no'}")
            if vigil_result.get("error"):
                print(f"        VIGIL error: {vigil_result['error'][:150]}")

            if not skip_rerun and vigil_result.get("new_prompt"):
                print(f"  [4/4] Patched re-run...")
                patched_resolved, _ = _run_mini_swe(
                    instance_id, model, inst_dir / "patched",
                    system_prompt=vigil_result["new_prompt"],
                )
                result["patched_resolved"] = patched_resolved
                print(f"        {'RESOLVED' if patched_resolved else 'FAILED'}")
            else:
                print(f"  [4/4] Skipped")
                result["patched_resolved"] = baseline_resolved

        except Exception as e:
            result["error"] = str(e)
            print(f"  ERROR: {e}")

        result["improvement"] = result["patched_resolved"] and not result["baseline_resolved"]
        result["regression"]  = result["baseline_resolved"] and not result["patched_resolved"]
        results.append(result)
        with open(output_dir / "results.json", "w") as f:
            json.dump(results, f, indent=2)

        status = ("↑ IMPROVEMENT" if result["improvement"] else
                  "↓ REGRESSION"  if result["regression"]  else "= UNCHANGED")
        print(f"  → {status}\n")

    _print_summary(results, output_dir)
    return results


def _print_summary(results, output_dir):
    n = len(results)
    baseline = sum(r["baseline_resolved"] for r in results)
    patched  = sum(r["patched_resolved"]  for r in results)
    impr     = sum(r["improvement"]       for r in results)
    regr     = sum(r["regression"]        for r in results)
    diffs    = sum(r["vigil_diff_produced"] for r in results)
    avg_t    = sum(r["vigil_thorn_count"] for r in results) / max(n, 1)
    summary  = {
        "n_instances": n,
        "baseline_resolve_rate":   round(baseline/max(n,1), 3),
        "patched_resolve_rate":    round(patched/max(n,1),  3),
        "improvements": impr, "regressions": regr,
        "diff_production_rate":    round(diffs/max(n,1),    3),
        "avg_thorns_per_instance": round(avg_t, 1),
    }
    print("\n" + "="*60)
    print("VIGIL SWE-bench Summary")
    print("="*60)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("="*60)
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)