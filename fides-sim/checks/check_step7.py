"""Đối chiếu nhãn, SIMO báo cáo và mô hình phản ứng bước 7. Thoát mã 1 nếu có mục không đạt."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from checks.check_step6 import fit_logit
from sim.step4_mules import as_timestamp

TZ = "Asia/Ho_Chi_Minh"
FRAUD_TYPES = {"police", "investment", "bank_staff", "relative", "deposit", "romance"}
LEAKED = {"is_fraud", "label_observed", "case_id"}


def load_config() -> dict:
    import yaml

    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def pct(rate: float) -> str:
    return f"{100 * rate:.2f}%"


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str, bool]] = []

    def add(self, item: str, actual: str, expected: str, detail: str, ok: bool) -> None:
        self.rows.append((item, actual, expected, detail, ok))

    def ok(self) -> bool:
        return all(row[-1] for row in self.rows)

    def print(self) -> None:
        print()
        widths = [max(len(row[index]) for row in self.rows + [("Hạng mục", "Thực tế", "Mục tiêu", "Lệch", "Kết quả")]) for index in range(4)]
        header = ("Hạng mục", "Thực tế", "Mục tiêu", "Lệch", "Kết quả")
        print("  ".join(text.ljust(widths[index] if index < 4 else 0) for index, text in enumerate(header)))
        print("  ".join("-" * width for width in widths) + "  " + "-" * 7)
        for item, actual, expected, detail, ok in self.rows:
            print(f"{item.ljust(widths[0])}  {actual.ljust(widths[1])}  {expected.ljust(widths[2])}  {detail.ljust(widths[3])}  {'ĐẠT' if ok else 'LỖI'}")


def main() -> None:
    cfg = load_config()
    spec = cfg["labels"]
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    labels = pd.read_parquet(truth / "labels.parquet")
    cases = pd.read_parquet(truth / "truth_cases.parquet")
    plans = pd.read_parquet(truth / "case_transfers_plan.parquet")
    model = pd.read_parquet(truth / "response_model.parquet")
    simo = pd.read_parquet(observed / "simo_list.parquet")
    numbers = pd.read_parquet(observed / "number_reports.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    truth_numbers = pd.read_parquet(truth / "truth_numbers.parquet")
    transactions = pd.read_parquet(observed / "transactions.parquet")
    sessions = pd.read_parquet(observed / "sessions.parquet")
    scenarios = pd.read_parquet(truth / "truth_scenarios.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    truth_accounts = pd.read_parquet(truth / "accounts_truth.parquet")
    customers = pd.read_parquet(observed / "customers.parquet")
    latent = pd.read_parquet(truth / "customers_latent.parquet")
    report = Report()

    safe_ids = set(truth_accounts.loc[truth_accounts["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str))
    safe_cases = set(plans.loc[plans["to_mule_account_id"].astype(str).isin(safe_ids) & (plans["planned_amount"] > 0), "case_id"].astype(str))
    type_of = dict(zip(cases["case_id"].astype(str), cases["scam_type"].astype(str)))
    fraud = labels.loc[labels["is_fraud"]].copy()
    fraud["case_id"] = fraud["case_id"].astype(str)
    case_reported = fraud.groupby("case_id")["reported"].any()
    rate_ok = True
    parts = []
    groups = {kind: [] for kind in list(spec["report_rate"])}
    for case_id, flagged in case_reported.items():
        if case_id in {"nan", "<NA>", "None"}:
            continue
        kind = "own_name_safe_account" if case_id in safe_cases else type_of.get(str(case_id), "")
        if kind not in groups:
            continue
        groups[kind].append(bool(flagged))
    for kind, target in spec["report_rate"].items():
        sample = groups.get(kind, [])
        actual = float(np.mean(sample)) if sample else 0.0
        parts.append(f"{kind} {pct(actual)}")
        rate_ok = rate_ok and abs(actual - float(target)) <= 0.03 and len(sample) > 0
    report.add("Tỷ lệ reported theo kiểu", ", ".join(parts), "±3 đ.p", "-", rate_ok)

    delays = spec["report_delay_days"]
    last_tx = fraud.groupby("case_id")["ts"].max()
    delay_ok = True
    checked_delay = 0
    for case_id, part in fraud.loc[fraud["reported"]].groupby("case_id", sort=False):
        if str(case_id) not in type_of:
            continue
        kind = type_of[str(case_id)]
        low, high = delays[kind]
        stamp = part["report_ts"].iloc[0]
        delta = (stamp - last_tx.loc[case_id]).total_seconds() / 86400
        checked_delay += 1
        if delta < float(low) - 1e-6 or delta > float(high) + 1e-6:
            delay_ok = False
    later = fraud.loc[fraud["reported"], "label_available_at"] > fraud.loc[fraud["reported"], "ts"]
    false_later = labels.loc[labels["reported"] & ~labels["is_fraud"]]
    false_ok = True if false_later.empty else bool((false_later["label_available_at"] > false_later["ts"]).all())
    report.add(
        "Độ trễ báo cáo và nhãn sau giao dịch",
        f"{checked_delay} vụ",
        "trong khoảng, label_available_at > ts",
        "đạt" if delay_ok and bool(later.all()) and false_ok else "lệch",
        delay_ok and bool(later.all()) and false_ok and checked_delay > 0,
    )

    consistent = True
    for case_id, part in fraud.groupby("case_id", sort=False):
        if not bool(part["reported"].any()):
            continue
        stamp = part.loc[part["reported"], "report_ts"].iloc[0]
        early = part.loc[part["ts"] < stamp]
        if len(early) and not bool(early["reported"].all()):
            consistent = False
            break
    report.add("Nhãn cấp vụ", "đạt" if consistent else "lệch", "không có reported=false trước report_ts", "-", consistent)

    legit = labels.loc[~labels["is_fraud"]]
    false_rate = float(legit["reported"].mean()) if len(legit) else 0.0
    target_false = float(spec["false_report_rate"])
    report.add(
        "Báo nhầm giao dịch hợp lệ",
        f"{false_rate:.6f}",
        f"{target_false:.6f}",
        f"{int(legit['reported'].sum())}/{len(legit)}",
        abs(false_rate - target_false) <= max(0.00005, 3 * np.sqrt(target_false * (1 - target_false) / max(len(legit), 1))),
    )

    print("  Tỷ lệ nhãn khả dụng trên giao dịch lừa đảo:")
    marks = {
        "sim_start+60 ngày": sim_start + pd.Timedelta(days=60),
        "sim_end": sim_end,
        "sim_end+30 ngày": sim_end + pd.Timedelta(days=30),
    }
    ready = fraud["reported"] & fraud["label_available_at"].notna()
    for name, stamp in marks.items():
        rate = float((ready & (fraud["label_available_at"] <= stamp)).mean()) if len(fraud) else 0.0
        print(f"    {name}: {pct(rate)}")
    report.add("Bảng nhãn khả dụng", "đã in", "3 mốc", "-", len(fraud) > 0)

    tx = transactions[["tx_id", "ts", "to_account", "amount"]].copy()
    tx["tx_id"] = tx["tx_id"].astype(str)
    rings = pd.read_parquet(truth / "mule_rings.parquet", columns=["account_id"])
    mule_ids = set(rings["account_id"].astype(str)) | safe_ids
    fraud_tx = fraud.merge(tx[["tx_id", "to_account"]].rename(columns={"tx_id": "txn_id"}), on="txn_id", how="left")
    fraud_tx = fraud_tx.loc[fraud_tx["to_account"].astype(str).isin(mule_ids)]
    listed = simo[["account_id", "listed_at"]].copy()
    listed["account_id"] = listed["account_id"].astype(str)
    first_list = listed.groupby("account_id")["listed_at"].min()
    payee = fraud_tx["to_account"].astype(str)
    listed_at = payee.map(first_list)
    conflict = listed_at.notna() & (listed_at + pd.Timedelta(days=1) < fraud_tx["ts"])
    conflict_rate = float(conflict.mean()) if len(fraud_tx) else 0.0
    print(f"  Giao dịch lừa đảo vào mule đã listed_at hơn 1 ngày: {pct(conflict_rate)}")
    conflict_ok = conflict_rate < 0.02
    report.add(
        "Xung đột SIMO",
        pct(conflict_rate),
        "< 2%",
        "đạt" if conflict_ok else "cần mule nghỉ sau khi bị liệt kê ở bước 5",
        conflict_ok,
    )

    number_ok = len(numbers) > 0 and bool(numbers["reported_at"].notna().all())
    scammer_ids = set(truth_numbers["number_id"].astype(str))
    reported_count = phones.loc[phones["number_id"].astype(str).isin(scammer_ids), "reported_count"]
    pre_rate = float((reported_count > 0).mean()) if len(reported_count) else 0.0
    count_ok = abs(pre_rate - float(cfg["cases"]["scammer_number_prereported_share"])) <= 0.03
    report.add(
        "number_reports và reported_count",
        f"{len(numbers)} dòng, đếm sẵn {pct(pre_rate)}",
        "có thời điểm, reported_count như bước 5",
        "-",
        number_ok and count_ok,
    )

    needed_groups = {"police", "bank_staff", "relative", "deposit", "investment", "romance", "own_name_safe_account", "legit"}
    combo = model.groupby(["scenario_group", "segment", "action", "guardian_notified"], sort=False).size()
    full = True
    for group in needed_groups:
        for segment in ("thường", "cao tuổi"):
            for action in ("allow", "friction", "hold"):
                for guardian in (False, True):
                    if (group, segment, action, guardian) not in combo.index:
                        full = False
    monotone = True
    keys = ["scenario_group", "segment", "guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"]
    for _, part in model.groupby(keys, sort=False):
        order = {name: float(part.loc[part["action"].astype(str).eq(name), "p_stop"].iloc[0]) for name in ("allow", "friction", "hold")}
        abandon = {name: float(part.loc[part["action"].astype(str).eq(name), "p_abandon"].iloc[0]) for name in ("allow", "friction", "hold")}
        if not (order["hold"] >= order["friction"] >= order["allow"] and abandon["hold"] >= abandon["friction"] >= abandon["allow"]):
            monotone = False
            break
    source_ok = bool(model["source"].astype(str).eq("assumption").all())
    report.add(
        "response_model",
        f"{len(model)} dòng",
        "hold ≥ friction ≥ allow, đủ tổ hợp, source assumption",
        "đạt" if monotone and full and source_ok else "lệch",
        monotone and full and source_ok,
    )

    smoke(transactions, sessions, scenarios, accounts, customers, latent, truth_accounts, model)
    observed_cols: set[str] = set()
    for path in observed.glob("*.parquet"):
        observed_cols |= set(pd.read_parquet(path).columns)
    leaked = observed_cols & LEAKED
    report.add("observed không lộ nhãn", "có " + ",".join(sorted(leaked)) if leaked else "không", "không có is_fraud, label_observed, case_id", "-", not leaked)

    print()
    report.print()
    if not report.ok():
        failed = sum(1 for row in report.rows if not row[-1])
        print(f"\n{failed} mục không đạt.")
        sys.exit(1)
    print("\nTất cả kiểm tra bước 7 đều đạt.")


def smoke(transactions, sessions, scenarios, accounts, customers, latent, truth_accounts, model) -> None:
    sessions = sessions.copy()
    sessions["session_id"] = sessions["session_id"].astype(str)
    scenarios = scenarios.copy()
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    joined = sessions.merge(scenarios[["session_id", "scenario_type", "case_id", "hangs_up_before_transfer"]], on="session_id", how="left")
    tx = transactions.loc[transactions["session_id"].notna(), ["session_id", "amount", "from_balance_before", "ts", "to_account", "from_account", "tx_type"]].copy()
    tx["session_id"] = tx["session_id"].astype(str)
    main_tx = tx.sort_values("amount", ascending=False).drop_duplicates("session_id")
    feature = joined.merge(main_tx, on="session_id", how="left")
    owner = dict(zip(accounts["account_id"].astype(str), accounts["customer_id"].astype(str)))
    ratio = feature["amount"].fillna(0).to_numpy(dtype=float) / np.maximum(feature["from_balance_before"].fillna(1).to_numpy(dtype=float), 1.0)
    label = feature["scenario_type"].astype(str).isin(FRAUD_TYPES).to_numpy()
    hour = pd.to_datetime(feature["start_ts"]).dt.tz_convert(TZ).dt.hour.fillna(0).to_numpy(dtype=float)
    safe_ids = set(truth_accounts.loc[truth_accounts["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str))
    newish = feature["to_account"].astype(str).isin(safe_ids).to_numpy(dtype=float)
    matrix = np.column_stack([ratio, hour, newish])
    score = fit_logit(matrix, label)
    order = np.argsort(-score, kind="mergesort")
    n = len(feature)
    hold_n = max(1, int(round(0.002 * n)))
    friction_n = max(hold_n, int(round(0.01 * n)))
    action = np.array(["allow"] * n, dtype=object)
    action[order[:hold_n]] = "hold"
    action[order[hold_n:friction_n]] = "friction"
    feature = feature.reset_index(drop=True)
    feature["action"] = action
    feature["score"] = score
    segment_of = dict(zip(customers["customer_id"].astype(str), customers["segment"].astype(str)))
    guardian_of = dict(zip(customers["customer_id"].astype(str), customers["has_guardian"].astype(bool)))
    alone_of = dict(zip(latent["customer_id"].astype(str), latent["lives_alone"].astype(bool)))
    lookup = model.set_index(["scenario_group", "segment", "action", "guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"])
    saved = 0.0
    bothered = 0
    legit_n = 0
    complaints = 0.0
    feature["customer_id"] = feature["customer_id"].astype(str)
    feature = feature.sort_values(["customer_id", "start_ts"], kind="mergesort")
    prior: dict[str, list[pd.Timestamp]] = {}
    for row in feature.itertuples(index=False):
        fraud = str(row.scenario_type) in FRAUD_TYPES or str(row.to_account) in safe_ids
        if fraud and str(row.to_account) in safe_ids:
            group = "own_name_safe_account"
        elif fraud:
            group = str(row.scenario_type)
        else:
            group = "legit"
        segment = "cao tuổi" if segment_of.get(str(row.customer_id)) == "cao tuổi" else "thường"
        guardian = bool(row.action == "hold" and guardian_of.get(str(row.customer_id), False))
        coached = group in {"police", "bank_staff", "own_name_safe_account"}
        alone = bool(alone_of.get(str(row.customer_id), False))
        hangs = bool(row.hangs_up_before_transfer) if pd.notna(row.hangs_up_before_transfer) else False
        chosen = lookup.loc[(group, segment, str(row.action), guardian, coached, alone, hangs)]
        history = prior.setdefault(str(row.customer_id), [])
        if str(row.action) == "friction":
            recent = sum(1 for stamp in history if row.start_ts - stamp <= pd.Timedelta(days=30))
        else:
            recent = 0
        stop = float(chosen["p_stop"]) * (0.85 ** recent)
        if str(row.action) == "friction":
            history.append(row.start_ts)
        if not fraud:
            legit_n += 1
            if str(row.action) in {"friction", "hold"}:
                bothered += 1
            complaints += float(chosen["p_complaint"])
        elif str(row.action) in {"friction", "hold"}:
            saved += float(row.amount or 0) * stop
    bother_rate = bothered / legit_n if legit_n else 0.0
    print(
        f"  Smoke test can thiệp 1%: tiền lừa đảo tránh được {saved:,.0f} VND, "
        f"phiên hợp lệ bị làm phiền {pct(bother_rate)}, khiếu nại kỳ vọng {complaints:.1f}"
    )


if __name__ == "__main__":
    main()
