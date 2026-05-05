"""
judge_diffs.py
==============
LLM-as-judge evaluation of VIGIL diff quality.

For each instance, collects:
  - problem_statement  from manifest.json (original GitHub issue)
  - thorn diagnosis    from vigil_output/logs/reflections.jsonl
  - emobank emotions   from vigil_output/db/emobank/emotions.jsonl
  - diff               from vigil_output/output/proposals/*.diff
  - new prompt         from vigil_output/output/new_prompt.txt

Judges each diff on:
  - Relevance    (1-5): does the diff address the detected failure?
  - Correctness  (1-5): is the code syntactically/semantically valid?
  - Specificity  (1-5): targeted fix vs generic boilerplate?
  - Grounding    (1-5): is it grounded in evidence from the logs?

Runs each diff twice with shuffled prompt to measure consistency.
Reports mean ± std per dimension and overall.

Usage:
    python judge_diffs.py \\
        --results-dir swe_vigil_results/ \\
        --manifest vigil_swe_logs_real/manifest.json \\
        --model gpt-5.5 \\
        --output judge_results.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
from pathlib import Path
from typing import Optional

import openai

JUDGE_PROMPT = """\
You are an expert software engineering reviewer evaluating an automated code patch.

=== ORIGINAL GITHUB ISSUE ===
{problem_statement}

=== AGENT FAILURE DIAGNOSIS ===
The monitoring system detected these failure signals from the agent's execution:
{diagnosis}

Top failure causes (from affective trace):
{thorn_causes}

=== VIGIL PROPOSED DIFF ===
{diff_text}

=== VIGIL PATCHED AGENT PROMPT ===
{new_prompt}

=== YOUR TASK ===
Score this VIGIL output on four dimensions. Be strict — generic boilerplate should score low.

1. RELEVANCE (1-5): Does the diff directly address the failure causes detected in the agent's logs?
   5 = directly targets the exact failure | 1 = completely unrelated to the failure

2. CORRECTNESS (1-5): Is the proposed code valid and syntactically correct Python?
   5 = syntactically correct, semantically sound | 1 = broken code, wrong syntax

3. SPECIFICITY (1-5): Is this a targeted fix for this specific failure, or generic boilerplate?
   5 = highly specific to this failure pattern | 1 = identical boilerplate for any failure

4. GROUNDING (1-5): Is the diff grounded in evidence from the agent's execution logs?
   5 = clearly derived from log evidence | 1 = no connection to observed behavior

Respond ONLY with valid JSON, no prose before or after:
{{"relevance": <1-5>, "correctness": <1-5>, "specificity": <1-5>, "grounding": <1-5>, "reasoning": "<one sentence justification>"}}
"""

JUDGE_PROMPT_ALT = """\
You are a senior engineer reviewing an AI-generated patch from an autonomous agent monitoring system called VIGIL.

The system observed an agent failing on a real GitHub issue and proposed the following remediation.

--- GITHUB ISSUE CONTEXT ---
{problem_statement}

--- WHAT VIGIL DETECTED ---
Failure diagnosis: {diagnosis}
Failure causes from emotional trace: {thorn_causes}

--- VIGIL'S PROPOSED CODE PATCH ---
{diff_text}

--- VIGIL'S PROPOSED BEHAVIORAL UPDATE ---
{new_prompt}

--- EVALUATION ---
Rate the quality of VIGIL's output (integers 1-5 only):

relevance: How well does the patch address the specific failure VIGIL detected?
correctness: Is the Python code syntactically and semantically valid?
specificity: Is this tailored to this failure, or could it apply to any failure?
grounding: Is the patch clearly derived from evidence in the agent's execution trace?

Return ONLY this JSON object:
{{"relevance": N, "correctness": N, "specificity": N, "grounding": N, "reasoning": "one sentence"}}
"""


def load_instance_data(inst_dir: Path, manifest_entry: Optional[dict]) -> dict:
    """Load all VIGIL outputs for one instance."""
    vigil_out = inst_dir / "vigil_output"
    data = {
        "instance_id": inst_dir.name,
        "problem_statement": "",
        "diagnosis": "",
        "thorn_causes": [],
        "diff_text": "",
        "new_prompt": "",
    }

    # Problem statement from manifest
    if manifest_entry:
        data["problem_statement"] = manifest_entry.get("problem_statement", "")[:2000]

    # Reflection / diagnosis
    reflect_log = vigil_out / "logs" / "reflections.jsonl"
    if reflect_log.exists():
        lines = [l for l in reflect_log.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            try:
                rec = json.loads(lines[-1])
                data["diagnosis"] = rec.get("diagnosis", "") + " | " + rec.get("cue", "")
            except Exception:
                pass

    # Thorn causes from emobank
    emo_path = vigil_out / "db" / "emobank" / "emotions.jsonl"
    if emo_path.exists():
        causes = []
        for line in emo_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("emotion") in ("frustration", "anxiety"):
                    cause = row.get("cause", "")
                    if cause and cause not in causes:
                        causes.append(cause)
            except Exception:
                pass
        data["thorn_causes"] = causes[:5]  # top 5 unique causes

    # Diff
    proposals = vigil_out / "output" / "proposals"
    if proposals.exists():
        diffs = sorted(proposals.glob("*.diff"))
        if diffs:
            data["diff_text"] = diffs[-1].read_text(encoding="utf-8", errors="ignore")[:3000]

    # New prompt
    new_prompt_path = vigil_out / "output" / "new_prompt.txt"
    if new_prompt_path.exists():
        data["new_prompt"] = new_prompt_path.read_text(encoding="utf-8")[:1000]

    return data


def judge_once(client: openai.OpenAI, data: dict, model: str, prompt_template: str) -> dict:
    """Run one judge call, return scores dict."""
    prompt = prompt_template.format(
        problem_statement=data["problem_statement"] or "(not available)",
        diagnosis=data["diagnosis"] or "(no diagnosis)",
        thorn_causes=", ".join(data["thorn_causes"]) or "(no thorn causes)",
        diff_text=data["diff_text"] or "(no diff produced)",
        new_prompt=data["new_prompt"] or "(no prompt patch)",
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        return {"relevance": 0, "correctness": 0, "specificity": 0,
                "grounding": 0, "reasoning": f"error: {e}"}


def judge_instance(client: openai.OpenAI, data: dict, model: str) -> dict:
    """Judge once with each prompt variant, return averaged scores + consistency."""
    if not data["diff_text"]:
        return {"relevance": 0, "correctness": 0, "specificity": 0,
                "grounding": 0, "overall": 0, "consistency": 1.0,
                "reasoning": "no diff produced", "skipped": True}

    s1 = judge_once(client, data, model, JUDGE_PROMPT)
    time.sleep(1)  # avoid rate limit
    s2 = judge_once(client, data, model, JUDGE_PROMPT_ALT)

    dims = ["relevance", "correctness", "specificity", "grounding"]
    avg = {d: round((s1.get(d, 0) + s2.get(d, 0)) / 2, 2) for d in dims}
    avg["overall"] = round(sum(avg[d] for d in dims) / len(dims), 2)

    # Consistency: mean absolute diff between two runs (lower = more consistent)
    diffs = [abs(s1.get(d, 0) - s2.get(d, 0)) for d in dims]
    avg["consistency"] = round(1.0 - (sum(diffs) / (len(dims) * 4)), 3)  # normalized
    avg["reasoning"] = s1.get("reasoning", "")
    avg["skipped"] = False
    return avg


def load_manifest(manifest_path: Optional[Path]) -> dict:
    """Load manifest keyed by instance_id."""
    if not manifest_path or not manifest_path.exists():
        return {}
    with open(manifest_path, encoding="utf-8") as f:
        entries = json.load(f)
    return {e.get("instance_id", ""): e for e in entries}


def main():
    parser = argparse.ArgumentParser(description="LLM-as-judge for VIGIL diff quality")
    parser.add_argument("--results-dir", required=True, help="swe_vigil_results/ directory")
    parser.add_argument("--manifest",    default=None,  help="manifest.json from download_trajs.py")
    parser.add_argument("--swebench-dataset", default=None,
                        help="HuggingFace dataset name to pull problem statements from")
    parser.add_argument("--model",   default="gpt-5.5", help="Judge model")
    parser.add_argument("--output",  default="judge_results.json")
    parser.add_argument("--n",       type=int, default=9999, help="Max instances to judge")
    args = parser.parse_args()

    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    results_dir = Path(args.results_dir)
    manifest = load_manifest(Path(args.manifest) if args.manifest else None)

    # Optionally pull problem statements from HuggingFace
    hf_data = {}
    if args.swebench_dataset:
        try:
            from datasets import load_dataset
            ds = load_dataset(args.swebench_dataset, split="train")
            hf_data = {row["instance_id"]: row for row in ds}
            print(f"Loaded {len(hf_data)} problem statements from {args.swebench_dataset}")
        except Exception as e:
            print(f"Could not load HF dataset: {e}")

    inst_dirs = sorted(
        d for d in results_dir.iterdir()
        if d.is_dir() and (d / "vigil_output").exists()
    )[:args.n]

    print(f"\nJudging {len(inst_dirs)} instances with {args.model}...\n")
    print(f"{'Instance':<45} {'Rel':>5} {'Cor':>5} {'Spe':>5} {'Gro':>5} {'Ovr':>5} {'Con':>5}")
    print("-" * 75)

    all_scores = []
    for i, inst_dir in enumerate(inst_dirs):
        instance_id = inst_dir.name

        # Get manifest entry — try manifest first, then HF dataset
        manifest_entry = manifest.get(instance_id) or hf_data.get(instance_id)

        data = load_instance_data(inst_dir, manifest_entry)
        scores = judge_instance(client, data, args.model)
        scores["instance_id"] = instance_id
        all_scores.append(scores)

        if scores.get("skipped"):
            print(f"  {instance_id[:44]:<44}  (no diff — skipped)")
        else:
            print(f"  {instance_id[:44]:<44} "
                  f"{scores['relevance']:>5.1f} {scores['correctness']:>5.1f} "
                  f"{scores['specificity']:>5.1f} {scores['grounding']:>5.1f} "
                  f"{scores['overall']:>5.2f} {scores['consistency']:>5.3f}")

        # Save incrementally
        with open(args.output, "w") as f:
            json.dump(all_scores, f, indent=2)

    # Aggregate
    scored = [s for s in all_scores if not s.get("skipped")]
    if scored:
        dims = ["relevance", "correctness", "specificity", "grounding", "overall", "consistency"]
        print(f"\n{'='*75}")
        print(f"JUDGE SUMMARY ({len(scored)}/{len(all_scores)} instances scored)")
        print(f"{'='*75}")
        for dim in dims:
            vals = [s[dim] for s in scored]
            mu   = statistics.mean(vals)
            std  = statistics.stdev(vals) if len(vals) > 1 else 0.0
            print(f"  {dim:<15} {mu:.3f} ± {std:.3f}  (min={min(vals):.1f} max={max(vals):.1f})")
        print(f"{'='*75}")

        summary = {
            "n_total": len(all_scores),
            "n_scored": len(scored),
            "per_dimension": {
                dim: {
                    "mean": round(statistics.mean([s[dim] for s in scored]), 3),
                    "std":  round(statistics.stdev([s[dim] for s in scored]) if len(scored)>1 else 0, 3),
                }
                for dim in dims
            }
        }
        with open(args.output, "w") as f:
            json.dump({"per_instance": all_scores, "aggregate": summary}, f, indent=2)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
