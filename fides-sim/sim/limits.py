"""Hạn mức chuyển khoản theo phân khúc khách.

Giá trị trên tài khoản là hạn mức ban đầu. Nâng trong cửa sổ nằm ở account_changes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEGMENT_BAND = {
    "thường": "normal",
    "cao tuổi": "elderly",
    "doanh nghiệp nhỏ": "small_business",
}


def assign_initial_limits(accounts: pd.DataFrame, customers: pd.DataFrame, cfg: dict, rng: np.random.Generator) -> pd.DataFrame:
    """Gán limit_per_txn và limit_per_day. ~above_default_share khách đã tự nâng gấp đôi từ trước."""
    spec = cfg["limits"]
    per_txn = {str(key): int(value) for key, value in spec["per_txn"].items()}
    per_day = {str(key): int(value) for key, value in spec["per_day"].items()}
    segment = customers.set_index(customers["customer_id"].astype(str))["segment"].astype(str)
    owner = accounts["customer_id"]
    known = owner.notna().to_numpy()
    band = np.full(len(accounts), "normal", dtype=object)
    if known.any():
        mapped = owner.astype(str).map(segment).map(SEGMENT_BAND)
        band[known] = mapped[known].fillna("normal").to_numpy()
    txn = np.array([per_txn[str(name)] for name in band], dtype=np.int64)
    day = np.array([per_day[str(name)] for name in band], dtype=np.int64)
    people = customers["customer_id"].astype(str).to_numpy()
    n_raise = int(round(float(spec["above_default_share"]) * len(people)))
    chosen = set(rng.choice(people, size=n_raise, replace=False).tolist()) if n_raise else set()
    raised = known & owner.astype(str).isin(chosen).to_numpy()
    txn[raised] *= np.int64(2)
    day[raised] *= np.int64(2)
    out = accounts.copy()
    out["limit_per_txn"] = txn
    out["limit_per_day"] = day
    return out


def _seconds(ts: pd.Series) -> np.ndarray:
    raw = ts.astype("int64").to_numpy()
    if len(raw) == 0:
        return raw
    scale = int(np.max(np.abs(raw)))
    if scale < 10**14:
        return raw
    if scale < 10**17:
        return raw // 1_000_000
    return raw // 1_000_000_000


def day_key(ts: pd.Series) -> np.ndarray:
    """Ngày lịch Asia/Ho_Chi_Minh, không phụ thuộc đơn vị datetime của parquet."""
    return (_seconds(ts) + 7 * 3600) // 86400


def limit_report(transactions: pd.DataFrame, accounts: pd.DataFrame, changes: pd.DataFrame) -> dict:
    """Đếm giao dịch khách vượt hạn mức đang có, và nhóm lệnh bị chia."""
    account_id = accounts["account_id"].astype(str).to_numpy()
    customer = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str))
    txn_lim = dict(zip(account_id, accounts["limit_per_txn"].astype(np.int64)))
    day_lim = dict(zip(account_id, accounts["limit_per_day"].astype(np.int64)))
    raises = []
    if len(changes):
        raised = changes.loc[changes["change_type"].astype(str).eq("limit_raise")]
        for record in raised.itertuples(index=False):
            raises.append((int(_seconds(pd.Series([record.ts]))[0]), str(record.account_id), str(record.new_value)))
    raises.sort()
    order = np.argsort(_seconds(transactions["ts"]), kind="mergesort")
    spent: dict[tuple[str, int], int] = {}
    cursor = 0
    violations = 0
    outbound = 0
    split = 0
    prev: dict[str, tuple[int, str, str, bool]] = {}
    open_group: dict[str, bool] = {}
    from_account = transactions["from_account"].astype(str).to_numpy()
    to_account = transactions["to_account"].astype(str).to_numpy()
    tx_type = transactions["tx_type"].astype(str).to_numpy()
    amount = transactions["amount"].astype(np.int64).to_numpy()
    seconds = _seconds(transactions["ts"])
    days = day_key(transactions["ts"])
    for index in order:
        sender = from_account[index]
        if sender not in customer:
            continue
        stamp = int(seconds[index])
        while cursor < len(raises) and raises[cursor][0] <= stamp:
            _, account, new_value = raises[cursor]
            txn_new, day_new = _parse_limit(new_value)
            if account in txn_lim:
                txn_lim[account] = txn_new
                day_lim[account] = day_new
            cursor += 1
        pay = int(amount[index])
        day = int(days[index])
        key = (sender, day)
        used = spent.get(key, 0)
        room = int(day_lim[sender]) - used
        cap_txn = int(txn_lim[sender])
        if pay > cap_txn or pay > room:
            violations += 1
        spent[key] = used + pay
        capped = pay == cap_txn or pay == room
        dest = to_account[index]
        kind = tx_type[index]
        previous = prev.get(sender)
        continues = (
            previous is not None
            and previous[3]
            and previous[1] == dest
            and previous[2] == kind
            and 0 < stamp - previous[0] <= 36 * 3600
        )
        if continues:
            if not open_group.get(sender, False):
                split += 1
                open_group[sender] = True
        else:
            outbound += 1
            open_group[sender] = False
        prev[sender] = (stamp, dest, kind, bool(capped))
    return {"violations": violations, "outbound": outbound, "split": split}


def _parse_limit(value: str) -> tuple[int, int]:
    parts = {}
    for item in str(value).split(";"):
        key, raw = item.split("=")
        parts[key] = int(raw)
    return parts["txn"], parts["day"]


def format_limit(per_txn: int, per_day: int) -> str:
    return f"txn={int(per_txn)};day={int(per_day)}"


def over_limit_outcomes(transactions: pd.DataFrame, accounts: pd.DataFrame, changes: pd.DataFrame, sme_ids: set[str]) -> dict:
    """Phân loại lệnh vượt hạn mức trước khi nâng: chia nhỏ hay nâng rồi chuyển một lần."""
    account_id = accounts["account_id"].astype(str).to_numpy()
    customer = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str))
    initial_txn = dict(zip(account_id, accounts["limit_per_txn"].astype(np.int64)))
    initial_day = dict(zip(account_id, accounts["limit_per_day"].astype(np.int64)))
    raises: dict[str, list[tuple[int, int, int]]] = {}
    if len(changes):
        raised = changes.loc[changes["change_type"].astype(str).eq("limit_raise")]
        for record in raised.itertuples(index=False):
            old_txn, old_day = _parse_limit(str(record.old_value))
            new_txn, new_day = _parse_limit(str(record.new_value))
            raises.setdefault(str(record.account_id), []).append(
                (int(_seconds(pd.Series([record.ts]))[0]), int(new_txn), int(new_day), int(old_txn), int(old_day))
            )
    for events in raises.values():
        events.sort()
    txn_lim = dict(initial_txn)
    day_lim = dict(initial_day)
    order = np.argsort(_seconds(transactions["ts"]), kind="mergesort")
    spent: dict[tuple[str, int], int] = {}
    cursor = {account: 0 for account in raises}
    prev: dict[str, tuple] = {}
    groups: list[dict] = []
    from_account = transactions["from_account"].astype(str).to_numpy()
    to_account = transactions["to_account"].astype(str).to_numpy()
    tx_type = transactions["tx_type"].astype(str).to_numpy()
    amount = transactions["amount"].astype(np.int64).to_numpy()
    seconds = _seconds(transactions["ts"])
    days = day_key(transactions["ts"])

    for index in order:
        sender = from_account[index]
        if sender not in customer:
            continue
        stamp = int(seconds[index])
        events = raises.get(sender, [])
        pos = cursor.get(sender, 0)
        while pos < len(events) and events[pos][0] <= stamp:
            txn_lim[sender] = int(events[pos][1])
            day_lim[sender] = int(events[pos][2])
            pos += 1
        cursor[sender] = pos
        pay = int(amount[index])
        day = int(days[index])
        key = (sender, day)
        used = spent.get(key, 0)
        room = int(day_lim[sender]) - used
        capped = pay == int(txn_lim[sender]) or pay == room
        dest = to_account[index]
        kind = tx_type[index]
        previous = prev.get(sender)
        continues = (
            previous is not None
            and previous[3]
            and previous[1] == dest
            and previous[2] == kind
            and 0 < stamp - previous[0] <= 36 * 3600
        )
        if continues:
            groups[int(previous[4])]["amount"] += pay
            groups[int(previous[4])]["n"] += 1
            group_index = int(previous[4])
        else:
            group_index = len(groups)
            groups.append(
                {
                    "account": sender,
                    "amount": pay,
                    "n": 1,
                    "start": stamp,
                    "spent_before": used,
                    "sme": sender in sme_ids,
                }
            )
        spent[key] = used + pay
        prev[sender] = (stamp, dest, kind, bool(capped), group_index)
    consumed: dict[str, int] = {}
    over = over_split = over_sme = over_sme_split = 0
    for group in groups:
        account = group["account"]
        bound = consumed.get(account, -1)
        txn = int(initial_txn[account])
        day = int(initial_day[account])
        candidate = None
        for event in raises.get(account, []):
            if event[0] > int(group["start"]):
                break
            if event[0] <= bound:
                txn, day = int(event[1]), int(event[2])
            else:
                candidate = event
        if group["amount"] <= txn and group["amount"] <= day - int(group["spent_before"]):
            continue
        over += 1
        split = group["n"] > 1
        if split:
            over_split += 1
        if group["sme"]:
            over_sme += 1
            if split:
                over_sme_split += 1
        if candidate is not None and not split:
            consumed[account] = int(candidate[0])
    return {
        "over": over,
        "over_split": over_split,
        "over_sme": over_sme,
        "over_sme_split": over_sme_split,
    }
