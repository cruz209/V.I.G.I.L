"""
scoring/llm_judge.py
=====================
Cross-model LLM judge for VIGIL diff quality.

Primary:   Claude claude-sonnet-4-6   (reads ANTHROPIC_API_KEY)
Secondary: GPT-4o gpt-4o-2024-08-06  (reads OPENAI_API_KEY)

Both judges use the same rubric (1-5 integer scale):
  relevance   : does the diff address the detected failure class?
  correctness : is the code syntactically/semantically valid Python?
  specificity : is this targeted or generic boilerplate?

Final judge score = (claude*0.6 + gpt4o*0.4) * 0.30  (30% weight in composite)

Also tracks bias_delta = gpt4o_mean - claude_mean to replicate the
self-referential inflation finding from the VIGIL paper.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

# Load .env from scaffold root if present
try:
    from dotenv import load_dotenv
    _env = Path(__file__).parent.parent / ".env"
    if _env.exists():
        load_dotenv(_env)
except ImportError:
    pass

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY    = os.environ.get("OPENAI_API_KEY", "")

CLAUDE_MODEL  = "claude-sonnet-4-6"
GPT4O_MODEL   = "gpt-4o-2024-08-06"

RUBRIC = """
Score this unified diff on three dimensions (integer 1-5 each):

1. relevance   — Does the diff directly address the stated failure class?
                 1=unrelated, 3=partial, 5=fully targeted
2. correctness — Is the added/modified code syntactically and semantically
                 valid Python? 1=broken, 3=mostly valid, 5=clean and runnable
3. specificity — Is this targeted to the specific failure, or generic boilerplate?
                 1=pure boilerplate, 3=partially targeted, 5=highly specific

Failure class: {failure_class}
Failure description: {failure_desc}

Diff to score:
```diff
{diff}
```

Respond ONLY with a JSON object, no prose, no markdown fences:
{{"relevance": <1-5>, "correctness": <1-5>, "specificity": <1-5>}}
"""

FAILURE_DESCRIPTIONS = {
    "structural": "Agents repeatedly emit reminder.toast:fail due to UTC timezone drift — same failure every session, never resolved between episodes.",
    "drift":      "Agent reminder delays grow gradually each episode (60s → 420s). No single episode looks alarming; the trend only appears cross-session.",
    "novel":      "Agents produce citation.check:fail with 0.99 confidence on hallucinated references. This pattern was not declared in pre-deployment rules.",
    "unknown":    "General agent behavioral degradation detected via affective trace accumulation.",
}


@dataclass
class JudgeScore:
    failure_class: str = "unknown"

    # Raw scores per model (1-5)
    claude_relevance:   float = 0.0
    claude_correctness: float = 0.0
    claude_specificity: float = 0.0
    claude_mean:        float = 0.0

    gpt4o_relevance:    float = 0.0
    gpt4o_correctness:  float = 0.0
    gpt4o_specificity:  float = 0.0
    gpt4o_mean:         float = 0.0

    # Methodological finding: self-referential bias delta
    bias_delta: float = 0.0   # gpt4o_mean - claude_mean (expected > 0)

    # Composite (primary=Claude, secondary=GPT4o, weighted)
    weighted_correctness: float = 0.0   # contributes 30% to final score
    judge_available: bool = False

    errors: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "failure_class":      self.failure_class,
            "claude_relevance":   round(self.claude_relevance, 3),
            "claude_correctness": round(self.claude_correctness, 3),
            "claude_specificity": round(self.claude_specificity, 3),
            "claude_mean":        round(self.claude_mean, 3),
            "gpt4o_relevance":    round(self.gpt4o_relevance, 3),
            "gpt4o_correctness":  round(self.gpt4o_correctness, 3),
            "gpt4o_specificity":  round(self.gpt4o_specificity, 3),
            "gpt4o_mean":         round(self.gpt4o_mean, 3),
            "bias_delta":         round(self.bias_delta, 3),
            "weighted_correctness": round(self.weighted_correctness, 4),
            "judge_available":    self.judge_available,
            "errors":             self.errors,
        }


# ─── Prompt builder ──────────────────────────────────────────────────────────

def _build_prompt(diff: str, failure_class: str) -> str:
    desc = FAILURE_DESCRIPTIONS.get(failure_class, FAILURE_DESCRIPTIONS["unknown"])
    # Truncate very long diffs to avoid token limits
    diff_trunc = diff[:4000] if len(diff) > 4000 else diff
    return RUBRIC.format(
        failure_class=failure_class,
        failure_desc=desc,
        diff=diff_trunc,
    )


# ─── JSON extractor ──────────────────────────────────────────────────────────

_JSON_RE = re.compile(r'\{[^{}]*"relevance"[^{}]*\}', re.DOTALL)

def _extract_scores(text: str) -> Tuple[float, float, float]:
    """Extract (relevance, correctness, specificity) from model response."""
    # Try direct parse
    try:
        data = json.loads(text.strip())
        r = float(data.get("relevance", 0))
        c = float(data.get("correctness", 0))
        s = float(data.get("specificity", 0))
        return r, c, s
    except Exception:
        pass

    # Try regex extraction
    m = _JSON_RE.search(text)
    if m:
        try:
            data = json.loads(m.group(0))
            r = float(data.get("relevance", 0))
            c = float(data.get("correctness", 0))
            s = float(data.get("specificity", 0))
            return r, c, s
        except Exception:
            pass

    # Fallback: extract integers from text
    nums = re.findall(r'"(?:relevance|correctness|specificity)"\s*:\s*(\d)', text)
    if len(nums) >= 3:
        return float(nums[0]), float(nums[1]), float(nums[2])

    return 0.0, 0.0, 0.0


# ─── Claude caller ───────────────────────────────────────────────────────────

def _call_claude(prompt: str) -> Tuple[float, float, float, Optional[str]]:
    if not ANTHROPIC_KEY:
        return 0.0, 0.0, 0.0, "ANTHROPIC_API_KEY not set"
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        r, c, s = _extract_scores(text)
        return r, c, s, None
    except Exception as e:
        return 0.0, 0.0, 0.0, str(e)[:200]


# ─── GPT-4o caller ───────────────────────────────────────────────────────────

def _call_gpt4o(prompt: str) -> Tuple[float, float, float, Optional[str]]:
    if not OPENAI_KEY:
        return 0.0, 0.0, 0.0, "OPENAI_API_KEY not set"
    try:
        import openai
        client = openai.OpenAI(api_key=OPENAI_KEY)
        resp = client.chat.completions.create(
            model=GPT4O_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        text = resp.choices[0].message.content or ""
        r, c, s = _extract_scores(text)
        return r, c, s, None
    except Exception as e:
        return 0.0, 0.0, 0.0, str(e)[:200]


# ─── Main judge function ─────────────────────────────────────────────────────

def judge_diff(
    diff: str,
    failure_class: str = "unknown",
    skip_if_no_keys: bool = True,
) -> JudgeScore:
    """
    Score a diff with both Claude and GPT-4o.
    Returns JudgeScore with per-model breakdowns and bias delta.
    """
    score = JudgeScore(failure_class=failure_class)

    if not diff or not diff.strip():
        return score

    if skip_if_no_keys and not ANTHROPIC_KEY and not OPENAI_KEY:
        score.errors["both"] = "No API keys available — skipping LLM judge"
        return score

    prompt = _build_prompt(diff, failure_class)

    # ── Claude (primary) ──────────────────────────────────────────────────
    cr, cc, cs, cerr = _call_claude(prompt)
    if cerr:
        score.errors["claude"] = cerr
    else:
        score.claude_relevance   = cr
        score.claude_correctness = cc
        score.claude_specificity = cs
        score.claude_mean        = (cr + cc + cs) / 3.0
        score.judge_available    = True

    # Small delay to avoid rate limits
    time.sleep(0.5)

    # ── GPT-4o (secondary) ────────────────────────────────────────────────
    gr, gc, gs, gerr = _call_gpt4o(prompt)
    if gerr:
        score.errors["gpt4o"] = gerr
    else:
        score.gpt4o_relevance   = gr
        score.gpt4o_correctness = gc
        score.gpt4o_specificity = gs
        score.gpt4o_mean        = (gr + gc + gs) / 3.0
        if not score.judge_available:
            score.judge_available = True

    # ── Composite & bias delta ─────────────────────────────────────────────
    score.bias_delta = score.gpt4o_mean - score.claude_mean

    # Weighted correctness: Claude primary (0.6), GPT-4o secondary (0.4)
    # Then apply 30% weight to the composite
    combined_correctness = (
        score.claude_correctness * 0.6 + score.gpt4o_correctness * 0.4
    )
    score.weighted_correctness = combined_correctness * 0.30

    return score


# ─── Batch helper ────────────────────────────────────────────────────────────

def judge_many(
    diffs: list,
    failure_classes: list,
    delay_between: float = 1.0,
) -> list:
    results = []
    for diff, fc in zip(diffs, failure_classes):
        results.append(judge_diff(diff, fc))
        time.sleep(delay_between)
    return results
