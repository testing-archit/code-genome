"""Optional recurrent challenger for instability forecasting (``instability-gru@1``).

A single-layer GRU reads each component's last ``window`` periods, oldest to newest, and its
final hidden state is combined with the static features (fix rate so far, size) to predict a
bug fix in the next period. It consumes exactly the flat rows the logistic model uses, so
both are trained and scored on the same samples.

PyTorch is an optional dependency (``pip install code-genome[deep]``). Without it,
``torch_available()`` is false and callers keep the logistic regression. Training is
full-batch, CPU-only, and seeded, so results are reproducible.
"""

import importlib.util
from typing import Any

import numpy as np

GRU_VERSION = "instability-gru@1"
HIDDEN = 16
EPOCHS = 300
LEARNING_RATE = 0.01
WEIGHT_DECAY = 1e-3
SEED = 7


def torch_available() -> bool:
    return importlib.util.find_spec("torch") is not None


class GruForecaster:
    """Fit/predict on flat rows: ``window * steps_features`` lagged values (lag 0 first),
    then ``static`` trailing features."""

    def __init__(self, window: int, step_features: int, static: int) -> None:
        self.window = window
        self.step_features = step_features
        self.static = static
        self._model: Any = None
        self._mean: np.ndarray | None = None
        self._scale: np.ndarray | None = None

    def _tensors(self, x: np.ndarray) -> tuple[Any, Any]:
        import torch  # noqa: PLC0415

        assert self._mean is not None and self._scale is not None
        z = (x - self._mean) / self._scale
        lagged = z[:, : self.window * self.step_features].reshape(
            len(z), self.window, self.step_features
        )
        sequence = lagged[:, ::-1, :].copy()  # lag 0 is the newest period; feed oldest first
        static = z[:, self.window * self.step_features :]
        return (
            torch.tensor(sequence, dtype=torch.float32),
            torch.tensor(static, dtype=torch.float32),
        )

    def fit(self, x: np.ndarray, y: np.ndarray) -> "GruForecaster":
        import torch  # noqa: PLC0415
        from torch import nn  # noqa: PLC0415

        torch.manual_seed(SEED)
        self._mean = x.mean(axis=0)
        self._scale = np.where(x.std(axis=0) > 0, x.std(axis=0), 1.0)
        step_features, static = self.step_features, self.static

        class Network(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.gru = nn.GRU(step_features, HIDDEN, batch_first=True)
                self.head = nn.Linear(HIDDEN + static, 1)

            def forward(self, sequence: Any, extra: Any) -> Any:
                _, hidden = self.gru(sequence)
                return self.head(torch.cat([hidden[-1], extra], dim=1)).squeeze(1)

        model = Network()
        sequence, extra = self._tensors(x)
        target = torch.tensor(y, dtype=torch.float32)
        positives = float(y.sum())
        weight = torch.tensor((len(y) - positives) / max(positives, 1.0))
        loss_fn = nn.BCEWithLogitsLoss(pos_weight=weight)
        optimiser = torch.optim.Adam(
            model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY
        )
        model.train()
        for _ in range(EPOCHS):
            optimiser.zero_grad()
            loss = loss_fn(model(sequence, extra), target)
            loss.backward()
            optimiser.step()
        model.eval()
        self._model = model
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        import torch  # noqa: PLC0415

        if self._model is None:
            raise RuntimeError("GruForecaster is not fitted.")
        sequence, extra = self._tensors(x)
        with torch.no_grad():
            scores = torch.sigmoid(self._model(sequence, extra))
        return np.asarray(scores.numpy(), dtype=float)

    def reset_contributions(self, x_now: np.ndarray, typical: np.ndarray) -> np.ndarray:
        """Probability change when each feature alone is reset to its typical value."""
        base = self.predict_proba(x_now)
        out = np.zeros_like(x_now, dtype=float)
        for column in range(x_now.shape[1]):
            changed = x_now.copy()
            changed[:, column] = typical[column]
            out[:, column] = base - self.predict_proba(changed)
        return out
