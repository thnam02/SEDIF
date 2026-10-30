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

from engine.policy.v24 import STRING_LEVEL, Facts, MemoryState, evaluate, load_policy, threshold
from eval.decision import isotonic_apply, shift_rate, with_response
from eval.model_v21 import _matrix, load_profile, prepare
from eval.policy_ablation import money_parts
from eval.split import FRAUD_TYPES
from features.store import TRANSFER_TYPES

LEVELS = ("none", "banner", "medium", "high")
LEVEL1_RULES = (
    "R_MODEL_COST",
    "R_CALL_NEWPAYEE",
    "R_SIMO_ALONE",
    "R_DEVICE_CAPABILITY",
    "R_BEHAVIOUR_ONLY",
)


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
        merchant_verified=None if _opt(row.get("merchant_verified")) is None else bool(int(_opt(row.get("merchant_verified")))),
        simo_listed=bool(row["simo_listed"]),
        prior_payments=int(row["payee_transfers_before_listing"]),
        first_days_before_listing=float(row["payee_first_days_before_listing"]),
        p_fraud=float(probability),
        segment=str(row["segment"]),
        behaviour_p_rarity=None,
        same_task_sessions=None,
        limit_per_txn=limit_per_txn,
    )


def _criteria(row: pd.Series) -> dict[str, bool]:
    age = _opt(row.get("payee_account_age_days"))
    senders = _opt(row.get("distinct_senders_7d"))
    passed = _opt(row.get("passthrough_24h"))
    return {
        "age<365": age is not None and age < float(threshold("recipient_age_days_below")),
        "distinct_senders_7d>6": senders is not None and senders > float(threshold("distinct_senders_7d_above")),
        "passthrough_24h>0.951": passed is not None and passed > float(threshold("passthrough_24h_above")),
    }


def _rule_table(title: str, chosen: pd.DataFrame, ge2_ids: set[str], n_all: int) -> None:
    print(f"simulation only: {title}", flush=True)
    print(f"  n {len(chosen)}  of class {100 * len(chosen) / n_all:.2f}%" if n_all else f"  n {len(chosen)}", flush=True)
    counts: dict[str, int] = {}
    sole: dict[str, int] = {}
    episode = 0
    for rules in chosen["rules"]:
        causing = [rule for rule in rules if rule in ge2_ids]
        if not causing:
            episode += 1
        for rule in rules:
            counts[rule] = counts.get(rule, 0) + 1
        if len(causing) == 1:
            sole[causing[0]] = sole.get(causing[0], 0) + 1
    print(f"  {'rule':<28} {'fired':>8} {'sole':>8} {'fired%all':>10} {'sole%all':>10}", flush=True)
    names = sorted(set(counts) | ge2_ids, key=lambda name: (-counts.get(name, 0), name))
    for name in names:
        fired_n = counts.get(name, 0)
        sole_n = sole.get(name, 0) if name in ge2_ids else 0
        fired_pct = 100 * fired_n / n_all if n_all else float("nan")
        sole_pct = 100 * sole_n / n_all if n_all and name in ge2_ids else float("nan")
        sole_cell = f"{sole_n:8d}" if name in ge2_ids else f"{'—':>8}"
        sole_pct_cell = f"{sole_pct:9.2f}%" if name in ge2_ids else f"{'—':>10}"
        print(f"  {name:<28} {fired_n:8d} {sole_cell} {fired_pct:9.2f}% {sole_pct_cell}", flush=True)
    episode_pct = 100 * episode / n_all if n_all else float("nan")
    print(
        f"  {'episode_floor':<28} {episode:8d} {episode:8d} {episode_pct:9.2f}% {episode_pct:9.2f}%",
        flush=True,
    )
    print("  sole = the only rule with level >= 2 on that session. episode_floor = level >= 2 with no such rule.", flush=True)


def _level1_table(chosen: pd.DataFrame, n_legit: int) -> None:
    """Đếm trên mọi phiên legit. sole = đúng một luật mức 1 trong danh sách này."""
    print("simulation only: legit level-1 rules", flush=True)
    print(f"  {'rule':<28} {'fired':>8} {'sole':>8} {'fired%legit':>12} {'sole%legit':>12}", flush=True)
    listed = set(LEVEL1_RULES)
    for name in LEVEL1_RULES:
        fired_n = 0
        sole_n = 0
        for rules in chosen["rules"]:
            hit = [rule for rule in rules if rule in listed]
            if name not in rules:
                continue
            fired_n += 1
            if hit == [name]:
                sole_n += 1
        fired_pct = 100 * fired_n / n_legit if n_legit else float("nan")
        sole_pct = 100 * sole_n / n_legit if n_legit else float("nan")
        print(f"  {name:<28} {fired_n:8d} {sole_n:8d} {fired_pct:11.2f}% {sole_pct:11.2f}%", flush=True)
    print("  sole = the only level-1 rule from this list on that legit session.", flush=True)


def _unusual_table(work: pd.DataFrame) -> None:
    print("simulation only: R_RECIPIENT_UNUSUAL criteria", flush=True)
    hit = work["rules"].map(lambda rules: "R_RECIPIENT_UNUSUAL" in rules)
    print(f"  {'criterion':<28} {'legit':>8} {'legit%rule':>11} {'fraud':>8} {'fraud%rule':>11}", flush=True)
    groups = {
        "legit": hit & ~work["is_fraud"].astype(bool),
        "fraud": hit & work["is_fraud"].astype(bool),
    }
    rows = {name: work.loc[mask] for name, mask in groups.items()}
    labels = ("age<365", "distinct_senders_7d>6", "passthrough_24h>0.951")
    parsed = {name: [_criteria(row) for _, row in frame.iterrows()] for name, frame in rows.items()}
    for label in labels:
        cells = []
        for name in ("legit", "fraud"):
            total = len(parsed[name])
            count = sum(1 for item in parsed[name] if item[label])
            pct = 100 * count / total if total else float("nan")
            cells.append(f"{count:8d} {pct:10.1f}%")
        print(f"  {label:<28} {cells[0]} {cells[1]}", flush=True)
    print(f"  {'combo':<72} {'legit':>8} {'fraud':>8}", flush=True)
    combos: dict[str, dict[str, int]] = {}
    for name in ("legit", "fraud"):
        for item in parsed[name]:
            key = "+".join(label for label in labels if item[label]) or "(none)"
            combos.setdefault(key, {"legit": 0, "fraud": 0})
            combos[key][name] += 1
    for key in sorted(combos, key=lambda item: -(combos[item]["legit"] + combos[item]["fraud"])):
        print(f"  {key:<72} {combos[key]['legit']:8d} {combos[key]['fraud']:8d}", flush=True)


def _simo_table(work: pd.DataFrame, n_legit: int, n_fraud: int) -> None:
    print("simulation only: R_SIMO true vs noise listing", flush=True)
    hit = work["rules"].map(lambda rules: "R_SIMO" in rules)
    noise = work["payee_false_listed"].fillna(False).astype(bool)
    print(f"  {'listing':<16} {'legit':>8} {'legit%all':>10} {'fraud':>8} {'fraud%all':>10}", flush=True)
    for label, mask in (("true", ~noise), ("noise", noise)):
        legit_n = int((hit & mask & ~work["is_fraud"].astype(bool)).sum())
        fraud_n = int((hit & mask & work["is_fraud"].astype(bool)).sum())
        legit_pct = 100 * legit_n / n_legit if n_legit else float("nan")
        fraud_pct = 100 * fraud_n / n_fraud if n_fraud else float("nan")
        print(f"  {label:<16} {legit_n:8d} {legit_pct:9.2f}% {fraud_n:8d} {fraud_pct:9.2f}%", flush=True)


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
    meta = frames["test"][["session_id", "start_ts", "in_call", "call_ended_in_session", "acct_input", "merchant_verified"]].copy()
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
    fired_rows: list[list[str]] = []
    total = len(work)
    for index, row in work.iterrows():
        outcome = evaluate(_facts(row, scores[str(row["session_id"])]), state)
        levels.append(STRING_LEVEL[outcome.level])
        fired_rows.append(outcome.rules_fired)
        if index and index % 5000 == 0:
            print(f"simulation only: {index}/{total}", flush=True)
    work["level"] = levels
    work["rules"] = fired_rows
    work["payee_false_listed"] = work["payee_false_listed"].fillna(False).astype(bool)
    answered = with_response(work, bundle["hang_of"], bundle["alone_of"], bundle["response"])
    saved, damage, rate, saved_row, _ = money_parts(answered)
    fraud = answered["is_fraud"].to_numpy(dtype=bool)
    level = answered["level"].to_numpy()
    ge1 = np.isin(level, ["banner", "medium", "high"])
    ge2 = np.isin(level, ["medium", "high"])
    recall = float(ge2[fraud].mean()) if fraud.any() else float("nan")
    legit = float(ge2[~fraud].mean()) if (~fraud).any() else float("nan")
    legit_ge1 = float(ge1[~fraud].mean()) if (~fraud).any() else float("nan")
    scenario = answered["scam_type"].astype(str).to_numpy()
    print("simulation only", flush=True)
    print(f"n {total}  tiền tránh được {saved:,.0f} VND ({100 * rate:.1f}% của {damage:,.0f})", flush=True)
    print(f"recall ≥ level 2 {100 * recall:.2f}%", flush=True)
    fraud_ge1 = float(ge1[fraud].mean()) if fraud.any() else float("nan")
    print(f"legit ≥ level 1 {100 * legit_ge1:.2f}%", flush=True)
    print(f"fraud ≥ level 1 {100 * fraud_ge1:.2f}%", flush=True)
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
    ge2_ids = {rule["id"] for rule in load_policy()["rules"] if int(rule["level"]) >= 2}
    at_ge2 = answered["level"].isin(["medium", "high"])
    n_legit = int((~fraud).sum())
    n_fraud = int(fraud.sum())
    _level1_table(answered.loc[~answered["is_fraud"].astype(bool)], n_legit)
    _rule_table("legit sessions at level >= 2", answered.loc[at_ge2 & ~answered["is_fraud"].astype(bool)], ge2_ids, n_legit)
    _rule_table("fraud sessions at level >= 2", answered.loc[at_ge2 & answered["is_fraud"].astype(bool)], ge2_ids, n_fraud)
    _unusual_table(answered)
    _simo_table(answered, n_legit, n_fraud)


if __name__ == "__main__":
    main()
