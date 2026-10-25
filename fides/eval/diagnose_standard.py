"""Chẩn đoán profile standard. Không ghi artifact."""

from __future__ import annotations

import numpy as np
import pandas as pd

from eval.bootstrap import bootstrap_values, cluster_index
from eval.model_v21 import (
    ADVANCED,
    BASIC,
    SEED,
    STANDARD,
    _booster,
    _categories,
    _label_groups,
    _matrix,
    _metrics,
    fit_profile,
    prepare,
)
from eval.split import SIM
from features.service import session_families
from train.baseline import recall_at

SDK = ["in_call", "call_seconds_since_open", "call_ended_in_session", "acct_input", "app_switches"]
HARD = (
    "legit_drain",
    "legit_new_payee_large",
    "legit_passthrough",
    "legit_on_call_unknown",
    "legit_guided_by_relative",
    "legit_friend_loan",
)
SEEDS = (42, 43, 44, 45, 46)
ADV_NO_SDK = [name for name in ADVANCED if name not in SDK]


def _scenarios() -> pd.DataFrame:
    frame = pd.read_parquet(SIM / "out" / "truth" / "truth_scenarios.parquet", columns=["session_id", "scenario_type", "sample_weight"])
    frame["session_id"] = frame["session_id"].astype(str)
    frame["scenario_type"] = frame["scenario_type"].astype(str)
    return frame.drop_duplicates("session_id")


def _weight_report(train: pd.DataFrame, scenarios: pd.DataFrame, columns: list[str], name: str) -> None:
    import lightgbm as lgb

    categories = _categories(train, columns)
    weight = train["sample_weight"].to_numpy(dtype=float)
    label = train["label"].to_numpy()
    dataset = lgb.Dataset(
        _matrix(train, columns, categories),
        label=label,
        weight=weight,
        categorical_feature=[item for item in columns if item in categories] or "auto",
        free_raw_data=False,
    )
    got = np.asarray(dataset.get_weight(), dtype=float)
    gap = float(np.max(np.abs(got - weight))) if len(weight) else 0.0
    kind = train["session_id"].astype(str).map(dict(zip(scenarios["session_id"], scenarios["scenario_type"]))).fillna("(không scenario)").to_numpy()
    truth_w = train["session_id"].astype(str).map(dict(zip(scenarios["session_id"], scenarios["sample_weight"].astype(float))))
    print(f"weight {name}: Dataset.get_weight lệch tối đa {gap:.3g} so với cột sample_weight. tổng {weight.sum():,.1f}", flush=True)
    print(f"  khớp truth theo session: {bool(np.allclose(weight, truth_w.to_numpy(dtype=float), equal_nan=False))}", flush=True)
    for bit in (0, 1):
        mask = label == bit
        print(f"  nhãn {bit}: n {int(mask.sum()):,}  tổng weight {weight[mask].sum():,.1f}", flush=True)
    hard = np.isin(kind, HARD)
    print(f"  mẫu khó: n {int(hard.sum()):,}  tổng weight {weight[hard].sum():,.1f}", flush=True)
    print(f"  còn lại: n {int((~hard).sum()):,}  tổng weight {weight[~hard].sum():,.1f}", flush=True)
    for item in HARD:
        mask = kind == item
        if not mask.any():
            print(f"    {item}: n 0", flush=True)
            continue
        print(
            f"    {item}: n {int(mask.sum()):,}  tổng weight {weight[mask].sum():,.1f}  weight/phiên {weight[mask].mean():.4f}",
            flush=True,
        )


def _slice_masks(frame: pd.DataFrame, scenarios: pd.DataFrame) -> dict[str, np.ndarray]:
    kind = frame["session_id"].astype(str).map(dict(zip(scenarios["session_id"], scenarios["scenario_type"]))).fillna("")
    fraud = frame["is_fraud"].to_numpy(dtype=bool)
    return {
        "fraud": fraud,
        "legit": ~fraud,
        "legit_guided": kind.eq("legit_guided_by_relative").to_numpy(),
        "legit_friend_loan": kind.eq("legit_friend_loan").to_numpy(),
    }


def _value_line(series: pd.Series) -> str:
    null = float(series.isna().mean()) if len(series) else float("nan")
    if series.name == "acct_input":
        counts = series.astype("string").fillna("(null)").value_counts(normalize=True)
        bits = " ".join(f"{key} {100 * value:.1f}%" for key, value in counts.items())
        return f"null {100 * null:.1f}% | {bits}"
    values = pd.to_numeric(series, errors="coerce")
    observed = values.dropna()
    if observed.empty:
        return f"null {100 * null:.1f}%"
    share_pos = float((observed > 0).mean())
    return (
        f"null {100 * null:.1f}%  mean {observed.mean():.2f}  p50 {observed.median():.2f}  "
        f">0 {100 * share_pos:.1f}%  =1 {100 * float((observed == 1).mean()):.1f}%"
    )


def _distributions(frames: dict[str, pd.DataFrame], scenarios: pd.DataFrame) -> None:
    print("phân phối 5 cột SDK", flush=True)
    for part in ("train", "test"):
        frame = frames[part]
        masks = _slice_masks(frame, scenarios)
        for group, mask in masks.items():
            held = frame.loc[mask]
            print(f"  {part} {group} n={len(held):,}", flush=True)
            for name in SDK:
                print(f"    {name}: {_value_line(held[name])}", flush=True)


def _family_gap(frames: dict[str, pd.DataFrame], families: pd.DataFrame) -> None:
    indexed = families.set_index("session_id")
    print("lệch họ hiện tại so với SDK", flush=True)
    for part in ("train", "test"):
        frame = frames[part]
        sid = frame["session_id"].astype(str)
        call_now = sid.map(indexed["call"]).fillna(False).to_numpy(dtype=bool)
        entry_now = sid.map(indexed["entry"]).fillna(False).to_numpy(dtype=bool)
        call_sdk = frame["in_call"].eq(1).to_numpy()
        ended = frame["call_ended_in_session"].eq(1).to_numpy()
        entry_sdk = frame["acct_input"].astype("string").eq("pasted").to_numpy()
        print(
            f"  {part}: call lệch in_call {float(np.mean(call_now != call_sdk)):.2%}, "
            f"call lệch in_call|ended {float(np.mean(call_now != (call_sdk | ended))):.2%}, "
            f"entry lệch acct_input=pasted {float(np.mean(entry_now != entry_sdk)):.2%}",
            flush=True,
        )


def sdk_counts(frames: dict[str, pd.DataFrame], families: pd.DataFrame) -> np.ndarray:
    indexed = families.set_index("session_id")
    parts = []
    for name in ("train", "test", "buffer"):
        frame = frames[name]
        sid = frame["session_id"].astype(str)
        call = frame["in_call"].eq(1).to_numpy(dtype=bool)
        entry = frame["acct_input"].astype("string").eq("pasted").to_numpy(dtype=bool)
        payee = sid.map(indexed["payee"]).fillna(False).to_numpy(dtype=bool)
        funding = sid.map(indexed["funding"]).fillna(False).to_numpy(dtype=bool)
        parts.append(call.astype(np.int8) + entry.astype(np.int8) + payee.astype(np.int8) + funding.astype(np.int8))
    return np.concatenate(parts)


def _bins(name: str, values: pd.Series) -> pd.Series:
    if name == "in_call":
        out = pd.Series(np.where(values.isna(), "(null)", np.where(values.eq(1), "1", "0")), index=values.index)
        return out
    if name == "acct_input":
        return values.astype("string").fillna("(null)")
    numeric = pd.to_numeric(values, errors="coerce")
    if name == "call_seconds_since_open":
        edges = [-0.1, 0, 30, 120, 600, np.inf]
        labels = ["0", "1-30", "31-120", "121-600", ">600"]
    else:
        edges = [-0.1, 0, 1, 2, 5, np.inf]
        labels = ["0", "1", "2", "3-5", ">5"]
    binned = pd.cut(numeric, edges, labels=labels)
    return binned.astype("string").fillna("(null)")


def _shap(model, frame: pd.DataFrame, columns: list[str], categories: dict) -> None:
    contrib = model.predict(_matrix(frame, columns, categories), pred_contrib=True)
    fraud = frame["is_fraud"].to_numpy(dtype=bool)
    print("SHAP standard trên test, mean theo giá trị", flush=True)
    for name in ("in_call", "call_seconds_since_open", "acct_input", "app_switches"):
        column = contrib[:, columns.index(name)]
        group = _bins(name, frame[name]).to_numpy()
        print(f"  {name}", flush=True)
        for key in pd.unique(group):
            picked = np.flatnonzero(group == key)
            mean = float(column[picked].mean()) if len(picked) else float("nan")
            fraud_mean = float(column[picked][fraud[picked]].mean()) if fraud[picked].any() else float("nan")
            legit_mean = float(column[picked][~fraud[picked]].mean()) if (~fraud[picked]).any() else float("nan")
            print(f"    {key}: n {len(picked):,}  mean {mean:+.5f}  fraud {fraud_mean:+.5f}  legit {legit_mean:+.5f}", flush=True)
        if name == "in_call":
            on = frame[name].eq(1).to_numpy()
            mean_on = float(column[on].mean()) if on.any() else float("nan")
            print(f"    in_call=1 làm {'GIẢM' if mean_on < 0 else 'TĂNG'} điểm. mean {mean_on:+.5f}", flush=True)


def _pack(bundle: dict, metrics: dict) -> dict:
    population = bundle["population"]
    legit = np.flatnonzero(bundle["legit"])
    test = np.flatnonzero(population["session_id"].isin(bundle["test_ids"]).to_numpy())
    level = metrics["_pop_level"]
    test_level = level[test]
    friend = metrics["_friend"]
    guided = metrics["_guided"]
    if len(test_level) != len(friend) or len(metrics["_saved_row"]) != len(friend):
        raise RuntimeError("lệch thứ tự test khi ghép chỉ số")
    friend_hit = test_level[friend] == "medium"
    hang_hit = metrics["_action"][guided] == "hangup_required"
    if abs(float(friend_hit.mean()) - metrics["medium_loan"]) > 1e-9 or abs(float(hang_hit.mean()) - metrics["guided_hangup"]) > 1e-9:
        raise RuntimeError("chỉ số làm phiền lệch khỏi bảng")
    return {
        "saved_row": metrics["_saved_row"],
        "raw": metrics["_raw"],
        "label": metrics["_label"],
        "groups": metrics["_groups"],
        "mh": np.isin(level[legit], ["medium", "high"]),
        "friend_hit": friend_hit,
        "hang_hit": hang_hit,
        "saved": metrics["saved"],
        "recall": metrics["recall_at_1"],
        "legit_mh": metrics["legit_mh"],
        "medium_loan": metrics["medium_loan"],
        "guided_hangup": metrics["guided_hangup"],
        "tau_hi": metrics["tau_hi"],
    }


def _subset_groups(bundle: dict, mask_name: str) -> tuple:
    population = bundle["population"]
    legit = np.flatnonzero(bundle["legit"])
    if mask_name == "legit":
        held = population.iloc[legit]
        return cluster_index(_label_groups(held["case_id"], held["customer_id"], "c"))
    test = np.flatnonzero(population["session_id"].isin(bundle["test_ids"]).to_numpy())
    held = population.iloc[test]
    kind = held["scenario_type"].astype(str)
    name = "legit_friend_loan" if mask_name == "friend" else "legit_guided_by_relative"
    picked = held.loc[kind.eq(name)]
    return cluster_index(_label_groups(picked["case_id"], picked["customer_id"], "c"))


def _paired(left: dict, right: dict, bundle: dict) -> dict:
    money = bootstrap_values(left["groups"], lambda index, a=left, b=right: float(a["saved_row"][index].sum() - b["saved_row"][index].sum()), 200, SEED)
    recall = bootstrap_values(
        left["groups"],
        lambda index, a=left, b=right: float(recall_at(a["label"][index], a["raw"][index]) - recall_at(b["label"][index], b["raw"][index])),
        200,
        SEED,
    )
    legit_groups = _subset_groups(bundle, "legit")
    friend_groups = _subset_groups(bundle, "friend")
    hang_groups = _subset_groups(bundle, "guided")
    mh = bootstrap_values(legit_groups, lambda index, a=left, b=right: float(a["mh"][index].mean() - b["mh"][index].mean()), 200, SEED)
    loan = bootstrap_values(friend_groups, lambda index, a=left, b=right: float(a["friend_hit"][index].mean() - b["friend_hit"][index].mean()), 200, SEED)
    hang = bootstrap_values(hang_groups, lambda index, a=left, b=right: float(a["hang_hit"][index].mean() - b["hang_hit"][index].mean()), 200, SEED)
    return {
        "money": (left["saved"] - right["saved"], float(np.nanpercentile(money, 2.5)), float(np.nanpercentile(money, 97.5))),
        "recall": (left["recall"] - right["recall"], float(np.nanpercentile(recall, 2.5)), float(np.nanpercentile(recall, 97.5))),
        "legit_mh": (left["legit_mh"] - right["legit_mh"], float(np.nanpercentile(mh, 2.5)), float(np.nanpercentile(mh, 97.5))),
        "medium_loan": (left["medium_loan"] - right["medium_loan"], float(np.nanpercentile(loan, 2.5)), float(np.nanpercentile(loan, 97.5))),
        "guided_hangup": (left["guided_hangup"] - right["guided_hangup"], float(np.nanpercentile(hang, 2.5)), float(np.nanpercentile(hang, 97.5))),
    }


def _fmt_pair(item: dict) -> str:
    lines = []
    labels = {
        "money": "tiền",
        "recall": "recall@1%",
        "legit_mh": "MH hợp lệ",
        "medium_loan": "Medium vay",
        "guided_hangup": "cúp máy",
    }
    for key, title in labels.items():
        point, low, high = item[key]
        if key == "money":
            lines.append(f"{title} {point:,.0f} [{low:,.0f}–{high:,.0f}]")
        elif key == "recall":
            lines.append(f"{title} {100 * point:+.2f} điểm [{100 * low:+.2f}–{100 * high:+.2f}]")
        else:
            lines.append(f"{title} {100 * point:+.2f} điểm % [{100 * low:+.2f}–{100 * high:+.2f}]")
    return "; ".join(lines)


def main() -> None:
    frames, bundle = prepare()
    scenarios = _scenarios()
    train = frames["train"]
    print(
        f"train n {len(train):,}  weight cột có: {'sample_weight' in train.columns}  "
        f"giá trị khác 1: {int((train['sample_weight'].to_numpy(dtype=float) != 1).sum()):,}",
        flush=True,
    )
    for name, columns in (("basic", BASIC), ("standard", STANDARD), ("advanced", ADVANCED)):
        _weight_report(train, scenarios, columns, name)
        model = _booster(train, columns, _categories(train, columns), SEED)
        column_sum = float(train["sample_weight"].sum())
        print(f"  lgb.train {name} nhận tổng weight {model._fides_weight_sum:,.1f} (cột {column_sum:,.1f})", flush=True)
    _distributions(frames, scenarios)
    families = session_families(SIM / "out" / "observed")
    families["session_id"] = families["session_id"].astype(str)
    _family_gap(frames, families)
    counts = sdk_counts(frames, families)
    print(f"họ SDK: mean count {counts.mean():.3f}  họ hiện tại {bundle['population']['families_n'].mean():.3f}", flush=True)

    seed_rows: dict[str, list[tuple[float, float]]] = {name: [] for name in ("basic", "standard", "advanced")}
    packed: dict[str, dict] = {}
    columns_of = {"basic": BASIC, "standard": STANDARD, "advanced": ADVANCED}
    for seed in SEEDS:
        for name, columns in columns_of.items():
            print(f"seed {seed} {name}", flush=True)
            fitted = fit_profile(frames, columns, seed)
            metrics = _metrics(bundle, frames, fitted, None)
            seed_rows[name].append((metrics["recall_at_1"], metrics["saved"]))
            print(f"  recall@1% {100 * metrics['recall_at_1']:.2f}%  tiền {metrics['saved']:,.0f}  τ_hi {metrics['tau_hi']:.4f}", flush=True)
            if seed == SEED and name == "standard":
                _shap(fitted["model"], frames["test"], columns, fitted["categories"])
            if seed == SEED:
                packed[name] = _pack(bundle, metrics)
            del fitted

    print("5 seed, mean ± sd", flush=True)
    for name, rows in seed_rows.items():
        recall = np.array([item[0] for item in rows])
        money = np.array([item[1] for item in rows])
        print(
            f"  {name}: recall@1% {100 * recall.mean():.2f}% ± {100 * recall.std(ddof=1):.2f}  "
            f"tiền {money.mean():,.0f} ± {money.std(ddof=1):,.0f}",
            flush=True,
        )

    print("biến thể SDK chỉ vào họ", flush=True)
    for name, columns in (("basic+họ SDK", BASIC), ("advanced bỏ SDK+họ SDK", ADV_NO_SDK)):
        print(f"train {name} ({len(columns)} cột)", flush=True)
        fitted = fit_profile(frames, columns, SEED)
        metrics = _metrics(bundle, frames, fitted, None, counts)
        packed[name] = _pack(bundle, metrics)
        row = packed[name]
        print(
            f"  tiền {row['saved']:,.0f} ({100 * row['saved'] / 12_539_478_000:.2f}%)  "
            f"recall@1% {100 * row['recall']:.2f}%  MH {100 * row['legit_mh']:.2f}%  "
            f"Medium vay {100 * row['medium_loan']:.2f}%  cúp máy {100 * row['guided_hangup']:.2f}%  τ_hi {row['tau_hi']:.4f}",
            flush=True,
        )
        del fitted

    print("paired bootstrap, trái trừ phải", flush=True)
    pairs = {}
    for left_name in ("basic+họ SDK", "advanced bỏ SDK+họ SDK"):
        for right_name in ("standard", "advanced"):
            item = _paired(packed[left_name], packed[right_name], bundle)
            pairs[(left_name, right_name)] = item
            print(f"  {left_name} − {right_name}: {_fmt_pair(item)}", flush=True)
    item = _paired(packed["basic"], packed["basic+họ SDK"], bundle)
    pairs[("basic", "basic+họ SDK")] = item
    print(f"  basic − basic+họ SDK: {_fmt_pair(item)}", flush=True)

    names = list(packed)
    best = max(names, key=lambda name: packed[name]["saved"])
    eligible = []
    for name in names:
        if name == best:
            eligible.append(name)
            continue
        compared = _paired(packed[name], packed[best], bundle)
        _, _, high = compared["money"]
        if high >= 0:
            eligible.append(name)
        print(f"  cửa tiền {name} − {best}: {_fmt_pair(compared)}", flush=True)
    eligible.sort(key=lambda name: (packed[name]["legit_mh"], packed[name]["medium_loan"], packed[name]["guided_hangup"]))
    chosen = eligible[0]
    print("đủ cửa tiền:", ", ".join(eligible), flush=True)
    print(
        f"chọn {chosen}: tiền {packed[chosen]['saved']:,.0f}, MH {100 * packed[chosen]['legit_mh']:.2f}%, "
        f"Medium vay {100 * packed[chosen]['medium_loan']:.2f}%, cúp máy {100 * packed[chosen]['guided_hangup']:.2f}%",
        flush=True,
    )


if __name__ == "__main__":
    main()
