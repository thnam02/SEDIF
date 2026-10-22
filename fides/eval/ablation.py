"""Ablation cùng một tập test: single_bank, single_bank + T4c, chỉ T4c.

T4c là bốn đặc trưng mạng lưới. Thiếu thì NaN và cờ *_missing, không điền 0.
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
from features.service import session_families
from features.store import FEATURE_COLUMNS, LONG_TERM, NETWORK_ONLY
from train.baseline import recall_at, weighted_auc

T4C = list(NETWORK_ONLY)
T4C_MISSING = [f"{name}_missing" for name in T4C]
BANK = [name for name in FEATURE_COLUMNS if name not in NETWORK_ONLY and name not in LONG_TERM]
DRAWS = 200
SEED = 42
FRAUD = ["police", "tax_authority", "bank_staff", "investment", "relative", "deposit", "romance", "own_name_safe_account"]
HARD = ["legit_guided_by_relative", "legit_friend_loan"]


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, columns: list[str]) -> np.ndarray:
    import lightgbm as lgb

    categorical = [name for name in ("add_method",) if name in columns]
    matrix_train = train[columns].copy()
    matrix_test = test[columns].copy()
    for frame in (matrix_train, matrix_test):
        if "add_method" in frame.columns:
            frame["add_method"] = frame["add_method"].astype("category")
        for name in T4C:
            if name in frame.columns:
                frame[name] = frame[name].astype(float)
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
    return model.predict(matrix_test)


def with_flags(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for name, flag in zip(T4C, T4C_MISSING):
        values = pd.to_numeric(out[name], errors="coerce")
        out[name] = values
        out[flag] = values.isna().astype(np.int8)
    return out


def load_joined() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    out = ROOT / "out"
    bank_train = pd.read_parquet(out / "features_single_bank_train.parquet")
    bank_test = pd.read_parquet(out / "features_single_bank_test.parquet")
    net_train = pd.read_parquet(out / "features_network_train.parquet")
    net_test = pd.read_parquet(out / "features_network_test.parquet")
    parts = build_split()
    train = _align(bank_train, net_train, parts["train"])
    test = _align(bank_test, net_test, parts["test"])
    return train, test, parts["test"]


def _align(bank: pd.DataFrame, network: pd.DataFrame, split: pd.DataFrame) -> pd.DataFrame:
    bank = bank.copy()
    bank["session_id"] = bank["session_id"].astype(str)
    network = network.copy()
    network["session_id"] = network["session_id"].astype(str)
    network = with_flags(network)
    keep = ["session_id", *T4C, *T4C_MISSING]
    merged = bank.drop(columns=[name for name in T4C if name in bank.columns], errors="ignore")
    merged = merged.merge(network[keep], on="session_id", how="left")
    meta = split.copy()
    meta["session_id"] = meta["session_id"].astype(str)
    merged = meta[["session_id", "case_id"]].merge(merged, on="session_id", how="inner")
    if len(merged) != len(meta):
        raise RuntimeError("Tập đặc trưng và tập chia không cùng phiên")
    return merged


def missing_report(test: pd.DataFrame) -> None:
    print("Tỷ lệ thiếu feature mạng lưới trên cùng test (NaN, không điền 0):")
    for name, flag in zip(T4C, T4C_MISSING):
        values = test[name]
        absent = float(values.isna().mean())
        zero = float((values == 0).mean())
        flag_rate = float(test[flag].mean())
        print(f"  {name}: thiếu {absent:.1%}, cờ {flag} {flag_rate:.1%}, giá trị 0 đã quan sát {zero:.1%}")


def metric_bundle(label: np.ndarray, score: np.ndarray, weight: np.ndarray, groups: list[np.ndarray]) -> None:
    def auc_of(index: np.ndarray) -> float:
        return weighted_auc(label[index], score[index], weight[index])

    def recall_of(index: np.ndarray) -> float:
        return recall_at(label[index], score[index])

    auc = weighted_auc(label, score, weight)
    recall = recall_at(label, score)
    auc_ci = bootstrap_values(groups, auc_of, DRAWS, SEED)
    recall_ci = bootstrap_values(groups, recall_of, DRAWS, SEED + 1)
    print(f"  AUC {format_ci(auc, auc_ci)}  recall@1% {format_ci(recall, recall_ci, percent=True)}")


def scenario_table(test: pd.DataFrame, score: np.ndarray, families: pd.DataFrame) -> None:
    frame = test[["session_id", "case_id", "scam_type", "scenario_type", "label"]].copy()
    frame["score"] = score
    fam = families.copy()
    fam["session_id"] = fam["session_id"].astype(str)
    count = fam[["call", "entry", "payee", "funding"]].fillna(False).astype(int).sum(axis=1).to_numpy()
    call = fam["call"].fillna(False).to_numpy(dtype=bool)
    level = np.full(len(fam), "một họ", dtype=object)
    level[(count == 0) | (call & (count == 1))] = "none"
    level[count == 2] = "medium"
    level[count >= 3] = "high"
    fam["level"] = level
    frame = frame.merge(fam[["session_id", "level"]], on="session_id", how="left")
    frame["level"] = frame["level"].fillna("none")
    clusters = cluster_of(frame["case_id"], frame["session_id"])
    groups = cluster_index(clusters)
    levels = frame["level"].to_numpy()
    print("Theo scenario. Top 1% là recall với lừa đảo và tỷ lệ lọt top với mẫu hợp lệ. CI 95% theo vụ.")
    print(f"{'scenario':<28} {'n':>6} {'High':>22} {'Medium':>22} {'một họ':>22} {'không can thiệp':>22} {'top 1%':>22}")
    for name in FRAUD + HARD:
        if name in HARD:
            mask = frame["scenario_type"].astype(str).eq(name).to_numpy()
        else:
            mask = frame["scam_type"].astype(str).eq(name).to_numpy()
        if not mask.any():
            continue
        part_level = levels[mask]
        point = {
            "high": float((part_level == "high").mean()),
            "medium": float((part_level == "medium").mean()),
            "low": float((part_level == "một họ").mean()),
            "none": float((part_level == "none").mean()),
            "top": float("nan"),
        }
        base_k = max(1, int(round(0.01 * len(frame))))
        base_top = np.zeros(len(frame), dtype=bool)
        base_top[np.argpartition(-score, base_k - 1)[:base_k]] = True
        point["top"] = float(base_top[mask].mean())

        def statistic(index: np.ndarray, key: str) -> float:
            chosen = mask[index]
            if not np.any(chosen):
                return np.nan
            if key == "top":
                local = score[index]
                local_k = max(1, int(round(0.01 * len(index))))
                picked = np.zeros(len(index), dtype=bool)
                picked[np.argpartition(-local, local_k - 1)[:local_k]] = True
                return float(picked[chosen].mean())
            got = levels[index][chosen]
            label = {"low": "một họ"}.get(key, key)
            return float((got == label).mean())

        samples = {
            key: bootstrap_values(groups, lambda index, key=key: statistic(index, key), DRAWS, SEED + 3)
            for key in ("high", "medium", "low", "none", "top")
        }
        print(
            f"{name:<28} {int(mask.sum()):6d} "
            f"{format_ci(point['high'], samples['high'], percent=True):>22} "
            f"{format_ci(point['medium'], samples['medium'], percent=True):>22} "
            f"{format_ci(point['low'], samples['low'], percent=True):>22} "
            f"{format_ci(point['none'], samples['none'], percent=True):>22} "
            f"{format_ci(point['top'], samples['top'], percent=True):>22}"
        )


def crosstab() -> None:
    observed = SIM / "out" / "observed"
    truth = SIM / "out" / "truth"
    signals = pd.read_parquet(observed / "device_signals.parquet", columns=["session_id", "in_call_at_open"])
    events = pd.read_parquet(observed / "session_events.parquet", columns=["session_id", "event_type"])
    scenarios = pd.read_parquet(truth / "truth_scenarios.parquet", columns=["session_id", "scenario_type"])
    for frame in (signals, events, scenarios):
        frame["session_id"] = frame["session_id"].astype(str)
    kind = events["event_type"].astype(str)
    mode = (
        events.loc[kind.str.startswith("acct_input_")]
        .groupby("session_id", sort=False)["event_type"]
        .first()
        .astype(str)
        .str.replace("acct_input_", "", regex=False)
    )
    table = scenarios.merge(signals, on="session_id", how="left")
    table["in_call"] = table["in_call_at_open"].fillna(False).astype(bool)
    table["acct_input"] = table["session_id"].map(mode).fillna("(thiếu)")
    names = FRAUD[:-1] + ["legit_guided_by_relative", "legit_friend_loan", "legit_on_call_known", "legit_on_call_ott", "legit_normal"]
    print("Crosstab in_call × acct_input (số phiên).")
    hard = False
    for name in names:
        part = table.loc[table["scenario_type"].astype(str).eq(name)]
        if part.empty:
            continue
        counts = pd.crosstab(part["in_call"], part["acct_input"])
        print(f"\n{name}")
        print(counts.to_string())
        called = part.loc[part["in_call"]]
        idle = part.loc[~part["in_call"]]
        # Ô vài phiên bằng 0 là nhiễu mẫu, không phải luật sinh pasted theo in_call.
        if len(called) >= 30 and len(idle) >= 30 and "pasted" in part["acct_input"].unique():
            rates = [
                float(called["acct_input"].eq("pasted").mean()),
                float(idle["acct_input"].eq("pasted").mean()),
            ]
            locked = min(rates) == 0.0 and max(rates) >= 0.8
            locked = locked or (max(rates) == 1.0 and min(rates) <= 0.2)
            hard = hard or locked
    if hard:
        print("\npasted phụ thuộc cứng vào in_call ở ít nhất một scenario.")
    else:
        print("\npasted không phụ thuộc cứng vào in_call. Giữ cách nhập đã sinh, không rút lại.")


def main() -> None:
    train, test, _split = load_joined()
    missing_report(test)
    specs = {
        "single_bank": BANK,
        "single_bank+T4c": BANK + T4C + T4C_MISSING,
        "chỉ T4c": T4C + T4C_MISSING,
    }
    label = test["label"].to_numpy(dtype=float)
    weight = test["sample_weight"].to_numpy(dtype=float)
    groups = cluster_index(cluster_of(test["case_id"], test["session_id"]))
    scores = {}
    print("\nAblation cùng test set. CI 95% bootstrap theo vụ.")
    for name, columns in specs.items():
        print(name)
        scores[name] = fit_predict(train, test, columns)
        metric_bundle(label, scores[name], weight, groups)
    families = session_families(SIM / "out" / "observed")
    scenarios = pd.read_parquet(SIM / "out" / "truth" / "truth_scenarios.parquet", columns=["session_id", "scenario_type"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    families = families.merge(scenarios, on="session_id", how="left")
    # Mẫu khó lấy theo scenario_type, không theo scam_type.
    test = test.copy()
    test["scenario_type"] = test["session_id"].map(dict(zip(scenarios["session_id"], scenarios["scenario_type"].astype(str))))
    print()
    scenario_table(test, scores["single_bank+T4c"], families)
    print()
    crosstab()


if __name__ == "__main__":
    main()
