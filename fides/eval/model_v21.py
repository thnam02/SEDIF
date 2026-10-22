"""Ba profile demo model v2.1 và một bản lộ trình không đóng gói.

basic là sổ, sản phẩm, thay đổi tài khoản, và tra cứu tên người nhận.
standard thêm khối thiết bị của SDK.
advanced thêm T4c, cờ thiếu và simo_listed.
advanced+telco+PAD thêm telco và PAD (accessibility, remote, screen sharing, overlay).
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
    SEED,
    T4C,
    calibrate,
    choose_tau_hi,
    family_counts,
    scale_for,
    with_response,
)
from eval.policy_ablation import FLAGS, attach_t4c, load_part, money_parts
from eval.split import FRAUD_TYPES, SIM, _label_groups, build_split, group_folds
from features import compute_features
from features.service import levels_from_counts, session_families
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
    "advanced+telco+PAD": ROADMAP,
}
PACKAGED = ("basic", "standard", "advanced")
CATEGORICAL = {"acct_input", "add_method"}
PRODUCT_KIND = {
    "product_savings_7d": "savings_liquidation",
    "product_loan_7d": "quick_loan_disbursement",
    "product_cash_advance_7d": "credit_card_cash_advance",
}
ACCT_INPUT = ("typed", "typed_chunked", "pasted", "chosen")


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
        frames[name] = frame.merge(extra, on="session_id", how="left")
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


def _matrix(frame: pd.DataFrame, columns: list[str], categories: dict[str, pd.CategoricalDtype]) -> pd.DataFrame:
    matrix = frame[columns].copy()
    for name in columns:
        if name in categories:
            matrix[name] = matrix[name].astype("string").astype(categories[name])
        else:
            matrix[name] = pd.to_numeric(matrix[name], errors="coerce")
    return matrix


def _booster(train: pd.DataFrame, columns: list[str], categories: dict[str, pd.CategoricalDtype]):
    import lightgbm as lgb

    matrix = _matrix(train, columns, categories)
    categorical = [name for name in columns if name in categories]
    dataset = lgb.Dataset(
        matrix,
        label=train["label"].to_numpy(),
        weight=train["sample_weight"].to_numpy(dtype=float),
        categorical_feature=categorical or "auto",
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
    return model


def _out_of_fold(train: pd.DataFrame, columns: list[str]) -> np.ndarray:
    raw = np.zeros(len(train), dtype=float)
    groups = _label_groups(train["case_id"], train["session_id"], "c")
    for train_mask, test_mask in group_folds(groups, n_splits=5, seed=SEED):
        held = train.iloc[np.flatnonzero(train_mask)]
        categories = _categories(held, columns)
        model = _booster(held, columns, categories)
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
    pieces = []
    for frame in frames.values():
        work = frame[["session_id", "case_id", "customer_id", "amount", "is_fraud", "scam_type"]].copy()
        work["families_n"] = family_counts(frame, families, None)
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
    }


def _metrics(bundle: dict, frames: dict[str, pd.DataFrame], scores: dict, tau_hi: float | None) -> dict:
    population = bundle["population"]
    probability = population["session_id"].map(scores["map"]).to_numpy(dtype=float)
    amount = population["amount"].to_numpy(dtype=float)
    families = population["families_n"].to_numpy(dtype=int)
    scale = scale_for(population["segment"], bundle["fp_table"])
    if tau_hi is None:
        tau_hi = choose_tau_hi(probability, amount, families, bundle["legit"], scale, quiet=True)
    level = levels_from_counts(probability, amount, families, COST_ETA, scale, tau_hi)
    scored = population.copy()
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
    for name in FRAUD_TYPES:
        mask = fraud & (scenario == name)
        by_scenario[name] = float(caught[mask].mean()) if mask.any() else float("nan")
        money_by_scenario[name] = float(saved_row[mask].sum()) if mask.any() else 0.0
    level = answered["level"].to_numpy()
    money_by_level = {
        "medium": float(saved_row[level == "medium"].sum()),
        "high": float(saved_row[level == "high"].sum()),
    }
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
        "medium_loan": float(np.mean(answered["level"].to_numpy()[friend] == "medium")) if friend.any() else float("nan"),
        "guided_hangup": float(np.mean(answered["action"].to_numpy()[guided] == "hangup_required")) if guided.any() else float("nan"),
        "scenario_recall": by_scenario,
        "money_by_level": money_by_level,
        "money_by_scenario": money_by_scenario,
        "_saved_row": saved_row,
        "_raw": raw,
        "_label": label,
        "_groups": groups,
    }


def fit_profile(frames: dict[str, pd.DataFrame], columns: list[str]) -> dict:
    categories = _categories(frames["train"], columns)
    raw_oof = _out_of_fold(frames["train"], columns)
    _, apply = calibrate(raw_oof, frames["train"]["is_fraud"].to_numpy(dtype=float))
    model = _booster(frames["train"], columns, categories)
    raw_test = model.predict(_matrix(frames["test"], columns, categories))
    raw_buffer = model.predict(_matrix(frames["buffer"], columns, categories))
    mapping = dict(zip(frames["train"]["session_id"].astype(str), apply(raw_oof)))
    mapping.update(zip(frames["test"]["session_id"].astype(str), apply(raw_test)))
    mapping.update(zip(frames["buffer"]["session_id"].astype(str), apply(raw_buffer)))
    return {"map": mapping, "raw_test": raw_test, "raw_oof": raw_oof, "model": model, "categories": categories, "apply": apply}


def _policy_payload(columns: list[str], categories: dict, apply, tau_hi: float) -> dict:
    return {
        "model_version": "model-v2.1",
        "seed": SEED,
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


def save_profile(name: str, columns: list[str], fitted: dict, metrics: dict) -> None:
    directory = ARTIFACTS / name
    directory.mkdir(parents=True, exist_ok=True)
    fitted["model"].save_model(str(directory / "model.txt"))
    policy = _policy_payload(columns, fitted["categories"], fitted["apply"], metrics["tau_hi"])
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
    return _metrics(bundle, frames, {"map": mapping, "raw_test": raw_test}, policy["tau_hi"])


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


def run() -> list[dict]:
    mapping = column_map()
    print("đối chiếu cột", flush=True)
    for item in mapping:
        where = ",".join(item["profiles"]) if item["profiles"] else "không"
        print(f"  {item['column']}: {where}. {item['note']}", flush=True)
    frames, bundle = prepare()
    rows = []
    for name, columns in PROFILES.items():
        print(f"train {name} ({len(columns)} cột)", flush=True)
        fitted = fit_profile(frames, columns)
        metrics = _metrics(bundle, frames, fitted, None)
        metrics["profile"] = name
        metrics["columns"] = len(columns)
        rows.append(metrics)
        if name in PACKAGED:
            save_profile(name, columns, fitted, {key: value for key, value in metrics.items() if not key.startswith("_")})
            print(f"  đã ghi {ARTIFACTS / name}", flush=True)
    pairs = _paired(rows)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    payload = {"seed": SEED, "column_map": mapping, "rows": [_public(row) for row in rows], "pairs": pairs}
    (ARTIFACTS / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_table([_public(row) for row in rows])
    for item in pairs:
        print(
            f"  {item['left']} - {item['right']}: tiền {item['money']:,.0f} "
            f"[{item['money_low']:,.0f}–{item['money_high']:,.0f}], "
            f"recall@1% {100 * item['recall_at_1']:.2f} điểm "
            f"[{100 * item['recall_low']:.2f}–{100 * item['recall_high']:.2f}]",
            flush=True,
        )
    return rows


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
