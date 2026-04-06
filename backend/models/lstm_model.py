"""
lstm_model.py — Lightweight LSTM for demand forecasting.

Architecture:
  Input:  sequence of (load, generation, weather) at 10 timesteps → shape (batch, 10, 3)
  LSTM:   2 layers, hidden_size=32
  Output: predicted next-step demand (scalar)

Training:
  Synthetic dataset is generated once at startup (500 samples).
  No GPU required — runs on CPU in <2 seconds.
"""

import numpy as np  # type: ignore
import torch  # type: ignore
import torch.nn as nn  # type: ignore
from sklearn.preprocessing import MinMaxScaler  # type: ignore


# -----------------------------------------------------------------------
# Model Definition
# -----------------------------------------------------------------------

class LSTMForecaster(nn.Module):
    """
    2-layer LSTM followed by a fully-connected head predicting next demand.
    """

    def __init__(self, input_size: int = 3, hidden_size: int = 32,
                 num_layers: int = 2, output_size: int = 1):
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=0.1,
        )
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, 16),
            nn.ReLU(),
            nn.Linear(16, output_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, input_size)
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_size)
        out, _ = self.lstm(x, (h0, c0))
        # Take only the last timestep's hidden state
        return self.fc(out[:, -1, :])


# -----------------------------------------------------------------------
# Dataset Generation
# -----------------------------------------------------------------------

def generate_synthetic_data(n_samples: int = 600, seq_len: int = 10):
    """
    Generate synthetic grid time-series data.

    Simulates a day-night load cycle with random noise and occasional spikes.
    Returns (X, y) where:
      X: (n_samples, seq_len, 3)  — [load, generation, weather]
      y: (n_samples,)             — next-step load
    """
    np.random.seed(42)
    t = np.linspace(0, 4 * np.pi, n_samples + seq_len)

    # Day-night load cycle
    base_load = 0.5 + 0.3 * np.sin(t) + 0.1 * np.random.randn(len(t))
    base_load = np.clip(base_load, 0.1, 1.5)

    # Generation follows solar pattern (peaks at midday)
    generation = 0.6 + 0.25 * np.cos(t + np.pi / 4) + 0.05 * np.random.randn(len(t))
    generation = np.clip(generation, 0.05, 1.2)

    # Weather proxy: random storms
    weather = np.zeros(len(t))
    storm_starts = np.random.randint(0, len(t) - 20, 5)
    for s in storm_starts:
        weather[s:s + 20] = np.random.uniform(0.6, 1.0)

    X_list, y_list = [], []
    for i in range(n_samples):
        window = np.stack([
            base_load[i:i + seq_len],
            generation[i:i + seq_len],
            weather[i:i + seq_len],
        ], axis=1)
        X_list.append(window)
        y_list.append(base_load[i + seq_len])

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.float32)
    return X, y


# -----------------------------------------------------------------------
# Training Helper
# -----------------------------------------------------------------------

def train_lstm(model: LSTMForecaster, X: np.ndarray, y: np.ndarray,
               epochs: int = 30, lr: float = 1e-3) -> list:
    """
    Train the LSTM on synthetic data. Returns loss history.
    Designed to complete in <5 seconds on CPU.
    """
    model.train()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()

    X_t = torch.tensor(X)
    y_t = torch.tensor(y).unsqueeze(1)

    losses = []
    for epoch in range(epochs):
        optimizer.zero_grad()
        pred = model(X_t)  # type: ignore
        loss = criterion(pred, y_t)
        loss.backward()
        optimizer.step()
        losses.append(loss.item())

    return losses


# -----------------------------------------------------------------------
# Top-Level Manager
# -----------------------------------------------------------------------

class DemandForecaster:
    """
    High-level wrapper used by the API.
    - Generates synthetic training data at startup
    - Pre-trains the LSTM (fast, CPU-only)
    - Provides predict() for the /predict endpoint
    """

    SEQ_LEN = 10

    def __init__(self):
        self.model = LSTMForecaster()
        self.scaler = MinMaxScaler()
        self._pretrain()

    def _pretrain(self):
        """Train the LSTM on synthetic data at startup."""
        print("[LSTM] Generating synthetic dataset...")
        X, y = generate_synthetic_data(n_samples=500, seq_len=self.SEQ_LEN)

        # Fit scaler on flattened features
        flat = X.reshape(-1, 3)
        self.scaler.fit(flat)

        # Normalise
        X_norm = np.array([
            self.scaler.transform(x) for x in X
        ], dtype=np.float32)

        print("[LSTM] Training on CPU (30 epochs)...")
        losses = train_lstm(self.model, X_norm, y, epochs=30)
        print(f"[LSTM] Training complete. Final loss: {losses[-1]:.6f}")
        self.model.eval()

    def predict(self, sequence: list) -> float:
        """
        Predict the next demand given a list of 10 [load, gen, weather] triples.

        Args:
            sequence: list of 10 lists, each [load, generation, weather]
        Returns:
            Predicted next load (float, unnormalised)
        """
        if len(sequence) < self.SEQ_LEN:
            sequence = [[0.5, 0.5, 0.0]] * (self.SEQ_LEN - len(sequence)) + sequence

        seq_arr = np.array(sequence[-self.SEQ_LEN:], dtype=np.float32)  # type: ignore
        seq_norm = self.scaler.transform(seq_arr)
        x_t = torch.tensor(seq_norm).unsqueeze(0)  # (1, 10, 3)

        with torch.no_grad():
            pred = self.model(x_t).item()  # type: ignore

        # Clamp to reasonable demand range
        return float(np.clip(pred, 0.05, 2.0))
