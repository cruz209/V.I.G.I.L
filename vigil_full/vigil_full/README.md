# VIGIL — Verifiable Inspection and Guarded Iterative Learning

A reflective runtime for self-healing LLM agents.

## Structure

```
vigil_full/
├── robin_b_project_scaffold/   ← VIGIL core (EmoBank, RBT, orchestrator)
│   └── robin_b/
│       ├── b_core/             ← appraise.py, emobank.py
│       ├── runtime/            ← b_reflect, b_diagnose, b_prompt, b_propose
│       └── RobinBAgent/        ← orchestrator.py (stage machine + LLM pipeline)
├── broken_agents/              ← 20 intentionally broken agent codebases
│   ├── robin_a/ ... robin_webhook/
│   └── run_broken_agents.py   ← main eval driver
└── vigil_swe_eval/             ← SWE-bench integration + ablation + baselines
    ├── swe_adapter.py
    ├── swe_runner.py
    ├── ablation.py
    └── baselines.py
```

## Quick Start

```bash
pip install openai agents
export OPENAI_API_KEY=sk-...
export LLM_MODEL=gpt-5.5

# Run one broken agent through full VIGIL pipeline
python broken_agents/run_broken_agents.py --agent robin_a

# Run all 20
python broken_agents/run_broken_agents.py

# Dry run (no LLM calls, just generate logs)
python broken_agents/run_broken_agents.py --dry-run

# Ablation study
python -m vigil_swe_eval.cli ablation --eval-artifacts eval_artifacts/eval_artifacts/

# Baseline comparison
python -m vigil_swe_eval.cli baseline --eval-artifacts eval_artifacts/eval_artifacts/
```

## Paper

arXiv: https://arxiv.org/abs/2512.07094
GitHub: https://github.com/cruz209/V.I.G.I.L
