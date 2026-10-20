"""Bước 6: phiên, dòng tiền lừa đảo, chuyển tiếp mule và tín hiệu thiết bị.

Đọc bước 1–5. Chạy lại thì gỡ phần đã chèn rồi sinh lại. Cùng seed cho cùng kết quả.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.io import write
from sim.step4_mules import as_timestamp, next_ids
from sim.step5_cases import to_ns

TZ = "Asia/Ho_Chi_Minh"
VAGUE = ["chuyen tien", "ck", "thanh toan", "chuyen khoan"]
PURPOSE_VI = {
    "phoi hop dieu tra": "Phối hợp điều tra",
    "chuyen tien an toan": "Chuyển khoản để đảm bảo an toàn",
    "nop tien bao lanh": "Nộp tiền bảo lãnh",
    "nang cap bao mat": "Nâng cấp bảo mật tài khoản",
    "xac thuc tai khoan": "Xác thực tài khoản",
    "gop von": "Góp vốn đầu tư",
    "nap dau tu": "Nạp tiền đầu tư",
    "phi giai ngan": "Phí giải ngân",
    "chuyen tien": "Chuyển tiền giúp người thân",
    "giup do": "Giúp đỡ người quen",
    "vay gap": "Cho vay gấp",
    "dat coc": "Đặt cọc",
    "phi ho so": "Nộp phí hồ sơ",
    "giu cho": "Giữ chỗ",
    "chuyen tien gap": "Chuyển tiền gấp cho người nhà",
    "vien phi": "Đóng viện phí",
    "bao lanh": "Nộp tiền bảo lãnh",
    "tra lai": "Nhận lãi đầu tư",
    "chuyen tu tiet kiem": "Chuyển tiền từ sổ tiết kiệm",
    "chuyen tiep": "Chuyển tiếp",
    "rut tien mat": "Rút tiền mặt",
    "mua crypto": "Mua tài sản số",
}
LEGIT_PURPOSE = {
    "rent": "Trả tiền thuê nhà",
    "school_fee": "Đóng học phí",
    "large_oneoff": "Thanh toán khoản lớn",
    "own_transfer": "Chuyển giữa các tài khoản của tôi",
    "own_transfer_drain": "Chuyển sang tài khoản khác của tôi",
    "passthrough_out": "Chuyển tiếp tiền giúp người khác",
    "transfer": "Chuyển khoản",
}
YES = "có"
NO = "không"


def load_config() -> dict:
    import yaml

    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def stamp_ns(value: pd.Timestamp) -> int:
    return int(value.value)


def lognormal_seconds(median: float, n: int, rng, low: float = 2.0, high: float = 90.0) -> np.ndarray:
    draw = np.exp(np.log(median) + 0.62 * rng.standard_normal(n))
    return np.clip(draw, low, high)


def reset_previous(transactions: pd.DataFrame, accounts: pd.DataFrame, observed: Path, truth: Path) -> pd.DataFrame:
    """Bỏ giao dịch bước 6 (theo mốc tx_id đã ghi) và trả session_id về null."""
    del accounts
    transactions = transactions.copy()
    if not (observed / "sessions.parquet").exists():
        transactions["session_id"] = pd.Series(pd.NA, index=transactions.index, dtype="string")
        return transactions
    floor_path = truth / "step6_floor.parquet"
    if not floor_path.exists():
        raise RuntimeError("Có sessions nhưng thiếu truth/step6_floor.parquet, không gỡ được lần chạy trước")
    floor = int(pd.read_parquet(floor_path)["first_tx_num"].iloc[0])
    numbers = transactions["tx_id"].astype(str).str[1:].astype(np.int64)
    kept = transactions.loc[numbers < floor].copy()
    kept["session_id"] = pd.Series(pd.NA, index=kept.index, dtype="string")
    return kept


def device_table(links: pd.DataFrame) -> dict[str, list[tuple[str, int, int | None]]]:
    table: dict[str, list[tuple[str, int, int | None]]] = {}
    ordered = links.sort_values("login_count", ascending=False)
    first = to_ns(ordered["first_seen"].astype("int64").to_numpy())
    last_raw = ordered["last_seen"]
    last_ns = to_ns(last_raw.astype("int64").to_numpy()) if last_raw.notna().any() else np.zeros(len(ordered), dtype=np.int64)
    missing_last = last_raw.isna().to_numpy()
    accounts = ordered["account_id"].astype(str).to_numpy()
    devices = ordered["device_id"].astype(str).to_numpy()
    for index, account in enumerate(accounts):
        stop = None if missing_last[index] else int(last_ns[index])
        table.setdefault(str(account), []).append((str(devices[index]), int(first[index]), stop))
    return table


def device_at(table, account: str, when: pd.Timestamp) -> str | None:
    rows = table.get(str(account))
    if not rows:
        return None
    stamp = stamp_ns(when)
    for device, start, stop in rows:
        if start <= stamp and (stop is None or stamp <= stop):
            return device
    return rows[0][0]


def assign_legit_sessions(transactions: pd.DataFrame, banks: set[str], kinds: set[str], account_bank: dict[str, str], rng) -> dict[str, str]:
    mask = (
        transactions["tx_type"].astype(str).isin(kinds)
        & transactions["channel"].astype(str).eq("app")
        & transactions["from_account"].astype(str).map(account_bank).isin(banks)
    )
    sub = transactions.loc[mask, ["tx_id", "ts", "from_account"]].sort_values(["from_account", "ts", "tx_id"], kind="mergesort")
    if sub.empty:
        return {}
    account = sub["from_account"].astype(str).to_numpy()
    stamps = to_ns(sub["ts"].astype("int64").to_numpy())
    tx_ids = sub["tx_id"].astype(str).to_numpy()
    assigned: dict[str, str] = {}
    session_no = 0
    start = 0
    capacity = 1
    ten_min = 10 * 60 * 1_000_000_000
    for index in range(len(sub)):
        gap = stamps[index] - stamps[index - 1] if index else ten_min + 1
        if index == 0 or account[index] != account[index - 1] or gap > ten_min or (index - start) >= capacity:
            session_no += 1
            start = index
            capacity = int(rng.choice([1, 2, 3], p=[0.55, 0.30, 0.15]))
        assigned[str(tx_ids[index])] = f"S{session_no:08d}"
    return assigned


def customer_accounts(accounts: pd.DataFrame) -> dict[str, pd.DataFrame]:
    owned = accounts.loc[accounts["customer_id"].notna(), ["account_id", "customer_id", "bank_id", "account_type", "opening_balance"]].copy()
    owned["account_id"] = owned["account_id"].astype(str)
    owned["customer_id"] = owned["customer_id"].astype(str)
    return {customer: part.reset_index(drop=True) for customer, part in owned.groupby("customer_id", sort=False)}


def pick_account(part: pd.DataFrame, bank: str | None, kind: str) -> str | None:
    pool = part
    if bank is not None:
        pool = part.loc[part["bank_id"].astype(str) == bank]
    pool = pool.loc[pool["account_type"].astype(str) == kind]
    if pool.empty:
        return None
    return str(pool.sort_values("opening_balance", ascending=False)["account_id"].iloc[0])


def make_tx(tx_id, ts, source, dest, amount, tx_type, purpose, session_id) -> dict:
    return {
        "tx_id": tx_id,
        "ts": ts,
        "from_account": source,
        "to_account": dest,
        "amount": int(amount),
        "tx_type": tx_type,
        "purpose_text": purpose,
        "channel": "app",
        "session_id": session_id,
        "from_balance_before": np.int64(0),
    }


def history_before(transactions: pd.DataFrame, needed: set[str], opening: dict[str, int]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Số dư sau mỗi giao dịch cũ, để khoản lừa đảo phía sau hỏi số dư ngay trước nó."""
    source = transactions["from_account"].astype(str)
    dest = transactions["to_account"].astype(str)
    mask = source.isin(needed) | dest.isin(needed)
    sub = transactions.loc[mask, ["tx_id", "ts", "from_account", "to_account", "amount"]]
    buckets: dict[str, list[tuple]] = {account: [] for account in needed}
    for row in sub.itertuples(index=False):
        sender = str(row.from_account)
        receiver = "" if pd.isna(row.to_account) else str(row.to_account)
        stamp = row.ts.value
        amount = int(row.amount)
        if sender in buckets:
            buckets[sender].append((stamp, str(row.tx_id), -amount))
        if receiver in buckets and receiver != sender:
            buckets[receiver].append((stamp, str(row.tx_id), amount))
    curves = {}
    for account, items in buckets.items():
        items.sort()
        running = int(opening.get(account, 0))
        stamps = np.empty(len(items), dtype=np.int64)
        values = np.empty(len(items), dtype=np.int64)
        for index, (stamp, _tx, delta) in enumerate(items):
            running += delta
            stamps[index] = stamp
            values[index] = running
        curves[account] = (stamps, values)
    return curves


def balance_before(curves: dict[str, tuple[np.ndarray, np.ndarray]], opening: dict[str, int], account: str, when: pd.Timestamp) -> int:
    stamps, values = curves.get(account, (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)))
    if len(stamps) == 0:
        return int(opening.get(account, 0))
    index = int(np.searchsorted(stamps, np.int64(when.value), side="right")) - 1
    if index < 0:
        return int(opening.get(account, 0))
    return int(values[index])


def scale_investment(extra: list[dict], transactions: pd.DataFrame, opening: dict[str, int]) -> None:
    """Thu nhỏ cả chuỗi investment cùng một tỷ lệ để vừa số dư tài khoản chính, giữ thứ tự tăng/giảm."""
    payments = [row for row in extra if row.get("_invest")]
    if not payments:
        return
    needed = {str(row["from_account"]) for row in payments}
    curves = history_before(transactions, needed, opening)
    inserted: dict[str, list[tuple[pd.Timestamp, int]]] = {account: [] for account in needed}
    for row in extra:
        if row.get("_invest"):
            continue
        amount = int(row["amount"])
        sender = str(row["from_account"])
        receiver = "" if row["to_account"] is None or pd.isna(row["to_account"]) else str(row["to_account"])
        if sender in inserted:
            inserted[sender].append((row["ts"], -amount))
        if receiver in inserted:
            inserted[receiver].append((row["ts"], amount))

    def room(account: str, when: pd.Timestamp) -> int:
        added = sum(delta for stamp, delta in inserted[account] if stamp < when)
        return balance_before(curves, opening, account, when) + added

    grouped: dict[str, list[dict]] = {}
    for row in payments:
        grouped.setdefault(str(row["_case"]), []).append(row)
    for case_id in sorted(grouped, key=lambda key: min(row["ts"] for row in grouped[key])):
        rows = sorted(grouped[case_id], key=lambda row: (row["ts"], row["tx_id"]))
        original = [int(row["amount"]) for row in rows]
        cumulative = 0
        limit = 1.0
        for row, amount in zip(rows, original):
            cumulative += amount
            available = room(str(row["from_account"]), row["ts"])
            if cumulative > 0:
                limit = min(limit, available / cumulative)
        limit = max(0.0, min(1.0, limit))
        scaled = [int(amount * limit // 1000) * 1000 for amount in original]
        for index in range(1, len(scaled)):
            if original[index] > original[index - 1] and scaled[index] <= scaled[index - 1]:
                scaled[index] = scaled[index - 1] + 1000
            elif original[index] == original[index - 1]:
                scaled[index] = scaled[index - 1]
            elif original[index] < original[index - 1] and scaled[index] >= scaled[index - 1] and scaled[index - 1] > 1000:
                scaled[index] = scaled[index - 1] - 1000
        for row, amount in zip(rows, scaled):
            row["amount"] = int(amount)
            inserted[str(row["from_account"])].append((row["ts"], -int(amount)))


def forward_chain(inbounds: list[tuple[str, int, pd.Timestamp]], ring_of: dict[str, dict], rng, sim_end: pd.Timestamp, tx_ids: list[str], cursor: int) -> tuple[list[dict], int]:
    """Mỗi khoản vào lớp 1 đi theo forward_to, giữ 2–10% phí, rồi cashout."""
    rows = []
    queue = deque(inbounds)
    seen = 0
    while queue and seen < 200_000:
        account, amount, ts = queue.popleft()
        seen += 1
        meta = ring_of.get(account)
        if meta is None or amount < 1000 or ts >= sim_end:
            continue
        fee = float(rng.uniform(0.02, 0.10))
        send = int(amount * (1.0 - fee) // 1000 * 1000)
        if send < 1000:
            continue
        dests = [str(item) for item in meta["forward_to"] if str(item)]
        if meta["layer"] == "cashout" or not dests:
            method = meta["cashout_method"] if meta["cashout_method"] in {"cash_withdrawal", "crypto_purchase"} else "cash_withdrawal"
            delay = int(rng.integers(5, 41))
            when = ts + pd.Timedelta(minutes=delay)
            if when >= sim_end:
                when = sim_end - pd.Timedelta(seconds=30)
            if when <= ts:
                continue
            purpose = "rut tien mat" if method == "cash_withdrawal" else "mua crypto"
            tx_type = "cash_withdrawal" if method == "cash_withdrawal" else "crypto_purchase"
            rows.append(make_tx(tx_ids[cursor], when, account, None, send, tx_type, purpose, pd.NA))
            cursor += 1
            continue
        delay = int(meta["delay"]) if meta["delay"] else int(rng.integers(2, 31))
        when = ts + pd.Timedelta(minutes=delay)
        if when >= sim_end:
            when = sim_end - pd.Timedelta(seconds=20)
        if when <= ts:
            continue
        base = send // len(dests)
        parts = [base] * (len(dests) - 1)
        parts.append(send - base * (len(dests) - 1))
        for dest, part in zip(dests, parts):
            if part < 1000:
                continue
            rows.append(make_tx(tx_ids[cursor], when, account, dest, int(part), "transfer", "chuyen tiep", pd.NA))
            cursor += 1
            queue.append((dest, int(part), when))
    return rows, cursor


def replay(transactions: pd.DataFrame, opening: dict[str, int], tracked: set[str]) -> pd.DataFrame:
    frame = transactions.sort_values(["ts", "tx_id"], kind="mergesort")
    sender = frame["from_account"].astype(str).to_numpy()
    raw_to = frame["to_account"]
    receiver = raw_to.astype(str).to_numpy()
    receiver[raw_to.isna().to_numpy()] = ""
    amount = frame["amount"].to_numpy().astype(np.int64)
    kind = frame["tx_type"].astype(str).to_numpy()
    balance = {account: int(opening.get(account, 0)) for account in tracked}
    keep = np.ones(len(frame), dtype=bool)
    before = np.zeros(len(frame), dtype=np.int64)
    new_amount = amount.copy()
    for index in range(len(frame)):
        source = sender[index]
        dest = receiver[index]
        amt = int(new_amount[index])
        if source in tracked:
            avail = balance[source]
            if amt > avail:
                if kind[index] in {"own_transfer_drain", "large_oneoff", "school_fee", "passthrough_out"}:
                    keep[index] = False
                    continue
                amt = int(avail // 1000) * 1000
                if amt < 1000:
                    keep[index] = False
                    continue
                new_amount[index] = amt
            before[index] = avail
            balance[source] = avail - amt
        else:
            before[index] = int(opening.get(source, 0))
            if amt <= 0:
                keep[index] = False
                continue
        if dest and dest in tracked:
            balance[dest] = balance.get(dest, int(opening.get(dest, 0))) + amt
    kept = frame.loc[keep].copy()
    kept["amount"] = new_amount[keep]
    kept["from_balance_before"] = before[keep]
    kept["amount"] = kept["amount"].astype(np.int64)
    kept["from_balance_before"] = kept["from_balance_before"].astype(np.int64)
    return kept.reset_index(drop=True)


def event_chain(tx_rows: list[dict], new_flags: list[bool], paste_flags: list[bool], fraud: bool, rng, sim_start, sim_end) -> list[tuple[str, pd.Timestamp]]:
    events: list[tuple[str, pd.Timestamp]] = []
    previous = sim_start
    login_done = False
    enter_median = 22.0 if fraud else 11.0
    for row, is_new, do_paste in zip(tx_rows, new_flags, paste_flags):
        submit = row["ts"]
        if submit >= sim_end:
            submit = sim_end - pd.Timedelta(seconds=1)
        steps = ["open_transfer"]
        if is_new:
            steps.append("add_payee")
            if do_paste:
                steps.append("paste_account")
        steps.extend(["enter_amount", "submit"])
        if not login_done:
            steps = ["login"] + steps
            login_done = True
        gaps = []
        for name in steps[:-1]:
            median = enter_median if name == "enter_amount" else 8.0
            gaps.append(float(lognormal_seconds(median, 1, rng)[0]))
        total = sum(gaps)
        room = (submit - previous).total_seconds() - 1.0
        if room < len(gaps) * 1.5:
            room = max(len(gaps) * 1.5, 1.0)
            submit = previous + pd.Timedelta(seconds=room + 1.0)
            if submit >= sim_end:
                submit = sim_end - pd.Timedelta(seconds=1)
        if total > room:
            scale = room / total
            gaps = [max(1.5, gap * scale) for gap in gaps]
        cursor = submit
        staged = [("submit", submit)]
        for name, gap in zip(reversed(steps[:-1]), reversed(gaps)):
            cursor = cursor - pd.Timedelta(seconds=gap)
            staged.append((name, cursor))
        staged.reverse()
        if staged[0][1] < sim_start:
            shift = sim_start + pd.Timedelta(seconds=1) - staged[0][1]
            staged = [(name, stamp + shift) for name, stamp in staged]
        events.extend(staged)
        previous = staged[-1][1]
    return events


def main() -> None:
    cfg = load_config()
    spec = cfg["sessions"]
    rng = np.random.default_rng(int(cfg["seed"]))
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"

    accounts = pd.read_parquet(observed / "accounts.parquet")
    transactions = reset_previous(pd.read_parquet(observed / "transactions.parquet"), accounts, observed, truth)
    links = pd.read_parquet(observed / "account_device.parquet")
    devices_latent = pd.read_parquet(truth / "devices_latent.parquet")
    contacts = pd.read_parquet(truth / "contacts.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet", columns=["number_id", "owner_type"])
    cases = pd.read_parquet(truth / "truth_cases.parquet")
    plans = pd.read_parquet(truth / "case_transfers_plan.parquet")
    case_contacts = pd.read_parquet(truth / "case_contacts.parquet")
    rings = pd.read_parquet(truth / "mule_rings.parquet")
    numbers = pd.read_parquet(truth / "truth_numbers.parquet")

    accounts["account_id"] = accounts["account_id"].astype(str)
    account_bank = dict(zip(accounts["account_id"], accounts["bank_id"].astype(str)))
    account_customer = dict(zip(accounts["account_id"], accounts["customer_id"].astype(str)))
    opening = dict(zip(accounts["account_id"], accounts["opening_balance"].astype(np.int64)))
    banks = set(spec["session_banks"])
    kinds = set(spec["session_txn_types"])
    legit_map = assign_legit_sessions(transactions, banks, kinds, account_bank, rng)
    transactions["session_id"] = transactions["tx_id"].astype(str).map(legit_map)
    transactions["session_id"] = transactions["session_id"].astype("string")

    by_customer = customer_accounts(accounts)
    devices = device_table(links)
    ring_of = {}
    for record in rings.itertuples(index=False):
        dests = record.forward_to
        dests = [] if dests is None else [str(item) for item in list(dests)]
        delay = record.forward_delay_minutes
        ring_of[str(record.account_id)] = {
            "layer": str(record.layer),
            "forward_to": dests,
            "delay": int(delay) if pd.notna(delay) else None,
            "cashout_method": None if pd.isna(record.cashout_method) else str(record.cashout_method),
        }
    mule_ids = set(ring_of)
    l1_ids = {account for account, meta in ring_of.items() if meta["layer"] == "l1"}
    home = str(spec["session_banks"][0])
    case_meta = cases.set_index("case_id")
    number_owner = rings  # silence lint
    _ = number_owner
    spoof_as = dict(zip(numbers["number_id"].astype(str), numbers["spoofed_as_number_id"].astype(str)))
    spoof_flag = dict(zip(numbers["number_id"].astype(str), numbers["spoofs_display"].astype(bool)))

    tx_ids = next_ids("T", transactions["tx_id"], 250_000)
    cursor = 0
    first_tx_num = int(transactions["tx_id"].astype(str).str[1:].astype(np.int64).max()) + 1
    extra = []
    fraud_sessions: dict[str, dict] = {}
    session_no = max((int(value[1:]) for value in legit_map.values()), default=0)

    def take_id() -> str:
        nonlocal cursor
        tx_id = tx_ids[cursor]
        cursor += 1
        return tx_id

    inbound_for_forward = []
    for record in plans.sort_values(["planned_ts", "case_id", "seq"], kind="mergesort").itertuples(index=False):
        case = case_meta.loc[record.case_id]
        customer = str(case.victim_customer_id)
        part = by_customer.get(customer)
        if part is None:
            continue
        personal = pick_account(part, home, "personal")
        on_b01 = personal is not None
        if personal is None:
            personal = pick_account(part, None, "personal")
        if personal is None:
            continue
        when = record.planned_ts
        if when < sim_start:
            when = sim_start + pd.Timedelta(minutes=5)
        if when >= sim_end:
            when = sim_end - pd.Timedelta(minutes=5)
        if int(record.planned_amount) < 0:
            extra.append(
                make_tx(
                    take_id(), when, str(record.to_mule_account_id), personal,
                    abs(int(record.planned_amount)), "transfer", "tra lai", pd.NA,
                )
            )
            continue
        if int(record.planned_amount) <= 0:
            continue
        session_id = pd.NA
        if on_b01:
            session_no += 1
            session_id = f"S{session_no:08d}"
            fraud_sessions[session_id] = {
                "customer_id": customer,
                "account_id": personal,
                "case_id": str(record.case_id),
                "scam_type": str(case.scam_type),
                "anchor": when,
            }
        if bool(record.from_own_savings_first):
            savings = pick_account(part, home, "savings") or pick_account(part, None, "savings")
            if savings and savings != personal:
                extra.append(
                    make_tx(
                        take_id(), when - pd.Timedelta(minutes=3), savings, personal,
                        int(record.planned_amount), "own_transfer", "chuyen tu tiet kiem", session_id,
                    )
                )
        purpose = str(record.coached_purpose_text)
        if rng.random() < 0.35:
            purpose = str(rng.choice(VAGUE))
        payment = make_tx(
            take_id(), when, personal, str(record.to_mule_account_id),
            int(record.planned_amount), "transfer", purpose, session_id,
        )
        payment["_case"] = str(record.case_id)
        payment["_invest"] = str(case.scam_type) == "investment"
        extra.append(payment)

    scale_investment(extra, transactions, opening)
    for row in extra:
        if row.get("_case") and int(row["amount"]) >= 1000 and str(row["to_account"]) in l1_ids and str(row["tx_type"]) == "transfer":
            inbound_for_forward.append((str(row["to_account"]), int(row["amount"]), row["ts"]))
    extra = [row for row in extra if int(row["amount"]) >= 1000]
    for row in extra:
        row.pop("_case", None)
        row.pop("_invest", None)

    forwards, cursor = forward_chain(inbound_for_forward, ring_of, rng, sim_end, tx_ids, cursor)
    extra.extend(forwards)
    if extra:
        transactions = pd.concat([transactions, pd.DataFrame(extra)], ignore_index=True)
    tracked = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str)) | mule_ids
    transactions = replay(transactions, opening, tracked)
    live_sessions = set(transactions["session_id"].dropna().astype(str))
    fraud_sessions = {key: value for key, value in fraud_sessions.items() if key in live_sessions}

    # Phiên.
    present = transactions.loc[transactions["session_id"].notna()].copy()
    present["session_id"] = present["session_id"].astype(str)
    session_rows = []
    for session_id, part in present.groupby("session_id", sort=False):
        part = part.sort_values(["ts", "tx_id"], kind="mergesort")
        account = str(part.iloc[-1]["from_account"])
        end = part["ts"].iloc[-1]
        device = device_at(devices, account, end) or device_at(devices, account, part["ts"].iloc[0])
        customer = account_customer.get(account, "")
        info = fraud_sessions.get(session_id)
        session_rows.append(
            {
                "session_id": session_id,
                "customer_id": info["customer_id"] if info else customer,
                "account_id": info["account_id"] if info else account,
                "device_id": device,
                "biometric_passed": True,
                "start_ts": part["ts"].iloc[0],
                "end_ts": end,
                "fraud": info is not None,
            }
        )
    sessions = pd.DataFrame(session_rows)
    sessions = sessions.loc[sessions["device_id"].notna()].reset_index(drop=True)
    kept_ids = set(sessions["session_id"].astype(str))
    transactions.loc[~transactions["session_id"].astype(str).isin(kept_ids), "session_id"] = pd.NA
    fraud_sessions = {key: value for key, value in fraud_sessions.items() if key in kept_ids}

    # Người nhận mới theo thứ tự thời gian.
    customer_of_tx = transactions["from_account"].astype(str).map(account_customer)
    seen_payee: set[tuple[str, str]] = set()
    is_new = []
    for customer, dest, source in zip(customer_of_tx, transactions["to_account"], transactions["from_account"].astype(str)):
        if pd.isna(dest) or not isinstance(customer, str) or customer in {"", "nan", "<NA>"}:
            is_new.append(False)
            continue
        key = (customer, str(dest))
        is_new.append(key not in seen_payee and str(dest) != source)
        seen_payee.add(key)
    transactions["_new_payee"] = is_new

    paste_legit = float(spec["paste_share_new_payee"]["legit"])
    paste_fraud = float(spec["paste_share_new_payee"]["fraud"])
    new_series = transactions.set_index("tx_id")["_new_payee"]
    event_rows = []
    session_new = {}
    session_paste = {}
    start_of = {}
    end_of = {}
    tx_by_session = {key: part.sort_values(["ts", "tx_id"], kind="mergesort") for key, part in present.groupby("session_id", sort=False)}
    for session in sessions.itertuples(index=False):
        part = tx_by_session.get(session.session_id)
        if part is None or part.empty:
            continue
        tx_rows = [
            {"ts": row.ts, "tx_id": str(row.tx_id), "tx_type": str(row.tx_type), "purpose_text": str(row.purpose_text)}
            for row in part.itertuples(index=False)
        ]
        flags = [bool(new_series.get(row["tx_id"], False)) for row in tx_rows]
        pastes = [bool(flag and rng.random() < (paste_fraud if session.fraud else paste_legit)) for flag in flags]
        chain = event_chain(tx_rows, flags, pastes, bool(session.fraud), rng, sim_start, sim_end)
        for seq, (name, stamp) in enumerate(chain, start=1):
            event_rows.append({"session_id": session.session_id, "seq": seq, "event_type": name, "ts": stamp})
        session_new[session.session_id] = any(flags)
        session_paste[session.session_id] = any(pastes)
        if chain:
            start_of[session.session_id] = chain[0][1]
            end_of[session.session_id] = chain[-1][1]
    sessions["start_ts"] = sessions["session_id"].map(start_of).fillna(sessions["start_ts"])
    sessions["end_ts"] = sessions["session_id"].map(end_of).fillna(sessions["end_ts"])

    events = pd.DataFrame(event_rows)
    # Cuộc gọi.
    contact_numbers = contacts.groupby(contacts["customer_id"].astype(str))["number_id"].apply(lambda values: set(values.astype(str))).to_dict()
    known_numbers = (
        contacts.loc[contacts["contact_type"].astype(str).isin(["family", "work"])]
        .groupby(contacts["customer_id"].astype(str))["number_id"]
        .apply(lambda values: values.astype(str).tolist())
        .to_dict()
    )
    friend_numbers = (
        contacts.loc[contacts["contact_type"].astype(str).isin(["friend", "family"])]
        .groupby(contacts["customer_id"].astype(str))["number_id"]
        .apply(lambda values: values.astype(str).tolist())
        .to_dict()
    )
    stranger_pool = phones.loc[phones["owner_type"].astype(str).isin(["business", "external_person"]), "number_id"].astype(str).to_numpy()
    legit = sessions.loc[~sessions["fraud"]].reset_index(drop=True)
    n_legit = len(legit)
    n_known = int(round(float(spec["legit_on_call_known"]) * n_legit))
    n_unknown = int(round(float(spec["legit_on_call_unknown"]) * n_legit))
    n_ott = int(round(float(spec["legit_ott_call"]) * n_legit))
    order = rng.permutation(n_legit)
    call_rows = []
    call_no = 1

    def add_call(session_id, when, duration, channel, number, direction="inbound"):
        nonlocal call_no
        call_rows.append(
            {
                "call_id": f"L{call_no:08d}",
                "session_id": session_id,
                "ts": when,
                "duration_s": int(duration),
                "channel": channel,
                "counterparty_number_id": number,
                "direction": direction,
            }
        )
        call_no += 1

    placed = {"known": 0, "unknown": 0, "ott": 0}
    for index in order:
        if placed["known"] >= n_known and placed["unknown"] >= n_unknown and placed["ott"] >= n_ott:
            break
        row = legit.iloc[int(index)]
        customer = str(row.customer_id)
        start = row.start_ts
        if placed["known"] < n_known:
            pool = known_numbers.get(customer) or []
            if pool:
                duration = int(rng.integers(40, 420))
                began = start - pd.Timedelta(seconds=int(rng.integers(0, 25)))
                add_call(row.session_id, began if began >= sim_start else sim_start, duration, "cellular", str(rng.choice(pool)), "outbound")
                placed["known"] += 1
                continue
        if placed["unknown"] < n_unknown and len(stranger_pool):
            mine = contact_numbers.get(customer, set())
            number = None
            for _ in range(8):
                candidate = str(stranger_pool[int(rng.integers(0, len(stranger_pool)))])
                if candidate not in mine:
                    number = candidate
                    break
            if number:
                duration = int(rng.integers(30, 240))
                began = start - pd.Timedelta(seconds=int(rng.integers(0, 20)))
                add_call(row.session_id, began if began >= sim_start else sim_start, duration, "cellular", number, "inbound")
                placed["unknown"] += 1
                continue
        if placed["ott"] < n_ott:
            pool = friend_numbers.get(customer) or known_numbers.get(customer) or []
            if pool:
                duration = int(rng.integers(30, 300))
                began = start - pd.Timedelta(seconds=int(rng.integers(0, 15)))
                add_call(row.session_id, began if began >= sim_start else sim_start, duration, "ott", str(rng.choice(pool)), "outbound")
                placed["ott"] += 1

    fraud_ids = set(fraud_sessions)
    case_contacts["case_id"] = case_contacts["case_id"].astype(str)
    for session_id, info in fraud_sessions.items():
        window = sessions.loc[sessions["session_id"] == session_id].iloc[0]
        start = window.start_ts
        end = window.end_ts
        related = case_contacts.loc[case_contacts["case_id"] == info["case_id"]]
        near = related.loc[(related["ts"] >= start - pd.Timedelta(hours=2)) & (related["ts"] <= end + pd.Timedelta(hours=2))]
        calls = near.loc[near["channel"].astype(str).isin(["cellular", "ott"])]
        hang = bool(case_meta.loc[info["case_id"]].hangs_up_before_transfer)
        spoofed = bool(case_meta.loc[info["case_id"]].spoofed)
        if calls.empty:
            continue
        first = True
        for call in calls.itertuples(index=False):
            number = str(call.number_id)
            if spoofed and spoof_flag.get(number) and isinstance(spoof_as.get(number), str) and spoof_as.get(number) not in {"", "nan", "<NA>"}:
                number = spoof_as[number]
            duration = int(call.duration_s) if int(call.duration_s) > 0 else 60
            when = call.ts
            if hang and first:
                when = start - pd.Timedelta(minutes=int(rng.integers(1, 21))) - pd.Timedelta(seconds=duration)
                first = False
            elif first and not hang:
                when = start - pd.Timedelta(seconds=int(rng.integers(5, 40)))
                first = False
            if when < sim_start:
                when = sim_start + pd.Timedelta(seconds=1)
            add_call(session_id, when, duration, str(call.channel), number, "inbound")

    calls = pd.DataFrame(call_rows) if call_rows else pd.DataFrame(
        columns=["call_id", "session_id", "ts", "duration_s", "channel", "counterparty_number_id", "direction"]
    )

    # Tín hiệu thiết bị.
    latent = devices_latent.copy()
    latent["device_id"] = latent["device_id"].astype(str)
    talkback = []
    password = []
    for value in latent["baseline_accessibility"]:
        items = [] if value is None else list(value)
        talkback.append("talkback" in items)
        password.append("password_manager" in items)
    latent["talkback"] = talkback
    latent["password"] = password
    latent_map = latent.set_index("device_id")
    bank_staff_ids = [key for key, info in fraud_sessions.items() if info["scam_type"] == "bank_staff"]
    n_remote = int(round(float(spec["bank_staff_remote_app"]) * len(bank_staff_ids)))
    remote_cases = set(rng.choice(bank_staff_ids, size=n_remote, replace=False).tolist()) if n_remote else set()
    signal_rows = []
    in_call = {}
    calls_by_session = {key: part for key, part in calls.groupby("session_id", sort=False)} if len(calls) else {}
    for session in sessions.itertuples(index=False):
        base = latent_map.loc[session.device_id] if session.device_id in latent_map.index else None
        talk = bool(base["talkback"]) if base is not None else False
        pw = bool(base["password"]) if base is not None else False
        remote = bool(base["remote_access_app_installed"]) if base is not None else False
        enabled = pd.NaT
        sharing = False
        overlay = False
        if session.session_id in remote_cases:
            remote = True
            enabled = session.start_ts - pd.Timedelta(hours=int(rng.integers(1, 7)))
            sharing = bool(rng.random() < 0.55)
            overlay = bool(rng.random() < 0.45)
        elif (not session.fraud) and rng.random() < 0.004:
            enabled = session.start_ts - pd.Timedelta(hours=int(rng.integers(1, 8)))
        related = calls_by_session.get(session.session_id, calls.iloc[0:0])
        active = False
        if len(related):
            finish = related["ts"] + pd.to_timedelta(related["duration_s"], unit="s")
            overlap = (related["ts"] < session.end_ts) & (finish > session.start_ts) & related["channel"].isin(["cellular", "ott"])
            active = bool(overlap.any())
        in_call[session.session_id] = active
        signal_rows.append(
            {
                "session_id": session.session_id,
                "device_id": session.device_id,
                "talkback_enabled": talk,
                "password_manager_installed": pw,
                "remote_access_app_installed": remote,
                "accessibility_enabled_since": enabled,
                "screen_sharing": sharing,
                "overlay_detected": overlay,
                "in_call_audio_mode": active,
            }
        )
    signals = pd.DataFrame(signal_rows)

    # Kịch bản và câu trả lời.
    lie_rate = float(spec["coached_lie_yes_no"])
    scenario_rows = []
    response_rows = []
    for session in sessions.itertuples(index=False):
        info = fraud_sessions.get(session.session_id)
        part = tx_by_session.get(session.session_id)
        if part is None:
            continue
        types = set(part["tx_type"].astype(str))
        on_call = bool(in_call.get(session.session_id, False))
        if info:
            scenario = info["scam_type"]
            case_id = info["case_id"]
            meta = case_meta.loc[case_id]
            split_count = int(plans.loc[(plans["case_id"] == case_id) & (plans["planned_amount"] > 0)].shape[0]) if bool(meta.split) else 1
            declines = bool(meta.declines_permission)
            hangs = bool(meta.hangs_up_before_transfer)
            channel = "none"
            related = calls_by_session.get(session.session_id, calls.iloc[0:0])
            if len(related):
                finish = related["ts"] + pd.to_timedelta(related["duration_s"], unit="s")
                live = related.loc[(related["ts"] < session.end_ts) & (finish > session.start_ts)]
                if (live["channel"] == "cellular").any():
                    channel = "cellular"
                elif (live["channel"] == "ott").any():
                    channel = "ott"
                elif hangs:
                    channel = str(related.iloc[0]["channel"])
            purpose_key = str(part.sort_values("amount", ascending=False)["purpose_text"].iloc[0])
            answer = PURPOSE_VI.get(purpose_key, "Chuyển khoản theo hướng dẫn")
            guided, police, secret = YES, (YES if scenario == "police" else NO), (YES if scenario in {"police", "bank_staff"} else NO)
            if rng.random() < lie_rate:
                guided, police, secret = (NO if guided == YES else YES), (NO if police == YES else YES), (NO if secret == YES else YES)
            permission = "decline" if declines else "accept"
        else:
            case_id = pd.NA
            split_count = int(len(part))
            declines = False
            hangs = False
            channel = "none"
            related = calls_by_session.get(session.session_id, calls.iloc[0:0])
            if len(related):
                channel = str(related.iloc[0]["channel"])
            if "own_transfer_drain" in types:
                scenario = "legit_drain"
            elif "passthrough_out" in types:
                scenario = "legit_passthrough"
            elif session_new.get(session.session_id) and (
                "large_oneoff" in types or int(part["amount"].max()) >= 20_000_000
            ):
                scenario = "legit_new_payee_large"
            elif on_call:
                scenario = "legit_on_call"
            else:
                scenario = "legit_normal"
            biggest = str(part.sort_values("amount", ascending=False)["tx_type"].iloc[0])
            answer = LEGIT_PURPOSE.get(biggest, "Chuyển khoản")
            guided, police, secret = NO, NO, NO
            permission = "accept"
        scenario_rows.append(
            {
                "session_id": session.session_id,
                "scenario_type": scenario,
                "case_id": case_id,
                "split_count": split_count,
                "declines_permission": declines,
                "hangs_up_before_transfer": hangs,
                "call_channel": channel,
            }
        )
        response_rows.append(
            {
                "session_id": session.session_id,
                "purpose_answer": answer,
                "yes_no_answers": json.dumps(
                    {"có ai hướng dẫn?": guided, "có xưng công an?": police, "có bắt giữ bí mật?": secret},
                    ensure_ascii=False,
                ),
                "permission_response": permission,
            }
        )

    scenarios = pd.DataFrame(scenario_rows)
    responses = pd.DataFrame(response_rows)
    sessions_out = sessions.drop(columns=["fraud"])
    for frame, cols in (
        (sessions_out, ["session_id", "customer_id", "account_id", "device_id"]),
        (events, ["session_id", "event_type"]),
        (signals, ["session_id", "device_id"]),
        (calls, ["call_id", "session_id", "channel", "counterparty_number_id", "direction"]),
        (scenarios, ["session_id", "scenario_type", "case_id", "call_channel"]),
        (responses, ["session_id", "purpose_answer", "yes_no_answers", "permission_response"]),
    ):
        for column in cols:
            if column in frame.columns:
                frame[column] = frame[column].astype("string")
    sessions_out["biometric_passed"] = sessions_out["biometric_passed"].astype(bool)
    events["seq"] = events["seq"].astype(np.int64)
    signals["talkback_enabled"] = signals["talkback_enabled"].astype(bool)
    signals["password_manager_installed"] = signals["password_manager_installed"].astype(bool)
    signals["remote_access_app_installed"] = signals["remote_access_app_installed"].astype(bool)
    signals["screen_sharing"] = signals["screen_sharing"].astype(bool)
    signals["overlay_detected"] = signals["overlay_detected"].astype(bool)
    signals["in_call_audio_mode"] = signals["in_call_audio_mode"].astype(bool)
    if len(calls):
        calls["duration_s"] = calls["duration_s"].astype(np.int64)
    scenarios["split_count"] = scenarios["split_count"].astype(np.int64)
    scenarios["declines_permission"] = scenarios["declines_permission"].astype(bool)
    scenarios["hangs_up_before_transfer"] = scenarios["hangs_up_before_transfer"].astype(bool)
    transactions = transactions.drop(columns=["_new_payee"], errors="ignore")
    transactions["tx_id"] = transactions["tx_id"].astype("string")
    transactions["from_account"] = transactions["from_account"].astype("string")
    transactions["to_account"] = transactions["to_account"].astype("string")
    transactions["tx_type"] = transactions["tx_type"].astype("string")
    transactions["purpose_text"] = transactions["purpose_text"].astype("string")
    transactions["channel"] = transactions["channel"].astype("string")
    transactions["session_id"] = transactions["session_id"].astype("string")
    transactions["amount"] = transactions["amount"].astype(np.int64)
    transactions["from_balance_before"] = transactions["from_balance_before"].astype(np.int64)

    write(transactions, "observed", "transactions")
    write(sessions_out, "observed", "sessions")
    write(events, "observed", "session_events")
    write(signals, "observed", "device_signals")
    write(calls, "observed", "call_events")
    write(scenarios, "truth", "truth_scenarios")
    write(responses, "truth", "user_responses")
    write(pd.DataFrame({"first_tx_num": [np.int64(first_tx_num)]}), "truth", "step6_floor")
    n_fraud = int(sessions["fraud"].sum()) if len(sessions) else 0
    print(
        f"Đã sinh {len(sessions_out)} phiên ({n_fraud} lừa đảo), {len(events)} bước, "
        f"{len(calls)} cuộc gọi, {len(transactions)} giao dịch."
    )
    print(f"  Cuộc gọi hợp lệ: quen {placed['known']}, lạ {placed['unknown']}, OTT {placed['ott']}.")


if __name__ == "__main__":
    main()
