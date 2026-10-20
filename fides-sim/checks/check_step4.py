"""Đối chiếu đường dây mule bước 4. Thoát mã 1 nếu có mục không đạt."""

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
LEAKED = {"is_mule", "ring_id", "layer"}


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
        widths = [max(len(str(row[col])) for row in table) for col in range(5)]
        print("  ".join(str(header).ljust(widths[index]) for index, header in enumerate(headers)))
        print("  ".join("-" * width for width in widths))
        for row in table[1:]:
            print("  ".join(str(row[index]).ljust(widths[index]) for index in range(5)))


def pct(value: float) -> str:
    return f"{100 * value:.2f}%"


def percentiles(values: np.ndarray) -> str:
    if len(values) == 0:
        return "trống"
    low, mid, high = np.quantile(values, [0.1, 0.5, 0.9])
    return f"p10 {low:.0f} / p50 {mid:.0f} / p90 {high:.0f}"


def overlaps(left: np.ndarray, right: np.ndarray) -> bool:
    if len(left) == 0 or len(right) == 0:
        return False
    left_low, left_high = np.quantile(left, [0.1, 0.9])
    right_low, right_high = np.quantile(right, [0.1, 0.9])
    return left_low <= right_high and right_low <= left_high


def auc_score(label: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=float)
    ranks[order] = np.arange(1, len(score) + 1, dtype=float)
    sorted_score = score[order]
    start = 0
    while start < len(score):
        stop = start + 1
        while stop < len(score) and sorted_score[stop] == sorted_score[start]:
            stop += 1
        if stop > start + 1:
            mean = 0.5 * (start + 1 + stop)
            ranks[order[start:stop]] = mean
        start = stop
    positive = label.astype(bool)
    n_pos = int(positive.sum())
    n_neg = len(label) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.0
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def fit_logit(features: np.ndarray, label: np.ndarray) -> np.ndarray:
    """Hồi quy logistic có chặn, L2 nhẹ. Trả về điểm trên toàn bộ mẫu."""
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std[std == 0] = 1.0
    scaled = (features - mean) / std
    design = np.column_stack([np.ones(len(scaled)), scaled])
    weight = np.zeros(design.shape[1])
    positive = label.astype(float)
    for _ in range(30):
        linear = np.clip(design @ weight, -20, 20)
        prob = 1.0 / (1.0 + np.exp(-linear))
        hessian_w = prob * (1.0 - prob)
        gradient = design.T @ (positive - prob) - 0.5 * weight
        gradient[0] += 0.5 * weight[0]
        hessian = design.T @ (design * hessian_w[:, None]) + 0.5 * np.eye(design.shape[1])
        hessian[0, 0] -= 0.5
        step = np.linalg.solve(hessian, gradient)
        weight += step
        if np.max(np.abs(step)) < 1e-6:
            break
    return design @ weight


def main() -> None:
    cfg = load_config()
    spec = cfg["mules"]
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    report = Report()

    accounts = pd.read_parquet(observed / "accounts.parquet")
    devices = pd.read_parquet(observed / "devices.parquet")
    links = pd.read_parquet(observed / "account_device.parquet")
    transactions = pd.read_parquet(observed / "transactions.parquet")
    simo = pd.read_parquet(observed / "simo_list.parquet")
    customers = pd.read_parquet(observed / "customers.parquet")
    latent = pd.read_parquet(truth / "customers_latent.parquet")
    rings = pd.read_parquet(truth / "mule_rings.parquet")
    truth_accounts = pd.read_parquet(truth / "accounts_truth.parquet")

    for frame in (accounts, devices, links, transactions, simo, rings, truth_accounts):
        if "account_id" in frame.columns:
            frame["account_id"] = frame["account_id"].astype(str)
    rings["ring_id"] = rings["ring_id"].astype(str)
    customers["customer_id"] = customers["customer_id"].astype(str)
    latent["customer_id"] = latent["customer_id"].astype(str)
    accounts["customer_id"] = accounts["customer_id"].astype("string")

    active = rings.loc[rings["active_to"] >= sim_start]
    old = rings.loc[rings["active_to"] < sim_start]
    n_active = active["ring_id"].nunique()
    n_old = old["ring_id"].nunique()
    report.add(
        "Số đường dây",
        f"{n_active} hoạt động, {n_old} cũ",
        f"{int(spec['n_active_rings'])} hoạt động, {int(spec['n_old_rings'])} cũ",
        "-",
        n_active == int(spec["n_active_rings"]) and n_old == int(spec["n_old_rings"]),
    )
    sizes = rings.groupby("ring_id").size()
    low = int(spec["ring_size"]["min"])
    high = int(spec["ring_size"]["max"])
    report.add(
        "Kích thước đường dây",
        f"{int(sizes.min())}–{int(sizes.max())}, trung vị {float(sizes.median()):.0f}",
        f"{low}–{high}",
        "-",
        bool((sizes >= low).all() and (sizes <= high).all()),
    )

    def share_ok(series: pd.Series, targets: dict) -> tuple[str, bool]:
        rate = series.value_counts(normalize=True)
        parts = []
        ok = True
        for name, target in targets.items():
            actual = float(rate.get(name, 0.0))
            parts.append(f"{name} {pct(actual)}")
            ok = ok and abs(actual - float(target)) <= 0.03
        return ", ".join(parts), ok

    core_mules = truth_accounts.loc[truth_accounts["is_mule"] & truth_accounts["mule_type"].astype(str).ne("own_name_safe_account")]
    type_text, type_ok = share_ok(core_mules["mule_type"].astype(str), spec["type_share"])
    layer_text, layer_ok = share_ok(rings["layer"].astype(str), spec["layer_share"])
    report.add("Tỷ lệ loại mule", type_text, "±3 đ.p", "-", type_ok and len(core_mules) == len(rings))
    report.add("Tỷ lệ lớp", layer_text, "±3 đ.p", "-", layer_ok)

    bank_rate = float((accounts.set_index("account_id").loc[rings["account_id"], "bank_id"].astype(str) == "B01").mean())
    report.add("Mule tại B01", pct(bank_rate), pct(float(cfg["accounts"]["bank_share"]["B01"])), f"{100 * (bank_rate - 0.20):+.2f} đ.p", abs(bank_rate - 0.20) <= 0.03)

    people = customers.merge(latent[["customer_id", "family_group_id", "income_monthly"]], on="customer_id", how="left")
    family_of = dict(zip(people["customer_id"], people["family_group_id"].astype(str)))
    holder = dict(zip(accounts["account_id"], accounts["holder_name_ascii"].astype(str)))
    owner = dict(zip(accounts["account_id"], accounts["customer_id"].astype(str)))
    mule_ids = set(rings["account_id"].astype(str)) | set(truth_accounts.loc[truth_accounts["is_mule"], "account_id"].astype(str))
    linked = links.copy()
    linked["account_id"] = linked["account_id"].astype(str)
    linked["device_id"] = linked["device_id"].astype(str)
    linked["household"] = linked["account_id"].map(lambda account: family_of.get(owner.get(account, ""), account))
    households = linked.groupby("device_id")["household"].nunique()
    crowded = set(households[households > 2].index.astype(str))
    mule_devices = 0
    for device_id, part in linked.loc[linked["device_id"].isin(crowded)].groupby("device_id", sort=False):
        if set(part["account_id"]) <= mule_ids:
            mule_devices += 1
    mule_share = mule_devices / len(crowded) if crowded else 0.0
    exposed = set(linked.loc[linked["device_id"].isin(crowded) & linked["account_id"].isin(mule_ids), "account_id"])
    exposed_rate = len(exposed) / len(mule_ids) if mule_ids else 0.0
    print(f"  Mule lộ qua thiết bị nhiều hộ: {pct(exposed_rate)}; mule chỉ máy riêng: {pct(1 - exposed_rate)}")
    print(f"  Thiết bị > 2 hộ khác nhau: {len(crowded)} máy, mule {mule_devices} ({pct(mule_share)})")
    report.add(
        "Thiết bị > 2 holder khác hộ",
        f"{len(crowded)} máy, mule {pct(mule_share)}",
        "85–95% là mule",
        f"{mule_devices} máy mule",
        0.85 <= mule_share <= 0.95 and len(crowded) > 0,
    )

    age_years = sim_start.year - customers.set_index("customer_id")["birth_year"]
    customer_age = accounts["customer_id"].map(age_years)
    opened = accounts.set_index("account_id")["opened_at"]
    balance = accounts.set_index("account_id")["opening_balance"].astype(np.int64)
    account_age_days = (sim_end - opened).dt.total_seconds() / 86400
    rented = truth_accounts.loc[truth_accounts["mule_type"].astype(str) == "rented", "account_id"]
    rented_customers = accounts.set_index("account_id").loc[rented, "customer_id"].astype(str)
    rented_age = customer_age.loc[accounts["account_id"].isin(set(rented))].dropna()
    young_band = (customer_age >= 18) & (customer_age <= 35) & accounts["customer_id"].notna()
    young_normal = accounts.loc[young_band & ~accounts["account_id"].isin(mule_ids), "account_id"]
    young_rented = accounts.loc[young_band & accounts["account_id"].isin(set(rented)), "account_id"]
    rented_age_days = account_age_days.loc[young_rented].to_numpy(dtype=float)
    normal_age_days = account_age_days.loc[young_normal].to_numpy(dtype=float)
    rented_bal = balance.loc[young_rented].to_numpy(dtype=float)
    normal_bal = balance.loc[young_normal].to_numpy(dtype=float)
    age_overlap = overlaps(rented_age_days, normal_age_days)
    bal_overlap = overlaps(rented_bal, normal_bal)
    print(f"  Tuổi TK thuê 18–35: {percentiles(rented_age_days)} ngày; người thường: {percentiles(normal_age_days)}")
    print(f"  Số dư thuê 18–35: {percentiles(rented_bal)}; người thường: {percentiles(normal_bal)}")
    report.add(
        "Tuổi TK và số dư mule thuê chồng người thường",
        "chồng" if age_overlap and bal_overlap else "tách",
        "chồng phân vị 10–90",
        f"{len(young_rented)} mule trong nhóm 18–35",
        age_overlap and bal_overlap,
    )

    new_ids = truth_accounts.loc[truth_accounts["mule_type"].astype(str) == "new_opened", "account_id"]
    student = accounts["customer_id"].map(age_years)
    student_new = accounts.loc[
        (student <= 25) & (accounts["opened_at"] >= sim_start) & ~accounts["account_id"].isin(mule_ids),
        "account_id",
    ]
    new_age = account_age_days.loc[new_ids].to_numpy(dtype=float)
    student_age = account_age_days.loc[student_new].to_numpy(dtype=float)
    new_overlap = overlaps(new_age, student_age)
    print(f"  Tuổi TK new_opened: {percentiles(new_age)} ngày; sinh viên mới: {percentiles(student_age)}")
    report.add("Tuổi TK new_opened chồng tài khoản sinh viên mới", "chồng" if new_overlap else "tách", "chồng phân vị 10–90", f"{len(student_new)} tài khoản sinh viên", new_overlap)

    old_accounts = set(old["account_id"])
    active_accounts = set(active["account_id"])
    device_of = linked.groupby("account_id")["device_id"].apply(set).to_dict()
    old_devices = set().union(*(device_of.get(account, set()) for account in old_accounts))
    tx_pairs = transactions.loc[transactions["to_account"].notna(), ["from_account", "to_account"]]
    linked_rings = set()
    for ring, part in active.groupby("ring_id"):
        accounts_in_ring = set(part["account_id"])
        devices_in_ring = set().union(*(device_of.get(account, set()) for account in accounts_in_ring))
        by_device = bool(devices_in_ring & old_devices)
        touched = tx_pairs["from_account"].isin(accounts_in_ring) | tx_pairs["to_account"].isin(accounts_in_ring)
        other = tx_pairs.loc[touched, ["from_account", "to_account"]].stack()
        by_tx = bool(set(other.astype(str)) & old_accounts)
        if by_device or by_tx:
            linked_rings.add(ring)
    need = int(np.ceil(n_active / 3))
    report.add(
        "Nối mule cũ và mule mới",
        f"{len(linked_rings)}/{n_active} đường dây",
        f"≥ {need}",
        "-",
        len(linked_rings) >= need,
    )

    if "source" in simo.columns:
        simo_base = simo.loc[simo["source"].astype(str).ne("victim_report")].copy()
    else:
        simo_base = simo
    simo_ids = set(simo_base["account_id"].astype(str))
    legit = set(accounts["account_id"]) - mule_ids
    false_ids = simo_ids - mule_ids
    false_rate = len(false_ids) / len(legit) if legit else 0.0
    active_listed = len(active_accounts & simo_ids)
    report.add(
        "SIMO báo nhầm",
        pct(false_rate),
        pct(float(spec["simo_false_listing_rate"])),
        f"{len(false_ids)} tài khoản",
        abs(false_rate - float(spec["simo_false_listing_rate"])) <= 0.01,
    )
    report.add("SIMO chưa có mule đang hoạt động", str(active_listed), "0", f"mule cũ trong SIMO {len(old_accounts & simo_ids)}/{len(old_accounts)}", active_listed == 0 and old_accounts <= simo_ids)
    if len(simo):
        report_ok = bool((simo_base.loc[simo_base["account_id"].astype(str).isin(old_accounts), "listed_at"] < sim_start).all()) if len(simo_base) else False
    else:
        report_ok = False
    report.add("SIMO mule cũ listed_at trước cửa sổ", "đạt" if report_ok else "lỗi", "trước sim_start", "-", report_ok)

    negative = int((transactions["from_balance_before"] < transactions["amount"]).sum()) if len(transactions) else 0
    # Người gửi không theo dõi có thể ghi số dư mở; chỉ mule và khách hàng phải đủ số dư chạy.
    tracked = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str)) | mule_ids
    tracked_tx = transactions["from_account"].isin(tracked)
    negative = int((transactions.loc[tracked_tx, "from_balance_before"] < transactions.loc[tracked_tx, "amount"]).sum())
    report.add("Số dư không âm", f"{negative} lệch", "0", "-", negative == 0)

    account_ids = set(accounts["account_id"])
    device_ids = set(devices["device_id"].astype(str))
    phone_ids = set(pd.read_parquet(observed / "phone_numbers.parquet", columns=["number_id"])["number_id"].astype(str))
    fk_ok = mule_ids <= account_ids and set(simo["account_id"].astype(str)) <= account_ids
    fk_ok = fk_ok and set(links["account_id"].astype(str)) <= account_ids and set(links["device_id"].astype(str)) <= device_ids
    named = accounts["phone_number_id"].dropna().astype(str)
    fk_ok = fk_ok and bool(named.isin(phone_ids).all())
    customer_ids = set(customers["customer_id"])
    named_customers = accounts["customer_id"].dropna().astype(str)
    fk_ok = fk_ok and bool(named_customers.isin(customer_ids).all())
    report.add("Khóa ngoại", "đạt" if fk_ok else "lỗi", "đạt", "-", fk_ok)

    observed_cols: set[str] = set()
    for path in observed.glob("*.parquet"):
        observed_cols |= set(pd.read_parquet(path).columns)
    leaked = observed_cols & LEAKED
    report.add("observed không lộ nhãn mule", "có " + ",".join(sorted(leaked)) if leaked else "không", "không có is_mule, ring_id, layer", "-", not leaked)

    tx_count = pd.concat([transactions["from_account"], transactions["to_account"].dropna()]).astype(str).value_counts()
    holder_n = linked.groupby("device_id")["account_id"].nunique()
    max_holders = linked.groupby("account_id")["device_id"].apply(lambda devices_of: int(holder_n.reindex(list(devices_of)).max()) if len(devices_of) else 0)
    feature_accounts = accounts["account_id"].astype(str)
    label = feature_accounts.isin(mule_ids).to_numpy()
    features = np.column_stack(
        [
            account_age_days.loc[feature_accounts].fillna(0).to_numpy(dtype=float),
            np.log1p(balance.loc[feature_accounts].to_numpy(dtype=float)),
            feature_accounts.map(tx_count).fillna(0).to_numpy(dtype=float),
            feature_accounts.map(max_holders).fillna(0).to_numpy(dtype=float),
        ]
    )
    score = fit_logit(features, label)
    auc = auc_score(label, score)
    print(f"  AUC logistic trên đặc trưng tài khoản: {auc:.3f}")
    report.add("Baseline rò rỉ AUC", f"{auc:.3f}", "≤ 0.95", "tuổi TK, số dư, số giao dịch, số holder", auc <= 0.95)

    _ = (devices, holder, rented_customers, rented_age)
    print()
    report.print()
    if not report.ok():
        failed = sum(1 for row in report.rows if not row[-1])
        print(f"\n{failed} mục không đạt.")
        sys.exit(1)
    print("\nTất cả kiểm tra bước 4 đều đạt.")


if __name__ == "__main__":
    main()
