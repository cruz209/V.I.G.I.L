# robin_b/RobinBAgent/orchestrator.py
from __future__ import annotations
import asyncio, os, json, re, datetime as dt
from agents import Agent, function_tool, Runner

SESSION = {"stage": "start", "logs_path": None}

def _ts() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

def _ok(expected: str):
    if SESSION["stage"] != expected:
        raise ValueError(f"Wrong stage: {SESSION['stage']} (expected {expected})")

@function_tool
def update_eb_from_logs(logs_path: str, window_hours: int = 24) -> dict:
    """Read JSONL logs, appraise events, update EmoBank, return reflection summary."""
    from robin_b_project_scaffold.robin_b.runtime.b_reflect import run_reflection
    authoritative_path = SESSION.get("logs_path") or logs_path
    rec = run_reflection(window_hours=window_hours, logs_path=authoritative_path)
    SESSION["stage"] = "eb_updated"
    return rec

@function_tool
def diagnose_rbt(recent_n: int = 200) -> dict:
    """Derive Roses/Buds/Thorns from EmoBank + recent events."""
    _ok("eb_updated")
    from robin_b_project_scaffold.robin_b.b_core.emobank import recall_recent
    from robin_b_project_scaffold.robin_b.runtime.b_reflect import _fetch_recent_events
    from robin_b_project_scaffold.robin_b.runtime.b_diagnose import roses_buds_thorns
    logs_path = SESSION["logs_path"]
    emos = recall_recent(n=recent_n)
    events = _fetch_recent_events(logs_path=logs_path, hours=24)
    rbt = roses_buds_thorns(emos, events)
    SESSION["stage"] = "diagnosed"
    return rbt

@function_tool
def build_prompt_patch(agent_prompt_path: str, cue: str, rbt_json: str) -> dict:
    """Compose a new ADAPTIVE section. Writes output/new_prompt.txt."""
    _ok("diagnosed")
    from robin_b_project_scaffold.robin_b.runtime.b_prompt import generate_new_prompt
    with open(agent_prompt_path, "r", encoding="utf-8") as f:
        cur = f.read()
    rbt = rbt_json if isinstance(rbt_json, dict) else json.loads(rbt_json) if rbt_json else {}
    # Only enforce guardrails if identity block exists — plain Python files won't have it
    has_core = "## BEGIN_CORE_IDENTITY" in cur and "## END_CORE_IDENTITY" in cur
    new_prompt, block = generate_new_prompt(
        cur, cue=cue, guardrails=has_core,
        rbt_rules=rbt.get("prompt_rules_to_add"), rbt=rbt,
    )
    os.makedirs("output", exist_ok=True)
    out_path = "output/new_prompt.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(new_prompt)
    SESSION["stage"] = "prompt_done"
    return {"path": out_path, "block": block}

DIFF_RE = re.compile(r"(?ms)^---\s+.+?\n\+\+\+\s+.+?\n.+")

@function_tool
def emit_unified_diff(diff: str) -> str:
    """Return ONLY a valid unified diff."""
    _ok("prompt_done")
    if not DIFF_RE.search(diff or ""):
        raise ValueError("Invalid diff: expected '---' and '+++' headers.")
    SESSION["stage"] = "diff_done"
    return diff

@function_tool
def save_proposal(diff: str, evidence_json: str = "{}") -> dict:
    """Save the unified diff + PR note to output/proposals/."""
    _ok("diff_done")
    ts = _ts()
    os.makedirs("output/proposals", exist_ok=True)
    diff_path = f"output/proposals/LLM_patch_{ts}.diff"
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(diff)
    ev = json.loads(evidence_json) if isinstance(evidence_json, str) else evidence_json
    pr_path = f"output/proposals/LLM_PR_{ts}.md"
    with open(pr_path, "w", encoding="utf-8") as f:
        f.write(
            f"# LLM Code Suggestion\nGenerated: {ts}\n\n"
            f"Evidence:\n```json\n{json.dumps(ev, indent=2)}\n```\n\n"
            f"Apply:\n  git apply {diff_path}\nRollback:\n  git apply -R {diff_path}\n"
        )
    return {"diff_path": diff_path, "pr_path": pr_path}

INSTRUCTIONS = """
You are Robin B, a reflective maintainer. You MUST call ALL 5 tools in order. Do not stop early.

REQUIRED SEQUENCE — complete every step, no exceptions:
1) update_eb_from_logs(logs_path)
2) diagnose_rbt()
3) build_prompt_patch(agent_prompt_path, cue, rbt_json)
   - Pass agent_prompt_path exactly as given in the user message
   - Pass the cue string returned from step 1
   - Pass the ENTIRE output of diagnose_rbt() serialized as a JSON string for rbt_json
4) emit_unified_diff(diff) — write a real unified diff targeting the actual source file
5) save_proposal(diff, evidence_json)

CRITICAL: Stage diagnosed → you MUST call build_prompt_patch next. Do not stop.
CRITICAL: Stage prompt_done → you MUST call emit_unified_diff next. Do not stop.
CRITICAL: You are not finished until save_proposal returns successfully.
CRITICAL: At least one diff artifact must be produced — this is a hard requirement.

If diagnose_rbt() returns empty thorns, still complete steps 3-5 with a preventive hardening diff.
If you are unsure what to diff, add a logging or validation utility to agent.py.
"""

async def _run_once_async(logs_path: str, agent_prompt_path: str, repo_root: str) -> dict:
    SESSION["stage"] = "start"
    SESSION["logs_path"] = logs_path
    agent = Agent(
        name="RobinB-Orchestrator",
        instructions=INSTRUCTIONS,
        tools=[update_eb_from_logs, diagnose_rbt, build_prompt_patch, emit_unified_diff, save_proposal],
        model=os.getenv("LLM_MODEL", "gpt-5.5"),
    )
    msg = {
        "role": "user",
        "content": (
            f"logs_path: {logs_path}\n"
            f"agent_prompt_path: {agent_prompt_path}\n"
            f"repo_root: {repo_root}\n"
            f"Goal: process logs, diagnose RBT, build prompt block, propose ONE code diff, save artifacts."
        ),
    }
    print(f"[DEBUG] Starting pipeline, logs: {logs_path}")
    result = await Runner.run(agent, [msg], max_turns=25)
    print(f"[DEBUG] Pipeline done, final stage: {SESSION['stage']}")
    print(f"[DEBUG] Final output: {result.final_output[:300] if result.final_output else 'None'}")
    return {"text": result.final_output}

def run_once(logs_path: str, agent_prompt_path: str, repo_root: str) -> dict:
    return asyncio.run(_run_once_async(logs_path, agent_prompt_path, repo_root))
