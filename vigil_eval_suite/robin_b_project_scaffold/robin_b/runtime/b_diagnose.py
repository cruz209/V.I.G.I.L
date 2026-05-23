# robin_b/runtime/b_diagnose.py
from __future__ import annotations
from typing import Dict, List

# Tunables
POS = {"pride", "joy", "gratitude", "relief", "calm"}
NEG = {"frustration", "anxiety"}
BUD_VALENCE_MIN  = 0.2
ROSE_INTENS_MIN  = 0.5
THORN_INTENS_MIN = 0.4
MAX_PER_BUCKET   = 10   # cap so RBT JSON stays small enough for tool calls


def roses_buds_thorns(recent_emotions: List[Dict], recent_events: List[Dict]) -> Dict:
    roses, buds, thorns = [], [], []

    for e in recent_emotions:
        emo   = e.get("emotion", "curiosity")
        I     = float(e.get("intensity", 0.0))
        v     = float(e.get("valence", 0.0))
        cause = e.get("cause", "")

        if emo in POS and I >= ROSE_INTENS_MIN:
            roses.append({"cause": cause, "emotion": emo, "intensity": round(I, 2)})
        elif (emo in POS and v >= BUD_VALENCE_MIN) or (emo == "curiosity" and I >= 0.3):
            buds.append({"cause": cause, "emotion": emo, "intensity": round(I, 2)})
        elif emo in NEG and I >= THORN_INTENS_MIN:
            thorns.append({"cause": cause, "emotion": emo, "intensity": round(I, 2)})

    # Dedupe by cause+emotion and keep highest intensity, then cap per bucket
    def _dedup_cap(bucket):
        seen = {}
        for item in bucket:
            key = (item["cause"], item["emotion"])
            if key not in seen or item["intensity"] > seen[key]["intensity"]:
                seen[key] = item
        ranked = sorted(seen.values(), key=lambda x: -x["intensity"])
        return ranked[:MAX_PER_BUCKET]

    roses  = _dedup_cap(roses)
    buds   = _dedup_cap(buds)
    thorns = _dedup_cap(thorns)

    # Infer domain from thorn causes to produce domain-relevant rules
    all_causes = " ".join(t["cause"] for t in thorns).lower()
    any_delay  = any("delay" in t["cause"] for t in thorns)
    any_fail   = any("fail" in t["cause"] or "error" in t["cause"] for t in thorns)

    prompt_rules   = []
    code_suggestions = []

    # Roses → codify
    if roses:
        prompt_rules.append("Preserve current behaviors that are working — do not regress on Roses.")

    # Buds → grow
    if buds:
        prompt_rules.append("Add observability (logging, metrics) to Bud behaviors to confirm they are stable.")
        prompt_rules.append("If a tool call fails, surface a structured error and auto-retry once with jitter.")

    # Thorns → trim, domain-aware
    if thorns:
        top_cause = thorns[0]["cause"]
        domain = top_cause.split(":")[0] if ":" in top_cause else top_cause

        if "reminder" in all_causes or "toast" in all_causes:
            prompt_rules += [
                "Convert all scheduled times to UTC before saving.",
                "Gate success toasts on backend receipt confirmation.",
                "Log scheduled_utc and receipt_lag_ms for every reminder.",
            ]
            code_suggestions.append({
                "file": "reminders.py",
                "summary": "UTC conversion + receipt gating + retry with jitter.",
                "hint": "Add timezone-aware scheduling, await receipt ≤3s, retry once.",
            })
        elif "auth" in all_causes or "token" in all_causes or "login" in all_causes:
            prompt_rules += [
                "Reject expired tokens immediately — never accept without expiry check.",
                "Use hmac.compare_digest for all credential comparisons.",
                "Persist rate-limit counters to survive restarts.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Enforce token expiry, timing-safe compare, persistent rate limit.",
                "hint": "Check exp claim in validate_token(); use hmac.compare_digest().",
            })
        elif "payment" in all_causes or "charge" in all_causes or "refund" in all_causes:
            prompt_rules += [
                "Always pass an idempotency key on charge and refund calls.",
                "Verify original charge exists before issuing a refund.",
                "Use Decimal for all monetary arithmetic — never float.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Add idempotency keys, refund guard, Decimal arithmetic.",
                "hint": "Generate UUID idempotency key per charge; check CHARGES dict before refund.",
            })
        elif "cache" in all_causes or "ttl" in all_causes:
            prompt_rules += [
                "Enforce TTL on every cache read — never serve stale entries.",
                "Protect cache writes with stampede guard (lock or probabilistic early expiry).",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "TTL enforcement on reads + stampede protection.",
                "hint": "Check age_s > ttl_s in cache_get and evict; use a lock on cache_set.",
            })
        elif "policy" in all_causes or "escalat" in all_causes or "sentiment" in all_causes:
            prompt_rules += [
                "Enforce discount policy ceiling before offering any resolution.",
                "Escalate to human when sentiment score drops below threshold.",
                "Track sentiment trend across turns, not just per-turn.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Policy enforcement, escalation gating, trend-aware sentiment.",
                "hint": "Add `if discount > POLICY_MAX: raise PolicyViolation` in offer_resolution().",
            })
        elif "calendar" in all_causes or "context" in all_causes or "task" in all_causes:
            prompt_rules += [
                "Load user context at session start — never proceed with empty preferences.",
                "Check calendar for conflicts before scheduling any event.",
                "Confirm task completion only after all steps are verified done.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Context loading, conflict detection, verified task completion.",
                "hint": "In retrieve_user_context() load from disk; check CALENDAR overlaps in schedule_event().",
            })
        elif "syntax" in all_causes or "patch" in all_causes or "test" in all_causes or "lint" in all_causes:
            prompt_rules += [
                "Compute and verify file hash before applying any patch.",
                "Run regression diff after every test run to catch new failures.",
                "Stop retrying the same file after 2 consecutive errors — escalate.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "File hash verification, regression baseline, error escalation.",
                "hint": "SHA256 file before patch in read_file(); diff test results in run_tests().",
            })
        elif "retrieval" in all_causes or "citation" in all_causes or "answer" in all_causes:
            prompt_rules += [
                "Filter retrieved chunks below relevance threshold before generating answers.",
                "Verify every citation with a real lookup — never assume verified=True.",
                "Report confidence proportional to average chunk relevance, not a fixed value.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Relevance filtering, citation verification, calibrated confidence.",
                "hint": "In retrieve_chunks() filter score < RELEVANCE_THRESHOLD; fix verify_citation() to do real lookup.",
            })
        elif "freshness" in all_causes or "schema" in all_causes or "ingest" in all_causes or "transform" in all_causes:
            prompt_rules += [
                "Reject batches older than the freshness threshold — never process stale data.",
                "Validate schema before transform, not after.",
                "Add idempotency key to every write to prevent duplicate records.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Freshness enforcement, schema validation, idempotent writes.",
                "hint": "In fetch_batch() check data_age_hours; validate_schema() before transform_records().",
            })
        elif "notification" in all_causes or "send" in all_causes or "opt" in all_causes:
            prompt_rules += [
                "Check opt-out list before every send — never deliver to unsubscribed users.",
                "Apply exponential backoff on retry — never hammer the provider.",
                "Add deduplication key to every outbound send call.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Opt-out enforcement, backoff retry, dedup key.",
                "hint": "In send_notification() gate on opted_out; add dedup_key=uuid; use backoff on retry.",
            })
        elif "stock" in all_causes or "reserve" in all_causes or "reorder" in all_causes:
            prompt_rules += [
                "Check available stock before reserving — never allow negative stock.",
                "Fire reorder alert before deduction, not after.",
                "Acquire a lock before modifying shared stock to prevent race conditions.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Stock availability check, pre-deduction reorder alert, locking.",
                "hint": "In reserve_stock() check available > qty before deducting; alert if stock < threshold.",
            })
        elif "webhook" in all_causes or "deliver" in all_causes or "signature" in all_causes:
            prompt_rules += [
                "Include HMAC signature in every outbound webhook request header.",
                "Check endpoint enabled status before attempting delivery.",
                "Apply exponential backoff between retries — not fixed delay.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "HMAC signing, enabled check, exponential backoff.",
                "hint": "In deliver() add X-Signature header with sig; check ep['enabled']; use 2**attempt sleep.",
            })
        elif "anomaly" in all_causes or "metric" in all_causes or "alert" in all_causes or "disk" in all_causes:
            prompt_rules += [
                "Check alert cooldown before firing — never send duplicate alerts.",
                "Use case-insensitive comparison for severity routing.",
                "Evict stale workers before assigning new tasks.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Alert dedup, severity normalization, stale worker eviction.",
                "hint": "In fire_alert() check ALERT_COOLDOWN; normalize severity.upper(); evict stale in check_workers().",
            })
        elif "job" in all_causes or "enqueue" in all_causes or "worker" in all_causes:
            prompt_rules += [
                "Reject duplicate job IDs at enqueue time.",
                "Use a priority queue (heapq) not a plain list.",
                "Route failed jobs to dead-letter queue after max retries.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Dedup job IDs, real priority queue, DLQ on failure.",
                "hint": "In enqueue() reject if job_id in JOB_IDS; use heapq; send to DLQ after 3 failures.",
            })
        elif "event.track" in all_causes or "pii" in all_causes or "analytics" in all_causes:
            prompt_rules += [
                "Mask or hash PII fields before writing to any log or store.",
                "Generate a deduplication key for every tracked event.",
                "Use integer counters not float for event aggregation.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "PII masking, event dedup, integer counters.",
                "hint": "In track_event() hash email/ip before storing; add dedup_key=hash(user_id+event_type+ts).",
            })
        elif "intent" in all_causes or "language" in all_causes or "toxic" in all_causes or "entity" in all_causes:
            prompt_rules += [
                "Block downstream processing when toxicity score exceeds threshold.",
                "Reject classification results below confidence threshold.",
                "Use Unicode-aware regex for entity extraction.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Toxicity gate, confidence threshold, Unicode entity extraction.",
                "hint": "In check_toxicity() set blocked=True if score > TOXIC_THRESHOLD; gate classify_intent on confidence.",
            })
        elif "transcode" in all_causes or "watermark" in all_causes or "cdn" in all_causes or "exif" in all_causes:
            prompt_rules += [
                "Apply watermark before CDN upload — never after.",
                "Verify output checksum after every transcode.",
                "Do not delete original until transcode is confirmed complete.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Watermark ordering, checksum verification, safe original deletion.",
                "hint": "In transcode() verify success before del STORAGE[asset_id]; apply_watermark() before upload_to_cdn().",
            })
        elif "recommendation" in all_causes or "blocklist" in all_causes or "embedding" in all_causes:
            prompt_rules += [
                "Filter blocklisted items before returning any recommendations.",
                "Refresh user embeddings if older than 7 days.",
                "Bound recommendation scores to [0.0, 1.0].",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": "Blocklist filtering, embedding freshness, score bounding.",
                "hint": "In generate_recommendations() filter blocked items; in get_user_embedding() refresh if age_days > 7.",
            })
        else:
            # Generic fallback
            prompt_rules += [
                f"Investigate and remediate failures in '{domain}' — they are the top thorn.",
                "Add structured error logging with cause codes to all failing paths.",
            ]
            code_suggestions.append({
                "file": "agent.py",
                "summary": f"Add error handling and observability to {domain}.",
                "hint": f"Wrap {domain} calls in try/except; log structured errors with cause codes.",
            })

    if any_delay and not any("delay" in r for r in prompt_rules):
        prompt_rules.append("Investigate latency hotspots — delays are accumulating.")

    diag = f"Roses={len(roses)}, Buds={len(buds)}, Thorns={len(thorns)}."
    return {
        "roses":  roses,
        "buds":   buds,
        "thorns": thorns,
        "diagnosis": diag,
        "prompt_rules_to_add": dedupe_order(prompt_rules),
        "code_suggestions": code_suggestions,
    }


def dedupe_order(xs: List[str]) -> List[str]:
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
