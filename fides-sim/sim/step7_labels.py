"""Bước 7: nhãn báo cáo, SIMO từ nạn nhân, số bị tố và mô hình phản ứng.

Đọc bước 1–6. Chạy lại thì thay phần nhãn, không cộng reported_count.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.io import write
from sim.step4_mules import as_timestamp

TZ = "Asia/Ho_Chi_Minh"
FRAUD_TYPES = {"police", "investment", "bank_staff", "relative", "deposit", "romance", "tax_authority", "own_name_safe_account"}
SEGMENTS = ["thường", "cao tuổi"]
ACTIONS = ["allow", "g1_banner", "pause_and_verify", "guardian_confirm", "friction", "show_chain", "recovery_window", "hangup_required", "hold"]


def load_config() -> dict:
    import yaml

    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def day_draw(rng, bounds) -> int:
    low, high = int(bounds[0]), int(bounds[1])
    if high < low:
        low, high = high, low
    return int(rng.integers(low, high + 1))


def response_table(spec: dict) -> pd.DataFrame:
    """Mọi xác suất lấy từ config và được đánh dấu GIẢ ĐỊNH."""
    assumptions = spec["response_assumptions"]
    actions = list(assumptions["actions"])
    stop_of = assumptions["p_stop_fraud"]
    abandon_of = assumptions["p_abandon_legit"]
    complaint_of = assumptions.get("p_complaint_legit", {})
    guided_hangup = float(assumptions["p_abandon_guided_hangup_required"])
    coached_friction = float(assumptions["p_stop_fraud_coached_friction"])
    hang_bonus = float(assumptions["p_stop_hold_if_hangs_up"])
    guard_alone = float(assumptions["p_stop_guardian_alone"])
    guard = float(assumptions["p_stop_guardian"])
    cap = float(assumptions["p_stop_cap"])
    groups = [
        "police", "bank_staff", "tax_authority", "relative", "deposit", "investment",
        "romance", "own_name_safe_account", "legit", "legit_guided_by_relative",
    ]
    rows = []
    for group in groups:
        fraud = group not in {"legit", "legit_guided_by_relative"}
        for segment in SEGMENTS:
            for action in actions:
                for guardian in (False, True):
                    for coached in (False, True):
                        for alone in (False, True):
                            for hangs in (False, True):
                                if fraud:
                                    base = float(stop_of[action])
                                    if action == "friction" and coached:
                                        base = coached_friction
                                    if action in {"hold", "hangup_required"} and hangs:
                                        base += hang_bonus
                                    if guardian:
                                        base += guard_alone if alone else guard
                                    stop = min(cap, base)
                                    abandon, complaint = 0.0, 0.0
                                else:
                                    stop = 0.0
                                    abandon = guided_hangup if group == "legit_guided_by_relative" and action == "hangup_required" else float(abandon_of[action])
                                    complaint = float(complaint_of.get(action, 0.0))
                                rows.append(
                                    {
                                        "scenario_group": group,
                                        "segment": segment,
                                        "action": action,
                                        "guardian_notified": guardian,
                                        "coached": coached,
                                        "lives_alone": alone,
                                        "hangs_up_before_transfer": hangs,
                                        "p_stop": stop,
                                        "p_abandon": abandon,
                                        "p_complaint": complaint,
                                        "source": "GIẢ ĐỊNH",
                                    }
                                )
    frame = pd.DataFrame(rows)
    frame["guardian_notified"] = frame["guardian_notified"].astype(bool)
    frame["coached"] = frame["coached"].astype(bool)
    frame["lives_alone"] = frame["lives_alone"].astype(bool)
    frame["hangs_up_before_transfer"] = frame["hangs_up_before_transfer"].astype(bool)
    for column in ("p_stop", "p_abandon", "p_complaint"):
        frame[column] = frame[column].astype(float)
    frame["source"] = frame["source"].astype("string")
    frame["scenario_group"] = frame["scenario_group"].astype("string")
    frame["segment"] = frame["segment"].astype("string")
    frame["action"] = frame["action"].astype("string")
    return frame


def main() -> None:
    cfg = load_config()
    spec = cfg["labels"]
    rng = np.random.default_rng(int(cfg["seed"]))
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"

    transactions = pd.read_parquet(observed / "transactions.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    cases = pd.read_parquet(truth / "truth_cases.parquet")
    plans = pd.read_parquet(truth / "case_transfers_plan.parquet")
    scenarios = pd.read_parquet(truth / "truth_scenarios.parquet")
    rings = pd.read_parquet(truth / "mule_rings.parquet")
    truth_accounts = pd.read_parquet(truth / "accounts_truth.parquet")
    simo = pd.read_parquet(observed / "simo_list.parquet")

    transactions["tx_id"] = transactions["tx_id"].astype(str)
    transactions["from_account"] = transactions["from_account"].astype(str)
    transactions["to_account"] = transactions["to_account"].astype(str)
    owner = dict(zip(accounts["account_id"].astype(str), accounts["customer_id"].astype(str)))
    mule_ids = set(rings["account_id"].astype(str))
    safe_ids = set(truth_accounts.loc[truth_accounts["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str))
    mule_ids |= safe_ids
    victims = set(cases["victim_customer_id"].astype(str))
    fraud_sessions = scenarios.loc[scenarios["scenario_type"].astype(str).isin(FRAUD_TYPES - {"own_name_safe_account"}), ["session_id", "case_id"]]
    fraud_session_ids = set(fraud_sessions["session_id"].astype(str))
    case_of_session = dict(zip(fraud_sessions["session_id"].astype(str), fraud_sessions["case_id"].astype(str)))

    in_session = transactions["session_id"].notna()
    to_mule = transactions["to_account"].isin(mule_ids) & transactions["tx_type"].astype(str).eq("transfer") & (transactions["amount"] > 0)
    from_victim = transactions["from_account"].map(owner).isin(victims)
    keep = in_session | (to_mule & from_victim)
    labeled = transactions.loc[keep, ["tx_id", "ts", "session_id", "from_account", "to_account", "amount", "tx_type"]].copy()
    labeled["session_id"] = labeled["session_id"].astype(str)
    labeled["case_id"] = labeled["session_id"].map(case_of_session)
    labeled["is_fraud"] = labeled["session_id"].isin(fraud_session_ids)
    positive = plans.loc[plans["planned_amount"] > 0, ["case_id", "planned_ts", "to_mule_account_id"]].copy()
    positive["case_id"] = positive["case_id"].astype(str)
    positive["to_mule_account_id"] = positive["to_mule_account_id"].astype(str)
    victim_of = dict(zip(cases["case_id"].astype(str), cases["victim_customer_id"].astype(str)))
    missing = labeled["case_id"].isna() & labeled["to_account"].isin(mule_ids) & labeled["from_account"].map(owner).isin(victims)
    for index in labeled.index[missing]:
        row = labeled.loc[index]
        victim = owner.get(str(row.from_account), "")
        pool = positive.loc[positive["to_mule_account_id"].eq(str(row.to_account))]
        pool = pool.loc[pool["case_id"].map(victim_of).eq(victim)]
        if pool.empty:
            continue
        gap = (pool["planned_ts"] - row.ts).abs()
        labeled.at[index, "case_id"] = str(pool.loc[gap.idxmin(), "case_id"])
    plan_hit = labeled["case_id"].notna() & labeled["to_account"].isin(mule_ids) & labeled["tx_type"].astype(str).eq("transfer")
    labeled.loc[plan_hit, "is_fraud"] = True

    safe_cases = set(positive.loc[positive["to_mule_account_id"].isin(safe_ids), "case_id"].astype(str))
    type_of = dict(zip(cases["case_id"].astype(str), cases["scam_type"].astype(str)))
    rates = spec["report_rate"]
    delays = spec["report_delay_days"]
    processing = spec["bank_processing_days"]
    case_ids = sorted({str(case_id) for case_id in labeled.loc[labeled["is_fraud"], "case_id"].dropna().astype(str) if str(case_id) not in {"nan", "<NA>", "None"}})
    by_kind: dict[str, list[str]] = {}
    for case_id in case_ids:
        kind = "own_name_safe_account" if case_id in safe_cases else type_of.get(case_id, "police")
        by_kind.setdefault(kind, []).append(case_id)
    report_ts_of = {}
    available_of = {}
    reported_cases = set()
    for kind, ids in by_kind.items():
        rate = float(rates.get(kind, rates.get(type_of.get(ids[0], ""), 0.5)))
        n_report = min(len(ids), int(round(rate * len(ids))))
        chosen = rng.choice(np.array(ids, dtype=object), size=n_report, replace=False) if n_report else []
        for case_id in chosen:
            case_id = str(case_id)
            part = labeled.loc[labeled["case_id"].astype(str).eq(case_id) & labeled["is_fraud"]]
            if part.empty:
                continue
            last = part["ts"].max()
            delay_key = type_of.get(case_id, "police")
            report_ts = last + pd.Timedelta(days=day_draw(rng, delays.get(delay_key, [0, 3])))
            ready = report_ts + pd.Timedelta(days=day_draw(rng, processing))
            report_ts_of[case_id] = report_ts
            available_of[case_id] = ready
            reported_cases.add(case_id)

    reported = []
    report_ts = []
    available = []
    for row in labeled.itertuples(index=False):
        case_id = None if pd.isna(row.case_id) else str(row.case_id)
        if case_id in reported_cases and row.ts <= report_ts_of[case_id]:
            reported.append(True)
            report_ts.append(report_ts_of[case_id])
            ready = available_of[case_id]
            if ready <= row.ts:
                ready = row.ts + pd.Timedelta(seconds=1)
            available.append(ready)
        else:
            reported.append(False)
            report_ts.append(pd.NaT)
            available.append(pd.NaT)
    labeled["reported"] = reported
    labeled["report_ts"] = report_ts
    labeled["label_available_at"] = available

    legit_index = labeled.index[~labeled["is_fraud"].to_numpy()]
    n_false = int(round(float(spec["false_report_rate"]) * len(legit_index)))
    if n_false and len(legit_index):
        picked = rng.choice(legit_index.to_numpy(), size=min(n_false, len(legit_index)), replace=False)
        for index in picked:
            stamp = labeled.at[index, "ts"] + pd.Timedelta(days=day_draw(rng, [0, 3]))
            ready = stamp + pd.Timedelta(days=day_draw(rng, processing))
            if ready <= labeled.at[index, "ts"]:
                ready = labeled.at[index, "ts"] + pd.Timedelta(seconds=1)
            labeled.at[index, "reported"] = True
            labeled.at[index, "report_ts"] = stamp
            labeled.at[index, "label_available_at"] = ready

    labeled["label_observed"] = labeled["reported"].astype(int)
    labels = labeled[["tx_id", "case_id", "ts", "is_fraud", "reported", "report_ts", "label_available_at", "label_observed"]].copy()
    labels = labels.rename(columns={"tx_id": "txn_id"})
    labels["txn_id"] = labels["txn_id"].astype("string")
    labels["case_id"] = labels["case_id"].astype("string")
    labels["is_fraud"] = labels["is_fraud"].astype(bool)
    labels["reported"] = labels["reported"].astype(bool)
    labels["label_observed"] = labels["label_observed"].astype(np.int64)

    listing_delay = spec["simo_listing_delay_days"]
    l1_of = dict(zip(cases["case_id"].astype(str), cases["l1_account_id"].astype(str))) if "l1_account_id" in cases.columns else {}
    listed_at_of = {}
    for case_id in sorted(reported_cases):
        listed_at_of[case_id] = report_ts_of[case_id] + pd.Timedelta(days=day_draw(rng, listing_delay))
    b01_ids = set(accounts.loc[accounts["bank_id"].astype(str).eq("B01"), "account_id"].astype(str))
    blank_session = {"nan", "<NA>", "None", ""}
    b01_tx = labeled.loc[
        labeled["is_fraud"]
        & labeled["from_account"].astype(str).isin(b01_ids)
        & ~labeled["session_id"].astype(str).isin(blank_session)
        & labeled["case_id"].notna()
    ].copy()
    b01_tx["case_id"] = b01_tx["case_id"].astype(str)
    b01_tx["session_id"] = b01_tx["session_id"].astype(str)
    eligible = sorted(set(b01_tx["case_id"]) & reported_cases)
    complaint_rng = np.random.default_rng(np.random.SeedSequence([int(cfg["seed"]), 7]))
    n_complaint = min(len(eligible), int(round(float(spec["internal_complaint_share"]) * len(eligible))))
    chosen_cases = set(complaint_rng.choice(np.array(eligible, dtype=object), size=n_complaint, replace=False)) if n_complaint else set()
    complaint_rows = []
    seq = 1
    for case_id in sorted(chosen_cases):
        part = b01_tx.loc[b01_tx["case_id"].eq(case_id)]
        listed_at = listed_at_of[case_id]
        report_ts = report_ts_of[case_id]
        latest = part["ts"].max()
        high = min(report_ts, listed_at) - pd.Timedelta(minutes=1)
        low = latest + pd.Timedelta(minutes=1)
        if high <= low:
            complaint_ts = listed_at - pd.Timedelta(minutes=1)
        else:
            span = int((high - low).total_seconds())
            complaint_ts = low + pd.Timedelta(seconds=int(complaint_rng.integers(0, span + 1)))
        if not complaint_ts < listed_at:
            complaint_ts = listed_at - pd.Timedelta(minutes=1)
        sessions_of = part.groupby("session_id", sort=False)["from_account"].first()
        for session_id, account_id in sessions_of.items():
            complaint_rows.append(
                {
                    "complaint_id": f"C{seq:06d}",
                    "customer_id": victim_of.get(case_id, ""),
                    "account_id": str(account_id),
                    "session_id": str(session_id),
                    "complaint_ts": complaint_ts,
                }
            )
            seq += 1
    complaints = pd.DataFrame(complaint_rows)
    if complaints.empty:
        complaints = pd.DataFrame(
            {
                "complaint_id": pd.Series(dtype="string"),
                "customer_id": pd.Series(dtype="string"),
                "account_id": pd.Series(dtype="string"),
                "session_id": pd.Series(dtype="string"),
                "complaint_ts": pd.Series(dtype=f"datetime64[ns, {TZ}]"),
            }
        )
    else:
        for column in ("complaint_id", "customer_id", "account_id", "session_id"):
            complaints[column] = complaints[column].astype("string")
    simo_rows = []
    number_rows = []
    numbers_of = dict(zip(cases["case_id"].astype(str), cases["scammer_number_ids"]))
    for case_id in sorted(reported_cases):
        listed_at = listed_at_of[case_id]
        accounts_listed = []
        l1 = l1_of.get(case_id, "")
        if l1:
            accounts_listed.append(l1)
        if case_id in safe_cases:
            dest = positive.loc[positive["case_id"].eq(case_id) & positive["to_mule_account_id"].isin(safe_ids), "to_mule_account_id"]
            accounts_listed.extend(dest.astype(str).unique().tolist())
        for account in accounts_listed:
            simo_rows.append({"account_id": account, "listed_at": listed_at, "source": "victim_report"})
        for number in list(numbers_of.get(case_id, [])):
            number_rows.append({"number_id": str(number), "reported_at": report_ts_of[case_id]})

    if "source" in simo.columns:
        simo = simo.loc[simo["source"].astype(str).ne("victim_report")].copy()
    if simo_rows:
        simo = pd.concat([simo, pd.DataFrame(simo_rows)], ignore_index=True)
    simo["account_id"] = simo["account_id"].astype("string")
    simo["source"] = simo["source"].astype("string")
    reports = pd.DataFrame(number_rows)
    if reports.empty:
        reports = pd.DataFrame({"number_id": pd.Series(dtype="string"), "reported_at": pd.Series(dtype=f"datetime64[ns, {TZ}]")})
    else:
        reports["number_id"] = reports["number_id"].astype("string")

    model = response_table(spec)
    write(labels, "truth", "labels")
    write(model, "truth", "response_model")
    write(simo, "observed", "simo_list")
    write(reports, "observed", "number_reports")
    write(complaints, "observed", "internal_complaints")
    n_reported_cases = len(reported_cases)
    print(
        f"Đã ghi {len(labels)} nhãn, {n_reported_cases} vụ được báo, "
        f"{len(simo_rows)} dòng SIMO, {len(reports)} báo số, {len(complaints)} phiếu nội bộ."
    )
    _ = (sim_start, sim_end)


if __name__ == "__main__":
    main()
