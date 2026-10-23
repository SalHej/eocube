"""
eocube.baselines
================

Interchangeable ``fit_predict`` implementations for :func:`eocube.pipeline.make_train_eval`.

Every baseline has the same signature::

    fit_predict(X_train, y_train, X_test, seed) -> y_pred

so swapping models is a one-line change in the experiment script and nothing else
moves. That matters for an ablation: the harness varies the *gate*, so the model
must stay fixed and identifiable.

Three tiers, in increasing order of what they require:

- :func:`ridge` — pure NumPy. No dependencies, runs anywhere, trains instantly.
  Use it to debug the whole pipeline before you have a GPU.
- :func:`random_forest` / :func:`gradient_boosting` — scikit-learn. Strong,
  CPU-only, and a genuinely competitive baseline for pixel-wise biophysical
  retrieval. Many published results do not beat a well-tuned random forest.
- :func:`torch_mlp` — PyTorch, GPU when available. The slot a real model goes
  into once HPC access arrives.

A note on expectations: for tabular, pixel-wise regression a random forest is
often as good as a deep network. If a deep model does not beat it, that is a
result about the task, not a failure of implementation, and it should be reported
rather than tuned away.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

__all__ = [
    "ridge", "make_ridge",
    "random_forest", "make_random_forest",
    "gradient_boosting",
    "torch_mlp", "make_torch_mlp",
    "standardise",
]


def standardise(X_train: np.ndarray, X_test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Z-score using training statistics only.

    Fitting the scaler on the full dataset leaks test information into training.
    It is a small leak, but it is free to avoid.
    """
    mu = X_train.mean(axis=0, keepdims=True)
    sd = X_train.std(axis=0, keepdims=True)
    sd[sd == 0] = 1.0
    return (X_train - mu) / sd, (X_test - mu) / sd


# --------------------------------------------------------------------------
# Tier 1 — pure NumPy
# --------------------------------------------------------------------------
def make_ridge(alpha: float = 1.0) -> Callable:
    """Ridge regression with a configurable penalty."""

    def fit_predict(X_train, y_train, X_test, seed: int = 0) -> np.ndarray:
        Xtr, Xte = standardise(np.asarray(X_train), np.asarray(X_test))
        Xtr = np.column_stack([Xtr, np.ones(len(Xtr))])
        Xte = np.column_stack([Xte, np.ones(len(Xte))])
        n_features = Xtr.shape[1]
        penalty = alpha * np.eye(n_features)
        penalty[-1, -1] = 0.0                      # do not penalise the intercept
        w = np.linalg.solve(Xtr.T @ Xtr + penalty, Xtr.T @ np.asarray(y_train))
        return Xte @ w

    return fit_predict


#: Ridge regression with alpha=1.0. Dependency-free; use this first.
ridge = make_ridge(1.0)


# --------------------------------------------------------------------------
# Tier 2 — scikit-learn
# --------------------------------------------------------------------------
def make_random_forest(n_estimators: int = 300, max_depth: int | None = None,
                       n_jobs: int = -1) -> Callable:
    """Random forest regressor. Requires scikit-learn."""

    def fit_predict(X_train, y_train, X_test, seed: int = 0) -> np.ndarray:
        from sklearn.ensemble import RandomForestRegressor
        model = RandomForestRegressor(
            n_estimators=n_estimators, max_depth=max_depth,
            random_state=seed, n_jobs=n_jobs,
        )
        model.fit(X_train, y_train)
        return model.predict(X_test)

    return fit_predict


#: Random forest with 300 trees. The baseline a deep model must beat.
random_forest = make_random_forest()


def gradient_boosting(X_train, y_train, X_test, seed: int = 0) -> np.ndarray:
    """Histogram gradient boosting. Requires scikit-learn."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    model = HistGradientBoostingRegressor(random_state=seed)
    model.fit(X_train, y_train)
    return model.predict(X_test)


# --------------------------------------------------------------------------
# Tier 3 — PyTorch, for when the HPC account arrives
# --------------------------------------------------------------------------
def make_torch_mlp(
    hidden: tuple[int, ...] = (128, 64),
    epochs: int = 200,
    lr: float = 1e-3,
    batch_size: int = 512,
    device: str | None = None,
    verbose: bool = False,
) -> Callable:
    """Small MLP in PyTorch, GPU if available.

    Deliberately modest. Swap in a temporal encoder or a pre-trained EO backbone
    once the pipeline is validated end to end; the signature does not change, so
    the ablation harness and every experiment script stay untouched.
    """

    def fit_predict(X_train, y_train, X_test, seed: int = 0) -> np.ndarray:
        import torch
        import torch.nn as nn

        torch.manual_seed(seed)
        np.random.seed(seed)
        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")

        Xtr, Xte = standardise(np.asarray(X_train), np.asarray(X_test))
        y_mu, y_sd = float(np.mean(y_train)), float(np.std(y_train)) or 1.0

        xt = torch.tensor(Xtr, dtype=torch.float32, device=dev)
        yt = torch.tensor((np.asarray(y_train) - y_mu) / y_sd,
                          dtype=torch.float32, device=dev).unsqueeze(1)
        xv = torch.tensor(Xte, dtype=torch.float32, device=dev)

        layers: list[nn.Module] = []
        prev = xt.shape[1]
        for h in hidden:
            layers += [nn.Linear(prev, h), nn.ReLU()]
            prev = h
        layers.append(nn.Linear(prev, 1))
        model = nn.Sequential(*layers).to(dev)

        opt = torch.optim.Adam(model.parameters(), lr=lr)
        loss_fn = nn.MSELoss()
        n = xt.shape[0]

        model.train()
        for epoch in range(epochs):
            perm = torch.randperm(n, device=dev)
            total = 0.0
            for i in range(0, n, batch_size):
                idx = perm[i:i + batch_size]
                opt.zero_grad()
                loss = loss_fn(model(xt[idx]), yt[idx])
                loss.backward()
                opt.step()
                total += float(loss) * len(idx)
            if verbose and (epoch + 1) % 50 == 0:
                print(f"    epoch {epoch+1:4d}  loss={total/n:.5f}")

        model.eval()
        with torch.no_grad():
            pred = model(xv).squeeze(1).cpu().numpy()
        return pred * y_sd + y_mu

    return fit_predict


#: Default MLP: two hidden layers, 200 epochs.
torch_mlp = make_torch_mlp()
