"""Tám bản: p95 × c_FP doanh nghiệp nhỏ ×3 × T4c. Cùng test, seed 42."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.bootstrap import bootstrap_values, cluster_index, format_ci
from eval.decision import (
    BANK,
    COST_ETA,
    P95,
    SEED,
    STRONG_ACTIONS,
    T4C,
    calibrate,
    choose_tau_hi,
    family_counts,
    g3_apply,
    g3_out_of_fold,
    load_fp_scale,
    load_part,
    out_of_fold,
    predict_model,
    scale_for,
    train_model,
    with_response,
)
from eval.split import _label_groups, build_split
from features.service import levels_from_counts, session_families
from features.store import LONG_TERM, FeatureStore, using_store
from features import compute_features
from train.baseline import recall_at

DRAWS = 200
FLAGS = [f"{name}_missing" for name in T4C]


def behavior_frame(part: pd.DataFrame, store: FeatureStore) -> pd.DataFrame:
    rows = []
    ids = []
    for record in part.itertuples(index=False):
        as_of = pd.Timestamp(record.end_ts) + pd.Timedelta(microseconds=1)
        rows.append(store.behavior_at(str(record.session_id), as_of, "single_bank"))
        ids.append(str(record.session_id))
    frame = pd.DataFrame(rows)
    frame.insert(0, "session_id", ids)
    return frame


def featurize_network(store: FeatureStore, part: pd.DataFrame) -> pd.DataFrame:
    rows = []
    with using_store(store):
        for record in part.itertuples(index=False):
            as_of = pd.Timestamp(record.end_ts) + pd.Timedelta(microseconds=1)
            features = compute_features(str(record.session_id), as_of, "network")
            features["session_id"] = str(record.session_id)
            features["scam_type"] = str(record.scam_type)
            rows.append(features)
    frame = pd.DataFrame(rows)
    for name, flag in zip(T4C, FLAGS):
        values = pd.to_numeric(frame[name], errors="coerce")
        frame[name] = values
        frame[flag] = values.isna().astype(np.int8)
    return frame


def attach_t4c(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    network = pd.read_parquet(ROOT / "out" / f"features_network_{name}.parquet", columns=["session_id", *T4C])
    network["session_id"] = network["session_id"].astype(str)
    network = network.drop_duplicates("session_id")
    for column, flag in zip(T4C, FLAGS):
        values = pd.to_numeric(network[column], errors="coerce")
        network[column] = values
        network[flag] = values.isna().astype(np.int8)
    bare = frame.drop(columns=[column for column in [*T4C, *FLAGS] if column in frame.columns])
    return bare.merge(network, on="session_id", how="left")


def money_parts(frame: pd.DataFrame) -> tuple[float, float, float, np.ndarray, np.ndarray]:
    amount = pd.to_numeric(frame["amount"], errors="coerce").fillna(0).to_numpy(dtype=float)
    fraud = frame["is_fraud"].to_numpy(dtype=bool)
    stop = pd.to_numeric(frame["p_stop"], errors="coerce").fillna(0).to_numpy(dtype=float)
    strong = frame["action"].astype(str).isin(STRONG_ACTIONS).to_numpy()
    saved = np.where(fraud & strong, amount * stop, 0.0)
    damage = np.where(fraud, amount, 0.0)
    point_saved = float(saved.sum())
    point_damage = float(damage.sum())
    point_rate = point_saved / point_damage if point_damage else float("nan")
    return point_saved, point_damage, point_rate, saved, damage


def segment_rates(frame: pd.DataFrame, name: str) -> tuple[float, float]:
    fraud = frame["is_fraud"].to_numpy(dtype=bool)
    hit = frame["level"].isin(["banner", "medium", "high"]).to_numpy()
    mask = frame["segment"].astype(str).eq(name).to_numpy()
    intervened = mask & hit
    caught = mask & fraud
    precision = float(fraud[intervened].mean()) if intervened.any() else float("nan")
    recall = float(hit[caught].mean()) if caught.any() else float("nan")
    return precision, recall


def overlaps(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def prepare() -> dict:
    parts = build_split()
    train = load_part("train", parts["train"]).reset_index(drop=True)
    test = load_part("test", parts["test"]).reset_index(drop=True)
    store = FeatureStore(SIM / "out" / "observed")
    extra_train = behavior_frame(parts["train"], store)
    extra_test = behavior_frame(parts["test"], store)
    keep = ["session_id", P95, *LONG_TERM]
    train = train.merge(extra_train[keep], on="session_id", how="left")
    test = test.merge(extra_test[keep], on="session_id", how="left")
    train = attach_t4c(train, "train")
    test = attach_t4c(test, "test")
    oof_g3, cutoff, g3_bundle = g3_out_of_fold(train)
    test_g3 = g3_apply(g3_bundle, test)
    buffer = featurize_network(store, parts["buffer"])
    meta = parts["buffer"][["session_id", "case_id", "customer_id", "is_fraud"]].copy()
    meta["session_id"] = meta["session_id"].astype(str)
    buffer = buffer.merge(meta, on="session_id", how="inner").reset_index(drop=True)
    buffer_g3 = g3_apply(g3_bundle, buffer)
    families = session_families(SIM / "out" / "observed")
    families["session_id"] = families["session_id"].astype(str)
    customers = pd.read_parquet(
        SIM / "out" / "observed" / "customers.parquet",
        columns=["customer_id", "segment", "has_guardian"],
    )
    customers["customer_id"] = customers["customer_id"].astype(str)
    scenarios = pd.read_parquet(
        SIM / "out" / "truth" / "truth_scenarios.parquet",
        columns=["session_id", "scenario_type", "hangs_up_before_transfer"],
    )
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    hang_of = dict(zip(scenarios["session_id"], scenarios["hangs_up_before_transfer"].fillna(False).astype(bool)))
    scenario_of = dict(zip(scenarios["session_id"], scenarios["scenario_type"].astype(str)))
    latent = pd.read_parquet(SIM / "out" / "truth" / "customers_latent.parquet", columns=["customer_id", "lives_alone"])
    latent["customer_id"] = latent["customer_id"].astype(str)
    alone_of = dict(zip(latent["customer_id"], latent["lives_alone"].fillna(False).astype(bool)))
    response = pd.read_parquet(SIM / "out" / "truth" / "response_model.parquet")
    g3_of = dict(zip(train["session_id"], oof_g3))
    g3_of.update(zip(test["session_id"], test_g3))
    g3_of.update(zip(buffer["session_id"], buffer_g3))
    pieces = []
    for frame in (train, test, buffer):
        work = frame[["session_id", "case_id", "customer_id", "amount", "is_fraud", "scam_type"]].copy()
        anomaly = work["session_id"].map(g3_of).fillna(0).to_numpy(dtype=float) >= cutoff
        work["families_n"] = family_counts(frame, families, anomaly)
        pieces.append(work)
    population = pd.concat(pieces, ignore_index=True)
    population["customer_id"] = population["customer_id"].astype(str)
    population["scenario_type"] = population["session_id"].map(scenario_of)
    population = population.merge(customers, on="customer_id", how="left")
    population["has_guardian"] = population["has_guardian"].fillna(False).astype(bool)
    population["amount"] = pd.to_numeric(population["amount"], errors="coerce")
    test_rows = population["session_id"].isin(set(test["session_id"])).to_numpy()
    legit = ~population["is_fraud"].to_numpy(dtype=bool)
    base = [name for name in BANK if name in train.columns and name != P95]
    return {
        "train": train,
        "test": test,
        "buffer": buffer,
        "population": population,
        "test_rows": test_rows,
        "legit": legit,
        "base": base,
        "hang_of": hang_of,
        "alone_of": alone_of,
        "response": response,
        "fp_table": load_fp_scale(),
        "g3_cutoff": cutoff,
        "g3_bundle": g3_bundle,
    }


def fit_scores(bundle: dict, columns: list[str]) -> dict[str, np.ndarray]:
    train = bundle["train"]
    test = bundle["test"]
    buffer = bundle["buffer"]
    raw_oof = out_of_fold(train, columns)
    calibrated, apply = calibrate(raw_oof, train["is_fraud"].to_numpy(dtype=float))
    model, categories = train_model(train, columns)
    raw_test = predict_model(model, categories, test, columns)
    raw_buffer = predict_model(model, categories, buffer, columns)
    probability = dict(zip(train["session_id"], calibrated))
    probability.update(zip(test["session_id"], apply(raw_test)))
    probability.update(zip(buffer["session_id"], apply(raw_buffer)))
    return {"map": probability, "raw_test": raw_test, "model": model, "categories": categories, "apply": apply}


def evaluate(bundle: dict, scores: dict[str, np.ndarray], use_scale: bool) -> dict:
    population = bundle["population"]
    probability = population["session_id"].map(scores["map"]).to_numpy(dtype=float)
    amount = population["amount"].to_numpy(dtype=float)
    families = population["families_n"].to_numpy(dtype=int)
    scale = scale_for(population["segment"], bundle["fp_table"]) if use_scale else np.ones(len(population), dtype=float)
    tau_hi = choose_tau_hi(probability, amount, families, bundle["legit"], scale, quiet=True)
    level = levels_from_counts(probability, amount, families, COST_ETA, scale, tau_hi)
    scored = population.copy()
    scored["level"] = level
    test = with_response(scored.iloc[np.flatnonzero(bundle["test_rows"])].reset_index(drop=True), bundle["hang_of"], bundle["alone_of"], bundle["response"])
    saved, damage, rate, saved_row, damage_row = money_parts(test)
    groups = cluster_index(_label_groups(test["case_id"], test["customer_id"], "c"))
    saved_ci = bootstrap_values(groups, lambda index: float(saved_row[index].sum()), DRAWS, SEED)
    rate_ci = bootstrap_values(
        groups,
        lambda index: float(saved_row[index].sum() / damage_row[index].sum()) if damage_row[index].sum() else np.nan,
        DRAWS,
        SEED,
    )
    label = bundle["test"]["is_fraud"].to_numpy(dtype=float)
    precision, recall = segment_rates(scored, "doanh nghiệp nhỏ")
    friend = test["scenario_type"].astype(str).eq("legit_friend_loan").to_numpy()
    guided = test["scenario_type"].astype(str).eq("legit_guided_by_relative").to_numpy()
    medium_loan = float(np.mean(test["level"].to_numpy()[friend] == "medium")) if friend.any() else float("nan")
    hangup = float(np.mean(test["action"].to_numpy()[guided] == "hangup_required")) if guided.any() else float("nan")
    return {
        "saved": saved,
        "saved_low": float(np.nanpercentile(saved_ci, 2.5)),
        "saved_high": float(np.nanpercentile(saved_ci, 97.5)),
        "rate": rate,
        "rate_low": float(np.nanpercentile(rate_ci, 2.5)),
        "rate_high": float(np.nanpercentile(rate_ci, 97.5)),
        "damage": damage,
        "recall": recall_at(label, scores["raw_test"]),
        "precision": precision,
        "dn_recall": recall,
        "medium_loan": medium_loan,
        "hangup": hangup,
        "tau_hi": tau_hi,
    }


def main() -> None:
    bundle = prepare()
    fitted: dict[tuple[bool, bool], dict] = {}
    rows = []
    for use_p95 in (False, True):
        for use_t4c in (False, True):
            columns = list(bundle["base"])
            if use_p95:
                columns.append(P95)
            if use_t4c:
                columns.extend([*T4C, *FLAGS])
            fitted[(use_p95, use_t4c)] = fit_scores(bundle, columns)
            fitted[(use_p95, use_t4c)]["ncols"] = len(columns)
    for use_p95 in (False, True):
        for use_scale in (False, True):
            for use_t4c in (False, True):
                scores = fitted[(use_p95, use_t4c)]
                metrics = evaluate(bundle, scores, use_scale)
                metrics.update(
                    {
                        "p95": use_p95,
                        "scale": use_scale,
                        "t4c": use_t4c,
                        "ncols": scores["ncols"],
                    }
                )
                rows.append(metrics)
    best = max(rows, key=lambda item: item["saved"])
    band = (best["saved_low"], best["saved_high"])
    pool = [item for item in rows if overlaps((item["saved_low"], item["saved_high"]), band)]
    chosen = min(pool, key=lambda item: (item["ncols"], -item["saved"]))
    print(
        f"{'p95':>3} {'fp×3':>4} {'T4c':>4} {'cột':>4} "
        f"{'tiền tránh được (VND)':>42} {'% thiệt hại':>22} {'recall@1%':>10} "
        f"{'DN prec':>8} {'DN rec':>8} {'Medium vay':>11} {'cúp máy':>8}"
    )
    for item in rows:
        mark = "*" if item is chosen else " "
        saved = f"{item['saved']:,.0f} [{item['saved_low']:,.0f}–{item['saved_high']:,.0f}]"
        share = f"{100 * item['rate']:.1f}% [{100 * item['rate_low']:.1f}–{100 * item['rate_high']:.1f}]"
        print(
            f"{mark}{int(item['p95']):>2} {int(item['scale']):>4} {int(item['t4c']):>4} {item['ncols']:>4} "
            f"{saved:>42} {share:>22} {100 * item['recall']:>9.1f}% "
            f"{100 * item['precision']:>7.1f}% {100 * item['dn_recall']:>7.1f}% "
            f"{100 * item['medium_loan']:>10.1f}% {100 * item['hangup']:>7.1f}%"
        )
    print(
        "chọn: "
        f"p95={'có' if chosen['p95'] else 'không'}, "
        f"c_FP DN ×3={'có' if chosen['scale'] else 'không'}, "
        f"T4c={'có' if chosen['t4c'] else 'không'} "
        f"({chosen['ncols']} cột, τ_hi={chosen['tau_hi']:.4f}). "
        "Tiền tránh được cao nhất; nếu CI chồng nhau thì lấy bản ít cột hơn."
    )


if __name__ == "__main__":
    main()
