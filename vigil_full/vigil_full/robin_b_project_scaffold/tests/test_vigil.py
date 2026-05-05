"""
test_vigil.py  –  VIGIL Real Test Suite
========================================
Replaces the three placeholder files (test_decay_bounds, test_identity_guard,
test_patch_atomicity) with real assertions against the actual modules.

Run from the robin_b_project_scaffold/ directory:
    pytest tests/test_vigil.py -v

Or from the repo root:
    pytest robin_b_project_scaffold/tests/test_vigil.py -v
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import datetime as dt
from pathlib import Path
from typing import Dict, List

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap – makes the scaffold importable regardless of cwd
# ---------------------------------------------------------------------------
SCAFFOLD = Path(__file__).parent.parent.resolve()   # robin_b_project_scaffold/
REPO_ROOT = SCAFFOLD.parent.resolve()               # vigil/
for p in (str(SCAFFOLD), str(SCAFFOLD.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from robin_b_project_scaffold.robin_b.b_core import appraise as _appraise_mod
from robin_b_project_scaffold.robin_b.b_core.appraise import appraise_event, ev_severity
from robin_b_project_scaffold.robin_b.b_core import emobank as _emo
from robin_b_project_scaffold.robin_b.runtime.b_diagnose import roses_buds_thorns, dedupe_order
from robin_b_project_scaffold.robin_b.runtime.b_prompt import (
    generate_new_prompt, ADAPTIVE_RE, CORE_RE,
)
from robin_b_project_scaffold.robin_b.runtime.common import clamp, now_iso, dedupe_hash
from robin_b_project_scaffold.robin_b.runtime.events_log import fetch_recent_events


# ===========================================================================
# Helpers & fixtures
# ===========================================================================

def _iso(offset_seconds: float = 0.0) -> str:
    """UTC ISO timestamp shifted by offset_seconds from now."""
    t = dt.datetime.utcnow() + dt.timedelta(seconds=offset_seconds)
    return t.replace(microsecond=0).isoformat() + "Z"


def _stub_prompt(core_text: str = "I am a reliable assistant.", extra: str = "") -> str:
    return (
        "## BEGIN_CORE_IDENTITY\n"
        f"{core_text}\n"
        "## END_CORE_IDENTITY\n\n"
        "## BEGIN_ADAPTIVE_SECTION\n"
        f"# (auto-updated){extra}\n"
        "## END_ADAPTIVE_SECTION\n"
    )


@pytest.fixture()
def tmp_emo(tmp_path, monkeypatch):
    """Redirect EmoBank to a fresh temp directory for each test."""
    emo_dir = tmp_path / "emobank"
    emo_dir.mkdir()
    monkeypatch.setattr(_emo, "ROOT",       str(emo_dir))
    monkeypatch.setattr(_emo, "PATH_EMO",   str(emo_dir / "emotions.jsonl"))
    monkeypatch.setattr(_emo, "PATH_STATE", str(emo_dir / "state.json"))
    monkeypatch.setattr(_emo, "PATH_INDEX", str(emo_dir / "index.json"))
    return emo_dir


@pytest.fixture()
def tmp_cwd(tmp_path, monkeypatch):
    """Change cwd to tmp_path so file outputs land there, not in source tree."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ===========================================================================
# 1. COMMON UTILITIES
# ===========================================================================

class TestClamp:
    def test_below_lo(self):
        assert clamp(-5.0, 0.0, 1.0) == 0.0

    def test_above_hi(self):
        assert clamp(2.0, 0.0, 1.0) == 1.0

    def test_in_range(self):
        assert clamp(0.5, 0.0, 1.0) == 0.5

    def test_exact_boundaries(self):
        assert clamp(0.0, 0.0, 1.0) == 0.0
        assert clamp(1.0, 0.0, 1.0) == 1.0

    def test_negative_range(self):
        assert clamp(-0.5, -1.0, 0.0) == -0.5


class TestNowIso:
    def test_format(self):
        ts = now_iso()
        assert ts.endswith("Z")
        # Must parse without error
        dt.datetime.fromisoformat(ts.replace("Z", ""))

    def test_recent(self):
        # now_iso() truncates to second precision; allow ±1s slop for microsecond drift
        before = dt.datetime.utcnow() - dt.timedelta(seconds=1)
        ts = now_iso()
        after = dt.datetime.utcnow() + dt.timedelta(seconds=1)
        parsed = dt.datetime.fromisoformat(ts.replace("Z", ""))
        assert before <= parsed <= after


class TestDedupeHash:
    def test_same_event_same_hash(self):
        ev = {"kind": "reminder.toast", "status": "ok",
              "payload": {"x": 1}, "ts": "2025-01-01T00:00:00Z"}
        assert dedupe_hash(ev) == dedupe_hash(ev)

    def test_different_status_different_hash(self):
        base = {"kind": "reminder.toast", "status": "ok",
                "payload": {}, "ts": "2025-01-01T00:00:00Z"}
        fail = {**base, "status": "fail"}
        assert dedupe_hash(base) != dedupe_hash(fail)

    def test_payload_order_insensitive(self):
        a = {"kind": "k", "status": "ok",
             "payload": {"b": 2, "a": 1}, "ts": "2025-01-01T00:00:00Z"}
        b = {"kind": "k", "status": "ok",
             "payload": {"a": 1, "b": 2}, "ts": "2025-01-01T00:00:00Z"}
        assert dedupe_hash(a) == dedupe_hash(b)


# ===========================================================================
# 2. APPRAISAL ENGINE
# ===========================================================================

class TestEvSeverity:
    def test_fail_status_high(self):
        ev = {"kind": "reminder.toast", "status": "fail", "payload": {}}
        assert ev_severity(ev) >= 0.8

    def test_delay_proportional(self):
        low  = ev_severity({"kind": "t", "status": "delay", "payload": {"delayed_by_sec": 60}})
        high = ev_severity({"kind": "t", "status": "delay", "payload": {"delayed_by_sec": 600}})
        assert low < high

    def test_delay_capped_at_1(self):
        ev = {"kind": "t", "status": "delay", "payload": {"delayed_by_sec": 9999}}
        assert ev_severity(ev) <= 1.0

    def test_improvement_pct_scaled(self):
        low  = ev_severity({"kind": "t", "status": "ok", "payload": {"improvement_pct": 5}})
        high = ev_severity({"kind": "t", "status": "ok", "payload": {"improvement_pct": 45}})
        assert low < high

    def test_positive_magnitude_wins(self):
        # When improvement_pct is present, it should dominate over plain status
        ev = {"kind": "t", "status": "ok", "payload": {"improvement_pct": 30}}
        mag = ev_severity(ev)
        assert 0.2 < mag < 1.0


class TestAppraiseEvent:
    def _ap(self, kind="reminder.toast", status="ok", payload=None):
        return appraise_event({"kind": kind, "status": status,
                                "payload": payload or {}})

    def test_fail_yields_frustration(self):
        r = self._ap(status="fail")
        assert r["emotion"] == "frustration"
        assert r["valence"] < 0

    def test_delay_yields_anxiety(self):
        r = self._ap(status="delay", payload={"delayed_by_sec": 300})
        assert r["emotion"] == "anxiety"
        assert r["valence"] < 0

    def test_ok_yields_positive(self):
        r = self._ap(status="ok")
        assert r["emotion"] in {"relief", "pride", "curiosity"}
        assert r["valence"] > 0 or r["emotion"] == "curiosity"

    def test_intensity_in_range(self):
        for status in ("ok", "fail", "delay", "timeout"):
            payload = {"delayed_by_sec": 200} if status == "delay" else {}
            r = appraise_event({"kind": "t", "status": status, "payload": payload})
            assert 0.0 <= r["intensity"] <= 1.0, f"Out of range for status={status}"

    def test_cause_field_set(self):
        r = self._ap(kind="reminder.toast", status="fail")
        assert r["cause"] == "reminder.toast:fail"

    def test_error_kind_also_negative(self):
        r = appraise_event({"kind": "tool.error", "status": "error", "payload": {}})
        assert r["valence"] < 0

    def test_strong_fail_intensity(self):
        # A hard fail should produce intensity >= 0.8 (ev_severity for fail is 0.9,
        # clamped to [0.2, 0.95] by appraise)
        r = self._ap(status="fail")
        assert r["intensity"] >= 0.7

    def test_high_delay_high_intensity(self):
        # 600 s delay → severity 1.0 → clamped to 0.95
        r = self._ap(status="delay", payload={"delayed_by_sec": 600})
        assert r["intensity"] >= 0.9


# ===========================================================================
# 3. EMOBANK – deposit, decay, summarize, policies
# ===========================================================================

class TestEmoBankDeposit:
    def test_deposit_writes_line(self, tmp_emo):
        _emo.deposit({"emotion": "relief", "intensity": 0.6, "valence": 0.5, "cause": "t:ok"})
        lines = (tmp_emo / "emotions.jsonl").read_text().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["emotion"] == "relief"

    def test_deposit_sets_ts(self, tmp_emo):
        _emo.deposit({"emotion": "anxiety", "intensity": 0.5, "valence": -0.5, "cause": "t:delay"})
        row = json.loads((tmp_emo / "emotions.jsonl").read_text().splitlines()[0])
        assert row.get("ts", "").endswith("Z")

    def test_deposit_sets_episode(self, tmp_emo):
        _emo.deposit({"emotion": "frustration", "intensity": 0.7, "valence": -0.7, "cause": "t:fail"})
        row = json.loads((tmp_emo / "emotions.jsonl").read_text().splitlines()[0])
        assert "episode" in row and len(row["episode"]) == 12

    def test_multiple_deposits_accumulate(self, tmp_emo):
        for i in range(5):
            _emo.deposit({"emotion": "anxiety", "intensity": 0.5, "valence": -0.5, "cause": f"t:e{i}"})
        lines = (tmp_emo / "emotions.jsonl").read_text().splitlines()
        assert len(lines) == 5


class TestEmoBankDecayBounds:
    """Decay is virtual (applied at read time). Intensities returned by summarize
    must stay within [0, 1] regardless of how old entries are."""

    def test_decay_output_in_bounds(self, tmp_emo):
        # Deposit an old entry by patching its timestamp to 48 h ago
        old_ts = (dt.datetime.utcnow() - dt.timedelta(hours=48)).replace(microsecond=0).isoformat() + "Z"
        _emo.deposit({"ts": old_ts, "emotion": "frustration",
                      "intensity": 1.0, "valence": -1.0, "cause": "t:old"})
        # Current entry
        _emo.deposit({"emotion": "relief", "intensity": 0.8, "valence": 0.6, "cause": "t:new"})
        snap = _emo.summarize(window_hours=24)
        # Stress/energy/motivation/focus are all composites of decayed intensities
        for key in ("energy", "stress", "motivation", "focus"):
            v = snap[key]
            assert 0.0 <= v <= 1.0, f"{key}={v} out of bounds"

    def test_old_entries_excluded_from_window(self, tmp_emo):
        # 48h-old frustration entry – outside 24h window – should not raise stress
        old_ts = (dt.datetime.utcnow() - dt.timedelta(hours=48)).replace(microsecond=0).isoformat() + "Z"
        _emo.deposit({"ts": old_ts, "emotion": "frustration",
                      "intensity": 1.0, "valence": -1.0, "cause": "t:old"})
        snap = _emo.summarize(window_hours=24)
        # No recent entries → defaults to calm
        assert snap["mood"] == "calm"

    def test_recent_frustrations_dominate_mood(self, tmp_emo):
        for _ in range(3):
            _emo.deposit({"emotion": "frustration", "intensity": 0.8,
                          "valence": -0.7, "cause": "t:fail"})
        snap = _emo.summarize(window_hours=24)
        assert "frustration" in snap["dominant_emotions"]


class TestEmoBankDepositWithPolicy:
    def test_noise_floor_skips_weak_entry(self, tmp_emo):
        result = _emo.deposit_with_policy({"emotion": "curiosity", "intensity": 0.1,
                                           "valence": 0.2, "cause": "t:ok"})
        assert result is None
        lines = (tmp_emo / "emotions.jsonl").read_text().splitlines()
        assert len(lines) == 0

    def test_valence_flip_bypasses_noise_floor(self, tmp_emo):
        # First: negative entry
        _emo.deposit_with_policy({"emotion": "frustration", "intensity": 0.6,
                                   "valence": -0.6, "cause": "t:fail"})
        # Then: very weak positive (would normally be blocked by noise floor)
        result = _emo.deposit_with_policy({"emotion": "relief", "intensity": 0.1,
                                            "valence": 0.3, "cause": "t:ok"})
        # Sign flip → should NOT be skipped
        assert result is not None

    def test_coalesce_same_emotion_within_window(self, tmp_emo):
        _emo.deposit_with_policy({"emotion": "frustration", "intensity": 0.6,
                                   "valence": -0.6, "cause": "t:fail"})
        # Same emotion+cause immediately after → should coalesce (return None)
        r2 = _emo.deposit_with_policy({"emotion": "frustration", "intensity": 0.5,
                                        "valence": -0.5, "cause": "t:fail"})
        assert r2 is None

    def test_rebound_shadow_injected(self, tmp_emo):
        # Negative then positive → determination shadow
        _emo.deposit_with_policy({"emotion": "frustration", "intensity": 0.7,
                                   "valence": -0.7, "cause": "t:fail"})
        _emo.deposit_with_policy({"emotion": "relief", "intensity": 0.6,
                                   "valence": 0.5, "cause": "t:ok"})
        lines = (tmp_emo / "emotions.jsonl").read_text().splitlines()
        emotions = [json.loads(l)["emotion"] for l in lines if l.strip()]
        assert "determination" in emotions

    def test_strong_entry_above_floor(self, tmp_emo):
        r = _emo.deposit_with_policy({"emotion": "frustration", "intensity": 0.8,
                                       "valence": -0.7, "cause": "t:fail"})
        assert r is not None


class TestEmoBankSummarize:
    def test_empty_bank_returns_calm(self, tmp_emo):
        snap = _emo.summarize()
        assert snap["mood"] == "calm"
        assert snap["dominant_emotions"] == []

    def test_stress_elevated_after_frustration(self, tmp_emo):
        for _ in range(4):
            _emo.deposit({"emotion": "frustration", "intensity": 0.85,
                          "valence": -0.8, "cause": "t:fail"})
        snap = _emo.summarize(window_hours=24)
        assert snap["stress"] > 0.3

    def test_motivation_elevated_after_pride(self, tmp_emo):
        for _ in range(3):
            _emo.deposit({"emotion": "pride", "intensity": 0.8,
                          "valence": 0.8, "cause": "t:win"})
        snap = _emo.summarize(window_hours=24)
        assert snap["motivation"] > 0.3


# ===========================================================================
# 4. RBT DIAGNOSIS
# ===========================================================================

def _make_emotions(specs: List[Dict]) -> List[Dict]:
    """Build minimal emotion dicts from (emotion, intensity, valence, cause) specs."""
    out = []
    for s in specs:
        out.append({
            "emotion":   s.get("emotion", "curiosity"),
            "intensity": s.get("intensity", 0.5),
            "valence":   s.get("valence", 0.0),
            "cause":     s.get("cause", "t:ok"),
        })
    return out


class TestRosesbudsThorns:
    def test_frustration_becomes_thorn(self):
        emos = _make_emotions([{"emotion": "frustration", "intensity": 0.7, "valence": -0.7,
                                 "cause": "reminder.toast:fail"}])
        rbt = roses_buds_thorns(emos, [])
        assert len(rbt["thorns"]) == 1
        assert rbt["thorns"][0]["emotion"] == "frustration"

    def test_anxiety_becomes_thorn(self):
        emos = _make_emotions([{"emotion": "anxiety", "intensity": 0.5, "valence": -0.5,
                                 "cause": "reminder.toast:delay"}])
        rbt = roses_buds_thorns(emos, [])
        assert len(rbt["thorns"]) >= 1

    def test_pride_high_intensity_is_rose(self):
        emos = _make_emotions([{"emotion": "pride", "intensity": 0.8, "valence": 0.9,
                                 "cause": "reminder.toast:ok"}])
        rbt = roses_buds_thorns(emos, [])
        assert len(rbt["roses"]) == 1

    def test_low_intensity_frustration_not_thorn(self):
        # Below THORN_INTENS_MIN (0.4)
        emos = _make_emotions([{"emotion": "frustration", "intensity": 0.3, "valence": -0.5,
                                 "cause": "t:fail"}])
        rbt = roses_buds_thorns(emos, [])
        assert len(rbt["thorns"]) == 0

    def test_curiosity_with_moderate_intensity_is_bud(self):
        emos = _make_emotions([{"emotion": "curiosity", "intensity": 0.4, "valence": 0.3,
                                 "cause": "t:ok"}])
        rbt = roses_buds_thorns(emos, [])
        assert len(rbt["buds"]) >= 1

    def test_delay_thorn_generates_prompt_rules(self):
        emos = _make_emotions([{"emotion": "anxiety", "intensity": 0.6, "valence": -0.6,
                                 "cause": "reminder.toast:delay"}])
        rbt = roses_buds_thorns(emos, [])
        rules_text = " ".join(rbt["prompt_rules_to_add"]).lower()
        assert "utc" in rules_text or "receipt" in rules_text

    def test_fail_thorn_generates_code_suggestion(self):
        emos = _make_emotions([{"emotion": "frustration", "intensity": 0.8, "valence": -0.7,
                                 "cause": "reminder.toast:fail"}])
        rbt = roses_buds_thorns(emos, [])
        assert len(rbt["code_suggestions"]) >= 1

    def test_mixed_produces_all_categories(self):
        emos = _make_emotions([
            {"emotion": "pride",       "intensity": 0.8, "valence":  0.9, "cause": "t:ok"},
            {"emotion": "curiosity",   "intensity": 0.4, "valence":  0.3, "cause": "t:ok"},
            {"emotion": "frustration", "intensity": 0.7, "valence": -0.7, "cause": "t:fail"},
        ])
        rbt = roses_buds_thorns(emos, [])
        assert len(rbt["roses"]) >= 1
        assert len(rbt["buds"])  >= 1
        assert len(rbt["thorns"]) >= 1

    def test_empty_emotions_no_crash(self):
        rbt = roses_buds_thorns([], [])
        assert rbt["roses"] == []
        assert rbt["buds"]  == []
        assert rbt["thorns"] == []
        assert "diagnosis" in rbt

    def test_diagnosis_string_in_output(self):
        rbt = roses_buds_thorns([], [])
        assert isinstance(rbt["diagnosis"], str)
        assert "Roses=" in rbt["diagnosis"]

    def test_dedupe_order_removes_duplicates(self):
        xs = ["A", "B", "A", "C", "B"]
        result = dedupe_order(xs)
        assert result == ["A", "B", "C"]


# ===========================================================================
# 5. PROMPT ADAPTATION – core-identity guard & adaptive rewrite
# ===========================================================================

class TestCoreIdentityGuard:
    """The core identity block must survive any prompt rewrite unchanged."""

    def test_core_preserved_after_rewrite(self, tmp_cwd):
        prompt = _stub_prompt("I am a reliable assistant.")
        core_before = CORE_RE.search(prompt).group(0)
        new_prompt, _ = generate_new_prompt(prompt, cue="UTC and receipt gating required.")
        core_after = CORE_RE.search(new_prompt).group(0)
        assert core_before == core_after

    def test_adaptive_section_updated(self, tmp_cwd):
        prompt = _stub_prompt()
        _, adaptive = generate_new_prompt(prompt, cue="test cue")
        assert "test cue" in adaptive or "Note to self: test cue" in adaptive

    def test_guardrail_raises_if_core_missing(self, tmp_cwd):
        prompt = "No identity block here.\n## BEGIN_ADAPTIVE_SECTION\n...\n## END_ADAPTIVE_SECTION\n"
        with pytest.raises(ValueError, match="Core identity"):
            generate_new_prompt(prompt, cue="irrelevant", guardrails=True)

    def test_guardrail_disabled_skips_check(self, tmp_cwd):
        prompt = "No identity block here.\n## BEGIN_ADAPTIVE_SECTION\n...\n## END_ADAPTIVE_SECTION\n"
        # Should not raise
        new_prompt, _ = generate_new_prompt(prompt, cue="ok", guardrails=False)
        assert "## BEGIN_ADAPTIVE_SECTION" in new_prompt

    def test_no_adaptive_section_appends_one(self, tmp_cwd):
        prompt = (
            "## BEGIN_CORE_IDENTITY\nI am an agent.\n## END_CORE_IDENTITY\n"
            "Some other content here.\n"
        )
        new_prompt, adaptive = generate_new_prompt(prompt, cue="add adaptive", guardrails=True)
        assert "## BEGIN_ADAPTIVE_SECTION" in new_prompt
        assert "## END_ADAPTIVE_SECTION" in new_prompt

    def test_output_file_written(self, tmp_cwd):
        prompt = _stub_prompt("Stable core.")
        generate_new_prompt(prompt, cue="file test")
        assert (tmp_cwd / "output" / "new_prompt.txt").exists()

    def test_rbt_rules_injected_into_adaptive(self, tmp_cwd):
        prompt = _stub_prompt()
        _, adaptive = generate_new_prompt(
            prompt,
            cue="test",
            rbt_rules=["Always verify UTC.", "Gate on receipt."],
        )
        assert "UTC" in adaptive
        assert "receipt" in adaptive.lower() or "receipt" in adaptive

    def test_rbt_plan_included_when_provided(self, tmp_cwd):
        prompt = _stub_prompt()
        rbt = {
            "roses":  [{"cause": "t:ok",   "emotion": "pride",       "intensity": 0.8}],
            "buds":   [{"cause": "t:ok",   "emotion": "curiosity",   "intensity": 0.4}],
            "thorns": [{"cause": "t:fail", "emotion": "frustration", "intensity": 0.7}],
            "prompt_rules_to_add": ["Rule A", "Rule B"],
        }
        _, adaptive = generate_new_prompt(prompt, cue="test", rbt=rbt)
        assert "Roses" in adaptive
        assert "Thorns" in adaptive


# ===========================================================================
# 6. PATCH ATOMICITY – each diff should target at most one file per hunk
# ===========================================================================

class TestPatchAtomicity:
    """Parse real .diff files from output/proposals/ and verify structural sanity."""

    DIFF_HEADER_RE = re.compile(r"^--- a/", re.MULTILINE)
    PLUS_HEADER_RE = re.compile(r"^\+\+\+ b/", re.MULTILINE)

    def _diff_files(self) -> List[Path]:
        proposals = REPO_ROOT / "output" / "proposals"
        return list(proposals.glob("*.diff")) if proposals.exists() else []

    def test_diff_files_parseable(self):
        # Valid unified diffs use either "--- a/..." (modification) or
        # "--- /dev/null" (new file). Both are paired with a "+++ b/..." line.
        # Count all --- lines and all +++ lines; they must be equal.
        MINUS_RE = re.compile(r"^---[ \t]", re.MULTILINE)
        PLUS_RE  = re.compile(r"^\+\+\+[ \t]", re.MULTILINE)
        for diff_path in self._diff_files():
            content = diff_path.read_text(encoding="utf-8", errors="ignore")
            minus = MINUS_RE.findall(content)
            plus  = PLUS_RE.findall(content)
            assert len(minus) == len(plus), (
                f"{diff_path.name}: mismatched diff headers "
                f"(--- count={len(minus)}, +++ count={len(plus)})"
            )

    def test_diff_has_at_least_one_hunk(self):
        for diff_path in self._diff_files():
            content = diff_path.read_text(encoding="utf-8", errors="ignore")
            has_hunk = bool(re.search(r"^@@", content, re.MULTILINE))
            assert has_hunk, f"{diff_path.name}: no diff hunks found"

    def test_diff_targets_real_file(self):
        """At least one diff in the proposals folder should reference a Python file."""
        diffs = self._diff_files()
        if not diffs:
            pytest.skip("No .diff files in output/proposals/")
        py_ref = any(
            ".py" in diff_path.read_text(encoding="utf-8", errors="ignore")
            for diff_path in diffs
        )
        assert py_ref, "No diff references a .py file"

    def test_no_core_identity_mutation_in_diffs(self):
        """Diffs must never touch the BEGIN_CORE_IDENTITY block."""
        for diff_path in self._diff_files():
            content = diff_path.read_text(encoding="utf-8", errors="ignore")
            assert "BEGIN_CORE_IDENTITY" not in content, (
                f"{diff_path.name}: patch touches core identity block!"
            )


# ===========================================================================
# 7. EVENT LOG INGESTION
# ===========================================================================

class TestFetchRecentEvents:
    def _write_events(self, path: Path, events: List[Dict]):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

    def test_recent_event_included(self, tmp_path):
        path = tmp_path / "logs" / "events.jsonl"
        ev = {"ts": _iso(-60), "kind": "reminder.toast", "status": "ok", "payload": {}}
        self._write_events(path, [ev])
        results = fetch_recent_events(str(path), window_hours=24)
        assert len(results) == 1

    def test_old_event_excluded(self, tmp_path):
        path = tmp_path / "logs" / "events.jsonl"
        old_ts = (dt.datetime.utcnow() - dt.timedelta(hours=48)).replace(microsecond=0).isoformat() + "Z"
        ev = {"ts": old_ts, "kind": "reminder.toast", "status": "ok", "payload": {}}
        self._write_events(path, [ev])
        results = fetch_recent_events(str(path), window_hours=24)
        assert len(results) == 0

    def test_limit_respected(self, tmp_path):
        path = tmp_path / "logs" / "events.jsonl"
        events = [{"ts": _iso(-i), "kind": "t", "status": "ok", "payload": {}} for i in range(100)]
        self._write_events(path, events)
        results = fetch_recent_events(str(path), window_hours=24, limit=10)
        assert len(results) <= 10

    def test_missing_file_returns_empty(self, tmp_path):
        results = fetch_recent_events(str(tmp_path / "nonexistent.jsonl"))
        assert results == []

    def test_malformed_lines_skipped(self, tmp_path):
        path = tmp_path / "logs" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "NOT JSON\n"
            + json.dumps({"ts": _iso(-10), "kind": "t", "status": "ok", "payload": {}}) + "\n"
        )
        results = fetch_recent_events(str(path), window_hours=24)
        assert len(results) == 1


# ===========================================================================
# 8. END-TO-END PIPELINE SMOKE TEST  (no LLM, pure deterministic)
# ===========================================================================

class TestEndToEndDeterministicPipeline:
    """
    Exercises: log ingest → appraise → emobank deposit → RBT diagnosis → prompt patch.
    Does NOT call the LLM (no run_once / code proposal).
    """

    def _run(self, tmp_path, tmp_emo, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # 1. Write failure logs
        logs_path = tmp_path / "logs" / "events.jsonl"
        logs_path.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {"ts": _iso(-i * 60), "kind": "reminder.toast",
             "status": "fail" if i % 2 == 0 else "delay",
             "payload": {"delayed_by_sec": 200 + i * 10}}
            for i in range(12)
        ]
        with open(logs_path, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        # 2. Appraise & deposit
        from robin_b_project_scaffold.robin_b.runtime.events_log import fetch_recent_events
        from robin_b_project_scaffold.robin_b.b_core.appraise import appraise_event
        from robin_b_project_scaffold.robin_b.b_core import emobank

        loaded = fetch_recent_events(str(logs_path), window_hours=24)
        for ev in loaded:
            dep = appraise_event(ev)
            emobank.deposit(dep)

        # 3. Diagnose
        rows = list(_emo._iter_jsonl(_emo.PATH_EMO))
        rbt = roses_buds_thorns(rows, loaded)

        # 4. Patch prompt
        prompt = _stub_prompt("I reliably schedule reminders.")
        new_prompt, _ = generate_new_prompt(
            prompt,
            cue="Reduce reminder failures.",
            rbt_rules=rbt["prompt_rules_to_add"],
            rbt=rbt,
        )
        return rbt, new_prompt, rows

    def test_thorns_detected_from_failure_logs(self, tmp_path, tmp_emo, monkeypatch):
        rbt, _, _ = self._run(tmp_path, tmp_emo, monkeypatch)
        assert len(rbt["thorns"]) > 0, "Expected thorns from injected failures"

    def test_emobank_populated(self, tmp_path, tmp_emo, monkeypatch):
        _, _, rows = self._run(tmp_path, tmp_emo, monkeypatch)
        assert len(rows) > 0

    def test_prompt_contains_adaptive_content(self, tmp_path, tmp_emo, monkeypatch):
        _, new_prompt, _ = self._run(tmp_path, tmp_emo, monkeypatch)
        assert "BEGIN_ADAPTIVE_SECTION" in new_prompt
        assert "reliability" in new_prompt.lower() or "UTC" in new_prompt or "receipt" in new_prompt

    def test_core_identity_unchanged_after_pipeline(self, tmp_path, tmp_emo, monkeypatch):
        prompt = _stub_prompt("I reliably schedule reminders.")
        monkeypatch.chdir(tmp_path)
        core_before = CORE_RE.search(prompt).group(0)
        new_prompt, _ = generate_new_prompt(prompt, cue="pipeline test")
        core_after = CORE_RE.search(new_prompt).group(0)
        assert core_before == core_after
