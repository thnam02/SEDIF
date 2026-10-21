"""Phục vụ đặc trưng theo profile và luật họ tín hiệu.

basic chỉ đọc sổ giao dịch và thụ hưởng của chính ngân hàng.
standard thêm tín hiệu thiết bị.
advanced thêm nhật ký cuộc gọi nhà mạng và sổ các ngân hàng khác.
Tín hiệu nằm ngoài profile được ghi vào missing_signals, không đưa vào quyết định.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from features.store import TRANSFER_TYPES, FeatureStore, _ns

MOBILIZE_TYPES = {"savings_liquidation", "quick_loan_disbursement", "credit_card_cash_advance"}
ENTRY_MODES = {"pasted"}


class FeatureService:
    """Một profile cho mỗi lần phục vụ. Cùng một kho, nhưng mỗi profile chỉ đọc phần của mình."""

    def __init__(self, observed: Path | str, profile: str = "standard"):
        if profile not in {"basic", "standard", "advanced"}:
            raise ValueError("profile phải là basic, standard hoặc advanced")
        self.profile = profile
        self.store = FeatureStore(observed)

    def compute(self, session_id: str, as_of_ts, view: str = "single_bank") -> dict:
        return self.store.compute(str(session_id), as_of_ts, view, self.profile)


def severity(active: set[str]) -> str:
    """Chỉ có cuộc gọi thì không can thiệp. Hai họ tối đa Medium. Từ ba họ trở lên là High."""
    families = {name for name in active if name}
    if not families or families == {"call"}:
        return "none"
    if len(families) >= 3:
        return "high"
    if len(families) == 2:
        return "medium"
    return "low"


def action_for(level: str) -> str:
    return {
        "none": "allow",
        "low": "g1_banner",
        "medium": "pause_and_verify",
        "high": "hangup_required",
    }[level]


def actions_from_families(frame: pd.DataFrame) -> pd.Series:
    call = frame["call"].fillna(False).to_numpy(dtype=bool)
    entry = frame["entry"].fillna(False).to_numpy(dtype=bool)
    payee = frame["payee"].fillna(False).to_numpy(dtype=bool)
    funding = frame["funding"].fillna(False).to_numpy(dtype=bool)
    count = call.astype(np.int8) + entry.astype(np.int8) + payee.astype(np.int8) + funding.astype(np.int8)
    level = np.full(len(frame), "low", dtype=object)
    level[(count == 0) | (call & (count == 1))] = "none"
    level[count == 2] = "medium"
    level[count >= 3] = "high"
    return pd.Series([action_for(item) for item in level], index=frame.index)


def session_families(observed: Path | str) -> pd.DataFrame:
    """Bốn họ: cuộc gọi, dán số tài khoản, người nhận lạ, huy động tiền. Chỉ đọc sổ đã quan sát."""
    observed = Path(observed)
    sessions = pd.read_parquet(
        observed / "sessions.parquet",
        columns=["session_id", "customer_id", "account_id", "start_ts", "end_ts"],
    )
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["customer_id"] = sessions["customer_id"].astype(str)
    sessions["account_id"] = sessions["account_id"].astype(str)
    signals = pd.read_parquet(
        observed / "device_signals.parquet",
        columns=["session_id", "in_call_at_open", "call_ended_in_session"],
    )
    signals["session_id"] = signals["session_id"].astype(str)
    events = pd.read_parquet(observed / "session_events.parquet", columns=["session_id", "event_type"])
    events["session_id"] = events["session_id"].astype(str)
    kind = events["event_type"].astype(str)
    mode = (
        events.loc[kind.str.startswith("acct_input_")]
        .groupby("session_id", sort=False)["event_type"]
        .first()
        .astype(str)
        .str.replace("acct_input_", "", regex=False)
    )
    accounts = pd.read_parquet(observed / "accounts.parquet", columns=["account_id", "customer_id", "limit_per_txn"])
    accounts["account_id"] = accounts["account_id"].astype(str)
    owner = dict(zip(accounts["account_id"], accounts["customer_id"].astype(str)))
    limit_of = dict(zip(accounts["account_id"], accounts["limit_per_txn"].fillna(0).to_numpy(dtype=np.int64)))
    transactions = pd.read_parquet(
        observed / "transactions.parquet",
        columns=["ts", "from_account", "to_account", "amount", "tx_type", "session_id"],
    )
    transactions["from_account"] = transactions["from_account"].astype(str)
    transactions["to_account"] = transactions["to_account"].astype(str)
    transactions["session_id"] = transactions["session_id"].astype(str)
    transactions["customer"] = transactions["from_account"].map(owner).fillna("")
    transactions["ts_ns"] = _ns(transactions["ts"])
    known = transactions.loc[
        transactions["customer"].ne("") & transactions["to_account"].ne("") & transactions["to_account"].ne("<NA>"),
        ["customer", "to_account", "ts_ns"],
    ]
    first = known.groupby(["customer", "to_account"], sort=False)["ts_ns"].min().rename("first_ns").reset_index()
    in_session = transactions["session_id"].isin(set(sessions["session_id"]))
    sess_tx = transactions.loc[in_session]
    xfer = sess_tx["tx_type"].astype(str).isin(TRANSFER_TYPES)
    main = sess_tx.loc[xfer].sort_values(["amount", "ts_ns"], kind="mergesort").groupby("session_id", sort=False).tail(1)
    main = main.merge(sessions[["session_id", "customer_id", "start_ts"]], on="session_id", how="left")
    main = main.merge(first, left_on=["customer_id", "to_account"], right_on=["customer", "to_account"], how="left")
    start_ns = _ns(main["start_ts"])
    known_first = main["first_ns"].notna().to_numpy()
    first_ns = np.zeros(len(main), dtype=np.int64)
    if known_first.any():
        first_ns[known_first] = main.loc[known_first, "first_ns"].to_numpy(dtype=np.int64)
    payee_new = ~known_first | (first_ns >= start_ns)
    payee = pd.Series(payee_new, index=main["session_id"].to_numpy())
    mobilize = sess_tx.loc[sess_tx["tx_type"].astype(str).isin(MOBILIZE_TYPES)].groupby("session_id", sort=False).size()
    piece = sess_tx.loc[xfer, ["session_id", "from_account", "amount"]].copy()
    piece["limit"] = piece["from_account"].map(limit_of).fillna(0)
    piece = piece.loc[(piece["limit"] > 0) & (piece["amount"] >= 0.85 * piece["limit"])]
    split = piece.groupby("session_id", sort=False).size()
    changes_path = observed / "account_changes.parquet"
    raised_ids: set[str] = set()
    if changes_path.exists():
        changes = pd.read_parquet(changes_path, columns=["account_id", "ts", "change_type"])
        raises = changes.loc[changes["change_type"].astype(str).eq("limit_raise"), ["account_id", "ts"]]
        raises["account_id"] = raises["account_id"].astype(str)
        window = sessions.merge(raises, on="account_id", how="inner")
        if len(window):
            hit = (window["ts"] >= window["start_ts"] - pd.Timedelta(minutes=30)) & (window["ts"] <= window["end_ts"])
            raised_ids = set(window.loc[hit, "session_id"].astype(str))
    call = signals["in_call_at_open"].fillna(False).astype(bool) | signals["call_ended_in_session"].fillna(False).astype(bool)
    call.index = signals["session_id"].to_numpy()
    mode_hit = mode.isin(ENTRY_MODES).reindex(sessions["session_id"]).fillna(False)
    frame = pd.DataFrame({"session_id": sessions["session_id"].to_numpy()})
    frame["call"] = frame["session_id"].map(call).fillna(False).astype(bool)
    frame["entry"] = mode_hit.to_numpy(dtype=bool)
    frame["payee"] = frame["session_id"].map(payee).fillna(False).astype(bool)
    funding = (
        frame["session_id"].map(mobilize).fillna(0).gt(0)
        | frame["session_id"].map(split).fillna(0).ge(2)
        | frame["session_id"].isin(raised_ids)
    )
    frame["funding"] = funding.to_numpy(dtype=bool)
    return frame.reset_index(drop=True)
