"""
scoring/proxy_scorer.py
========================
Objective proxy metrics for VIGIL diff quality.
No LLM required — deterministic, fast, reproducible.

Scoring dimensions (each 1 point, total /7):
  1. retry_logic       — retry keyword present
  2. jitter            — jitter or random.uniform
  3. utc_handling      — utc or timezone handling
  4. structured_logging — logging calls present
  5. error_codes        — structured error cause codes
  6. guard_clause       — defensive if+raise pattern
  7. observability      — latency/duration tracking

Final proxy score = (points / 7) * 0.70  (70% weight in composite)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class ProxyScore:
    # Raw checks (True/False each)
    retry_logic:       bool = False
    jitter:            bool = False
    utc_handling:      bool = False
    structured_logging: bool = False
    error_codes:       bool = False
    guard_clause:      bool = False
    observability:     bool = False

    # Diff validity
    is_valid_diff:     bool = False
    line_count:        int  = 0
    files_touched:     List[str] = field(default_factory=list)

    # Computed
    raw_points:        int   = 0    # 0-7
    proxy_weighted:    float = 0.0  # (points/7) * 0.70

    def to_dict(self) -> Dict:
        return {
            "retry_logic":        self.retry_logic,
            "jitter":             self.jitter,
            "utc_handling":       self.utc_handling,
            "structured_logging": self.structured_logging,
            "error_codes":        self.error_codes,
            "guard_clause":       self.guard_clause,
            "observability":      self.observability,
            "is_valid_diff":      self.is_valid_diff,
            "line_count":         self.line_count,
            "files_touched":      self.files_touched,
            "raw_points":         self.raw_points,
            "proxy_weighted":     round(self.proxy_weighted, 4),
        }


# ─── Regex patterns ──────────────────────────────────────────────────────────

_RETRY_RE     = re.compile(r"\bretry\b", re.IGNORECASE)
_JITTER_RE    = re.compile(r"\bjitter\b|random\.uniform|random\.randint", re.IGNORECASE)
_UTC_RE       = re.compile(r"\butc\b|timezone|datetime\.timezone|pytz|zoneinfo", re.IGNORECASE)
_LOG_RE       = re.compile(r"\blog(?:ging|ger)?\.(debug|info|warning|error|critical)\b|print\(.*log", re.IGNORECASE)
_ERR_CODE_RE  = re.compile(r"cause_code|error_code|\"cause\"\s*:|\'cause\'\s*:|error_type", re.IGNORECASE)
_OBSERVE_RE   = re.compile(r"time\.monotonic|latency|duration_ms|elapsed|perf_counter", re.IGNORECASE)

# Diff validity
_HUNK_RE      = re.compile(r"^@@", re.MULTILINE)
_MINUS_HDR_RE = re.compile(r"^---\s", re.MULTILINE)
_PLUS_HDR_RE  = re.compile(r"^\+\+\+\s", re.MULTILINE)
_PLUS_FILE_RE = re.compile(r"^\+\+\+\s+b/(.+)$", re.MULTILINE)

# Guard clause: `if` within 5 lines of `raise`
_GUARD_RE     = re.compile(
    r"if\s+.{1,120}\n(?:[^\n]*\n){0,4}[^\n]*raise\b",
    re.DOTALL
)


def score_diff(diff: str) -> ProxyScore:
    """
    Score a unified diff string on objective proxy metrics.
    Returns a ProxyScore dataclass.
    """
    s = ProxyScore()

    if not diff or not diff.strip():
        return s

    # ── Diff validity ──────────────────────────────────────────────────────
    has_hunk   = bool(_HUNK_RE.search(diff))
    has_minus  = bool(_MINUS_HDR_RE.search(diff))
    has_plus   = bool(_PLUS_HDR_RE.search(diff))
    s.is_valid_diff = has_hunk and has_minus and has_plus

    s.line_count = len(diff.splitlines())
    s.files_touched = _PLUS_FILE_RE.findall(diff)

    # ── Feature checks ─────────────────────────────────────────────────────
    s.retry_logic        = bool(_RETRY_RE.search(diff))
    s.jitter             = bool(_JITTER_RE.search(diff))
    s.utc_handling       = bool(_UTC_RE.search(diff))
    s.structured_logging = bool(_LOG_RE.search(diff))
    s.error_codes        = bool(_ERR_CODE_RE.search(diff))
    s.guard_clause       = bool(_GUARD_RE.search(diff))
    s.observability      = bool(_OBSERVE_RE.search(diff))

    # ── Aggregate ──────────────────────────────────────────────────────────
    checks = [
        s.retry_logic, s.jitter, s.utc_handling,
        s.structured_logging, s.error_codes,
        s.guard_clause, s.observability,
    ]
    s.raw_points     = sum(checks)
    s.proxy_weighted = (s.raw_points / 7.0) * 0.70

    return s


def score_text(text: str) -> ProxyScore:
    """
    Score any text block (prompt patch, prose, etc.) using the same feature checks.
    Diff validity will be False since it's not a diff — everything else applies.
    Useful for scoring VIGIL prompt patches when no diff is available.
    """
    return score_diff(text)  # reuses same logic; is_valid_diff will be False


# ─── Batch helper ────────────────────────────────────────────────────────────

def score_many(diffs: List[Optional[str]]) -> List[ProxyScore]:
    return [score_diff(d or "") for d in diffs]
