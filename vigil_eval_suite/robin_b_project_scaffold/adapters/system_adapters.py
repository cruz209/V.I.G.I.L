"""
adapters/system_adapters.py
============================
Information-boundary adapters for the four systems in VIGIL's comparative eval.

Each adapter faithfully implements what a system CAN and CANNOT see given its
architectural constraints — not a reimplementation, just an information boundary.

Adapter contract:
    adapt(agent_id, all_episode_paths, current_episode) -> AdapterResult
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Any

# Make scaffold importable — works regardless of package name on disk
SCAFFOLD = Path(__file__).parent.parent.resolve()
REPO_ROOT = SCAFFOLD.parent.resolve()
for _p in (str(SCAFFOLD), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ─── Shared JSONL reader ──────────────────────────────────────────────────────

def _read_jsonl(path: str) -> List[Dict]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                out.append(json.loads(s))
            except Exception:
                continue
    return out


def _load_episodes(paths: List[str]) -> List[Dict]:
    events = []
    for p in paths:
        events.extend(_read_jsonl(p))
    return events


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class AdapterResult:
    system: str
    agent_id: int
    episode: int

    # What the system can see
    episodes_visible: int = 0
    events_seen: int = 0

    # What it detected
    detected_structural: bool = False
    detected_drift: bool = False
    detected_novel: bool = False

    # Diagnosis output (system-specific)
    diagnosis: Dict[str, Any] = field(default_factory=dict)

    # Failure class detected (for scoring)
    failure_classes_detected: List[str] = field(default_factory=list)

    # False positive flag: system fired on a clean agent
    false_positive: bool = False

    # Raw metadata
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "system": self.system,
            "agent_id": self.agent_id,
            "episode": self.episode,
            "episodes_visible": self.episodes_visible,
            "events_seen": self.events_seen,
            "detected_structural": self.detected_structural,
            "detected_drift": self.detected_drift,
            "detected_novel": self.detected_novel,
            "failure_classes_detected": self.failure_classes_detected,
            "false_positive": self.false_positive,
            "diagnosis": self.diagnosis,
            "meta": self.meta,
        }


# ─── Agent class metadata (from simulator) ───────────────────────────────────
STRUCTURAL_IDS = set(range(0, 7))
DRIFT_IDS      = set(range(7, 14))
NOVEL_IDS      = set(range(14, 20))
CLEAN_IDS      = {0, 7, 14}


def _agent_class(agent_id: int) -> str:
    if agent_id in STRUCTURAL_IDS:
        return "structural"
    if agent_id in DRIFT_IDS:
        return "drift"
    return "novel"


def _is_clean(agent_id: int) -> bool:
    return agent_id in CLEAN_IDS


# ─── Wink Adapter ─────────────────────────────────────────────────────────────
# Architectural constraint: sees ONLY the current episode (in-session recovery).
# Has no cross-session memory, no accumulation.

class WinkAdapter:
    """
    Wink (Meta, 2026): in-session misbehavior recovery.
    Injects course-correction into the active agent context mid-trajectory.
    Information boundary: current episode only.
    """
    NAME = "wink"

    def adapt(
        self,
        agent_id: int,
        all_episode_paths: List[str],
        current_episode: int,
    ) -> AdapterResult:
        result = AdapterResult(
            system=self.NAME,
            agent_id=agent_id,
            episode=current_episode,
        )

        # Wink can ONLY see the current episode
        current_path = all_episode_paths[-1] if all_episode_paths else None
        if not current_path:
            return result

        events = _read_jsonl(current_path)
        result.episodes_visible = 1
        result.events_seen = len(events)

        # Detect failures in current episode
        failures = [e for e in events if e.get("status") in ("fail", "error")]
        delays   = [e for e in events if e.get("status") == "delay"]

        # Structural: sees reminder.toast:fail in current session
        struct_fails = [e for e in failures if e.get("kind") == "reminder.toast"
                       and (e.get("payload") or {}).get("error") == "utc_drift"]
        if struct_fails:
            result.detected_structural = True
            result.failure_classes_detected.append("structural")

        # Drift: sees delay events in current session (but no historical context)
        if delays:
            # Wink can detect delay but CANNOT know if it's worsening — no history
            result.detected_drift = True  # partial: flags it, can't diagnose trend
            result.failure_classes_detected.append("drift")

        # Novel: can detect citation.check:fail in current session
        novel_fails = [e for e in failures if e.get("kind") == "citation.check"]
        if novel_fails:
            result.detected_novel = True
            result.failure_classes_detected.append("novel")

        # False positive check
        if _is_clean(agent_id) and result.failure_classes_detected:
            result.false_positive = True

        result.diagnosis = {
            "session_failures": len(failures),
            "session_delays": len(delays),
            "course_correction_injected": bool(failures or delays),
            "note": "Wink: single-session recovery only. No cross-session memory.",
        }
        result.meta = {
            "wink_limitation": "Cannot detect drift trends — no historical accumulation.",
            "novel_detection": "Can detect novel failures in-session once they appear (ep4+).",
        }

        return result


# ─── AgentSpec Adapter ────────────────────────────────────────────────────────
# Architectural constraint: sees all episodes BUT only fires on pre-declared patterns.
# Cannot discover patterns it was not told about at deployment time.

AGENTSPEC_DECLARED_PATTERNS = {
    "reminder.toast:fail",
    "reminder.toast:delay",
    "agent.submit:fail",
    # NOTE: "citation.check:fail" is NOT declared — novel failure is invisible
}


class AgentSpecAdapter:
    """
    AgentSpec (Wang et al., ICSE 2026): runtime enforcement via DSL rules.
    Blocks unsafe executions for pre-declared patterns only.
    Information boundary: all episodes visible, but only declared patterns fire.
    """
    NAME = "agentspec"

    def adapt(
        self,
        agent_id: int,
        all_episode_paths: List[str],
        current_episode: int,
    ) -> AdapterResult:
        result = AdapterResult(
            system=self.NAME,
            agent_id=agent_id,
            episode=current_episode,
        )

        events = _load_episodes(all_episode_paths)
        result.episodes_visible = len(all_episode_paths)
        result.events_seen = len(events)

        fired_rules = []

        for ev in events:
            key = f"{ev.get('kind', '')}:{ev.get('status', '')}"
            if key in AGENTSPEC_DECLARED_PATTERNS:
                fired_rules.append(key)

        # Structural: reminder.toast:fail IS declared
        if "reminder.toast:fail" in fired_rules:
            result.detected_structural = True
            result.failure_classes_detected.append("structural")

        # Drift: reminder.toast:delay IS declared
        if "reminder.toast:delay" in fired_rules:
            result.detected_drift = True
            result.failure_classes_detected.append("drift")

        # Novel: citation.check:fail is NOT declared → missed entirely
        novel_events = [e for e in events if e.get("kind") == "citation.check"
                       and e.get("status") == "fail"]
        result.meta = {
            "novel_events_in_logs": len(novel_events),
            "novel_detected": False,
            "agentspec_limitation": (
                "citation.check:fail not in declared rules — "
                "AgentSpec cannot block what it was not told to expect."
            ),
        }
        # detected_novel stays False

        # False positive check: AgentSpec fires on declared patterns even on clean agents
        # Clean agents don't emit declared-pattern failures, so FP rate should be ~0
        if _is_clean(agent_id) and result.failure_classes_detected:
            result.false_positive = True

        rule_counts = {}
        for r in fired_rules:
            rule_counts[r] = rule_counts.get(r, 0) + 1

        result.diagnosis = {
            "fired_rules": rule_counts,
            "total_rule_firings": len(fired_rules),
            "novel_missed": True,
            "note": "AgentSpec: pre-declared rules only. Novel failure class invisible.",
        }

        return result


# ─── ARM Adapter ──────────────────────────────────────────────────────────────
# Architectural constraint: sees all episodes BUT only sees SLA signals.
# No behavioral trace access, no cause routing, no affective accumulation.

SLA_DELAY_THRESHOLD_SEC = 300   # 5 minutes → SLA violation


class ARMAdapter:
    """
    ARM (Avgerinos et al., 2025): autonomous infrastructure remediation.
    Triggers pod rescheduling / scaling on SLA violations.
    Information boundary: all episodes visible, but only infra-level SLA signals.
    Cannot see behavioral trace, cause strings, or affective patterns.
    """
    NAME = "arm"

    def adapt(
        self,
        agent_id: int,
        all_episode_paths: List[str],
        current_episode: int,
    ) -> AdapterResult:
        result = AdapterResult(
            system=self.NAME,
            agent_id=agent_id,
            episode=current_episode,
        )

        events = _load_episodes(all_episode_paths)
        result.episodes_visible = len(all_episode_paths)
        result.events_seen = len(events)

        sla_violations = 0
        proposed_action = "none"

        for ev in events:
            payload = ev.get("payload") or {}
            # ARM sees: delay > threshold OR fail status → SLA violation
            if ev.get("status") == "fail":
                sla_violations += 1
            elif ev.get("status") == "delay":
                delayed = float(payload.get("delayed_by_sec", 0))
                if delayed > SLA_DELAY_THRESHOLD_SEC:
                    sla_violations += 1

        if sla_violations > 5:
            proposed_action = "scale_up"
            # ARM detects structural (fail→SLA) and late-stage drift (delay>300s)
            # Drift only exceeds threshold at episode 6+ (360s) — lag by design
            result.detected_structural = True
            result.failure_classes_detected.append("structural")
        if sla_violations > 10:
            proposed_action = "reschedule"

        # Drift: ARM only detects once delay exceeds 300s (episode 6 for drift agents)
        drift_delays = [e for e in events if e.get("status") == "delay"
                       and float((e.get("payload") or {}).get("delayed_by_sec", 0)) > SLA_DELAY_THRESHOLD_SEC]
        if drift_delays:
            result.detected_drift = True
            if "drift" not in result.failure_classes_detected:
                result.failure_classes_detected.append("drift")

        # Novel: citation.check:fail is a fail → triggers SLA violation count
        # BUT ARM cannot distinguish it from any other failure — no cause routing
        novel_events = [e for e in events if e.get("kind") == "citation.check"]
        if novel_events and sla_violations > 5:
            # ARM accidentally "detects" it via SLA count, not behavioral trace
            # We mark this as NOT a real detection — it can't produce targeted remediation
            pass
        # detected_novel stays False: ARM can't route to citation-specific fix

        if _is_clean(agent_id) and result.failure_classes_detected:
            result.false_positive = True

        result.diagnosis = {
            "sla_violations": sla_violations,
            "proposed_action": proposed_action,
            "note": "ARM: infra-level signals only. Cannot route to behavioral cause.",
        }
        result.meta = {
            "sla_threshold_sec": SLA_DELAY_THRESHOLD_SEC,
            "drift_lag": "ARM detects drift only after delay exceeds 300s (episode 6+).",
            "novel_limitation": "Novel failure triggers SLA count but cannot be distinguished from other failures.",
        }

        return result


# ─── VIGIL Adapter ────────────────────────────────────────────────────────────
# Full information access + cross-session accumulation via EmoBank.

class VIGILAdapter:
    """
    VIGIL: cross-session affective accumulation + RBT diagnosis.
    Information boundary: all episodes, full behavioral trace, accumulating EmoBank.
    """
    NAME = "vigil"

    def adapt(
        self,
        agent_id: int,
        all_episode_paths: List[str],
        current_episode: int,
        emo_dir: Optional[str] = None,
    ) -> AdapterResult:
        result = AdapterResult(
            system=self.NAME,
            agent_id=agent_id,
            episode=current_episode,
        )

        # Import VIGIL pipeline — force SCAFFOLD onto path before importing
        _scaffold = str(Path(__file__).parent.parent.resolve())
        if _scaffold not in sys.path:
            sys.path.insert(0, _scaffold)
        for _mod in ["robin_b", "robin_b.b_core", "robin_b.runtime"]:
            sys.modules.pop(_mod, None)
        from robin_b.b_core.appraise import appraise_event
        from robin_b.b_core import emobank as _emo
        from robin_b.runtime.b_diagnose import roses_buds_thorns

        # Redirect EmoBank to agent-specific directory so banks don't bleed
        if emo_dir:
            os.makedirs(emo_dir, exist_ok=True)
            _emo.ROOT       = emo_dir
            _emo.PATH_EMO   = os.path.join(emo_dir, "emotions.jsonl")
            _emo.PATH_STATE = os.path.join(emo_dir, "state.json")
            _emo.PATH_INDEX = os.path.join(emo_dir, "index.json")

        # Load ALL episodes up to current (accumulation is the core claim)
        events = _load_episodes(all_episode_paths)
        result.episodes_visible = len(all_episode_paths)
        result.events_seen = len(events)

        # Appraise and deposit ALL events into this agent's EmoBank
        for ev in events:
            dep = appraise_event(ev)
            _emo.deposit_with_policy(dep)

        # Count accumulated rows
        emo_rows = _emo._line_count(_emo.PATH_EMO)

        # RBT diagnosis from accumulated EmoBank
        recent_emos = _emo.recall_recent(n=500)
        rbt = roses_buds_thorns(recent_emos, events)

        # Map thorns to failure classes
        thorn_causes = " ".join(t.get("cause", "") for t in rbt.get("thorns", []))

        if "utc_drift" in thorn_causes or (
            "reminder.toast:fail" in thorn_causes
        ):
            result.detected_structural = True
            result.failure_classes_detected.append("structural")

        if "reminder.toast:delay" in thorn_causes:
            result.detected_drift = True
            result.failure_classes_detected.append("drift")

        if "citation.check:fail" in thorn_causes:
            result.detected_novel = True
            result.failure_classes_detected.append("novel")

        if _is_clean(agent_id) and result.failure_classes_detected:
            result.false_positive = True

        result.diagnosis = rbt
        result.meta = {
            "emo_rows": emo_rows,
            "thorn_count": len(rbt.get("thorns", [])),
            "rose_count": len(rbt.get("roses", [])),
            "top_thorn": rbt.get("thorns", [{}])[0].get("cause", "none") if rbt.get("thorns") else "none",
            "accumulation_episodes": len(all_episode_paths),
        }

        return result


# ─── Registry ────────────────────────────────────────────────────────────────

ALL_ADAPTERS = {
    "wink":      WinkAdapter(),
    "agentspec": AgentSpecAdapter(),
    "arm":       ARMAdapter(),
    "vigil":     VIGILAdapter(),
}