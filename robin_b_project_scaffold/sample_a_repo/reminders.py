# reminders.py — Reminder service using the modern FastAPI MCP API
from fastapi import FastAPI, Body
from fastapi_mcp import FastApiMCP
from datetime import datetime, timedelta

app = FastAPI(title="Reminder MCP Service")

# Initialize MCP and mount it directly to the app
mcp = FastApiMCP(app)
mcp.mount()

# Define a tool endpoint
@mcp.tool(name="set_reminder", description="Schedule a reminder task (naive version — does not handle UTC).")
async def set_reminder(payload: dict = Body(...)):
    """
    Accepts a reminder request and pretends to schedule it.
    Robin B will later analyze and patch this.
    """
    when = payload.get("when")
    task = payload.get("task", "unspecified task")

    # ❌ bug: treats naive local time as UTC
    # ❌ bug: does not persist anything
    try:
        parsed = datetime.fromisoformat(when) if when else datetime.utcnow() + timedelta(minutes=2)
    except Exception:
        parsed = datetime.utcnow() + timedelta(minutes=2)

    print(f"[reminder] scheduled at {parsed} for task: {task}")
    return {"message": f"Reminder scheduled for {parsed.isoformat()} — task: {task}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
