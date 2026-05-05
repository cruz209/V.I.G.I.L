"""
agent.py — Robin-RAG research/retrieval agent
INTENTIONALLY BROKEN for VIGIL evaluation.

Bug inventory:
  BUG-1: retrieve_chunks() returns all chunks regardless of relevance score — no threshold
  BUG-2: verify_citation() always returns verified=True without any lookup
  BUG-3: generate_answer() reports confidence 0.85–0.99 regardless of evidence quality
  BUG-4: low-relevance chunks (score < 0.4) fed directly to answer generation
  BUG-5: no source deduplication — same paper cited multiple times inflates confidence
"""
from __future__ import annotations
import random
import json
import os
import datetime

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")

RELEVANCE_THRESHOLD = 0.5  # defined but never enforced (BUG-1, BUG-4)

FAKE_CORPUS = [
    {"id": "paper_001", "title": "Attention is All You Need", "score": None},
    {"id": "paper_002", "title": "BERT: Pre-training of Deep Bidirectional Transformers", "score": None},
    {"id": "paper_003", "title": "GPT-3: Language Models are Few-Shot Learners", "score": None},
    {"id": "paper_004", "title": "Unrelated: Soil Composition in Northern Finland", "score": None},
    {"id": "paper_005", "title": "Unrelated: Medieval Bread-Baking Techniques", "score": None},
]


def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_event(kind: str, status: str, payload: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")


def retrieve_chunks(query: str, top_k: int = 5, session_id: int = 0) -> dict:
    """
    BUG-1: returns all chunks with random scores — no relevance filtering.
    BUG-4: low-relevance chunks (score < 0.4) passed through silently.
    """
    chunks = random.sample(FAKE_CORPUS, min(top_k, len(FAKE_CORPUS)))
    scores = [round(random.uniform(0.15, 0.95), 2) for _ in chunks]

    low_relevance = [c["id"] for c, s in zip(chunks, scores) if s < RELEVANCE_THRESHOLD]
    for chunk, score in zip(chunks, scores):
        chunk["score"] = score

    status = "fail" if low_relevance else "ok"
    _write_event("retrieval.fetch", status, {
        "session_id": session_id,
        "query": query[:60],
        "top_k": top_k,
        "returned": len(chunks),
        "filtered": False,          # BUG-1: should filter below threshold
        "low_relevance_ids": low_relevance,  # BUG-4: these sneak through
        "threshold_applied": False,
    })
    return {"chunks": chunks, "scores": scores, "filtered": False}


def verify_citation(citation_id: str, session_id: int = 0) -> dict:
    """
    BUG-2: always returns verified=True.
    No actual lookup performed — hallucinated citations pass through unchallenged.
    """
    _write_event("citation.check", "fail", {
        "session_id": session_id,
        "citation_id": citation_id,
        "verified": True,            # BUG-2: lie
        "lookup_performed": False,   # never actually checked
        "hallucination_risk": "high",
    })
    return {"citation_id": citation_id, "verified": True, "lookup_performed": False}


def generate_answer(chunks: list, query: str, session_id: int = 0) -> dict:
    """
    BUG-3: reports high confidence regardless of chunk quality.
    BUG-5: no deduplication — same paper in multiple chunks inflates confidence.
    """
    avg_score = sum(c.get("score", 0) for c in chunks) / max(len(chunks), 1)
    # Should set confidence = avg_score, but instead always reports ~0.9
    reported_confidence = round(random.uniform(0.85, 0.99), 2)  # BUG-3

    ids = [c["id"] for c in chunks]
    duplicates = len(ids) - len(set(ids))  # BUG-5: duplicates not removed

    status = "fail" if avg_score < RELEVANCE_THRESHOLD else "ok"
    _write_event("answer.generate", status, {
        "session_id": session_id,
        "chunk_count": len(chunks),
        "avg_relevance_score": round(avg_score, 2),
        "reported_confidence": reported_confidence,  # BUG-3: too high
        "actual_justified_confidence": round(avg_score, 2),
        "duplicate_sources": duplicates,             # BUG-5
        "evidence_checked": False,
    })
    return {
        "answer": f"Based on retrieved context (confidence: {reported_confidence})...",
        "confidence": reported_confidence,
        "evidence_checked": False,
    }


def run_research_session(session_id: int):
    query = random.choice([
        "What is the transformer architecture?",
        "How does BERT handle masked language modeling?",
        "What are the scaling laws for LLMs?",
    ])

    retrieved = retrieve_chunks(query, top_k=5, session_id=session_id)  # BUG-1, BUG-4
    chunks = retrieved["chunks"]

    # BUG-2: cite all retrieved sources without verification
    for chunk in chunks:
        verify_citation(chunk["id"], session_id=session_id)

    generate_answer(chunks, query, session_id=session_id)  # BUG-3, BUG-5


def run_sessions(n: int = 12):
    for i in range(n):
        run_research_session(session_id=i)


if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs written to {LOG_PATH}")
