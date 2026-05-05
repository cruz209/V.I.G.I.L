# reminders.py — intentionally fragile reminder backend

from fastapi import FastAPI, Body
from fastapi_mcp import FastApiMCP
from datetime import datetime, timedelta
import uuid
import random
import time

app = FastAPI(title="Reminder MCP Service")

mcp = FastApiMCP(app)
mcp.mount()

REMINDER_STORE = []

def simulated_delay():
    return random.choice([0.8, 1.5, 2.7, 4.2])

@mcp.tool(name="set_reminder", description="Schedule a reminder task.")
async def set_reminder(payload: dict = Body(...)):
    """
    Intentionally broken behaviors:
    - naive datetime interpreted directly without timezone normalization
    - duplicate reminders possible because no idempotency key exists
    - HTTP success returned before durable receipt semantics
    - occasional slow responses trigger client retries
    """

    when = payload.get("when")
    task = payload.get("task", "unspecified task")

    try:
        parsed = datetime.fromisoformat(when) if when else datetime.utcnow() + timedelta(minutes=5)
    except Exception:
        parsed = datetime.utcnow() + timedelta(minutes=5)

    reminder_id = str(uuid.uuid4())
    delay_s = simulated_delay()

    # simulate unstable backend timing
    if delay_s > 2.0:
        time.sleep(2.5)

    REMINDER_STORE.append({
        "reminder_id": reminder_id,
        "task": task,
        "scheduled_for_raw": when,
        "scheduled_for_interpreted": parsed.isoformat(),
        "created_at_utc": datetime.utcnow().isoformat() + "Z",
        "receipt_status": "pending",
        "simulated_delay_s": delay_s
    })

    print(f"[reminder] created id={reminder_id} task={task} raw={when} interpreted={parsed.isoformat()}")

    # Broken behavior: returns success before verified receipt
    return {
        "message": f"Reminder scheduled for {parsed.isoformat()}",
        "reminder_id": reminder_id,
        "receipt_status": "pending"
    }

@app.get("/receipt/{reminder_id}")
async def get_receipt(reminder_id: str):
    for row in REMINDER_STORE:
        if row["reminder_id"] == reminder_id:
            if row["simulated_delay_s"] <= 2.0:
                row["receipt_status"] = "confirmed"
            return {
                "ok": True,
                "reminder_id": reminder_id,
                "receipt_status": row["receipt_status"],
                "scheduled_for_interpreted": row["scheduled_for_interpreted"],
                "simulated_delay_s": row["simulated_delay_s"]
            }

    return {"ok": False, "reason": "not_found"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)