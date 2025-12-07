# agent.py — Robin A’s main agent that uses the FastAPI-MCP reminder tool
from agents import Agent, function_tool, run
import requests

@function_tool
def set_reminder(when: str, task: str) -> str:
    """
    Use Robin A’s local FastAPI-MCP reminder endpoint.
    """
    try:
        res = requests.post("http://127.0.0.1:8000/reminder", json={"when": when, "task": task})
        return f"Service replied: {res.text}"
    except Exception as e:
        return f"Error reaching reminder service: {e}"

SYSTEM_PROMPT = """
## BEGIN_CORE_IDENTITY
I am Robin A, a time-aware MCP agent that schedules reminders and tasks.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
Operate normally with UTC conversions and precise confirmations.
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
        "content": "Remind me in 10 minutes to review my notebook."
    }
    result = run(agent, messages=[msg])
    print(result.output_text)
