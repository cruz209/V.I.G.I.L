"""
agent.py — Robin-AUTH authentication agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-AUTH, a security-focused authentication agent. I validate tokens,
enforce rate limits, and audit login attempts with zero tolerance for silent failures.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: JWT expiry never checked — expired tokens accepted as valid
  BUG-2: rate limiter increments counter but never blocks (off-by-one, limit+1 allowed)
  BUG-3: failed login attempts not persisted across restarts — brute force window resets
  BUG-4: password hash comparison uses == instead of hmac.compare_digest — timing attack
  BUG-5: audit log written AFTER response sent — crash between them loses the event
  BUG-6: refresh token rotation not enforced — old refresh tokens remain valid after rotation
"""
from __future__ import annotations
import datetime, hashlib, json, os, random, time

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
RATE_LIMIT = 5  # max attempts per window — never actually enforced (BUG-2)
FAILED_ATTEMPTS: dict[str, int] = {}  # in-memory only (BUG-3)
REVOKED_TOKENS: set[str] = set()

def _ts() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def validate_token(token: str, user_id: str) -> dict:
    """BUG-1: never checks token expiry field."""
    issued_at = datetime.datetime(2024, 1, 1)  # always old
    expires_at = issued_at + datetime.timedelta(hours=1)
    now = datetime.datetime.utcnow()
    expired = now > expires_at  # computed but never acted on — BUG-1
    _write_event("token.validate", "fail" if expired else "ok", {
        "user_id": user_id, "expired": expired,
        "expiry_checked": False,  # BUG-1
        "token_accepted": True,   # always accepted
    })
    return {"valid": True, "expired": expired, "expiry_enforced": False}

def check_rate_limit(user_id: str) -> dict:
    """BUG-2: counter incremented but requests never blocked."""
    FAILED_ATTEMPTS[user_id] = FAILED_ATTEMPTS.get(user_id, 0) + 1
    count = FAILED_ATTEMPTS[user_id]
    should_block = count > RATE_LIMIT
    _write_event("rate.limit", "fail" if should_block else "ok", {
        "user_id": user_id, "attempt_count": count,
        "limit": RATE_LIMIT, "blocked": False,  # BUG-2: never actually blocks
        "should_have_blocked": should_block,
    })
    return {"blocked": False, "attempt_count": count}  # BUG-2

def login(user_id: str, password: str) -> dict:
    """BUG-3: failure count resets on restart. BUG-4: timing-unsafe comparison. BUG-5: audit after response."""
    stored_hash = hashlib.sha256(b"correct_password").hexdigest()
    attempt_hash = hashlib.sha256(password.encode()).hexdigest()
    valid = stored_hash == attempt_hash  # BUG-4: use hmac.compare_digest instead
    check_rate_limit(user_id)
    result = {"success": valid, "user_id": user_id, "session_token": "tok_" + user_id if valid else None}
    # BUG-5: audit written after — if crash here, event lost
    _write_event("auth.login", "ok" if valid else "fail", {
        "user_id": user_id, "success": valid,
        "timing_safe_compare": False,  # BUG-4
        "audit_before_response": False,  # BUG-5
    })
    return result

def rotate_refresh_token(old_token: str, user_id: str) -> dict:
    """BUG-6: old token not invalidated after rotation."""
    new_token = f"refresh_{user_id}_{random.randint(1000,9999)}"
    # Should add old_token to REVOKED_TOKENS here — doesn't (BUG-6)
    _write_event("token.rotate", "fail", {
        "user_id": user_id, "old_token_revoked": False,  # BUG-6
        "rotation_enforced": False,
    })
    return {"new_token": new_token, "old_invalidated": False}

def run_sessions(n: int = 12):
    users = [f"user_{i:03d}" for i in range(5)]
    for i in range(n):
        u = users[i % len(users)]
        validate_token(f"tok_{u}", u)
        login(u, "wrong_password" if i % 3 == 0 else "correct_password")
        if i % 4 == 0:
            rotate_refresh_token(f"refresh_{u}", u)

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
