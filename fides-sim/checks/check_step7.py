"""Đối chiếu nhãn, SIMO báo cáo và mô hình phản ứng bước 7. Thoát mã 1 nếu có mục không đạt."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.step4_mules import as_timestamp

FIDES = ROOT.parent / "fides"
if str(FIDES) not in sys.path:
    sys.path.insert(0, str(FIDES))

from features.service import actions_from_families, session_families

TZ = "Asia/Ho_Chi_Minh"
FRAUD_TYPES = {"police", "investment", "bank_staff", "relative", "deposit", "romance", "tax_authority"}
LEAKED = {"is_fraud", "label_observed", "case_id"}
HARD_SAMPLES = ("legit_guided_by_relative", "legit_friend_loan")


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

    fraud["kind"] = fraud["case_id"].map(type_of)
    week = fraud.loc[(fraud["ts"] >= sim_start) & (fraud["ts"] < sim_end)].copy()
    week["week"] = ((week["ts"] - sim_start).dt.total_seconds() // (7 * 86400)).astype(int)
    print("  Giao dịch lừa đảo theo tuần trong cửa sổ:")
    for index, part in week.groupby("week", sort=True):
        opening = sim_start + pd.Timedelta(days=7 * int(index))
        print(f"    tuần {int(index)} từ {opening.date()}: {len(part)} giao dịch")
    marks = {
        "sim_start+30 ngày": sim_start + pd.Timedelta(days=30),
        "sim_start+60 ngày": sim_start + pd.Timedelta(days=60),
        "sim_end": sim_end,
        "sim_end+30 ngày": sim_end + pd.Timedelta(days=30),
    }
    print("  Nhãn khả dụng: trong các giao dịch lừa đảo có ts ≤ T, tỷ lệ label_available_at ≤ T")
    kind_order = ["police", "tax_authority", "bank_staff", "relative", "deposit", "investment", "romance"]
    police_60 = None
    investment_60 = None
    for name, stamp in marks.items():
        happened = fraud.loc[fraud["ts"] <= stamp]
        ready = happened["label_available_at"].notna() & (happened["label_available_at"] <= stamp)
        print(f"    {name}: {int(ready.sum())}/{len(happened)} = {pct(float(ready.mean()) if len(happened) else 0.0)}")
        for kind in kind_order:
            part = happened.loc[happened["kind"].astype(str).eq(kind)]
            flagged = part["label_available_at"].notna() & (part["label_available_at"] <= stamp)
            rate = float(flagged.mean()) if len(part) else 0.0
            print(f"      {kind}: {int(flagged.sum())}/{len(part)} = {pct(rate)}")
            if name == "sim_start+60 ngày" and kind == "police":
                police_60 = rate
            if name == "sim_start+60 ngày" and kind == "investment":
                investment_60 = rate
    label_ok = (
        police_60 is not None
        and investment_60 is not None
        and 0.55 <= police_60 <= 0.70
        and investment_60 <= police_60 - 0.15
    )
    report.add(
        "Nhãn khả dụng tại sim_start+60",
        f"police {pct(police_60 or 0.0)}, investment {pct(investment_60 or 0.0)}",
        "police 55–70%, investment thấp hơn ít nhất 15 đ.p",
        "mẫu số là giao dịch đã xảy ra",
        label_ok,
    )

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

    assumptions = spec["response_assumptions"]
    actions = list(assumptions["actions"])
    needed_groups = {"police", "bank_staff", "tax_authority", "relative", "deposit", "investment", "romance", "own_name_safe_account", "legit", "legit_guided_by_relative"}
    combo = model.groupby(["scenario_group", "segment", "action", "guardian_notified"], sort=False).size()
    full = True
    for group in needed_groups:
        for segment in ("thường", "cao tuổi"):
            for action in actions:
                for guardian in (False, True):
                    if (group, segment, action, guardian) not in combo.index:
                        full = False
    monotone = True
    keys = ["scenario_group", "segment", "guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"]
    for key, part in model.groupby(keys, sort=False):
        group = str(key[0])
        order = {name: float(part.loc[part["action"].astype(str).eq(name), "p_stop"].iloc[0]) for name in actions}
        abandon_names = [name for name in actions if not (group == "legit_guided_by_relative" and name == "hangup_required")]
        abandon = {name: float(part.loc[part["action"].astype(str).eq(name), "p_abandon"].iloc[0]) for name in abandon_names}
        stop_ok = all(order[actions[index]] <= order[actions[index + 1]] + 1e-9 for index in range(len(actions) - 1))
        leave_ok = all(abandon[abandon_names[index]] <= abandon[abandon_names[index + 1]] + 1e-9 for index in range(len(abandon_names) - 1))
        if not (stop_ok and leave_ok):
            monotone = False
            break
    guided_rows = model.loc[
        model["scenario_group"].astype(str).eq("legit_guided_by_relative") & model["action"].astype(str).eq("hangup_required"),
        "p_abandon",
    ]
    guided_abandon = float(assumptions["p_abandon_guided_hangup_required"])
    guided_ok = len(guided_rows) > 0 and bool(np.allclose(guided_rows.to_numpy(dtype=float), guided_abandon))
    source_ok = bool(model["source"].astype(str).eq("GIẢ ĐỊNH").all())
    probs = model[["p_stop", "p_abandon", "p_complaint"]].to_numpy(dtype=float)
    bounded = bool(np.isfinite(probs).all() and (probs >= 0).all() and (probs <= 1).all())
    report.add(
        "response_model",
        f"{len(model)} dòng, cúp máy người nhà {guided_abandon:.2f}",
        "xác suất tăng theo mức can thiệp, GIẢ ĐỊNH, đủ tổ hợp",
        "đạt" if monotone and full and source_ok and guided_ok and bounded else "lệch",
        monotone and full and source_ok and guided_ok and bounded,
    )

    print_signal_rates(sessions, scenarios, observed)
    guided_hangup = smoke(transactions, sessions, scenarios, accounts, customers, latent, truth_accounts, model, labels, cases, sim_start, sim_end)
    report.add(
        "Người nhà bị bắt cúp máy",
        pct(guided_hangup),
        "< 2%",
        "legit_guided_by_relative, hangup_required",
        guided_hangup < 0.02,
    )
    complaint_ok, complaint_text = complaint_timing(labels, cases, scenarios, accounts, simo, observed)
    print(f"  {complaint_text}")
    report.add("Phiếu nội bộ trước SIMO", complaint_text, "mọi phiếu B01, complaint_ts < listed_at", "-", complaint_ok)
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


def smoke(transactions, sessions, scenarios, accounts, customers, latent, truth_accounts, model, labels, cases, sim_start, sim_end) -> float:
    sessions = sessions.copy()
    sessions["session_id"] = sessions["session_id"].astype(str)
    scenarios = scenarios.copy()
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    joined = sessions.merge(scenarios[["session_id", "scenario_type", "case_id", "hangs_up_before_transfer"]], on="session_id", how="left")
    tx = transactions.loc[transactions["session_id"].notna(), ["session_id", "amount", "from_balance_before", "ts", "to_account", "from_account", "tx_type"]].copy()
    tx["session_id"] = tx["session_id"].astype(str)
    main_tx = tx.sort_values("amount", ascending=False).drop_duplicates("session_id")
    feature = joined.merge(main_tx, on="session_id", how="left")
    families = session_families(ROOT / "out" / "observed")
    families["session_id"] = families["session_id"].astype(str)
    feature = feature.reset_index(drop=True)
    feature["action"] = feature["session_id"].map(dict(zip(families["session_id"], actions_from_families(families)))).fillna("allow")
    safe_ids = set(truth_accounts.loc[truth_accounts["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str))
    guided = feature["scenario_type"].astype(str).eq("legit_guided_by_relative")
    guided_hangup = float(feature.loc[guided, "action"].astype(str).eq("hangup_required").mean()) if bool(guided.any()) else 0.0
    segment_of = dict(zip(customers["customer_id"].astype(str), customers["segment"].astype(str)))
    guardian_of = dict(zip(customers["customer_id"].astype(str), customers["has_guardian"].astype(bool)))
    alone_of = dict(zip(latent["customer_id"].astype(str), latent["lives_alone"].astype(bool)))
    lookup = model.set_index(["scenario_group", "segment", "action", "guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"])
    saved = 0.0
    bothered = 0
    legit_n = 0
    complaints = 0.0
    session_stop: dict[str, tuple[float, str, str]] = {}
    feature["customer_id"] = feature["customer_id"].astype(str)
    feature = feature.sort_values(["customer_id", "start_ts"], kind="mergesort")
    prior: dict[str, list[pd.Timestamp]] = {}
    for row in feature.itertuples(index=False):
        fraud = str(row.scenario_type) in FRAUD_TYPES or str(row.to_account) in safe_ids
        # tax_authority đi cùng nhóm giả danh cơ quan.
        if fraud and str(row.to_account) in safe_ids:
            group = "own_name_safe_account"
        elif str(row.scenario_type) == "legit_guided_by_relative":
            group = "legit_guided_by_relative"
        elif fraud:
            group = str(row.scenario_type)
        else:
            group = "legit"
        segment = "cao tuổi" if segment_of.get(str(row.customer_id)) == "cao tuổi" else "thường"
        guardian = bool(row.action == "hold" and guardian_of.get(str(row.customer_id), False))
        coached = group in {"police", "bank_staff", "tax_authority", "own_name_safe_account"}
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
        session_stop[str(row.session_id)] = (stop, str(row.action), group)
        intervened = str(row.action) in {"pause_and_verify", "guardian_confirm", "friction", "show_chain", "recovery_window", "hangup_required", "hold"}
        if not fraud:
            legit_n += 1
            if intervened:
                bothered += 1
            complaints += float(chosen["p_complaint"])
        elif intervened:
            saved += float(row.amount or 0) * stop
    bother_rate = bothered / legit_n if legit_n else 0.0
    print(
        f"  Smoke test luật họ tín hiệu: tiền lừa đảo tránh được {saved:,.0f} VND, "
        f"phiên hợp lệ bị làm phiền {pct(bother_rate)}, khiếu nại kỳ vọng {complaints:.1f}, "
        f"người nhà bị bắt cúp máy {pct(guided_hangup)}"
    )
    bank_of = dict(zip(accounts["account_id"].astype(str), accounts["bank_id"].astype(str)))
    fraud_id = set(labels.loc[labels["is_fraud"], "txn_id"].astype(str))
    kind_of = dict(zip(cases["case_id"].astype(str), cases["scam_type"].astype(str)))
    case_of = dict(zip(labels["txn_id"].astype(str), labels["case_id"].astype(str)))
    flow = transactions.loc[
        transactions["tx_id"].astype(str).isin(fraud_id)
        & transactions["from_account"].astype(str).map(bank_of).eq("B01")
        & transactions["tx_type"].astype(str).eq("transfer")
        & (transactions["amount"] > 0)
        & (transactions["ts"] >= sim_start)
        & (transactions["ts"] < sim_end)
    ].copy()
    flow["session_id"] = flow["session_id"].astype(str)
    flow["kind"] = flow["tx_id"].astype(str).map(case_of).map(kind_of)
    safe_payee = flow["to_account"].astype(str).isin(safe_ids)
    flow.loc[safe_payee, "kind"] = "own_name_safe_account"
    outflow = float(flow["amount"].sum())
    avoided = 0.0
    by_out: dict[str, float] = {}
    by_saved: dict[str, float] = {}
    for row in flow.itertuples(index=False):
        kind = str(row.kind)
        by_out[kind] = by_out.get(kind, 0.0) + float(row.amount)
        stop, action, _group = session_stop.get(str(row.session_id), (0.0, "allow", ""))
        if action in {"pause_and_verify", "guardian_confirm", "friction", "show_chain", "recovery_window", "hangup_required", "hold"}:
            kept = float(row.amount) * stop
            avoided += kept
            by_saved[kind] = by_saved.get(kind, 0.0) + kept
    avoided_rate = avoided / outflow if outflow else 0.0
    print(
        f"  Tiền lừa đảo đi ra từ B01 trong cửa sổ: {outflow:,.0f} VND, "
        f"tránh được {avoided:,.0f} VND ({pct(avoided_rate)})"
    )
    for kind in sorted(by_out):
        base = by_out[kind]
        rate = by_saved.get(kind, 0.0) / base if base else 0.0
        print(f"    {kind}: {base:,.0f} VND, tránh được {pct(rate)}")
    return guided_hangup


def print_signal_rates(sessions: pd.DataFrame, scenarios: pd.DataFrame, observed: Path) -> None:
    signals = pd.read_parquet(observed / "device_signals.parquet")
    events = pd.read_parquet(observed / "session_events.parquet", columns=["session_id", "event_type"])
    frame = sessions[["session_id"]].copy()
    frame["session_id"] = frame["session_id"].astype(str)
    scen = scenarios[["session_id", "scenario_type"]].copy()
    scen["session_id"] = scen["session_id"].astype(str)
    frame = frame.merge(scen, on="session_id", how="left")
    signals = signals.copy()
    signals["session_id"] = signals["session_id"].astype(str)
    frame = frame.merge(signals, on="session_id", how="left")
    events = events.copy()
    events["session_id"] = events["session_id"].astype(str)
    kind = events["event_type"].astype(str)
    mode = (
        events.loc[kind.str.startswith("acct_input_")]
        .groupby("session_id", sort=False)["event_type"]
        .first()
        .astype(str)
        .str.replace("acct_input_", "", regex=False)
    )
    switches = events.loc[kind.eq("app_switch")].groupby("session_id", sort=False).size()
    frame["acct_input"] = frame["session_id"].map(mode).fillna("(thiếu)")
    frame["app_switches"] = frame["session_id"].map(switches).fillna(0).astype(int).clip(upper=3).astype(str)
    seconds = pd.to_numeric(frame["call_seconds_since_open"], errors="coerce")
    second_bin = pd.Series("null", index=frame.index, dtype=object)
    second_bin.loc[seconds.notna() & (seconds < 120)] = "<2m"
    second_bin.loc[seconds.notna() & (seconds >= 120) & (seconds < 600)] = "<10m"
    second_bin.loc[seconds.notna() & (seconds >= 600) & (seconds < 1800)] = "<30m"
    second_bin.loc[seconds.notna() & (seconds >= 1800)] = ">=30m"
    frame["call_seconds"] = second_bin
    direction = frame["call_direction"].astype("string").fillna("null")
    direction = direction.mask(direction.isin(["<NA>", "nan", "None", ""]), "null")
    frame["call_direction"] = direction
    kind_name = frame["scenario_type"].astype(str)
    groups = []
    for name in ["police", "tax_authority", "bank_staff", "investment", "relative", "deposit", "romance"]:
        groups.append((name, kind_name.eq(name)))
    hard = set(HARD_SAMPLES)
    groups.append(("legit", kind_name.str.startswith("legit") & ~kind_name.isin(hard)))
    for name in HARD_SAMPLES:
        groups.append((name, kind_name.eq(name)))
    rows = []
    for name, mask in groups:
        part = frame.loc[mask]
        if part.empty:
            continue
        def share(series, value) -> float:
            return float((series.astype(str) == value).mean()) if len(series) else 0.0
        rows.append(
            {
                "nhóm": name,
                "n": len(part),
                "in_call": float(part["in_call_at_open"].fillna(False).astype(bool).mean()),
                "ended": float(part["call_ended_in_session"].fillna(False).astype(bool).mean()),
                "dir_in": share(part["call_direction"], "inbound"),
                "dir_out": share(part["call_direction"], "outbound"),
                "dir_null": share(part["call_direction"], "null"),
                "android": share(part["os"], "Android"),
                "ios": share(part["os"], "iOS"),
                "sec_null": share(part["call_seconds"], "null"),
                "sec_<2m": share(part["call_seconds"], "<2m"),
                "sec_<10m": share(part["call_seconds"], "<10m"),
                "sec_<30m": share(part["call_seconds"], "<30m"),
                "sec_>=30m": share(part["call_seconds"], ">=30m"),
                "typed": share(part["acct_input"], "typed"),
                "typed_chunked": share(part["acct_input"], "typed_chunked"),
                "pasted": share(part["acct_input"], "pasted"),
                "chosen": share(part["acct_input"], "chosen"),
                "sw0": share(part["app_switches"], "0"),
                "sw1": share(part["app_switches"], "1"),
                "sw2": share(part["app_switches"], "2"),
                "sw3+": share(part["app_switches"], "3"),
            }
        )
    table = pd.DataFrame(rows).set_index("nhóm")
    print("  Tỷ lệ tín hiệu thiết bị và cách nhập theo loại phiên:")
    with pd.option_context("display.max_columns", 30, "display.width", 220, "display.float_format", lambda value: f"{value:.1%}"):
        print(table.drop(columns=["n"]).T.to_string())
        print("  Số phiên: " + ", ".join(f"{name} {int(count)}" for name, count in table["n"].items()))


def complaint_timing(labels, cases, scenarios, accounts, simo, observed: Path) -> tuple[bool, str]:
    path = observed / "internal_complaints.parquet"
    if not path.exists():
        return False, "chưa có internal_complaints"
    complaints = pd.read_parquet(path)
    if {"case_id", "is_fraud", "label_observed"} & set(complaints.columns):
        return False, "phiếu lộ nhãn vụ"
    if complaints.empty:
        return False, "không có phiếu"
    bank = dict(zip(accounts["account_id"].astype(str), accounts["bank_id"].astype(str)))
    b01_ok = bool(complaints["account_id"].astype(str).map(bank).eq("B01").all())
    scen = scenarios[["session_id", "case_id", "scenario_type"]].copy()
    scen["session_id"] = scen["session_id"].astype(str)
    scen["case_id"] = scen["case_id"].astype(str)
    linked = complaints.copy()
    linked["session_id"] = linked["session_id"].astype(str)
    linked = linked.merge(scen[["session_id", "case_id"]], on="session_id", how="left")
    fraud = labels.loc[labels["is_fraud"] & labels["reported"]].copy()
    fraud["case_id"] = fraud["case_id"].astype(str)
    report_ts = fraud.groupby("case_id")["report_ts"].min()
    l1 = dict(zip(cases["case_id"].astype(str), cases["l1_account_id"].astype(str))) if "l1_account_id" in cases.columns else {}
    listed = simo.loc[simo["source"].astype(str).eq("victim_report"), ["account_id", "listed_at"]].copy()
    listed["account_id"] = listed["account_id"].astype(str)
    by_account = {account: part["listed_at"].sort_values() for account, part in listed.groupby("account_id", sort=False)}
    case_listed = {}
    for case_id, stamp in report_ts.items():
        stamps = by_account.get(l1.get(str(case_id), ""))
        if stamps is None or len(stamps) == 0 or pd.isna(stamp):
            continue
        owned = []
        for delay in (0, 1, 2):
            target = stamp + pd.Timedelta(days=int(delay))
            hit = stamps.loc[(stamps - target).abs() <= pd.Timedelta(seconds=1)]
            if len(hit):
                owned.append(pd.Timestamp(hit.iloc[0]))
        if owned:
            case_listed[str(case_id)] = owned
    late = 0
    delays = []
    for row in linked.itertuples(index=False):
        owned = case_listed.get(str(row.case_id), [])
        later = [stamp for stamp in owned if row.complaint_ts < stamp]
        if not later:
            late += 1
            continue
        delays.append((min(later) - row.complaint_ts).total_seconds() / 3600)
    sess_acc = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "account_id"])
    sess_acc["session_id"] = sess_acc["session_id"].astype(str)
    sess_acc["account_id"] = sess_acc["account_id"].astype(str)
    b01_accounts = set(accounts.loc[accounts["bank_id"].astype(str).eq("B01"), "account_id"].astype(str))
    pool = scen.loc[scen["scenario_type"].astype(str).isin(FRAUD_TYPES) & scen["case_id"].isin(set(case_listed))]
    pool = pool.merge(sess_acc, on="session_id", how="left")
    pool = pool.loc[pool["account_id"].isin(b01_accounts)]
    have = set(linked.loc[linked["case_id"].astype(str).isin(set(case_listed)), "session_id"].astype(str))
    rate = float(pool["session_id"].isin(have).mean()) if len(pool) else 0.0
    median = float(np.median(delays)) if delays else float("nan")
    ok = b01_ok and late == 0 and len(complaints) > 0
    text = f"{pct(rate)} phiên B01 của vụ đã lên SIMO, trễ trung vị {median:.1f} giờ, muộn {late}"
    return ok, text


if __name__ == "__main__":
    main()
