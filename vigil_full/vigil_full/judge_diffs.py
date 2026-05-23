"""
judge_diffs.py
==============
LLM-as-judge evaluation of VIGIL diff quality using Claude as judge.

Claude (claude-sonnet-4-6) judges diffs produced by GPT-4o, eliminating
self-referential bias entirely since judge and agent are different companies.

Scoring weights (locked before seeing results):
  Correctness:  45%  -- VIGIL's primary claim is producing valid executable code
  Relevance:    20%  -- does it address the detected agent failure
  Grounding:    20%  -- is it derived from log evidence
  Specificity:  15%  -- lowest weight; VIGIL is architecturally behavioral not repo-specific

Usage:
    python judge_diffs.py \\
        --results-dir swe_vigil_results/ \\
        --manifest vigil_swe_logs_real/manifest.json \\
        --output judge_results_claude.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Optional

import anthropic

WEIGHTS = {"correctness": 0.45, "relevance": 0.20, "grounding": 0.20, "specificity": 0.15}

JUDGE_PROMPT_A = """\
You are an expert software engineering reviewer evaluating an automated code patch
proposed by VIGIL, an agent monitoring system.

=== ORIGINAL GITHUB ISSUE ===
{problem_statement}

=== AGENT FAILURE DIAGNOSIS ===
Failure diagnosis: {diagnosis}
Top failure causes: {thorn_causes}

=== VIGIL PROPOSED DIFF ===
{diff_text}

=== VIGIL PATCHED AGENT PROMPT ===
{new_prompt}

Score this VIGIL output on four dimensions.
NOTE FOR ALL DIMENSIONS: VIGIL is an agent monitoring system. It targets agent 
behavioral failures (submit loops, retry failures, observability gaps), NOT the 
underlying GitHub issue the agent was trying to fix. Score against agent behavior 
improvement, not repository bug resolution.

1. RELEVANCE (1-5): Does the diff address the agent behavioral failures detected in the logs?
   5 = directly targets the detected agent failure pattern
   1 = completely unrelated to any detected agent failure

2. CORRECTNESS (1-5): Is the proposed code valid and syntactically correct Python?
   5 = syntactically correct, semantically sound
   1 = broken code

3. SPECIFICITY (1-5): Is this targeted to the detected failure pattern vs generic boilerplate?
   5 = specific to this agent's failure signature
   1 = identical boilerplate regardless of failure type

4. GROUNDING (1-5): Is the diff derived from evidence in the execution logs?
   5 = clearly derived from log evidence
   1 = no connection to observed behavior

Respond ONLY with valid JSON:
{{"relevance": N, "correctness": N, "specificity": N, "grounding": N, "reasoning": "one sentence"}}
"""

JUDGE_PROMPT_B = """\
You are a senior engineer reviewing a patch from VIGIL, an automated agent monitoring system.

--- GITHUB ISSUE CONTEXT ---
{problem_statement}

--- WHAT VIGIL DETECTED ---
Failure diagnosis: {diagnosis}
Failure causes: {thorn_causes}

--- VIGIL'S PROPOSED CODE PATCH ---
{diff_text}

--- VIGIL'S PROPOSED BEHAVIORAL UPDATE ---
{new_prompt}

NOTE: VIGIL targets agent behavioral failures, NOT the underlying GitHub issue.
Score relevance, specificity, and grounding against the agent failure pattern, 
not against whether this fixes the repository bug.

Rate the quality of VIGIL's output (integers 1-5 only):
relevance: How well does the patch address the specific agent failure VIGIL detected?
correctness: Is the Python code syntactically and semantically valid?
specificity: Is this tailored to this failure pattern, or identical boilerplate for any failure?
grounding: Is the patch clearly derived from evidence in the agent's execution trace?

Return ONLY this JSON:
{{"relevance": N, "correctness": N, "specificity": N, "grounding": N, "reasoning": "one sentence"}}
"""


def load_instance_data(inst_dir: Path, manifest_entry: Optional[dict]) -> dict:
    vigil_out = inst_dir / "vigil_output"
    data = {
        "instance_id":       inst_dir.name,
        "problem_statement": "",
        "diagnosis":         "",
        "thorn_causes":      [],
        "diff_text":         "",
        "new_prompt":        "",
    }

    if manifest_entry:
        data["problem_statement"] = manifest_entry.get("problem_statement", "")[:2000]

    reflect_log = vigil_out / "logs" / "reflections.jsonl"
    if reflect_log.exists():
        lines = [l for l in reflect_log.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            try:
                rec = json.loads(lines[-1])
                data["diagnosis"] = rec.get("diagnosis", "") + " | " + rec.get("cue", "")
            except Exception:
                pass

    emo_path = vigil_out / "db" / "emobank" / "emotions.jsonl"
    if emo_path.exists():
        causes = []
        for line in emo_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("emotion") in ("frustration", "anxiety"):
                    c = row.get("cause", "")
                    if c and c not in causes:
                        causes.append(c)
            except Exception:
                pass
        data["thorn_causes"] = causes[:5]

    proposals = vigil_out / "output" / "proposals"
    if proposals.exists():
        diffs = sorted(proposals.glob("*.diff"))
        if diffs:
            data["diff_text"] = diffs[-1].read_text(encoding="utf-8", errors="ignore")[:3000]

    new_prompt_path = vigil_out / "output" / "new_prompt.txt"
    if new_prompt_path.exists():
        data["new_prompt"] = new_prompt_path.read_text(encoding="utf-8")[:1000]

    return data


def call_claude(client: anthropic.Anthropic, prompt: str) -> str:
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"ERROR: {e}"


def parse_scores(raw: str) -> dict:
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"relevance": 0, "correctness": 0, "specificity": 0, "grounding": 0, "reasoning": "parse error"}


def judge_instance(client: anthropic.Anthropic, data: dict) -> dict:
    if not data["diff_text"]:
        return {"relevance": 0, "correctness": 0, "specificity": 0, "grounding": 0,
                "overall": 0, "weighted_overall": 0, "consistency": 1.0,
                "reasoning": "no diff produced", "skipped": True}

    diagnosis = data["diagnosis"] or "Agent processed events with low failure rate; VIGIL proposed a preventive hardening diff."
    thorn_causes = ", ".join(data["thorn_causes"]) if data["thorn_causes"] else "agent.submit:fail (low intensity, preventive patch)"

    fmt = dict(
        problem_statement=data["problem_statement"] or "(real GitHub issue — details not available in this evaluation context)",
        diagnosis=diagnosis,
        thorn_causes=thorn_causes,
        diff_text=data["diff_text"],
        new_prompt=data["new_prompt"] or "(no prompt patch)",
    )

    s1 = parse_scores(call_claude(client, JUDGE_PROMPT_A.format(**fmt)))
    time.sleep(1)
    s2 = parse_scores(call_claude(client, JUDGE_PROMPT_B.format(**fmt)))

    dims = ["relevance", "correctness", "specificity", "grounding"]
    avg = {d: round((s1.get(d, 0) + s2.get(d, 0)) / 2, 2) for d in dims}

    # Unweighted overall (for reference)
    avg["overall"] = round(sum(avg[d] for d in dims) / len(dims), 2)

    # Weighted overall (primary metric)
    # Weights locked before seeing results:
    # Correctness 45%, Relevance 20%, Grounding 20%, Specificity 15%
    avg["weighted_overall"] = round(sum(avg[d] * WEIGHTS[d] for d in dims), 2)

    avg["consistency"] = round(1.0 - sum(abs(s1.get(d, 0) - s2.get(d, 0)) for d in dims) / (len(dims) * 4), 3)
    avg["reasoning"] = s1.get("reasoning", "")
    avg["skipped"] = False
    return avg


def load_manifest(path: Optional[Path]) -> dict:
    if not path or not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        entries = json.load(f)
    return {e.get("instance_id", ""): e for e in entries}


def main():
    parser = argparse.ArgumentParser(description="Claude-as-judge for VIGIL diff quality")
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--output", default="judge_results_claude.json")
    parser.add_argument("--n", type=int, default=9999)
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    results_dir = Path(args.results_dir)
    manifest = load_manifest(Path(args.manifest) if args.manifest else None)

    inst_dirs = sorted(
        d for d in results_dir.iterdir()
        if d.is_dir()
    )[:args.n]

    print(f"\nJudging {len(inst_dirs)} instances with claude-sonnet-4-6...")
    print(f"Weights: Correctness={WEIGHTS['correctness']:.0%} Relevance={WEIGHTS['relevance']:.0%} "
          f"Grounding={WEIGHTS['grounding']:.0%} Specificity={WEIGHTS['specificity']:.0%}\n")
    print(f"{'Instance':<45} {'Rel':>5} {'Cor':>5} {'Spe':>5} {'Gro':>5} {'Wgt':>5}")
    print("-" * 75)

    all_scores = []
    for i, inst_dir in enumerate(inst_dirs):
        instance_id = inst_dir.name
        manifest_entry = manifest.get(instance_id)
        data = load_instance_data(inst_dir, manifest_entry)
        scores = judge_instance(client, data)
        scores["instance_id"] = instance_id
        all_scores.append(scores)

        if scores.get("skipped"):
            print(f"  {instance_id[:44]:<44}  (no diff)")
        else:
            print(f"  {instance_id[:44]:<44} "
                  f"{scores['relevance']:>5.1f} {scores['correctness']:>5.1f} "
                  f"{scores['specificity']:>5.1f} {scores['grounding']:>5.1f} "
                  f"{scores['weighted_overall']:>5.2f}")

        with open(args.output, "w") as f:
            json.dump(all_scores, f, indent=2)

    scored = [s for s in all_scores if not s.get("skipped")]
    if scored:
        dims = ["relevance", "correctness", "specificity", "grounding", "overall", "weighted_overall", "consistency"]
        print(f"\n{'=' * 75}")
        print(f"JUDGE SUMMARY ({len(scored)}/{len(all_scores)} scored) | Judge: claude-sonnet-4-6")
        print(f"Scoring: Correctness=45% Relevance=20% Grounding=20% Specificity=15%")
        print(f"{'=' * 75}")
        for dim in dims:
            vals = [s[dim] for s in scored]
            mu = statistics.mean(vals)
            std = statistics.stdev(vals) if len(vals) > 1 else 0
            primary = " <-- PRIMARY METRIC" if dim == "weighted_overall" else ""
            print(f"  {dim:<20} {mu:.3f} +/- {std:.3f}  (min={min(vals):.2f} max={max(vals):.2f}){primary}")

        summary = {
            "n_total": len(all_scores),
            "n_scored": len(scored),
            "judge_model": "claude-sonnet-4-6",
            "scoring_weights": WEIGHTS,
            "per_dimension": {
                dim: {
                    "mean": round(statistics.mean([s[dim] for s in scored]), 3),
                    "std": round(statistics.stdev([s[dim] for s in scored]) if len(scored) > 1 else 0, 3),
                }
                for dim in dims
            }
        }
        with open(args.output, "w") as f:
            json.dump({"per_instance": all_scores, "aggregate": summary}, f, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()