"""Đối chiếu dân số bước 1 với config. Thoát mã 1 nếu có mục không đạt."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.names import to_name_ascii

TZ = "Asia/Ho_Chi_Minh"
SEGMENT_OF = {
    "elderly": "cao tuổi",
    "small_business": "doanh nghiệp nhỏ",
    "normal": "thường",
}


def load_config() -> dict:
    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def as_timestamp(value) -> pd.Timestamp:
    stamp = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(TZ)
    return stamp.tz_convert(TZ)


def expected_elderly_share(bands, elderly_age: int) -> float:
    share = 0.0
    for band in bands:
        years = list(range(int(band["min"]), int(band["max"]) + 1))
        elderly_years = sum(age >= elderly_age for age in years)
        share += float(band["share"]) * elderly_years / len(years)
    return share


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
        line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
        print(line)
        print("  ".join("-" * width for width in widths))
        for row in table[1:]:
            print("  ".join(row[i].ljust(widths[i]) for i in range(5)))


def pct(value: float) -> str:
    return f"{100 * value:.2f}%"


def share_row(report: Report, item: str, actual: float, expected: float, tol: float = 0.01) -> None:
    diff = actual - expected
    report.add(item, pct(actual), pct(expected), f"{100 * diff:+.2f} đ.p", abs(diff) < tol)


def zone_name(series: pd.Series) -> str | None:
    tz = getattr(series.dt, "tz", None)
    if tz is None:
        return None
    return getattr(tz, "zone", None) or str(tz)


def main() -> None:
    cfg = load_config()
    pop = cfg["population"]
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    ref_year = int(sim_start.year)
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    customers = pd.read_parquet(observed / "customers.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    guardians = pd.read_parquet(observed / "guardians.parquet")
    latent = pd.read_parquet(truth / "customers_latent.parquet")

    report = Report()
    n = len(customers)
    age = ref_year - customers["birth_year"].to_numpy()
    bands = [(int(band["min"]), int(band["max"]), str(band["min"]) + "-" + str(band["max"]), float(band["share"])) for band in pop["age_bands"]]
    age_group = np.select(
        [age <= 30, age <= 50, age <= 65],
        ["18-30", "31-50", "51-65"],
        default="66-85",
    )
    group_ok = np.array_equal(age_group, customers["age_group"].to_numpy()) and np.all((age >= 18) & (age <= 85))
    report.add("birth_year khớp age_group", "khớp" if group_ok else "lệch", "khớp", "-", group_ok)
    for lo, hi, label, share in bands:
        actual = float(((age >= lo) & (age <= hi)).mean())
        share_row(report, f"Tuổi {label}", actual, share)

    elderly_age = int(pop["elderly_age"])
    elderly_expected = expected_elderly_share(pop["age_bands"], elderly_age)
    business_expected = float(pop["small_business_share"]) * (1.0 - elderly_expected)
    normal_expected = 1.0 - elderly_expected - business_expected
    expected_segment = {
        SEGMENT_OF["elderly"]: elderly_expected,
        SEGMENT_OF["small_business"]: business_expected,
        SEGMENT_OF["normal"]: normal_expected,
    }
    seg_counts = customers["segment"].value_counts(normalize=True)
    for label, expected in expected_segment.items():
        share_row(report, f"Segment {label}", float(seg_counts.get(label, 0.0)), expected)
    rule_ok = bool(np.array_equal(customers["segment"].to_numpy() == SEGMENT_OF["elderly"], age >= elderly_age))
    report.add("≥ elderly_age là cao tuổi", "đúng" if rule_ok else "sai", "đúng", "-", rule_ok)

    telco_counts = phones["telco"].value_counts(normalize=True)
    for name, share in pop["telco_share"].items():
        share_row(report, f"Nhà mạng {name}", float(telco_counts.get(name, 0.0)), float(share))

    city_counts = customers["city"].value_counts(normalize=True)
    for name, share in pop["city_share"].items():
        share_row(report, f"Thành phố {name}", float(city_counts.get(name, 0.0)), float(share))

    joined = customers[["customer_id", "birth_year", "full_name", "city"]].merge(
        latent[["customer_id", "family_group_id", "role", "lives_alone", "income_monthly", "susceptibility"]],
        on="customer_id",
        how="inner",
    )
    joined["age"] = ref_year - joined["birth_year"]
    child_bad = 0
    spouse_bad = 0
    link_child = 0
    link_spouse = 0
    child_away = 0
    child_n = 0
    for _, group in joined.groupby("family_group_id", sort=False):
        head = group.loc[group["role"] == "head", "age"]
        spouse = group.loc[group["role"] == "spouse", "age"]
        children = group.loc[group["role"] == "child", "age"]
        parents = group.loc[group["role"] == "parent", "age"]
        stayers = group.loc[group["role"] != "child", "city"]
        if len(stayers):
            home = stayers.iloc[0]
            kids_city = group.loc[group["role"] == "child", "city"]
            child_n += len(kids_city)
            child_away += int((kids_city != home).sum())
        if len(head) != 1:
            child_bad += 1
            continue
        head_age = int(head.iloc[0])
        if len(spouse) == 1:
            link_spouse += 1
            if abs(head_age - int(spouse.iloc[0])) > 15:
                spouse_bad += 1
        elif len(spouse) > 1:
            spouse_bad += 1
        spouse_age = int(spouse.iloc[0]) if len(spouse) == 1 else None
        for child_age in children.astype(int):
            link_child += 1
            if child_age > head_age - 18 or (spouse_age is not None and child_age > spouse_age - 18):
                child_bad += 1
        for parent_age in parents.astype(int):
            link_child += 1
            if head_age > int(parent_age) - 18:
                child_bad += 1
    report.add(
        "Con nhỏ hơn cha mẹ ≥ 18 tuổi",
        f"{child_bad} vi phạm / {link_child} cặp",
        "0 vi phạm",
        "-",
        child_bad == 0 and link_child > 0,
    )
    report.add(
        "Vợ chồng lệch ≤ 15 tuổi",
        f"{spouse_bad} vi phạm / {link_spouse} cặp",
        "0 vi phạm",
        "-",
        spouse_bad == 0 and link_spouse > 0,
    )

    owner = phones["owner_customer_id"].dropna()
    phone_ok = bool(
        phones["number_id"].is_unique
        and phones["msisdn"].is_unique
        and customers["phone_number_id"].is_unique
        and owner.is_unique
        and set(owner.astype(str)) == set(customers["customer_id"].astype(str))
    )
    mapped = customers.merge(phones, left_on="phone_number_id", right_on="number_id", how="left")
    phone_ok = phone_ok and bool(mapped["number_id"].notna().all())
    phone_ok = phone_ok and bool((mapped["owner_customer_id"].astype(str) == mapped["customer_id"].astype(str)).all())
    phone_ok = phone_ok and bool((mapped["owner_type"] == "customer").all())
    external = phones["owner_type"] != "customer"
    phone_ok = phone_ok and bool(phones.loc[external, "owner_customer_id"].isna().all())
    phone_ok = phone_ok and bool(phones["msisdn"].str.fullmatch(r"0\d{9}").all())
    report.add("Mỗi số một chủ, msisdn không trùng", "đạt" if phone_ok else "lỗi", "đạt", "-", phone_ok)

    fk_ok = bool(guardians["customer_id"].isin(customers["customer_id"]).all())
    fk_ok = fk_ok and bool(guardians["guardian_phone_number_id"].isin(phones["number_id"]).all())
    named = guardians["guardian_customer_id"].dropna()
    fk_ok = fk_ok and bool(named.isin(customers["customer_id"]).all())
    fk_ok = fk_ok and bool(customers["customer_id"].is_unique and latent["customer_id"].is_unique)
    fk_ok = fk_ok and bool(set(customers["customer_id"]) == set(latent["customer_id"]))
    report.add("Mọi khóa ngoại tồn tại", "đạt" if fk_ok else "lỗi", "đạt", "-", fk_ok)

    def before(series: pd.Series, bound: pd.Timestamp) -> bool:
        if series.isna().any():
            return False
        zone = zone_name(series)
        return zone == TZ and bool(series.max() < bound)

    created_ok = before(customers["created_at"], sim_start)
    phone_time_ok = before(phones["registered_at"], sim_end)
    guard_time_ok = before(guardians["registered_at"], sim_end)
    report.add("created_at < sim_start, múi giờ VN", "đạt" if created_ok else "lỗi", "đạt", "-", created_ok)
    report.add("registered_at < sim_end, múi giờ VN", "đạt" if phone_time_ok and guard_time_ok else "lỗi", "đạt", "-", phone_time_ok and guard_time_ok)

    money = customers[["customer_id", "segment"]].merge(latent[["customer_id", "income_monthly"]], on="customer_id")
    income_ok_dtype = str(money["income_monthly"].dtype) == "int64"
    report.add("income_monthly là int64 VND", str(money["income_monthly"].dtype), "int64", "-", income_ok_dtype)
    for key, label in SEGMENT_OF.items():
        target = float(pop["income_lognormal"][key]["median"])
        actual = float(money.loc[money["segment"] == label, "income_monthly"].median())
        rel = actual / target - 1.0
        report.add(
            f"Thu nhập trung vị {label}",
            f"{actual:,.0f}",
            f"{target:,.0f}",
            f"{100 * rel:+.2f}%",
            abs(rel) <= 0.10,
        )

    alone = customers[["customer_id"]].merge(latent[["customer_id", "lives_alone"]], on="customer_id")
    alone["elderly"] = age >= elderly_age
    rate_el = float(alone.loc[alone["elderly"], "lives_alone"].mean())
    rate_ot = float(alone.loc[~alone["elderly"], "lives_alone"].mean())
    report.add(
        "Sống một mình: cao tuổi > còn lại",
        f"{pct(rate_el)} > {pct(rate_ot)}",
        "cao tuổi cao hơn",
        f"{100 * (rate_el - rate_ot):+.2f} đ.p",
        rate_el > rate_ot,
    )

    names = joined.copy()
    names["surname"] = names["full_name"].str.split().str[0]
    same = 0
    total = 0
    for _, group in names.groupby("family_group_id", sort=False):
        surnames = group["surname"].to_numpy()
        m = len(surnames)
        if m < 2:
            continue
        total += m * (m - 1) // 2
        same += int(np.triu(surnames[:, None] == surnames[None, :], 1).sum())
    pair_rate = same / total if total else 0.0
    report.add("Cặp cùng hộ trùng họ", pct(pair_rate), "> 70%", f"{100 * pair_rate:.2f}%", pair_rate > 0.70)
    households_per_surname = names.groupby("surname")["family_group_id"].nunique()
    cross = bool((households_per_surname >= 2).any()) and int(households_per_surname.max()) > 1
    report.add(
        "Khác hộ vẫn trùng họ",
        f"họ đông nhất ở {int(households_per_surname.max())} hộ",
        "có họ dùng ở nhiều hộ",
        "-",
        cross,
    )

    ascii_ok = bool(customers["name_ascii"].equals(customers["full_name"].map(to_name_ascii).astype(customers["name_ascii"].dtype)))
    report.add("name_ascii in hoa, Đ→D", "đạt" if ascii_ok else "lỗi", "đạt", "-", ascii_ok)
    sus = latent["susceptibility"]
    sus_ok = bool(sus.notna().all() and (sus >= 0).all() and (sus <= 1).all())
    report.add("susceptibility trong [0, 1]", "đạt" if sus_ok else "lỗi", "đạt", "-", sus_ok)

    observed_cols: set[str] = set()
    for frame in (customers, phones, guardians):
        observed_cols |= set(frame.columns)
    truth_cols = set(latent.columns)
    overlap = observed_cols & truth_cols
    # device_id xuất hiện khi đã chạy bước 2; cả hai đều là khóa nối bảng.
    report.add(
        "Cột observed ∩ truth",
        "{" + ", ".join(sorted(overlap)) + "}",
        "chỉ khóa",
        "-",
        overlap <= {"customer_id", "device_id"} and "customer_id" in overlap,
    )

    # Tham khảo, không làm rớt kiểm tra.
    hh_size = joined.groupby("family_group_id").size()
    size_share = hh_size.value_counts(normalize=True).sort_index()
    parts = [f"{size}:{pct(float(size_share.get(size, 0.0)))}" for size in (1, 2, 3, 4, 5)]
    print()
    print("Tham khảo (không tính ĐẠT/LỖI)")
    print("  Quy mô hộ:", ", ".join(parts))
    elderly_mask = age >= elderly_age
    guarded = set(guardians["customer_id"].astype(str))
    ids = customers["customer_id"].astype(str).to_numpy()
    g_el = np.mean([cid in guarded for cid in ids[elderly_mask]])
    g_ot = np.mean([cid in guarded for cid in ids[~elderly_mask]])
    print(f"  Giám hộ: cao tuổi {pct(float(g_el))} (mục tiêu {pct(float(pop['guardian_share']['elderly']))}), còn lại {pct(float(g_ot))} (mục tiêu {pct(float(pop['guardian_share']['other']))})")
    recent = float(((phones["registered_at"] >= sim_start) & (phones["registered_at"] < sim_end)).mean())
    print(f"  SIM đăng ký trong cửa sổ: {pct(recent)} (mục tiêu {pct(float(pop['recent_sim_share']))})")
    prefix_telco = {}
    for telco, prefixes in {
        "Viettel": ["086", "096", "097", "098", "032", "033", "034", "035", "036", "037", "038", "039"],
        "VNPT": ["081", "082", "083", "084", "085", "088", "091", "094"],
        "MobiFone": ["070", "076", "077", "078", "079", "089", "090", "093"],
        "Vietnamobile": ["052", "056", "058", "092"],
    }.items():
        for prefix in prefixes:
            prefix_telco[prefix] = telco
    ported = float((phones["msisdn"].str[:3].map(prefix_telco) != phones["telco"]).mean())
    print(f"  Chuyển mạng (đầu số khác nhà mạng hiện tại): {pct(ported)} (mục tiêu {pct(float(pop['ported_share']))})")
    if child_n:
        print(f"  Con ở thành phố khác hộ: {pct(child_away / child_n)} (mục tiêu 20%)")
    print()
    report.print()
    if not report.ok():
        failed = sum(1 for row in report.rows if not row[-1])
        print(f"\n{failed} mục không đạt.")
        sys.exit(1)
    print("\nTất cả kiểm tra bước 1 đều đạt.")


if __name__ == "__main__":
    main()
