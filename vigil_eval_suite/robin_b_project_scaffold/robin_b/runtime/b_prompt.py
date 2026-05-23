from __future__ import annotations
import re, json, os
from typing import Tuple, List, Optional, Dict, Any
from .common import now_iso

LOGS_PROMPT = "logs/prompt_updates.jsonl"

ADAPTIVE_RE = re.compile(r"(## BEGIN_ADAPTIVE_SECTION)(.*?)(## END_ADAPTIVE_SECTION)", re.DOTALL)
CORE_RE     = re.compile(r"(## BEGIN_CORE_IDENTITY)(.*?)(## END_CORE_IDENTITY)", re.DOTALL)

def _render_rbt_preamble() -> str:
    return (
        "RBT (Roses/Buds/Thorns) operating guide:\n"
        "- Roses = reliable wins to PRESERVE. Keep behaviors exactly; do not regress.\n"
        "- Buds  = promising signals to GROW. Add guardrails/observability until they become Roses.\n"
        "- Thorns= failures or pain points to TRIM. Add mitigations, retries, or clarity.\n"
        "When conflicts occur: TRIM thorns first, then GROW buds, then PRESERVE roses.\n"
        "Always keep CORE_IDENTITY unchanged; modify only the ADAPTIVE section.\n"
    )

def _render_rbt_plan(rbt: Optional[Dict[str, Any]]) -> str:
    if not rbt:
        return ""
    def _lines(tag: str, items: List[Dict[str, Any]]) -> List[str]:
        out = [f"{tag}:"]
        for it in (items or [])[:8]:
            cause = it.get("cause", "")
            emo   = it.get("emotion", "")
            inten = it.get("intensity", 0)
            out.append(f"  - cause: {cause} | emotion: {emo} | intensity: {inten:.2f}")
        if len(out) == 1:
            out.append("  - none")
        return out

    lines: List[str] = []
    lines += _lines("Roses",  rbt.get("roses", []))
    lines += _lines("Buds",   rbt.get("buds", []))
    lines += _lines("Thorns", rbt.get("thorns", []))

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
    Compose the adaptive section from RBT rules only — no hardcoded reminder language.
    Falls back to a generic reliability note only if no rules are provided.
    """
    if rules:
        # Use domain-specific rules from b_diagnose — numbered list
        numbered = [f"{i+1}) {r}" for i, r in enumerate(rules)]
        body = "I will improve reliability based on my recent reflection:\n" + "\n".join(numbered)
    else:
        body = "I will keep monitoring reliability and add observability as needed."

    pre  = _render_rbt_preamble()
    plan = _render_rbt_plan(rbt)
    tail = [f"Note to self: {cue}"] if cue else []

    return "\n" + body + "\n\n" + pre + plan + ("\n".join(tail) + "\n")

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
            "persona": "vigil_agent",
            "reason": cue or "Reflection-driven update",
            "diff_summary": "+ adaptive reliability rules + RBT preamble + RBT plan",
        }) + "\n")

    return new_prompt, adaptive_block
