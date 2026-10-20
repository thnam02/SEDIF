"""Đối chiếu phiên và dòng tiền bước 6. Thoát mã 1 nếu có mục không đạt."""

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
LEAKED = {"case_id", "scenario_type", "purpose_answer", "yes_no_answers", "permission_response"}
EVENT_OK = ("login", "open_transfer", "add_payee", "paste_account", "enter_amount", "submit")


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
    return f"p10 {low:.2f} / p50 {mid:.2f} / p90 {high:.2f}"


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
            ranks[order[start:stop]] = 0.5 * (start + 1 + stop)
        start = stop
    positive = label.astype(bool)
    n_pos = int(positive.sum())
    n_neg = len(label) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.0
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def in_window(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    if len(series) == 0 or series.isna().any():
        return False
    tz = getattr(series.dt, "tz", None)
    zone = getattr(tz, "zone", None) or str(tz)
    return zone == TZ and bool(series.min() >= start and series.max() < end)


def replay_bad(transactions: pd.DataFrame, opening: dict[str, int], wanted: set[str]) -> int:
    sub = transactions.loc[
        transactions["from_account"].astype(str).isin(wanted) | transactions["to_account"].astype(str).isin(wanted),
        ["tx_id", "ts", "from_account", "to_account", "amount", "from_balance_before"],
    ].copy()
    sub["from_account"] = sub["from_account"].astype(str)
    sub["to_account"] = sub["to_account"].astype(str)
    sub = sub.sort_values(["ts", "tx_id"], kind="mergesort")
    balance = {account: int(opening[account]) for account in wanted}
    bad: set[str] = set()
    seen: set[str] = set()
    for row in sub.itertuples(index=False):
        sender = row.from_account
        receiver = row.to_account if row.to_account not in {"", "nan", "<NA>"} else ""
        amount = int(row.amount)
        if sender in balance:
            seen.add(sender)
            if int(row.from_balance_before) != balance[sender] or amount > balance[sender]:
                bad.add(sender)
                balance.pop(sender, None)
            else:
                balance[sender] -= amount
        if receiver in balance and receiver != sender:
            seen.add(receiver)
            balance[receiver] += amount
    _ = seen
    return len(bad)


def pass_through(inbound: pd.DataFrame, outbound: pd.DataFrame) -> np.ndarray:
    if inbound.empty:
        return np.array([])
    ratios = []
    outs = {account: part.sort_values("ts") for account, part in outbound.groupby("from_account", sort=False)}
    for row in inbound.itertuples(index=False):
        part = outs.get(str(row.to_account))
        if part is None:
            ratios.append(0.0)
            continue
        window = part.loc[(part["ts"] > row.ts) & (part["ts"] <= row.ts + pd.Timedelta(hours=24)), "amount"]
        ratios.append(min(1.0, float(window.sum()) / max(int(row.amount), 1)))
    return np.array(ratios, dtype=float)


def main() -> None:
    cfg = load_config()
    spec = cfg["sessions"]
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    sessions = pd.read_parquet(observed / "sessions.parquet")
    events = pd.read_parquet(observed / "session_events.parquet")
    signals = pd.read_parquet(observed / "device_signals.parquet")
    calls = pd.read_parquet(observed / "call_events.parquet")
    transactions = pd.read_parquet(observed / "transactions.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    scenarios = pd.read_parquet(truth / "truth_scenarios.parquet")
    rings = pd.read_parquet(truth / "mule_rings.parquet")
    cases = pd.read_parquet(truth / "truth_cases.parquet")
    plans = pd.read_parquet(truth / "case_transfers_plan.parquet")

    report = Report()
    fraud_types = {"police", "investment", "bank_staff", "relative", "deposit", "romance"}
    joined = sessions.merge(scenarios[["session_id", "scenario_type"]], on="session_id", how="left")
    n_sessions = len(sessions)
    fraud_rate = float(joined["scenario_type"].astype(str).isin(fraud_types).mean()) if n_sessions else 0.0
    report.add("Số phiên", str(n_sessions), "> 0", "-", n_sessions > 0)
    report.add("Tỷ lệ phiên lừa đảo", pct(fraud_rate), "có cả hai phía", "-", 0 < fraud_rate < 0.5)
    print("  Số phiên theo scenario_type:")
    counts = joined["scenario_type"].astype(str).value_counts()
    for name, count in counts.items():
        print(f"    {name}: {int(count)}")
    report.add("Đủ loại kịch bản", str(counts.size), "lừa đảo và legit_*", "-", counts.size >= 6)

    opening = dict(zip(accounts["account_id"].astype(str), accounts["opening_balance"].astype(np.int64)))
    customer_accounts = accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str).to_numpy()
    mule_accounts = set(rings["account_id"].astype(str))
    victim_ids = set(cases["victim_customer_id"].astype(str))
    victim_accounts = set(accounts.loc[accounts["customer_id"].astype(str).isin(victim_ids), "account_id"].astype(str))
    rng = np.random.default_rng(int(cfg["seed"]))
    sample = set(rng.choice(customer_accounts, size=min(1000, len(customer_accounts)), replace=False).tolist())
    wanted = sample | mule_accounts | victim_accounts
    negative = int(
        (
            transactions["from_account"].astype(str).isin(wanted)
            & (transactions["from_balance_before"] < transactions["amount"])
        ).sum()
    )
    bad = replay_bad(transactions, opening, wanted)
    report.add(
        "Số dư tính lại",
        f"{negative} âm, {bad} lệch",
        "0 âm, 0 lệch",
        f"{len(wanted)} tài khoản (mẫu + mule + nạn nhân)",
        negative == 0 and bad == 0,
    )

    l1 = set(rings.loc[rings["layer"].astype(str) == "l1", "account_id"].astype(str))
    delay_of = dict(zip(rings["account_id"].astype(str), rings["forward_delay_minutes"]))
    victim_plans = plans.loc[plans["planned_amount"] > 0, ["case_id", "planned_ts", "to_mule_account_id", "planned_amount"]].copy()
    victim_plans = victim_plans.loc[victim_plans["to_mule_account_id"].astype(str).isin(l1)]
    victim_of_case = dict(zip(cases["case_id"].astype(str), cases["victim_customer_id"].astype(str)))
    owner_of = dict(zip(accounts["account_id"].astype(str), accounts["customer_id"].astype(str)))
    forwards = transactions.loc[transactions["purpose_text"].astype(str).eq("chuyen tiep"), ["from_account", "ts", "amount"]].copy()
    forwards["from_account"] = forwards["from_account"].astype(str)
    forward_groups = {account: part.sort_values("ts") for account, part in forwards.groupby("from_account", sort=False)}
    inflow_tx = transactions.loc[
        transactions["to_account"].astype(str).isin(l1) & ~transactions["from_account"].astype(str).isin(mule_accounts),
        ["from_account", "to_account", "ts", "amount"],
    ].copy()
    inflow_tx["from_account"] = inflow_tx["from_account"].astype(str)
    inflow_tx["to_account"] = inflow_tx["to_account"].astype(str)
    inflow_groups = {account: part for account, part in inflow_tx.groupby("to_account", sort=False)}
    matched = 0
    checked = 0
    for row in victim_plans.itertuples(index=False):
        mule = str(row.to_mule_account_id)
        delay = delay_of.get(mule)
        pool = inflow_groups.get(mule)
        if pd.isna(delay) or pool is None:
            continue
        victim = victim_of_case.get(str(row.case_id), "")
        close_in = pool.loc[(pool["ts"] - row.planned_ts).abs() <= pd.Timedelta(minutes=2)]
        close_in = close_in.loc[close_in["from_account"].map(owner_of) == victim]
        if close_in.empty:
            continue
        checked += 1
        pick = close_in.iloc[(close_in["ts"] - row.planned_ts).abs().argmin()]
        expected = pick.ts + pd.Timedelta(minutes=int(delay))
        part = forward_groups.get(mule)
        if part is None:
            continue
        close = part.loc[(part["ts"] - expected).abs() <= pd.Timedelta(seconds=90)]
        if close.empty:
            continue
        ratio = float(close["amount"].sum()) / max(int(pick.amount), 1)
        if 0.88 <= ratio <= 0.99:
            matched += 1
    delay_rate = matched / checked if checked else 0.0
    report.add("Mule lớp 1 chuyển tiếp đúng độ trễ", pct(delay_rate), "≥ 80%", f"{matched}/{checked}", delay_rate >= 0.80)

    inbound = transactions.loc[
        transactions["to_account"].astype(str).isin(l1) & transactions["purpose_text"].astype(str).ne("chuyen tiep"),
        ["to_account", "ts", "amount", "from_account"],
    ]
    # Chỉ khoản nạn nhân, không tính chuyển nội bộ mule.
    inbound = inbound.loc[~inbound["from_account"].astype(str).isin(mule_accounts)]
    outbound = transactions.loc[transactions["from_account"].astype(str).isin(l1), ["from_account", "ts", "amount"]]
    l1_ratio = pass_through(inbound.rename(columns={"to_account": "to_account"}), outbound)
    normal_accounts = set(customer_accounts) - mule_accounts - victim_accounts
    normal_in = transactions.loc[
        transactions["to_account"].astype(str).isin(normal_accounts) & transactions["tx_type"].astype(str).eq("transfer"),
        ["to_account", "ts", "amount"],
    ]
    if len(normal_in) > 8000:
        normal_in = normal_in.sample(8000, random_state=int(cfg["seed"]))
    normal_out = transactions.loc[
        transactions["from_account"].astype(str).isin(set(normal_in["to_account"].astype(str))),
        ["from_account", "ts", "amount"],
    ]
    normal_ratio = pass_through(normal_in, normal_out)
    pass_customers = set(
        accounts.loc[
            accounts["account_id"].astype(str).isin(
                set(transactions.loc[transactions["tx_type"].astype(str).eq("passthrough_out"), "from_account"].astype(str))
            ),
            "customer_id",
        ].astype(str)
    )
    pass_accounts = set(accounts.loc[accounts["customer_id"].astype(str).isin(pass_customers), "account_id"].astype(str))
    pass_in = transactions.loc[transactions["to_account"].astype(str).isin(pass_accounts), ["to_account", "ts", "amount"]]
    if len(pass_in) > 8000:
        pass_in = pass_in.sample(8000, random_state=int(cfg["seed"]))
    pass_out = transactions.loc[
        transactions["from_account"].astype(str).isin(set(pass_in["to_account"].astype(str))),
        ["from_account", "ts", "amount"],
    ]
    pass_ratio = pass_through(pass_in, pass_out)
    print(f"  Pass-through 24h lớp 1: {percentiles(l1_ratio)}")
    print(f"  Pass-through 24h người thường: {percentiles(normal_ratio)}")
    print(f"  Pass-through 24h legit_passthrough: {percentiles(pass_ratio)}")
    l1_mid = float(np.median(l1_ratio)) if len(l1_ratio) else 0.0
    normal_mid = float(np.median(normal_ratio)) if len(normal_ratio) else 1.0
    report.add("Pass-through lớp 1 cao hơn người thường", f"{l1_mid:.2f} so với {normal_mid:.2f}", "trung vị lớp 1 cao hơn", "-", l1_mid > normal_mid)

    legit_sessions = set(joined.loc[~joined["scenario_type"].astype(str).isin(fraud_types), "session_id"].astype(str))
    fraud_sessions = set(joined.loc[joined["scenario_type"].astype(str).isin(fraud_types), "session_id"].astype(str))
    calls["session_id"] = calls["session_id"].astype(str)
    known = calls["channel"].astype(str).eq("cellular") & calls["direction"].astype(str).eq("outbound") & calls["session_id"].isin(legit_sessions)
    unknown = calls["channel"].astype(str).eq("cellular") & calls["direction"].astype(str).eq("inbound") & calls["session_id"].isin(legit_sessions)
    ott = calls["channel"].astype(str).eq("ott") & calls["session_id"].isin(legit_sessions)
    n_legit = max(len(legit_sessions), 1)
    known_rate = known.sum() / n_legit
    unknown_rate = unknown.sum() / n_legit
    ott_rate = ott.sum() / n_legit
    print(f"  Phiên hợp lệ đang gọi: quen {pct(float(known_rate))}, lạ {pct(float(unknown_rate))}, OTT {pct(float(ott_rate))}")
    report.add("Gọi người quen", pct(float(known_rate)), pct(float(spec["legit_on_call_known"])), f"{100 * (known_rate - float(spec['legit_on_call_known'])):+.2f} đ.p", abs(known_rate - float(spec["legit_on_call_known"])) <= 0.01)
    report.add("Gọi số lạ hợp lệ", pct(float(unknown_rate)), pct(float(spec["legit_on_call_unknown"])), f"{100 * (unknown_rate - float(spec['legit_on_call_unknown'])):+.2f} đ.p", abs(unknown_rate - float(spec["legit_on_call_unknown"])) <= 0.005)
    report.add("Gọi OTT hợp lệ", pct(float(ott_rate)), pct(float(spec["legit_ott_call"])), f"{100 * (ott_rate - float(spec['legit_ott_call'])):+.2f} đ.p", abs(ott_rate - float(spec["legit_ott_call"])) <= 0.01)

    sig = signals.copy()
    sig["session_id"] = sig["session_id"].astype(str)
    sig = sig.merge(sessions[["session_id", "start_ts", "end_ts"]], on="session_id", how="left")
    fraud_sig = sig.loc[sig["session_id"].isin(fraud_sessions)]
    call_finish = calls["ts"] + pd.to_timedelta(calls["duration_s"], unit="s")
    calls = calls.assign(finish=call_finish)
    telco = device_only = nobody = 0
    call_groups = {key: part for key, part in calls.groupby("session_id", sort=False)}
    for row in fraud_sig.itertuples(index=False):
        part = call_groups.get(row.session_id)
        if part is None:
            nobody += 1
            continue
        live = part.loc[(part["ts"] < row.end_ts) & (part["finish"] > row.start_ts)]
        if (live["channel"].astype(str) == "cellular").any():
            telco += 1
        elif (live["channel"].astype(str) == "ott").any() or bool(row.in_call_audio_mode):
            device_only += 1
        else:
            nobody += 1
    n_fraud = max(len(fraud_sig), 1)
    print(
        f"  Phiên lừa đảo: nhà mạng thấy {pct(telco / n_fraud)}, "
        f"chỉ thiết bị thấy {pct(device_only / n_fraud)}, không ai thấy {pct(nobody / n_fraud)}"
    )
    report.add(
        "Ba cách nhìn cuộc gọi lừa đảo",
        f"{telco}/{device_only}/{nobody}",
        "cả ba nhóm đều có",
        "nhà mạng / chỉ thiết bị / không ai",
        telco > 0 and device_only > 0 and nobody > 0,
    )

    invest_cases = set(cases.loc[cases["scam_type"].astype(str) == "investment", "case_id"].astype(str))
    invest_plans = plans.loc[plans["case_id"].astype(str).isin(invest_cases) & (plans["planned_amount"] > 0)]
    non_mono = 0
    have = 0
    for case_id, part in invest_plans.groupby("case_id", sort=False):
        victim = victim_of_case.get(str(case_id), "")
        amounts = []
        for row in part.sort_values(["planned_ts", "seq"], kind="mergesort").itertuples(index=False):
            pool = inflow_groups.get(str(row.to_mule_account_id))
            if pool is None:
                continue
            close = pool.loc[(pool["ts"] - row.planned_ts).abs() <= pd.Timedelta(minutes=2)]
            close = close.loc[close["from_account"].map(owner_of) == victim]
            if len(close):
                amounts.append(int(close.iloc[(close["ts"] - row.planned_ts).abs().argmin()].amount))
        if len(amounts) < 2:
            continue
        have += 1
        if any(amounts[index] <= amounts[index - 1] for index in range(1, len(amounts))):
            non_mono += 1
    non_rate = non_mono / have if have else 0.0
    target = float(spec["investment_non_monotonic_share"])
    print(f"  Investment không tăng dần: {pct(non_rate)} ({non_mono}/{have})")
    report.add("Investment không tăng dần", pct(non_rate), pct(target), f"{100 * (non_rate - target):+.2f} đ.p", abs(non_rate - target) <= 0.03)

    ordered = events.sort_values(["session_id", "seq"], kind="mergesort")
    seq_ok = True
    bad_pattern = 0
    for session_id, part in ordered.groupby("session_id", sort=False):
        names = part["event_type"].astype(str).tolist()
        stamps = part["ts"].tolist()
        if names[0] != "login" or any(stamps[i] > stamps[i + 1] for i in range(len(stamps) - 1)):
            seq_ok = False
            bad_pattern += 1
            continue
        if any(name not in EVENT_OK for name in names):
            seq_ok = False
            bad_pattern += 1
    times_ok = in_window(events["ts"], sim_start, sim_end) and in_window(sessions["start_ts"], sim_start, sim_end)
    times_ok = times_ok and in_window(sessions["end_ts"], sim_start, sim_end)
    if len(calls):
        times_ok = times_ok and in_window(calls["ts"], sim_start, sim_end)
    report.add("Thứ tự session_events", f"{bad_pattern} phiên lệch", "0", "login trước, ts tăng", seq_ok and bad_pattern == 0)
    report.add("Mốc thời gian trong cửa sổ", "đạt" if times_ok else "lỗi", "sim_start ≤ t < sim_end", "-", times_ok)

    main_tx = (
        transactions.loc[transactions["session_id"].notna(), ["session_id", "amount", "from_balance_before", "ts", "to_account", "from_account"]]
        .sort_values("amount", ascending=False)
        .drop_duplicates("session_id")
    )
    main_tx["session_id"] = main_tx["session_id"].astype(str)
    feature = joined.merge(main_tx, on="session_id", how="left").merge(sig[["session_id", "in_call_audio_mode", "remote_access_app_installed", "accessibility_enabled_since"]], on="session_id", how="left")
    paste_sessions = set(events.loc[events["event_type"].astype(str).eq("paste_account"), "session_id"].astype(str))
    new_payee = []
    # Người nhận mới: không có giao dịch sớm hơn cùng khách tới cùng tài khoản.
    owner = dict(zip(accounts["account_id"].astype(str), accounts["customer_id"].astype(str)))
    hist = transactions.loc[transactions["to_account"].notna(), ["from_account", "to_account", "ts"]].copy()
    hist["customer"] = hist["from_account"].astype(str).map(owner)
    first_pay = hist.dropna(subset=["customer"]).groupby(["customer", hist["to_account"].astype(str)], sort=False)["ts"].min()
    ratio = feature["amount"].fillna(0).to_numpy(dtype=float) / np.maximum(feature["from_balance_before"].fillna(1).to_numpy(dtype=float), 1.0)
    new_flag = []
    for row in feature.itertuples(index=False):
        customer = owner.get(str(row.from_account), "")
        key = (customer, str(row.to_account))
        first = first_pay.get(key, pd.NaT)
        new_flag.append(bool(pd.notna(first) and abs((first - row.ts).total_seconds()) < 1))
    enabled = pd.to_datetime(feature["accessibility_enabled_since"])
    age_hours = (feature["start_ts"] - enabled).dt.total_seconds() / 3600
    new_access = enabled.notna() & age_hours.between(0, 12)
    hour = feature["start_ts"].dt.tz_convert(TZ).dt.hour.fillna(0).to_numpy(dtype=float)
    label = feature["scenario_type"].astype(str).isin(fraud_types).to_numpy()
    matrix = np.column_stack(
        [
            ratio,
            np.array(new_flag, dtype=float),
            feature["session_id"].astype(str).isin(paste_sessions).to_numpy(dtype=float),
            feature["in_call_audio_mode"].fillna(False).to_numpy(dtype=float),
            new_access.fillna(False).to_numpy(dtype=float),
            feature["remote_access_app_installed"].fillna(False).to_numpy(dtype=float),
            hour,
        ]
    )
    score = fit_logit(matrix, label)
    auc = auc_score(label, score)
    print(f"  AUC logistic phiên: {auc:.3f}")
    report.add("Baseline rò rỉ AUC", f"{auc:.3f}", "0.80–0.93", "amount/balance, người nhận mới, paste, cuộc gọi, accessibility, remote, giờ", 0.80 <= auc <= 0.93)
    fraud_scores = score[label]
    if len(fraud_scores):
        threshold = float(np.quantile(fraud_scores, 0.20))
        caught = float((fraud_scores >= threshold).mean())
        print(f"  Ngưỡng bắt {pct(caught)} lừa đảo")
        print("  Báo nhầm theo loại hợp lệ:")
        legit_rows = feature.loc[~label]
        legit_scores = score[~label]
        for name, index in legit_rows.groupby(legit_rows["scenario_type"].astype(str)).groups.items():
            chosen = legit_scores[legit_rows.index.get_indexer(index)]
            rate = float((chosen >= threshold).mean()) if len(chosen) else 0.0
            print(f"    {name}: {pct(rate)} ({len(chosen)} phiên)")
    else:
        report.add("Ngưỡng 80% lừa đảo", "không có phiên lừa", "có", "-", False)

    observed_cols: set[str] = set()
    for path in observed.glob("*.parquet"):
        observed_cols |= set(pd.read_parquet(path).columns)
    leaked = observed_cols & LEAKED
    report.add("observed không lộ nhãn vụ", "có " + ",".join(sorted(leaked)) if leaked else "không", "không có case_id, scenario_type, câu trả lời", "-", not leaked)

    print()
    report.print()
    if not report.ok():
        failed = sum(1 for row in report.rows if not row[-1])
        print(f"\n{failed} mục không đạt.")
        sys.exit(1)
    print("\nTất cả kiểm tra bước 6 đều đạt.")


if __name__ == "__main__":
    main()
