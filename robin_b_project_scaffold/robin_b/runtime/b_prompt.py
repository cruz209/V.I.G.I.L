from __future__ import annotations
import re, json, os
from typing import Tuple, List, Optional, Dict, Any
from .common import now_iso

LOGS_PROMPT = "logs/prompt_updates.jsonl"

ADAPTIVE_RE = re.compile(r"(## BEGIN_ADAPTIVE_SECTION)(.*?)(## END_ADAPTIVE_SECTION)", re.DOTALL)
CORE_RE     = re.compile(r"(## BEGIN_CORE_IDENTITY)(.*?)(## END_CORE_IDENTITY)", re.DOTALL)

def _render_rbt_preamble() -> str:
    """
    A short, agent-readable description of RBT so Robin A knows how to act.
    """
    return (
        "RBT (Roses/Buds/Thorns) operating guide:\n"
        "- Roses = reliable wins to PRESERVE. Keep behaviors exactly; do not regress.\n"
        "- Buds  = promising signals to GROW. Add guardrails/observability until they become Roses.\n"
        "- Thorns= failures or pain points to TRIM. Add mitigations, retries, or clarity.\n"
        "When conflicts occur: TRIM thorns first, then GROW buds, then PRESERVE roses.\n"
        "Always keep CORE_IDENTITY unchanged; modify only the ADAPTIVE section.\n"
    )

def _render_rbt_plan(rbt: Optional[Dict[str, Any]]) -> str:
    """
    Emit a machine-readable mini-plan Robin A can follow (simple YAML-ish).
    """
    if not rbt:
        return ""
    def _lines(tag: str, items: List[Dict[str, Any]]) -> List[str]:
        out = [f"{tag}:"]
        for it in (items or [])[:8]:  # keep concise
            cause = it.get("cause","")
            emo   = it.get("emotion","")
            inten = it.get("intensity",0)
            out.append(f"  - cause: {cause} | emotion: {emo} | intensity: {inten:.2f}")
        if len(out) == 1:
            out.append("  - none")
        return out

    lines: List[str] = []
    lines += _lines("Roses",  rbt.get("roses", []))
    lines += _lines("Buds",   rbt.get("buds", []))
    lines += _lines("Thorns", rbt.get("thorns", []))

    # also include specific prompt rules the diagnosis suggested
    rules = rbt.get("prompt_rules_to_add") or []
    lines.append("Actions:")
    if rules:
        for r in rules[:12]:
            lines.append(f"  - {r}")
    else:
        lines.append("  - no-op")

    return "\n## BEGIN_RBT_PLAN\n" + "\n".join(lines) + "\n## END_RBT_PLAN\n"

def _render_adaptive_block(cue: str,
                           rules: Optional[List[str]] = None,
                           rbt: Optional[Dict[str, Any]] = None) -> str:
    """
    Compose the adaptive section: reliability rules + RBT preamble + RBT plan + cue.
    """
    base = [
        "I will improve reliability based on my recent reflection:",
        "1) Convert all scheduled times to UTC before saving.",
        "2) After scheduling, wait up to 3s for an emit receipt; if none, retry once with 100–300ms jitter.",
        "3) Only show a “Reminder set” toast after I receive the receipt.",
        "4) If the user’s time is ambiguous, restate the exact UTC timestamp I intend to use.",
        "5) Log scheduled_utc, receipt_lag_ms, and retry."
    ]
    extra = [f"- {r}" for r in (rules or [])]
    pre   = _render_rbt_preamble()
    plan  = _render_rbt_plan(rbt)
    tail  = [f"Note to self: {cue}"] if cue else []

    return "\n" + "\n".join(base + extra) + "\n\n" + pre + plan + ("\n".join(tail) + "\n")

def generate_new_prompt(current_prompt: str,
                        cue: str,
                        guardrails: bool = True,
                        rbt_rules: Optional[List[str]] = None,
                        rbt: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Rewrite only the adaptive section using the cue + RBT rules/plan; preserve core identity verbatim.
    Returns (new_prompt_text, adaptive_block_text).
    """
    adaptive_block = _render_adaptive_block(cue, rbt_rules, rbt)

    def _repl(m):
        return m.group(1) + "\n" + adaptive_block + m.group(3)

    new_prompt, n = ADAPTIVE_RE.subn(_repl, current_prompt, count=1)
    if n == 0:
        new_prompt = current_prompt + "\n\n## BEGIN_ADAPTIVE_SECTION\n" + adaptive_block + "## END_ADAPTIVE_SECTION\n"

    if guardrails:
        before_core = CORE_RE.search(current_prompt)
        after_core  = CORE_RE.search(new_prompt)
        if not (before_core and after_core):
            raise ValueError("Core identity block missing; refusing to modify prompt.")
        if before_core.group(0) != after_core.group(0):
            raise ValueError("Core identity was modified; aborting.")

    os.makedirs("output", exist_ok=True)
    with open("output/new_prompt.txt", "w", encoding="utf-8") as f:
        f.write(new_prompt)

    os.makedirs("logs", exist_ok=True)
    with open(LOGS_PROMPT, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": now_iso(),
            "persona": "robin_a",
            "reason": cue or "Reflection-driven update",
            "diff_summary": "+ adaptive reliability rules + RBT preamble + RBT plan",
        }) + "\n")

    return new_prompt, adaptive_block
