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
from eval.split import _label_groups, build_split, group_folds
from features.service import COST_ETA, cost_tau, decide_cost, levels_from_counts, session_families
from features.store import FEATURE_COLUMNS, LONG_TERM, NETWORK_ONLY, FeatureStore
from train.baseline import featurize, recall_at, weighted_auc

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
P95 = "amount_to_p95_90d"
MH_CAP = 0.0045
STRONG_ACTIONS = {"pause_and_verify", "guardian_confirm", "friction", "show_chain", "recovery_window", "hangup_required", "hold"}
COACHED = {"police", "bank_staff", "tax_authority", "own_name_safe_account"}


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


def isotonic_fit(score: np.ndarray, label: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """PAVA. Trả về mốc điểm thô tăng dần và p hiệu chỉnh không giảm."""
    order = np.argsort(np.asarray(score, dtype=float), kind="mergesort")
    target = np.asarray(label, dtype=float)[order]
    raw = np.asarray(score, dtype=float)[order]
    blocks: list[list[float]] = []
    for value in target:
        blocks.append([float(value), 1.0])
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            later = blocks.pop()
            blocks[-1][0] += later[0]
            blocks[-1][1] += later[1]
    fitted = np.empty(len(target), dtype=float)
    cursor = 0
    edges = []
    values = []
    for total, count in blocks:
        width = int(count)
        level = total / count
        fitted[cursor : cursor + width] = level
        cursor += width
        edges.append(raw[cursor - 1])
        values.append(level)
    return np.array(edges, dtype=float), np.array(values, dtype=float)


def isotonic_apply(score: np.ndarray, edges: np.ndarray, values: np.ndarray) -> np.ndarray:
    index = np.searchsorted(edges, np.asarray(score, dtype=float), side="left")
    return values[np.clip(index, 0, len(values) - 1)]


def shift_rate(probability: np.ndarray, source: float, target: float) -> np.ndarray:
    """Dịch odds để trung bình khớp tỷ lệ lừa đảo thật."""
    clipped = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    source = float(np.clip(source, 1e-6, 1 - 1e-6))
    target = float(np.clip(target, 1e-6, 1 - 1e-6))
    factor = (target / (1 - target)) / (source / (1 - source))
    odds = clipped / (1 - clipped) * factor
    return odds / (1 + odds)


def _c_factor(size: int) -> float:
    if size <= 1:
        return 0.0
    return 2.0 * (np.log(size - 1) + 0.5772156649) - 2.0 * (size - 1) / size


def _isolation_tree(matrix: np.ndarray, depth: int, limit: int, rng: np.random.Generator):
    rows = len(matrix)
    if rows <= 1 or depth >= limit:
        return ("leaf", rows, depth)
    feature = int(rng.integers(0, matrix.shape[1]))
    column = matrix[:, feature]
    low = float(np.min(column))
    high = float(np.max(column))
    if not np.isfinite(low) or high <= low:
        return ("leaf", rows, depth)
    cut = float(rng.uniform(low, high))
    left = column < cut
    if left.all() or not left.any():
        return ("leaf", rows, depth)
    return (
        "split",
        feature,
        cut,
        _isolation_tree(matrix[left], depth + 1, limit, rng),
        _isolation_tree(matrix[~left], depth + 1, limit, rng),
    )


def _tree_depth(tree, matrix: np.ndarray) -> np.ndarray:
    path = np.zeros(len(matrix), dtype=float)
    stack = [(tree, np.arange(len(matrix)))]
    while stack:
        node, index = stack.pop()
        if node[0] == "leaf":
            path[index] = node[2] + _c_factor(int(node[1]))
            continue
        _, feature, cut, left, right = node
        go_left = matrix[index, feature] < cut
        if go_left.any():
            stack.append((left, index[go_left]))
        if (~go_left).any():
            stack.append((right, index[~go_left]))
    return path


def isolation_score(train: np.ndarray, score_on: np.ndarray, trees: int = 100, sample: int = 256, seed: int = SEED) -> np.ndarray:
    """Điểm càng cao càng lệch khỏi phiên hợp lệ. Forest chỉ học trên các dòng train."""
    rng = np.random.default_rng(seed)
    width = min(sample, len(train))
    limit = int(np.ceil(np.log2(max(width, 2))))
    forest = []
    for _ in range(trees):
        chosen = rng.choice(len(train), size=width, replace=False)
        forest.append(_isolation_tree(train[chosen], 0, limit, rng))
    normaliser = _c_factor(width)
    depth = np.mean([_tree_depth(tree, score_on) for tree in forest], axis=0)
    return np.power(2.0, -depth / normaliser)


def train_model(train: pd.DataFrame, columns: list[str]):
    import lightgbm as lgb

    categories = pd.api.types.CategoricalDtype(train["add_method"].astype("string").dropna().unique())
    matrix = train[columns].copy()
    matrix["add_method"] = matrix["add_method"].astype("string").astype(categories)
    for name in columns:
        if name != "add_method":
            matrix[name] = pd.to_numeric(matrix[name], errors="coerce")
    dataset = lgb.Dataset(
        matrix,
        label=train["label"].to_numpy(),
        weight=train["sample_weight"].to_numpy(),
        categorical_feature=["add_method"],
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
        dataset,
        num_boost_round=200,
    )
    return model, categories


def predict_model(model, categories, frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    matrix = frame[columns].copy()
    matrix["add_method"] = matrix["add_method"].astype("string").astype(categories)
    for name in columns:
        if name != "add_method":
            matrix[name] = pd.to_numeric(matrix[name], errors="coerce")
    return model.predict(matrix)


def out_of_fold(train: pd.DataFrame, columns: list[str]) -> np.ndarray:
    raw = np.zeros(len(train), dtype=float)
    groups = _label_groups(train["case_id"], train["session_id"], "c")
    for train_mask, test_mask in group_folds(groups, n_splits=5, seed=SEED):
        model, categories = train_model(train.iloc[np.flatnonzero(train_mask)], columns)
        raw[test_mask] = predict_model(model, categories, train.iloc[np.flatnonzero(test_mask)], columns)
    return raw


def calibrate(raw_train: np.ndarray, fraud: np.ndarray) -> tuple[np.ndarray, callable]:
    edges, values = isotonic_fit(raw_train, fraud)
    fitted = isotonic_apply(raw_train, edges, values)
    source = float(np.mean(fitted))
    target = float(np.mean(fraud))
    adjusted = shift_rate(fitted, source, target)

    def apply(raw: np.ndarray) -> np.ndarray:
        return shift_rate(isotonic_apply(raw, edges, values), source, target)

    return adjusted, apply


def g3_score(train_extra: pd.DataFrame, test_extra: pd.DataFrame, legit: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    learned = train_extra.iloc[np.flatnonzero(np.asarray(legit, dtype=bool))][LONG_TERM].apply(pd.to_numeric, errors="coerce")
    fill = learned.median(numeric_only=True)
    train_x = train_extra[LONG_TERM].apply(pd.to_numeric, errors="coerce").fillna(fill).to_numpy(dtype=float)
    test_x = test_extra[LONG_TERM].apply(pd.to_numeric, errors="coerce").fillna(fill).to_numpy(dtype=float)
    normal = np.nan_to_num(learned.fillna(fill).to_numpy(dtype=float), nan=0.0)
    forest_train = isolation_score(normal, np.nan_to_num(train_x, nan=0.0))
    forest_test = isolation_score(normal, np.nan_to_num(test_x, nan=0.0))
    cusum = pd.to_numeric(train_extra.loc[legit, "cusum_out_30d"], errors="coerce").fillna(0).to_numpy(dtype=float)
    order = np.sort(cusum)

    def cusum_cdf(values: pd.Series) -> np.ndarray:
        raw = pd.to_numeric(values, errors="coerce").fillna(0).to_numpy(dtype=float)
        return np.searchsorted(order, raw, side="right") / max(len(order), 1)

    train_g3 = 0.5 * (forest_train + cusum_cdf(train_extra["cusum_out_30d"]))
    test_g3 = 0.5 * (forest_test + cusum_cdf(test_extra["cusum_out_30d"]))
    return train_g3, test_g3


def rate_table(frame: pd.DataFrame, column: str, order: list[str]) -> None:
    groups = cluster_index(_label_groups(frame["case_id"], frame["customer_id"], "c"))
    level = frame["level"].to_numpy()
    print(f"{'nhóm':<22} {'n':>8} {'banner':>22} {'Medium':>22} {'High':>22} {'Medium+High':>22}")
    masks = [(name, frame[column].astype(str).eq(name).to_numpy()) for name in order]
    masks.insert(0, ("tất cả", np.ones(len(frame), dtype=bool)))
    for name, mask in masks:
        if not mask.any():
            continue
        point = {
            "banner": float((level[mask] == "banner").mean()),
            "medium": float((level[mask] == "medium").mean()),
            "high": float((level[mask] == "high").mean()),
            "both": float(np.isin(level[mask], ["medium", "high"]).mean()),
        }

        def statistic(index: np.ndarray, key: str, mask: np.ndarray = mask) -> float:
            chosen = mask[index]
            if not np.any(chosen):
                return np.nan
            got = level[index][chosen]
            if key == "both":
                return float(np.isin(got, ["medium", "high"]).mean())
            return float((got == key).mean())

        samples = {key: bootstrap_values(groups, lambda index, key=key: statistic(index, key), DRAWS, SEED) for key in point}
        print(
            f"{name:<22} {int(mask.sum()):8d} "
            f"{format_ci(point['banner'], samples['banner'], percent=True):>22} "
            f"{format_ci(point['medium'], samples['medium'], percent=True):>22} "
            f"{format_ci(point['high'], samples['high'], percent=True):>22} "
            f"{format_ci(point['both'], samples['both'], percent=True):>22}"
        )


def levels_of(frame: pd.DataFrame, probability: np.ndarray, families: pd.DataFrame, anomaly: np.ndarray | None = None) -> np.ndarray:
    indexed = families.set_index("session_id")
    return decide_cost(
        probability,
        pd.to_numeric(frame["amount"], errors="coerce").to_numpy(dtype=float),
        frame["session_id"].map(indexed["call"]).fillna(False).to_numpy(dtype=bool),
        frame["session_id"].map(indexed["entry"]).fillna(False).to_numpy(dtype=bool),
        frame["session_id"].map(indexed["payee"]).fillna(False).to_numpy(dtype=bool),
        frame["session_id"].map(indexed["funding"]).fillna(False).to_numpy(dtype=bool),
        COST_ETA,
        anomaly,
    )


def _long_matrix(frame: pd.DataFrame, fill: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    values = frame[LONG_TERM].apply(pd.to_numeric, errors="coerce").fillna(fill)
    matrix = np.nan_to_num(values.to_numpy(dtype=float), nan=0.0)
    cusum = pd.to_numeric(frame["cusum_out_30d"], errors="coerce").fillna(0).to_numpy(dtype=float)
    return matrix, cusum


def _g3_query(reference: np.ndarray, reference_cusum: np.ndarray, query: np.ndarray, query_cusum: np.ndarray, seed: int) -> np.ndarray:
    if len(reference) < 2:
        return np.zeros(len(query), dtype=float)
    forest = isolation_score(reference, query, seed=seed)
    order = np.sort(reference_cusum)
    cdf = np.searchsorted(order, query_cusum, side="right") / len(order)
    return 0.5 * (forest + cdf)


def g3_out_of_fold(train: pd.DataFrame) -> tuple[np.ndarray, float, tuple]:
    """Forest và mốc CUSUM của mỗi fold chỉ học phần train. Ngưỡng là p99,5 của phiên hợp lệ."""
    fraud = train["is_fraud"].to_numpy(dtype=bool)
    groups = _label_groups(train["case_id"], train["session_id"], "c")
    train_g3 = np.zeros(len(train), dtype=float)
    for fold, (train_mask, hold_mask) in enumerate(group_folds(groups, n_splits=5, seed=SEED)):
        legit = train_mask & ~fraud
        learned = train.iloc[np.flatnonzero(legit)][LONG_TERM].apply(pd.to_numeric, errors="coerce")
        fill = learned.median(numeric_only=True)
        reference, reference_cusum = _long_matrix(train.iloc[np.flatnonzero(legit)], fill)
        query, query_cusum = _long_matrix(train.iloc[np.flatnonzero(hold_mask)], fill)
        train_g3[hold_mask] = _g3_query(reference, reference_cusum, query, query_cusum, SEED + fold)
    learned = train.iloc[np.flatnonzero(~fraud)][LONG_TERM].apply(pd.to_numeric, errors="coerce")
    fill = learned.median(numeric_only=True)
    reference, reference_cusum = _long_matrix(train.iloc[np.flatnonzero(~fraud)], fill)
    cutoff = float(np.quantile(train_g3[~fraud], 0.995))
    return train_g3, cutoff, (reference, reference_cusum, fill)


def g3_apply(bundle: tuple, frame: pd.DataFrame) -> np.ndarray:
    reference, reference_cusum, fill = bundle
    query, query_cusum = _long_matrix(frame, fill)
    return _g3_query(reference, reference_cusum, query, query_cusum, SEED)


def load_part(name: str, part: pd.DataFrame) -> pd.DataFrame:
    frame = pd.read_parquet(ROOT / "out" / f"features_single_bank_{name}.parquet")
    frame["session_id"] = frame["session_id"].astype(str)
    meta = part[["session_id", "case_id", "customer_id", "is_fraud"]].copy()
    meta["session_id"] = meta["session_id"].astype(str)
    return frame.merge(meta, on="session_id", how="inner")


def compare_models(test: pd.DataFrame, scores: dict[str, np.ndarray]) -> None:
    label = test["label"].to_numpy(dtype=float)
    weight = test["sample_weight"].to_numpy(dtype=float)
    kind = test["scam_type"].astype(str).to_numpy()
    groups = cluster_index(cluster_of(test["case_id"], test["session_id"]))
    print(f"{'bản':<22} {'AUC':>24} {'investment recall@1%':>28}")
    for name, score in scores.items():
        auc = weighted_auc(label, score, weight)
        recall = type_recall(score, label, kind, "investment")

        def auc_of(index: np.ndarray, score: np.ndarray = score) -> float:
            return weighted_auc(label[index], score[index], weight[index])

        def recall_of(index: np.ndarray, score: np.ndarray = score) -> float:
            return type_recall(score[index], label[index], kind[index], "investment")

        auc_ci = bootstrap_values(groups, auc_of, DRAWS, SEED)
        recall_ci = bootstrap_values(groups, recall_of, DRAWS, SEED + 1)
        print(f"{name:<22} {format_ci(auc, auc_ci):>24} {format_ci(recall, recall_ci, percent=True):>28}")


def scenario_report(frame: pd.DataFrame) -> None:
    groups = cluster_index(_label_groups(frame["case_id"], frame["customer_id"], "c"))
    level = frame["level"].to_numpy()
    print(f"{'scenario':<28} {'n':>6} {'High':>22} {'Medium':>22} {'banner':>22} {'none':>22}")
    for name in ORDER:
        if name in {"legit_guided_by_relative", "legit_friend_loan"}:
            mask = frame["scenario_type"].astype(str).eq(name).to_numpy()
        else:
            mask = frame["scam_type"].astype(str).eq(name).to_numpy()
        if not mask.any():
            continue
        point = {key: float((level[mask] == key).mean()) for key in ("high", "medium", "banner", "none")}

        def statistic(index: np.ndarray, key: str, mask: np.ndarray = mask) -> float:
            chosen = mask[index]
            if not np.any(chosen):
                return np.nan
            return float((level[index][chosen] == key).mean())

        samples = {key: bootstrap_values(groups, lambda index, key=key: statistic(index, key), DRAWS, SEED) for key in point}
        print(
            f"{name:<28} {int(mask.sum()):6d} "
            f"{format_ci(point['high'], samples['high'], percent=True):>22} "
            f"{format_ci(point['medium'], samples['medium'], percent=True):>22} "
            f"{format_ci(point['banner'], samples['banner'], percent=True):>22} "
            f"{format_ci(point['none'], samples['none'], percent=True):>22}"
        )


def _count_ci(point: float, samples: np.ndarray) -> str:
    low, high = np.nanpercentile(samples, [2.5, 97.5])
    return f"{int(round(point)):,} [{int(round(low)):,}–{int(round(high)):,}]"


def outcome_table(frame: pd.DataFrame, column: str, order: list[str]) -> None:
    groups = cluster_index(_label_groups(frame["case_id"], frame["customer_id"], "c"))
    fraud = frame["is_fraud"].to_numpy(dtype=bool)
    hit = frame["level"].isin(["banner", "medium", "high"]).to_numpy()
    print(f"{'nhóm':<22} {'can thiệp':>22} {'precision':>22} {'recall':>22}")
    masks = [("tất cả", np.ones(len(frame), dtype=bool))]
    masks.extend((name, frame[column].astype(str).eq(name).to_numpy()) for name in order)
    for name, mask in masks:
        if not mask.any():
            continue
        intervened = mask & hit
        caught = mask & fraud
        point = {
            "count": float(intervened.sum()),
            "precision": float(fraud[intervened].mean()) if intervened.any() else float("nan"),
            "recall": float(hit[caught].mean()) if caught.any() else float("nan"),
        }

        def statistic(index: np.ndarray, key: str, mask: np.ndarray = mask) -> float:
            chosen = mask[index]
            flagged = hit[index]
            fraudulent = fraud[index]
            if key == "count":
                return float(np.sum(chosen & flagged))
            if key == "precision":
                pool = chosen & flagged
                return float(fraudulent[pool].mean()) if np.any(pool) else np.nan
            pool = chosen & fraudulent
            return float(flagged[pool].mean()) if np.any(pool) else np.nan

        samples = {key: bootstrap_values(groups, lambda index, key=key: statistic(index, key), DRAWS, SEED) for key in point}
        print(
            f"{name:<22} {_count_ci(point['count'], samples['count']):>22} "
            f"{format_ci(point['precision'], samples['precision'], percent=True):>22} "
            f"{format_ci(point['recall'], samples['recall'], percent=True):>22}"
        )


def load_fp_scale() -> dict[str, float]:
    import yaml

    with (SIM / "config.yaml").open(encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)
    raw = spec.get("decision", {}).get("c_fp_scale", {})
    return {str(key): float(value) for key, value in raw.items()}


def scale_for(segment: pd.Series, table: dict[str, float]) -> np.ndarray:
    scale = np.ones(len(segment), dtype=float)
    text = segment.astype(str).to_numpy()
    for name, value in table.items():
        scale[text == name] = value
    return scale


def family_counts(frame: pd.DataFrame, families: pd.DataFrame, anomaly: np.ndarray | None) -> np.ndarray:
    indexed = families.set_index("session_id")
    session = frame["session_id"]
    count = (
        session.map(indexed["call"]).fillna(False).to_numpy(dtype=bool).astype(np.int8)
        + session.map(indexed["entry"]).fillna(False).to_numpy(dtype=bool).astype(np.int8)
        + session.map(indexed["payee"]).fillna(False).to_numpy(dtype=bool).astype(np.int8)
        + session.map(indexed["funding"]).fillna(False).to_numpy(dtype=bool).astype(np.int8)
    )
    if anomaly is not None:
        count = count + np.asarray(anomaly, dtype=bool).astype(np.int8)
    return count


def choose_tau_hi(
    probability: np.ndarray,
    amount: np.ndarray,
    families: np.ndarray,
    legit: np.ndarray,
    fp_scale: np.ndarray,
) -> float:
    """Ngưỡng thấp nhất còn giữ Medium+High trên phiên hợp lệ không quá 0,45%."""
    base = levels_from_counts(probability, amount, families, COST_ETA, fp_scale, None)
    legit = np.asarray(legit, dtype=bool)
    n = int(legit.sum())
    base_hit = legit & np.isin(base, ["medium", "high"])
    room = int(np.floor(MH_CAP * n - int(base_hit.sum())))
    promotable = legit & (np.asarray(families) <= 1) & np.isin(base, ["none", "banner"])
    scores = np.asarray(probability, dtype=float)[promotable]
    tau = 1.0
    included = 0
    if room > 0 and len(scores):
        ordered = np.sort(scores)[::-1]
        start = 0
        while start < len(ordered):
            value = float(ordered[start])
            end = start + 1
            while end < len(ordered) and float(ordered[end]) == value:
                end += 1
            count = end - start
            if included + count > room:
                break
            included += count
            tau = value
            start = end
    level = levels_from_counts(probability, amount, families, COST_ETA, fp_scale, tau)
    rate = float(np.isin(level[legit], ["medium", "high"]).mean()) if n else float("nan")
    print(
        f"τ_hi = {tau:.4f}. Nâng thêm {included:,} phiên hợp lệ có 0–1 họ. "
        f"Medium+High {rate:.2%} trên {n:,} phiên hợp lệ. Trần 0,45%."
    )
    return tau


def one_segment(frame: pd.DataFrame, name: str, title: str) -> None:
    groups = cluster_index(_label_groups(frame["case_id"], frame["customer_id"], "c"))
    fraud = frame["is_fraud"].to_numpy(dtype=bool)
    hit = frame["level"].isin(["banner", "medium", "high"]).to_numpy()
    mask = frame["segment"].astype(str).eq(name).to_numpy()
    intervened = mask & hit
    caught = mask & fraud
    point = {
        "count": float(intervened.sum()),
        "precision": float(fraud[intervened].mean()) if intervened.any() else float("nan"),
        "recall": float(hit[caught].mean()) if caught.any() else float("nan"),
    }

    def statistic(index: np.ndarray, key: str) -> float:
        chosen = mask[index]
        flagged = hit[index]
        fraudulent = fraud[index]
        if key == "count":
            return float(np.sum(chosen & flagged))
        if key == "precision":
            pool = chosen & flagged
            return float(fraudulent[pool].mean()) if np.any(pool) else np.nan
        pool = chosen & fraudulent
        return float(flagged[pool].mean()) if np.any(pool) else np.nan

    samples = {key: bootstrap_values(groups, lambda index, key=key: statistic(index, key), DRAWS, SEED) for key in point}
    print(
        f"{title:<22} {_count_ci(point['count'], samples['count']):>22} "
        f"{format_ci(point['precision'], samples['precision'], percent=True):>22} "
        f"{format_ci(point['recall'], samples['recall'], percent=True):>22}"
    )


def rate_ci(frame: pd.DataFrame, flag: np.ndarray, title: str) -> None:
    groups = cluster_index(_label_groups(frame["case_id"], frame["customer_id"], "c"))
    point = float(np.mean(flag)) if len(flag) else float("nan")
    samples = bootstrap_values(groups, lambda index: float(np.mean(flag[index])) if len(index) else np.nan, DRAWS, SEED)
    print(f"{title}: {format_ci(point, samples, percent=True)}")


def money_ci(frame: pd.DataFrame) -> None:
    amount = pd.to_numeric(frame["amount"], errors="coerce").fillna(0).to_numpy(dtype=float)
    fraud = frame["is_fraud"].to_numpy(dtype=bool)
    stop = pd.to_numeric(frame["p_stop"], errors="coerce").fillna(0).to_numpy(dtype=float)
    strong = frame["action"].astype(str).isin(STRONG_ACTIONS).to_numpy()
    saved = np.where(fraud & strong, amount * stop, 0.0)
    damage = np.where(fraud, amount, 0.0)
    point_saved = float(saved.sum())
    point_damage = float(damage.sum())
    point_rate = point_saved / point_damage if point_damage else float("nan")
    groups = cluster_index(_label_groups(frame["case_id"], frame["customer_id"], "c"))

    def saved_of(index: np.ndarray) -> float:
        return float(saved[index].sum())

    def rate_of(index: np.ndarray) -> float:
        base = float(damage[index].sum())
        return float(saved[index].sum() / base) if base else np.nan

    saved_samples = bootstrap_values(groups, saved_of, DRAWS, SEED)
    rate_samples = bootstrap_values(groups, rate_of, DRAWS, SEED)
    low, high = np.nanpercentile(saved_samples, [2.5, 97.5])
    print(
        f"tiền tránh được: {point_saved:,.0f} [{low:,.0f}–{high:,.0f}] VND, "
        f"{format_ci(point_rate, rate_samples, percent=True)} của {point_damage:,.0f} VND thiệt hại test"
    )


def with_response(frame: pd.DataFrame, hang_of: dict, alone_of: dict, response: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    scam = work["scam_type"].astype(str)
    scenario = work["scenario_type"].astype(str)
    group = np.full(len(work), "legit", dtype=object)
    fraud = work["is_fraud"].to_numpy(dtype=bool)
    group[fraud] = scam.to_numpy()[fraud]
    group[scenario.eq("legit_guided_by_relative").to_numpy()] = "legit_guided_by_relative"
    work["scenario_group"] = group
    work["model_segment"] = np.where(work["segment"].astype(str).eq("cao tuổi"), "cao tuổi", "thường")
    level = work["level"].to_numpy()
    elderly = work["segment"].astype(str).eq("cao tuổi").to_numpy()
    guard = work["has_guardian"].fillna(False).to_numpy(dtype=bool)
    action = np.full(len(work), "allow", dtype=object)
    action[level == "banner"] = "g1_banner"
    action[level == "medium"] = "pause_and_verify"
    action[(level == "medium") & elderly & guard] = "guardian_confirm"
    action[level == "high"] = "hangup_required"
    work["action"] = action
    work["guardian_notified"] = False
    work["coached"] = work["scenario_group"].isin(COACHED)
    work["lives_alone"] = work["customer_id"].map(alone_of).fillna(False).astype(bool)
    work["hangs_up_before_transfer"] = work["session_id"].map(hang_of).fillna(False).astype(bool)
    lookup = response.rename(columns={"segment": "model_segment"})
    keys = ["scenario_group", "model_segment", "action", "guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"]
    for name in ("guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"):
        lookup[name] = lookup[name].astype(bool)
        work[name] = work[name].astype(bool)
    return work.merge(lookup[keys + ["p_stop"]], on=keys, how="left")


def guarded_rate(frame: pd.DataFrame, flag: np.ndarray, title: str) -> None:
    groups = cluster_index(_label_groups(frame["case_id"], frame["customer_id"], "c"))
    point = float(np.mean(flag)) if len(flag) else float("nan")
    samples = bootstrap_values(groups, lambda index: float(np.mean(flag[index])) if len(index) else np.nan, DRAWS, SEED)
    print(f"  {title}: {format_ci(point, samples, percent=True)}")


def main() -> None:
    parts = build_split()
    train = load_part("train", parts["train"])
    test = load_part("test", parts["test"])
    columns = [name for name in BANK if name in train.columns]
    print("Hiệu chỉnh isotonic trên điểm out-of-fold, rồi dịch odds về tỷ lệ is_fraud của train.")
    raw_oof = out_of_fold(train, columns)
    fraud = train["is_fraud"].to_numpy(dtype=float)
    calibrated_oof, apply = calibrate(raw_oof, fraud)
    print(f"  tỷ lệ lừa đảo train {fraud.mean():.3%}, trung bình p sau isotonic và dịch odds {calibrated_oof.mean():.3%}")
    model, categories = train_model(train, columns)
    raw_test = predict_model(model, categories, test, columns)
    calibrated_test = apply(raw_test)
    amounts = [1_000_000, 10_000_000, 100_000_000]
    print(f"η = {COST_ETA:g}. τ(A) = c_FP(A) / (c_FP(A) + η·A), c_FP(A) = 50.000 + 0,002·A.")
    print(f"{'A':>14} {'τ(p)':>10} {'ngưỡng p×A':>14}")
    for amount in amounts:
        tau = float(cost_tau(np.array([amount]), COST_ETA)[0])
        print(f"{amount:14,.0f} {tau:10.5f} {tau * amount:14,.0f}")
    rng = np.random.default_rng(SEED)
    fraud_index = np.flatnonzero(test["is_fraud"].to_numpy(dtype=bool))
    legit_index = np.flatnonzero(~test["is_fraud"].to_numpy(dtype=bool))
    picked = np.concatenate([rng.choice(fraud_index, 2, replace=False), rng.choice(legit_index, 3, replace=False)])
    print(f"{'phiên':<12} {'loại':<16} {'p thô':>8} {'p hiệu chỉnh':>14} {'A':>14} {'τ(A)':>8}")
    for index in picked:
        amount = pd.to_numeric(test["amount"], errors="coerce").iloc[index]
        amount = 0.0 if pd.isna(amount) else float(amount)
        tau = float(cost_tau(np.array([amount]), COST_ETA)[0])
        print(
            f"{test['session_id'].iloc[index]:<12} {str(test['scam_type'].iloc[index]):<16} "
            f"{raw_test[index]:8.4f} {calibrated_test[index]:14.4f} {amount:14,.0f} {tau:8.5f}"
        )

    print("\nĐiểm g3. Bản OOF chấm phiên train bằng fold không chứa nó.")
    store = FeatureStore(SIM / "out" / "observed")
    extra_train = long_term(parts["train"], store)
    extra_test = long_term(parts["test"], store)
    base_train = train[columns + ["session_id", "label", "sample_weight", "case_id", "is_fraud", "customer_id"]].copy()
    base_test = test[columns + ["session_id", "label", "sample_weight", "scam_type", "case_id", "is_fraud", "customer_id"]].copy()
    joined_train = base_train.merge(extra_train, on="session_id", how="inner")
    joined_test = base_test.merge(extra_test, on="session_id", how="inner")
    raw_model, raw_categories = train_model(joined_train, columns + LONG_TERM)
    g3_train, g3_test = g3_score(joined_train, joined_test, ~joined_train["is_fraud"].to_numpy(dtype=bool))
    oof_g3, g3_cutoff, g3_bundle = g3_out_of_fold(joined_train)
    oof_test = g3_apply(g3_bundle, joined_test)
    plain_model, plain_categories = train_model(base_train, columns)
    seen_train = joined_train.copy()
    seen_test = joined_test.copy()
    seen_train["g3"] = g3_train
    seen_test["g3"] = g3_test
    oof_train = joined_train.copy()
    oof_test_frame = joined_test.copy()
    oof_train["g3"] = oof_g3
    oof_test_frame["g3"] = oof_test
    seen_model, seen_categories = train_model(seen_train, columns + ["g3"])
    oof_model, oof_categories = train_model(oof_train, columns + ["g3"])
    scores = {
        "không dài hạn": predict_model(plain_model, plain_categories, joined_test, columns),
        "cột thô": predict_model(raw_model, raw_categories, joined_test, columns + LONG_TERM),
        "g3 trong mẫu": predict_model(seen_model, seen_categories, seen_test, columns + ["g3"]),
        "g3 OOF": predict_model(oof_model, oof_categories, oof_test_frame, columns + ["g3"]),
    }
    compare_models(joined_test, scores)
    plain_auc = weighted_auc(joined_test["label"].to_numpy(dtype=float), scores["không dài hạn"], joined_test["sample_weight"].to_numpy(dtype=float))
    oof_auc = weighted_auc(joined_test["label"].to_numpy(dtype=float), scores["g3 OOF"], joined_test["sample_weight"].to_numpy(dtype=float))
    use_family = oof_auc < plain_auc
    print(f"g3 OOF AUC {oof_auc:.3f}, không dài hạn {plain_auc:.3f}. {'Bỏ g3 khỏi LightGBM, dùng làm họ thứ 5.' if use_family else 'Giữ g3 trong LightGBM.'}")
    print(f"Ngưỡng họ g3: p99,5 phiên hợp lệ train = {g3_cutoff:.4f}")

    print("\nChấm buffer để đủ mọi phiên hợp lệ.")
    buffer = featurize(store, parts["buffer"], "single_bank")
    buffer["session_id"] = buffer["session_id"].astype(str)
    meta = parts["buffer"][["session_id", "case_id", "customer_id", "is_fraud"]].copy()
    meta["session_id"] = meta["session_id"].astype(str)
    buffer = buffer.merge(meta, on="session_id", how="inner")
    families = session_families(SIM / "out" / "observed")
    families["session_id"] = families["session_id"].astype(str)
    customers = pd.read_parquet(
        SIM / "out" / "observed" / "customers.parquet",
        columns=["customer_id", "segment", "age_group", "has_guardian"],
    )
    customers["customer_id"] = customers["customer_id"].astype(str)
    buffer_long = long_term(parts["buffer"], store)
    buffer_long["g3"] = g3_apply(g3_bundle, buffer_long)
    g3_of = dict(zip(joined_train["session_id"], oof_g3))
    g3_of.update(zip(joined_test["session_id"], oof_test))
    g3_of.update(zip(buffer_long["session_id"], buffer_long["g3"]))
    scenarios = pd.read_parquet(SIM / "out" / "truth" / "truth_scenarios.parquet", columns=["session_id", "scenario_type"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    scenario_of = dict(zip(scenarios["session_id"], scenarios["scenario_type"].astype(str)))
    pieces = (
        (train, calibrated_oof),
        (test, calibrated_test),
        (buffer, apply(predict_model(model, categories, buffer, columns))),
    )
    scored = []
    for frame, probability in pieces:
        work = frame[["session_id", "case_id", "customer_id", "amount", "is_fraud", "scam_type"]].copy()
        anomaly = None
        if use_family:
            anomaly = work["session_id"].map(g3_of).fillna(0).to_numpy(dtype=float) >= g3_cutoff
        work["p"] = np.asarray(probability, dtype=float)
        work["families_n"] = family_counts(frame, families, anomaly)
        scored.append(work)
    population = pd.concat(scored, ignore_index=True)
    population["customer_id"] = population["customer_id"].astype(str)
    population["scenario_type"] = population["session_id"].map(scenario_of)
    population = population.merge(customers, on="customer_id", how="left")
    population["has_guardian"] = population["has_guardian"].fillna(False).astype(bool)
    population["amount"] = pd.to_numeric(population["amount"], errors="coerce")
    test_ids = set(test["session_id"])
    test_rows = population["session_id"].isin(test_ids).to_numpy()
    legit_mask = ~population["is_fraud"].to_numpy(dtype=bool)
    ones = np.ones(len(population), dtype=float)
    print("\nQuét τ_hi trên điểm hiện tại, c_FP chưa nhân hệ số doanh nghiệp nhỏ.")
    tau_hi = choose_tau_hi(
        population["p"].to_numpy(dtype=float),
        population["amount"].to_numpy(dtype=float),
        population["families_n"].to_numpy(dtype=int),
        legit_mask,
        ones,
    )
    current = population.copy()
    current["level"] = levels_from_counts(
        current["p"].to_numpy(dtype=float),
        current["amount"].to_numpy(dtype=float),
        current["families_n"].to_numpy(dtype=int),
        COST_ETA,
        ones,
        tau_hi,
    )
    print("\nTest sau τ_hi. p ≥ τ_hi thì 0–1 họ vẫn tối thiểu Medium.")
    scenario_report(current.iloc[np.flatnonzero(test_rows)].reset_index(drop=True))
    print("\nDoanh nghiệp nhỏ trước feature p95 và trước c_FP ×3. Can thiệp = banner, Medium hoặc High.")
    print(f"{'bản':<22} {'can thiệp':>22} {'precision':>22} {'recall':>22}")
    one_segment(current, "doanh nghiệp nhỏ", "trước")

    print("\nHuấn luyện lại với amount / p95 90 ngày của chính tài khoản.")
    new_columns = columns + [P95]
    raw_oof_new = out_of_fold(joined_train, new_columns)
    calibrated_new, apply_new = calibrate(raw_oof_new, joined_train["is_fraud"].to_numpy(dtype=float))
    new_model, new_categories = train_model(joined_train, new_columns)
    new_test_raw = predict_model(new_model, new_categories, joined_test, new_columns)
    if P95 in buffer.columns:
        buffer_scored = buffer
    else:
        buffer_scored = buffer.merge(buffer_long[["session_id", P95]], on="session_id", how="left")
    new_buffer_raw = predict_model(new_model, new_categories, buffer_scored, new_columns)
    probability_of = dict(zip(joined_train["session_id"], calibrated_new))
    probability_of.update(zip(joined_test["session_id"], apply_new(new_test_raw)))
    probability_of.update(zip(buffer_scored["session_id"], apply_new(new_buffer_raw)))
    revised = population.copy()
    revised["p"] = revised["session_id"].map(probability_of).astype(float)
    fp_table = load_fp_scale()
    fp_scale = scale_for(revised["segment"], fp_table)
    print(f"c_FP doanh nghiệp nhỏ ×{fp_table.get('doanh nghiệp nhỏ', 1):g} (GIẢ ĐỊNH, config).")
    print("Quét lại τ_hi sau feature và hệ số chi phí.")
    tau_hi_final = choose_tau_hi(
        revised["p"].to_numpy(dtype=float),
        revised["amount"].to_numpy(dtype=float),
        revised["families_n"].to_numpy(dtype=int),
        legit_mask,
        fp_scale,
    )
    revised["level"] = levels_from_counts(
        revised["p"].to_numpy(dtype=float),
        revised["amount"].to_numpy(dtype=float),
        revised["families_n"].to_numpy(dtype=int),
        COST_ETA,
        fp_scale,
        tau_hi_final,
    )
    print("\nDoanh nghiệp nhỏ sau feature p95 và c_FP ×3.")
    print(f"{'bản':<22} {'can thiệp':>22} {'precision':>22} {'recall':>22}")
    one_segment(revised, "doanh nghiệp nhỏ", "sau")
    print("\nTest sau feature, c_FP ×3 và τ_hi.")
    scenario_report(revised.iloc[np.flatnonzero(test_rows)].reset_index(drop=True))

    hang = pd.read_parquet(SIM / "out" / "truth" / "truth_scenarios.parquet", columns=["session_id", "hangs_up_before_transfer"])
    hang["session_id"] = hang["session_id"].astype(str)
    hang_of = dict(zip(hang["session_id"], hang["hangs_up_before_transfer"].fillna(False).astype(bool)))
    latent = pd.read_parquet(SIM / "out" / "truth" / "customers_latent.parquet", columns=["customer_id", "lives_alone"])
    latent["customer_id"] = latent["customer_id"].astype(str)
    alone_of = dict(zip(latent["customer_id"], latent["lives_alone"].fillna(False).astype(bool)))
    response = pd.read_parquet(SIM / "out" / "truth" / "response_model.parquet")
    final_test = with_response(revised.iloc[np.flatnonzero(test_rows)].reset_index(drop=True), hang_of, alone_of, response)
    legit = revised.iloc[np.flatnonzero(legit_mask)].copy()
    print("\nBộ số cho slide. CI 95% theo vụ, không có vụ thì theo khách hàng. Seed 42, 200 lần.")
    print("Tiền tránh được chỉ tính trên test, khi hành động mạnh hơn banner, nhân p_stop GIẢ ĐỊNH.")
    money_ci(final_test)
    label = joined_test["is_fraud"].to_numpy(dtype=float)
    groups = cluster_index(cluster_of(joined_test["case_id"], joined_test["session_id"]))
    recall_point = recall_at(label, new_test_raw)
    recall_samples = bootstrap_values(groups, lambda index: recall_at(label[index], new_test_raw[index]), DRAWS, SEED)
    print(f"recall@1%: {format_ci(recall_point, recall_samples, percent=True)}")
    rate_ci(legit, legit["level"].isin(["medium", "high"]).to_numpy(), "Medium+High trên phiên hợp lệ")
    print("Precision theo nhóm tuổi. Can thiệp = banner, Medium hoặc High.")
    outcome_table(revised, "age_group", ["18-30", "31-50", "51-65", "66-85"])
    guided = final_test.loc[final_test["scenario_type"].astype(str).eq("legit_guided_by_relative")].copy()
    rate_ci(guided, guided["action"].astype(str).eq("hangup_required").to_numpy(), "bắt cúp máy người nhà hướng dẫn trên test")


if __name__ == "__main__":
    main()
