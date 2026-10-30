"""Replay the test set and describe R_CONTEXTUAL_ONLY. simulation only.

The packaged rule is unchanged. A session is "level 1 only via R_CONTEXTUAL_ONLY"
when the decided level is 1 and that rule is the only one that fired. Candidate
definitions are scored afterwards: they can only remove that level-1 contribution.
Level >= 2 stays the outcome the current rules already produced.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "fides"), str(REPO)]

from engine.policy.v24 import MemoryState, STRING_LEVEL, evaluate
from eval.model_v21 import prepare

_spec = importlib.util.spec_from_file_location("sim_policy_v24", REPO / "scripts" / "sim_policy_v24.py")
_sim = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_sim)
_facts = _sim._facts
_identities = _sim._identities
_scores = _sim._scores

# Same default the bank asks a purpose for, and candidate (b) uses >=.
PURPOSE_VND = 10_000_000
CONTEXT = (
    "has_call",
    "entry_paste_or_qr",
    "beneficiary_is_new",
    "recent_funds_event",
)
SOURCES = {
    "has_call": "sdk",
    "entry_paste_or_qr": "sdk",
    "beneficiary_is_new": "core_banking",
    "recent_funds_event": "core_banking",
}
FLAGS = CONTEXT + ("amount>=purpose",)


def _present(signals) -> dict[str, bool]:
    found = {item.signal_id: item.status == "present" for item in signals}
    return {name: bool(found.get(name)) for name in CONTEXT}


def _combo(flags: dict[str, bool]) -> str:
    names = [name for name in FLAGS if flags.get(name)]
    return "+".join(names) if names else "(none)"


def _candidate_a(flags: dict[str, bool]) -> bool:
    """One present contextual signal stays 0. Two sources present becomes 1."""
    sources = {SOURCES[name] for name in CONTEXT if flags[name]}
    return len(sources) >= 2


def _candidate_b(flags: dict[str, bool]) -> bool:
    call_new = flags["has_call"] and flags["beneficiary_is_new"]
    pasted_new = flags["entry_paste_or_qr"] and flags["beneficiary_is_new"] and flags["amount>=purpose"]
    return call_new or pasted_new


def _candidate_c(flags: dict[str, bool]) -> bool:
    return flags["has_call"] and flags["beneficiary_is_new"]


def _ge1(level: int, rules: list[str], fires: bool) -> bool:
    """Keep a level >= 2 outcome. A stricter contextual rule only drops a sole level-1 hit."""
    if level >= 2:
        return True
    if any(rule != "R_CONTEXTUAL_ONLY" for rule in rules):
        return True
    return fires


def _rate(mask, chosen) -> float:
    picked = chosen.sum() if hasattr(chosen, "sum") else sum(chosen)
    return 100 * float(picked) / float(mask) if mask else float("nan")


def _replay():
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
    rows = []
    total = len(work)
    for index, row in work.iterrows():
        outcome = evaluate(_facts(row, scores[str(row["session_id"])]), state)
        flags = _present(outcome.signals)
        flags["amount>=purpose"] = int(row["amount"]) >= PURPOSE_VND
        rows.append(
            {
                "fraud": bool(row["is_fraud"]),
                "level": int(outcome.level),
                "rules": list(outcome.rules_fired),
                "flags": flags,
                "combo": _combo(flags),
            }
        )
        if index and index % 5000 == 0:
            print(f"simulation only: {index}/{total}", flush=True)
    return rows


def _print_signals(sole: list[dict], legit: list[dict], fraud: list[dict]) -> None:
    n_legit = len(legit)
    n_fraud = len(fraud)
    print("simulation only: contextual signals on legit level 1 via R_CONTEXTUAL_ONLY alone", flush=True)
    print(f"  sole sessions {len(sole)}  {100 * len(sole) / n_legit:.2f}% of legit" if n_legit else f"  sole sessions {len(sole)}", flush=True)
    print(f"  {'signal':<28} {'sole':>8} {'%sole':>8} {'%all legit':>12} {'%all fraud':>12}", flush=True)
    for name in CONTEXT:
        count = sum(1 for item in sole if item["flags"][name])
        legit_n = sum(1 for item in legit if item["flags"][name])
        fraud_n = sum(1 for item in fraud if item["flags"][name])
        sole_pct = 100 * count / len(sole) if sole else float("nan")
        print(
            f"  {name:<28} {count:8d} {sole_pct:7.2f}% {100 * legit_n / n_legit:11.2f}% {100 * fraud_n / n_fraud:11.2f}%",
            flush=True,
        )
    print("  present = status present. %sole is of the sole cohort. %all is of every legit or fraud session.", flush=True)


def _print_combos(sole: list[dict], legit: list[dict], fraud: list[dict]) -> None:
    n_legit = len(legit)
    n_fraud = len(fraud)
    ranked: dict[str, int] = {}
    for item in sole:
        ranked[item["combo"]] = ranked.get(item["combo"], 0) + 1
    legit_combo: dict[str, int] = {}
    fraud_combo: dict[str, int] = {}
    for item in legit:
        legit_combo[item["combo"]] = legit_combo.get(item["combo"], 0) + 1
    for item in fraud:
        fraud_combo[item["combo"]] = fraud_combo.get(item["combo"], 0) + 1
    print("simulation only: top combinations on that sole cohort", flush=True)
    print(f"  purpose threshold {PURPOSE_VND} VND, flag amount>=purpose means amount >= threshold", flush=True)
    print(f"  {'combination':<72} {'sole':>8} {'%all legit':>12} {'%all fraud':>12}", flush=True)
    ordered = sorted(ranked, key=lambda key: (-ranked[key], key))[:10]
    for key in ordered:
        sole_n = ranked[key]
        legit_pct = 100 * legit_combo.get(key, 0) / n_legit if n_legit else float("nan")
        fraud_pct = 100 * fraud_combo.get(key, 0) / n_fraud if n_fraud else float("nan")
        print(f"  {key:<72} {sole_n:8d} {legit_pct:11.2f}% {fraud_pct:11.2f}%", flush=True)
    print("  sole = legit sessions at level 1 whose only fired rule is R_CONTEXTUAL_ONLY.", flush=True)
    print("  %all legit and %all fraud count every session with that same combination, not only the sole cohort.", flush=True)


def _print_candidates(rows: list[dict]) -> None:
    legit = [item for item in rows if not item["fraud"]]
    fraud = [item for item in rows if item["fraud"]]
    specs = (
        ("current", lambda flags: True),
        ("a two sources", _candidate_a),
        ("b call+new or paste+new+amount", _candidate_b),
        ("c call+new", _candidate_c),
    )
    print("simulation only: candidate R_CONTEXTUAL_ONLY definitions, no rule change", flush=True)
    print("  sources: has_call sdk, entry_paste_or_qr sdk, beneficiary_is_new core_banking, recent_funds_event core_banking", flush=True)
    print("  a: one present contextual signal -> 0; present signals from two sources -> 1", flush=True)
    print("  b: (has_call AND beneficiary_is_new) OR (entry_paste_or_qr AND beneficiary_is_new AND amount >= purpose)", flush=True)
    print("  c: has_call AND beneficiary_is_new", flush=True)
    print(f"  {'definition':<36} {'legit>=1':>10} {'fraud>=1':>10} {'legit>=2':>10} {'fraud>=2':>10}", flush=True)
    current_ge2 = None
    for name, predicate in specs:
        rates = []
        ge2 = []
        for group in (legit, fraud):
            hit1 = []
            hit2 = []
            for item in group:
                fires = "R_CONTEXTUAL_ONLY" in item["rules"] and predicate(item["flags"])
                if name == "current":
                    fires = "R_CONTEXTUAL_ONLY" in item["rules"]
                hit1.append(_ge1(item["level"], item["rules"], fires))
                hit2.append(item["level"] >= 2)
            rates.append(_rate(len(group), hit1))
            ge2.append(_rate(len(group), hit2))
        if current_ge2 is None:
            current_ge2 = ge2
        print(f"  {name:<36} {rates[0]:9.2f}% {rates[1]:9.2f}% {ge2[0]:9.2f}% {ge2[1]:9.2f}%", flush=True)
        if ge2 != current_ge2:
            raise SystemExit("level >= 2 changed under a candidate")
    print("  level >= 2 matches the current rules for every candidate. No rule was changed.", flush=True)
    print(f"  level names {STRING_LEVEL[1]} is level 1, {STRING_LEVEL[2]} and {STRING_LEVEL[3]} are level >= 2.", flush=True)


def main() -> None:
    print("simulation only", flush=True)
    rows = _replay()
    legit = [item for item in rows if not item["fraud"]]
    fraud = [item for item in rows if item["fraud"]]
    sole = [
        item
        for item in legit
        if item["level"] == 1 and item["rules"] == ["R_CONTEXTUAL_ONLY"]
    ]
    _print_signals(sole, legit, fraud)
    _print_combos(sole, legit, fraud)
    _print_candidates(rows)


if __name__ == "__main__":
    main()
