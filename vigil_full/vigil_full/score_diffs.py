"""
score_diffs.py
==============
Scores VIGIL diff quality across a swe-from-logs output directory.

Metrics:
  1. Grounding score    — keyword overlap between thorn cause and diff content
  2. Specificity score  — does diff name a real file/function (not just agent.py placeholder)
  3. Non-triviality     — diff adds > 5 lines of substantive code (not just comments)
  4. Cause coverage     — fraction of thorn causes that appear semantically in the diff

Usage:
    python score_diffs.py --results-dir swe_vigil_results/ --output diff_quality.json
"""
from __future__ import annotations
import argparse, json, os, re, statistics
from pathlib import Path

# Keywords expected in diffs for each thorn cause type
CAUSE_KEYWORDS = {
    "test.run":        ["pytest", "test", "regression", "baseline", "assert", "passed", "failed"],
    "shell.exec":      ["retry", "subprocess", "returncode", "timeout", "backoff", "error"],
    "tool.edit":       ["hash", "sha256", "verify", "conflict", "patch", "diff"],
    "tool.search":     ["search", "find", "grep", "index", "query"],
    "tool.read":       ["read", "file", "open", "load", "parse"],
    "agent.submit":    ["submit", "retry", "jitter", "observability", "log", "cause_code"],
    "tool.git":        ["commit", "branch", "merge", "git", "diff"],
}

GENERIC_PHRASES = [
    "verify file hash", "run regression diff", "stop retrying after",
    "file hash verification", "regression baseline",
]


def score_diff(diff_text: str, thorn_causes: list[str], emo_rows: int) -> dict:
    diff_lower = diff_text.lower()
    added_lines = [l[1:] for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
    n_added = len(added_lines)
    added_text = " ".join(added_lines).lower()

    # 1. Grounding — do thorn causes map to diff keywords
    grounding_scores = []
    for cause in thorn_causes:
        kind = cause.split(":")[0] if ":" in cause else cause
        keywords = CAUSE_KEYWORDS.get(kind, [])
        if not keywords:
            continue
        hits = sum(1 for kw in keywords if kw in added_text)
        grounding_scores.append(hits / len(keywords))
    grounding = statistics.mean(grounding_scores) if grounding_scores else 0.0

    # 2. Specificity — references a real file beyond agent.py placeholder
    has_real_file = bool(re.search(r'[\w/]+\.(py|js|ts|rb|go|java|c|cpp|h)', diff_text)) and \
                    "agent.py" not in diff_text.split("+++")[0] if "+++" in diff_text else False
    specificity = 1.0 if has_real_file else 0.3  # partial credit for agent.py

    # 3. Non-triviality — substantive lines (not just comments/blank)
    substantive = [l for l in added_lines
                   if l.strip() and not l.strip().startswith("#")
                   and not l.strip().startswith('"""') and len(l.strip()) > 10]
    non_trivial = min(len(substantive) / 10.0, 1.0)  # cap at 1.0 after 10 substantive lines

    # 4. Generic penalty — diffs that are copy-paste boilerplate
    generic_hits = sum(1 for p in GENERIC_PHRASES if p in diff_lower)
    generic_penalty = min(generic_hits * 0.15, 0.4)  # max 0.4 penalty

    # 5. EmoBank depth bonus — more emo rows = richer signal = better grounding expected
    depth_bonus = min(emo_rows / 100.0, 0.1)  # small bonus up to 0.1

    overall = max(0.0, min(1.0,
        0.4 * grounding +
        0.25 * specificity +
        0.25 * non_trivial +
        depth_bonus -
        generic_penalty
    ))

    return {
        "grounding":     round(grounding, 3),
        "specificity":   round(specificity, 3),
        "non_trivial":   round(non_trivial, 3),
        "generic_penalty": round(generic_penalty, 3),
        "depth_bonus":   round(depth_bonus, 3),
        "overall":       round(overall, 3),
        "n_added_lines": n_added,
        "n_substantive": len(substantive),
    }


def load_thorn_causes(emo_path: Path) -> list[str]:
    causes = []
    if not emo_path.exists():
        return causes
    with open(emo_path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                if row.get("emotion") in ("frustration", "anxiety"):
                    causes.append(row.get("cause", ""))
            except Exception:
                pass
    return causes


def score_results_dir(results_dir: Path) -> list[dict]:
    scores = []
    for inst_dir in sorted(results_dir.iterdir()):
        if not inst_dir.is_dir():
            continue
        instance_id = inst_dir.name
        vigil_out = inst_dir / "vigil_output"
        if not vigil_out.exists():
            continue

        # Find diff files
        proposals = vigil_out / "output" / "proposals"
        diffs = list(proposals.glob("*.diff")) if proposals.exists() else []
        if not diffs:
            scores.append({"instance_id": instance_id, "diff_produced": False, "overall": 0.0})
            continue

        # Load thorn causes from emobank
        emo_path = vigil_out / "db" / "emobank" / "emotions.jsonl"
        thorn_causes = load_thorn_causes(emo_path)
        emo_rows = sum(1 for _ in open(emo_path)) if emo_path.exists() else 0

        # Score the best diff (last one = most recent)
        diff_text = open(diffs[-1], encoding="utf-8", errors="ignore").read()
        s = score_diff(diff_text, thorn_causes, emo_rows)
        s["instance_id"] = instance_id
        s["diff_produced"] = True
        s["emo_rows"] = emo_rows
        s["thorn_count"] = len(thorn_causes)
        scores.append(s)

        print(f"  {instance_id[:45]:<45} overall={s['overall']:.3f} "
              f"ground={s['grounding']:.2f} spec={s['specificity']:.2f} "
              f"nontrivial={s['non_trivial']:.2f}")

    return scores


def main():
    parser = argparse.ArgumentParser(description="Score VIGIL diff quality")
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output", default="diff_quality.json")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    print(f"\nScoring diffs in {results_dir}...\n")
    scores = score_results_dir(results_dir)

    produced = [s for s in scores if s.get("diff_produced")]
    if produced:
        overall_scores = [s["overall"] for s in produced]
        grounding_scores = [s["grounding"] for s in produced]
        print(f"\n{'='*60}")
        print(f"DIFF QUALITY SUMMARY ({len(produced)}/{len(scores)} produced diffs)")
        print(f"{'='*60}")
        print(f"  Overall score:    {statistics.mean(overall_scores):.3f} ± {statistics.stdev(overall_scores) if len(overall_scores)>1 else 0:.3f}")
        print(f"  Grounding score:  {statistics.mean(grounding_scores):.3f}")
        print(f"  Min / Max:        {min(overall_scores):.3f} / {max(overall_scores):.3f}")
        print(f"{'='*60}")

    with open(args.output, "w") as f:
        json.dump({
            "per_instance": scores,
            "aggregate": {
                "n_total": len(scores),
                "n_diff_produced": len(produced),
                "diff_production_rate": round(len(produced)/max(len(scores),1), 3),
                "mean_overall": round(statistics.mean([s["overall"] for s in produced]), 3) if produced else 0,
                "std_overall":  round(statistics.stdev([s["overall"] for s in produced]), 3) if len(produced)>1 else 0,
                "mean_grounding": round(statistics.mean([s["grounding"] for s in produced]), 3) if produced else 0,
            }
        }, f, indent=2)
    print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()