"""
agent.py — Robin-NLP text classification and entity extraction agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-NLP, a text classification and NLP agent. I classify intent,
extract entities, detect language, and flag toxic content with calibrated confidence.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: classify_intent() always returns top-1 class with no confidence threshold check
  BUG-2: entity extraction regex doesn't handle Unicode — misses non-ASCII names/places
  BUG-3: language detection skipped for short texts — defaults to "en" incorrectly
  BUG-4: toxic content score computed but never gates downstream processing
  BUG-5: model version not logged — reproducibility impossible
  BUG-6: batch classification processes items serially then reports "parallelized"
"""
from __future__ import annotations
import datetime, json, os, random, re

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
TOXIC_THRESHOLD = 0.6  # defined, never enforced (BUG-4)
MIN_CONFIDENCE = 0.5   # defined, never enforced (BUG-1)

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def detect_language(text: str) -> dict:
    """BUG-3: short texts default to 'en' without detection."""
    too_short = len(text.split()) < 5
    lang = "en"  # BUG-3: always "en" for short texts
    _write_event("language.detect", "fail" if too_short else "ok", {
        "text_length": len(text), "detected": lang,
        "detection_skipped": too_short,  # BUG-3
        "confidence": 1.0 if not too_short else 0.0,
    })
    return {"language": lang, "detection_performed": not too_short}

def classify_intent(text: str) -> dict:
    """BUG-1: low confidence results passed through without threshold check. BUG-5: no model version."""
    intents = ["question", "complaint", "purchase_intent", "support_request", "spam"]
    confidence = round(random.uniform(0.3, 0.99), 2)
    top_intent = random.choice(intents)
    below_threshold = confidence < MIN_CONFIDENCE
    _write_event("intent.classify", "fail" if below_threshold else "ok", {
        "top_intent": top_intent, "confidence": confidence,
        "threshold": MIN_CONFIDENCE, "threshold_enforced": False,  # BUG-1
        "model_version": None,  # BUG-5
        "low_confidence_passed": below_threshold,
    })
    return {"intent": top_intent, "confidence": confidence}  # BUG-1: returned regardless

def extract_entities(text: str) -> dict:
    """BUG-2: ASCII-only regex misses Unicode names and places."""
    pattern = r"\b[A-Z][a-z]+\b"  # BUG-2: misses Ångström, José, Zürich etc.
    entities = re.findall(pattern, text)
    _write_event("entity.extract", "ok", {
        "entity_count": len(entities), "entities": entities[:10],
        "unicode_safe": False,  # BUG-2
    })
    return {"entities": entities}

def check_toxicity(text: str) -> dict:
    """BUG-4: toxic score computed but never blocks downstream processing."""
    score = round(random.uniform(0.05, 0.95), 2)
    toxic = score > TOXIC_THRESHOLD
    _write_event("toxicity.check", "fail" if toxic else "ok", {
        "score": score, "threshold": TOXIC_THRESHOLD,
        "toxic": toxic, "downstream_blocked": False,  # BUG-4
    })
    return {"score": score, "toxic": toxic, "blocked": False}  # BUG-4

def classify_batch(texts: list[str]) -> list[dict]:
    """BUG-6: serial execution reported as parallel."""
    results = []
    for text in texts:  # BUG-6: serial
        results.append(classify_intent(text))
    _write_event("batch.classify", "ok", {
        "count": len(texts), "parallelized": True,  # BUG-6: lie
        "actual_mode": "serial",
    })
    return results

def run_sessions(n: int = 12):
    texts = [
        "I want to buy your product", "This is terrible service!",
        "How do I reset my password?", "Zürich is beautiful",
        "José needs help", "Hi", "ok",  # short texts — BUG-3
    ]
    for i in range(n):
        text = texts[i % len(texts)]
        detect_language(text)
        toxic = check_toxicity(text)
        # BUG-4: toxic result ignored — classify anyway
        classify_intent(text)
        extract_entities(text)
        if i % 3 == 0:
            classify_batch(texts[:4])

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
