"""Đối chiếu lịch sử giao dịch bước 3. Thoát mã 1 nếu có mục không đạt."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.limits import limit_report, over_limit_outcomes

TZ = "Asia/Ho_Chi_Minh"
SCHOOL_AGE = 22
# ts, channel trùng tên với lịch liên lạc bước 5; không phải nhãn mule.
KEYS = {
    "customer_id", "device_id", "number_id", "account_id", "ts", "channel",
    "session_id", "start_ts", "end_ts", "seq", "duration_s", "remote_access_app_installed",
    "segment", "source",
}
ANCHORS = (5, 10, 15, 25)


def load_config() -> dict:
    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def as_timestamp(value) -> pd.Timestamp:
    stamp = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(TZ)
    return stamp.tz_convert(TZ)


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str, bool]] = []

    def add(self, item: str, actual: str, expected: str, detail: str, ok: bool) -> None:
        self.rows.append((item, actual, expected, detail, ok))

    def ok(self) -> bool:
        return all(row[-1] for row in self.rows)

    def print(self) -> None:
        headers = ("Hạng mục", "Thực tế", "Mục tiêu", "Lệch", "Kết quả")
        table = [headers]
        for item, actual, expected, detail, passed in self.rows:
            table.append((item, actual, expected, detail, "ĐẠT" if passed else "LỖI"))
        widths = [max(len(row[col]) for row in table) for col in range(5)]
        print("  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)))
        print("  ".join("-" * width for width in widths))
        for row in table[1:]:
            print("  ".join(row[i].ljust(widths[i]) for i in range(5)))


def pct(value: float) -> str:
    return f"{100 * value:.2f}%"


def zone_name(series: pd.Series) -> str | None:
    tz = getattr(series.dt, "tz", None)
    if tz is None:
        return None
    return getattr(tz, "zone", None) or str(tz)


def in_window(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    if series.isna().any() or zone_name(series) != TZ:
        return False
    return bool(series.min() >= start and series.max() < end)


def replay(transactions: pd.DataFrame, accounts: pd.DataFrame, rng) -> tuple[int, int]:
    """Trả về (số tài khoản kiểm tra, số tài khoản lệch)."""
    owned = accounts.loc[accounts["customer_id"].notna(), ["account_id", "opening_balance"]]
    involved = set(transactions["from_account"].dropna().astype(str)) | set(transactions["to_account"].dropna().astype(str))
    owned = owned.loc[owned["account_id"].astype(str).isin(involved)]
    take = min(1000, len(owned))
    sample = owned.sample(n=take, random_state=int(rng.integers(0, 1_000_000)))
    wanted = set(sample["account_id"].astype(str))
    opening = dict(zip(sample["account_id"].astype(str), sample["opening_balance"].astype(np.int64)))
    sub = transactions.loc[
        transactions["from_account"].astype(str).isin(wanted) | transactions["to_account"].astype(str).isin(wanted),
        ["tx_id", "ts", "from_account", "to_account", "amount", "from_balance_before"],
    ].copy()
    sub["from_account"] = sub["from_account"].astype(str)
    sub["to_account"] = sub["to_account"].astype(str)
    sub = sub.sort_values(["ts", "tx_id"], kind="mergesort")
    balance = {account: int(value) for account, value in opening.items()}
    bad_accounts: set[str] = set()
    seen: set[str] = set()
    for row in sub.itertuples(index=False):
        sender = row.from_account
        receiver = row.to_account
        amount = int(row.amount)
        if sender in balance:
            seen.add(sender)
            if int(row.from_balance_before) != balance[sender] or amount > balance[sender]:
                bad_accounts.add(sender)
                balance.pop(sender, None)
            else:
                balance[sender] -= amount
        if receiver in balance and receiver != sender:
            seen.add(receiver)
            balance[receiver] += amount
    missed = set(opening) - seen
    return take, len(bad_accounts | missed)


def replay_subset(transactions: pd.DataFrame, accounts: pd.DataFrame, wanted: set[str]) -> int:
    """Số dòng lệch số dư chạy trên đúng các tài khoản được nêu."""
    if not wanted:
        return 0
    opening = dict(
        zip(
            accounts.loc[accounts["account_id"].astype(str).isin(wanted), "account_id"].astype(str),
            accounts.loc[accounts["account_id"].astype(str).isin(wanted), "opening_balance"].astype(np.int64),
        )
    )
    balance = dict(opening)
    sub = transactions.loc[
        transactions["from_account"].astype(str).isin(wanted) | transactions["to_account"].astype(str).isin(wanted)
    ].sort_values(["ts", "tx_id"], kind="mergesort")
    bad = 0
    for row in sub.itertuples(index=False):
        sender = str(row.from_account)
        receiver = None if pd.isna(row.to_account) else str(row.to_account)
        amount = int(row.amount)
        if sender in balance:
            if int(row.from_balance_before) != balance[sender] or amount > balance[sender]:
                bad += 1
                balance.pop(sender, None)
            else:
                balance[sender] -= amount
        if receiver in balance and receiver != sender:
            balance[receiver] = balance.get(receiver, 0) + amount
    return bad


def main() -> None:
    cfg = load_config()
    hist = cfg["history"]
    rng = np.random.default_rng(int(cfg["seed"]))
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    ref_year = int(sim_start.year)
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    customers = pd.read_parquet(observed / "customers.parquet")
    latent = pd.read_parquet(truth / "customers_latent.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    transactions = pd.read_parquet(observed / "transactions.parquet")
    payees = pd.read_parquet(observed / "payees.parquet")
    contacts = pd.read_parquet(truth / "contacts.parquet")
    ledger = transactions
    floor_path = truth / "step6_floor.parquet"
    if floor_path.exists() and ledger["session_id"].notna().any():
        floor = int(pd.read_parquet(floor_path)["first_tx_num"].iloc[0])
        numbers = ledger["tx_id"].astype(str).str[1:].astype(np.int64)
        transactions = ledger.loc[numbers < floor].copy()
    guardians = pd.read_parquet(observed / "guardians.parquet")
    report = Report()

    nonneg = bool((ledger["from_balance_before"] >= 0).all() and (ledger["amount"] > 0).all())
    customer_accounts = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str))
    outbound = ledger.loc[ledger["from_account"].astype(str).isin(customer_accounts)]
    covered = bool((outbound["from_balance_before"] >= outbound["amount"]).all())
    checked, bad = replay(ledger, accounts, rng)
    report.add(
        "Không số dư âm, from_balance_before khớp số dư chạy",
        f"{bad} lệch / {checked} tài khoản" if bad else f"0 lệch / {checked} tài khoản",
        "0 lệch trên 1.000 tài khoản khách",
        "from_balance_before ≥ amount" if covered and nonneg else "số dư âm hoặc thiếu tiền",
        nonneg and covered and bad == 0,
    )

    owner = accounts.loc[accounts["customer_id"].notna(), ["account_id", "customer_id"]]
    owner["account_id"] = owner["account_id"].astype(str)
    owner["customer_id"] = owner["customer_id"].astype(str)
    acc_cust = dict(zip(owner["account_id"], owner["customer_id"]))
    touch = pd.concat(
        [
            transactions[["tx_id", "ts", "from_account"]].rename(columns={"from_account": "account_id"}),
            transactions[["tx_id", "ts", "to_account"]].rename(columns={"to_account": "account_id"}),
        ],
        ignore_index=True,
    )
    touch["account_id"] = touch["account_id"].astype(str)
    touch["customer_id"] = touch["account_id"].map(acc_cust)
    touch = touch.dropna(subset=["customer_id"]).drop_duplicates(["customer_id", "tx_id"])
    touch["month"] = touch["ts"].dt.month
    counts = touch.groupby(["customer_id", "month"], sort=False).size()
    full_index = pd.MultiIndex.from_product([customers["customer_id"].astype(str), [7, 8, 9]], names=["customer_id", "month"])
    counts = counts.reindex(full_index, fill_value=0).astype(float)
    p10, p50, p90 = np.percentile(counts.to_numpy(), [10, 50, 90])
    cv = float(counts.std() / counts.mean()) if float(counts.mean()) else 0.0
    print()
    print("Tham khảo (không tính ĐẠT/LỖI)")
    print(f"  Giao dịch/khách/tháng: p10 {p10:.1f}, p50 {p50:.1f}, p90 {p90:.1f}, CV {cv:.2f}")
    report.add("Hệ số biến thiên giao dịch/khách/tháng", f"{cv:.2f}", "> 0.5", f"p10/p50/p90 {p10:.0f}/{p50:.0f}/{p90:.0f}", cv > 0.5)

    people = customers[["customer_id", "segment"]].merge(latent[["customer_id", "income_monthly"]], on="customer_id")
    people["customer_id"] = people["customer_id"].astype(str)
    salary = transactions.loc[transactions["tx_type"] == "salary_in", ["to_account", "amount", "ts"]].copy()
    salary["customer_id"] = salary["to_account"].astype(str).map(acc_cust)
    salary = salary.merge(people, on="customer_id", how="left")
    regular = salary.loc[salary["segment"] == "thường"]
    ratio = regular["amount"] / regular["income_monthly"]
    med = float(ratio.median()) if len(ratio) else 0.0
    day = salary["ts"].dt.day.to_numpy()
    nearest = np.min(np.abs(day[:, None] - np.array(ANCHORS)[None, :]), axis=1)
    cycle_ok = bool(len(salary) and (nearest <= 2).all())
    report.add(
        "Lương khách thường / thu nhập",
        f"trung vị {med:.2f}x",
        "1.0x ±10%",
        "đúng ngày 5/10/15/25 ±2" if cycle_ok else "lệch chu kỳ",
        abs(med - 1.0) <= 0.10 and cycle_ok,
    )

    hour = transactions["ts"].dt.hour.to_numpy()
    hist_hour = np.bincount(hour, minlength=24)
    print("  Histogram giờ:")
    peak = int(hist_hour.max()) if len(hist_hour) else 1
    for label, count in enumerate(hist_hour):
        bar = "#" * int(round(32 * count / peak)) if peak else ""
        print(f"    {label:02d}h {int(count):8d} {bar}")
    night = float(hist_hour[:6].sum() / hist_hour.sum())
    noon_peak = float(hist_hour[11:14].mean() > hist_hour.mean() and hist_hour[11:14].max() >= hist_hour[7:11].max())
    eve_peak = float(hist_hour[19:23].mean() > hist_hour.mean() and hist_hour[19:23].max() >= hist_hour[14:19].max())
    report.add(
        "Phân bố giờ",
        f"0–6h {pct(night)}",
        "đỉnh trưa và tối, 0–6h trong 3–8%",
        f"trưa {'có' if noon_peak else 'không'}, tối {'có' if eve_peak else 'không'}",
        0.03 <= night <= 0.08 and bool(noon_peak) and bool(eve_peak),
    )
    smooth_bad = []
    for hour_i in range(24):
        # 6–8h là bậc sáng. 20–22h và 0–1h dốc xuống đáy đêm để 0–6h còn 3–8%.
        if hour_i in (0, 1, 6, 7, 8, 20, 21, 22):
            continue
        for neighbor in ((hour_i - 1) % 24, (hour_i + 1) % 24):
            if hist_hour[hour_i] > hist_hour[neighbor] * 1.25:
                smooth_bad.append(hour_i)
                break
    others = np.concatenate([hist_hour[:3], hist_hour[5:]])
    lowest_ok = int(hist_hour[3]) <= int(others.min()) and int(hist_hour[4]) <= int(others.min())
    report.add(
        "Giờ liền kề và đáy đêm",
        f"3h {int(hist_hour[3])}, 4h {int(hist_hour[4])}",
        "≤ 25% liền kề, trừ 6–8h; 3–4h thấp nhất",
        "mượt" if not smooth_bad else "vượt ở " + ",".join(str(item) for item in smooth_bad),
        not smooth_bad and lowest_ok,
    )

    round_rate = float((transactions["amount"] % 1000 == 0).mean())
    target_round = float(hist["round_amount_share"])
    report.add(
        "Số tiền tròn nghìn",
        pct(round_rate),
        pct(target_round),
        f"{100 * (round_rate - target_round):+.2f} đ.p",
        abs(round_rate - target_round) <= 0.03,
    )

    age = ref_year - customers["birth_year"]
    household = latent[["customer_id", "family_group_id", "role"]].merge(customers[["customer_id"]], on="customer_id")
    household["age"] = household["customer_id"].map(dict(zip(customers["customer_id"], age)))
    school_child = household.loc[(household["role"] == "child") & (household["age"] <= SCHOOL_AGE), "family_group_id"]
    heads = household.loc[household["role"] == "head", ["customer_id", "family_group_id"]]
    school_heads = heads.loc[heads["family_group_id"].isin(set(school_child))]
    fee_customers = set(transactions.loc[transactions["tx_type"] == "school_fee", "from_account"].astype(str).map(acc_cust).dropna())
    paid = school_heads["customer_id"].astype(str).isin(fee_customers)
    school_rate = float(paid.mean()) if len(school_heads) else 0.0
    n_customers = len(customers)
    large_customers = transactions.loc[transactions["tx_type"] == "large_oneoff", "from_account"].astype(str).map(acc_cust).nunique()
    drain_customers = transactions.loc[transactions["tx_type"] == "own_transfer_drain", "from_account"].astype(str).map(acc_cust).nunique()
    pass_customers = transactions.loc[transactions["tx_type"] == "passthrough_out", "from_account"].astype(str).map(acc_cust).nunique()
    large_rate = large_customers / n_customers
    drain_rate = drain_customers / n_customers
    pass_rate = pass_customers / n_customers
    drain_rows = transactions.loc[transactions["tx_type"] == "own_transfer_drain"]
    drain_deep = bool(len(drain_rows) and (drain_rows["amount"] > drain_rows["from_balance_before"] * 0.80).all())
    report.add(
        "Học phí hộ có con đi học",
        pct(school_rate),
        pct(float(hist["school_fee_parent_share"])),
        f"{int(paid.sum())}/{len(school_heads)} hộ",
        abs(school_rate - float(hist["school_fee_parent_share"])) <= 0.05 and int(paid.sum()) > 0,
    )
    report.add(
        "large_oneoff",
        pct(large_rate),
        pct(float(hist["large_oneoff_share"])),
        f"{large_customers} khách",
        large_customers > 0 and abs(large_rate - float(hist["large_oneoff_share"])) <= 0.0015,
    )
    report.add(
        "own_transfer_drain",
        pct(drain_rate),
        pct(float(hist["own_transfer_drain_share"])),
        "> 80% số dư" if drain_deep else "không đủ sâu",
        drain_customers > 0 and drain_deep and abs(drain_rate - float(hist["own_transfer_drain_share"])) <= 0.005,
    )
    report.add(
        "legit_passthrough",
        pct(pass_rate),
        pct(float(hist["legit_passthrough_share"])),
        f"{pass_customers} khách",
        pass_customers > 0 and abs(pass_rate - float(hist["legit_passthrough_share"])) <= 0.002,
    )

    pair_old = payees.rename(columns={"from_account": "from_account", "to_account": "to_account"})
    pair_old["new_payee"] = pair_old["added_at"] >= sim_start
    linked = transactions.loc[transactions["to_account"].notna(), ["from_account", "to_account", "tx_type"]].copy()
    linked["from_account"] = linked["from_account"].astype(str)
    linked["to_account"] = linked["to_account"].astype(str)
    flags = pair_old.copy()
    flags["from_account"] = flags["from_account"].astype(str)
    flags["to_account"] = flags["to_account"].astype(str)
    linked = linked.merge(flags[["from_account", "to_account", "new_payee"]], on=["from_account", "to_account"], how="left")
    print("  Tỷ lệ người nhận mới theo loại giao dịch:")
    new_rates = {}
    for name, part in linked.groupby("tx_type", sort=True):
        known = part["new_payee"].notna()
        if not known.any():
            print(f"    {name}: không tạo payee (chiều nhận)")
            continue
        rate = float(part.loc[known, "new_payee"].mean())
        new_rates[str(name)] = rate
        print(f"    {name}: {pct(rate)}")
    school_new = new_rates.get("school_fee", 0.0)
    large_new = new_rates.get("large_oneoff", 0.0)
    school_tx = transactions.loc[transactions["tx_type"] == "school_fee", ["from_account", "to_account", "ts", "amount"]].copy()
    school_tx["from_account"] = school_tx["from_account"].astype(str)
    school_tx["to_account"] = school_tx["to_account"].astype(str)
    school_tx["payer"] = school_tx["from_account"].map(acc_cust)
    cust_family = dict(zip(latent["customer_id"].astype(str), latent["family_group_id"].astype(str)))
    school_tx["family"] = school_tx["payer"].map(cust_family)
    paying_families = set(school_tx["family"].dropna())
    child_rows = household.loc[(household["role"] == "child") & (household["age"] <= SCHOOL_AGE)].copy()
    child_rows["family_group_id"] = child_rows["family_group_id"].astype(str)
    paying_children = child_rows.loc[child_rows["family_group_id"].isin(paying_families)]
    window_days = max((sim_end - sim_start).days, 1)
    per_child_year = (len(school_tx) / len(paying_children) * (365 / window_days)) if len(paying_children) else 0.0
    report.add(
        "Học phí tới người nhận mới",
        pct(school_new),
        "5–15%",
        f"{per_child_year:.2f} lần/con/năm",
        0.05 <= school_new <= 0.15 and len(school_tx) > 0,
    )
    payee_added = payees.copy()
    payee_added["from_account"] = payee_added["from_account"].astype(str)
    payee_added["to_account"] = payee_added["to_account"].astype(str)
    school_pairs = school_tx.merge(payee_added[["from_account", "to_account", "added_at"]], on=["from_account", "to_account"], how="left")
    new_schools = school_pairs.loc[school_pairs["added_at"] >= sim_start, ["from_account", "to_account", "added_at"]].drop_duplicates(["from_account", "to_account"])
    new_months = new_schools["added_at"].dt.month if len(new_schools) else pd.Series(dtype=int)
    aug_oct = float(new_months.isin([8, 9, 10]).mean()) if len(new_months) else 0.0
    print("  Người nhận trường mới theo tháng:")
    if len(new_months):
        for month, count in new_months.value_counts().sort_index().items():
            print(f"    tháng {int(month)}: {int(count)}")
    else:
        print("    không có")
    print(f"  Học phí: {per_child_year:.2f} lần/con/năm, người nhận mới {pct(school_new)}")
    report.add(
        "Người nhận trường mới trong tháng 8–10",
        pct(aug_oct),
        "≥ 70%",
        f"{len(new_schools)} người nhận mới",
        len(new_schools) > 0 and aug_oct >= 0.70,
    )
    n_aug = int((new_months == 8).sum()) if len(new_months) else 0
    n_sep = int((new_months == 9).sum()) if len(new_months) else 0
    report.add(
        "Người nhận trường mới ở tháng 8 và 9",
        f"tháng 8: {n_aug}, tháng 9: {n_sep}",
        "cả hai tháng",
        "-",
        n_aug > 0 and n_sep > 0,
    )
    fee_amounts = school_tx["amount"].to_numpy().astype(np.int64)
    n_fee_levels = int(pd.Series(fee_amounts).nunique()) if len(fee_amounts) else 0
    fee_cv = float(fee_amounts.std() / fee_amounts.mean()) if len(fee_amounts) and fee_amounts.mean() else 0.0
    report.add(
        "Học phí nhiều mức tiền",
        f"{n_fee_levels} mức, CV {fee_cv:.2f}",
        "> 50 mức, CV > 0.4",
        "-",
        n_fee_levels > 50 and fee_cv > 0.4,
    )
    if len(school_tx):
        day_share = float(school_tx["ts"].dt.floor("D").value_counts(normalize=True).max())
        busiest = school_tx["ts"].dt.floor("D").value_counts().index[0]
        busiest_label = str(pd.Timestamp(busiest).date())
    else:
        day_share = 1.0
        busiest_label = "-"
    report.add(
        "Học phí không dồn một ngày",
        pct(day_share),
        "≤ 15%",
        busiest_label,
        len(school_tx) > 0 and day_share <= 0.15,
    )
    schools_per_family = school_tx.groupby("family")["to_account"].nunique()
    children_per_family = child_rows.groupby("family_group_id").size()
    compared = schools_per_family.to_frame("schools").join(children_per_family.rename("children"), how="left")
    too_many = int((compared["schools"] > compared["children"].fillna(0)).sum())
    report.add(
        "Mỗi con một trường tại một thời điểm",
        f"{too_many} hộ vượt",
        "0",
        "-",
        too_many == 0,
    )
    report.add(
        "large_oneoff tới người nhận mới",
        pct(large_new),
        "> 80%",
        "-",
        large_new > 0.80,
    )

    methods = payees["add_method"].value_counts(normalize=True)
    method_ok = True
    for name, target in hist["payee_add_method"].items():
        actual = float(methods.get(name, 0.0))
        ok = abs(actual - float(target)) <= 0.02
        method_ok = method_ok and ok
        report.add(f"add_method {name}", pct(actual), pct(float(target)), f"{100 * (actual - float(target)):+.2f} đ.p", ok)

    time_ok = in_window(ledger["ts"], sim_start, sim_end)
    other_time = zone_name(payees["added_at"]) == TZ and zone_name(contacts["first_contact"]) == TZ
    other_time = other_time and bool(payees["added_at"].max() < sim_end) and bool(contacts["first_contact"].max() < sim_end)
    report.add("ts giao dịch trong cửa sổ, giờ VN", "đạt" if time_ok else "lỗi", "đạt", "-", time_ok and other_time)
    sessions_path = observed / "sessions.parquet"
    filled = ledger["session_id"].notna()
    if filled.any() and sessions_path.exists():
        known_sessions = set(pd.read_parquet(sessions_path, columns=["session_id"])["session_id"].astype(str))
        session_ok = bool(ledger.loc[filled, "session_id"].astype(str).isin(known_sessions).all())
        session_label = f"{int(filled.sum())} giao dịch có phiên"
        session_target = "session_id thuộc observed/sessions"
    else:
        session_ok = bool(ledger["session_id"].isna().all())
        session_label = "null" if session_ok else "có giá trị"
        session_target = "null"
    report.add("session_id trống", session_label, session_target, "-", session_ok)
    money_ok = str(ledger["amount"].dtype) == "int64" and str(ledger["from_balance_before"].dtype) == "int64"
    report.add("Tiền là int64 VND", f"{ledger['amount'].dtype}, {ledger['from_balance_before'].dtype}", "int64", "-", money_ok)

    fk_ok = bool(ledger["from_account"].astype(str).isin(accounts["account_id"].astype(str)).all())
    to_id = ledger["to_account"].dropna().astype(str)
    fk_ok = fk_ok and bool(to_id.isin(accounts["account_id"].astype(str)).all())
    fk_ok = fk_ok and bool(payees["from_account"].astype(str).isin(accounts["account_id"].astype(str)).all())
    fk_ok = fk_ok and bool(payees["to_account"].astype(str).isin(accounts["account_id"].astype(str)).all())
    fk_ok = fk_ok and bool(contacts["customer_id"].astype(str).isin(customers["customer_id"].astype(str)).all())
    fk_ok = fk_ok and bool(contacts["number_id"].astype(str).isin(phones["number_id"].astype(str)).all())
    report.add("Khóa ngoại", "đạt" if fk_ok else "lỗi", "đạt", "-", fk_ok)

    observed_cols: set[str] = set()
    truth_cols: set[str] = set()
    for path in observed.glob("*.parquet"):
        observed_cols |= set(pd.read_parquet(path).columns)
    for path in truth.glob("*.parquet"):
        truth_cols |= set(pd.read_parquet(path).columns)
    overlap = observed_cols & truth_cols
    report.add(
        "Cột observed ∩ truth",
        "{" + ", ".join(sorted(overlap)) + "}",
        "chỉ khóa",
        "không còn relation",
        overlap <= KEYS and "customer_id" in overlap,
    )

    changes = pd.read_parquet(observed / "account_changes.parquet")
    calls = pd.read_parquet(observed / "bank_calls.parquet")
    devices = pd.read_parquet(observed / "devices.parquet")
    limits = limit_report(transactions, accounts, changes)
    split_rate = limits["split"] / limits["outbound"] if limits["outbound"] else 0.0
    print(f"  Giao dịch hợp lệ bị chia do hạn mức: {limits['split']}/{limits['outbound']} ({split_rate:.2%})")
    report.add("Không giao dịch khách vượt hạn mức hiện hành", f"{limits['violations']} lệnh", "0", "-", limits["violations"] == 0)

    n_customers = len(customers)
    change_customer = changes.merge(
        accounts.loc[accounts["customer_id"].notna(), ["account_id", "customer_id"]].astype(str),
        on="account_id",
        how="left",
    )
    raise_rows = change_customer.loc[change_customer["change_type"].astype(str).eq("limit_raise")]
    phone_rows = change_customer.loc[change_customer["change_type"].astype(str).eq("phone_change")]
    device_rows = changes.loc[changes["change_type"].astype(str).eq("new_device")]
    raise_rate = raise_rows["customer_id"].nunique() / n_customers
    phone_rate = phone_rows["customer_id"].nunique() / n_customers
    sme_ids = set(
        accounts.loc[
            accounts["customer_id"].astype(str).isin(set(customers.loc[customers["segment"].astype(str).eq("doanh nghiệp nhỏ"), "customer_id"].astype(str))),
            "account_id",
        ].astype(str)
    )
    outcomes = over_limit_outcomes(transactions, accounts, changes, sme_ids)
    over_rate = outcomes["over_split"] / outcomes["over"] if outcomes["over"] else 0.0
    sme_split = outcomes["over_sme_split"] / outcomes["over_sme"] if outcomes["over_sme"] else 0.0
    print(
        f"  Lệnh vượt hạn mức bị chia: {outcomes['over_split']}/{outcomes['over']} ({pct(over_rate)}); "
        f"SME {outcomes['over_sme_split']}/{outcomes['over_sme']} ({pct(sme_split)})"
    )
    report.add(
        "Chia nhỏ trong lệnh vượt hạn mức",
        pct(over_rate),
        "20–30%",
        f"SME {pct(sme_split)}",
        outcomes["over"] > 0 and 0.20 <= over_rate <= 0.30,
    )
    report.add(
        "Nâng hạn mức trong cửa sổ",
        pct(raise_rate),
        pct(float(hist["limit_raise_share"])),
        f"{raise_rows['customer_id'].nunique()} khách",
        abs(raise_rate - float(hist["limit_raise_share"])) <= 0.025,
    )
    large_tx = transactions.loc[
        (transactions["amount"] >= 20_000_000)
        | transactions["tx_type"].astype(str).isin(["large_oneoff", "school_fee", "savings_liquidation"]),
        ["from_account", "ts"],
    ].copy()
    large_tx["from_account"] = large_tx["from_account"].astype(str)
    large_tx["ts"] = large_tx["ts"].dt.as_unit("ns")
    large_at = {
        account: part["ts"].sort_values().astype("int64").to_numpy()
        for account, part in large_tx.groupby("from_account", sort=False)
    }
    gaps_s: list[float] = []
    quiet = 0
    quick = 0
    for record in raise_rows.itertuples(index=False):
        later = large_at.get(str(record.account_id))
        raise_ns = np.int64(pd.Timestamp(record.ts).as_unit("ns").value)
        if later is None or len(later) == 0:
            quiet += 1
            continue
        after = later[later > raise_ns]
        if len(after) == 0:
            quiet += 1
            continue
        gap_s = (int(after[0]) - int(raise_ns)) / 1e9
        gaps_s.append(gap_s)
        if gap_s > 7 * 86400:
            quiet += 1
        if gap_s < 30 * 60:
            quick += 1
    gap_hours = np.array(gaps_s) / 3600 if gaps_s else np.array([])
    if len(gap_hours):
        g10, g50, g90 = np.percentile(gap_hours, [10, 50, 90])
        gap_label = f"p10 {g10:.1f}h, p50 {g50:.1f}h, p90 {g90:.1f}h"
    else:
        gap_label = "không có"
    quiet_rate = quiet / max(len(raise_rows), 1)
    quick_rate = quick / max(len(raise_rows), 1)
    print(f"  Khoảng cách nâng hạn mức → giao dịch lớn đầu tiên: {gap_label}")
    print(f"  Không chi lớn trong 7 ngày sau nâng: {pct(quiet_rate)}; dưới 30 phút: {pct(quick_rate)}")
    report.add(
        "Nâng hạn mức không chi lớn trong 7 ngày",
        pct(quiet_rate),
        pct(float(hist["limit_raise_idle_share"])),
        "10–20%",
        0.10 <= quiet_rate <= 0.20,
    )
    report.add("Nâng hạn mức rồi chi lớn dưới 30 phút", pct(quick_rate), "≥ 5%", gap_label, quick_rate >= 0.05)
    report.add(
        "Đổi số điện thoại",
        pct(phone_rate),
        pct(float(hist["phone_change_share"])),
        f"{phone_rows['customer_id'].nunique()} khách",
        abs(phone_rate - float(hist["phone_change_share"])) <= 0.0015,
    )
    window_devices = devices.loc[
        devices["customer_id"].notna() & (devices["first_seen"] >= sim_start) & (devices["first_seen"] < sim_end),
        "device_id",
    ].astype(str)
    device_match = set(device_rows["new_value"].astype(str)) == set(window_devices)
    report.add(
        "Thiết bị mới khớp bước 2",
        f"{device_rows['new_value'].nunique()} dòng",
        f"{window_devices.nunique()} máy",
        "-",
        device_match and len(device_rows) == int(window_devices.nunique()),
    )

    savings_accounts = accounts.loc[(accounts["account_type"].astype(str) == "savings") & accounts["customer_id"].notna(), "account_id"].astype(str)
    liquid = transactions.loc[transactions["tx_type"].astype(str).eq("savings_liquidation")].copy()
    mature = liquid.loc[liquid["purpose_text"].astype(str).eq("tat toan dao han"), "from_account"].astype(str).nunique()
    early = liquid.loc[liquid["purpose_text"].astype(str).eq("tat toan truoc han"), "from_account"].astype(str).nunique()
    n_savings = max(len(savings_accounts), 1)
    mature_rate = mature / n_savings
    early_rate = early / n_savings
    holder = dict(zip(accounts["account_id"].astype(str), accounts["customer_id"].astype(str)))
    kind_of = dict(zip(accounts["account_id"].astype(str), accounts["account_type"].astype(str)))
    same_owner = liquid["from_account"].astype(str).map(holder).eq(liquid["to_account"].astype(str).map(holder))
    to_personal = liquid["to_account"].astype(str).map(kind_of).eq("personal")
    from_savings = liquid["from_account"].astype(str).map(kind_of).eq("savings")
    report.add(
        "Tất toán đáo hạn",
        pct(mature_rate),
        pct(float(hist["savings_maturity_share"])),
        f"{mature}/{len(savings_accounts)} sổ",
        abs(mature_rate - float(hist["savings_maturity_share"])) <= 0.005 and bool(from_savings.all() and to_personal.all() and same_owner.all()),
    )
    report.add(
        "Tất toán trước hạn",
        pct(early_rate),
        pct(float(hist["savings_early_share"])),
        f"{early} sổ",
        abs(early_rate - float(hist["savings_early_share"])) <= 0.0015,
    )
    loan_ids = set(transactions.loc[transactions["tx_type"].astype(str).eq("quick_loan_disbursement"), "to_account"].astype(str).map(acc_cust).dropna())
    card_ids = set(transactions.loc[transactions["tx_type"].astype(str).eq("credit_card_cash_advance"), "to_account"].astype(str).map(acc_cust).dropna())
    loan_customers = len(loan_ids)
    card_customers = len(card_ids)
    print(f"  Khách trùng vay nhanh và rút thẻ: {len(loan_ids & card_ids)}")
    loan_rate = loan_customers / n_customers
    card_rate = card_customers / n_customers
    report.add("Vay nhanh", pct(loan_rate), pct(float(hist["quick_loan_share"])), f"{loan_customers} khách", abs(loan_rate - float(hist["quick_loan_share"])) <= 0.0015)
    report.add("Rút tiền thẻ", pct(card_rate), pct(float(hist["card_cash_advance_share"])), f"{card_customers} khách", abs(card_rate - float(hist["card_cash_advance_share"])) <= 0.0015)
    watched = set(liquid["from_account"].astype(str)) | set(liquid["to_account"].astype(str))
    watched |= set(transactions.loc[transactions["tx_type"].isin(["quick_loan_disbursement", "credit_card_cash_advance"]), "to_account"].astype(str))
    balance_bad = replay_subset(transactions, accounts, watched)
    report.add("Số dư sổ, vay nhanh và rút thẻ", f"{balance_bad} lệch", "0", "from_balance_before khớp sổ", balance_bad == 0)

    external = accounts.loc[
        accounts["customer_id"].isna() & accounts["phone_number_id"].notna() & (accounts["account_type"].astype(str) == "personal"),
        ["account_id", "bank_id"],
    ].copy()
    external["account_id"] = external["account_id"].astype(str)
    b01_accounts = set(accounts.loc[accounts["bank_id"].astype(str).eq("B01") & accounts["customer_id"].notna(), "account_id"].astype(str))
    paid_in = transactions.loc[transactions["to_account"].astype(str).isin(set(external["account_id"])), ["from_account", "to_account", "ts"]].copy()
    paid_in["from_account"] = paid_in["from_account"].astype(str)
    paid_in["to_account"] = paid_in["to_account"].astype(str)
    paid_in = paid_in.loc[paid_in["from_account"].isin(b01_accounts)]
    paid_in["month"] = paid_in["ts"].dt.month
    monthly = paid_in.groupby(["to_account", "month"], sort=False)["from_account"].nunique()
    popular = monthly.groupby("to_account").median()
    popular = popular.loc[popular >= 5]
    pop_rate = len(popular) / max(len(external), 1)
    sender_values = monthly.loc[monthly.index.get_level_values(0).isin(popular.index)].to_numpy() if len(popular) else np.array([])
    if len(sender_values):
        p10, p50, p90 = np.percentile(sender_values, [10, 50, 90])
        sender_label = f"p10 {p10:.0f}, p50 {p50:.0f}, p90 {p90:.0f}"
    else:
        p10 = p50 = p90 = 0.0
        sender_label = "không có"
    opened = accounts.set_index(accounts["account_id"].astype(str))["opened_at"]
    account_age = (sim_start - opened.reindex(popular.index)).dt.total_seconds() / 86400
    if len(account_age):
        a10, a50, a90 = np.percentile(account_age.to_numpy(), [10, 50, 90])
        young_rate = float((account_age < 180).mean())
        age_label = f"p10 {a10:.0f}, p50 {a50:.0f}, p90 {a90:.0f} ngày; <180 ngày {pct(young_rate)}"
    else:
        a10 = a50 = a90 = 0.0
        young_rate = 0.0
        age_label = "không có"
    print(f"  Người nhận phổ biến: {len(popular)} tài khoản, người gửi B01/tháng {sender_label}")
    print(f"  Tuổi tài khoản người nhận phổ biến: {age_label}")
    report.add(
        "Người nhận cá nhân phổ biến",
        pct(pop_rate),
        pct(float(hist["popular_payee_share"])),
        sender_label,
        abs(pop_rate - float(hist["popular_payee_share"])) <= 0.001 and len(popular) > 0 and 5 <= p10 and p90 <= 40,
    )
    report.add(
        "Người nhận phổ biến dưới 180 ngày tuổi",
        pct(young_rate),
        pct(float(hist["popular_payee_young_share"])),
        age_label,
        abs(young_rate - float(hist["popular_payee_young_share"])) <= 0.08,
    )

    call_month = calls["ts_start"].dt.month
    call_rates = []
    for month in (7, 8, 9):
        call_rates.append(int((call_month == month).sum()) / n_customers)
    call_mean = float(np.mean(call_rates)) if call_rates else 0.0
    call_target = float(hist["bank_calls_per_month"]) * float(cfg["bank_call_log_completeness"])
    print(f"  Ngân hàng gọi khách, tỷ lệ ghi được mỗi tháng: {pct(call_mean)} (mục tiêu {pct(call_target)})")
    report.add(
        "Cuộc gọi ngân hàng được ghi",
        pct(call_mean),
        pct(call_target),
        "1,5%/tháng × độ đầy đủ nhật ký",
        abs(call_mean - call_target) <= 0.004 and bool((calls["ts_end"] > calls["ts_start"]).all()),
    )

    blank = float((transactions["purpose_text"].fillna("").astype(str) == "").mean())
    print(f"  Purpose để trống: {pct(blank)}")
    print(f"  Giao dịch {len(transactions)}, người nhận {len(payees)}, liên hệ {len(contacts)}")
    print()
    report.print()
    if not report.ok():
        failed = sum(1 for row in report.rows if not row[-1])
        print(f"\n{failed} mục không đạt.")
        sys.exit(1)
    print("\nTất cả kiểm tra bước 3 đều đạt.")


if __name__ == "__main__":
    main()
