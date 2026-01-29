
---

````markdown
# 🧠 Robin B — Reflective Agentic Maintainer

**Robin B** is a self-reflective AI runtime that analyzes behavioral logs, diagnoses systemic issues through an *Emotional Bank* (Roses 🌹 / Buds 🌱 / Thorns 🌵), and autonomously proposes prompt and code updates for sibling agents such as **Robin A**.

> A closed reflective-AI system for autonomous agent maintenance, reliability optimization, and prompt self-adaptation.

---

## ✨ Core Capabilities

- **Emotional Bank Reflection:** converts runtime events into affective entries → Roses / Buds / Thorns.
- **RBT Diagnosis:** interprets emotional context into actionable reliability rules.
- **Prompt Adaptation:** rewrites only the `## BEGIN_ADAPTIVE_SECTION` of an agent’s prompt under strict guardrails.
- **Code Proposal Engine:** scans the target repository, synthesizes safe diffs, and emits PR-ready artifacts.
- **Stage-Locked Orchestration:** deterministic pipeline ensures each phase completes in sequence.
- **Audit Trail:** every reflection and proposal is persisted to `logs/` and `output/proposals/`.

---

## 🏗️ Architecture Overview

```text
robin_b_project_scaffold/
│
├── robin_b/
│   ├── runtime/
│   │   ├── b_reflect.py     ← reads logs & updates EmoBank
│   │   ├── b_diagnose.py    ← derives Roses / Buds / Thorns
│   │   ├── b_prompt.py      ← generates adaptive prompt patches
│   │   ├── b_propose.py     ← code diff & PR generator
│   │   ├── b_review.py      ← scans repository for hotspots
│   │   └── common.py, events_log.py
│   ├── b_core/
│   │   ├── emobank.py       ← persistent emotional ledger
│   │   └── appraise.py      ← event → emotion mapping
│   └── agents/
│       └── orchestrator.py  ← coordinates reflection stages
│
├── sample_a_repo/           ← Robin A (MCP agent under observation)
│   ├── agent.py             ← Agent definition (OpenAI Agents SDK)
│   └── reminders.py         ← FastAPI-MCP reminder service
│
├── logs/
│   └── events.jsonl         ← chat + runtime traces (input)
├── output/
│   └── proposals/           ← diffs and PR markdown (output)
└── run_robin_b.py           ← entry point for full reflection cycle
````



---

## ⚙️ Local Execution (Internal Workflow)

### 1️⃣ Run Robin A

```bash
python sample_a_repo/reminders.py
```

Starts the MCP reminder tool on `http://127.0.0.1:8000`.

### 2️⃣ Generate Logs

Interact with Robin A (e.g., schedule reminders).
This populates `logs/events.jsonl` with behavioral traces.

### 3️⃣ Run Robin B Reflection Cycle

```bash
python run_robin_b.py
```

**Expected artifacts:**

```
output/
 ├── new_prompt.txt
 └── proposals/
     ├── LLM_patch_<timestamp>.diff
     └── LLM_PR_<timestamp>.md
```

---

## 🧩 Process Summary

| Stage                   | Module         | Description                                |
| ----------------------- | -------------- | ------------------------------------------ |
| 1️⃣ update_eb_from_logs | `b_reflect`    | Reads logs, deposits emotions to EmoBank.  |
| 2️⃣ diagnose_rbt        | `b_diagnose`   | Builds Roses/Buds/Thorns and prompt rules. |
| 3️⃣ build_prompt_patch  | `b_prompt`     | Generates new adaptive prompt block.       |
| 4️⃣ emit_unified_diff   | `b_propose`    | Synthesizes `.diff` patch suggestions.     |
| 5️⃣ save_proposal       | `orchestrator` | Persists PR summary and diff artifacts.    |

---

## 🧠 Example Reflection Outcome

```
Cue: Gate toasts on receipts; log receipt_lag_ms.
Diagnosis: Reminder latency elevated — enforce UTC + receipt gating.
Proposed Patch: utils/receipt_gating.py (bounded receipt gating helper)
```

---

## 🧩 Extending Robin B (Internal Use Only)

* **Add new Strategy:**
  Implement a `Strategy` subclass in `b_propose.py` with `match()` and `transform()` methods.
* **Modify Emotional Policy:**
  Adjust `appraise.py` or `emobank.py` to redefine emotion types or weighting.

---

## 🧬 System Intent

Robin B demonstrates a fully contained *Reflective Maintenance Loop*:

```
Operational Logs → Emotional Bank → Diagnosis → Prompt/Code Proposal → Audit Output
```

This architecture enables controlled self-improvement without compromising core identity or safety boundaries.

---



## 🧾 Maintainer

**Christopher Cruz**
AI Infrastructure / Reflective Systems Research
