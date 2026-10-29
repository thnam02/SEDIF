"""Replay the simulated test set through the v2.4 policy. simulation only.

Behaviour stays unknown. Call, entry, payee, funding, and SIMO come from the
simulated ledger. Purpose is not taken from the label. The packaged LightGBM
score is unchanged.
"""

from __future__ import annotations

import logging
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
FIDES = REPO / "fides"
SIM = REPO / "fides-sim"
sys.path[:0] = [str(FIDES), str(REPO)]

from engine.policy.v24 import STRING_LEVEL, Facts, MemoryState, evaluate
from eval.decision import isotonic_apply, shift_rate, with_response
from eval.model_v21 import _matrix, load_profile, prepare
from eval.policy_ablation import money_parts
from eval.split import FRAUD_TYPES
from features.store import TRANSFER_TYPES

LEVELS = ("none", "banner", "medium", "high")


def _scores(frame: pd.DataFrame) -> dict[str, float]:
    model, policy, categories = load_profile("advanced_ctx_payee")
    raw = model.predict(_matrix(frame, policy["columns"], categories))
    calibrator = policy["calibrator"]
    fitted = isotonic_apply(raw, np.asarray(calibrator["edges"]), np.asarray(calibrator["values"]))
    probability = shift_rate(fitted, calibrator["source"], calibrator["target"])
    return dict(zip(frame["session_id"].astype(str), probability))


def _identities(session_ids: set[str]) -> pd.DataFrame:
    observed = SIM / "out" / "observed"
    sessions = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "account_id"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["account_id"] = sessions["account_id"].astype(str)
    sessions = sessions.loc[sessions["session_id"].isin(session_ids)]
    accounts = pd.read_parquet(observed / "accounts.parquet", columns=["account_id", "limit_per_txn"])
    accounts["account_id"] = accounts["account_id"].astype(str)
    sessions = sessions.merge(accounts, on="account_id", how="left")
    tx = pd.read_parquet(
        observed / "transactions.parquet",
        columns=["session_id", "to_account", "tx_type", "amount", "ts"],
    )
    tx["session_id"] = tx["session_id"].astype(str)
    tx["to_account"] = tx["to_account"].astype(str)
    tx = tx.loc[tx["session_id"].isin(session_ids) & tx["tx_type"].astype(str).isin(TRANSFER_TYPES)]
    last = tx.sort_values(["amount", "ts"], kind="mergesort").groupby("session_id", sort=False).tail(1)
    return sessions.merge(last[["session_id", "to_account"]], on="session_id", how="left")


def _call_state(in_call: float, ended: float) -> str:
    if in_call == 1:
        return "active"
    if ended == 1:
        return "ended_in_session"
    return "none"


def _task(pasted: bool, new_payee: bool) -> str:
    if pasted:
        return "new_pasted"
    if new_payee:
        return "new_typed"
    return "saved_payee"


def _opt(value) -> float | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _facts(row: pd.Series, probability: float) -> Facts:
    when = pd.Timestamp(row["start_ts"]).floor("us").to_pydatetime()
    new_payee = bool(row["payee"])
    pasted = str(row.get("acct_input") or "") == "pasted"
    funds = []
    if bool(row["funding"]):
        funds = [
            {
                "type": "savings_liquidation",
                "at": (when - timedelta(hours=1)).isoformat(),
                "channel": "app",
            }
        ]
    limit = row.get("limit_per_txn")
    try:
        limit_per_txn = int(limit) if limit == limit and int(limit) > 0 else None
    except (TypeError, ValueError):
        limit_per_txn = None
    payee = str(row.get("to_account") or row["session_id"])
    return Facts(
        sender=str(row["account_id"]),
        payee=payee,
        amount=int(row["amount"]),
        ts=when,
        call_state=_call_state(_opt(row.get("in_call")) or 0.0, _opt(row.get("call_ended_in_session")) or 0.0),
        task_type=_task(pasted, new_payee),
        transfer_purpose="not_asked",
        device_state="none",
        funds_events=funds,
        new_payee=new_payee,
        payee_age_days=_opt(row.get("payee_account_age_days")),
        distinct_senders_7d=_opt(row.get("distinct_senders_7d")),
        passthrough_24h=_opt(row.get("passthrough_24h")),
        simo_listed=bool(row["simo_listed"]),
        prior_payments=int(row["payee_transfers_before_listing"]),
        first_days_before_listing=float(row["payee_first_days_before_listing"]),
        p_fraud=float(probability),
        segment=str(row["segment"]),
        behaviour_p_rarity=None,
        same_task_sessions=None,
        limit_per_txn=limit_per_txn,
    )


def main() -> None:
    print("simulation only", flush=True)
    logging.getLogger("engine.policy").setLevel(logging.ERROR)
    frames, bundle = prepare()
    print("simulation only: scoring packaged model", flush=True)
    scores = _scores(frames["test"])
    population = bundle["population"]
    population = population.loc[population["session_id"].astype(str).isin(bundle["test_ids"])].copy()
    population["session_id"] = population["session_id"].astype(str)
    population = population.drop_duplicates("session_id")
    meta = frames["test"][["session_id", "start_ts", "in_call", "call_ended_in_session", "acct_input"]].copy()
    meta["session_id"] = meta["session_id"].astype(str)
    work = population.merge(meta, on="session_id", how="left")
    work = work.merge(_identities(set(work["session_id"])), on="session_id", how="left")
    work["payee"] = work["payee"].fillna(False).astype(bool)
    work["funding"] = work["funding"].fillna(False).astype(bool)
    work["simo_listed"] = pd.to_numeric(work["simo_listed"], errors="coerce").fillna(0).ge(1)
    work["segment"] = work["segment"].astype("string").fillna("thường")
    work["amount"] = pd.to_numeric(work["amount"], errors="coerce").fillna(0)
    work["payee_transfers_before_listing"] = pd.to_numeric(work["payee_transfers_before_listing"], errors="coerce").fillna(0)
    work["payee_first_days_before_listing"] = pd.to_numeric(work["payee_first_days_before_listing"], errors="coerce").fillna(0)
    work["account_id"] = work["account_id"].astype("string").fillna(work["session_id"])
    work = work.sort_values(["start_ts", "session_id"], kind="mergesort").reset_index(drop=True)
    state = MemoryState()
    levels = []
    total = len(work)
    for index, row in work.iterrows():
        outcome = evaluate(_facts(row, scores[str(row["session_id"])]), state)
        levels.append(STRING_LEVEL[outcome.level])
        if index and index % 5000 == 0:
            print(f"simulation only: {index}/{total}", flush=True)
    work["level"] = levels
    answered = with_response(work, bundle["hang_of"], bundle["alone_of"], bundle["response"])
    saved, damage, rate, saved_row, _ = money_parts(answered)
    fraud = answered["is_fraud"].to_numpy(dtype=bool)
    level = answered["level"].to_numpy()
    ge2 = np.isin(level, ["medium", "high"])
    recall = float(ge2[fraud].mean()) if fraud.any() else float("nan")
    legit = float(ge2[~fraud].mean()) if (~fraud).any() else float("nan")
    scenario = answered["scam_type"].astype(str).to_numpy()
    print("simulation only", flush=True)
    print(f"n {total}  tiền tránh được {saved:,.0f} VND ({100 * rate:.1f}% của {damage:,.0f})", flush=True)
    print(f"recall ≥ level 2 {100 * recall:.2f}%", flush=True)
    print(f"legit ≥ level 2 {100 * legit:.2f}%", flush=True)
    mix = " ".join(f"{name} {100 * float((level[fraud] == name).mean()):.1f}%" for name in LEVELS)
    print(f"fraud levels {mix}", flush=True)
    print("per scenario", flush=True)
    for name in FRAUD_TYPES:
        mask = fraud & (scenario == name)
        if not mask.any():
            print(f"  {name}: n 0", flush=True)
            continue
        caught = float(ge2[mask].mean())
        money = float(saved_row[mask].sum())
        parts = " ".join(f"{key} {100 * float((level[mask] == key).mean()):.1f}%" for key in LEVELS)
        print(f"  {name}: recall ≥2 {100 * caught:.1f}%  tiền {money:,.0f}  {parts}", flush=True)


if __name__ == "__main__":
    main()
