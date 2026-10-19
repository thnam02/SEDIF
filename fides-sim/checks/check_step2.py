"""Đối chiếu tài khoản và thiết bị bước 2 với config. Thoát mã 1 nếu có mục không đạt."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TZ = "Asia/Ho_Chi_Minh"
KEYS = {"customer_id", "device_id"}


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


def before(series: pd.Series, bound: pd.Timestamp) -> bool:
    if series.isna().any() or zone_name(series) != TZ:
        return False
    return bool(series.max() < bound)


def main() -> None:
    cfg = load_config()
    block = cfg["accounts"]
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    customers = pd.read_parquet(observed / "customers.parquet")
    latent_people = pd.read_parquet(truth / "customers_latent.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    devices = pd.read_parquet(observed / "devices.parquet")
    links = pd.read_parquet(observed / "account_device.parquet")
    devices_latent = pd.read_parquet(truth / "devices_latent.parquet")

    report = Report()
    personal = accounts[(accounts["account_type"] == "personal") & accounts["customer_id"].notna()]
    missing = len(set(customers["customer_id"]) - set(personal["customer_id"]))
    report.add("Mọi khách hàng có ≥ 1 tài khoản personal", f"{missing} khách thiếu", "0", "-", missing == 0)

    distinct = personal.groupby("customer_id")["bank_id"].nunique()
    sizes = personal.groupby("customer_id").size()
    banks_ok = bool(distinct.eq(sizes).all())
    report.add("Personal ở các ngân hàng khác nhau", "đạt" if banks_ok else "trùng ngân hàng", "không trùng", "-", banks_ok)

    home = str(block["home_bank"])
    with_home = accounts.loc[accounts["bank_id"] == home, "customer_id"].dropna().nunique() / len(customers)
    target = float(block["bank_share"][home])
    report.add(
        f"Khách hàng có tài khoản ở {home}",
        pct(float(with_home)),
        pct(target),
        f"{100 * (with_home - target):+.2f} đ.p",
        abs(with_home - target) < 0.01,
    )

    money = (
        accounts.dropna(subset=["customer_id"])
        .merge(customers[["customer_id", "segment"]], on="customer_id")
        .merge(latent_people[["customer_id", "income_monthly"]], on="customer_id")
    )
    money["ratio"] = money["opening_balance"] / money["income_monthly"]
    balance_spec = block["balance_multiple_of_income"]
    dtype_ok = str(accounts["opening_balance"].dtype) == "int64"
    report.add("opening_balance là int64 VND", str(accounts["opening_balance"].dtype), "int64", "-", dtype_ok)
    for segment, key in (("cao tuổi", "elderly"), ("doanh nghiệp nhỏ", "default"), ("thường", "default")):
        expected = float(balance_spec[key]["median"])
        actual = float(money.loc[money["segment"] == segment, "ratio"].median())
        rel = actual / expected - 1.0
        report.add(
            f"Số dư / thu nhập, {segment}",
            f"{actual:.2f}x",
            f"{expected:.1f}x",
            f"{100 * rel:+.2f}%",
            abs(rel) <= 0.10,
        )

    names = customers.set_index("customer_id")["name_ascii"]
    for kind in ("savings", "securities"):
        part = accounts.loc[accounts["account_type"] == kind]
        matched = bool(part["holder_name_ascii"].eq(part["customer_id"].map(names)).all()) and part["customer_id"].notna().all()
        report.add(f"{kind} cùng holder_name_ascii với chủ", "đạt" if matched else "lệch", "trùng tên chủ", "-", matched)

    per_device = links.groupby("device_id").size()
    max_accounts = int(per_device.max()) if len(per_device) else 0
    report.add("Tài khoản trên một thiết bị", str(max_accounts), "≤ 3", "-", max_accounts <= 3)

    linked = links.merge(accounts[["account_id", "customer_id"]], on="account_id", how="left")
    linked = linked.dropna(subset=["customer_id"]).merge(latent_people[["customer_id", "family_group_id"]], on="customer_id", how="left")
    family_count = linked.groupby("device_id")["family_group_id"].nunique()
    customer_count = linked.groupby("device_id")["customer_id"].nunique()
    shared = customer_count[customer_count > 1].index
    if len(shared) == 0:
        shared_ok = False
    else:
        shared_ok = bool((family_count.loc[shared] == 1).all())
    report.add(
        "Thiết bị dùng chung cùng một hộ",
        f"{len(shared)} thiết bị" if shared_ok else "có hộ khác nhau hoặc không có máy dùng chung",
        "chỉ trong cùng family_group_id",
        "-",
        shared_ok,
    )

    report.add("opened_at < sim_end, múi giờ VN", "đạt" if before(accounts["opened_at"], sim_end) else "lỗi", "đạt", "-", before(accounts["opened_at"], sim_end))
    report.add("first_seen < sim_end, múi giờ VN", "đạt" if before(devices["first_seen"], sim_end) else "lỗi", "đạt", "-", before(devices["first_seen"], sim_end))
    active_ok = set(accounts["status"].unique()) == {"active"}
    report.add("Chưa có tài khoản mule", "active" if active_ok else "có trạng thái khác", "active", "-", active_ok)

    observed_cols: set[str] = set()
    truth_cols: set[str] = set()
    for path in observed.glob("*.parquet"):
        observed_cols |= set(pd.read_parquet(path).columns)
    for path in truth.glob("*.parquet"):
        truth_cols |= set(pd.read_parquet(path).columns)
    overlap = observed_cols & truth_cols
    report.add("Cột observed ∩ truth", "{" + ", ".join(sorted(overlap)) + "}", "chỉ khóa", "-", overlap <= KEYS and "device_id" in overlap)

    fk_ok = bool(personal["customer_id"].isin(customers["customer_id"]).all())
    fk_ok = fk_ok and bool(devices["customer_id"].isin(customers["customer_id"]).all())
    fk_ok = fk_ok and bool(links["account_id"].isin(accounts["account_id"]).all())
    fk_ok = fk_ok and bool(links["device_id"].isin(devices["device_id"]).all())
    fk_ok = fk_ok and bool(devices_latent["device_id"].isin(devices["device_id"]).all())
    fk_ok = fk_ok and bool(set(devices_latent["device_id"]) == set(devices["device_id"]))
    named_phone = accounts["phone_number_id"].dropna()
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    fk_ok = fk_ok and bool(named_phone.isin(phones["number_id"]).all())
    report.add("Khóa ngoại tài khoản và thiết bị", "đạt" if fk_ok else "lỗi", "đạt", "-", fk_ok)

    sim_start = as_timestamp(cfg["sim_start"])
    new_accounts = float(((accounts["opened_at"] >= sim_start) & (accounts["opened_at"] < sim_end)).mean())
    new_devices = float(((devices["first_seen"] >= sim_start) & (devices["first_seen"] < sim_end)).mean())
    per_customer = devices.groupby("customer_id").size()
    two = float((per_customer == 2).mean())
    print()
    print("Tham khảo (không tính ĐẠT/LỖI)")
    print(f"  Tài khoản mới trong cửa sổ: {pct(new_accounts)} (mục tiêu {pct(float(block['new_account_in_window_share']))})")
    print(f"  Thiết bị mới trong cửa sổ: {pct(new_devices)} (mục tiêu {pct(float(block['new_device_in_window_share']))})")
    print(f"  Khách có 2 thiết bị: {pct(two)} (mục tiêu tối thiểu {pct(float(block['second_device_share']))})")
    print(f"  Android {pct(float((devices['os'] == 'Android').mean()))}, rooted {pct(float(devices['is_rooted'].mean()))}")
    merchants = accounts["customer_id"].isna() & (accounts["account_type"] == "business") & accounts["phone_number_id"].isna()
    print(f"  Merchant {int(merchants.sum())}, trong đó verified {pct(float(accounts.loc[merchants, 'merchant_verified'].mean()))} (mục tiêu {pct(float(block['merchant_verified_share']))})")
    print()
    report.print()
    if not report.ok():
        failed = sum(1 for row in report.rows if not row[-1])
        print(f"\n{failed} mục không đạt.")
        sys.exit(1)
    print("\nTất cả kiểm tra bước 2 đều đạt.")


if __name__ == "__main__":
    main()
