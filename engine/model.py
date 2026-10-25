"""Booster và isotonic của model-v2.1. SHA256 kiểm khi nạp, trước khi phục vụ."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ARTIFACT = Path(__file__).resolve().parent / "artifacts" / "model-v2.1" / "advanced_ctx_payee"


def _check_hashes(directory: Path) -> None:
    lines = (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if actual != digest:
            raise RuntimeError(f"sha256 lệch {name}")


def _isotonic(score: float, edges: np.ndarray, values: np.ndarray) -> float:
    index = int(np.searchsorted(edges, score, side="left"))
    return float(values[min(max(index, 0), len(values) - 1)])


def _shift(probability: float, source: float, target: float) -> float:
    clipped = min(max(probability, 1e-6), 1 - 1e-6)
    source = min(max(source, 1e-6), 1 - 1e-6)
    target = min(max(target, 1e-6), 1 - 1e-6)
    factor = (target / (1 - target)) / (source / (1 - source))
    odds = clipped / (1 - clipped) * factor
    return float(odds / (1 + odds))


class Scorer:
    def __init__(self, model, policy: dict, categories: dict):
        self.model = model
        self.policy = policy
        self.columns = list(policy["columns"])
        self.categories = categories
        calibrator = policy["calibrator"]
        self.edges = np.asarray(calibrator["edges"], dtype=float)
        self.values = np.asarray(calibrator["values"], dtype=float)
        self.source = float(calibrator["source"])
        self.target = float(calibrator["target"])
        self.version = str(policy.get("model_version", "model-v2.1"))
        self.tau_hi = float(policy["tau_hi"])

    @classmethod
    def load(cls, directory: Path = ARTIFACT) -> "Scorer":
        _check_hashes(directory)
        policy = json.loads((directory / "policy.json").read_text(encoding="utf-8"))
        features = json.loads((directory / "features.json").read_text(encoding="utf-8"))
        if features["columns"] != policy["columns"]:
            raise RuntimeError("features.json lệch policy.json")
        model = lgb.Booster(model_file=str(directory / "model.txt"))
        if model.feature_name() != policy["columns"]:
            raise RuntimeError("thứ tự cột của booster lệch features.json")
        if float(policy["tau_hi"]) != 0.8125:
            raise RuntimeError("τ_hi đóng gói không phải 0.8125")
        categories = {key: pd.api.types.CategoricalDtype(value) for key, value in policy["categorical"].items()}
        return cls(model, policy, categories)

    def score(self, row: dict) -> float:
        data = {}
        for name in self.columns:
            if name in self.categories:
                data[name] = pd.Series([row.get(name)], dtype=self.categories[name])
            else:
                value = row.get(name)
                data[name] = [np.nan if value is None else float(value)]
        raw = float(self.model.predict(pd.DataFrame(data))[0])
        return _shift(_isotonic(raw, self.edges, self.values), self.source, self.target)
