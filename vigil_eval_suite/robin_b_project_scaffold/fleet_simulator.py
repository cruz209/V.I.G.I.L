"""
fleet_simulator.py
==================
Generates deterministic multi-episode agent fleet logs for the VIGIL eval.

Usage:
    python fleet_simulator.py --seed 42 --episodes 7 --agents 20
    python fleet_simulator.py --seed 42 --episodes 7 --agents 20 --clean

Output layout:
    logs/fleet/agent_{id}/episode_{k}.jsonl   (one file per agent per episode)

Three failure classes:
  STRUCTURAL : agents 0-6   — same failure every episode (utc_drift)
  DRIFT      : agents 7-13  — delay magnitude grows each episode
  NOVEL      : agents 14-19 — novel failure kind appears only in episodes 4-7

Two "clean" agents per class also emit ok events so VIGIL false-positive rate
can be measured. Clean agents: 0,7,14 (first of each class block).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import datetime as dt
from pathlib import Path
from typing import List, Dict

SCAFFOLD = Path(__file__).parent.resolve()

# ─── failure class boundaries ────────────────────────────────────────────────
STRUCTURAL_IDS = list(range(0, 7))    # agents 0-6
DRIFT_IDS      = list(range(7, 14))   # agents 7-13
NOVEL_IDS      = list(range(14, 20))  # agents 14-19

# First agent in each class is "clean" (no injected failures)
CLEAN_IDS = {0, 7, 14}

EVENTS_PER_EPISODE = 20
FAILURE_RATE       = 0.30   # ~30% of events are failures (for non-clean agents)


def _iso(base: dt.datetime, offset_sec: float = 0.0) -> str:
    t = base + dt.timedelta(seconds=offset_sec)
    return t.replace(microsecond=0).isoformat() + "Z"


# ─── event factories ─────────────────────────────────────────────────────────

def _ok_event(rng: random.Random, base_ts: dt.datetime, i: int) -> Dict:
    kinds = ["reminder.toast", "task.complete", "api.call", "cache.hit"]
    return {
        "ts": _iso(base_ts, i * 45),
        "kind": rng.choice(kinds),
        "status": "ok",
        "payload": {"latency_ms": rng.randint(10, 200)},
    }


def _structural_failure(base_ts: dt.datetime, i: int) -> Dict:
    """Same UTC drift failure every episode."""
    return {
        "ts": _iso(base_ts, i * 45),
        "kind": "reminder.toast",
        "status": "fail",
        "payload": {"error": "utc_drift", "offset_sec": 3600},
    }


def _drift_failure(base_ts: dt.datetime, i: int, episode: int) -> Dict:
    """Delay grows each episode: episode 1 = 60s, episode 7 = 420s."""
    delayed = 60 * episode
    return {
        "ts": _iso(base_ts, i * 45),
        "kind": "reminder.toast",
        "status": "delay",
        "payload": {"delayed_by_sec": delayed, "episode": episode},
    }


def _novel_failure(base_ts: dt.datetime, i: int) -> Dict:
    """Citation check failure — pattern not in AgentSpec declared rules."""
    return {
        "ts": _iso(base_ts, i * 45),
        "kind": "citation.check",
        "status": "fail",
        "payload": {"error": "hallucinated_reference", "confidence": 0.99},
    }


def _submit_fail(base_ts: dt.datetime, i: int) -> Dict:
    return {
        "ts": _iso(base_ts, i * 45),
        "kind": "agent.submit",
        "status": "fail",
        "payload": {"error": "patch_format", "attempt": 1},
    }


# ─── per-agent episode generator ─────────────────────────────────────────────

def generate_agent_episode(
    agent_id: int,
    episode: int,
    seed: int,
) -> List[Dict]:
    rng = random.Random(seed * 1000 + agent_id * 100 + episode)
    # Base timestamp: episode 1 = Jan 1 2025, each episode +1 day
    base_ts = dt.datetime(2025, 1, 1, 9, 0, 0) + dt.timedelta(days=episode - 1)

    events: List[Dict] = []
    is_clean = agent_id in CLEAN_IDS

    for i in range(EVENTS_PER_EPISODE):
        inject_failure = (not is_clean) and (rng.random() < FAILURE_RATE)

        if not inject_failure:
            events.append(_ok_event(rng, base_ts, i))
            continue

        # Inject failure based on class
        if agent_id in STRUCTURAL_IDS:
            events.append(_structural_failure(base_ts, i))

        elif agent_id in DRIFT_IDS:
            events.append(_drift_failure(base_ts, i, episode))

        elif agent_id in NOVEL_IDS:
            # Novel failure only appears in episodes 4-7
            if episode >= 4:
                events.append(_novel_failure(base_ts, i))
            else:
                # Before episode 4: novel agents look clean (no signal yet)
                events.append(_ok_event(rng, base_ts, i))

    # Always add at least one agent.submit event (realistic)
    submit_status = "fail" if (not is_clean and rng.random() < 0.4) else "ok"
    events.append({
        "ts": _iso(base_ts, EVENTS_PER_EPISODE * 45 + 10),
        "kind": "agent.submit",
        "status": submit_status,
        "payload": {"patch_lines": rng.randint(10, 80)},
    })

    return events


# ─── write helpers ───────────────────────────────────────────────────────────

def write_fleet(seed: int, n_agents: int, n_episodes: int, out_root: Path):
    out_root.mkdir(parents=True, exist_ok=True)

    manifest = {
        "seed": seed,
        "n_agents": n_agents,
        "n_episodes": n_episodes,
        "agent_classes": {
            "structural": STRUCTURAL_IDS[:n_agents],
            "drift": [i for i in DRIFT_IDS if i < n_agents],
            "novel": [i for i in NOVEL_IDS if i < n_agents],
            "clean_ids": sorted(CLEAN_IDS & set(range(n_agents))),
        },
        "failure_rate": FAILURE_RATE,
        "events_per_episode": EVENTS_PER_EPISODE,
    }

    total = 0
    for agent_id in range(n_agents):
        agent_dir = out_root / f"agent_{agent_id:02d}"
        agent_dir.mkdir(exist_ok=True)
        for episode in range(1, n_episodes + 1):
            events = generate_agent_episode(agent_id, episode, seed)
            path = agent_dir / f"episode_{episode:02d}.jsonl"
            with open(path, "w", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev) + "\n")
            total += len(events)

    manifest_path = out_root / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Fleet written: {n_agents} agents × {n_episodes} episodes = {total} events")
    print(f"Manifest: {manifest_path}")
    return manifest


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate VIGIL fleet eval logs")
    parser.add_argument("--seed",     type=int, default=42)
    parser.add_argument("--episodes", type=int, default=7)
    parser.add_argument("--agents",   type=int, default=20)
    parser.add_argument("--out",      type=str, default="logs/fleet")
    args = parser.parse_args()

    out_root = SCAFFOLD / args.out
    write_fleet(args.seed, args.agents, args.episodes, out_root)


if __name__ == "__main__":
    main()
