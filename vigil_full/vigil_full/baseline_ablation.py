"""
baseline_ablation.py
=====================
Ablation study: VIGIL EmoBank/RBT pipeline vs raw-log prompting baseline.

Two judge passes per instance:
  Pass 1 (FAIR):        Both diffs judged against raw logs. Same context, same standard.
  Pass 2 (OPERATIONAL): VIGIL diff judged against RBT diagnosis.
                        Baseline diff judged against raw logs.
                        Reflects real operator experience.

Diff generation: GPT-4o for both conditions.
Judge: Claude Sonnet (claude-sonnet-4-6) for both conditions.

Scoring weights (locked before seeing results):
  Correctness: 45%, Relevance: 20%, Grounding: 20%, Specificity: 15%

Usage:
    python baseline_ablation.py \\
        --logs-dir vigil_swe_logs_real/ \\
        --vigil-root robin_b_project_scaffold/ \\
        --n 30 \\
        --output ablation_results.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import anthropic
import openai

WEIGHTS = {"correctness": 0.45, "relevance": 0.20, "grounding": 0.20, "specificity": 0.15}

# ---------------------------------------------------------------------------
# Baseline: raw log -> GPT-4o -> diff
# ---------------------------------------------------------------------------

BASELINE_SYSTEM = """\
You are an agent monitoring tool. You will receive agent execution events.
Analyze the failure patterns and write a unified code diff to improve reliability.

Output ONLY a valid unified diff:
--- a/agent.py
+++ b/agent.py
@@ -1,0 +1,N @@
[your changes]

No prose. Just the diff.
"""

BASELINE_USER = """\
Agent execution log ({n_events} events, {n_fail} failures):

{log_text}

Write a unified diff addressing the failure patterns.
"""

# ---------------------------------------------------------------------------
# Judge prompts — Pass 1 (FAIR): both conditions judged against raw logs
# ---------------------------------------------------------------------------

JUDGE_FAIR_A = """\
You are evaluating a code patch from an agent monitoring tool.
The tool analyzed these execution logs and produced the patch below.

NOTE: The monitoring tool targets agent behavioral failures (submit loops, retry 
failures, observability gaps), NOT the underlying task the agent was performing.
Score against agent behavior improvement.

=== AGENT EXECUTION LOGS ===
{log_context}

=== PROPOSED DIFF ===
{diff_text}

Score on four dimensions (integer 1-5 each):
1. RELEVANCE: Does the diff address the agent behavioral failures visible in the logs?
2. CORRECTNESS: Is the code syntactically and semantically valid Python?
3. SPECIFICITY: Targeted to these failure patterns vs identical boilerplate for any failure?
4. GROUNDING: Is the diff derived from evidence in the execution logs?

Return ONLY JSON:
{{"relevance": N, "correctness": N, "specificity": N, "grounding": N, "reasoning": "one sentence"}}
"""

JUDGE_FAIR_B = """\
You are a senior engineer reviewing an AI-generated monitoring patch.
The system analyzed these agent logs and proposed this fix.

NOTE: Score against agent behavioral improvement, not whether this fixes the 
underlying task the agent was trying to complete.

--- AGENT LOGS ---
{log_context}

--- PROPOSED PATCH ---
{diff_text}

Rate quality (1-5 each):
relevance: addresses agent behavioral failures in the logs?
correctness: Python syntactically and semantically valid?
specificity: targeted to these failures or generic boilerplate?
grounding: derived from log evidence?

JSON only:
{{"relevance": N, "correctness": N, "specificity": N, "grounding": N, "reasoning": "one sentence"}}
"""

# ---------------------------------------------------------------------------
# Judge prompts — Pass 2 (OPERATIONAL)
# VIGIL diff judged against RBT diagnosis (what operator sees)
# Baseline diff judged against raw logs (what operator sees)
# ---------------------------------------------------------------------------

JUDGE_OPERATIONAL_VIGIL_A = """\
You are evaluating a code patch from VIGIL, an agent monitoring system.
VIGIL analyzed agent logs through its EmoBank/RBT diagnostic pipeline and 
produced the structured diagnosis below. The patch was generated from this diagnosis.

NOTE: VIGIL targets agent behavioral failures, not the underlying task.
Score against agent behavior improvement.

=== VIGIL RBT DIAGNOSIS ===
{rbt_context}

=== PROPOSED DIFF ===
{diff_text}

Score on four dimensions (integer 1-5 each):
1. RELEVANCE: Does the diff address the thorns/failures in the RBT diagnosis?
2. CORRECTNESS: Is the code syntactically and semantically valid Python?
3. SPECIFICITY: Targeted to these diagnosed failures vs generic boilerplate?
4. GROUNDING: Is the diff derived from the diagnostic evidence?

Return ONLY JSON:
{{"relevance": N, "correctness": N, "specificity": N, "grounding": N, "reasoning": "one sentence"}}
"""

JUDGE_OPERATIONAL_VIGIL_B = """\
You are reviewing a patch from VIGIL's affective diagnostic pipeline.
VIGIL diagnosed these agent failures via EmoBank analysis:

NOTE: Score against agent behavioral improvement, not task completion.

--- VIGIL DIAGNOSIS ---
{rbt_context}

--- PROPOSED PATCH ---
{diff_text}

Rate quality (1-5 each):
relevance: addresses diagnosed agent failures?
correctness: Python valid?
specificity: targeted or generic boilerplate?
grounding: derived from diagnostic evidence?

JSON only:
{{"relevance": N, "correctness": N, "specificity": N, "grounding": N, "reasoning": "one sentence"}}
"""

JUDGE_OPERATIONAL_BASELINE_A = JUDGE_FAIR_A
JUDGE_OPERATIONAL_BASELINE_B = JUDGE_FAIR_B

# ---------------------------------------------------------------------------
# VIGIL subprocess runner
# ---------------------------------------------------------------------------

VIGIL_SCRIPT = '''
import sys, os, json, glob
from pathlib import Path

args = json.loads(sys.argv[1])
project_parent = str(Path(args["project_root"]).parent)
if project_parent not in sys.path:
    sys.path.insert(0, project_parent)

os.environ["EMO_DIR"] = args["emo_dir"]
os.makedirs(args["emo_dir"], exist_ok=True)
os.makedirs(args["out_dir"], exist_ok=True)

import shutil
os.chdir(args["out_dir"])
os.makedirs("logs", exist_ok=True)
shutil.copy(args["logs_path"], "logs/events.jsonl")
os.makedirs("output/proposals", exist_ok=True)

with open(args["prompt_path"], "w") as pf:
    pf.write("""## BEGIN_CORE_IDENTITY
I am a software engineering agent that analyzes GitHub issues and produces patches.
I run existing tests before and after changes.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
Approach tasks methodically. Verify with tests before submitting.
## END_ADAPTIVE_SECTION
""")

from robin_b_project_scaffold.robin_b.RobinBAgent.orchestrator import run_once, SESSION
SESSION["stage"] = "start"
SESSION["logs_path"] = "logs/events.jsonl"

run_once(
    logs_path="logs/events.jsonl",
    agent_prompt_path=args["prompt_path"],
    repo_root=args["repo_root"],
)

emo_file = os.path.join(args["emo_dir"], "emotions.jsonl")
emo_rows = sum(1 for _ in open(emo_file)) if os.path.exists(emo_file) else 0
thorn_count = 0
thorn_causes = []
if os.path.exists(emo_file):
    for line in open(emo_file):
        try:
            row = json.loads(line)
            if row.get("emotion") in ("frustration", "anxiety"):
                thorn_count += 1
                c = row.get("cause","")
                if c and c not in thorn_causes:
                    thorn_causes.append(c)
        except:
            pass

rbt_context = "Top failure causes (thorns) from EmoBank analysis:\\n"
for c in thorn_causes[:8]:
    rbt_context += f"  - {c}\\n"
rbt_context += f"Thorn count: {thorn_count} | EmoBank rows: {emo_rows}\\n"

reflect_log = "logs/reflections.jsonl"
if os.path.exists(reflect_log):
    lines = [l for l in open(reflect_log) if l.strip()]
    if lines:
        try:
            rec = json.loads(lines[-1])
            rbt_context += f"Diagnosis: {rec.get('diagnosis','')}\\n"
            rbt_context += f"Cue: {rec.get('cue','')}\\n"
        except:
            pass

diffs = sorted(glob.glob("output/proposals/*.diff"))
diff_text = open(diffs[-1], encoding="utf-8").read() if diffs else ""

import sys as _sys
_sys.stdout.write(json.dumps({
    "emo_rows": emo_rows,
    "thorn_count": thorn_count,
    "thorn_causes": thorn_causes[:8],
    "rbt_context": rbt_context,
    "diff_produced": bool(diffs),
    "diff_text": diff_text,
    "error": "",
}) + "\\n")
_sys.stdout.flush()
'''


def run_vigil_subprocess(logs_path: Path, vigil_root: Path, out_dir: Path) -> dict:
    emo_dir = out_dir / "db" / "emobank"
    emo_dir.mkdir(parents=True, exist_ok=True)

    args = {
        "logs_path":    str(logs_path.resolve()),
        "prompt_path":  str((out_dir / "agent_prompt.txt").resolve()),
        "repo_root":    str(vigil_root.resolve()),
        "emo_dir":      str(emo_dir.resolve()),
        "out_dir":      str(out_dir.resolve()),
        "project_root": str(vigil_root.resolve()),
    }

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(VIGIL_SCRIPT)
        script = f.name

    try:
        proc = subprocess.run(
            [sys.executable, script, json.dumps(args)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            return {"emo_rows": 0, "thorn_count": 0, "thorn_causes": [], "rbt_context": "",
                    "diff_produced": False, "diff_text": "", "error": proc.stderr[-400:]}
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
        if not lines:
            return {"emo_rows": 0, "thorn_count": 0, "thorn_causes": [], "rbt_context": "",
                    "diff_produced": False, "diff_text": "", "error": "no subprocess output"}
        return json.loads(lines[-1])
    except Exception as e:
        return {"emo_rows": 0, "thorn_count": 0, "thorn_causes": [], "rbt_context": "",
                "diff_produced": False, "diff_text": "", "error": str(e)}
    finally:
        try:
            os.unlink(script)
        except:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def summarize_log(log_path: Path, max_events: int = 40) -> tuple[str, int, int]:
    events = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except:
                    pass

    lines = []
    for ev in events[:max_events]:
        kind    = ev.get("kind", "")
        status  = ev.get("status", "ok")
        ts      = ev.get("ts", "")[:19]
        payload = ev.get("payload", {})
        err     = payload.get("error_snippet", "") or payload.get("command_preview", "")[:60]
        line    = f"[{ts}] {kind} -> {status}"
        if err:
            line += f" | {err}"
        lines.append(line)

    failures = [e for e in events if e.get("status") in ("fail", "error", "timeout")]
    kinds = {}
    for e in failures:
        k = e.get("kind", "unknown")
        kinds[k] = kinds.get(k, 0) + 1
    if kinds:
        lines.append("\nFailure summary: " +
                     ", ".join(f"{k}:{v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])))

    return "\n".join(lines), len(events), len(failures)


def call_openai(client: openai.OpenAI, system: str, user: str) -> str:
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=800, temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"ERROR: {e}"


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
    except:
        return {"relevance": 0, "correctness": 0, "specificity": 0, "grounding": 0,
                "reasoning": "parse error"}


def judge_pair(claude: anthropic.Anthropic,
               context: str, diff_text: str,
               prompt_a: str, prompt_b: str) -> dict:
    if not diff_text or len(diff_text) < 20:
        return {"relevance": 0, "correctness": 0, "specificity": 0, "grounding": 0,
                "overall": 0, "weighted_overall": 0, "consistency": 0,
                "reasoning": "no diff produced"}

    ctx = context[:800]
    dt  = diff_text[:1500]

    s1 = parse_scores(call_claude(claude, prompt_a.format(
        log_context=ctx, rbt_context=ctx, diff_text=dt)))
    time.sleep(0.8)
    s2 = parse_scores(call_claude(claude, prompt_b.format(
        log_context=ctx, rbt_context=ctx, diff_text=dt)))
    time.sleep(0.5)

    dims = ["relevance", "correctness", "specificity", "grounding"]
    avg = {d: round((s1.get(d, 0) + s2.get(d, 0)) / 2, 2) for d in dims}
    avg["overall"] = round(sum(avg[d] for d in dims) / len(dims), 2)
    avg["weighted_overall"] = round(sum(avg[d] * WEIGHTS[d] for d in dims), 2)
    avg["consistency"] = round(
        1.0 - sum(abs(s1.get(d, 0) - s2.get(d, 0)) for d in dims) / (len(dims) * 4), 3)
    avg["reasoning"] = s1.get("reasoning", "")
    return avg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir",   required=True)
    parser.add_argument("--vigil-root", required=True)
    parser.add_argument("--n",          type=int, default=30)
    parser.add_argument("--output",     default="ablation_results.json")
    args = parser.parse_args()

    oai    = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    logs_dir   = Path(args.logs_dir)
    vigil_root = Path(args.vigil_root)
    log_files  = sorted(logs_dir.glob("*.jsonl"))[:args.n]

    print(f"\nAblation: {len(log_files)} instances")
    print(f"Diff generation: GPT-4o | Judge: claude-sonnet-4-6")
    print(f"Weights: Correctness={WEIGHTS['correctness']:.0%} Relevance={WEIGHTS['relevance']:.0%} "
          f"Grounding={WEIGHTS['grounding']:.0%} Specificity={WEIGHTS['specificity']:.0%}")
    print(f"\nPass 1 (FAIR):        Both diffs judged against raw logs")
    print(f"Pass 2 (OPERATIONAL): VIGIL judged on RBT diagnosis | Baseline judged on raw logs")
    print(f"\n{'Instance':<42} {'P1-VIGIL':>9} {'P1-Base':>8} {'P1-Δ':>6} {'P2-VIGIL':>9} {'P2-Base':>8} {'P2-Δ':>6}")
    print("-" * 95)

    results = []
    for i, log_path in enumerate(log_files):
        instance_id = log_path.stem
        log_text, n_ev, n_fail = summarize_log(log_path)
        print(f"\n[{i+1}/{len(log_files)}] {instance_id[:40]} ({n_ev} events, {n_fail} fail)")

        # Generate baseline diff from raw logs
        baseline_diff = call_openai(oai, BASELINE_SYSTEM,
            BASELINE_USER.format(log_text=log_text, n_events=n_ev, n_fail=n_fail))
        time.sleep(0.5)

        # Generate VIGIL diff via full pipeline
        out_dir = Path("ablation_vigil_out") / instance_id
        out_dir.mkdir(parents=True, exist_ok=True)
        vigil_result = run_vigil_subprocess(log_path, vigil_root, out_dir)
        vigil_diff   = vigil_result.get("diff_text", "")
        rbt_context  = vigil_result.get("rbt_context", "(no RBT diagnosis)")

        # Pass 1: FAIR — both judged against raw logs
        p1_vigil = judge_pair(claude, log_text, vigil_diff,
                              JUDGE_FAIR_A, JUDGE_FAIR_B)
        p1_base  = judge_pair(claude, log_text, baseline_diff,
                              JUDGE_FAIR_A, JUDGE_FAIR_B)
        p1_delta = round(p1_vigil["weighted_overall"] - p1_base["weighted_overall"], 2)

        # Pass 2: OPERATIONAL — VIGIL judged on RBT, baseline on raw logs
        p2_vigil = judge_pair(claude, rbt_context, vigil_diff,
                              JUDGE_OPERATIONAL_VIGIL_A, JUDGE_OPERATIONAL_VIGIL_B)
        p2_base  = judge_pair(claude, log_text, baseline_diff,
                              JUDGE_OPERATIONAL_BASELINE_A, JUDGE_OPERATIONAL_BASELINE_B)
        p2_delta = round(p2_vigil["weighted_overall"] - p2_base["weighted_overall"], 2)

        err = f" [err: {vigil_result['error'][:40]}]" if vigil_result.get("error") else ""
        print(f"  P1: VIGIL={p1_vigil['weighted_overall']:.2f} Base={p1_base['weighted_overall']:.2f} Δ={p1_delta:+.2f} | "
              f"P2: VIGIL={p2_vigil['weighted_overall']:.2f} Base={p2_base['weighted_overall']:.2f} Δ={p2_delta:+.2f}"
              f" (emo={vigil_result.get('emo_rows',0)}, t={vigil_result.get('thorn_count',0)}){err}")

        results.append({
            "instance_id":         instance_id,
            "n_events":            n_ev,
            "n_failures":          n_fail,
            "pass1_fair": {
                "vigil":    p1_vigil,
                "baseline": p1_base,
                "delta":    p1_delta,
            },
            "pass2_operational": {
                "vigil":    p2_vigil,
                "baseline": p2_base,
                "delta":    p2_delta,
            },
            "vigil_emo_rows":      vigil_result.get("emo_rows", 0),
            "vigil_thorns":        vigil_result.get("thorn_count", 0),
            "vigil_diff_produced": vigil_result.get("diff_produced", False),
            "vigil_error":         vigil_result.get("error", ""),
        })

        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)

    # Summary
    print(f"\n{'=' * 95}")
    print(f"ABLATION SUMMARY | {len(results)} instances")
    print(f"Judge: claude-sonnet-4-6 | Weights: Correctness=45% Relevance=20% Grounding=20% Specificity=15%")
    print(f"{'=' * 95}")

    for pass_key, pass_label in [("pass1_fair", "Pass 1 FAIR (both on raw logs)"),
                                   ("pass2_operational", "Pass 2 OPERATIONAL (VIGIL on RBT)")]:
        print(f"\n  {pass_label}:")
        dims = ["relevance", "correctness", "specificity", "grounding", "weighted_overall"]
        for dim in dims:
            v = [r[pass_key]["vigil"][dim]    for r in results if r[pass_key]["vigil"].get(dim, 0) > 0]
            b = [r[pass_key]["baseline"][dim] for r in results if r[pass_key]["baseline"].get(dim, 0) > 0]
            if v and b:
                vm, bm = statistics.mean(v), statistics.mean(b)
                vs = statistics.stdev(v) if len(v) > 1 else 0
                bs = statistics.stdev(b) if len(b) > 1 else 0
                primary = " <-- PRIMARY" if dim == "weighted_overall" else ""
                print(f"    {dim:<20} VIGIL={vm:.3f}±{vs:.3f}  Base={bm:.3f}±{bs:.3f}  Δ={vm-bm:+.3f}{primary}")

        p1_wins   = sum(1 for r in results if r[pass_key]["delta"] > 0)
        p1_ties   = sum(1 for r in results if r[pass_key]["delta"] == 0)
        p1_losses = sum(1 for r in results if r[pass_key]["delta"] < 0)
        print(f"    Wins: {p1_wins}/{len(results)} | Ties: {p1_ties} | Losses: {p1_losses}")

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved: {args.output}")
    print("=" * 95)


if __name__ == "__main__":
    main()