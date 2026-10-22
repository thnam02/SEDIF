"""Quyết định theo τ(A) và recall trước/sau feature dài hạn.

τ là ngưỡng kỳ vọng tổn thất p×A, khớp trên train. Họ tín hiệu chỉ chặn High.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.bootstrap import bootstrap_values, cluster_index, cluster_of, format_ci
from eval.split import build_split
from features.service import decide_levels, fit_tau, session_families
from features.store import FEATURE_COLUMNS, LONG_TERM, NETWORK_ONLY, FeatureStore
from train.baseline import recall_at, weighted_auc

BANK = [name for name in FEATURE_COLUMNS if name not in NETWORK_ONLY and name not in LONG_TERM]
T4C = list(NETWORK_ONLY)
SEED = 42
DRAWS = 200
ORDER = [
    "police",
    "tax_authority",
    "bank_staff",
    "investment",
    "relative",
    "deposit",
    "romance",
    "own_name_safe_account",
    "legit_guided_by_relative",
    "legit_friend_loan",
]
SLOW = ["investment", "deposit", "romance"]


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
    import lightgbm as lgb

    categorical = [name for name in ("add_method",) if name in columns]
    matrix_train = train[columns].copy()
    matrix_test = test[columns].copy()
    for frame in (matrix_train, matrix_test):
        if "add_method" in frame.columns:
            frame["add_method"] = frame["add_method"].astype("category")
        for name in columns:
            if name != "add_method":
                frame[name] = pd.to_numeric(frame[name], errors="coerce")
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
            "seed": SEED,
        },
        train_set,
        num_boost_round=200,
    )
    return model.predict(matrix_train), model.predict(matrix_test)


def with_flags(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for name in T4C:
        values = pd.to_numeric(out[name], errors="coerce")
        out[name] = values
        out[f"{name}_missing"] = values.isna().astype(np.int8)
    return out


def load_model_frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    out = ROOT / "out"
    parts = build_split()
    frames = {}
    for name in ("train", "test"):
        bank = pd.read_parquet(out / f"features_single_bank_{name}.parquet")
        network = with_flags(pd.read_parquet(out / f"features_network_{name}.parquet"))
        bank["session_id"] = bank["session_id"].astype(str)
        network["session_id"] = network["session_id"].astype(str)
        keep = ["session_id", *T4C, *[f"{col}_missing" for col in T4C]]
        merged = bank.drop(columns=[col for col in T4C if col in bank.columns], errors="ignore")
        merged = merged.merge(network[keep], on="session_id", how="left")
        meta = parts[name][["session_id", "case_id", "scam_type"]].copy()
        meta["session_id"] = meta["session_id"].astype(str)
        merged = meta.merge(merged.drop(columns=["scam_type"], errors="ignore"), on="session_id", how="inner")
        frames[name] = merged
    return frames["train"], frames["test"]


def long_term(part: pd.DataFrame, store: FeatureStore) -> pd.DataFrame:
    rows = []
    ids = []
    total = len(part)
    for index, record in enumerate(part.itertuples(index=False), start=1):
        as_of = pd.Timestamp(record.end_ts) + pd.Timedelta(microseconds=1)
        rows.append(store.behavior_at(str(record.session_id), as_of, "single_bank"))
        ids.append(str(record.session_id))
        if index % 20000 == 0:
            print(f"    dài hạn {index}/{total}", flush=True)
    frame = pd.DataFrame(rows)
    frame.insert(0, "session_id", ids)
    return frame


def top_mask(score: np.ndarray, rate: float = 0.01) -> np.ndarray:
    chosen = np.zeros(len(score), dtype=bool)
    if len(score) == 0:
        return chosen
    k = max(1, int(round(rate * len(score))))
    chosen[np.argpartition(-score, k - 1)[:k]] = True
    return chosen


def type_recall(score: np.ndarray, label: np.ndarray, kind: np.ndarray, name: str) -> float:
    mask = (kind == name) & (label == 1)
    if not np.any(mask):
        return float("nan")
    return float(top_mask(score)[mask].mean())


def scenario_table(test: pd.DataFrame, level: np.ndarray, score: np.ndarray, amount: np.ndarray, families: pd.DataFrame, tau: tuple[float, float, float]) -> None:
    frame = test[["session_id", "case_id", "scam_type", "scenario_type"]].copy()
    frame["level"] = level
    clusters = cluster_index(cluster_of(frame["case_id"], frame["session_id"]))
    fam = families.set_index("session_id")
    call = frame["session_id"].map(fam["call"]).fillna(False).to_numpy(dtype=bool)
    entry = frame["session_id"].map(fam["entry"]).fillna(False).to_numpy(dtype=bool)
    payee = frame["session_id"].map(fam["payee"]).fillna(False).to_numpy(dtype=bool)
    funding = frame["session_id"].map(fam["funding"]).fillna(False).to_numpy(dtype=bool)
    print(
        f"τ(A) là ngưỡng p×A trên train: banner {tau[0]:,.0f}, Medium {tau[1]:,.0f}, High {tau[2]:,.0f} VND. "
        "High cần ≥3 họ. Top 1% với 0–1 họ được nâng Medium. Chỉ cuộc gọi và dưới ngưỡng thì không can thiệp."
    )
    print(f"{'scenario':<28} {'n':>6} {'High':>22} {'Medium':>22} {'banner':>22} {'none':>22}")
    levels = frame["level"].to_numpy()
    for name in ORDER:
        if name in {"legit_guided_by_relative", "legit_friend_loan"}:
            mask = frame["scenario_type"].astype(str).eq(name).to_numpy()
        else:
            mask = frame["scam_type"].astype(str).eq(name).to_numpy()
        if not mask.any():
            continue
        point = {key: float((levels[mask] == key).mean()) for key in ("high", "medium", "banner", "none")}

        def statistic(index: np.ndarray, key: str) -> float:
            chosen = mask[index]
            if not np.any(chosen):
                return np.nan
            local_score = score[index]
            local_amount = amount[index]
            local_level = decide_levels(
                local_score,
                local_amount,
                call[index],
                entry[index],
                payee[index],
                funding[index],
                tau[0],
                tau[1],
                tau[2],
                top_mask(local_score),
            )
            return float((local_level[chosen] == key).mean())

        samples = {
            key: bootstrap_values(clusters, lambda index, key=key: statistic(index, key), DRAWS, SEED)
            for key in ("high", "medium", "banner", "none")
        }
        print(
            f"{name:<28} {int(mask.sum()):6d} "
            f"{format_ci(point['high'], samples['high'], percent=True):>22} "
            f"{format_ci(point['medium'], samples['medium'], percent=True):>22} "
            f"{format_ci(point['banner'], samples['banner'], percent=True):>22} "
            f"{format_ci(point['none'], samples['none'], percent=True):>22}"
        )


def friend_loan(test: pd.DataFrame, level: np.ndarray, families: pd.DataFrame) -> None:
    fam = families.copy()
    fam["session_id"] = fam["session_id"].astype(str)
    frame = test.loc[test["scenario_type"].astype(str).eq("legit_friend_loan"), ["session_id"]].copy()
    frame["level"] = level[test["scenario_type"].astype(str).eq("legit_friend_loan").to_numpy()]
    frame = frame.merge(fam, on="session_id", how="left")
    count = frame[["call", "entry", "payee", "funding"]].fillna(False).astype(int).sum(axis=1)
    high = frame.loc[count >= 3].copy()
    print(f"\nlegit_friend_loan trên test có ≥3 họ: {len(high)} / {len(frame)}. High của luật cũ cần đủ 3 họ.")
    if high.empty:
        return
    for row in high.itertuples(index=False):
        active = [name for name in ("call", "entry", "payee", "funding") if bool(getattr(row, name))]
        print(f"  {row.session_id}  mức mới {row.level}  họ: {', '.join(active)}")


def recall_block(title: str, score: np.ndarray, test: pd.DataFrame) -> None:
    label = test["label"].to_numpy(dtype=float)
    kind = test["scam_type"].astype(str).to_numpy()
    groups = cluster_index(cluster_of(test["case_id"], test["session_id"]))
    print(title)
    for name in SLOW:
        point = type_recall(score, label, kind, name)

        def statistic(index: np.ndarray, name: str = name) -> float:
            return type_recall(score[index], label[index], kind[index], name)

        samples = bootstrap_values(groups, statistic, DRAWS, SEED)
        count = int(((kind == name) & (label == 1)).sum())
        print(f"  {name}: n {count}, recall@1% {format_ci(point, samples, percent=True)}")


def main() -> None:
    train, test = load_model_frame()
    columns = BANK + T4C + [f"{name}_missing" for name in T4C]
    train_score, test_score = fit_predict(train, test, columns)
    amount_train = pd.to_numeric(train["amount"], errors="coerce").to_numpy(dtype=float)
    amount_test = pd.to_numeric(test["amount"], errors="coerce").to_numpy(dtype=float)
    tau = fit_tau(train_score, amount_train)
    families = session_families(SIM / "out" / "observed")
    families["session_id"] = families["session_id"].astype(str)
    scenarios = pd.read_parquet(SIM / "out" / "truth" / "truth_scenarios.parquet", columns=["session_id", "scenario_type"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    test = test.copy()
    test["scenario_type"] = test["session_id"].map(dict(zip(scenarios["session_id"], scenarios["scenario_type"].astype(str))))
    fam = families.set_index("session_id")
    level = decide_levels(
        test_score,
        amount_test,
        test["session_id"].map(fam["call"]).fillna(False).to_numpy(dtype=bool),
        test["session_id"].map(fam["entry"]).fillna(False).to_numpy(dtype=bool),
        test["session_id"].map(fam["payee"]).fillna(False).to_numpy(dtype=bool),
        test["session_id"].map(fam["funding"]).fillna(False).to_numpy(dtype=bool),
        *tau,
        top_mask(test_score),
    )
    scenario_table(test, level, test_score, amount_test, families, tau)
    friend_loan(test, level, families)

    print("\nFeature dài hạn, view single_bank, cùng tập test.")
    parts = build_split()
    store = FeatureStore(SIM / "out" / "observed")
    before_cols = [name for name in BANK if name in train.columns]
    base_train = train[before_cols + ["session_id", "label", "sample_weight"]].copy()
    base_test = test[before_cols + ["label", "sample_weight", "scam_type", "case_id", "session_id"]].copy()
    _, before_score = fit_predict(base_train, base_test, before_cols)
    recall_block("Trước khi thêm feature dài hạn:", before_score, base_test)
    extra_train = long_term(parts["train"], store)
    extra_test = long_term(parts["test"], store)
    after_train = base_train.merge(extra_train, on="session_id", how="inner")
    after_test = base_test.merge(extra_test, on="session_id", how="inner")
    _, after_score = fit_predict(after_train, after_test, before_cols + LONG_TERM)
    recall_block("Sau khi thêm CUSUM 30 ngày, người nhận mới 7/30 ngày, chuỗi tăng dần, tỷ lệ số dư 30 ngày:", after_score, after_test)
    print("AUC test trước/sau:", end=" ")
    y = after_test["label"].to_numpy(dtype=float)
    w = after_test["sample_weight"].to_numpy(dtype=float)
    print(f"{weighted_auc(y, before_score, w):.3f} → {weighted_auc(y, after_score, w):.3f}")


if __name__ == "__main__":
    main()
