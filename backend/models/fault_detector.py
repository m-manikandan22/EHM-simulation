"""
fault_detector.py — AI-based fault detection for the Smart Grid.

Architecture:
  - Anomaly Scorer : computes a per-node fault score from voltage, frequency,
                     load, and generation using physics thresholds.
  - ANN Classifier : lightweight 3-layer MLP trained on synthetic fault
                     patterns to classify the fault type.

Output per node:
  - fault_score   : 0.0 – 1.0  (anomaly severity)
  - fault_type    : "overload" | "undervoltage" | "frequency_deviation" |
                    "generation_loss" | "normal"
  - is_fault      : bool (score > 0.55)

No GPU required — runs on CPU in < 1 ms per call.
"""

import math
import random
import numpy as np          # type: ignore
import torch                # type: ignore
import torch.nn as nn       # type: ignore


# ── Fault class labels ────────────────────────────────────────────────
FAULT_TYPES = ["normal", "overload", "undervoltage", "frequency_deviation", "generation_loss"]
FAULT_THRESHOLD = 0.55   # score above this → fault declared


# ── ANN Classifier ────────────────────────────────────────────────────

class FaultClassifierANN(nn.Module):
    """
    3-layer MLP.
    Input  : 5 features [voltage, frequency_norm, load, generation, stress]
    Output : 5 class logits (fault types)
    """
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(5, 32),
            nn.ReLU(),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, len(FAULT_TYPES)),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ── Synthetic Training Data ───────────────────────────────────────────

def _generate_fault_data(n: int = 800):
    """
    Generate labelled synthetic fault samples.
    Each sample: [voltage, freq_norm, load, generation, stress] → label
    """
    X, y = [], []
    rng = random.Random(99)

    for _ in range(n):
        label = rng.randint(0, len(FAULT_TYPES) - 1)
        fault = FAULT_TYPES[label]

        if fault == "normal":
            v   = rng.uniform(0.97, 1.03)
            f   = rng.uniform(49.8, 50.2) / 50.0
            ld  = rng.uniform(0.2, 0.7)
            gen = rng.uniform(0.3, 0.9)
            st  = rng.uniform(0.0, 0.15)
        elif fault == "overload":
            v   = rng.uniform(0.88, 0.96)
            f   = rng.uniform(49.0, 49.7) / 50.0
            ld  = rng.uniform(1.4, 2.0)
            gen = rng.uniform(0.2, 0.6)
            st  = rng.uniform(0.6, 1.0)
        elif fault == "undervoltage":
            v   = rng.uniform(0.80, 0.91)
            f   = rng.uniform(49.2, 50.1) / 50.0
            ld  = rng.uniform(0.5, 1.2)
            gen = rng.uniform(0.1, 0.4)
            st  = rng.uniform(0.5, 0.9)
        elif fault == "frequency_deviation":
            v   = rng.uniform(0.93, 1.05)
            f   = rng.uniform(48.0, 49.3) / 50.0   # or 50.7-52
            f   = f if rng.random() > 0.5 else rng.uniform(50.7, 52.0) / 50.0
            ld  = rng.uniform(0.3, 1.0)
            gen = rng.uniform(0.2, 0.8)
            st  = rng.uniform(0.4, 0.85)
        else:  # generation_loss
            v   = rng.uniform(0.85, 0.97)
            f   = rng.uniform(49.0, 49.8) / 50.0
            ld  = rng.uniform(0.4, 1.0)
            gen = rng.uniform(0.0, 0.15)
            st  = rng.uniform(0.45, 0.9)

        X.append([v, f, ld, gen, st])
        y.append(label)

    return (np.array(X, dtype=np.float32),
            np.array(y, dtype=np.int64))


# ── High-level Manager ────────────────────────────────────────────────

class FaultDetector:
    """
    Wraps anomaly scoring + ANN fault classification.

    Usage in API:
        result = detector.analyse(grid.nodes)
        # result = {
        #   "alerts": [...],           # list of fault events
        #   "node_scores": {...},      # per-node score dict
        #   "system_health": 0.0-1.0,
        # }
    """

    def __init__(self):
        self.model = FaultClassifierANN()
        self._train()
        self.model.eval()

    def _train(self):
        print("[FaultDetector] Training ANN classifier on synthetic fault data...")
        X, y = _generate_fault_data(n=1000)
        X_t  = torch.tensor(X)
        y_t  = torch.tensor(y)

        opt  = torch.optim.Adam(self.model.parameters(), lr=1e-3)
        loss_fn = nn.CrossEntropyLoss()

        self.model.train()
        for _ in range(80):
            opt.zero_grad()
            loss = loss_fn(self.model(X_t), y_t)
            loss.backward()
            opt.step()

        print(f"[FaultDetector] ANN trained. Final loss: {loss.item():.4f}")

    # ── Per-node anomaly score (physics thresholds) ───────────────────
    @staticmethod
    def _anomaly_score(node) -> float:
        """
        3-component score:
          voltage deviation  (nominal = 1.0, dead-band ±0.05)
          frequency deviation (nominal = 50, dead-band ±0.3 Hz)
          load-generation imbalance ratio
        """
        v_dev = max(0.0, abs(node.voltage - 1.0) - 0.05) / 0.15
        f_dev = max(0.0, abs(node.frequency - 50.0) - 0.3) / 2.0
        if node.generation > 0.01:
            imb  = max(0.0, (node.load - node.generation) / max(node.load, 0.01))
        else:
            imb  = 1.0 if node.load > 0.1 else 0.0

        score = (0.35 * v_dev + 0.30 * f_dev + 0.35 * imb)
        return float(min(1.0, score))

    # ── ANN fault-type classification ────────────────────────────────
    def _classify(self, node) -> str:
        feat = torch.tensor([[
            node.voltage,
            node.frequency / 50.0,
            node.load,
            node.generation,
            node.stress_level,
        ]], dtype=torch.float32)
        with torch.no_grad():
            logits = self.model(feat)
            idx    = int(torch.argmax(logits, dim=1).item())
        return FAULT_TYPES[idx]

    # ── Public API ────────────────────────────────────────────────────
    def analyse(self, grid_nodes: dict) -> dict:
        """
        Analyse all grid nodes and return structured fault report.

        Args:
            grid_nodes: dict of node_id -> GridNode (live node objects)

        Returns:
            {
              "alerts":        list of alert dicts for faulted nodes,
              "node_scores":   {node_id: score},
              "system_health": float 0-1,
            }
        """
        alerts      = []
        node_scores = {}

        for nid, node in grid_nodes.items():
            # ── HARDWARE FAILURE: node is failed (physical fault) ─────
            if node.failed:
                node_scores[nid] = 1.0
                alerts.append({
                    "node_id":    nid,
                    "fault_type": "hard_failure",
                    "score":      1.0,
                    "severity":   "CRITICAL",
                    "message":    f"Node {nid} is offline (hardware failure).",
                })
                continue

            # ── DE-ENERGIZED: isolated but healthy ─────────────────────
            # This is NOT the same as a hardware fault.
            # The node is physically healthy but cut off pending FLISR restoration.
            # Score = 0.4 (elevated but not critical), severity = MEDIUM
            if node.isolated:
                node_scores[nid] = 0.4
                alerts.append({
                    "node_id":    nid,
                    "fault_type": "de_energized",
                    "score":      0.4,
                    "severity":   "MEDIUM",
                    "message":    f"{nid}: De-energized (isolated by sectionalization — awaiting FLISR).",
                })
                continue

            score      = self._anomaly_score(node)
            fault_type = self._classify(node) if score > 0.25 else "normal"
            node_scores[nid] = round(score, 4)

            if score >= FAULT_THRESHOLD:
                severity = "HIGH" if score >= 0.75 else "MEDIUM"
                alerts.append({
                    "node_id":    nid,
                    "fault_type": fault_type,
                    "score":      round(score, 4),
                    "severity":   severity,
                    "message":    _alert_msg(nid, fault_type, score),
                })

        # System health = 1 - mean(scores)
        if node_scores:
            system_health = float(max(0.0, 1.0 - sum(node_scores.values()) / len(node_scores)))
        else:
            system_health = 1.0

        # Sort by severity
        alerts.sort(key=lambda a: -a["score"])

        return {
            "alerts":        alerts[:10],    # top-10 worst
            "node_scores":   node_scores,
            "system_health": round(system_health, 4),
            "fault_count":   len(alerts),
        }


def _alert_msg(nid: str, fault_type: str, score: float) -> str:
    msgs = {
        "overload":            f"⚠️ {nid}: Overload detected (score {score:.2f}) — demand exceeds capacity.",
        "undervoltage":        f"⚡ {nid}: Undervoltage (score {score:.2f}) — voltage below safe threshold.",
        "frequency_deviation": f"📉 {nid}: Frequency deviation (score {score:.2f}) — grid instability.",
        "generation_loss":     f"🔋 {nid}: Generation loss (score {score:.2f}) — source failure.",
        "normal":              f"✅ {nid}: Anomaly detected (score {score:.2f}) — monitor closely.",
    }
    return msgs.get(fault_type, f"⚠ {nid} anomaly (score {score:.2f})")
