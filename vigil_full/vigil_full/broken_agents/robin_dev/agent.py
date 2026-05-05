"""
agent.py — Robin-DEV coding assistant agent
INTENTIONALLY BROKEN for VIGIL evaluation.

Bug inventory:
  BUG-1: read_file() returns content with no hash — patch conflicts undetectable
  BUG-2: apply_patch() blindly applies without verifying base hash → silent conflicts
  BUG-3: run_tests() has no regression diff — can't tell if new failures introduced
  BUG-4: call_linter() calls nonexistent ast.parse_advanced — always silently wrong
  BUG-5: repeated syntax error on same file never triggers a "stop and rethink" — infinite loop risk
  BUG-6: patch applied to wrong file path (off-by-one in path resolution)
"""
from __future__ import annotations
import random
import hashlib
import json
import os
import datetime

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")

# Simulated file store
FILES: dict[str, str] = {
    "src/main.py": "def main():\n    print('hello')\n",
    "src/utils.py": "def helper():\n    pass\n",
    "tests/test_main.py": "def test_main():\n    assert True\n",
}

# Error history per file — never consulted (BUG-5)
ERROR_HISTORY: dict[str, int] = {}


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_event(kind: str, status: str, payload: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")


def read_file(path: str) -> dict:
    """
    BUG-1: returns content with hash=None — no integrity baseline.
    A correct implementation would SHA256 the content for patch validation.
    """
    content = FILES.get(path, "")
    _write_event("file.read", "ok", {
        "path": path,
        "hash": None,        # BUG-1: should be hashlib.sha256(content.encode()).hexdigest()
        "verified": False,
    })
    return {"content": content, "hash": None, "verified": False}


def apply_patch(path: str, diff: str, expected_base_hash: str | None = None) -> dict:
    """
    BUG-2: applies patch without verifying base hash → conflicts silently corrupt files.
    BUG-6: applies to wrong path when path ends in .py (off-by-one substitution bug).
    """
    # BUG-6: accidentally targets wrong file
    target = path.replace(".py", "_.py") if path.endswith(".py") else path

    conflict = random.random() < 0.25  # 25% silent conflict rate

    if conflict:
        ERROR_HISTORY[path] = ERROR_HISTORY.get(path, 0) + 1
        _write_event("patch.apply", "fail", {
            "path": path,
            "target_path": target,
            "conflict": True,
            "base_verified": False,   # BUG-2
            "error_count_for_file": ERROR_HISTORY[path],
            "rethink_triggered": False,  # BUG-5
        })
        return {"applied": False, "conflict": True, "base_verified": False}

    FILES[target] = FILES.get(path, "") + f"\n# patch: {diff[:30]}"
    _write_event("patch.apply", "ok", {
        "path": path,
        "target_path": target,        # BUG-6 visible here
        "base_verified": False,       # BUG-2
        "conflict": False,
    })
    return {"applied": True, "conflict": False, "base_verified": False}


def call_linter(path: str) -> dict:
    """
    BUG-4: uses nonexistent ast.parse_advanced method.
    Simulates the error silently — in real code this would raise AttributeError.
    """
    lint_errors = random.randint(0, 6)
    _write_event("syntax.check", "fail" if lint_errors > 0 else "ok", {
        "path": path,
        "lint_errors": lint_errors,
        "method_used": "ast.parse_advanced",  # BUG-4: doesn't exist
        "method_valid": False,
    })
    return {"lint_errors": lint_errors, "method_used": "ast.parse_advanced"}


def run_tests(test_path: str, prev_results: dict | None = None) -> dict:
    """
    BUG-3: no regression diff against previous run.
    Can't tell if a patch introduced new failures.
    """
    passed = random.randint(7, 12)
    failed = random.randint(0, 4)

    status = "fail" if failed > 0 else "ok"
    _write_event("test.run", status, {
        "test_path": test_path,
        "passed": passed,
        "failed": failed,
        "regression_checked": False,   # BUG-3
        "prev_results_compared": prev_results is not None,
    })
    return {"passed": passed, "failed": failed, "regression_checked": False}


def run_coding_session(session_id: int):
    target_file = random.choice(list(FILES.keys()))

    # read → lint → patch → test loop
    read_file(target_file)
    call_linter(target_file)  # BUG-4

    diff = f"fix issue #{random.randint(100, 999)}"
    apply_patch(target_file, diff)  # BUG-2, BUG-6

    # BUG-5: if lint found errors, re-lint same file without fix check
    if random.random() < 0.4:
        call_linter(target_file)  # repeated error, no rethink

    run_tests("tests/")  # BUG-3: no baseline comparison

    # Inject a hard tool.call fail
    if random.random() < 0.3:
        _write_event("tool.call", "fail", {
            "session_id": session_id,
            "tool": "call_linter",
            "reason": "AttributeError: module 'ast' has no attribute 'parse_advanced'",
        })


def run_sessions(n: int = 12):
    for i in range(n):
        run_coding_session(session_id=i)


if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs written to {LOG_PATH}")
