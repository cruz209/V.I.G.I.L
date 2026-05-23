# run_robin_b.py
from robin_b_project_scaffold.robin_b.RobinBAgent.orchestrator import run_once

if __name__ == "__main__":
    result = run_once(
        logs_path="robin_b_project_scaffold/logs/events.jsonl",
        agent_prompt_path="robin_b_project_scaffold/sample_a_repo/agent.py",
        repo_root="robin_b_project_scaffold/sample_a_repo/"
    )
    print("\n===== ROBIN B OUTPUT =====")
    print(result["text"])
