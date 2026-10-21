"""Lưới độ nhạy: ott_share × tỷ lệ báo cáo × η.

η là hệ số GIẢ ĐỊNH nhân vào p_stop. ott_share là tỷ lệ cuộc gọi OTT hiện trên thiết bị.
report_rate_scale nhân tỷ lệ báo cáo trong config, trần 1.
Không sinh lại sổ giao dịch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.service import actions_from_families, session_families

FRAUD_TYPES = {"police", "tax_authority", "bank_staff", "relative", "deposit", "investment", "romance", "own_name_safe_account"}
STOP_ACTIONS = {"pause_and_verify", "guardian_confirm", "friction", "show_chain", "recovery_window", "hangup_required", "hold"}


def load_config() -> dict:
    import yaml

    with (SIM / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def call_family(calls: pd.DataFrame, sessions: pd.DataFrame, share: float, seed: int) -> pd.Series:
    rng = np.random.default_rng(np.random.SeedSequence([seed, int(round(share * 100))]))
    work = calls.copy()
    hide = work["channel"].astype(str).eq("ott").to_numpy() & (rng.random(len(work)) >= share)
    work = work.loc[~hide]
    if work.empty:
        return pd.Series(False, index=sessions["session_id"].to_numpy())
    merged = work.merge(sessions[["session_id", "start_ts", "end_ts"]], on="session_id", how="inner")
    finish = merged["ts"] + pd.to_timedelta(merged["duration_s"].fillna(0), unit="s")
    seen = (merged["ts"] <= merged["start_ts"]) & (finish > merged["start_ts"])
    seen = seen | ((finish > merged["start_ts"]) & (finish <= merged["end_ts"]))
    hit = set(merged.loc[seen, "session_id"].astype(str))
    return sessions["session_id"].astype(str).isin(hit)


def main() -> None:
    cfg = load_config()
    grid = cfg["labels"]["sensitivity"]
    spec = cfg["labels"]
    observed = SIM / "out" / "observed"
    truth = SIM / "out" / "truth"
    families = session_families(observed)
    families["session_id"] = families["session_id"].astype(str)
    sessions = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "customer_id", "start_ts", "end_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["customer_id"] = sessions["customer_id"].astype(str)
    calls = pd.read_parquet(
        observed / "call_events.parquet",
        columns=["session_id", "ts", "duration_s", "channel"],
    )
    calls["session_id"] = calls["session_id"].astype(str)
    scenarios = pd.read_parquet(truth / "truth_scenarios.parquet", columns=["session_id", "case_id", "scenario_type", "hangs_up_before_transfer"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    scenarios["case_id"] = scenarios["case_id"].astype(str)
    customers = pd.read_parquet(observed / "customers.parquet", columns=["customer_id", "segment", "has_guardian"])
    latent = pd.read_parquet(truth / "customers_latent.parquet", columns=["customer_id", "lives_alone"])
    model = pd.read_parquet(truth / "response_model.parquet")
    transactions = pd.read_parquet(
        observed / "transactions.parquet",
        columns=["tx_id", "session_id", "from_account", "to_account", "amount", "tx_type"],
    )
    accounts = pd.read_parquet(observed / "accounts.parquet", columns=["account_id", "bank_id"])
    truth_accounts = pd.read_parquet(truth / "accounts_truth.parquet", columns=["account_id", "mule_type"])
    cases = pd.read_parquet(truth / "truth_cases.parquet", columns=["case_id", "scam_type"])
    b01 = set(accounts.loc[accounts["bank_id"].astype(str).eq("B01"), "account_id"].astype(str))
    safe = set(truth_accounts.loc[truth_accounts["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str))
    scam_of = dict(zip(cases["case_id"].astype(str), cases["scam_type"].astype(str)))
    tx = transactions.loc[transactions["session_id"].notna()].copy()
    tx["session_id"] = tx["session_id"].astype(str)
    tx["from_account"] = tx["from_account"].astype(str)
    tx["to_account"] = tx["to_account"].astype(str)
    moved = tx.loc[tx["tx_type"].astype(str).eq("transfer") & tx["from_account"].isin(b01) & (tx["amount"] > 0)]
    amount_of = moved.groupby("session_id")["amount"].sum()
    safe_sessions = set(moved.loc[moved["to_account"].isin(safe), "session_id"].astype(str))
    joined = families.merge(scenarios, on="session_id", how="left")
    joined = joined.merge(sessions[["session_id", "customer_id"]], on="session_id", how="left")
    segment_of = dict(zip(customers["customer_id"].astype(str), customers["segment"].astype(str)))
    guardian_of = dict(zip(customers["customer_id"].astype(str), customers["has_guardian"].astype(bool)))
    alone_of = dict(zip(latent["customer_id"].astype(str), latent["lives_alone"].astype(bool)))
    lookup = model.set_index(["scenario_group", "segment", "action", "guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"])
    rates = spec["report_rate"]

    def group_of(row) -> str:
        kind = str(row.scenario_type)
        if row.session_id in safe_sessions and kind in FRAUD_TYPES:
            return "own_name_safe_account"
        if kind == "legit_guided_by_relative":
            return "legit_guided_by_relative"
        if kind in FRAUD_TYPES:
            return kind
        return "legit"

    def stop_of(row, action: str) -> float:
        group = group_of(row)
        segment = "cao tuổi" if segment_of.get(str(row.customer_id)) == "cao tuổi" else "thường"
        guardian = bool(action == "hold" and guardian_of.get(str(row.customer_id), False))
        coached = group in {"police", "bank_staff", "tax_authority", "own_name_safe_account"}
        alone = bool(alone_of.get(str(row.customer_id), False))
        hangs = bool(row.hangs_up_before_transfer) if pd.notna(row.hangs_up_before_transfer) else False
        chosen = lookup.loc[(group, segment, action, guardian, coached, alone, hangs)]
        return float(chosen["p_stop"])

    fraud_mask = joined["scenario_type"].astype(str).isin(FRAUD_TYPES) | joined["session_id"].isin(safe_sessions)
    guided_mask = joined["scenario_type"].astype(str).eq("legit_guided_by_relative")
    fraud = joined.loc[fraud_mask].copy()
    base_actions = ["allow", "g1_banner", "pause_and_verify", "hangup_required"]
    stop_table = {action: fraud.apply(lambda row, name=action: stop_of(row, name), axis=1).to_numpy(dtype=float) for action in base_actions}
    inclusion_rate = []
    for row in fraud.itertuples(index=False):
        kind = "own_name_safe_account" if row.session_id in safe_sessions else scam_of.get(str(row.case_id), str(row.scenario_type))
        inclusion_rate.append(float(rates.get(kind, 0.5)))
    inclusion_rate = np.array(inclusion_rate, dtype=float)
    fraud_amount = fraud["session_id"].map(amount_of).fillna(0).to_numpy(dtype=float)
    print("Lưới độ nhạy (GIẢ ĐỊNH). η nhân p_stop. report_rate_scale nhân tỷ lệ báo cáo, trần 1.")
    print(f"{'ott':>6} {'báo':>6} {'η':>6} {'cúp máy người nhà':>18} {'High trên lừa đảo':>18} {'tiền tránh được':>18}")
    seed = int(cfg["seed"])
    for share in grid["ott_share"]:
        seen = call_family(calls, sessions, float(share), seed)
        seen.index = sessions["session_id"].to_numpy()
        view = families.copy()
        view["call"] = view["session_id"].map(seen).fillna(False).astype(bool)
        actions = actions_from_families(view)
        action_of = dict(zip(view["session_id"], actions))
        fraud_action = fraud["session_id"].map(action_of).fillna("allow")
        guided_action = joined.loc[guided_mask, "session_id"].map(action_of)
        hangup = float(guided_action.eq("hangup_required").mean()) if len(guided_action) else 0.0
        high = float(fraud_action.eq("hangup_required").mean()) if len(fraud_action) else 0.0
        for scale in grid["report_rate_scale"]:
            weight = np.minimum(1.0, inclusion_rate * float(scale))
            for eta in grid["eta_scale"]:
                stopped = np.array([stop_table[action][index] if action in stop_table else 0.0 for index, action in enumerate(fraud_action)], dtype=float)
                active = fraud_action.isin(STOP_ACTIONS).to_numpy()
                avoided = float((fraud_amount * stopped * active * weight * float(eta)).sum())
                print(f"{float(share):6.1f} {float(scale):6.1f} {float(eta):6.1f} {hangup:18.2%} {high:18.2%} {avoided:18,.0f}")


if __name__ == "__main__":
    main()
