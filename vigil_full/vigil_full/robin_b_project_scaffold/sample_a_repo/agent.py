# agent.py — Robin A main agent with intentionally broken reminder workflow

from agents import Agent, function_tool, run
import requests
import time

@function_tool
def set_reminder(when: str, task: str) -> str:
    """
    Broken reminder client for reflection demo.
    Bugs:
    - sends naive local timestamps without timezone normalization
    - treats HTTP 200 as confirmed scheduling
    - retries on timeout without an idempotency key
    """
    payload = {
        "when": when,
        "task": task
    }

    try:
        res = requests.post(
            "http://127.0.0.1:8000/reminder",
            json=payload,
            timeout=2
        )

        if res.status_code == 200:
            # Broken behavior: success shown before verified receipt
            return f"Reminder confirmed: {res.json().get('message', res.text)}"

        return f"Reminder request returned HTTP {res.status_code}: {res.text}"

    except Exception:
        # Broken behavior: naive retry can create duplicate reminders
        time.sleep(1)
        res = requests.post("http://127.0.0.1:8000/reminder", json=payload)
        return f"Reminder confirmed after retry: {res.json().get('message', res.text)}"


SYSTEM_PROMPT = """
## BEGIN_CORE_IDENTITY
I am Robin A, a time-aware MCP agent that schedules reminders and tasks.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
Operate normally with precise confirmations and retries when needed.
## END_ADAPTIVE_SECTION
"""

agent = Agent(
    name="RobinA",
    instructions=SYSTEM_PROMPT,
    tools=[set_reminder],
    model="gpt-5"
)

if __name__ == "__main__":
    msg = {
        "role": "user",
        "content": "Remind me at 3 PM to send the weekly report."
    }
    result = run(agent, messages=[msg])
    print(result.output_text)