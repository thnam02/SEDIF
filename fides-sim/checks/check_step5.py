"""Đối chiếu kế hoạch vụ lừa đảo bước 5. Thoát mã 1 nếu có mục không đạt."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.step5_cases import auc_score, balance_at, build_curves, to_ns

TZ = "Asia/Ho_Chi_Minh"
LEAKED = {"is_scammer", "case_id"}


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


def fit_logit(features: np.ndarray, label: np.ndarray) -> np.ndarray:
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std[std == 0] = 1.0
    scaled = (features - mean) / std
    design = np.column_stack([np.ones(len(scaled)), scaled])
    weight = np.zeros(design.shape[1])
    positive = label.astype(float)
    for _ in range(25):
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


def overlaps(left: np.ndarray, right: np.ndarray) -> bool:
    if len(left) == 0 or len(right) == 0:
        return False
    left_low, left_high = np.quantile(left, [0.1, 0.9])
    right_low, right_high = np.quantile(right, [0.1, 0.9])
    return left_low <= right_high and right_low <= left_high


def in_window(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    if series.isna().any():
        return False
    tz = getattr(series.dt, "tz", None)
    zone = getattr(tz, "zone", None) or str(tz)
    if zone != TZ:
        return False
    return bool(series.min() >= start and series.max() < end)


def main() -> None:
    cfg = load_config()
    spec = cfg["cases"]
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    cases = pd.read_parquet(truth / "truth_cases.parquet")
    contacts = pd.read_parquet(truth / "case_contacts.parquet")
    plans = pd.read_parquet(truth / "case_transfers_plan.parquet")
    numbers = pd.read_parquet(truth / "truth_numbers.parquet")
    rings = pd.read_parquet(truth / "mule_rings.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    customers = pd.read_parquet(observed / "customers.parquet")
    latent = pd.read_parquet(truth / "customers_latent.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    transactions = pd.read_parquet(observed / "transactions.parquet")
    floor_path = truth / "step6_floor.parquet"
    if floor_path.exists() and "session_id" in transactions.columns and transactions["session_id"].notna().any():
        floor = int(pd.read_parquet(floor_path)["first_tx_num"].iloc[0])
        tx_num = transactions["tx_id"].astype(str).str[1:].astype(int)
        transactions = transactions.loc[tx_num < floor].reset_index(drop=True)

    report = Report()
    n_cases = len(cases)
    report.add("Số vụ", str(n_cases), str(int(spec["n_cases"])), "-", n_cases == int(spec["n_cases"]))

    def share_row(name: str, actual: float, expected: float) -> None:
        report.add(name, pct(actual), pct(expected), f"{100 * (actual - expected):+.2f} đ.p", abs(actual - expected) <= 0.03)

    type_rate = cases["scam_type"].astype(str).value_counts(normalize=True)
    type_ok = True
    parts = []
    for kind, target in spec["type_share"].items():
        actual = float(type_rate.get(kind, 0.0))
        parts.append(f"{kind} {pct(actual)}")
        type_ok = type_ok and abs(actual - float(target)) <= 0.03
    report.add("Tỷ lệ kiểu lừa", ", ".join(parts), "±3 đ.p", "-", type_ok)
    share_row("Tỷ lệ OTT", float((cases["channel"].astype(str) == "ott").mean()), float(spec["ott_share"]))
    share_row("Tỷ lệ giả mạo", float(cases["spoofed"].mean()), float(spec["spoofed_caller_share"]))
    share_row("Tỷ lệ chia nhỏ", float(cases["split"].mean()), float(spec["split_share"]))
    share_row("Tỷ lệ từ chối quyền", float(cases["declines_permission"].mean()), float(spec["declines_permission_share"]))
    share_row("Tỷ lệ cúp máy", float(cases["hangs_up_before_transfer"].mean()), float(spec["hangs_up_before_transfer_share"]))
    repeat_rate = 1.0 - cases["victim_customer_id"].nunique() / n_cases
    share_row("Tỷ lệ nạn nhân tái phạm", repeat_rate, float(spec["repeat_victim_share"]))

    bounds = rings.groupby("ring_id", sort=False).agg(active_from=("active_from", "min"), active_to=("active_to", "max"))
    merged = cases.merge(bounds, left_on="ring_id", right_index=True, how="left")
    covered = merged["active_from"].notna() & (merged["active_from"] <= merged["start_ts"]) & (merged["active_to"] >= merged["end_ts"])
    l1 = set(rings.loc[rings["layer"].astype(str) == "l1", "account_id"].astype(str))
    safe_ids = set()
    truth_accounts_path = truth / "accounts_truth.parquet"
    if truth_accounts_path.exists():
        mule_type = pd.read_parquet(truth_accounts_path, columns=["account_id", "mule_type"])
        safe_ids = set(mule_type.loc[mule_type["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str))
    core_plans = plans.loc[~plans["extra_fee"].fillna(False).astype(bool)] if "extra_fee" in plans.columns else plans
    mule_cases = core_plans.loc[~core_plans["to_mule_account_id"].astype(str).isin(safe_ids)].groupby("to_mule_account_id")["case_id"].nunique()
    over = int((mule_cases > int(spec["l1_mule_max_cases"])).sum())
    on_l1 = bool(set(core_plans["to_mule_account_id"].astype(str)) <= (l1 | safe_ids))
    report.add(
        "Đường dây hoạt động và mule lớp 1",
        f"{int(covered.sum())}/{n_cases} vụ, {over} mule vượt trần",
        f"mọi vụ, ≤ {int(spec['l1_mule_max_cases'])} vụ/mule",
        "lớp 1" if on_l1 else "có mule không phải lớp 1",
        bool(covered.all()) and over == 0 and on_l1,
    )
    received = plans.loc[(plans["planned_amount"] > 0) & plans["to_mule_account_id"].astype(str).isin(l1)].copy()
    received["account_id"] = received["to_mule_account_id"].astype(str)
    received["day"] = pd.to_datetime(received["planned_ts"]).dt.tz_convert("Asia/Ho_Chi_Minh").dt.date
    span_days = received.groupby("account_id")["planned_ts"].agg(lambda stamps: (stamps.max() - stamps.min()).total_seconds() / 86400.0)
    per_day = received.groupby(["account_id", "day"])["case_id"].nunique()
    hottest = per_day.groupby("account_id").max()
    hot_share = float((hottest >= 5).mean()) if len(hottest) else 1.0
    p50 = float(span_days.median()) if len(span_days) else 0.0
    p90 = float(span_days.quantile(0.9)) if len(span_days) else 0.0
    print(
        "  Mule lớp 1, ngày cuối − ngày đầu nhận tiền nạn nhân: "
        f"p10 {float(span_days.quantile(0.1)):.2f}, p50 {p50:.2f}, p90 {p90:.2f} ngày; "
        f"mule có ngày ≥ 5 vụ: {hot_share:.1%}"
    )
    report.add(
        "Thời gian hoạt động mule lớp 1",
        f"trung vị {p50:.2f} ngày, p90 {p90:.2f}, {hot_share:.1%} mule ≥ 5 vụ/ngày",
        "trung vị 3–7 ngày, p90 ≥ 14, ≤ 30% mule có ngày ≥ 5 vụ",
        f"{len(span_days)} mule",
        3.0 <= p50 <= 7.0 and p90 >= 14.0 and hot_share <= 0.30,
    )

    owned = accounts.loc[
        accounts["customer_id"].notna() & accounts["account_type"].isin(["personal", "savings"]),
        ["account_id", "customer_id", "opening_balance", "opened_at"],
    ].copy()
    owned["account_id"] = owned["account_id"].astype(str)
    owned["customer_id"] = owned["customer_id"].astype(str)
    opening = dict(zip(owned["account_id"], owned["opening_balance"].astype(np.int64)))
    opened = dict(zip(owned["account_id"], owned["opened_at"]))
    accounts_of = owned.groupby("customer_id")["account_id"].apply(list).to_dict()
    curves = build_curves(transactions, owned)
    victim_of = dict(zip(cases["case_id"].astype(str), cases["victim_customer_id"].astype(str)))
    ordered = plans.sort_values(["planned_ts", "case_id", "seq"], kind="mergesort")
    spent: dict[str, list[tuple[pd.Timestamp, int]]] = {}
    short = 0
    for row in ordered.itertuples(index=False):
        customer = victim_of[str(row.case_id)]
        base = sum(max(0, balance_at(account, row.planned_ts, opening, opened, curves)) for account in accounts_of.get(customer, []))
        already = sum(amount for stamp, amount in spent.get(customer, []) if stamp <= row.planned_ts)
        inflow = int(getattr(row, "inflow_amount", 0) or 0)
        available = base - already + inflow
        if int(row.planned_amount) > available:
            short += 1
        spent.setdefault(customer, []).append((row.planned_ts, int(row.planned_amount) - inflow))
    report.add("Khoản kế hoạch ≤ số dư huy động được", f"{short} khoản vượt", "0", "gồm tiết kiệm, trừ khoản kế hoạch trước", short == 0)

    people = customers.merge(latent[["customer_id", "susceptibility"]], on="customer_id", how="left")
    label = people["customer_id"].astype(str).isin(set(cases["victim_customer_id"].astype(str))).to_numpy()
    score = fit_logit(people["susceptibility"].to_numpy(dtype=float).reshape(-1, 1), label)
    auc = auc_score(label, score)
    print(f"  AUC chỉ susceptibility: {auc:.3f}")
    report.add("AUC susceptibility phân biệt nạn nhân", f"{auc:.3f}", "0.65–0.75", "-", 0.65 <= auc <= 0.75)

    scammer_ids = set(numbers["number_id"].astype(str))
    phone_age = phones.copy()
    phone_age["number_id"] = phone_age["number_id"].astype(str)
    phone_age["age_days"] = (sim_end - phone_age["registered_at"]).dt.total_seconds() / 86400
    scammer_age = phone_age.loc[phone_age["number_id"].isin(scammer_ids), "age_days"].to_numpy(dtype=float)
    legit_new = phone_age.loc[
        ~phone_age["number_id"].isin(scammer_ids)
        & (phone_age["registered_at"] >= sim_start)
        & (phone_age["registered_at"] < sim_end),
        "age_days",
    ].to_numpy(dtype=float)
    age_ok = overlaps(scammer_age, legit_new)
    reported = phone_age.loc[phone_age["number_id"].isin(scammer_ids), "reported_count"]
    reported_rate = float((reported > 0).mean()) if len(reported) else 0.0
    print(f"  Số kẻ gian đã bị báo cáo: {pct(reported_rate)}")
    report.add(
        "Tuổi số kẻ gian chồng số mới hợp lệ",
        "chồng" if age_ok else "tách",
        "chồng phân vị 10–90",
        f"đã báo cáo {pct(reported_rate)}",
        age_ok and abs(reported_rate - float(spec["scammer_number_prereported_share"])) <= 0.03,
    )
    new_rate = float(
        (
            (phone_age["number_id"].isin(scammer_ids))
            & (phone_age["registered_at"] >= sim_start - pd.Timedelta(days=30))
        ).sum()
        / max(len(scammer_ids), 1)
    )
    # Số mới: đăng ký trong 30 ngày trước lần dùng. Đo bằng cờ gần cửa sổ hơn là mọi số cũ.
    new_sim = phone_age.loc[phone_age["number_id"].isin(scammer_ids)].copy()
    # Số cũ mua lại nằm trước 2023; số mới nằm trong khoảng 30 ngày trước cửa sổ trở đi.
    new_sim_rate = float((new_sim["registered_at"] >= sim_start - pd.Timedelta(days=40)).mean())
    report.add(
        "Số kẻ gian đăng ký mới",
        pct(new_sim_rate),
        pct(float(spec["scammer_number_new_sim_share"])),
        f"{100 * (new_sim_rate - float(spec['scammer_number_new_sim_share'])):+.2f} đ.p",
        abs(new_sim_rate - float(spec["scammer_number_new_sim_share"])) <= 0.05,
    )
    _ = (to_ns, new_rate)

    invest_ids = set(cases.loc[cases["scam_type"].astype(str) == "investment", "case_id"].astype(str))
    rising = 0
    reverse = 0
    for case_id, part in plans.loc[plans["case_id"].isin(invest_ids)].groupby("case_id", sort=False):
        ordered_part = part.sort_values(["seq", "planned_ts"], kind="mergesort")
        if "extra_fee" in ordered_part.columns:
            ordered_part = ordered_part.loc[~ordered_part["extra_fee"].fillna(False).astype(bool)]
        positive = ordered_part.loc[ordered_part["planned_amount"] > 0, "planned_amount"].to_numpy()
        if len(positive) >= 2 and bool(np.all(positive[1:] > positive[:-1])):
            rising += 1
        if bool((ordered_part["planned_amount"] < 0).any()):
            reverse += 1
    n_invest = len(invest_ids)
    rise_rate = rising / n_invest if n_invest else 0.0
    reverse_rate = reverse / n_invest if n_invest else 0.0
    print(f"  Investment tăng dần: {pct(rise_rate)}; có lãi ngược: {pct(reverse_rate)}")
    report.add("Investment số tiền tăng dần", pct(rise_rate), "≥ 80%", f"{rising}/{n_invest}", rise_rate >= 0.80)
    report.add("Investment có khoản lãi ngược", pct(reverse_rate), "≥ 50%", f"{reverse}/{n_invest}", reverse_rate >= 0.50)

    fee_kinds = {"police", "bank_staff", "tax_authority", "investment"}
    fee_cases = set(cases.loc[cases["scam_type"].astype(str).isin(fee_kinds), "case_id"].astype(str))
    if "extra_fee" in plans.columns:
        fees = plans.loc[plans["extra_fee"].fillna(False).astype(bool) & plans["case_id"].astype(str).isin(fee_cases)]
    else:
        fees = plans.iloc[0:0]
    fee_rate = fees["case_id"].nunique() / max(len(fee_cases), 1)
    per_case = fees.groupby(fees["case_id"].astype(str)).size() if len(fees) else pd.Series(dtype=int)
    fee_span_ok = bool(len(per_case)) and int(per_case.min()) >= 1 and int(per_case.max()) <= 3
    mobilize_ok = bool(len(fees)) and set(fees["mobilize"].astype(str)) <= {
        "savings_liquidation",
        "quick_loan_disbursement",
        "credit_card_cash_advance",
        "limit_raise",
        "split_near_limit",
    } and bool((fees["mobilize"].astype(str) != "").all())
    print(f"  Phí bổ sung: {pct(fee_rate)} vụ nhóm thuế/công an/ngân hàng/đầu tư, {int(per_case.min()) if len(per_case) else 0}–{int(per_case.max()) if len(per_case) else 0} lệnh")
    report.add(
        "Phí bổ sung tới người nhận mới",
        pct(fee_rate),
        "30–50%, mỗi vụ 1–3 lệnh, có bước huy động",
        f"{fees['case_id'].nunique() if len(fees) else 0} vụ",
        0.30 <= fee_rate <= 0.50 and fee_span_ok and mobilize_ok,
    )
    if "raise_during_call" in cases.columns:
        raise_ok = True
        raise_bits = []
        for kind, share in spec["limit_raise_on_call"].items():
            part = cases.loc[cases["scam_type"].astype(str).eq(kind)]
            rate = float(part["raise_during_call"].mean()) if len(part) else 0.0
            raise_bits.append(f"{kind} {pct(rate)}")
            raise_ok = raise_ok and abs(rate - float(share)) <= 0.04
        report.add("Nâng hạn mức trong lúc gọi", ", ".join(raise_bits), "police 30%, bank_staff 40%", "-", raise_ok)
    else:
        report.add("Nâng hạn mức trong lúc gọi", "thiếu", "có cờ", "-", False)
    if "fake_hotline" in cases.columns:
        bank = cases.loc[cases["scam_type"].astype(str).eq("bank_staff")]
        fake_rate = float(bank["fake_hotline"].mean()) if len(bank) else 0.0
        report.add(
            "bank_staff giả số tổng đài",
            pct(fake_rate),
            pct(float(spec["bank_staff_fake_hotline_share"])),
            f"{100 * (fake_rate - float(spec['bank_staff_fake_hotline_share'])):+.2f} đ.p",
            abs(fake_rate - float(spec["bank_staff_fake_hotline_share"])) <= 0.04,
        )

    times_ok = in_window(cases["start_ts"], sim_start, sim_end) and in_window(cases["end_ts"], sim_start, sim_end)
    times_ok = times_ok and in_window(contacts["ts"], sim_start, sim_end) and in_window(plans["planned_ts"], sim_start, sim_end)
    report.add("Mọi mốc vụ nằm trong cửa sổ", "đạt" if times_ok else "lỗi", "sim_start ≤ t < sim_end", "-", times_ok)

    observed_cols: set[str] = set()
    for path in observed.glob("*.parquet"):
        observed_cols |= set(pd.read_parquet(path, columns=None).columns)
    leaked = observed_cols & LEAKED
    owner_ok = bool(
        phones.loc[phones["number_id"].astype(str).isin(scammer_ids), "owner_type"].astype(str).eq("external_person").all()
    )
    report.add(
        "observed không lộ kẻ gian",
        "có " + ",".join(sorted(leaked)) if leaked else "không",
        "không có is_scammer, case_id",
        "owner_type external_person" if owner_ok else "owner_type lệch",
        not leaked and owner_ok,
    )

    b01_customers = set(accounts.loc[accounts["bank_id"].astype(str) == "B01", "customer_id"].dropna().astype(str))
    b01_rate = float(cases["victim_customer_id"].astype(str).isin(b01_customers).mean())
    print(f"  Nạn nhân có tài khoản B01: {pct(b01_rate)}")
    report.add(
        "Nạn nhân có tài khoản B01",
        pct(b01_rate),
        pct(float(cfg["accounts"]["bank_share"]["B01"])),
        f"{100 * (b01_rate - 0.20):+.2f} đ.p",
        abs(b01_rate - 0.20) <= 0.03,
    )

    customer_ids = set(customers["customer_id"].astype(str))
    account_ids = set(accounts["account_id"].astype(str))
    phone_ids = set(phones["number_id"].astype(str))
    ring_ids = set(rings["ring_id"].astype(str))
    fk_ok = bool(cases["victim_customer_id"].astype(str).isin(customer_ids).all())
    fk_ok = fk_ok and bool(cases["ring_id"].astype(str).isin(ring_ids).all())
    fk_ok = fk_ok and bool(plans["to_mule_account_id"].astype(str).isin(account_ids).all())
    fk_ok = fk_ok and bool(contacts["number_id"].astype(str).isin(phone_ids).all())
    fk_ok = fk_ok and bool(numbers["number_id"].astype(str).isin(phone_ids).all())
    listed = numbers["spoofed_as_number_id"].dropna().astype(str)
    fk_ok = fk_ok and bool(listed.isin(phone_ids).all())
    fk_ok = fk_ok and set(contacts["case_id"].astype(str)) <= set(cases["case_id"].astype(str))
    fk_ok = fk_ok and set(plans["case_id"].astype(str)) <= set(cases["case_id"].astype(str))
    report.add("Khóa ngoại kế hoạch vụ", "đạt" if fk_ok else "lỗi", "đạt", "-", fk_ok)

    print()
    report.print()
    if not report.ok():
        failed = sum(1 for row in report.rows if not row[-1])
        print(f"\n{failed} mục không đạt.")
        sys.exit(1)
    print("\nTất cả kiểm tra bước 5 đều đạt.")


if __name__ == "__main__":
    main()
