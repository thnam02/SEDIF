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

TZ = "Asia/Ho_Chi_Minh"
SCHOOL_AGE = 22
KEYS = {"customer_id", "device_id", "number_id", "relation"}
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
    report = Report()

    nonneg = bool((transactions["from_balance_before"] >= 0).all() and (transactions["amount"] > 0).all())
    customer_accounts = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str))
    outbound = transactions.loc[transactions["from_account"].astype(str).isin(customer_accounts)]
    covered = bool((outbound["from_balance_before"] >= outbound["amount"]).all())
    checked, bad = replay(transactions, accounts, rng)
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
        "đỉnh trưa và tối, 0–6h < 5%",
        f"trưa {'có' if noon_peak else 'không'}, tối {'có' if eve_peak else 'không'}",
        night < 0.05 and bool(noon_peak) and bool(eve_peak),
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
    report.add(
        "Học phí và large_oneoff tới người nhận mới",
        f"học phí {pct(school_new)}, oneoff {pct(large_new)}",
        "> 80%",
        "-",
        school_new > 0.80 and large_new > 0.80,
    )

    methods = payees["add_method"].value_counts(normalize=True)
    method_ok = True
    for name, target in hist["payee_add_method"].items():
        actual = float(methods.get(name, 0.0))
        ok = abs(actual - float(target)) <= 0.02
        method_ok = method_ok and ok
        report.add(f"add_method {name}", pct(actual), pct(float(target)), f"{100 * (actual - float(target)):+.2f} đ.p", ok)

    time_ok = in_window(transactions["ts"], sim_start, sim_end)
    other_time = zone_name(payees["added_at"]) == TZ and zone_name(contacts["first_contact"]) == TZ
    other_time = other_time and bool(payees["added_at"].max() < sim_end) and bool(contacts["first_contact"].max() < sim_end)
    report.add("ts giao dịch trong cửa sổ, giờ VN", "đạt" if time_ok else "lỗi", "đạt", "-", time_ok and other_time)
    session_ok = bool(transactions["session_id"].isna().all())
    report.add("session_id trống", "null" if session_ok else "có giá trị", "null", "-", session_ok)
    money_ok = str(transactions["amount"].dtype) == "int64" and str(transactions["from_balance_before"].dtype) == "int64"
    report.add("Tiền là int64 VND", f"{transactions['amount'].dtype}, {transactions['from_balance_before'].dtype}", "int64", "-", money_ok)

    fk_ok = bool(transactions["from_account"].astype(str).isin(accounts["account_id"].astype(str)).all())
    to_id = transactions["to_account"].dropna().astype(str)
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
        "relation trùng tên với guardians bước 1",
        overlap <= KEYS and "customer_id" in overlap,
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
