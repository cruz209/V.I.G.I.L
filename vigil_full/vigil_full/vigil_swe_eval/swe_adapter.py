"""
vigil_swe_eval/swe_adapter.py
==============================
Converts mini-swe-agent .traj.json files into VIGIL-schema JSONL event logs.

mini-swe-agent trajectory format (v2):
  Linear messages list — the trajectory IS the messages array.
  Each assistant turn has a ```bash ... ``` block.
  Each following user turn has {"returncode": N, "output": "..."}.
  Top-level keys: exit_status, submission, messages.

Install:
    pip install mini-swe-agent

Run SWE-bench:
    mini-extra swebench \\
        --output swe_out/ --subset verified --split test \\
        --filter '^(django__django-11099)$'

Usage:
    from vigil_swe_eval.swe_adapter import MiniSWEAdapter, traj_to_vigil_events
    log_path = MiniSWEAdapter.from_existing_traj("run.traj.json", "django__django-11099", "logs/")
"""
from __future__ import annotations
import datetime as dt, json, os, re, subprocess
from pathlib import Path
from typing import Optional

BASH_BLOCK_RE  = re.compile(r"```(?:bash|sh|shell)?\s*\n(.*?)```", re.DOTALL)
OBS_JSON_RE    = re.compile(r'\{\s*"returncode"\s*:\s*(-?\d+)', re.DOTALL)
TEST_PASS_RE   = re.compile(r"(\d+) passed")
TEST_FAIL_RE   = re.compile(r"(\d+) failed")
TEST_ERROR_RE  = re.compile(r"(\d+) error")

FAILURE_SIGNALS = [
    "Traceback (most recent call last)", "SyntaxError", "ImportError",
    "ModuleNotFoundError", "AssertionError", "NameError", "TypeError",
    "AttributeError", "FAILED", "pytest: error", "error: ", "Error: ",
]
TIMEOUT_SIGNALS = ["timeout", "timed out", "TimeoutExpired"]


def _ts(base: dt.datetime, offset_s: float) -> str:
    return (base + dt.timedelta(seconds=offset_s)).replace(microsecond=0).isoformat() + "Z"

def _now() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def _kind(command: str) -> str:
    cmd = command.strip()
    if re.match(r"pytest|python -m pytest", cmd): return "test.run"
    if re.match(r"grep|find |rg ", cmd):          return "tool.search"
    if re.match(r"cat |head |tail |nl ", cmd):     return "tool.read"
    if re.match(r"sed |awk ", cmd) and "-i" in cmd: return "tool.edit"
    if re.match(r"git ", cmd):                     return "tool.git"
    if "submit" in cmd:                            return "agent.submit"
    return "shell.exec"

def _status(returncode: int, output: str, kind: str) -> str:
    if any(s.lower() in output.lower() for s in TIMEOUT_SIGNALS): return "timeout"
    if returncode != 0: return "fail"
    if any(s in output for s in FAILURE_SIGNALS): return "fail"
    if kind == "test.run" and (TEST_FAIL_RE.search(output) or TEST_ERROR_RE.search(output)): return "fail"
    return "ok"

def _payload(command: str, output: str, returncode: int, status: str, step: int, iid: str) -> dict:
    p: dict = {"step": step, "instance_id": iid, "returncode": returncode, "obs_len": len(output)}
    m = re.search(r'[\w./\-]+\.py', command)
    if m: p["path"] = m.group(0)
    for pat, key in [(TEST_PASS_RE, "tests_passed"), (TEST_FAIL_RE, "tests_failed"), (TEST_ERROR_RE, "tests_error")]:
        mm = pat.search(output)
        if mm: p[key] = int(mm.group(1))
    if status == "fail":
        for line in output.split("\n"):
            if any(s in line for s in FAILURE_SIGNALS):
                p["error_snippet"] = line.strip()[:300]; break
    p["command_preview"] = command[:200]
    return p


def traj_to_vigil_events(traj_data: dict, instance_id: str) -> list[dict]:
    """
    Convert mini-swe-agent .traj.json → VIGIL JSONL events.

    Traj structure:
      messages: [{"role":"system","content":"..."}, {"role":"user","content":"<issue>"},
                 {"role":"assistant","content":"...```bash\ncmd\n```"},
                 {"role":"user","content":'{"returncode":0,"output":"..."}'},
                 ...]
      exit_status: "submitted" | "limits_exceeded" | "format_error" | ...
      submission: "git diff ..."
    """
    messages    = traj_data.get("messages", [])
    exit_status = traj_data.get("exit_status") or traj_data.get("info", {}).get("exit_status", "unknown")
    submission  = traj_data.get("submission", "") or traj_data.get("info", {}).get("submission", "")
    resolved    = exit_status == "submitted" and bool(submission)

    events = []
    base   = dt.datetime.utcnow()
    step   = 0
    i      = 0

    while i < len(messages):
        msg  = messages[i]
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "assistant":
            bash_m  = BASH_BLOCK_RE.search(content)
            command = bash_m.group(1).strip() if bash_m else content.strip()

            obs_content = ""
            returncode  = 0
            if i + 1 < len(messages) and messages[i + 1].get("role") == "user":
                raw = messages[i + 1].get("content", "") or ""
                rc_m = OBS_JSON_RE.search(raw)
                if rc_m:
                    returncode = int(rc_m.group(1))
                    try:
                        obs_content = json.loads(raw).get("output", raw)
                    except Exception:
                        obs_content = raw
                else:
                    obs_content = raw
                i += 1  # consume observation

            kind   = _kind(command)
            status = _status(returncode, obs_content, kind)
            events.append({
                "ts":      _ts(base, step * 30),
                "kind":    kind,
                "status":  status,
                "payload": _payload(command, obs_content, returncode, status, step, instance_id),
            })
            step += 1

        i += 1

    # Final submission event
    events.append({
        "ts":   _ts(base, step * 30),
        "kind": "agent.submit",
        "status": "ok" if resolved else "fail",
        "payload": {
            "instance_id":   instance_id,
            "exit_status":   exit_status,
            "resolved":      resolved,
            "total_steps":   step,
            "submission_len": len(submission),
        },
    })
    return events


class MiniSWEAdapter:
    """Run mini-swe-agent on one SWE-bench instance and convert to VIGIL JSONL."""

    def __init__(self, instance_id: str, model: str = "gpt-5.5", extra_args: Optional[list] = None):
        self.instance_id = instance_id
        self.model       = model
        self.extra_args  = extra_args or []

    def run_and_convert(self, output_dir: str | Path) -> Path:
        output_dir = Path(output_dir)
        traj_dir   = output_dir / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "mini-extra", "swebench",
            "--output", str(traj_dir),
            "--subset", "verified", "--split", "test",
            "--filter", f"^({re.escape(self.instance_id)})$",
        ]
        if self.model:
            cmd += ["--model", self.model]
        cmd += self.extra_args

        print(f"  [mini-swe-agent] Running {self.instance_id}...")
        try:
            subprocess.run(cmd, timeout=600, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            print(f"  [mini-swe-agent] Timeout on {self.instance_id}")

        traj_files = (list(traj_dir.rglob(f"*{self.instance_id}*.traj.json")) or
                      list(traj_dir.rglob(f"*{self.instance_id}*.traj")))

        if not traj_files:
            events = [{"ts": _now(), "kind": "agent.submit", "status": "fail",
                       "payload": {"instance_id": self.instance_id, "resolved": False,
                                   "error": "no trajectory produced", "total_steps": 0}}]
        else:
            with open(traj_files[0], encoding="utf-8") as f:
                events = traj_to_vigil_events(json.load(f), self.instance_id)

        log_path = output_dir / f"{self.instance_id}.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        resolved = any(ev["kind"] == "agent.submit" and ev["payload"].get("resolved") for ev in events)
        print(f"  [mini-swe-agent] {self.instance_id}: {'RESOLVED' if resolved else 'FAILED'} ({len(events)} events)")
        return log_path

    @staticmethod
    def from_existing_traj(traj_path: str | Path, instance_id: str, output_dir: str | Path) -> Path:
        """Convert an already-computed .traj.json without re-running."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(traj_path, encoding="utf-8") as f:
            events = traj_to_vigil_events(json.load(f), instance_id)
        log_path = output_dir / f"{instance_id}.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return log_path

# Backward-compat alias
SWEAgentAdapter = MiniSWEAdapter