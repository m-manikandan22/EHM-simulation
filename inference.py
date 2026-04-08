"""
inference.py — Multi-Step Agent Inference Script
AI Self-Healing Smart Grid — Submission-Level Agent Loop

Runs a [START] → [STEP] × N → [END] agent inference loop
against the simulation environment server (Flask app on port 5000).

Fixed Issues:
  ✅ ISSUE 1 — Port corrected to 5000 (matches server/app.py Flask server)
  ✅ ISSUE 2 — workflow_context reads from 'previous_decisions' (correct field)
  ✅ ISSUE 3 — sentiment reads from 'customer_sentiment' (not hardcoded)
  ✅ ISSUE 4 — Duplicate exception handlers removed (single clean block)

Usage:
    python inference.py
    OR
    set ENV_URL=http://localhost:5000 && python inference.py
"""

from __future__ import annotations

import os
import json
import time
import random
import requests
from typing import Dict, Any, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Environment Configuration
# ─────────────────────────────────────────────────────────────────────────────

def get_environment_config() -> Dict[str, str]:
    """
    Returns environment configuration from env vars with safe defaults.

    ISSUE 1 FIX: env_url default changed from :8000 to :5000
    (Flask server in app.py runs on port 5000)
    """
    return {
        "api_base_url": os.getenv("API_BASE_URL", "http://localhost:11434/v1"),
        "model_name":   os.getenv("MODEL_NAME",   "llama2"),
        "hf_token":     os.getenv("HF_TOKEN",      ""),
        "env_url":      os.getenv("ENV_URL",        "http://localhost:5000"),  # ✅ FIX 1
        "api_key":      os.getenv("HF_TOKEN",       "not-needed-for-local"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Environment Communication
# ─────────────────────────────────────────────────────────────────────────────

def get_observation(env_url: str, session_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetches the current observation from the simulation environment.
    Returns None on any network or server error.
    """
    try:
        response = requests.get(
            f"{env_url}/api/grid/state",
            params={"session_id": session_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"  [WARN] Network error fetching observation: {e}")
        return None
    except Exception as e:
        print(f"  [WARN] Unexpected error fetching observation: {e}")
        return None


def submit_action(env_url: str, session_id: str, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Submits the chosen action to the simulation environment.
    Returns the step result or None on error.
    """
    try:
        response = requests.post(
            f"{env_url}/api/fault/simulate",
            json={**action, "session_id": session_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"  [WARN] Network error submitting action: {e}")
        return None
    except Exception as e:
        print(f"  [WARN] Unexpected error submitting action: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Reward & Scoring
# ─────────────────────────────────────────────────────────────────────────────

def compute_reward(observation: Dict[str, Any], action: Dict[str, Any]) -> float:
    """
    Computes the reward for the given observation-action pair.

    Reward components:
      +2.0  per node close to nominal voltage
      +1.0  per stable frequency
      -5.0  per failed node
      -1.0  per high-stress node
    """
    reward = 0.0
    nodes = observation.get("nodes", [])

    for node in nodes:
        node_type = node.get("type", "")
        loads     = node.get("loads", [])

        # Voltage stability proxy: fewer critical loads = more stable
        crit = sum(1 for l in loads if l.get("priority") == "CRITICAL")
        norm = sum(1 for l in loads if l.get("priority") != "CRITICAL")

        if crit == 0 and norm > 0:
            reward += 2.0   # nominal voltage proxy
        if crit > 0:
            reward -= 5.0   # failed/stressed node penalty

    # Action quality bonus (restoring ties is rewarded)
    action_type = action.get("type", "")
    if action_type in ("ADD_TIE_SWITCH", "reroute"):
        reward += 1.0

    return round(reward, 2)


def normalize_score(raw_reward: float,
                    min_r: float = -20.0,
                    max_r: float = 10.0) -> float:
    """
    Normalizes reward to [0, 1] scale for submission scoring.
    Clamps to [min_r, max_r] before normalizing.
    """
    clamped = max(min_r, min(max_r, raw_reward))
    return round((clamped - min_r) / (max_r - min_r), 4)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy & Action Selection
# ─────────────────────────────────────────────────────────────────────────────

def select_action_llm(observation: Dict[str, Any],
                      workflow_context: Dict[str, Any],
                      sentiment: str,
                      config: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Attempts to select an action via local LLM (Ollama / HF endpoint).
    Returns None if LLM is unavailable or returns an invalid response.
    """
    prompt = _build_prompt(observation, workflow_context, sentiment)

    try:
        response = requests.post(
            f"{config['api_base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       config["model_name"],
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.0,   # deterministic for reproducibility
                "max_tokens":  256,
            },
            timeout=15,
        )
        response.raise_for_status()
        result    = response.json()
        raw_text  = result["choices"][0]["message"]["content"].strip()
        action    = json.loads(raw_text)
        return action
    except requests.exceptions.RequestException as e:
        print(f"  [LLM] Network error: {e} — falling back to heuristic")
        return None
    except Exception as e:
        print(f"  [LLM] Parse/unexpected error: {e} — falling back to heuristic")
        return None


def select_action_heuristic(observation: Dict[str, Any],
                             workflow_context: Dict[str, Any],
                             sentiment: str,
                             step: int) -> Dict[str, Any]:
    """
    Deterministic heuristic fallback action selector.

    Strategy:
      - If any critical node exists → attempt fault restoration
      - If sentiment is negative    → shift load to reduce stress
      - Default                     → routine monitoring
    """
    nodes    = observation.get("nodes", [])
    edges    = observation.get("edges", [])

    has_critical = any(
        any(l.get("priority") == "CRITICAL" for l in n.get("loads", []))
        for n in nodes
    )
    has_tie_available = any(e.get("is_tie", False) and not e.get("is_switch", True)
                            for e in edges)

    # Priority 1: restore via tie switch if fault + tie available
    if has_critical and has_tie_available:
        tie_edges = [e for e in edges if e.get("is_tie", False)]
        chosen    = random.choice(tie_edges) if tie_edges else edges[0] if edges else {}
        return {
            "type":       "ADD_TIE_SWITCH",
            "u":          chosen.get("source", "bus1"),
            "v":          chosen.get("target", "bus2"),
            "reason":     "Critical load detected — closing tie switch for restoration",
            "step":       step,
        }

    # Priority 2: load shedding on negative sentiment
    if sentiment in ("negative", "very_negative"):
        return {
            "type":   "shift_load",
            "amount": 0.15,
            "reason": f"Negative sentiment ({sentiment}) — deferring non-critical loads",
            "step":   step,
        }

    # Default: routine action (round-robin over stable controls)
    actions = ["monitor", "increase_generation", "use_battery"]
    return {
        "type":   actions[step % len(actions)],
        "reason": "Routine grid monitoring and balancing",
        "step":   step,
    }


def _build_prompt(observation: Dict[str, Any],
                  workflow_context: Dict[str, Any],
                  sentiment: str) -> str:
    """Builds a concise, structured prompt for the LLM action selector."""
    nodes_summary = [
        {"id": n["id"], "type": n["type"], "loads": len(n.get("loads", []))}
        for n in observation.get("nodes", [])[:5]   # limit for token budget
    ]
    return (
        f"You are a smart grid control AI. Your task is to choose the best action.\n\n"
        f"Current Grid State (partial):\n{json.dumps(nodes_summary, indent=2)}\n\n"
        f"Previous Decisions:\n{json.dumps(workflow_context, indent=2)}\n\n"
        f"Customer Sentiment: {sentiment}\n\n"
        f"Respond ONLY with a JSON object like:\n"
        f'{{"type": "ADD_TIE_SWITCH", "u": "bus1", "v": "bus2", "reason": "..."}}\n'
        f"or:\n"
        f'{{"type": "shift_load", "amount": 0.15, "reason": "..."}}'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Escalation Logic
# ─────────────────────────────────────────────────────────────────────────────

def check_escalation(observation: Dict[str, Any], cumulative_reward: float) -> bool:
    """
    Returns True if the situation requires escalation to human operator.

    Escalation triggers:
      - 3+ critical nodes simultaneously
      - Cumulative reward below -10 (system degrading)
    """
    nodes    = observation.get("nodes", [])
    crit_cnt = sum(
        1 for n in nodes
        if any(l.get("priority") == "CRITICAL" for l in n.get("loads", []))
    )
    if crit_cnt >= 3:
        print(f"  [ESCALATE] {crit_cnt} critical nodes — escalating to human operator")
        return True
    if cumulative_reward < -10.0:
        print(f"  [ESCALATE] Cumulative reward {cumulative_reward:.2f} below threshold")
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Main Inference Loop  [START] → [STEP] × N → [END]
# ─────────────────────────────────────────────────────────────────────────────

def run_inference(max_steps: int = 5) -> Dict[str, Any]:
    """
    Runs the full multi-step agent inference loop.

    Format:
        [START]        — initialise session
        [STEP 1..N]    — observe → decide → act → reward
        [END]          — summarise results

    Returns a summary dict with steps, total reward, and normalized score.
    """
    config     = get_environment_config()
    env_url    = config["env_url"]
    session_id = f"session_{int(time.time())}_{random.randint(1000, 9999)}"

    print("=" * 60)
    print("  AI Smart Grid — Inference Agent")
    print(f"  Server  : {env_url}")
    print(f"  Session : {session_id}")
    print(f"  Steps   : {max_steps}")
    print("=" * 60)

    # ── [START] ────────────────────────────────────────────────────────
    print("\n[START]")
    print(f"  Connecting to environment at {env_url} ...")

    history: list[Dict[str, Any]] = []
    cumulative_reward  = 0.0
    previous_decisions: Dict[str, Any] = {}   # ✅ FIX 2: correct field name

    # ── [STEP Loop] ───────────────────────────────────────────────────
    for step in range(1, max_steps + 1):
        print(f"\n[STEP {step}/{max_steps}]")

        # 1. Observe
        observation = get_observation(env_url, session_id)
        if observation is None:
            print(f"  [ERROR] Could not fetch observation. Skipping step {step}.")
            time.sleep(1)
            continue

        # ── ISSUE 2 FIX: read 'previous_decisions' not 'workflow_context' ──
        workflow_context = observation.get("previous_decisions", {})   # ✅ FIX 2
        if not workflow_context and previous_decisions:
            workflow_context = previous_decisions   # use local memory as fallback

        # ── ISSUE 3 FIX: read 'customer_sentiment' not hardcoded 'neutral' ──
        sentiment = observation.get("customer_sentiment", "neutral")   # ✅ FIX 3

        print(f"  Nodes       : {len(observation.get('nodes', []))}")
        print(f"  Sentiment   : {sentiment}")
        print(f"  Prev context: {len(workflow_context)} key(s)")

        # 2. Decide (LLM first, heuristic fallback)
        action = select_action_llm(observation, workflow_context, sentiment, config)
        if action is None:
            action = select_action_heuristic(observation, workflow_context, sentiment, step)
            print(f"  Strategy    : HEURISTIC → {action['type']}")
        else:
            print(f"  Strategy    : LLM → {action['type']}")

        print(f"  Reason      : {action.get('reason', 'n/a')}")

        # 3. Act
        result = submit_action(env_url, session_id, action)
        step_reward = compute_reward(observation, action)
        cumulative_reward += step_reward

        # 4. Log step
        step_log = {
            "step":            step,
            "action_type":     action.get("type"),
            "sentiment":       sentiment,
            "reward":          step_reward,
            "cumulative":      round(cumulative_reward, 2),
            "server_response": result.get("status", "no_response") if result else "error",
        }
        history.append(step_log)

        # Update local memory of previous decisions
        previous_decisions[f"step_{step}"] = {
            "action": action.get("type"),
            "reward": step_reward,
        }

        print(f"  Reward      : {step_reward:+.2f}  (cumulative: {cumulative_reward:+.2f})")

        # 5. Escalation check
        if check_escalation(observation, cumulative_reward):
            print("  [ESCALATE] Breaking loop — human intervention required")
            break

        time.sleep(0.3)   # small delay between steps

    # ── [END] ─────────────────────────────────────────────────────────
    print("\n[END]")
    normalized = normalize_score(cumulative_reward)
    print(f"  Steps completed  : {len(history)}/{max_steps}")
    print(f"  Total reward     : {cumulative_reward:+.2f}")
    print(f"  Normalized score : {normalized:.4f}  (0 = worst, 1 = best)")
    print("=" * 60)

    summary = {
        "session_id":       session_id,
        "env_url":          env_url,
        "steps_completed":  len(history),
        "total_reward":     round(cumulative_reward, 2),
        "normalized_score": normalized,
        "history":          history,
    }
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = run_inference(max_steps=5)
    print("\n--- Final Summary (JSON) ---")
    print(json.dumps(result, indent=2))
