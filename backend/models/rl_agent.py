"""
rl_agent.py — DQN Reinforcement Learning agent for smart grid control.

State  (per node): [voltage, frequency/50, load, generation, stress] × 8 nodes = 40-dim
Actions (5 discrete):
  0 → Increase generation on all substations
  1 → Use battery storage (highest-deficit node)
  2 → Use supercapacitor (largest spike node)
  3 → Shift load (reduce 10% on house nodes)
  4 → Reroute energy (activate cross-links, trigger multi-agent share)

Reward:
  +2   per node close to nominal voltage (|v−1| < 0.05)
  +1   per node close to nominal frequency (|f−50| < 0.2)
  -5   per failed/isolated node
  -1   per node with stress > 0.7
  -0.1 × total_energy_loss (cost proxy)

DQN details:
  - 2-hidden-layer MLP (64→64)
  - Replay buffer (capacity 2000)
  - ε-greedy exploration (ε decays 1.0 → 0.05)
  - Target network updated every 20 steps
  - Warms up on 100 random experience samples at startup
"""

from __future__ import annotations

import random
import math
from collections import deque
from typing import Optional

import numpy as np  # type: ignore
import torch  # type: ignore
import torch.nn as nn  # type: ignore
import torch.optim as optim  # type: ignore


# -----------------------------------------------------------------------
# Action Catalogue
# -----------------------------------------------------------------------

ACTIONS = [
    {
        "id": 0,
        "name": "increase_generation",
        "label": "⚡ Boosting generation on substations",
        "color": "green",
    },
    {
        "id": 1,
        "name": "use_battery",
        "label": "🔋 Drawing from battery storage",
        "color": "blue",
    },
    {
        "id": 2,
        "name": "use_supercapacitor",
        "label": "⚡ Discharging supercapacitor (spike suppression)",
        "color": "cyan",
    },
    {
        "id": 3,
        "name": "shift_load",
        "label": "📊 Deferring non-critical loads",
        "color": "yellow",
    },
    {
        "id": 4,
        "name": "reroute_energy",
        "label": "🔀 Rerouting energy via alternate paths",
        "color": "purple",
    },
]

N_ACTIONS = len(ACTIONS)
STATE_DIM = 52   # (3 Gen + 6 Sub) × 5 features + 7 global features


# -----------------------------------------------------------------------
# Neural Network Q-function
# -----------------------------------------------------------------------

class DQNetwork(nn.Module):
    """Simple MLP approximating Q(s, a) for all actions simultaneously."""

    def __init__(self, state_dim: int = STATE_DIM, n_actions: int = N_ACTIONS):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            nn.ReLU(),
            nn.Linear(64, n_actions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# -----------------------------------------------------------------------
# Replay Buffer
# -----------------------------------------------------------------------

class ReplayBuffer:
    def __init__(self, capacity: int = 2000):
        self.buf = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buf.append((state, action, reward, next_state, done))

    def sample(self, batch_size: int):
        batch = random.sample(self.buf, batch_size)
        s, a, r, ns, d = zip(*batch)
        return (
            torch.tensor(np.array(s), dtype=torch.float32),
            torch.tensor(a, dtype=torch.long),
            torch.tensor(r, dtype=torch.float32),
            torch.tensor(np.array(ns), dtype=torch.float32),
            torch.tensor(d, dtype=torch.float32),
        )

    def __len__(self):
        return len(self.buf)


# -----------------------------------------------------------------------
# DQN Agent
# -----------------------------------------------------------------------

class DQNAgent:
    """
    Deep Q-Network agent for smart grid control.
    Designed for CPU-only, fast iteration — suitable for hackathon demo.
    """

    GAMMA = 0.95
    LR = 1e-3
    BATCH_SIZE = 32
    EPSILON_START = 1.0
    EPSILON_END = 0.05
    EPSILON_DECAY = 200   # steps to decay ε
    TARGET_UPDATE = 20    # steps between target network sync

    def __init__(self):
        self.policy_net = DQNetwork()
        self.target_net = DQNetwork()
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()

        self.optimizer = optim.Adam(self.policy_net.parameters(), lr=self.LR)
        self.buffer = ReplayBuffer()
        self.criterion = nn.SmoothL1Loss()   # Huber loss

        self.steps_done = 0
        self.epsilon = self.EPSILON_START
        self._last_action_id = 0
        self._last_reasoning = ""
        
        # Warm up is now called externally via smart_warmup(grid)

    # ------------------------------------------------------------------
    # Exploration schedule
    # ------------------------------------------------------------------

    def _get_epsilon(self) -> float:
        """Exponential ε decay."""
        eps = self.EPSILON_END + (self.EPSILON_START - self.EPSILON_END) * \
              math.exp(-self.steps_done / self.EPSILON_DECAY)
        return eps

    # ------------------------------------------------------------------
    # Rule-Guided Warm-Up
    # ------------------------------------------------------------------

    def smart_warmup(self, grid, scada_instance=None):
        """
        Rule-guided warmup to populate replay buffer with intentional experiences.
        Learns from an 'expert policy' to kickstart DQN stability.
        """
        print("[DQN] Warming up replay buffer via expert imitation...")
        
        for _ in range(150):
            state = grid.get_rl_state()
            state_dict = grid.get_state()
            
            sys_info = state_dict.get("system", {})
            nodes = state_dict.get("nodes", {})
            balance = sys_info.get("balance", 0.0)
            num_failed = sum(1 for n in nodes.values() if n.get("failed"))
            num_isolated = sum(1 for n in nodes.values() if n.get("isolated"))
            has_spike = any(n.get("load", 0) > 1.2 for n in nodes.values())
            
            # Simple rule-based expert
            if num_failed > 0 or num_isolated > 0:
                action_id = 4 # reroute
            elif has_spike:
                action_id = 2 # supercapacitor
            elif balance < -0.3:
                action_id = 0 # increase gen
            elif balance < -0.1:
                action_id = 1 # battery
            else:
                action_id = 3 # drop load
                
            action_name = str(ACTIONS[action_id]["name"])
            
            # Apply and step
            if scada_instance:
                scada_instance._dispatch_control_signal(action_name, state_dict, grid)
            grid.step()
            
            next_state_dict = grid.get_state()
            next_state = grid.get_rl_state()
            reward = self.compute_reward(next_state_dict, action_name)
            
            s = np.array(state, dtype=np.float32)
            ns = np.array(next_state, dtype=np.float32)
            self.buffer.push(s, action_id, reward, ns, False)
            
            # Inject random failure to teach recovery (disabled for clean startup)
            # if random.random() < 0.02:
            #     grid.inject_failure(random.choice(["H0", "H1", "H2", "H3", "H4"]))
                
        # Run a batch of training to initialize network
        for _ in range(40):
            self._train_step()
            
        grid.reset()
        grid.heal_all()  # Ensure clean startup state
        print("[DQN] Smart warmup complete.")

    # ------------------------------------------------------------------
    # Action Selection
    # ------------------------------------------------------------------

    def select_action(self, state: list, predicted_load: float = 0.5,
                      grid_state: Optional[dict] = None) -> dict:
        """
        Choose an action given the current state vector and context.
        Builds a human-readable reasoning string explaining the choice.

        Returns dict with: action_id, action_name, label, color, reasoning
        """
        state_vec = np.array(state, dtype=np.float32)
        self.epsilon = self._get_epsilon()
        self.steps_done += 1
        
        # Action Masking Logic
        valid_actions = []
        if grid_state:
            sys_info = grid_state.get("system", {})
            nodes = grid_state.get("nodes", {})
            balance = sys_info.get("balance", 0.0)
            failed = any(n.get("failed") for n in nodes.values())
            isolated = any(n.get("isolated") for n in nodes.values())
            spike = any(n.get("load", 0) > 1.2 for n in nodes.values())
            
            if balance < -0.1: valid_actions.extend([0, 1])
            if spike: valid_actions.append(2)
            if failed or isolated: valid_actions.append(4)
            valid_actions.append(3) # Load shift is always an option
                
        if not valid_actions:
            valid_actions = [0, 1, 2, 3, 4]
            
        valid_actions = list(set(valid_actions))

        # Action Selection and Confidence
        confidence = 0.0
        with torch.no_grad():
            q_vals = self.policy_net(torch.tensor(state_vec).unsqueeze(0))  # type: ignore
            mask = torch.full((1, N_ACTIONS), float('-inf'))
            for a in valid_actions:
                mask[0, a] = 0.0
            
            masked_q = q_vals + mask
            best_action = masked_q.argmax(dim=1).item()
            
            # Extract confidence using Softmax over valid actions
            valid_q = q_vals[mask == 0.0]
            if len(valid_q) > 0:
                probs = torch.nn.functional.softmax(valid_q, dim=0)
                confidence = probs.max().item()

        # Epsilon Greedy Override
        if random.random() < self.epsilon:
            action_id = random.choice(valid_actions)
            confidence = 1.0 / len(valid_actions)  # Low confidence on random jump
        else:
            action_id = best_action

        self._last_action_id = action_id
        action = ACTIONS[action_id]

        # Build reasoning
        reasoning = self._build_reasoning(action_id, predicted_load, grid_state)
        self._last_reasoning = reasoning

        return {
            "action_id": action_id,
            "action_name": action["name"],
            "label": action["label"],
            "color": action["color"],
            "reasoning": reasoning,
            "epsilon": round(float(self.epsilon), 3),  # type: ignore
            "confidence": round(float(confidence), 3),
        }

    def _build_reasoning(self, action_id: int, predicted_load: float,
                          grid_state: Optional[dict]) -> str:
        """Generate a human-readable explanation of why this action was chosen."""
        ctx_parts = []

        if grid_state:
            sys = grid_state.get("system", {})
            balance = sys.get("balance", 0)
            health = sys.get("health_score", 1.0)
            avg_v = sys.get("avg_voltage", 1.0)
            storm = grid_state.get("storm_active", False)
            failed = [nid for nid, n in grid_state.get("nodes", {}).items() if n.get("failed")]

            if storm:
                ctx_parts.append("🌩️ Storm active — high demand")
            if failed:
                ctx_parts.append(f"⚠️ Node(s) {failed} are failed")
            if predicted_load > 0.8:
                ctx_parts.append(f"📈 High demand predicted ({predicted_load:.2f} MW)")
            elif predicted_load < 0.3:
                ctx_parts.append(f"📉 Low demand predicted ({predicted_load:.2f} MW)")
            if balance < -0.3:
                ctx_parts.append("🔴 Grid deficit detected")
            elif balance > 0.5:
                ctx_parts.append("🟢 Grid surplus — stable")
            if health < 0.5:
                ctx_parts.append(f"🚨 System health low ({health:.0%})")
            if abs(avg_v - 1.0) > 0.08:
                ctx_parts.append(f"⚡ Voltage deviation ({avg_v:.3f} p.u.)")

        context = " | ".join(ctx_parts) if ctx_parts else "Routine monitoring"
        action_label = ACTIONS[action_id]["label"]

        return f"{context} → {action_label}"

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def store_experience(self, state: list, action_id: int, reward: float,
                         next_state: list, done: bool = False):
        """Push a transition to the replay buffer and train."""
        s = np.array(state, dtype=np.float32)
        ns = np.array(next_state, dtype=np.float32)
        self.buffer.push(s, action_id, reward, ns, done)

        if len(self.buffer) >= self.BATCH_SIZE:
            self._train_step()

        # Periodically sync target network
        if self.steps_done % self.TARGET_UPDATE == 0:
            self.target_net.load_state_dict(self.policy_net.state_dict())

    def _train_step(self):
        """One gradient-descent step on a random batch from the replay buffer."""
        if len(self.buffer) < self.BATCH_SIZE:
            return

        states, actions, rewards, next_states, dones = self.buffer.sample(self.BATCH_SIZE)

        # Zero gradients BEFORE forward pass to avoid stale graph references
        self.optimizer.zero_grad()

        # Current Q-values for taken actions (fresh forward pass)
        q_values = self.policy_net(states).gather(1, actions.unsqueeze(1)).squeeze(-1)

        # Target Q-values (Bellman equation) — fully detached from computation graph
        with torch.no_grad():
            next_q = self.target_net(next_states).max(1)[0]
            targets = (rewards + self.GAMMA * next_q * (1 - dones)).detach()

        loss = self.criterion(q_values, targets)
        loss.backward()
        # Gradient clipping for stability
        nn.utils.clip_grad_norm_(self.policy_net.parameters(), 1.0)
        self.optimizer.step()

    # ------------------------------------------------------------------
    # Reward Computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_reward(grid_state: dict, action_name: str = "") -> float:
        """
        Compute a sharp, intentional reward focused on stability, balance, and minimizing failure.
        """
        reward = 0.0
        nodes = grid_state.get("nodes", {})
        system = grid_state.get("system", {})

        avg_voltage = system.get("avg_voltage", 1.0)
        avg_freq = system.get("avg_frequency", 50.0)
        balance = system.get("balance", 0.0)
        total_energy_loss = system.get("total_energy_loss", 0.0)

        num_failed = sum(1 for n in nodes.values() if n.get("failed"))
        num_isolated = sum(1 for n in nodes.values() if n.get("isolated"))

        # Stability (HIGH priority)
        reward += 5.0 * (1.0 - abs(avg_voltage - 1.0) / 0.1)
        reward += 3.0 * (1.0 - abs(avg_freq - 50.0) / 1.5)

        # Balance (VERY IMPORTANT)
        reward -= 4.0 * abs(balance)

        # Failure penalty (CRITICAL)
        reward -= 10.0 * num_failed
        reward -= 6.0 * num_isolated

        # Efficiency
        reward -= 0.2 * total_energy_loss

        # Smart behavior conditional bonuses
        if action_name == "use_supercapacitor" and any(n.get("load", 0) > 1.2 for n in nodes.values()):
            reward += 2.0
            
        if action_name == "reroute_energy" and (num_failed > 0 or num_isolated > 0):
            reward += 3.0

        return float(reward)
