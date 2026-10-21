"""Xuất đặc trưng train/test và baseline LightGBM cho cả hai view."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.split import build_split, describe
from features import FEATURE_COLUMNS, NETWORK_ONLY, compute_features
from features.store import FeatureStore, using_store

OUT = ROOT / "out"
FRAUD_GROUPS = {"police", "bank_staff", "relative", "deposit", "investment", "romance", "own_name_safe_account"}


def featurize(store: FeatureStore, part: pd.DataFrame, view: str) -> pd.DataFrame:
    rows = []
    total = len(part)
    with using_store(store):
        for index, record in enumerate(part.itertuples(index=False), start=1):
            as_of = pd.Timestamp(record.end_ts) + pd.Timedelta(microseconds=1)
            features = compute_features(record.session_id, as_of, view)
            features["session_id"] = record.session_id
            features["label"] = int(record.label)
            features["sample_weight"] = float(record.sample_weight)
            features["scam_type"] = str(record.scam_type)
            rows.append(features)
            if index % 20000 == 0:
                print(f"    {view}: {index}/{total}")
    frame = pd.DataFrame(rows)
    ordered = ["session_id", "label", "sample_weight", "scam_type", *FEATURE_COLUMNS]
    return frame[ordered]


def null_report(frame: pd.DataFrame) -> None:
    print(f"  {len(frame)} dòng, {len(FEATURE_COLUMNS)} cột đặc trưng")
    missing = frame[FEATURE_COLUMNS].isna().mean()
    for name, rate in missing.items():
        print(f"    {name}: thiếu {rate:.1%}")


def weighted_auc(label: np.ndarray, score: np.ndarray, weight: np.ndarray) -> float:
    order = np.argsort(score, kind="mergesort")
    label = label[order].astype(bool)
    score = score[order]
    weight = weight[order]
    pos = float(weight[label].sum())
    neg = float(weight[~label].sum())
    if pos == 0 or neg == 0:
        return float("nan")
    change = np.r_[0, np.flatnonzero(score[1:] != score[:-1]) + 1, len(score)]
    cum_neg = np.cumsum(np.where(label, 0.0, weight))
    caught = 0.0
    for start, end in zip(change[:-1], change[1:]):
        block = label[start:end]
        neg_in = float(weight[start:end][~block].sum())
        neg_before = float(cum_neg[start - 1]) if start else 0.0
        caught += float(weight[start:end][block].sum()) * (neg_before + 0.5 * neg_in)
    return caught / (pos * neg)


def recall_at(label: np.ndarray, score: np.ndarray, rate: float = 0.01) -> float:
    k = max(1, int(round(rate * len(label))))
    chosen = np.argpartition(-score, k - 1)[:k]
    total = float(label.sum())
    return float(label[chosen].sum() / total) if total else float("nan")


def rank_money(score: np.ndarray, amount: np.ndarray, kind: np.ndarray, fraud: np.ndarray, rate: float = 0.01) -> dict[str, float]:
    k = max(1, int(round(rate * len(score))))
    chosen = np.zeros(len(score), dtype=bool)
    chosen[np.argpartition(-score, k - 1)[:k]] = True
    caught = fraud & chosen
    out = {"_all": float(amount[caught].sum())}
    for name in sorted(set(kind[fraud])):
        mask = fraud & (kind == name)
        base = float(amount[mask].sum())
        saved = float(amount[mask & chosen].sum())
        out[str(name)] = saved / base if base else 0.0
    out["_base"] = float(amount[fraud].sum())
    return out


def main() -> None:
    import lightgbm as lgb

    print(describe(build_split()))
    parts = build_split()
    observed = SIM / "out" / "observed"
    truth = SIM / "out" / "truth"
    store = FeatureStore(observed)
    contacts = pd.read_parquet(truth / "contacts.parquet", columns=["customer_id", "number_id", "first_contact"])
    store.attach_contacts(contacts)
    OUT.mkdir(parents=True, exist_ok=True)
    tables = {}
    for view in ("single_bank", "network"):
        for name in ("train", "test"):
            print(f"Tính {view} / {name}")
            frame = featurize(store, parts[name], view)
            path = OUT / f"features_{view}_{name}.parquet"
            frame.to_parquet(path, index=False)
            print(f"  ghi {path}")
            null_report(frame)
            tables[(view, name)] = frame
        train = tables[(view, "train")]
        test = tables[(view, "test")]
        columns = [col for col in FEATURE_COLUMNS if view == "network" or col not in NETWORK_ONLY]
        categorical = ["add_method"]
        matrix_train = train[columns].copy()
        matrix_test = test[columns].copy()
        for frame in (matrix_train, matrix_test):
            frame["add_method"] = frame["add_method"].astype("category")
        train_set = lgb.Dataset(
            matrix_train,
            label=train["label"].to_numpy(),
            weight=train["sample_weight"].to_numpy(),
            categorical_feature=categorical,
            free_raw_data=False,
        )
        model = lgb.train(
            {
                "objective": "binary",
                "learning_rate": 0.08,
                "num_leaves": 31,
                "min_child_samples": 40,
                "bagging_fraction": 0.8,
                "bagging_freq": 1,
                "feature_fraction": 0.8,
                "verbosity": -1,
                "seed": 42,
            },
            train_set,
            num_boost_round=200,
        )
        score = model.predict(matrix_test)
        label = test["label"].to_numpy(dtype=float)
        weight = test["sample_weight"].to_numpy(dtype=float)
        auc = weighted_auc(label, score, weight)
        recall = recall_at(label, score)
        print(f"{view}: AUC test {auc:.3f}, recall tại 1% can thiệp {recall:.2%}")
        exposure = test["a_w"].fillna(0).to_numpy(dtype=float)
        money = parts["test"]["fraud_vnd"].to_numpy(dtype=float)
        kind = test["scam_type"].astype(str).to_numpy()
        fraud = label.astype(bool)
        by_p = rank_money(score, money, kind, fraud)
        by_value = rank_money(score * np.maximum(exposure, 0), money, kind, fraud)
        base = by_p["_base"]
        print(f"  Tiền lừa từ B01 trong top 1% theo p: {by_p['_all']:,.0f} / {base:,.0f} VND")
        print(f"  Theo p × A_w: {by_value['_all']:,.0f} / {base:,.0f} VND")
        for name in sorted(set(kind[fraud])):
            print(f"    {name}: p {by_p.get(name, 0):.1%}, p×A_w {by_value.get(name, 0):.1%}")


if __name__ == "__main__":
    main()
