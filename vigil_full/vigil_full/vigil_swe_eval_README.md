# vigil-swe-eval

Real-world evaluation harness for [VIGIL](https://github.com/cruz209/V.I.G.I.L) — a reflective runtime for self-healing LLM agents.

## What this package provides

Three evaluation components designed to support rigorous empirical evaluation of VIGIL:

### 1. SWE-bench Integration (`vigil-swe swe`)
Runs VIGIL on real SWE-bench Verified instances:
1. Runs SWE-agent (baseline) on an instance → collects trajectory
2. Converts trajectory to VIGIL-schema JSONL events
3. Runs VIGIL pipeline → diagnosis + prompt patch + diff
4. Re-runs SWE-agent with VIGIL's patched prompt
5. Reports whether patching improved resolve rate

### 2. Ablation Study (`vigil-swe ablation`)
Compares three conditions on the same synthetic event logs:
- **Full VIGIL** — EmoBank + affective appraisal + exponential decay + coalescing
- **No decay** — EmoBank without temporal decay (flat intensity)
- **Counter only** — Simple failure rate counter, no EmoBank, no emotions

Reports soft failure detection as the key differentiating metric (delays, latency creep, premature confirmations).

### 3. Baseline Comparison (`vigil-swe baseline`)
Compares VIGIL against:
- **Threshold monitor** — fires when failure rate > 20%, generic output
- **Rule-based monitor** — LangSmith-style predefined rules for each (kind, status) pattern

Scores on: thorn detection rate, soft failure detection, false positive rate, prompt rule relevance, remediation specificity.

## Installation

```bash
pip install vigil-swe-eval
```

## Quick Start

```bash
# Run ablation on existing eval_artifacts
vigil-swe ablation --eval-artifacts eval_artifacts/eval_artifacts/

# Run baseline comparison
vigil-swe baseline --eval-artifacts eval_artifacts/eval_artifacts/

# Run both
vigil-swe all --eval-artifacts eval_artifacts/eval_artifacts/

# Run SWE-bench real evaluation (requires OpenAI key + sweagent)
export OPENAI_API_KEY=sk-...
vigil-swe swe \
    --n-instances 50 \
    --model gpt-5.5 \
    --vigil-root /path/to/robin_b_project_scaffold/
```

## Python API

```python
from vigil_swe_eval import run_ablation, run_baseline_comparison, SWEAgentAdapter

# Ablation study
results = run_ablation(
    eval_artifacts_dir="eval_artifacts/eval_artifacts/",
    seeds=range(1, 6),
    output_path="ablation_results.json",
)

# Baseline comparison
results = run_baseline_comparison(
    eval_artifacts_dir="eval_artifacts/eval_artifacts/",
    output_path="baseline_comparison.json",
)

# Convert existing SWE-agent trajectory to VIGIL events
log_path = SWEAgentAdapter.from_existing_traj(
    traj_path="my_agent.traj",
    instance_id="django__django-11099",
    output_dir="vigil_logs/",
)

# Run SWE-agent + VIGIL pipeline on a new instance
adapter = SWEAgentAdapter(instance_id="django__django-11099", model="gpt-5.5")
log_path = adapter.run_and_convert(output_dir="vigil_logs/")
```

## Repository Structure

```
vigil_swe_eval/
├── swe_adapter.py    # SWE-agent trajectory → VIGIL JSONL converter
├── swe_runner.py     # End-to-end SWE-bench evaluation pipeline
├── ablation.py       # Ablation study (Full / No Decay / Counter)
├── baselines.py      # Baseline monitors (Threshold / Rule-Based)
└── cli.py            # CLI entry point (vigil-swe command)
```

## Event Schema

VIGIL expects JSONL events with this schema:

```json
{
  "ts": "2026-01-01T00:00:00Z",
  "kind": "tool.edit",
  "status": "fail",
  "payload": {
    "path": "src/utils.py",
    "error_snippet": "SyntaxError: invalid syntax",
    "step": 3
  }
}
```

SWE-agent action types are mapped to VIGIL kinds automatically:
- `str_replace_editor`, `edit_file` → `tool.edit`
- `bash`, `execute_bash` → `shell.exec`
- `pytest`, `run_tests` → `test.run`
- `submit`, `finish` → `agent.submit`

## Citation

```bibtex
@article{cruz2025vigil,
  title={VIGIL: A Reflective Runtime for Self-Healing LLM Agents},
  author={Cruz, Christopher},
  journal={arXiv preprint arXiv:2512.07094},
  year={2025}
}
```
