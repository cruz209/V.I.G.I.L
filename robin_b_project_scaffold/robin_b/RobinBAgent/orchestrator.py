# robin_b/RobinBAgent/orchestrator.py
from __future__ import annotations
import os, json, re, datetime as dt
from typing import List, Dict, Any, Optional
from agents import Agent, function_tool, Runner



# ---- shared state (simple in-memory stage machine) ----
SESSION = {"stage": "start"}  # start -> eb_updated -> diagnosed -> prompt_done -> diff_done


def _ts() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _ok(expected: str):
    if SESSION["stage"] != expected:
        raise ValueError(f"Wrong stage: {SESSION['stage']} (expected {expected})")


# =====================================================================
# 1) PROCESS LOGS → UPDATE EMOBANK
# =====================================================================
@function_tool
def update_eb_from_logs(logs_path: str, window_hours: int = 24) -> dict:
    """
    Read JSONL logs, appraise events, update the contextual EmoBank with policy,
    and return a short reflection summary (incl. 'cue').
    """
    from robin_b_project_scaffold.robin_b.runtime.b_reflect import run_reflection

    rec = run_reflection(window_hours=window_hours, logs_path=logs_path)
    SESSION["stage"] = "eb_updated"
    return rec  # {"summary","diagnosis","cue",...}


# =====================================================================
# 2) RBT DIAGNOSIS
# =====================================================================
@function_tool
def diagnose_rbt(logs_path: str = "logs/events.jsonl", recent_n: int = 200) -> dict:
    """
    Derive Roses/Buds/Thorns from EmoBank + recent events; returns rbt dict with rules.
    """
    _ok("eb_updated")
    from robin_b_project_scaffold.robin_b.b_core.emobank import recall_recent
    from robin_b_project_scaffold.robin_b.runtime.b_reflect import _fetch_recent_events
    from robin_b_project_scaffold.robin_b.runtime.b_diagnose import roses_buds_thorns

    emos = recall_recent(n=recent_n)
    events = _fetch_recent_events(logs_path="logs/events.jsonl", hours=24)

    rbt = roses_buds_thorns(emos, events)
    SESSION["stage"] = "diagnosed"
    return rbt



# =====================================================================
# 3) GENERATE PROMPT PATCH (NOT APPLY)
# =====================================================================
@function_tool
def build_prompt_patch(agent_prompt_path: str, cue: str, rbt_json: str) -> dict:
    """
    Compose a new ADAPTIVE section using the cue + RBT. Writes output/new_prompt.txt.
    Returns the path and the new block text. Does NOT modify the source file.
    """
    _ok("diagnosed")
    from robin_b_project_scaffold.robin_b.runtime.b_prompt import generate_new_prompt

    with open(agent_prompt_path, "r", encoding="utf-8") as f:
        cur = f.read()
    rbt = json.loads(rbt_json)
    new_prompt, block = generate_new_prompt(
        cur,
        cue=cue,
        guardrails=True,
        rbt_rules=rbt.get("prompt_rules_to_add"),
        rbt=rbt,
    )

    os.makedirs("output", exist_ok=True)
    out_path = "output/new_prompt.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(new_prompt)

    SESSION["stage"] = "prompt_done"
    return {"path": out_path, "block": block}


# =====================================================================
# 4) WRITE UNIFIED DIFF (LLM-AUTHORED)
# =====================================================================
DIFF_RE = re.compile(r"(?ms)^---\s+.+?\n\+\+\+\s+.+?\n.+")


@function_tool
def emit_unified_diff(diff: str) -> str:
    """
    Return ONLY a valid unified diff (no prose). The agent must call this to produce the code suggestion.
    """
    _ok("prompt_done")
    if not DIFF_RE.search(diff or ""):
        raise ValueError("Invalid diff: expected '---' and '+++' headers.")
    SESSION["stage"] = "diff_done"
    return diff


# =====================================================================
# 5) SAVE ARTIFACTS
# =====================================================================
@function_tool
def save_proposal(diff: str, evidence_json: str = "{}") -> dict:
    """
    Save the unified diff + PR note to output/proposals/. Returns file paths.
    """
    _ok("diff_done")
    ts = _ts()
    os.makedirs("output/proposals", exist_ok=True)

    diff_path = f"output/proposals/LLM_patch_{ts}.diff"
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(diff)

    ev = json.loads(evidence_json)
    pr_path = f"output/proposals/LLM_PR_{ts}.md"
    with open(pr_path, "w", encoding="utf-8") as f:
        f.write(
            f"# LLM Code Suggestion\nGenerated: {ts}\n\n"
            f"Evidence:\n```json\n{json.dumps(ev, indent=2)}\n```\n\n"
            f"Apply:\n  git apply {diff_path}\nRollback:\n  git apply -R {diff_path}\n"
        )

    return {"diff_path": diff_path, "pr_path": pr_path}


# =====================================================================
# Agent definition & run
# =====================================================================
INSTRUCTIONS = """
You are Robin B, a reflective maintainer. Call the tools IN ORDER:
1) update_eb_from_logs(logs_path)  → returns {"cue": ...}
2) diagnose_rbt()                  → returns RBT dict
3) build_prompt_patch(agent_prompt_path, cue, rbt_json)
4) emit_unified_diff(diff)         → YOU must author the diff in the tool call
5) save_proposal(diff, evidence_json)

Rules:
- Do NOT modify core identity in prompts; only the ADAPTIVE section.
- For code, propose minimal fixes as a unified diff (UTC, receipt gating, bounded retry, etc.).
- Use diagnose_rbt().evidence to justify changes.
- Produce ONE diff per run focused on the top 'thorn'.
"""


def run_once(logs_path: str, agent_prompt_path: str, repo_root: str) -> dict:
    agent = Agent(
        name="RobinB-Orchestrator",
        instructions=INSTRUCTIONS,
        tools=[
            update_eb_from_logs,
            diagnose_rbt,
            build_prompt_patch,
            emit_unified_diff,
            save_proposal,
        ],
        model=os.getenv("LLM_MODEL", "gpt-5"),  # defaults to GPT-5
    )

    msg = {
        "role": "user",
        "content": (
            f"logs_path: {logs_path}\n"
            f"agent_prompt_path: {agent_prompt_path}\n"
            f"repo_root: {repo_root}\n"
            f"Goal: process logs, diagnose RBT, build prompt block, "
            f"propose ONE code diff, save artifacts."
        ),
    }

    result = Runner.run_sync(agent, [msg])


    return {"text": result.final_output}

