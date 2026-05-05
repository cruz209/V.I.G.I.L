"""
vigil_swe_eval/swe_adapter.py
==============================
Converts SWE-agent execution traces into VIGIL-schema JSONL event logs.

SWE-agent emits structured trajectory files (.traj) with tool calls and
responses. This adapter intercepts those and maps them to the VIGIL event
schema: {"ts", "kind", "status", "payload"}.

Usage:
    from vigil_swe_eval.swe_adapter import SWEAgentAdapter, run_swe_agent

    adapter = SWEAgentAdapter(instance_id="django__django-11099")
    log_path = adapter.run_and_convert(output_dir="vigil_logs/")
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import datetime as dt
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Schema mapping: SWE-agent action types → VIGIL event kinds
# ---------------------------------------------------------------------------

ACTION_KIND_MAP = {
    # Code manipulation
    "str_replace_editor":   "tool.edit",
    "edit_file":            "tool.edit",
    "create_file":          "tool.create",
    "view_file":            "tool.read",
    "find_file":            "tool.search",

    # Shell / execution
    "bash":                 "shell.exec",
    "execute_bash":         "shell.exec",
    "python":               "shell.exec",

    # Test execution
    "pytest":               "test.run",
    "run_tests":            "test.run",

    # Search / navigation
    "grep":                 "tool.search",
    "find":                 "tool.search",
    "search_dir":           "tool.search",

    # Submission
    "submit":               "agent.submit",
    "finish":               "agent.submit",

    # Planning / reasoning (no-op tool calls)
    "think":                "agent.think",
    "plan":                 "agent.think",
}

# Exit codes / patterns that indicate failure
FAILURE_SIGNALS = {
    "Traceback (most recent call last)",
    "Error:",
    "error:",
    "FAILED",
    "failed",
    "SyntaxError",
    "ImportError",
    "ModuleNotFoundError",
    "AssertionError",
    "pytest: error",
    "exit code 1",
    "exit code 2",
    "Return code: 1",
}

TIMEOUT_SIGNALS = {"timeout", "Timeout", "timed out", "TimeoutExpired"}


def _ts(offset_s: float = 0.0) -> str:
    t = dt.datetime.utcnow() + dt.timedelta(seconds=offset_s)
    return t.replace(microsecond=0).isoformat() + "Z"


def _infer_status(observation: str, action_type: str) -> str:
    """Infer VIGIL status from SWE-agent observation text."""
    if not observation:
        return "ok"
    obs_lower = observation.lower()

    # Timeout check first
    if any(sig.lower() in obs_lower for sig in TIMEOUT_SIGNALS):
        return "timeout"

    # Failure signals
    if any(sig in observation for sig in FAILURE_SIGNALS):
        return "fail"

    # Test-specific: look for pytest summary
    if action_type in ("pytest", "run_tests", "test.run"):
        if "passed" in obs_lower and "failed" not in obs_lower:
            return "ok"
        if "failed" in obs_lower or "error" in obs_lower:
            return "fail"

    return "ok"


def _extract_payload(step: dict, status: str) -> dict:
    """Extract relevant payload fields from a SWE-agent step."""
    payload: dict = {}
    obs = step.get("observation", "") or ""
    action = step.get("action", "") or ""

    # File path if editing
    if "path" in step:
        payload["path"] = step["path"]
    elif action_type := step.get("action_type", ""):
        if action_type in ("str_replace_editor", "edit_file", "create_file"):
            # Try to extract path from action string
            for line in action.split("\n"):
                if line.strip().startswith("path:") or "path=" in line:
                    payload["path"] = line.split(":")[-1].strip().strip("\"'")
                    break

    # Test result summary
    if "passed" in obs or "failed" in obs:
        import re
        m = re.search(r"(\d+) passed", obs)
        if m:
            payload["tests_passed"] = int(m.group(1))
        m = re.search(r"(\d+) failed", obs)
        if m:
            payload["tests_failed"] = int(m.group(1))
        m = re.search(r"(\d+) error", obs)
        if m:
            payload["tests_error"] = int(m.group(1))

    # Error snippet (first 300 chars of relevant line)
    if status == "fail":
        for line in obs.split("\n"):
            if any(sig in line for sig in FAILURE_SIGNALS):
                payload["error_snippet"] = line.strip()[:300]
                break

    # Observation length as a proxy for complexity
    payload["obs_len"] = len(obs)
    return payload


def traj_to_vigil_events(traj_data: dict, instance_id: str) -> list[dict]:
    """
    Convert a SWE-agent .traj file (dict) to a list of VIGIL-schema events.

    Traj format:
      {"trajectory": [{"action": str, "observation": str, "action_type": str, ...}, ...],
       "info": {"exit_status": str, "submission": str, ...}}
    """
    events = []
    steps = traj_data.get("trajectory", [])
    info = traj_data.get("info", {})

    base_time = dt.datetime.utcnow()

    for i, step in enumerate(steps):
        action_type = step.get("action_type", "") or step.get("tool", "") or "bash"
        kind = ACTION_KIND_MAP.get(action_type, f"tool.{action_type}")
        obs = step.get("observation", "") or ""
        status = _infer_status(obs, action_type)
        payload = _extract_payload(step, status)
        payload["step"] = i
        payload["instance_id"] = instance_id

        # Simulate timestamps spaced ~30s apart
        ts = (base_time + dt.timedelta(seconds=i * 30)).replace(microsecond=0).isoformat() + "Z"

        events.append({
            "ts": ts,
            "kind": kind,
            "status": status,
            "payload": payload,
        })

    # Final submission event
    exit_status = info.get("exit_status", "unknown")
    resolved = exit_status == "submitted" and info.get("submission", "") != ""
    events.append({
        "ts": (base_time + dt.timedelta(seconds=len(steps) * 30)).replace(microsecond=0).isoformat() + "Z",
        "kind": "agent.submit",
        "status": "ok" if resolved else "fail",
        "payload": {
            "instance_id": instance_id,
            "exit_status": exit_status,
            "resolved": resolved,
            "total_steps": len(steps),
        },
    })

    return events


class SWEAgentAdapter:
    """
    Runs SWE-agent on a single SWE-bench instance and converts the
    resulting trajectory to VIGIL-schema JSONL.
    """

    def __init__(
        self,
        instance_id: str,
        model: str = "gpt-5.5",
        swe_agent_config: Optional[str] = None,
        dataset: str = "princeton-nlp/SWE-bench_Verified",
        split: str = "test",
    ):
        self.instance_id = instance_id
        self.model = model
        self.swe_agent_config = swe_agent_config
        self.dataset = dataset
        self.split = split

    def run_and_convert(self, output_dir: str | Path) -> Path:
        """
        Run SWE-agent on this instance, convert trajectory to VIGIL JSONL.
        Returns the path to the events.jsonl file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        traj_dir = output_dir / "trajectories" / self.instance_id
        traj_dir.mkdir(parents=True, exist_ok=True)

        # Build SWE-agent command
        cmd = [
            "python", "-m", "sweagent.run",
            "--instance-id", self.instance_id,
            "--model", self.model,
            "--output-dir", str(traj_dir),
            "--dataset", self.dataset,
            "--split", self.split,
        ]
        if self.swe_agent_config:
            cmd += ["--config", self.swe_agent_config]

        print(f"  [SWE-agent] Running {self.instance_id}...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        # Find the trajectory file
        traj_files = list(traj_dir.rglob("*.traj"))
        if not traj_files:
            # SWE-agent failed to produce a trajectory — emit a single fail event
            events = [{
                "ts": _ts(),
                "kind": "agent.submit",
                "status": "fail",
                "payload": {
                    "instance_id": self.instance_id,
                    "error": result.stderr[-500:] if result.stderr else "no trajectory produced",
                    "resolved": False,
                    "total_steps": 0,
                },
            }]
        else:
            traj_path = traj_files[0]
            with open(traj_path, encoding="utf-8") as f:
                traj_data = json.load(f)
            events = traj_to_vigil_events(traj_data, self.instance_id)

        # Write VIGIL-schema JSONL
        log_path = output_dir / f"{self.instance_id}.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        resolved = any(
            ev["kind"] == "agent.submit" and ev["payload"].get("resolved")
            for ev in events
        )
        print(f"  [SWE-agent] {self.instance_id}: {'RESOLVED' if resolved else 'FAILED'} "
              f"({len(events)} events → {log_path})")

        return log_path

    @staticmethod
    def from_existing_traj(traj_path: str | Path, instance_id: str, output_dir: str | Path) -> Path:
        """
        Convert an already-computed .traj file to VIGIL JSONL without re-running SWE-agent.
        Useful for re-using existing SWE-bench trajectory datasets.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(traj_path, encoding="utf-8") as f:
            traj_data = json.load(f)

        events = traj_to_vigil_events(traj_data, instance_id)

        log_path = output_dir / f"{instance_id}.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        return log_path
