"""
download_trajs.py
=================
Downloads pre-computed SWE-bench trajectories from HuggingFace and converts
them to VIGIL-schema JSONL events — no Docker, no Linux required.

Priority order:
  1. SWE-bench/SWE-smith-trajectories  — 5,017 real agent trajectories with
                                          actual bash commands + observations
  2. nebius/SWE-agent-trajectories      — 80,036 trajectories fallback
  3. princeton-nlp/SWE-bench_Verified   — metadata only (synthetic fallback)

Usage:
    python download_trajs.py --n 50 --output vigil_swe_logs/
    python download_trajs.py --n 50 --output vigil_swe_logs/ --split lite
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

FAILURE_SIGNALS = [
    "Traceback (most recent call last)", "SyntaxError", "ImportError",
    "ModuleNotFoundError", "AssertionError", "FAILED", "pytest: error",
    "Error:", "error:", "returned non-zero",
]
TIMEOUT_SIGNALS = ["timeout", "timed out", "TimeoutExpired"]
TEST_PASS_RE = re.compile(r"(\d+) passed")
TEST_FAIL_RE = re.compile(r"(\d+) failed")
TEST_ERROR_RE = re.compile(r"(\d+) error")
BASH_BLOCK_RE = re.compile(r"```(?:bash|sh|shell)?\s*\n(.*?)```", re.DOTALL)


def _ts(base: dt.datetime, offset_s: float) -> str:
    return (base + dt.timedelta(seconds=offset_s)).replace(microsecond=0).isoformat() + "Z"


def _kind(command: str) -> str:
    cmd = command.strip()
    if re.match(r"pytest|python -m pytest", cmd): return "test.run"
    if re.match(r"grep|find |rg ", cmd):           return "tool.search"
    if re.match(r"cat |head |tail |nl ", cmd):      return "tool.read"
    if re.match(r"sed |awk ", cmd) and "-i" in cmd: return "tool.edit"
    if re.match(r"git ", cmd):                      return "tool.git"
    if "submit" in cmd:                             return "agent.submit"
    return "shell.exec"


def _status(returncode: int, output: str, kind: str) -> str:
    if any(s.lower() in output.lower() for s in TIMEOUT_SIGNALS): return "timeout"
    if returncode != 0: return "fail"
    if any(s in output for s in FAILURE_SIGNALS): return "fail"
    if kind == "test.run" and (TEST_FAIL_RE.search(output) or TEST_ERROR_RE.search(output)):
        return "fail"
    return "ok"


def messages_to_vigil_events(messages: list[dict], instance_id: str,
                              exit_status: str, resolved: bool) -> list[dict]:
    events = []
    base = dt.datetime.utcnow()
    step = 0
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if role == "assistant":
            bash_m = BASH_BLOCK_RE.search(content)
            command = bash_m.group(1).strip() if bash_m else content.strip()
            obs_content = ""
            returncode = 0
            if i + 1 < len(messages) and messages[i + 1].get("role") == "user":
                raw = messages[i + 1].get("content", "") or ""
                try:
                    parsed = json.loads(raw)
                    returncode = parsed.get("returncode", 0)
                    obs_content = parsed.get("output", raw)
                except Exception:
                    obs_content = raw
                    rc_m = re.search(r'"returncode"\s*:\s*(-?\d+)', raw)
                    if rc_m:
                        returncode = int(rc_m.group(1))
                i += 1
            kind = _kind(command)
            status = _status(returncode, obs_content, kind)
            payload: dict = {
                "step": step, "instance_id": instance_id,
                "returncode": returncode, "obs_len": len(obs_content),
                "command_preview": command[:200],
            }
            pm = re.search(r'[\w./\-]+\.py', command)
            if pm: payload["path"] = pm.group(0)
            for pat, key in [(TEST_PASS_RE,"tests_passed"),(TEST_FAIL_RE,"tests_failed"),(TEST_ERROR_RE,"tests_error")]:
                mm = pat.search(obs_content)
                if mm: payload[key] = int(mm.group(1))
            if status == "fail":
                for line in obs_content.split("\n"):
                    if any(s in line for s in FAILURE_SIGNALS):
                        payload["error_snippet"] = line.strip()[:300]; break
            events.append({"ts": _ts(base, step*30), "kind": kind, "status": status, "payload": payload})
            step += 1
        i += 1
    events.append({
        "ts": _ts(base, step*30), "kind": "agent.submit",
        "status": "ok" if resolved else "fail",
        "payload": {"instance_id": instance_id, "exit_status": exit_status,
                    "resolved": resolved, "total_steps": step},
    })
    return events


def traj_dict_to_vigil_events(traj: dict, instance_id: str) -> list[dict]:
    messages = traj.get("messages", [])
    exit_status = traj.get("exit_status") or traj.get("info", {}).get("exit_status", "unknown")
    submission = traj.get("submission", "") or traj.get("info", {}).get("submission", "")
    resolved = exit_status == "submitted" and bool(submission)

    if messages:
        return messages_to_vigil_events(messages, instance_id, exit_status, resolved)

    # Old SWE-agent format
    steps = traj.get("trajectory", [])
    base = dt.datetime.utcnow()
    events = []
    for i, step in enumerate(steps):
        obs = step.get("observation", "") or ""
        action_type = step.get("action_type", "bash")
        kind = {"str_replace_editor":"tool.edit","edit_file":"tool.edit",
                "bash":"shell.exec","pytest":"test.run","submit":"agent.submit"}.get(action_type, "shell.exec")
        status = _status(1 if any(s in obs for s in FAILURE_SIGNALS) else 0, obs, kind)
        events.append({"ts": _ts(base, i*30), "kind": kind, "status": status,
                       "payload": {"step": i, "instance_id": instance_id, "obs_len": len(obs)}})
    events.append({"ts": _ts(base, len(steps)*30), "kind": "agent.submit",
                   "status": "ok" if resolved else "fail",
                   "payload": {"instance_id": instance_id, "resolved": resolved,
                               "exit_status": exit_status, "total_steps": len(steps)}})
    return events


def _make_synthetic(instance: dict, instance_id: str) -> dict:
    """Last resort: synthesize events from problem statement + patch metadata."""
    problem = instance.get("problem_statement", "")
    patch = instance.get("patch", "") or instance.get("solution", "")
    resolved = bool(patch)
    return {
        "messages": [
            {"role": "system",    "content": "You are a coding agent."},
            {"role": "user",      "content": problem[:500]},
            {"role": "assistant", "content": "```bash\nfind . -name '*.py' | head -20\n```"},
            {"role": "user",      "content": '{"returncode": 0, "output": "found files"}'},
            {"role": "assistant", "content": "```bash\npython -m pytest tests/ -x\n```"},
            {"role": "user",      "content": '{"returncode": 1, "output": "FAILED tests/test_models.py - AssertionError"}'},
            {"role": "assistant", "content": "```bash\nsed -i 's/old/new/' models.py\n```"},
            {"role": "user",      "content": '{"returncode": 0, "output": ""}'},
            {"role": "assistant", "content": "```bash\npython -m pytest tests/ -x\n```"},
            {"role": "user",      "content": f'{{"returncode": {"0" if resolved else "1"}, "output": "{"1 passed" if resolved else "1 failed"}"}}'} ,
        ],
        "exit_status": "submitted" if resolved else "failed",
        "submission": patch,
    }


def download_and_convert(n: int, output_dir: Path, split: str = "verified") -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from datasets import load_dataset
    except ImportError:
        print("Installing datasets...")
        os.system(f"{sys.executable} -m pip install datasets -q")
        from datasets import load_dataset

    print("Loading SWE-bench trajectory data from HuggingFace...")
    dataset = None
    source = "unknown"

    # Priority 1: SWE-smith-trajectories — real agent traces with bash commands
    try:
        dataset = load_dataset("SWE-bench/SWE-smith-trajectories", split="train")
        print(f"  ✓ Loaded SWE-smith-trajectories: {len(dataset)} real agent trajectories")
        source = "SWE-smith-trajectories"
    except Exception as e:
        print(f"  SWE-smith-trajectories unavailable: {e}")

    # Priority 2: nebius large trajectory dataset
    if dataset is None:
        try:
            dataset = load_dataset("nebius/SWE-agent-trajectories", split="train")
            print(f"  ✓ Loaded nebius/SWE-agent-trajectories: {len(dataset)} trajectories")
            source = "nebius-swe-agent-trajectories"
        except Exception as e:
            print(f"  nebius trajectories unavailable: {e}")

    # Priority 3: metadata only — synthetic fallback
    if dataset is None:
        try:
            ds_name = "princeton-nlp/SWE-bench_Verified" if split == "verified" else "princeton-nlp/SWE-bench_Lite"
            dataset = load_dataset(ds_name, split="test")
            print(f"  ✓ Loaded {ds_name} (metadata only — synthetic events will be generated)")
            source = "swebench_metadata_synthetic"
        except Exception as e:
            print(f"  Failed to load any dataset: {e}")
            return []

    # One trajectory per repo — deduplicate by repo so we get n distinct repos
    seen_repos = set()
    instances = []
    for item in dataset:
        iid = item.get("instance_id", "")
        # Extract repo: "django__django" from "django__django-11099"
        parts = iid.split("__")
        repo = parts[0] + "__" + parts[1].rsplit("-", 1)[0] if len(parts) >= 2 else iid
        if repo not in seen_repos:
            seen_repos.add(repo)
            instances.append(item)
        if len(instances) >= n:
            break
    print(f"\nConverting {len(instances)} instances ({source})...\n")

    results = []
    for i, instance in enumerate(instances):
        instance_id = instance.get("instance_id", f"instance_{i:04d}")
        print(f"  [{i+1}/{len(instances)}] {instance_id}", end="  ")

        # Extract trajectory data — field names vary by dataset
        traj_data = {}
        if "messages" in instance and instance["messages"]:
            traj_data["messages"]     = instance["messages"]
            traj_data["exit_status"]  = instance.get("exit_status", "unknown")
            traj_data["submission"]   = instance.get("submission", "")
        elif "trajectory" in instance and instance["trajectory"]:
            traj_data["trajectory"] = instance["trajectory"]
            traj_data["info"]       = {
                "exit_status": instance.get("exit_status", "unknown"),
                "submission":  instance.get("patch", ""),
            }
        elif "traj" in instance and instance["traj"]:
            # Some datasets use "traj" key
            inner = instance["traj"]
            if isinstance(inner, str):
                try: inner = json.loads(inner)
                except Exception: inner = {}
            traj_data = inner
        else:
            traj_data = _make_synthetic(instance, instance_id)

        events = traj_dict_to_vigil_events(traj_data, instance_id)
        resolved = any(ev["kind"] == "agent.submit" and ev["payload"].get("resolved") for ev in events)
        n_fails  = sum(1 for ev in events if ev["status"] in ("fail","error","timeout"))

        log_path = output_dir / f"{instance_id}.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        print(f"{len(events)} events, {n_fails} failures, resolved={resolved}")
        results.append({
            "instance_id": instance_id,
            "log_path":    str(log_path),
            "n_events":    len(events),
            "n_failures":  n_fails,
            "resolved":    resolved,
            "source":      source,
        })

    with open(output_dir / "manifest.json", "w") as f:
        json.dump(results, f, indent=2)

    n_resolved = sum(r["resolved"] for r in results)
    print(f"\nDone. {len(results)} instances → {output_dir}")
    print(f"Source: {source}")
    print(f"Resolved: {n_resolved}/{len(results)} ({n_resolved/max(len(results),1):.1%})")
    if source == "swebench_metadata_synthetic":
        print("⚠  WARNING: Using synthetic events — no real trajectory data found.")
        print("   For real trajectories, ensure HuggingFace can access SWE-bench/SWE-smith-trajectories")
    return results


def main():
    parser = argparse.ArgumentParser(description="Download SWE-bench trajs → VIGIL events")
    parser.add_argument("--n",      type=int, default=50)
    parser.add_argument("--output", default="vigil_swe_logs/")
    parser.add_argument("--split",  default="verified", choices=["verified","lite"])
    args = parser.parse_args()

    results = download_and_convert(n=args.n, output_dir=Path(args.output), split=args.split)
    if results:
        print(f"\nNext step:")
        print(f"  python -m vigil_swe_eval.cli swe-from-logs --logs-dir {args.output} --vigil-root robin_b_project_scaffold/")


if __name__ == "__main__":
    main()