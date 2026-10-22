"""Lưới ott_share × tỷ lệ báo cáo × η.

Mỗi ô rút lại nhãn train theo report_rate rồi huấn luyện lại LightGBM trên cùng test.
Không nhân chỉ số đầu ra. ott_share áp cho mọi cuộc gọi, kể cả hợp lệ và người nhà hướng dẫn:
xác suất cuộc gọi là OTT, rồi thiết bị chỉ thấy OTT với ott_visible_to_device_share.
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

from eval.split import _label_groups, build_split, group_folds, ring_groups
from features.service import actions_from_families, session_families
from features.store import FEATURE_COLUMNS, LONG_TERM, NETWORK_ONLY
from train.baseline import recall_at, weighted_auc

BANK = [name for name in FEATURE_COLUMNS if name not in NETWORK_ONLY and name not in LONG_TERM]
SEED = 42


def load_config() -> dict:
    import yaml

    with (SIM / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, model_seed: int = SEED) -> np.ndarray:
    import lightgbm as lgb

    matrix_train = train[BANK].copy()
    matrix_test = test[BANK].copy()
    kinds = pd.api.types.CategoricalDtype(
        pd.concat([matrix_train["add_method"], matrix_test["add_method"]], ignore_index=True).astype("string").dropna().unique()
    )
    matrix_train["add_method"] = matrix_train["add_method"].astype("string").astype(kinds)
    matrix_test["add_method"] = matrix_test["add_method"].astype("string").astype(kinds)
    train_set = lgb.Dataset(
        matrix_train,
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
            "seed": model_seed,
        },
        train_set,
        num_boost_round=200,
    )
    return model.predict(matrix_test)


def call_visibility(calls: pd.DataFrame, sessions: pd.DataFrame, share: float, visible_p: float, seed: int) -> pd.DataFrame:
    """Mỗi cuộc gọi, kể cả hợp lệ, là OTT với xác suất share. Thiết bị thấy OTT với visible_p."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, int(round(share * 1000))]))
    work = calls.copy().reset_index(drop=True)
    work["session_id"] = work["session_id"].astype(str)
    ott = rng.random(len(work)) < share
    device = (~ott) | (rng.random(len(work)) < visible_p)
    carrier = ~ott
    meta = sessions[["session_id", "start_ts", "end_ts"]].copy()
    meta["session_id"] = meta["session_id"].astype(str)

    def covering(mask: np.ndarray, stamp: str) -> set[str]:
        part = work.loc[mask]
        if part.empty:
            return set()
        merged = part.merge(meta, on="session_id", how="inner")
        finish = merged["ts"] + pd.to_timedelta(merged["duration_s"].fillna(0), unit="s")
        mark = merged[stamp]
        hit = (merged["ts"] <= mark) & (finish > mark)
        return set(merged.loc[hit, "session_id"].astype(str))

    device_open = covering(device, "start_ts")
    carrier_alive = covering(carrier, "end_ts")
    carrier_any = set(work.loc[carrier, "session_id"].astype(str))
    out = pd.DataFrame({"session_id": meta["session_id"].to_numpy()})
    out["in_call_audio_mode"] = out["session_id"].isin(device_open).astype(float)
    on_call = np.full(len(out), np.nan)
    known = out["session_id"].isin(carrier_any).to_numpy()
    on_call[known] = out["session_id"].isin(carrier_alive).to_numpy()[known].astype(float)
    out["on_call"] = on_call
    recent = np.full(len(out), np.nan)
    active = out["on_call"].eq(1).to_numpy()
    recent[active] = 0.0
    out["recent_call_minutes_ago"] = recent
    out["call"] = out["session_id"].isin(device_open)
    return out


def redraw_labels(train: pd.DataFrame, rates: dict, scale: float, eta: float, rng: np.random.Generator) -> pd.DataFrame:
    work = train.copy()
    fraud = work.loc[work["is_fraud"].astype(bool), ["case_id", "scam_type"]].drop_duplicates("case_id")
    fraud = fraud.loc[~fraud["case_id"].astype(str).isin(["<NA>", "nan", "None", ""])]
    chance = fraud["scam_type"].astype(str).map(rates).fillna(0.5).to_numpy(dtype=float) * scale
    reported = rng.random(len(fraud)) < np.minimum(1.0, chance)
    ready = reported & (rng.random(len(fraud)) < eta)
    positive = set(fraud.loc[ready, "case_id"].astype(str))
    case = work["case_id"].astype(str)
    fraud_label = case.isin(positive).astype(int)
    legit = ~work["is_fraud"].astype(bool)
    work["label"] = np.where(legit, work["label_legit"].to_numpy(), fraud_label.to_numpy()).astype(int)
    return work


def _mean_sd(values: list[float]) -> str:
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return "—"
    spread = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    return f"{float(arr.mean()):.3f} ± {spread:.3f}"


def _mean_sd_pct(values: list[float]) -> str:
    arr = np.asarray(values, dtype=float)
    spread = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    return f"{100 * float(arr.mean()):.1f}% ± {100 * spread:.1f}"


def _patch(frame: pd.DataFrame, view: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["in_call_audio_mode"] = out["session_id"].map(view["in_call_audio_mode"]).fillna(0).astype(float)
    out["on_call"] = out["session_id"].map(view["on_call"])
    out["recent_call_minutes_ago"] = out["session_id"].map(view["recent_call_minutes_ago"])
    return out


def _fold_auc(frame: pd.DataFrame, groups: np.ndarray, labeled_case: set[str], model_seed: int) -> list[float]:
    scores = []
    work = frame.copy()
    fraud = work["is_fraud"].astype(bool).to_numpy()
    chosen = work["case_id"].astype(str).isin(labeled_case).to_numpy()
    work["label"] = np.where(fraud, chosen.astype(int), work["label_legit"].to_numpy()).astype(int)
    y_true = work["is_fraud"].to_numpy(dtype=float)
    weight = work["sample_weight"].to_numpy(dtype=float)
    for train_mask, test_mask in group_folds(groups, n_splits=5, seed=SEED):
        if y_true[test_mask].sum() == 0 or work.loc[train_mask, "label"].sum() == 0:
            continue
        score = fit_predict(work.loc[train_mask], work.loc[test_mask], model_seed)
        scores.append(weighted_auc(y_true[test_mask], score, weight[test_mask]))
    return scores


def main() -> None:
    cfg = load_config()
    grid = cfg["labels"]["sensitivity"]
    rates = cfg["labels"]["report_rate"]
    visible_p = float(cfg["sessions"]["ott_visible_to_device_share"])
    seed = int(cfg["seed"])
    parts = build_split()
    out = ROOT / "out"
    frames = {}
    for name in ("train", "test"):
        frame = pd.read_parquet(out / f"features_single_bank_{name}.parquet")
        frame["session_id"] = frame["session_id"].astype(str)
        meta = parts[name][["session_id", "case_id", "scam_type", "is_fraud", "label"]].copy()
        meta["session_id"] = meta["session_id"].astype(str)
        meta = meta.rename(columns={"label": "label_legit"})
        frame = frame.drop(columns=["scam_type"], errors="ignore").merge(meta, on="session_id", how="inner")
        frames[name] = frame
    calls = pd.read_parquet(SIM / "out" / "observed" / "call_events.parquet", columns=["session_id", "ts", "duration_s", "channel"])
    sessions = pd.read_parquet(SIM / "out" / "observed" / "sessions.parquet", columns=["session_id", "start_ts", "end_ts"])
    families = session_families(SIM / "out" / "observed")
    families["session_id"] = families["session_id"].astype(str)
    scenarios = pd.read_parquet(SIM / "out" / "truth" / "truth_scenarios.parquet", columns=["session_id", "case_id", "scenario_type"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    cases = pd.read_parquet(SIM / "out" / "truth" / "truth_cases.parquet", columns=["case_id", "ring_id"])
    ring_of = dict(zip(cases["case_id"].astype(str), cases["ring_id"].astype(str)))
    guided = set(scenarios.loc[scenarios["scenario_type"].astype(str).eq("legit_guided_by_relative"), "session_id"])
    print(
        "Lưới: mỗi ô 5 seed. Rút nhãn train theo report_rate × hệ số và η, rồi huấn luyện lại. "
        "Test giữ nhãn sự thật. In mean ± sd. ring_id chỉ để chia fold."
    )
    print(f"{'ott':>5} {'báo':>5} {'η':>5} {'dương':>10} {'AUC':>18} {'recall@1%':>18} {'cúp máy người nhà':>22}")
    stored: dict[tuple, dict] = {}
    for share in grid["ott_share"]:
        for scale in grid["report_rate_scale"]:
            for eta in grid["eta_scale"]:
                aucs, recalls, hangs, positives = [], [], [], []
                draws = []
                for index in range(5):
                    view = call_visibility(calls, sessions, float(share), visible_p, seed + index).drop_duplicates("session_id").set_index("session_id")
                    train = _patch(frames["train"], view)
                    test = _patch(frames["test"], view)
                    sequence = [seed, int(round(float(share) * 1000)), int(round(float(scale) * 10)), int(round(float(eta) * 10))]
                    if index:
                        sequence.append(index)
                    rng = np.random.default_rng(np.random.SeedSequence(sequence))
                    labeled = redraw_labels(train, rates, float(scale), float(eta), rng)
                    score = fit_predict(labeled, test, SEED + index)
                    y = test["label"].to_numpy(dtype=float)
                    weight = test["sample_weight"].to_numpy(dtype=float)
                    aucs.append(weighted_auc(y, score, weight))
                    recalls.append(recall_at(y, score))
                    positives.append(int(labeled["label"].sum()))
                    fam = families.copy()
                    fam["call"] = fam["session_id"].map(view["call"]).fillna(False).astype(bool)
                    action = actions_from_families(fam)
                    action.index = fam["session_id"].to_numpy()
                    hangs.append(float(action.reindex(list(guided)).eq("hangup_required").mean()))
                    if float(share) == 0.5 and float(scale) == 1.0 and float(eta) == 1.0:
                        draws.append((index, view))
                print(
                    f"{float(share):5.1f} {float(scale):5.1f} {float(eta):5.1f} {int(np.mean(positives)):10d} "
                    f"{_mean_sd(aucs):>18} {_mean_sd_pct(recalls):>18} {_mean_sd_pct(hangs):>22}"
                )
                if draws:
                    stored[(float(share), float(scale), float(eta))] = {"auc": aucs, "draws": draws}
    pooled = pd.concat([frames["train"], frames["test"]], ignore_index=True)
    print("\nÔ ott 0.5, báo 1.0, η 1.0. So sánh chia thời gian với GroupKFold.")
    print("Fold theo vụ vẫn để cùng một đường dây ở hai phía. Fold theo ring_id thì không. ring_id không vào mô hình.")
    print(f"{'seed':>6} {'time AUC':>10} {'fold theo vụ':>16} {'fold theo đường dây':>20}")
    leak = []
    bundle = stored[(0.5, 1.0, 1.0)]
    for index, view in bundle["draws"]:
        block = _patch(pooled, view)
        sequence = [seed, 500, 10, 10]
        if index:
            sequence.append(index)
        rng = np.random.default_rng(np.random.SeedSequence(sequence))
        labeled = redraw_labels(block, rates, 1.0, 1.0, rng)
        positive_cases = set(labeled.loc[labeled["is_fraud"].astype(bool) & labeled["label"].eq(1), "case_id"].astype(str))
        by_case = _label_groups(block["case_id"], block["session_id"], "c")
        by_ring = ring_groups(block["case_id"], block["session_id"], ring_of)
        case_auc = _fold_auc(block, by_case, positive_cases, SEED + index)
        ring_auc = _fold_auc(block, by_ring, positive_cases, SEED + index)
        leak.append((float(np.mean(case_auc)), float(np.mean(ring_auc))))
        print(
            f"{SEED + index:6d} {bundle['auc'][index]:10.3f} "
            f"{_mean_sd(case_auc):>16} {_mean_sd(ring_auc):>20}"
        )
    case_mean = float(np.mean([item[0] for item in leak]))
    ring_mean = float(np.mean([item[1] for item in leak]))
    if case_mean - ring_mean > 0.03:
        print(f"AUC tụt {case_mean - ring_mean:.3f} khi giữ cả đường dây ở một phía. Có rò rỉ qua đường dây.")
    else:
        print(f"AUC fold theo vụ {case_mean:.3f}, theo đường dây {ring_mean:.3f}. Không thấy rò rỉ qua đường dây.")


def ring_only() -> None:
    """Chỉ so fold theo vụ với fold theo đường dây cho ô ott 0.5, báo 1, η 1."""
    cfg = load_config()
    rates = cfg["labels"]["report_rate"]
    visible_p = float(cfg["sessions"]["ott_visible_to_device_share"])
    seed = int(cfg["seed"])
    parts = build_split()
    frames = {}
    for name in ("train", "test"):
        frame = pd.read_parquet(ROOT / "out" / f"features_single_bank_{name}.parquet")
        frame["session_id"] = frame["session_id"].astype(str)
        meta = parts[name][["session_id", "case_id", "is_fraud", "label"]].copy()
        meta["session_id"] = meta["session_id"].astype(str)
        meta = meta.rename(columns={"label": "label_legit"})
        frames[name] = frame.merge(meta, on="session_id", how="inner")
    calls = pd.read_parquet(SIM / "out" / "observed" / "call_events.parquet", columns=["session_id", "ts", "duration_s", "channel"])
    sessions = pd.read_parquet(SIM / "out" / "observed" / "sessions.parquet", columns=["session_id", "start_ts", "end_ts"])
    cases = pd.read_parquet(SIM / "out" / "truth" / "truth_cases.parquet", columns=["case_id", "ring_id"])
    ring_of = dict(zip(cases["case_id"].astype(str), cases["ring_id"].astype(str)))
    pooled = pd.concat([frames["train"], frames["test"]], ignore_index=True)
    print(f"{'seed':>6} {'time AUC':>10} {'fold theo vụ':>16} {'fold theo đường dây':>20}")
    leak = []
    for index in range(5):
        view = call_visibility(calls, sessions, 0.5, visible_p, seed + index).drop_duplicates("session_id").set_index("session_id")
        train = _patch(frames["train"], view)
        test = _patch(frames["test"], view)
        sequence = [seed, 500, 10, 10]
        if index:
            sequence.append(index)
        rng = np.random.default_rng(np.random.SeedSequence(sequence))
        labeled = redraw_labels(train, rates, 1.0, 1.0, rng)
        score = fit_predict(labeled, test, SEED + index)
        time_auc = weighted_auc(test["label"].to_numpy(dtype=float), score, test["sample_weight"].to_numpy(dtype=float))
        block = _patch(pooled, view)
        rng = np.random.default_rng(np.random.SeedSequence(sequence))
        labeled_pool = redraw_labels(block, rates, 1.0, 1.0, rng)
        positive_cases = set(labeled_pool.loc[labeled_pool["is_fraud"].astype(bool) & labeled_pool["label"].eq(1), "case_id"].astype(str))
        case_auc = _fold_auc(block, _label_groups(block["case_id"], block["session_id"], "c"), positive_cases, SEED + index)
        ring_auc = _fold_auc(block, ring_groups(block["case_id"], block["session_id"], ring_of), positive_cases, SEED + index)
        leak.append((float(np.mean(case_auc)), float(np.mean(ring_auc))))
        print(f"{SEED + index:6d} {time_auc:10.3f} {_mean_sd(case_auc):>16} {_mean_sd(ring_auc):>20}", flush=True)
    case_mean = float(np.mean([item[0] for item in leak]))
    ring_mean = float(np.mean([item[1] for item in leak]))
    if case_mean - ring_mean > 0.03:
        print(f"AUC tụt {case_mean - ring_mean:.3f} khi giữ cả đường dây ở một phía. Có rò rỉ qua đường dây.")
    else:
        print(f"AUC fold theo vụ {case_mean:.3f}, theo đường dây {ring_mean:.3f}. Không thấy rò rỉ qua đường dây.")


if __name__ == "__main__":
    main()
