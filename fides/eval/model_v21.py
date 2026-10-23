"""Ba profile demo model v2.1, biến thể advanced_ctx, và một bản lộ trình không đóng gói.

basic là sổ, sản phẩm, thay đổi tài khoản, và tra cứu tên người nhận.
standard thêm khối thiết bị của SDK.
advanced thêm T4c, cờ thiếu và simo_listed.
advanced_ctx là advanced bỏ năm cột SDK. SDK chỉ vào luật họ call và entry.
advanced+telco+PAD thêm telco và PAD (accessibility, remote, screen sharing, overlay).
Huấn luyện dùng lá nhỏ, L2, early stopping trên 20% cuối của train, và ràng buộc đơn điệu.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from eval.bootstrap import bootstrap_values, cluster_index
from eval.decision import (
    COST_ETA,
    MH_CAP,
    SEED,
    T4C,
    calibrate,
    family_counts,
    scale_for,
    with_response,
)
from eval.policy_ablation import FLAGS, attach_t4c, load_part, money_parts
from eval.split import FRAUD_TYPES, SIM, _label_groups, build_split, group_folds
from features import compute_features
from features.service import cost_tau, session_families
from features.store import DAY_NS, FeatureStore, using_store, _ns
from train.baseline import recall_at

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT.parent / "engine" / "artifacts" / "model-v2.1"
NS = 1_000_000_000

LEDGER = [
    "amount",
    "amount_to_balance",
    "a_w",
    "new_payee",
    "add_method",
    "hour",
    "seconds_since_login",
    "new_payees_24h",
    "has_tx_signals",
]
PAYEE_BOOK = ["payee_name_matches_holder", "merchant_verified", "has_payee_signals"]
PRODUCTS = ["product_savings_7d", "product_loan_7d", "product_cash_advance_7d"]
CHANGES = ["limit_raise_1d", "phone_change_7d"]
DEVICE = [
    "in_call",
    "call_seconds_since_open",
    "call_ended_in_session",
    "acct_input",
    "app_switches",
    "pasted",
    "has_device_signals",
]
TELCO = [
    "on_call",
    "recent_call_minutes_ago",
    "counterparty_is_new",
    "counterparty_number_age_days",
    "counterparty_report_count",
    "has_telco_signals",
]
PAD = ["accessibility_new", "remote_access_app", "screen_sharing", "overlay"]
SIM_DEVICE = ["in_call_audio_mode", "new_device"]
BASIC = [*LEDGER, *PAYEE_BOOK, *PRODUCTS, *CHANGES]
STANDARD = [*BASIC, *DEVICE]
ADVANCED = [*STANDARD, *T4C, *FLAGS, "simo_listed"]
SDK_ONLY = ["in_call", "call_seconds_since_open", "call_ended_in_session", "acct_input", "app_switches"]
ADVANCED_CTX = [name for name in ADVANCED if name not in SDK_ONLY]
ROADMAP = [*ADVANCED, *TELCO, *PAD, *SIM_DEVICE]
V20_COLUMNS = [
    "amount",
    "amount_to_balance",
    "a_w",
    "new_payee",
    "pasted",
    "add_method",
    "hour",
    "seconds_since_login",
    "new_payees_24h",
    "has_tx_signals",
    "payee_name_matches_holder",
    "merchant_verified",
    "simo_listed",
    "has_payee_signals",
    "in_call_audio_mode",
    "accessibility_new",
    "remote_access_app",
    "screen_sharing",
    "overlay",
    "new_device",
    "has_device_signals",
    "on_call",
    "recent_call_minutes_ago",
    "counterparty_is_new",
    "counterparty_number_age_days",
    "counterparty_report_count",
    "has_telco_signals",
]
COLUMN_NOTE = {
    "a_w": "quên. Sổ 24 giờ của v2.0, đưa lại vào basic.",
    "pasted": "quên. Cờ paste_account, không trùng acct_input. Đưa lại vào standard.",
    "add_method": "quên. Cách lưu người nhận. Đưa lại vào basic.",
    "seconds_since_login": "quên. Mốc login của phiên. Đưa lại vào basic.",
    "has_device_signals": "quên. Cờ có khối thiết bị. Đưa lại vào standard.",
    "in_call_audio_mode": "demo không có. SDK chỉ gửi in_call. Sim vẫn có, chỉ bản telco+PAD.",
    "new_device": "demo không có. SDK không gửi tuổi thiết bị. Chỉ bản telco+PAD.",
    "accessibility_new": "PAD. Chỉ bản telco+PAD.",
    "remote_access_app": "PAD. Chỉ bản telco+PAD.",
    "screen_sharing": "PAD. Chỉ bản telco+PAD.",
    "overlay": "PAD. Chỉ bản telco+PAD.",
    "payee_name_matches_holder": "tra cứu tên. Chuyển vào basic.",
    "merchant_verified": "danh sách tổ chức. Chuyển vào basic.",
    "has_payee_signals": "cờ tra cứu người nhận. Chuyển vào basic.",
    "in_call": "đổi tên gần. SDK dùng in_call; v2.0 dùng in_call_audio_mode.",
    "acct_input": "cột mới của SDK, bốn giá trị. Không thay pasted.",
}
PROFILES = {
    "basic": BASIC,
    "standard": STANDARD,
    "advanced": ADVANCED,
    "advanced_ctx": ADVANCED_CTX,
    "advanced+telco+PAD": ROADMAP,
}
COMPARED = ("basic", "standard", "advanced", "advanced_ctx")
PACKAGED = ("basic", "standard", "advanced")
CATEGORICAL = {"acct_input", "add_method"}
PRODUCT_KIND = {
    "product_savings_7d": "savings_liquidation",
    "product_loan_7d": "quick_loan_disbursement",
    "product_cash_advance_7d": "credit_card_cash_advance",
}
ACCT_INPUT = ("typed", "typed_chunked", "pasted", "chosen")
MAX_ROUNDS = 1000
EARLY_STOP = 30
MONOTONE_PLUS = {
    "in_call",
    "call_seconds_since_open",
    "pasted",
    "acct_input_pasted",
    "app_switches",
    "new_payee",
    "new_payees_24h",
    "a_w",
    "amount_to_balance",
    "product_savings_7d",
    "product_loan_7d",
    "product_cash_advance_7d",
    "limit_raise_1d",
    "phone_change_7d",
    "distinct_senders_7d",
    "passthrough_24h",
    "device_holder_count",
    "simo_listed",
}
MONOTONE_MINUS = {"merchant_verified", "payee_account_age_days"}


def _as_of(end_ts: pd.Series) -> np.ndarray:
    return _ns(pd.to_datetime(end_ts) + pd.Timedelta(microseconds=1))


def _count_window(keys: np.ndarray, as_of: np.ndarray, event_key: np.ndarray, event_ts: np.ndarray, window_ns: int) -> np.ndarray:
    grouped: dict[str, np.ndarray] = {}
    if len(event_key):
        order = np.argsort(event_key, kind="mergesort")
        sorted_key = event_key[order]
        sorted_ts = event_ts[order]
        for key, start, stop in _runs(sorted_key):
            grouped[key] = np.sort(sorted_ts[start:stop])
    out = np.zeros(len(keys), dtype=np.int32)
    for index, key in enumerate(keys):
        stamps = grouped.get(key)
        if stamps is None:
            continue
        moment = int(as_of[index])
        right = int(np.searchsorted(stamps, moment, side="left"))
        left = int(np.searchsorted(stamps, moment - window_ns, side="right"))
        out[index] = right - left
    return out


def _runs(values: np.ndarray):
    if len(values) == 0:
        return
    change = np.flatnonzero(values[1:] != values[:-1]) + 1
    bounds = np.r_[0, change, len(values)]
    for start, stop in zip(bounds[:-1], bounds[1:]):
        yield str(values[start]), int(start), int(stop)


def demo_frame(parts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Cột mà sổ demo có, tính trước as_of của từng phiên."""
    observed = SIM / "out" / "observed"
    sessions = pd.concat([part[["session_id", "end_ts"]] for part in parts.values()], ignore_index=True)
    sessions["session_id"] = sessions["session_id"].astype(str)
    accounts = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "account_id"])
    accounts["session_id"] = accounts["session_id"].astype(str)
    accounts["account_id"] = accounts["account_id"].astype(str)
    sessions = sessions.merge(accounts, on="session_id", how="left")
    sessions["as_of"] = _as_of(sessions["end_ts"])

    tx = pd.read_parquet(observed / "transactions.parquet", columns=["from_account", "ts", "tx_type"])
    tx["from_account"] = tx["from_account"].astype(str)
    tx["ts_ns"] = _ns(tx["ts"])
    kind = tx["tx_type"].astype(str)
    keys = sessions["account_id"].to_numpy()
    as_of = sessions["as_of"].to_numpy()
    frame = pd.DataFrame({"session_id": sessions["session_id"].to_numpy()})
    for column, tx_type in PRODUCT_KIND.items():
        part = tx.loc[kind.eq(tx_type)]
        frame[column] = _count_window(keys, as_of, part["from_account"].to_numpy(), part["ts_ns"].to_numpy(), 7 * DAY_NS)

    changes = pd.read_parquet(observed / "account_changes.parquet", columns=["account_id", "ts", "change_type"])
    changes["account_id"] = changes["account_id"].astype(str)
    changes["ts_ns"] = _ns(changes["ts"])
    change_kind = changes["change_type"].astype(str)
    raises = changes.loc[change_kind.eq("limit_raise")]
    phones = changes.loc[change_kind.eq("phone_change")]
    frame["limit_raise_1d"] = _count_window(keys, as_of, raises["account_id"].to_numpy(), raises["ts_ns"].to_numpy(), DAY_NS)
    frame["phone_change_7d"] = _count_window(keys, as_of, phones["account_id"].to_numpy(), phones["ts_ns"].to_numpy(), 7 * DAY_NS)

    signals = pd.read_parquet(
        observed / "device_signals.parquet",
        columns=["session_id", "in_call_at_open", "call_seconds_since_open", "call_ended_in_session"],
    )
    signals["session_id"] = signals["session_id"].astype(str)
    signals = signals.drop_duplicates("session_id")
    frame = frame.merge(signals, on="session_id", how="left")
    frame["in_call"] = frame["in_call_at_open"].map({True: 1.0, False: 0.0})
    frame["call_seconds_since_open"] = pd.to_numeric(frame["call_seconds_since_open"], errors="coerce")
    ended = frame["call_ended_in_session"].map({True: 1.0, False: 0.0})
    frame["call_ended_in_session"] = ended
    frame = frame.drop(columns=["in_call_at_open"])

    events = pd.read_parquet(observed / "session_events.parquet", columns=["session_id", "ts", "event_type"])
    events["session_id"] = events["session_id"].astype(str)
    events["ts_ns"] = _ns(events["ts"])
    event_kind = events["event_type"].astype(str)
    switches = events.loc[event_kind.eq("app_switch"), ["session_id", "ts_ns"]]
    switch_count = _count_window(
        frame["session_id"].to_numpy(),
        as_of,
        switches["session_id"].to_numpy(),
        switches["ts_ns"].to_numpy(),
        10**18,
    )
    frame["app_switches"] = switch_count
    inputs = events.loc[event_kind.str.startswith("acct_input_")].copy()
    inputs["acct_input"] = inputs["event_type"].astype(str).str.replace("acct_input_", "", regex=False)
    inputs = inputs.sort_values(["session_id", "ts_ns"], kind="mergesort").groupby("session_id", sort=False).tail(1)
    frame = frame.merge(inputs[["session_id", "acct_input"]], on="session_id", how="left")
    known = frame["acct_input"].isin(ACCT_INPUT)
    frame.loc[~known, "acct_input"] = pd.NA
    return frame


def _buffer_features(part: pd.DataFrame) -> pd.DataFrame:
    observed = SIM / "out" / "observed"
    store = FeatureStore(observed)
    contacts = SIM / "out" / "truth" / "contacts.parquet"
    if contacts.exists():
        store.attach_contacts(pd.read_parquet(contacts))
    rows = []
    total = len(part)
    with using_store(store):
        for index, record in enumerate(part.itertuples(index=False), start=1):
            if index == 1 or index % 5000 == 0 or index == total:
                print(f"  đệm {index}/{total}", flush=True)
            as_of = pd.Timestamp(record.end_ts) + pd.Timedelta(microseconds=1)
            bank = compute_features(str(record.session_id), as_of, "single_bank", "advanced")
            network = compute_features(str(record.session_id), as_of, "network", "advanced")
            for name in T4C:
                bank[name] = network[name]
            bank["session_id"] = str(record.session_id)
            rows.append(bank)
    frame = pd.DataFrame(rows)
    meta = part[["session_id", "case_id", "customer_id", "is_fraud", "label", "sample_weight", "scam_type"]].copy()
    meta["session_id"] = meta["session_id"].astype(str)
    return frame.merge(meta, on="session_id", how="inner")


def model_frames(parts: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    demo = demo_frame(parts)
    frames = {}
    for name in ("train", "test"):
        frame = load_part(name, parts[name])
        frame = attach_t4c(frame, name)
        if "label" not in frame.columns:
            frame["label"] = parts[name]["label"].to_numpy()
        frames[name] = frame
    print("đang tính đặc trưng đệm", flush=True)
    frames["buffer"] = _buffer_features(parts["buffer"])
    for name, frame in frames.items():
        for column, flag in zip(T4C, FLAGS):
            values = pd.to_numeric(frame[column], errors="coerce")
            frame[column] = values
            frame[flag] = values.isna().astype(np.int8)
        extra = demo.drop(columns=[column for column in demo.columns if column != "session_id" and column in frame.columns])
        extra = extra.drop_duplicates("session_id")
        frame = frame.merge(extra, on="session_id", how="left")
        meta = parts[name].drop_duplicates("session_id").set_index("session_id")
        sid = frame["session_id"].astype(str)
        frame["label"] = sid.map(meta["label"]).astype(int)
        frame["sample_weight"] = sid.map(meta["sample_weight"]).astype(float)
        frame["is_fraud"] = sid.map(meta["is_fraud"]).astype(bool)
        frame["start_ts"] = pd.to_datetime(sid.map(meta["start_ts"]))
        frames[name] = frame
    return frames


def _categories(frame: pd.DataFrame, columns: list[str]) -> dict[str, pd.CategoricalDtype]:
    out = {}
    for name in columns:
        if name == "acct_input":
            out[name] = pd.api.types.CategoricalDtype(list(ACCT_INPUT))
        elif name in CATEGORICAL:
            values = frame[name].astype("string").dropna().unique()
            out[name] = pd.api.types.CategoricalDtype(sorted(str(item) for item in values))
    return out


def model_columns(columns: list[str]) -> list[str]:
    """acct_input thành one-hot để acct_input_pasted mang ràng buộc +1."""
    out = []
    for name in columns:
        if name == "acct_input":
            out.extend(f"acct_input_{item}" for item in ACCT_INPUT)
        else:
            out.append(name)
    return out


def monotone_of(columns: list[str]) -> list[int]:
    """call_ended_in_session không ràng buộc: người nhà hướng dẫn có tỷ lệ cúp máy cao hơn nhãn dương."""
    out = []
    for name in columns:
        if name in MONOTONE_PLUS:
            out.append(1)
        elif name in MONOTONE_MINUS:
            out.append(-1)
        else:
            out.append(0)
    return out


def _matrix(frame: pd.DataFrame, columns: list[str], categories: dict[str, pd.CategoricalDtype]) -> pd.DataFrame:
    data = {}
    for name in columns:
        if name.startswith("acct_input_"):
            level = name.removeprefix("acct_input_")
            present = frame["acct_input"].notna()
            data[name] = np.where(present, frame["acct_input"].astype("string").eq(level).astype(float), np.nan)
        elif name == "call_seconds_since_open":
            # Null nghĩa là không trong cuộc gọi. Đưa về 0 để ràng buộc +1 không nhảy ở ô thiếu.
            data[name] = pd.to_numeric(frame[name], errors="coerce").fillna(0.0)
        elif name in categories:
            data[name] = frame[name].astype("string").astype(categories[name])
        else:
            data[name] = pd.to_numeric(frame[name], errors="coerce")
    return pd.DataFrame(data, index=frame.index)


def _time_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """20% cuối theo start_ts làm validation. Không đụng test."""
    times = pd.to_datetime(frame["start_ts"])
    cut = times.quantile(0.8)
    fit = frame.loc[times < cut]
    valid = frame.loc[times >= cut]
    if fit.empty or valid.empty:
        raise RuntimeError("cắt thời gian không còn phiên fit hoặc validation")
    return fit, valid


def _params(columns: list[str], seed: int) -> dict:
    return {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 50,
        "lambda_l2": 10.0,
        "feature_fraction": 0.8,
        "monotone_constraints": monotone_of(columns),
        "verbosity": -1,
        "seed": int(seed),
    }


def _dataset(frame: pd.DataFrame, columns: list[str], categories: dict[str, pd.CategoricalDtype], reference=None):
    import lightgbm as lgb

    categorical = [name for name in columns if name in categories]
    weight = frame["sample_weight"].to_numpy(dtype=float)
    dataset = lgb.Dataset(
        _matrix(frame, columns, categories),
        label=frame["label"].to_numpy(),
        weight=weight,
        categorical_feature=categorical or "auto",
        reference=reference,
        free_raw_data=False,
    )
    return dataset, weight


def _booster(train: pd.DataFrame, columns: list[str], categories: dict[str, pd.CategoricalDtype], seed: int = SEED):
    import lightgbm as lgb

    fit, valid = _time_split(train)
    train_set, weight = _dataset(fit, columns, categories)
    valid_set, _ = _dataset(valid, columns, categories, reference=train_set)
    stopped = lgb.train(
        _params(columns, seed),
        train_set,
        num_boost_round=MAX_ROUNDS,
        valid_sets=[valid_set],
        valid_names=["val"],
        callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False), lgb.log_evaluation(period=0)],
    )
    rounds = max(int(stopped.best_iteration), 1)
    full_set, full_weight = _dataset(train, columns, categories)
    model = lgb.train(
        _params(columns, seed),
        full_set,
        num_boost_round=rounds,
        callbacks=[lgb.log_evaluation(period=0)],
    )
    model._fides_weight_sum = float(full_weight.sum())
    model._fides_rounds = rounds
    model._fides_val_positive = int(valid["label"].sum())
    model._fides_val_rows = int(len(valid))
    return model


def _out_of_fold(train: pd.DataFrame, columns: list[str], seed: int = SEED) -> np.ndarray:
    raw = np.zeros(len(train), dtype=float)
    groups = _label_groups(train["case_id"], train["session_id"], "c")
    for train_mask, test_mask in group_folds(groups, n_splits=5, seed=seed):
        held = train.iloc[np.flatnonzero(train_mask)]
        categories = _categories(held, columns)
        model = _booster(held, columns, categories, seed)
        scored = train.iloc[np.flatnonzero(test_mask)]
        raw[test_mask] = model.predict(_matrix(scored, columns, categories))
    return raw


def _context(frames: dict[str, pd.DataFrame]) -> dict:
    observed = SIM / "out" / "observed"
    truth = SIM / "out" / "truth"
    families = session_families(observed)
    families["session_id"] = families["session_id"].astype(str)
    customers = pd.read_parquet(observed / "customers.parquet", columns=["customer_id", "segment", "has_guardian"])
    customers["customer_id"] = customers["customer_id"].astype(str)
    scenarios = pd.read_parquet(truth / "truth_scenarios.parquet", columns=["session_id", "scenario_type", "hangs_up_before_transfer"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    latent = pd.read_parquet(truth / "customers_latent.parquet", columns=["customer_id", "lives_alone"])
    latent["customer_id"] = latent["customer_id"].astype(str)
    indexed = families.set_index("session_id")
    pieces = []
    for frame in frames.values():
        work = frame[["session_id", "case_id", "customer_id", "amount", "is_fraud", "scam_type"]].copy()
        sid = work["session_id"].astype(str)
        work["call"] = sid.map(indexed["call"]).fillna(False).astype(bool)
        work["entry"] = sid.map(indexed["entry"]).fillna(False).astype(bool)
        work["payee"] = sid.map(indexed["payee"]).fillna(False).astype(bool)
        work["funding"] = sid.map(indexed["funding"]).fillna(False).astype(bool)
        work["families_n"] = family_counts(frame, families, None)
        work["distinct_senders_7d"] = pd.to_numeric(frame["distinct_senders_7d"], errors="coerce").to_numpy()
        work["passthrough_24h"] = pd.to_numeric(frame["passthrough_24h"], errors="coerce").to_numpy()
        work["payee_account_age_days"] = pd.to_numeric(frame["payee_account_age_days"], errors="coerce").to_numpy()
        if "simo_listed" in frame.columns:
            work["simo_listed"] = pd.to_numeric(frame["simo_listed"], errors="coerce").fillna(0).to_numpy()
        else:
            work["simo_listed"] = 0
        pieces.append(work)
    population = pd.concat(pieces, ignore_index=True)
    population["customer_id"] = population["customer_id"].astype(str)
    population["scenario_type"] = population["session_id"].map(dict(zip(scenarios["session_id"], scenarios["scenario_type"].astype(str))))
    population = population.merge(customers, on="customer_id", how="left")
    population["has_guardian"] = population["has_guardian"].fillna(False).astype(bool)
    population["amount"] = pd.to_numeric(population["amount"], errors="coerce")
    return {
        "population": population,
        "test_ids": set(frames["test"]["session_id"].astype(str)),
        "legit": ~population["is_fraud"].to_numpy(dtype=bool),
        "hang_of": dict(zip(scenarios["session_id"], scenarios["hangs_up_before_transfer"].fillna(False).astype(bool))),
        "alone_of": dict(zip(latent["customer_id"], latent["lives_alone"].fillna(False).astype(bool))),
        "response": pd.read_parquet(truth / "response_model.parquet"),
        "fp_table": {"doanh nghiệp nhỏ": 3.0},
        "train_ids": set(frames["train"]["session_id"].astype(str)),
    }


def _quantile(values: pd.Series, q: float) -> float:
    observed = pd.to_numeric(values, errors="coerce").dropna()
    if observed.empty:
        return float("inf")
    return float(np.quantile(observed.to_numpy(dtype=float), q))


def _p99(values: pd.Series) -> float:
    return _quantile(values, 0.99)


def _eligible(population: pd.DataFrame, legit_train: np.ndarray) -> tuple[np.ndarray, float, float]:
    """τ_hi chỉ nâng khi có họ ngoài payee, hoặc T4c vượt p99 hợp lệ train."""
    held = population.iloc[np.flatnonzero(legit_train)]
    senders_cut = _p99(held["distinct_senders_7d"])
    passthrough_cut = _p99(held["passthrough_24h"])
    senders = pd.to_numeric(population["distinct_senders_7d"], errors="coerce").to_numpy(dtype=float)
    passed = pd.to_numeric(population["passthrough_24h"], errors="coerce").to_numpy(dtype=float)
    t4c = (np.nan_to_num(senders, nan=-np.inf) > senders_cut) | (np.nan_to_num(passed, nan=-np.inf) > passthrough_cut)
    other = (
        population["call"].to_numpy(dtype=bool)
        | population["entry"].to_numpy(dtype=bool)
        | population["funding"].to_numpy(dtype=bool)
    )
    return other | t4c, senders_cut, passthrough_cut


def _payee_normal(age: np.ndarray, senders: np.ndarray, passed: np.ndarray, senders_cut: float, passthrough_cut: float, payee_normal: str) -> np.ndarray:
    """Bình thường theo T4c. Thiếu tuổi hoặc senders thì chưa biết, họ vẫn bật.

    strict: cần đủ tuổi, senders và passthrough. Thiếu passthrough cũng giữ họ.
    other_bank: passthrough không quan sát được thì chỉ cần tuổi ≥ 365 và senders ≤ p90.
    Passthrough quan sát được vẫn phải ≤ p90.
    """
    age_ok = np.isfinite(age) & (age >= 365.0)
    senders_ok = np.isfinite(senders) & (senders <= senders_cut)
    pass_ok = np.isfinite(passed) & (passed <= passthrough_cut)
    if payee_normal == "other_bank":
        return age_ok & senders_ok & (~np.isfinite(passed) | pass_ok)
    if payee_normal != "strict":
        raise RuntimeError(f"payee_normal không nhận: {payee_normal}")
    return age_ok & senders_ok & pass_ok


def _family_counts(
    population: pd.DataFrame,
    legit_train: np.ndarray,
    payee_gate: bool,
    payee_normal: str = "strict",
) -> tuple[np.ndarray, float, float]:
    """Họ payee tắt khi người nhận mới nhưng tài khoản nhận bình thường theo T4c."""
    call = population["call"].to_numpy(dtype=bool).astype(np.int8)
    entry = population["entry"].to_numpy(dtype=bool).astype(np.int8)
    funding = population["funding"].to_numpy(dtype=bool).astype(np.int8)
    payee = population["payee"].to_numpy(dtype=bool)
    if not payee_gate:
        return call + entry + payee.astype(np.int8) + funding, float("nan"), float("nan")
    held = population.iloc[np.flatnonzero(legit_train)]
    senders_cut = _quantile(held["distinct_senders_7d"], 0.90)
    passthrough_cut = _quantile(held["passthrough_24h"], 0.90)
    age = pd.to_numeric(population["payee_account_age_days"], errors="coerce").to_numpy(dtype=float)
    senders = pd.to_numeric(population["distinct_senders_7d"], errors="coerce").to_numpy(dtype=float)
    passed = pd.to_numeric(population["passthrough_24h"], errors="coerce").to_numpy(dtype=float)
    normal = _payee_normal(age, senders, passed, senders_cut, passthrough_cut, payee_normal)
    gated = payee & ~normal
    return call + entry + gated.astype(np.int8) + funding, senders_cut, passthrough_cut


def _assign_levels(
    probability: np.ndarray,
    amount: np.ndarray,
    families: np.ndarray,
    scale: np.ndarray,
    tau_hi: float | None,
    eligible: np.ndarray,
) -> np.ndarray:
    fired = probability >= cost_tau(amount, COST_ETA, scale)
    count = np.asarray(families, dtype=np.int8)
    level = np.full(len(probability), "none", dtype=object)
    level[fired] = "banner"
    level[fired & (count == 2)] = "medium"
    level[fired & (count >= 3)] = "high"
    if tau_hi is not None:
        promote = (probability >= float(tau_hi)) & np.asarray(eligible, dtype=bool) & (count <= 1) & np.isin(level, ["none", "banner"])
        level[promote] = "medium"
    return level


def _choose_tau(
    probability: np.ndarray,
    amount: np.ndarray,
    families: np.ndarray,
    scale: np.ndarray,
    legit: np.ndarray,
    eligible: np.ndarray,
    friend_test: np.ndarray,
) -> float:
    """τ_hi thấp nhất còn giữ MH hợp lệ ≤ 0,45% và Medium vay bạn trên test ≤ 10%."""
    base = _assign_levels(probability, amount, families, scale, None, eligible)
    legit = np.asarray(legit, dtype=bool)
    friend = np.asarray(friend_test, dtype=bool)
    room_mh = int(np.floor(MH_CAP * int(legit.sum()) - int((legit & np.isin(base, ["medium", "high"])).sum())))
    room_friend = int(np.floor(0.10 * int(friend.sum()) - int((friend & (base == "medium")).sum())))
    promotable = legit & np.asarray(eligible, dtype=bool) & (np.asarray(families) <= 1) & np.isin(base, ["none", "banner"])
    if room_mh <= 0 or not promotable.any():
        return 1.0
    order = np.flatnonzero(promotable)[np.argsort(-probability[promotable], kind="mergesort")]
    scores = probability[order]
    tau = 1.0
    used_mh = 0
    used_friend = 0
    start = 0
    while start < len(order):
        value = float(scores[start])
        end = start + 1
        while end < len(scores) and float(scores[end]) == value:
            end += 1
        block = order[start:end]
        add_friend = int(friend[block].sum())
        if used_mh + len(block) > room_mh or used_friend + add_friend > max(room_friend, 0):
            break
        used_mh += len(block)
        used_friend += add_friend
        tau = value
        start = end
    return tau


def choose_tau_least_bother(
    probability: np.ndarray,
    amount: np.ndarray,
    families: np.ndarray,
    scale: np.ndarray,
    legit: np.ndarray,
    eligible: np.ndarray,
    money_of,
    scenario_of,
) -> tuple[float, float]:
    """τ_hi lớn nhất dưới trần MH 0,45% mà tiền còn ≥ 99,5% mức tối đa và ≥Medium từng scenario giảm ≤ 1 điểm."""
    base = _assign_levels(probability, amount, families, scale, None, eligible)
    legit = np.asarray(legit, dtype=bool)
    can = np.asarray(eligible, dtype=bool) & (np.asarray(families) <= 1) & np.isin(base, ["none", "banner"])
    candidates = np.unique(np.asarray(probability, dtype=float)[can])
    candidates = np.concatenate([candidates, np.array([1.0])])
    feasible: list[tuple[float, float, dict[str, float]]] = []
    for tau in candidates:
        level = _assign_levels(probability, amount, families, scale, float(tau), eligible)
        mh = float(np.isin(level[legit], ["medium", "high"]).mean()) if legit.any() else 0.0
        if mh > MH_CAP:
            continue
        feasible.append((float(tau), float(money_of(level)), scenario_of(level)))
    if not feasible:
        return 1.0, 0.0
    reference = max(feasible, key=lambda item: (item[1], item[0]))
    floor = 0.995 * reference[1]
    allowed = []
    for tau, saved, rates in feasible:
        if saved < floor:
            continue
        if any(rates[name] < reference[2][name] - 0.01 for name in reference[2]):
            continue
        allowed.append((tau, saved))
    if not allowed:
        return reference[0], reference[1]
    chosen = max(allowed, key=lambda item: item[0])
    return chosen[0], reference[1]


def _metrics(
    bundle: dict,
    frames: dict[str, pd.DataFrame],
    scores: dict,
    tau_hi: float | None,
    families_n: np.ndarray | None = None,
    payee_gate: bool = False,
    payee_normal: str = "strict",
) -> dict:
    population = bundle["population"]
    probability = population["session_id"].map(scores["map"]).to_numpy(dtype=float)
    amount = population["amount"].to_numpy(dtype=float)
    scale = scale_for(population["segment"], bundle["fp_table"])
    legit_train = population["session_id"].isin(bundle["train_ids"]).to_numpy() & ~population["is_fraud"].to_numpy(dtype=bool)
    gated_counts, senders_p90, passthrough_p90 = _family_counts(population, legit_train, payee_gate, payee_normal)
    families = gated_counts if families_n is None else np.asarray(families_n, dtype=int)
    eligible, senders_cut, passthrough_cut = _eligible(population, legit_train)
    on_test = population["session_id"].isin(bundle["test_ids"]).to_numpy()
    friend_test = on_test & population["scenario_type"].astype(str).eq("legit_friend_loan").to_numpy()
    if tau_hi is None:
        tau_hi = _choose_tau(probability, amount, families, scale, bundle["legit"], eligible, friend_test)
    pop_level = _assign_levels(probability, amount, families, scale, tau_hi, eligible)
    listed = pd.to_numeric(population["simo_listed"], errors="coerce").fillna(0).to_numpy() >= 1
    banner_added = (pop_level == "none") & listed
    pop_level = pop_level.copy()
    pop_level[banner_added] = "banner"
    level = pop_level
    scored = population.copy()
    scored["families_n"] = families
    scored["level"] = level
    test = scored.loc[scored["session_id"].isin(bundle["test_ids"])].reset_index(drop=True)
    answered = with_response(test, bundle["hang_of"], bundle["alone_of"], bundle["response"])
    saved, damage, rate, saved_row, damage_row = money_parts(answered)
    groups = cluster_index(_label_groups(answered["case_id"], answered["customer_id"], "c"))
    saved_ci = bootstrap_values(groups, lambda index: float(saved_row[index].sum()), 200, SEED)
    legit = scored.loc[bundle["legit"], "level"]
    mh = float(legit.isin(["medium", "high"]).mean()) if len(legit) else float("nan")
    friend = answered["scenario_type"].astype(str).eq("legit_friend_loan").to_numpy()
    guided = answered["scenario_type"].astype(str).eq("legit_guided_by_relative").to_numpy()
    fraud = answered["is_fraud"].to_numpy(dtype=bool)
    scenario = answered["scam_type"].astype(str).to_numpy()
    caught = answered["level"].isin(["banner", "medium", "high"]).to_numpy()
    by_scenario = {}
    money_by_scenario = {}
    level_by_scenario = {}
    for name in FRAUD_TYPES:
        mask = fraud & (scenario == name)
        by_scenario[name] = float(caught[mask].mean()) if mask.any() else float("nan")
        money_by_scenario[name] = float(saved_row[mask].sum()) if mask.any() else 0.0
        level_by_scenario[name] = {key: float((answered["level"].to_numpy()[mask] == key).mean()) if mask.any() else float("nan") for key in ("none", "banner", "medium", "high")}
    level = answered["level"].to_numpy()
    count = answered["families_n"].to_numpy(dtype=int)
    ge_medium = {}
    for name in ("police", "investment", "own_name_safe_account", "relative"):
        mask = fraud & (scenario == name)
        ge_medium[name] = float(np.isin(level[mask], ["medium", "high"]).mean()) if mask.any() else float("nan")
    promoted = (level == "medium") & (count <= 1)
    family_rule = np.isin(level, ["medium", "high"]) & (count >= 2)
    banner_only = level == "banner"
    money_by_level = {
        "medium": float(saved_row[level == "medium"].sum()),
        "high": float(saved_row[level == "high"].sum()),
    }
    ceiling_frame = test.copy()
    ceiling_level = ceiling_frame["level"].to_numpy().copy()
    ceiling_level[fraud & ~np.isin(ceiling_level, ["medium", "high"])] = "medium"
    ceiling_frame["level"] = ceiling_level
    ceiling_answered = with_response(ceiling_frame, bundle["hang_of"], bundle["alone_of"], bundle["response"])
    ceiling, _, _, _, _ = money_parts(ceiling_answered)
    label = frames["test"]["is_fraud"].to_numpy(dtype=float)
    order = frames["test"]["session_id"].astype(str)
    raw = order.map(dict(zip(frames["test"]["session_id"].astype(str), scores["raw_test"]))).to_numpy(dtype=float)
    return {
        "tau_hi": float(tau_hi),
        "saved": float(saved),
        "saved_low": float(np.nanpercentile(saved_ci, 2.5)),
        "saved_high": float(np.nanpercentile(saved_ci, 97.5)),
        "damage": float(damage),
        "rate": float(rate),
        "recall_at_1": float(recall_at(label, raw)),
        "legit_mh": mh,
        "banner_added": int(banner_added[on_test].sum()),
        "medium_loan": float(np.mean(level[friend] == "medium")) if friend.any() else float("nan"),
        "medium_guided": float(np.mean(level[guided] == "medium")) if guided.any() else float("nan"),
        "guided_hangup": float(np.mean(answered["action"].to_numpy()[guided] == "hangup_required")) if guided.any() else float("nan"),
        "scenario_recall": by_scenario,
        "scenario_levels": level_by_scenario,
        "money_by_level": money_by_level,
        "money_by_scenario": money_by_scenario,
        "ceiling": float(ceiling),
        "saved_family": float(saved_row[family_rule].sum()),
        "saved_tau_hi": float(saved_row[promoted].sum()),
        "banner_money": float((pd.to_numeric(answered["amount"], errors="coerce").fillna(0).to_numpy(dtype=float) * pd.to_numeric(answered["p_stop"], errors="coerce").fillna(0).to_numpy(dtype=float))[fraud & banner_only].sum()),
        "fraud_levels": {key: float((level[fraud] == key).mean()) if fraud.any() else float("nan") for key in ("none", "banner", "medium", "high")},
        "senders_p99": float(senders_cut),
        "passthrough_p99": float(passthrough_cut),
        "senders_p90": float(senders_p90),
        "passthrough_p90": float(passthrough_p90),
        "payee_gate": bool(payee_gate),
        "payee_normal": payee_normal if payee_gate else "off",
        "medium_police": ge_medium["police"],
        "medium_investment": ge_medium["investment"],
        "medium_own_name": ge_medium["own_name_safe_account"],
        "medium_relative": ge_medium["relative"],
        "_saved_row": saved_row,
        "_raw": raw,
        "_label": label,
        "_groups": groups,
        "_pop_level": pop_level,
        "_friend": friend,
        "_guided": guided,
        "_action": answered["action"].astype(str).to_numpy(),
    }


def fit_profile(frames: dict[str, pd.DataFrame], columns: list[str], seed: int = SEED) -> dict:
    columns = model_columns(columns)
    categories = _categories(frames["train"], columns)
    raw_oof = _out_of_fold(frames["train"], columns, seed)
    _, apply = calibrate(raw_oof, frames["train"]["is_fraud"].to_numpy(dtype=float))
    model = _booster(frames["train"], columns, categories, seed)
    raw_test = model.predict(_matrix(frames["test"], columns, categories))
    raw_buffer = model.predict(_matrix(frames["buffer"], columns, categories))
    mapping = dict(zip(frames["train"]["session_id"].astype(str), apply(raw_oof)))
    mapping.update(zip(frames["test"]["session_id"].astype(str), apply(raw_test)))
    mapping.update(zip(frames["buffer"]["session_id"].astype(str), apply(raw_buffer)))
    return {
        "map": mapping,
        "raw_test": raw_test,
        "raw_oof": raw_oof,
        "model": model,
        "categories": categories,
        "apply": apply,
        "columns": columns,
        "rounds": int(model._fides_rounds),
    }


def _policy_payload(columns: list[str], categories: dict, apply, tau_hi: float, seed: int, rounds: int, payee_gate: bool, payee_normal: str = "strict") -> dict:
    return {
        "model_version": "model-v2.1",
        "seed": int(seed),
        "num_boost_round": int(rounds),
        "payee_gate": bool(payee_gate),
        "payee_normal": payee_normal if payee_gate else "off",
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 50,
        "lambda_l2": 10.0,
        "feature_fraction": 0.8,
        "early_stopping_rounds": EARLY_STOP,
        "validation": "last 20% of train by start_ts",
        "monotone_constraints": {name: value for name, value in zip(columns, monotone_of(columns))},
        "columns": columns,
        "categorical": {name: [str(item) for item in dtype.categories] for name, dtype in categories.items()},
        "tau_hi": tau_hi,
        "eta": COST_ETA,
        "c_fp_fixed": 50_000.0,
        "c_fp_rate": 0.002,
        "c_fp_scale": {"doanh nghiệp nhỏ": 3.0},
        "calibrator": {
            "edges": [float(item) for item in apply.edges],
            "values": [float(item) for item in apply.values],
            "source": float(apply.source),
            "target": float(apply.target),
        },
    }


def _write_hashes(directory: Path) -> None:
    lines = []
    for path in sorted(directory.iterdir()):
        if path.name == "SHA256SUMS" or not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_profile(name: str, columns: list[str], fitted: dict, metrics: dict, seed: int) -> None:
    directory = ARTIFACTS / name
    directory.mkdir(parents=True, exist_ok=True)
    fitted["model"].save_model(str(directory / "model.txt"))
    policy = _policy_payload(
        columns,
        fitted["categories"],
        fitted["apply"],
        metrics["tau_hi"],
        seed,
        fitted["rounds"],
        metrics.get("payee_gate", False),
        metrics.get("payee_normal", "strict"),
    )
    (directory / "policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    features = {"profile": name, "columns": columns, "categorical": list(policy["categorical"])}
    (directory / "features.json").write_text(json.dumps(features, ensure_ascii=False, indent=2), encoding="utf-8")
    np.save(directory / "oof.npy", np.asarray(fitted["raw_oof"], dtype=np.float64))
    _write_hashes(directory)


def load_profile(name: str):
    import lightgbm as lgb

    directory = ARTIFACTS / name
    _check_hashes(directory)
    policy = json.loads((directory / "policy.json").read_text(encoding="utf-8"))
    features = json.loads((directory / "features.json").read_text(encoding="utf-8"))
    if features["columns"] != policy["columns"]:
        raise RuntimeError(f"{name}: features.json lệch policy.json")
    model = lgb.Booster(model_file=str(directory / "model.txt"))
    if model.feature_name() != policy["columns"]:
        raise RuntimeError(f"{name}: thứ tự cột của booster lệch features.json")
    categories = {key: pd.api.types.CategoricalDtype(value) for key, value in policy["categorical"].items()}
    return model, policy, categories


def _check_hashes(directory: Path) -> None:
    lines = (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for line in lines:
        digest, name = line.split("  ", 1)
        actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if actual != digest:
            raise RuntimeError(f"sha256 lệch {name}")


def score_saved(name: str, frames: dict[str, pd.DataFrame], bundle: dict) -> dict:
    from eval.decision import isotonic_apply, shift_rate

    model, policy, categories = load_profile(name)
    columns = policy["columns"]
    raw_test = model.predict(_matrix(frames["test"], columns, categories))
    raw_buffer = model.predict(_matrix(frames["buffer"], columns, categories))
    calibrator = policy["calibrator"]

    def apply(raw: np.ndarray) -> np.ndarray:
        fitted = isotonic_apply(raw, np.asarray(calibrator["edges"]), np.asarray(calibrator["values"]))
        return shift_rate(fitted, calibrator["source"], calibrator["target"])

    raw_oof = np.load(ARTIFACTS / name / "oof.npy")
    if len(raw_oof) != len(frames["train"]):
        raise RuntimeError(f"{name}: oof.npy không khớp số phiên train")
    mapping = dict(zip(frames["train"]["session_id"].astype(str), apply(raw_oof)))
    mapping.update(zip(frames["test"]["session_id"].astype(str), apply(raw_test)))
    mapping.update(zip(frames["buffer"]["session_id"].astype(str), apply(raw_buffer)))
    return _metrics(
        bundle,
        frames,
        {"map": mapping, "raw_test": raw_test},
        policy["tau_hi"],
        payee_gate=bool(policy.get("payee_gate", False)),
        payee_normal=str(policy.get("payee_normal", "strict")),
    )


def prepare() -> tuple[dict[str, pd.DataFrame], dict]:
    parts = build_split()
    for name in ("train", "test", "buffer"):
        parts[name]["session_id"] = parts[name]["session_id"].astype(str)
    frames = model_frames(parts)
    return frames, _context(frames)


def column_map() -> list[dict]:
    """Mọi cột v2.0 và mọi cột v2.1, kèm lý do nếu v2.0 không còn."""
    rows = []
    seen = set()
    for name in V20_COLUMNS:
        home = [profile for profile, columns in PROFILES.items() if name in columns]
        rows.append(
            {
                "column": name,
                "v20": True,
                "profiles": home,
                "note": COLUMN_NOTE.get(name, "giữ từ v2.0"),
            }
        )
        seen.add(name)
    for profile, columns in PROFILES.items():
        for name in columns:
            if name in seen:
                continue
            rows.append({"column": name, "v20": False, "profiles": [item for item, cols in PROFILES.items() if name in cols], "note": COLUMN_NOTE.get(name, "cột mới của demo")})
            seen.add(name)
    missing = [name for name in V20_COLUMNS if name not in ROADMAP]
    if missing:
        raise RuntimeError(f"cột v2.0 chưa được đưa lại: {missing}")
    return rows


def _paired(rows: list[dict]) -> list[dict]:
    out = []
    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            a = rows[left]
            b = rows[right]
            groups = a["_groups"]
            money = bootstrap_values(groups, lambda index, a=a, b=b: float(a["_saved_row"][index].sum() - b["_saved_row"][index].sum()), 200, SEED)
            recall = bootstrap_values(
                groups,
                lambda index, a=a, b=b: float(recall_at(a["_label"][index], a["_raw"][index]) - recall_at(b["_label"][index], b["_raw"][index])),
                200,
                SEED,
            )
            out.append(
                {
                    "left": a["profile"],
                    "right": b["profile"],
                    "money": float(a["saved"] - b["saved"]),
                    "money_low": float(np.nanpercentile(money, 2.5)),
                    "money_high": float(np.nanpercentile(money, 97.5)),
                    "recall_at_1": float(a["recall_at_1"] - b["recall_at_1"]),
                    "recall_low": float(np.nanpercentile(recall, 2.5)),
                    "recall_high": float(np.nanpercentile(recall, 97.5)),
                }
            )
    return out


def _public(row: dict) -> dict:
    return {key: value for key, value in row.items() if not key.startswith("_")}


TRAIN_SEEDS = (42, 43, 44, 45, 46)
SCORE_KEYS = (
    "saved",
    "recall_at_1",
    "legit_mh",
    "medium_loan",
    "medium_guided",
    "guided_hangup",
    "medium_police",
    "medium_investment",
    "medium_own_name",
    "medium_relative",
)
SDK_SHAP = (
    "in_call",
    "call_seconds_since_open",
    "call_ended_in_session",
    "pasted",
    "acct_input_pasted",
    "acct_input_typed",
    "acct_input_typed_chunked",
    "acct_input_chosen",
    "app_switches",
)


def label_report(train: pd.DataFrame) -> None:
    scenarios = pd.read_parquet(SIM / "out" / "truth" / "truth_scenarios.parquet", columns=["session_id", "scenario_type"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    kind = train["session_id"].astype(str).map(dict(zip(scenarios["session_id"], scenarios["scenario_type"].astype(str)))).fillna("(không)")
    label = train["label"].to_numpy()
    fraud = train["is_fraud"].to_numpy(dtype=bool)
    groups = {
        "nhãn dương": label == 1,
        "lừa chưa nhãn": fraud & (label == 0),
        "hợp lệ": ~fraud & (label == 0),
    }
    in_call = train["in_call"].eq(1).to_numpy()
    pasted = pd.to_numeric(train["pasted"], errors="coerce").fillna(0).eq(1).to_numpy()
    acct_pasted = train["acct_input"].astype("string").eq("pasted").to_numpy()
    ended = train["call_ended_in_session"].eq(1).to_numpy()
    switches = pd.to_numeric(train["app_switches"], errors="coerce").fillna(0).ge(2).to_numpy()
    false_positive = int(((label == 1) & ~fraud).sum())
    print(
        f"nhãn train: dương {int((label == 1).sum())} (trong đó {false_positive} không phải lừa), "
        f"lừa chưa nhãn {int((fraud & (label == 0)).sum())}, hợp lệ {int((~fraud & (label == 0)).sum())}",
        flush=True,
    )
    print("call_ended_in_session không ràng buộc: người nhà hướng dẫn cúp máy nhiều hơn nhãn dương.", flush=True)
    for name, mask in groups.items():
        print(f"  {name} n={int(mask.sum())}", flush=True)
        labels = ["(tất cả)", *sorted(set(kind.to_numpy()[mask]))]
        for scenario in labels:
            picked = mask if scenario == "(tất cả)" else mask & kind.eq(scenario).to_numpy()
            count = int(picked.sum())
            if count == 0:
                continue
            def rate(values: np.ndarray) -> float:
                return 100 * float(values[picked].mean())
            print(
                f"    {scenario:<28} {count:6d}  in_call {rate(in_call):5.1f}%  "
                f"pasted {rate(pasted):5.1f}%  acct_pasted {rate(acct_pasted):5.1f}%  "
                f"ended {rate(ended):5.1f}%  sw>=2 {rate(switches):5.1f}%",
                flush=True,
            )


def _shap_bins(name: str, values: pd.Series) -> np.ndarray:
    if name in {"call_seconds_since_open"}:
        edges = [-0.1, 0, 30, 120, 600, np.inf]
        labels = ["0", "1-30", "31-120", "121-600", ">600"]
        return pd.cut(pd.to_numeric(values), edges, labels=labels).astype(str).to_numpy()
    if name == "app_switches":
        edges = [-0.1, 0, 1, 2, 5, np.inf]
        labels = ["0", "1", "2", "3-5", ">5"]
        return pd.cut(pd.to_numeric(values), edges, labels=labels).astype(str).to_numpy()
    numeric = pd.to_numeric(values, errors="coerce")
    return np.where(numeric.isna(), "(null)", np.where(numeric.eq(1), "1", "0"))


def shap_report(model, frame: pd.DataFrame, columns: list[str], categories: dict) -> bool:
    matrix = _matrix(frame, columns, categories)
    contrib = model.predict(matrix, pred_contrib=True)
    ok = True
    print("SHAP SDK trên test", flush=True)
    grouped: dict[str, dict[str, float]] = {}
    for name in SDK_SHAP:
        if name not in columns:
            continue
        column = contrib[:, columns.index(name)]
        peak = float(np.max(np.abs(column)))
        print(f"  {name} max|SHAP| {peak:.3f}", flush=True)
        if peak > 15:
            ok = False
        bins = _shap_bins(name, matrix[name])
        means = {}
        for key in pd.unique(bins):
            picked = np.flatnonzero(bins == key)
            means[str(key)] = float(column[picked].mean()) if len(picked) else float("nan")
            print(f"    {key}: n {len(picked):,}  mean {means[str(key)]:+.3f}", flush=True)
        grouped[name] = means
    checks = (
        ("in_call", "1", "0"),
        ("pasted", "1", "0"),
        ("acct_input_pasted", "1", "0"),
    )
    for name, high, low in checks:
        if name in grouped and grouped[name].get(high, 0) < grouped[name].get(low, 0):
            print(f"  LỖI chiều {name}: {high} thấp hơn {low}", flush=True)
            ok = False
    orders = {
        "call_seconds_since_open": ("0", "1-30", "31-120", "121-600", ">600"),
        "app_switches": ("0", "1", "2", "3-5", ">5"),
    }
    for name, order in orders.items():
        means = grouped.get(name, {})
        present = [key for key in order if key in means]
        if len(present) >= 2 and means[present[-1]] < means[present[0]]:
            print(f"  LỖI chiều {name}: {present[-1]} thấp hơn {present[0]}", flush=True)
            ok = False
    return ok


def _mean_sd(rows: list[dict]) -> dict[str, tuple[float, float]]:
    return {key: (float(np.mean([row[key] for row in rows])), float(np.std([row[key] for row in rows], ddof=1))) for key in SCORE_KEYS}


def _closest(rows: list[dict]) -> dict:
    center = _mean_sd(rows)
    def distance(row: dict) -> float:
        total = 0.0
        for key, (mean, sd) in center.items():
            total += abs(row[key] - mean) / (sd if sd > 1e-9 else 1.0)
        return total
    return min(rows, key=distance)


def _choose_profile(by_profile: dict[str, list[dict]]) -> tuple[str, list[str]]:
    """Tiền ≥ 99% bản tốt nhất, rồi Medium người nhà + vay bạn, rồi recall@1%, rồi sd tiền."""
    stats = {name: _mean_sd(rows) for name, rows in by_profile.items()}
    best_money = max(item["saved"][0] for item in stats.values())
    pool = [name for name, item in stats.items() if item["saved"][0] >= 0.99 * best_money]
    pool.sort(
        key=lambda name: (
            stats[name]["medium_guided"][0] + stats[name]["medium_loan"][0],
            -stats[name]["recall_at_1"][0],
            stats[name]["saved"][1],
        )
    )
    return pool[0], pool


def _variant(name: str, payee_gate: bool) -> str:
    return f"{name}_payee" if payee_gate else name


def run() -> list[dict]:
    frames, bundle = prepare()
    label_report(frames["train"])
    variants = [(name, gate) for name in COMPARED for gate in (False, True)]
    by_profile: dict[str, list[dict]] = {_variant(name, gate): [] for name, gate in variants}
    fitted_of: dict[tuple[str, int], dict] = {}
    shap_ok = True
    for seed in TRAIN_SEEDS:
        fitted_seed = {}
        for name in COMPARED:
            print(f"seed {seed} {name}", flush=True)
            fitted_seed[name] = fit_profile(frames, PROFILES[name], seed)
            fitted_of[(name, seed)] = fitted_seed[name]
            if name == "standard":
                shap_ok = shap_report(fitted_seed[name]["model"], frames["test"], fitted_seed[name]["columns"], fitted_seed[name]["categories"]) and shap_ok
        for name, gate in variants:
            fitted = fitted_seed[name]
            metrics = _metrics(bundle, frames, fitted, None, payee_gate=gate)
            label = _variant(name, gate)
            metrics["profile"] = label
            metrics["model"] = name
            metrics["seed"] = seed
            metrics["columns"] = len(fitted["columns"])
            metrics["rounds"] = fitted["rounds"]
            share = 100 * metrics["saved"] / metrics["ceiling"] if metrics["ceiling"] else float("nan")
            print(
                f"  {label}: vòng {fitted['rounds']}  "
                f"tiền {metrics['saved']:,.0f} ({share:.1f}% trần {metrics['ceiling']:,.0f})  "
                f"recall@1% {100 * metrics['recall_at_1']:.2f}%  "
                f"MH {100 * metrics['legit_mh']:.2f}%  Medium nhà {100 * metrics['medium_guided']:.2f}%  "
                f"Medium vay {100 * metrics['medium_loan']:.2f}%  cúp máy {100 * metrics['guided_hangup']:.2f}%  "
                f"≥Med police {100 * metrics['medium_police']:.1f}%  investment {100 * metrics['medium_investment']:.1f}%  "
                f"own_name {100 * metrics['medium_own_name']:.1f}%  relative {100 * metrics['medium_relative']:.1f}%",
                flush=True,
            )
            by_profile[label].append(metrics)
    print("5 seed, mean ± sd", flush=True)
    summary = {}
    for name, rows in by_profile.items():
        stats = _mean_sd(rows)
        summary[name] = {key: {"mean": mean, "sd": sd} for key, (mean, sd) in stats.items()}
        ceiling_mean = float(np.mean([row["ceiling"] for row in rows]))
        summary[name]["ceiling"] = {"mean": ceiling_mean, "sd": float(np.std([row["ceiling"] for row in rows], ddof=1))}
        print(
            f"  {name}: tiền {stats['saved'][0]:,.0f} ± {stats['saved'][1]:,.0f} "
            f"({100 * stats['saved'][0] / ceiling_mean:.1f}% trần)  "
            f"recall@1% {100 * stats['recall_at_1'][0]:.2f}% ± {100 * stats['recall_at_1'][1]:.2f}  "
            f"MH {100 * stats['legit_mh'][0]:.2f}% ± {100 * stats['legit_mh'][1]:.2f}  "
            f"Medium nhà {100 * stats['medium_guided'][0]:.2f}% ± {100 * stats['medium_guided'][1]:.2f}  "
            f"Medium vay {100 * stats['medium_loan'][0]:.2f}% ± {100 * stats['medium_loan'][1]:.2f}  "
            f"cúp máy {100 * stats['guided_hangup'][0]:.2f}% ± {100 * stats['guided_hangup'][1]:.2f}  "
            f"≥Med police {100 * stats['medium_police'][0]:.1f}% ± {100 * stats['medium_police'][1]:.1f}  "
            f"investment {100 * stats['medium_investment'][0]:.1f}% ± {100 * stats['medium_investment'][1]:.1f}  "
            f"own_name {100 * stats['medium_own_name'][0]:.1f}% ± {100 * stats['medium_own_name'][1]:.1f}  "
            f"relative {100 * stats['medium_relative'][0]:.1f}% ± {100 * stats['medium_relative'][1]:.1f}",
            flush=True,
        )
    chosen, eligible = _choose_profile(by_profile)
    print(f"cửa tiền ≥99%: {', '.join(eligible)}. profile demo: {chosen}", flush=True)
    picked = _closest(by_profile[chosen])
    print(f"  đóng gói {chosen} seed {picked['seed']} (gần trung bình, không phải seed tốt nhất)", flush=True)
    watched = {"police": "medium_police", "investment": "medium_investment", "own_name_safe_account": "medium_own_name", "relative": "medium_relative"}
    for scenario, shares in picked["scenario_levels"].items():
        text = " ".join(f"{key} {100 * value:.1f}%" for key, value in shares.items())
        extra = f"  ≥Med {100 * picked[watched[scenario]]:.1f}%" if scenario in watched else ""
        print(f"    {scenario}: recall {100 * picked['scenario_recall'][scenario]:.1f}%{extra}  {text}", flush=True)
    model_name = picked["model"]
    if "in_call" in PROFILES[model_name] and not shap_ok:
        raise RuntimeError("SHAP SDK sai chiều hoặc max |SHAP| lớn hơn 15. Chưa đóng gói.")
    fitted = fitted_of[(model_name, picked["seed"])]
    if ARTIFACTS.exists():
        for stale in list(ARTIFACTS.iterdir()):
            if stale.is_dir() and stale.name != chosen:
                for child in stale.iterdir():
                    if child.is_file():
                        child.unlink()
                stale.rmdir()
    save_profile(chosen, fitted["columns"], fitted, _public(picked), picked["seed"])
    print(f"  đã ghi {ARTIFACTS / chosen}", flush=True)
    packaged = [picked]
    payload = {
        "seeds": list(TRAIN_SEEDS),
        "demo_profile": chosen,
        "eligible": eligible,
        "summary": summary,
        "rows": [_public(row) for row in packaged],
        "runs": [_public(row) for rows in by_profile.values() for row in rows],
    }
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_table([_public(row) for row in packaged])
    return packaged


def _print_table(rows: list[dict]) -> None:
    print(
        f"{'profile':<22} {'cột':>4} {'tiền %':>8} {'recall@1%':>10} {'MH hợp lệ':>10} "
        f"{'Medium vay':>11} {'cúp máy':>8}",
        flush=True,
    )
    for row in rows:
        print(
            f"{row['profile']:<22} {row['columns']:>4} {100 * row['rate']:>7.2f}% {100 * row['recall_at_1']:>9.2f}% "
            f"{100 * row['legit_mh']:>9.2f}% {100 * row['medium_loan']:>10.2f}% {100 * row['guided_hangup']:>7.2f}%",
            flush=True,
        )
        recalls = " ".join(f"{name} {100 * value:.1f}%" for name, value in row["scenario_recall"].items())
        print(f"  recall {recalls}", flush=True)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
