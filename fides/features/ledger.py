"""Đặc trưng tính từ sổ đã quan sát, cùng cửa sổ với bản online."""

from __future__ import annotations

import pandas as pd

from features.store import DAY_NS, TRANSFER_TYPES

PRODUCT_KIND = {
    "product_savings_7d": "savings_liquidation",
    "product_loan_7d": "quick_loan_disbursement",
    "product_cash_advance_7d": "credit_card_cash_advance",
}
COMPARED = ("new_payee", "a_w", *PRODUCT_KIND, "limit_raise_1d")


def _epoch_ns(value) -> int:
    if isinstance(value, pd.Timestamp):
        return int(value.value)
    return int(value)


def _before(frame: pd.DataFrame, column: str, as_of_ns: int, window_ns: int | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    stamp = frame[column].map(_epoch_ns).to_numpy()
    mask = stamp < as_of_ns
    if window_ns is not None:
        mask = mask & (stamp > as_of_ns - window_ns)
    return frame.loc[mask]


def ledger_pair_features(
    sender: str,
    payee: str,
    as_of_ns: int,
    customer_accounts: list[str],
    transfers: pd.DataFrame,
    products: pd.DataFrame,
    raises: pd.DataFrame,
    simo_listed_ns: dict[str, int] | None = None,
) -> dict:
    """new_payee, a_w, product_*_7d, limit_raise_1d tại as_of. Chỉ sự kiện có ts nhỏ hơn as_of.

    new_payee nhìn mọi giao dịch đã có người nhận. a_w chỉ cộng chuyển khoản: lần đầu gặp người nhận,
    hoặc người nhận đã bị liệt kê trước khoản đó.
    """
    accounts = set(customer_accounts) or {sender}
    listed = simo_listed_ns or {}
    touched = transfers.loc[transfers["from_account"].isin(accounts) & transfers["to_account"].astype(str).ne("")]
    seen = _before(touched, "ts_ns", as_of_ns)
    known = seen.loc[seen["to_account"].astype(str) == payee]
    first = seen.groupby(seen["to_account"].astype(str))["ts_ns"].min() if len(seen) else pd.Series(dtype="int64")
    outbound = transfers.loc[transfers["from_account"].isin(accounts) & transfers["tx_type"].isin(TRANSFER_TYPES)]
    recent = _before(outbound, "ts_ns", as_of_ns, DAY_NS)
    total = 0
    for record in recent.itertuples(index=False):
        target = str(record.to_account)
        if not target or target not in first.index:
            continue
        opened = _epoch_ns(first.get(target))
        marked = listed.get(target)
        stamp = _epoch_ns(record.ts_ns)
        if stamp == opened or (marked is not None and _epoch_ns(marked) < stamp):
            total += int(record.amount)
    row = {
        "new_payee": 0 if len(known) else 1,
        "a_w": total,
        "limit_raise_1d": int(len(_before(raises.loc[raises["account_id"] == sender], "ts_ns", as_of_ns, DAY_NS))),
    }
    owned = products.loc[products["account_id"] == sender]
    for column, kind in PRODUCT_KIND.items():
        row[column] = int(len(_before(owned.loc[owned["kind"] == kind], "ts_ns", as_of_ns, 7 * DAY_NS)))
    return row
