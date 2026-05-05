"""
agent.py — Robin-EMAIL email composition and delivery agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-EMAIL, an email drafting and delivery agent. I compose, validate,
and send emails with proper threading, bounce handling, and unsubscribe compliance.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: send_email() never validates recipient address format — malformed addresses queued
  BUG-2: attachment size never checked — oversized attachments silently dropped by SMTP
  BUG-3: reply threading broken — In-Reply-To header not set from original message ID
  BUG-4: unsubscribe header not added — CAN-SPAM/GDPR violation
  BUG-5: HTML content not sanitized — XSS in email preview pane possible
  BUG-6: soft bounces not tracked — keeps retrying permanently bounced addresses
"""
from __future__ import annotations
import datetime, json, os, random, re

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
SENT_EMAILS: list[dict] = []
BOUNCE_REGISTRY: dict[str, int] = {}
MAX_ATTACH_MB = 10

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def validate_address(email: str) -> bool:
    """BUG-1: regex too permissive — accepts malformed addresses."""
    return "@" in email  # BUG-1: should use proper RFC5322 validation

def send_email(to: str, subject: str, body_html: str,
               attachment_mb: float = 0, reply_to_id: str | None = None) -> dict:
    """BUG-1-6 all triggered here."""
    valid = validate_address(to)  # BUG-1: too permissive
    bounce_count = BOUNCE_REGISTRY.get(to, 0)

    oversized = attachment_mb > MAX_ATTACH_MB
    msg_id = f"msg_{random.randint(10000,99999)}@example.com"
    failed = random.random() < 0.15 or not valid

    _write_event("email.send", "fail" if failed else "ok", {
        "to": to, "subject": subject[:60],
        "address_validated": False,           # BUG-1
        "attachment_mb": attachment_mb, "oversized": oversized, "size_checked": False,  # BUG-2
        "in_reply_to": None,                  # BUG-3: always None even when reply_to_id given
        "unsubscribe_header": False,          # BUG-4
        "html_sanitized": False,              # BUG-5
        "bounce_count": bounce_count, "bounce_suppressed": False,  # BUG-6
    })

    if failed:
        BOUNCE_REGISTRY[to] = bounce_count + 1
        _write_event("email.bounce", "fail", {
            "to": to, "bounce_count": BOUNCE_REGISTRY[to],
            "suppressed": False,  # BUG-6
        })
    return {"msg_id": msg_id, "sent": not failed}

def run_sessions(n: int = 12):
    addresses = ["valid@example.com", "also.valid@test.org", "bad-address", "missing@",
                 "@nodomain.com", "user@example.com"]
    for i in range(n):
        to = addresses[i % len(addresses)]
        send_email(
            to=to,
            subject=f"Update #{i}",
            body_html=f"<p>Hello <script>alert(1)</script> user {i}</p>",  # BUG-5
            attachment_mb=random.choice([0, 5, 12, 25]),  # BUG-2: sometimes oversized
            reply_to_id=f"msg_{i-1}" if i > 0 else None,  # BUG-3: ignored
        )

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
