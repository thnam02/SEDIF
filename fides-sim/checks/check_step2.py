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
# number_id xuất hiện khi đã chạy bước 3 (danh bạ). contact_type không trùng cột observed.
# ts và channel trùng tên giữa giao dịch và lịch liên lạc, không phải nhãn.
# session_id, start_ts, end_ts, seq, duration_s, remote_access_app_installed
# trùng tên giữa observed và truth, không phải nhãn vụ lừa.
KEYS = {
    "customer_id", "device_id", "number_id", "account_id", "ts", "channel",
    "session_id", "start_ts", "end_ts", "seq", "duration_s", "remote_access_app_installed",
}


def load_config() -> dict:
    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def mule_account_ids() -> set[str]:
    path = ROOT / "out" / "truth" / "mule_rings.parquet"
    if not path.exists():
        return set()
    rings = pd.read_parquet(path, columns=["account_id"])
    return set(rings["account_id"].astype(str))


def old_mule_account_ids(sim_start: pd.Timestamp) -> set[str]:
    path = ROOT / "out" / "truth" / "mule_rings.parquet"
    if not path.exists():
        return set()
    rings = pd.read_parquet(path, columns=["account_id", "active_to"])
    old = rings.loc[rings["active_to"] < sim_start, "account_id"]
    return set(old.astype(str))


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
    owned_accounts = accounts.dropna(subset=["customer_id"])
    matched = bool(owned_accounts["holder_name_ascii"].eq(owned_accounts["customer_id"].map(names)).all())
    report.add("holder_name_ascii khớp name_ascii của chủ", "đạt" if matched else "lệch", "mọi tài khoản của khách", "-", matched)

    linked = links.merge(accounts[["account_id", "customer_id"]], on="account_id", how="left")
    linked["device_id"] = linked["device_id"].astype(str)
    linked["account_id"] = linked["account_id"].astype(str)
    linked = linked.merge(latent_people[["customer_id", "family_group_id"]], on="customer_id", how="left")
    linked["household"] = linked["family_group_id"].astype(str)
    missing_hh = linked["family_group_id"].isna() | linked["customer_id"].isna()
    linked.loc[missing_hh, "household"] = "A:" + linked.loc[missing_hh, "account_id"]
    mule_accounts = mule_account_ids()
    owner_segment = devices["customer_id"].map(customers.set_index("customer_id")["segment"]).astype(str)
    segment_of = dict(zip(devices["device_id"].astype(str), owner_segment))
    window_end = sim_end

    def holder_intervals(part: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        intervals = []
        named = part.dropna(subset=["customer_id"])
        for _, rows in named.groupby(named["customer_id"].astype(str), sort=False):
            start = rows["first_seen"].min()
            stop = rows["last_seen"].max()
            if pd.isna(stop):
                stop = window_end
            intervals.append((start, stop))
        intervals.sort(key=lambda item: item[0])
        return intervals

    def sequential(intervals: list[tuple[pd.Timestamp, pd.Timestamp]]) -> bool:
        return len(intervals) >= 2 and all(left[1] < right[0] for left, right in zip(intervals, intervals[1:]))

    def parallel(intervals: list[tuple[pd.Timestamp, pd.Timestamp]]) -> bool:
        if len(intervals) < 2:
            return False
        return max(item[0] for item in intervals) < min(item[1] for item in intervals)

    household_n = linked.groupby("device_id")["household"].nunique()
    customer_n = linked.dropna(subset=["customer_id"]).groupby("device_id")["customer_id"].nunique()
    resale_ids = []
    family3_ids = []
    shop_ids = []
    explained = True
    multi_ids = set(customer_n[customer_n > 1].index.astype(str))
    for device_id, part in linked.loc[linked["device_id"].isin(multi_ids)].groupby("device_id", sort=False):
        households = int(household_n.get(device_id, 0))
        customers_on = int(customer_n.get(device_id, 0))
        accounts_on = set(part["account_id"])
        mule_device = bool(accounts_on) and accounts_on <= mule_accounts
        intervals = holder_intervals(part)
        same_family = households == 1 and customers_on >= 2
        is_resale = households == 2 and customers_on == 2 and sequential(intervals)
        is_family3 = customers_on == 3 and households == 1
        is_shop = (
            segment_of.get(str(device_id)) == "doanh nghiệp nhỏ"
            and 4 <= customers_on <= 6
            and households == customers_on
            and parallel(intervals)
        )
        if is_resale:
            resale_ids.append(device_id)
        if is_family3:
            family3_ids.append(device_id)
        if is_shop:
            shop_ids.append(device_id)
        if customers_on > 1 and not (same_family or is_resale or is_family3 or is_shop or mule_device):
            explained = False
    n_devices = len(devices)
    resale_rate = len(resale_ids) / n_devices if n_devices else 0.0
    family3_rate = len(family3_ids) / n_devices if n_devices else 0.0
    report.add(
        "Máy cũ bán lại khác hộ",
        f"{len(resale_ids)} máy, {pct(resale_rate)}",
        "~0.30%",
        "last_seen chủ cũ < first_seen chủ mới",
        abs(resale_rate - 0.003) <= 0.0005 and len(resale_ids) > 0,
    )
    report.add(
        "Máy hộ 3 người",
        f"{len(family3_ids)} máy, {pct(family3_rate)}",
        "~0.10%",
        "cùng family_group_id",
        abs(family3_rate - 0.001) <= 0.0003 and len(family3_ids) > 0,
    )
    report.add(
        "Máy cửa hàng",
        f"{len(shop_ids)} máy",
        "30–60",
        "chủ doanh nghiệp nhỏ, 3–5 nhân viên khác hộ, đăng nhập song song",
        30 <= len(shop_ids) <= 60,
    )
    crowded = household_n[household_n > 2]
    mule_crowded = 0
    for device_id in crowded.index:
        accounts_on = set(linked.loc[linked["device_id"] == device_id, "account_id"])
        if accounts_on and accounts_on <= mule_accounts:
            mule_crowded += 1
    if mule_accounts:
        mule_share = mule_crowded / len(crowded) if len(crowded) else 0.0
        print(f"  Thiết bị > 2 hộ khác nhau: {len(crowded)} máy, mule {mule_crowded} ({pct(mule_share)})")
        crowded_ok = 0.85 <= mule_share <= 0.95
        crowded_target = "85–95% là mule"
    else:
        mule_share = 0.0
        crowded_ok = len(crowded) == len(shop_ids) and 30 <= len(shop_ids) <= 60
        crowded_target = "toàn bộ là máy cửa hàng khi chưa có mule"
        print(f"  Thiết bị > 2 hộ khác nhau: {len(crowded)} máy, chưa có mule")
    report.add(
        "Thiết bị > 2 holder khác hộ",
        f"{len(crowded)} máy, mule {pct(mule_share)}",
        crowded_target,
        f"{mule_crowded} máy mule",
        crowded_ok and explained,
    )

    report.add("opened_at < sim_end, múi giờ VN", "đạt" if before(accounts["opened_at"], sim_end) else "lỗi", "đạt", "-", before(accounts["opened_at"], sim_end))
    report.add("first_seen < sim_end, múi giờ VN", "đạt" if before(devices["first_seen"], sim_end) else "lỗi", "đạt", "-", before(devices["first_seen"], sim_end))
    sim_start = as_timestamp(cfg["sim_start"])
    marked = devices.dropna(subset=["customer_id"]).copy()
    marked["in_window"] = (marked["first_seen"] >= sim_start) & (marked["first_seen"] < sim_end)
    new_first = marked.loc[marked["in_window"]].groupby("customer_id")["first_seen"].min()
    retired = marked.loc[marked["last_seen"].notna(), ["customer_id", "last_seen"]]
    changers = set(new_first.index)
    ordered = changers == set(retired["customer_id"]) and len(changers) > 0
    if ordered:
        compared = retired.merge(new_first.rename("new_first"), on="customer_id")
        ordered = bool((compared["last_seen"] < compared["new_first"]).all())
    report.add(
        "Đổi máy trong cửa sổ: last_seen máy cũ < first_seen máy mới",
        f"{len(changers)} khách" if ordered else "lệch hoặc thiếu last_seen",
        "last_seen cũ < first_seen mới",
        "-",
        ordered,
    )
    active_count = marked.loc[marked["last_seen"].isna()].groupby("customer_id").size()
    parallel_rate = float((active_count == 2).sum() / len(customers))
    replace_rate = retired["customer_id"].nunique() / len(customers)
    report.add(
        "Dùng hai máy song song",
        pct(parallel_rate),
        pct(float(block["second_device_share"])),
        f"{100 * (parallel_rate - float(block['second_device_share'])):+.2f} đ.p",
        abs(parallel_rate - float(block["second_device_share"])) < 0.01,
    )
    report.add(
        "Đổi máy trong cửa sổ",
        pct(replace_rate),
        pct(float(block["new_device_in_window_share"])),
        f"{100 * (replace_rate - float(block['new_device_in_window_share'])):+.2f} đ.p",
        abs(replace_rate - float(block["new_device_in_window_share"])) < 0.01,
    )
    frozen = set(accounts.loc[accounts["status"].astype(str) == "frozen", "account_id"].astype(str))
    old_mules = old_mule_account_ids(sim_start)
    if not old_mules:
        active_ok = not frozen and set(accounts["status"].astype(str).unique()) == {"active"}
        report.add("Chưa có tài khoản mule", "active" if active_ok else "có trạng thái khác", "active", "-", active_ok)
    else:
        active_ok = frozen == old_mules
        report.add("Tài khoản đóng băng đúng đường dây cũ", f"{len(frozen)} đóng băng", f"{len(old_mules)} mule cũ", "-", active_ok)

    observed_cols: set[str] = set()
    truth_cols: set[str] = set()
    for path in observed.glob("*.parquet"):
        observed_cols |= set(pd.read_parquet(path).columns)
    for path in truth.glob("*.parquet"):
        truth_cols |= set(pd.read_parquet(path).columns)
    overlap = observed_cols & truth_cols
    report.add("Cột observed ∩ truth", "{" + ", ".join(sorted(overlap)) + "}", "chỉ khóa", "-", overlap <= KEYS and "device_id" in overlap)

    fk_ok = bool(personal["customer_id"].isin(customers["customer_id"]).all())
    device_owners = devices["customer_id"].dropna()
    fk_ok = fk_ok and bool(device_owners.isin(customers["customer_id"]).all())
    fk_ok = fk_ok and bool(links["account_id"].isin(accounts["account_id"]).all())
    fk_ok = fk_ok and bool(links["device_id"].isin(devices["device_id"]).all())
    fk_ok = fk_ok and bool(devices_latent["device_id"].isin(devices["device_id"]).all())
    fk_ok = fk_ok and bool(set(devices_latent["device_id"]) == set(devices["device_id"]))
    named_phone = accounts["phone_number_id"].dropna()
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    fk_ok = fk_ok and bool(named_phone.isin(phones["number_id"]).all())
    report.add("Khóa ngoại tài khoản và thiết bị", "đạt" if fk_ok else "lỗi", "đạt", "-", fk_ok)

    new_accounts = float(((accounts["opened_at"] >= sim_start) & (accounts["opened_at"] < sim_end)).mean())
    print()
    print("Tham khảo (không tính ĐẠT/LỖI)")
    print(f"  Tài khoản mới trong cửa sổ: {pct(new_accounts)} (mục tiêu {pct(float(block['new_account_in_window_share']))})")
    print(f"  Dùng 2 máy song song: {pct(parallel_rate)} (mục tiêu {pct(float(block['second_device_share']))})")
    print(f"  Đổi máy: {pct(replace_rate)} (mục tiêu {pct(float(block['new_device_in_window_share']))})")
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
