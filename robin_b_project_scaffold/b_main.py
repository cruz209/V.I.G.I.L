from __future__ import annotations
import typer, json, os, glob
from rich import print
from robin_b.b_ingest import ingest_jsonl
from robin_b.runtime.b_reflect import run_reflection
from robin_b.runtime.b_prompt import generate_new_prompt
from robin_b.runtime.b_review import review_codebase
from robin_b.runtime.b_propose import propose_code_patch, propose_prompt_patch

app = typer.Typer(help="Robin B CLI")

@app.command()
def logs(path: str = typer.Option(..., "--logs", help="JSONL log file to ingest"),
         reflect: bool = typer.Option(False, "--reflect", help="Run reflection after ingest"),
         rewrite_prompt: bool = typer.Option(False, "--rewrite-prompt", help="Write new prompt"),
         review_code: bool = typer.Option(False, "--review-code", help="Review codebase"),
         paths: str = typer.Option("", "--paths", help="Glob for code review"),
         propose_patch: bool = typer.Option(False, "--propose-patch", help="Propose code patch"),
         targets: str = typer.Option("", "--targets", help="Comma-separated filenames (e.g., reminders.py,agent.py)"),
         agent_prompt: str = typer.Option("sample_a_repo/agent.py", "--agent-prompt", help="Path to agent prompt file"),
         repo_root: str = typer.Option("sample_a_repo", "--repo-root", help="Root of target repo")):
    n = ingest_jsonl(path)
    print(f"[bold green]Ingested[/] {n} events from {path}")
    rec = None
    if reflect:
        rec = run_reflection()
        print("[bold cyan]Reflection:[/] ", json.dumps(rec, indent=2))

    if rewrite_prompt:
        with open(agent_prompt, "r", encoding="utf-8") as f:
            cur = f.read()
        cue = (rec or {}).get("cue", "I will improve time reliability.")
        new_prompt, _ = generate_new_prompt(cur, cue, guardrails=True)
        print(f"[bold magenta]Wrote[/] output/new_prompt.txt (adaptive section updated)")

    if review_code and paths:
        findings = review_codebase(paths)
        print("[bold yellow]Findings:[/]", findings)

    if propose_patch and targets:
        for fn in [t.strip() for t in targets.split(",") if t.strip()]:
            out = propose_code_patch(repo_root, fn)
            if out:
                print(f"[bold green]Proposed patch:[/] {out}")
        # also propose prompt patch
        ptxt, pr = propose_prompt_patch(agent_prompt)
        print(f"[bold green]Proposed prompt update:[/] {ptxt}")

if __name__ == "__main__":
    app()
