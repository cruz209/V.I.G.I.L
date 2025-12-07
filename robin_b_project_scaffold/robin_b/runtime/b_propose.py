# robin_b/runtime/b_propose.py
from __future__ import annotations
import os, json, difflib, re, datetime as dt
from typing import List, Tuple, Optional, Dict, Any, Callable

# --- tools from runtime / core (stay within this package) ---
from .b_reflect import run_reflection
from ..b_core.emobank import recall_recent as _recall_emotions
from .b_diagnose import roses_buds_thorns
from .b_review import review_codebase
from .b_prompt import generate_new_prompt
from .events_log import fetch_recent_events

# ======================================================================
# Filesystem helpers
# ======================================================================

def _ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def _load(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _write(path: str, txt: str):
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

def _now_stamp() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

# ======================================================================
# Prompt PROPOSAL (read-only artifact) — kept for completeness
# ======================================================================

ADAPTIVE_RE = re.compile(r"(## BEGIN_ADAPTIVE_SECTION)(.*?)(## END_ADAPTIVE_SECTION)", re.DOTALL)

def _render_adaptive_block_for_proposal(cue: str,
                                        rbt_rules: Optional[List[str]] = None,
                                        rbt: Optional[Dict[str, Any]] = None) -> str:
    base = [
        "I will improve reliability based on my recent reflection:",
        "1) Convert all scheduled times to UTC before saving.",
        "2) After scheduling, wait up to 3s for an emit receipt; if none, retry once with 100–300ms jitter.",
        "3) Only show a “Reminder set” toast after I receive the receipt.",
        "4) If the user’s time is ambiguous, restate the exact UTC timestamp I intend to use.",
        "5) Log scheduled_utc, receipt_lag_ms, and retry.",
    ]
    extra = [f"- {r}" for r in (rbt_rules or [])]

    plan_lines = []
    if rbt:
        plan_lines.append("## BEGIN_RBT_PLAN")
        for tag in ("roses", "buds", "thorns"):
            items = (rbt.get(tag) or [])[:8]
            plan_lines.append(f"{tag.capitalize()}:")
            if not items:
                plan_lines.append("  - none")
            else:
                for it in items:
                    cause = it.get("cause", "")
                    emo   = it.get("emotion", "")
                    inten = it.get("intensity", 0.0)
                    plan_lines.append(f"  - cause: {cause} | emotion: {emo} | intensity: {inten:.2f}")
        rules = rbt.get("prompt_rules_to_add") or []
        plan_lines.append("Actions:")
        if rules:
            for r in rules[:12]:
                plan_lines.append(f"  - {r}")
        else:
            plan_lines.append("  - no-op")
        plan_lines.append("## END_RBT_PLAN")

    tail = [f"Note to self: {cue}"] if cue else []
    return "\n" + "\n".join(base + extra) + ("\n\n" + "\n".join(plan_lines) if plan_lines else "") + ("\n" + "\n".join(tail) + "\n")

def propose_prompt_patch(agent_prompt_path: str,
                         cue: str = "",
                         rbt_rules: Optional[List[str]] = None,
                         rbt: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Reads agent_prompt_path and writes a *proposed* updated prompt file + PR summary under output/proposals/.
    DOES NOT modify the original file. Returns (proposed_file_path, pr_markdown).
    """
    src = _load(agent_prompt_path)
    adaptive_block = _render_adaptive_block_for_proposal(cue, rbt_rules, rbt)

    def _repl(m):  # keep sentinels, swap content
        return m.group(1) + "\n" + adaptive_block + m.group(3)

    new, n = ADAPTIVE_RE.subn(_repl, src, count=1)
    if n == 0:
        new = src + "\n\n## BEGIN_ADAPTIVE_SECTION\n" + adaptive_block + "## END_ADAPTIVE_SECTION\n"

    ts = _now_stamp()
    patch_path = f"output/proposals/prompt_{ts}.txt"
    _write(patch_path, new)

    diag = ""
    if rbt and "diagnosis" in rbt:
        diag = rbt["diagnosis"]

    pr_md = f"""# Why I’m proposing this
Reflection indicates reliability opportunities. {('RBT: ' + diag) if diag else ''}

# What I’m changing
- Update the adaptive prompt with UTC conversion, receipt gating, jittered retry.
- Include RBT preamble/plan and rules derived from the day’s Roses/Buds/Thorns.

# How to review/apply
- File to update: {agent_prompt_path}
- Replace the ADAPTIVE section with the proposed block.
- Guardrail: leave CORE_IDENTITY untouched.

# Risks
- None (prompt-only change).
"""
    _write(f"output/proposals/PR_{ts}_prompt.md", pr_md)
    _append_audit(ts, [agent_prompt_path], "Proposed adaptive prompt update (UTC + receipt + RBT).")
    return patch_path, pr_md

# ======================================================================
# Code PROPOSAL engine (read-only unified diffs)
# ======================================================================

# Transform function signature: (src_text, abs_path, context) -> new_text
TransformFn = Callable[[str, str, Dict[str, Any]], str]

def _already_has(text: str, needle: str) -> bool:
    return needle in text

def _inject_reminders_patch(src: str) -> str:
    """
    Legacy heuristic: timezone awareness, receipt wait (<=3s), single retry with 100–300ms jitter,
    async-capable emit with sync fallback, optional receipts/ids shims.
    Preserved for backward compatibility; strategies can supply custom transforms instead.
    """
    out = src

    # Ensure imports
    if not _already_has(out, "timezone"):
        out = out.replace(
            "from datetime import datetime, timedelta",
            '''from datetime import datetime, timedelta, timezone
import random
try:
    import receipts, ids
except Exception:  # optional deps
    receipts = None
    class _Ids:
        def new(self, prefix):
            return f"{prefix}-" + datetime.utcnow().strftime("%Y%m%d%H%M%S")
    ids = _Ids()
''')

    # Insert helpers if missing
    if not _already_has(out, "def _aware("):
        out = out.replace(
            "def schedule_toast(when_local, payload):",
            '''def _aware(dt):
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

async def _await_receipt(toast_id, timeout_sec=3.0):
    if receipts is None or not hasattr(receipts, "wait_for"):
        return True  # no receipts infra; assume success
    try:
        return await receipts.wait_for(toast_id, timeout=timeout_sec)
    except TimeoutError:
        return False

def schedule_toast(when_local, payload):
''')

    # Replace enqueue line with UTC + jitter + receipt gating
    if "scheduler.enqueue_at(when_local, emit_toast, payload)" in out and not _already_has(out, "scheduled_utc"):
        out = out.replace(
            "scheduler.enqueue_at(when_local, emit_toast, payload)",
            '''when_utc = _aware(when_local)
    jitter_ms = random.randint(100, 300)
    when_utc = when_utc + timedelta(milliseconds=jitter_ms)
    toast_id = payload.get("id") or ids.new("toast")

    async def _emit_async():
        emit_toast({**payload, "id": toast_id, "scheduled_utc": when_utc.isoformat()})
        ok = await _await_receipt(toast_id)
        if not ok:
            emit_toast({**payload, "id": toast_id, "retry": True})

    # Fallback if scheduler can't run async callables:
    def _emit_sync():
        import asyncio
        asyncio.run(_emit_async())

    try:
        scheduler.enqueue_at(when_utc, _emit_async)
    except TypeError:
        scheduler.enqueue_at(when_utc, _emit_sync)'''
        )

    return out

def propose_code_patch(target_repo_root: str,
                       filename: str,
                       evidence: Optional[Dict[str, Any]] = None,
                       transform_fn: Optional[TransformFn] = None) -> str:
    """
    Write a unified diff suggestion to output/proposals/*.diff for the given file.
    - Never writes into target_repo_root.
    - If transform_fn is provided, use it to compute the proposed text. Otherwise, fallback to
      legacy _inject_reminders_patch for .py files named 'reminders.py'.
    Returns path to the diff ("" if target not found).
    """
    src_path = os.path.join(target_repo_root, filename)
    try:
        src = _load(src_path)
    except FileNotFoundError:
        return ""

    context = {"repo_root": target_repo_root, "filename": filename, "evidence": evidence or {}}

    if transform_fn:
        target = transform_fn(src, src_path, context)
    else:
        target = src
        if filename.endswith(".py") and os.path.basename(filename) == "reminders.py":
            target = _inject_reminders_patch(src)

    diff = difflib.unified_diff(
        src.splitlines(keepends=True),
        target.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}"
    )
    ts = _now_stamp()
    out_path = f"output/proposals/patch_{ts}.diff"
    _write(out_path, "".join(diff))

    # Evidence-aware PR note
    lag_note = ""
    if evidence:
        avg = evidence.get("delay_avg_s")
        cnt = evidence.get("delay_count")
        if isinstance(avg, (int, float)) and isinstance(cnt, int):
            lag_note = f" Observed avg delay ~{int(avg)}s over {cnt} events."

    no_op = (src == target)
    pr_md = f"""# Why I’m proposing this
Automated remediation suggestion based on logs and code scan.{lag_note}

# How to review/apply
- Scope: 1 file (`{filename}`).
- Apply: `git apply {out_path}`
- Rollback: `git apply -R {out_path}`.

# Note
{"No-op (pattern not found or no change deemed necessary)." if no_op else "Changes are proposed below in the diff."}
"""
    _write(f"output/proposals/PR_{ts}_code.md", pr_md)
    _append_audit(ts, [filename], ("No-op (no changes)" if no_op else "Proposed automated transform."))
    return out_path

# ======================================================================
# Audit log
# ======================================================================

def _append_audit(ts: str, paths: list, summary: str):
    _ensure_dir("logs/proposals.jsonl")
    with open("logs/proposals.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": ts,
            "persona": "robin_a",
            "affected_paths": paths,
            "emotion_trigger": "frustration",
            "evidence": ["reminder.toast delays present"],
            "summary": summary
        }) + "\n")

# ======================================================================
# Strategy engine — dynamic, log-driven transforms (no hard-coded files)
# ======================================================================

class Strategy:
    """
    A remediation strategy that inspects logs/RBT + code index and produces
    transform functions for specific files (read-only proposals).
    """
    name = "base"

    def match(self, rbt: Dict[str, Any], events: List[Dict], code_findings: List[Dict]) -> float:
        """Return relevance score 0..1 for today's context."""
        return 0.0

    def targets(self, repo_root: str, code_findings: List[Dict]) -> List[str]:
        """Default: all .py files found by scanner (deduped, order preserved)."""
        rels: List[str] = []
        for f in code_findings:
            p = f.get("path") or ""
            if p.startswith(repo_root) and p.endswith(".py"):
                rels.append(os.path.relpath(p, repo_root))
        seen, out = set(), []
        for r in rels:
            if r not in seen:
                seen.add(r); out.append(r)
        return out

    def transform(self, rel_filename: str) -> TransformFn:
        def _no_change(src: str, _abs: str, _ctx: Dict[str, Any]) -> str:
            return src
        return _no_change

STRATEGIES: List[Strategy] = []
def register(strategy: Strategy):
    STRATEGIES.append(strategy)

# --- Strategy A: timezone normalization + receipt gating when delays/fails appear ---
class TZReceiptStrategy(Strategy):
    name = "timezone_receipt"

    def match(self, rbt: Dict[str, Any], events: List[Dict], code_findings: List[Dict]) -> float:
        has_thorn = any(("delay" in (t.get("cause","")) or "fail" in (t.get("cause",""))) for t in rbt.get("thorns", []))
        saw_sched = any("enqueue_at" in (f.get("preview","")) for f in code_findings)
        return 0.9 if (has_thorn and saw_sched) else 0.0

    def transform(self, rel_filename: str) -> TransformFn:
        def _xform(src: str, abs_path: str, ctx: Dict[str, Any]) -> str:
            text = src
            changed = False

            # ensure timezone/random imports (tolerant)
            if "timezone" not in text:
                text = re.sub(r"from\s+datetime\s+import\s+([^\n]+)",
                              lambda m: "from datetime import " + ", ".join(sorted(set([x.strip() for x in m.group(1).split(",")] + ["timezone"]))),
                              text, count=1)
                if "timezone" not in text:
                    text = "from datetime import datetime, timedelta, timezone\n" + text
                changed = True
            if "import random" not in text:
                text = "import random\n" + text
                changed = True

            # receipts/ids shim
            if "ids = _Ids()" not in text and "import receipts" not in text:
                shim = (
                    "\ntry:\n"
                    "    import receipts, ids\n"
                    "except Exception:\n"
                    "    receipts = None\n"
                    "    class _Ids:\n"
                    "        def new(self, prefix):\n"
                    "            return f\"{prefix}-\" + datetime.utcnow().strftime(\"%Y%m%d%H%M%S\")\n"
                    "    ids = _Ids()\n"
                )
                text = shim + text
                changed = True

            # helpers
            if "def _aware(" not in text:
                helpers = (
                    "\n\ndef _aware(dt):\n"
                    "    if getattr(dt, 'tzinfo', None) is None:\n"
                    "        return dt.replace(tzinfo=timezone.utc)\n"
                    "    return dt.astimezone(timezone.utc)\n"
                    "\nasync def _await_receipt(toast_id, timeout_sec=3.0):\n"
                    "    if receipts is None or not hasattr(receipts, 'wait_for'):\n"
                    "        return True\n"
                    "    try:\n"
                    "        return await receipts.wait_for(toast_id, timeout=timeout_sec)\n"
                    "    except TimeoutError:\n"
                    "        return False\n"
                )
                text = helpers + text
                changed = True

            # rewrite common enqueue patterns (generic)
            patterns = [
                r"scheduler\.enqueue_at\(\s*(?P<when>[a-zA-Z_][\w\.]*),\s*(?P<emit>[a-zA-Z_][\w\.]*),\s*(?P<payload>[a-zA-Z_][\w\.]*)\s*\)",
                r"scheduler\.enqueue_at\(\s*(?P<when>[a-zA-Z_][\w\.]*),\s*(?P<emit>[a-zA-Z_][\w\.]*)\s*\)"
            ]
            repl = (
                "when_utc = _aware(\\g<when>)\n"
                "jitter_ms = random.randint(100, 300)\n"
                "when_utc = when_utc + timedelta(milliseconds=jitter_ms)\n"
                "toast_id = (\\g<payload>.get('id') if isinstance(\\g<payload>, dict) else None) if 'payload' in locals() else None\n"
                "toast_id = toast_id or ids.new('toast')\n"
                "\n"
                "async def _emit_async():\n"
                "    payload = \\g<payload> if 'payload' in locals() and isinstance(\\g<payload>, dict) else {}\n"
                "    payload = {**payload, 'id': toast_id, 'scheduled_utc': when_utc.isoformat()}\n"
                "    \\g<emit>(payload)\n"
                "    ok = await _await_receipt(toast_id)\n"
                "    if not ok:\n"
                "        \\g<emit>({**payload, 'retry': True})\n"
                "\n"
                "def _emit_sync():\n"
                "    import asyncio\n"
                "    try:\n"
                "        loop = asyncio.get_running_loop()\n"
                "        loop.create_task(_emit_async())\n"
                "    except RuntimeError:\n"
                "        asyncio.run(_emit_async())\n"
                "\n"
                "try:\n"
                "    scheduler.enqueue_at(when_utc, _emit_async)\n"
                "except TypeError:\n"
                "    scheduler.enqueue_at(when_utc, _emit_sync)"
            )
            applied = 0
            for pat in patterns:
                new_text, n = re.subn(pat, repl, text, count=1)
                if n:
                    text = new_text; applied += n
            changed = changed or (applied > 0)
            return text
        return _xform

register(TZReceiptStrategy())

# --- Strategy B: generic bounded-retry when tool/API errors spike ---
class RetryErrorsStrategy(Strategy):
    name = "retry_errors"

    def match(self, rbt: Dict[str, Any], events: List[Dict], _findings: List[Dict]) -> float:
        return 0.7 if any(("fail" in t.get("cause","")) or ("timeout" in t.get("cause","")) for t in rbt.get("thorns", [])) else 0.0

    def transform(self, rel_filename: str) -> TransformFn:
        def _xform(src: str, _abs: str, _ctx: Dict[str, Any]) -> str:
            text = src
            changed = False

            if "def _with_retry(" not in text:
                helper = (
                    "\n\ndef _with_retry(fn, *args, **kwargs):\n"
                    "    import time\n"
                    "    for i in range(2):  # 1 retry\n"
                    "        try:\n"
                    "            return fn(*args, **kwargs)\n"
                    "        except Exception:\n"
                    "            if i == 0:\n"
                    "                time.sleep(0.2)\n"
                    "            else:\n"
                    "                raise\n"
                )
                text = helper + text
                changed = True

            # Heuristic wrap for common call sites
            call_pats = [
                r"(\btool\.call\()",
                r"(\bclient\.[a-zA-Z_][\w]*\()",
                r"(\bapi\.[a-zA-Z_][\w]*\()"
            ]
            for pat in call_pats:
                new_text, n = re.subn(pat, r"_with_retry(\1", text)
                if n:
                    text = new_text
                    changed = True

            return text
        return _xform

register(RetryErrorsStrategy())

# ======================================================================
# PROPOSE == SUGGEST (single workflow tool)
# ======================================================================

def propose(
    logs_path: str,
    *,
    repo_root: str,
    agent_prompt_relpath: Optional[str] = None,
    window_hours: int = 24,
    apply_prompt: bool = True,
) -> Dict[str, Any]:
    """
    End-to-end workflow (stays in runtime):
      1) Process logs + update EmoBank (reflection)
      2) Diagnose with Roses/Buds/Thorns (latest EB + recent events)
      3) Study Robin-A:
         - Prompt: APPLY adaptive update (allowed; guardrails keep CORE_IDENTITY)
         - Code: SUGGEST diffs only (emit unified diffs to output/proposals/)
    """
    # 1) Reflect (appraise -> deposit_with_policy -> summarize)
    rec = run_reflection(window_hours=window_hours, logs_path=logs_path)

    # 2) RBT from latest EB + events
    emotions = _recall_emotions(50)
    events = fetch_recent_events(path=logs_path, window_hours=window_hours, limit=500)
    rbt = roses_buds_thorns(emotions, events)

    # 3a) Prompt update (allowed; guardrails enforce CORE_IDENTITY immutability)
    prompt_info: Dict[str, Any] = {"path": None, "note": None}
    if agent_prompt_relpath:
        target_prompt = os.path.join(repo_root, agent_prompt_relpath)
        try:
            with open(target_prompt, "r", encoding="utf-8") as f:
                current_prompt = f.read()
            new_prompt, _adaptive = generate_new_prompt(
                current_prompt=current_prompt,
                cue=rec.get("cue", ""),
                guardrails=True,
                rbt_rules=rbt.get("prompt_rules_to_add"),
                rbt=rbt,
            )
            if apply_prompt:
                with open(target_prompt, "w", encoding="utf-8") as f:
                    f.write(new_prompt)
                prompt_info = {"path": target_prompt, "note": "Applied prompt update to Robin A."}
            else:
                os.makedirs("output", exist_ok=True)
                preview = os.path.join("output", "new_prompt_preview.txt")
                with open(preview, "w", encoding="utf-8") as f:
                    f.write(new_prompt)
                prompt_info = {"path": preview, "note": "Wrote prompt preview (not applied)."}
        except Exception as e:
            prompt_info = {"path": target_prompt, "note": f"Prompt update failed: {e}"}

    # 3b) Code suggestions (read-only): scan repo and apply strategy transforms as diffs
    findings = review_codebase(os.path.join(repo_root, "**", "*.py"))

    # Rank strategies by relevance today
    ranked: List[Tuple[float, Strategy]] = sorted(
        ((s.match(rbt, events, findings), s) for s in STRATEGIES),
        key=lambda x: -x[0]
    )

    code_artifacts: List[Tuple[str, str]] = []

    for score, strat in ranked:
        if score <= 0:
            continue
        # Choose targets from the scan (no hard-coded names)
        for rel in strat.targets(repo_root, findings):
            diff_path = propose_code_patch(
                target_repo_root=repo_root,
                filename=rel,
                evidence={"strategy": strat.name, "score": score},
                transform_fn=strat.transform(rel)
            )
            code_artifacts.append((f"{strat.name}:{rel}", diff_path or "no-op"))

    return {
        "repo_root": repo_root,
        "reflection": {"diagnosis": rec.get("diagnosis"), "cue": rec.get("cue")},
        "rbt_counts": {
            "roses": len(rbt.get("roses", [])),
            "buds": len(rbt.get("buds", [])),
            "thorns": len(rbt.get("thorns", [])),
        },
        "prompt": prompt_info,
        "code_suggestions": code_artifacts,
        "strategies_considered": [s.name for _sc, s in ranked],
        "hotspots_considered": sum(1 for f in findings if (f.get("path") or "").endswith(".py")),
    }
