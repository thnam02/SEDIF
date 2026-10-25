"""So SIMO và FIDES trên test, seed 45. Nhãn is_fraud lấy từ truth qua split.

a) chỉ banner khi người nhận đã có trên SIMO trước as_of.
b) advanced_ctx_payee bỏ simo_listed, 5 seed.
c) artifact advanced_ctx_payee seed 45.
d) c, rồi SIMO cùng họ thao túng thì ít nhất Medium; SIMO và người nhận quen hai chiều, không có họ thao túng, thì banner.
Họ thao túng là call, entry, mobilization. Quen hai chiều: hai khách đã chuyển cho nhau trước start_ts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from eval.decision import isotonic_apply, scale_for, shift_rate, with_response
from eval.model_v21 import (
    ADVANCED_CTX,
    ARTIFACTS,
    COST_ETA,
    MH_CAP,
    SIM,
    TRAIN_SEEDS,
    _assign_levels,
    _eligible,
    _family_counts,
    _matrix,
    _metrics,
    fit_profile,
    load_profile,
    prepare,
)
from eval.policy_ablation import money_parts
from features.store import TRANSFER_TYPES, _ns

SCENARIOS = ("police", "tax_authority", "bank_staff", "relative", "deposit", "investment", "romance", "own_name_safe_account")


def _payee_of_session() -> pd.DataFrame:
    observed = SIM / "out" / "observed"
    sessions = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "customer_id", "account_id", "start_ts", "end_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions["customer_id"] = sessions["customer_id"].astype(str)
    sessions["account_id"] = sessions["account_id"].astype(str)
    transactions = pd.read_parquet(observed / "transactions.parquet", columns=["ts", "from_account", "to_account", "amount", "tx_type", "session_id"])
    transactions["session_id"] = transactions["session_id"].astype(str)
    transactions["from_account"] = transactions["from_account"].astype(str)
    transactions["to_account"] = transactions["to_account"].astype(str)
    kind = transactions["tx_type"].astype(str)
    main = transactions.loc[kind.isin(TRANSFER_TYPES)].sort_values(["amount", "ts"], kind="mergesort").groupby("session_id", sort=False).tail(1)
    payee = dict(zip(main["session_id"], main["to_account"]))
    sessions["payee_account"] = sessions["session_id"].map(payee)
    sessions["as_of"] = _ns(pd.to_datetime(sessions["end_ts"]) + pd.Timedelta(microseconds=1))
    sessions["start_ns"] = _ns(pd.to_datetime(sessions["start_ts"]))
    return sessions, transactions


def _customer_of(accounts: pd.DataFrame) -> dict[str, str]:
    return dict(zip(accounts["account_id"].astype(str), accounts["customer_id"].astype(str)))


def _both_ways(transactions: pd.DataFrame, owner: dict[str, str]) -> dict[tuple[str, str], int]:
    left = transactions["from_account"].map(owner)
    right = transactions["to_account"].map(owner)
    stamp = _ns(pd.to_datetime(transactions["ts"]))
    ok = left.notna() & right.notna() & left.ne(right) & left.ne("") & right.ne("")
    frame = pd.DataFrame({"src": left[ok].astype(str), "dst": right[ok].astype(str), "ts": stamp[ok]})
    first = frame.groupby(["src", "dst"], sort=False)["ts"].min()
    return {(src, dst): int(ts) for (src, dst), ts in first.items()}


def _simo_hits(payee: pd.Series, as_of: np.ndarray, listed: pd.DataFrame) -> np.ndarray:
    lists: dict[str, np.ndarray] = {}
    if len(listed):
        grouped = listed.groupby("account_id", sort=False)["listed_ns"]
        for account, values in grouped:
            lists[str(account)] = np.sort(values.to_numpy(dtype=np.int64))
    out = np.zeros(len(payee), dtype=bool)
    accounts = payee.astype("string").to_numpy()
    for index, account in enumerate(accounts):
        if account is pd.NA or account == "<NA>":
            continue
        stamps = lists.get(str(account))
        if stamps is not None and np.searchsorted(stamps, as_of[index], side="left") > 0:
            out[index] = True
    return out


def _load_listed() -> pd.DataFrame:
    listed = pd.read_parquet(SIM / "out" / "observed" / "simo_list.parquet", columns=["account_id", "listed_at", "source"])
    listed["account_id"] = listed["account_id"].astype(str)
    listed["listed_ns"] = _ns(pd.to_datetime(listed["listed_at"]))
    listed["source"] = listed["source"].astype(str)
    return listed


def _scores(frames, model, columns, categories, apply) -> dict[str, float]:
    mapping: dict[str, float] = {}
    for name in ("train", "test", "buffer"):
        raw = model.predict(_matrix(frames[name], columns, categories))
        mapping.update(zip(frames[name]["session_id"].astype(str), apply(raw)))
    return mapping


def _levels_from_map(bundle, frames, mapping: dict[str, float], tau_hi: float) -> np.ndarray:
    population = bundle["population"]
    probability = population["session_id"].map(mapping).to_numpy(dtype=float)
    amount = population["amount"].to_numpy(dtype=float)
    scale = scale_for(population["segment"], bundle["fp_table"])
    legit_train = population["session_id"].isin(bundle["train_ids"]).to_numpy() & ~population["is_fraud"].to_numpy(dtype=bool)
    families, _, _ = _family_counts(population, legit_train, True, "strict")
    eligible, _, _ = _eligible(population, legit_train)
    return _assign_levels(probability, amount, families, scale, tau_hi, eligible)


def _apply_rule(level: np.ndarray, simo: np.ndarray, manip: np.ndarray, familiar: np.ndarray, legit: np.ndarray, probability: np.ndarray) -> np.ndarray:
    """SIMO + họ thao túng thì ít nhất Medium. SIMO + quen hai chiều, không thao túng, thì banner.

    Nâng Medium trên phiên hợp lệ chỉ đến khi MH còn ≤ 0,45%.
    """
    out = level.copy()
    floor = simo & manip & np.isin(out, ["none", "banner"])
    out[floor] = "medium"
    cap = simo & familiar & ~manip
    out[cap] = "banner"
    added = legit & floor & ~cap
    mh = legit & np.isin(out, ["medium", "high"])
    room = int(np.floor(MH_CAP * int(legit.sum()) - int((mh & ~added).sum())))
    if room < 0:
        room = 0
    order = np.flatnonzero(added)[np.argsort(-probability[added], kind="mergesort")]
    if len(order) > room:
        out[order[room:]] = level[order[room:]]
    return out


def _report(bundle, level: np.ndarray) -> dict:
    population = bundle["population"]
    scored = population.copy()
    scored["level"] = level
    test = scored.loc[scored["session_id"].isin(bundle["test_ids"])].reset_index(drop=True)
    answered = with_response(test, bundle["hang_of"], bundle["alone_of"], bundle["response"])
    saved, _, _, saved_row, _ = money_parts(answered)
    fraud = answered["is_fraud"].to_numpy(dtype=bool)
    amount = pd.to_numeric(answered["amount"], errors="coerce").fillna(0).to_numpy(dtype=float)
    stop = pd.to_numeric(answered["p_stop"], errors="coerce").fillna(0).to_numpy(dtype=float)
    banner_money = float((amount * stop)[fraud & (answered["level"].to_numpy() == "banner")].sum())
    level_test = answered["level"].to_numpy()
    caught = np.isin(level_test, ["medium", "high"])
    friend = answered["scenario_type"].astype(str).eq("legit_friend_loan").to_numpy()
    guided = answered["scenario_type"].astype(str).eq("legit_guided_by_relative").to_numpy()
    legit = scored.loc[bundle["legit"], "level"]
    scenario = answered["scam_type"].astype(str).to_numpy()
    by_scenario = {}
    for name in SCENARIOS:
        mask = fraud & (scenario == name)
        by_scenario[name] = {
            "n": int(mask.sum()),
            "recall": float(caught[mask].mean()) if mask.any() else float("nan"),
            "money": float(saved_row[mask].sum()) if mask.any() else 0.0,
        }
    return {
        "saved": float(saved),
        "banner_money": banner_money,
        "recall": float(caught[fraud].mean()) if fraud.any() else float("nan"),
        "legit_mh": float(legit.isin(["medium", "high"]).mean()) if len(legit) else float("nan"),
        "medium_loan": float(np.mean(level_test[friend] == "medium")) if friend.any() else float("nan"),
        "medium_guided": float(np.mean(level_test[guided] == "medium")) if guided.any() else float("nan"),
        "scenario": by_scenario,
        "level": level_test,
        "fraud": fraud,
        "scenario_name": scenario,
        "saved_row": saved_row,
        "session_id": answered["session_id"].astype(str).to_numpy(),
    }


def _line(name: str, row: dict) -> None:
    inv = row["scenario"]["investment"]
    print(
        f"{name}: tiền {row['saved']:,.0f}  banner {row['banner_money']:,.0f}  "
        f"recall≥Med {100 * row['recall']:.1f}%  MH {100 * row['legit_mh']:.2f}%  "
        f"vay {100 * row['medium_loan']:.1f}%  nhà {100 * row['medium_guided']:.1f}%  "
        f"investment {100 * inv['recall']:.1f}% ({inv['money']:,.0f})",
        flush=True,
    )


def _detect_mules(transactions: pd.DataFrame, mules: set[str]) -> dict[str, int]:
    """GIẢ ĐỊNH: ngân hàng giữ tài khoản thấy chuyển đi ≥ 50% khoản nhận trong 24 giờ. Mốc phát hiện là lúc chuyển đi."""
    kind_in = transactions["to_account"].isin(mules)
    kind_out = transactions["from_account"].isin(mules)
    inbound = transactions.loc[kind_in, ["to_account", "ts", "amount"]].rename(columns={"to_account": "account"})
    outbound = transactions.loc[kind_out, ["from_account", "ts", "amount"]].rename(columns={"from_account": "account"})
    inbound["side"] = 1
    outbound["side"] = -1
    events = pd.concat([inbound, outbound], ignore_index=True)
    events["ts_ns"] = _ns(pd.to_datetime(events["ts"]))
    events["amount"] = pd.to_numeric(events["amount"], errors="coerce").fillna(0).to_numpy(dtype=np.int64)
    found: dict[str, int] = {}
    window = 24 * 60 * 60 * 1_000_000_000
    for account, part in events.groupby("account", sort=False):
        part = part.sort_values(["ts_ns", "side"], kind="mergesort")
        stamps = part["ts_ns"].to_numpy(dtype=np.int64)
        amounts = part["amount"].to_numpy(dtype=np.int64)
        sides = part["side"].to_numpy(dtype=np.int8)
        received: list[tuple[int, int]] = []
        cursor = 0
        total = 0
        for stamp, amount, side in zip(stamps, amounts, sides):
            if side > 0:
                received.append((int(stamp), int(amount)))
                total += int(amount)
                continue
            while cursor < len(received) and received[cursor][0] < int(stamp) - window:
                total -= received[cursor][1]
                cursor += 1
            if total > 0 and int(amount) >= 0.5 * total:
                found[str(account)] = int(stamp)
                break
    return found


def _rescore(frames, bundle, model, columns, categories, apply, tau_hi: float, simo_of: pd.Series) -> np.ndarray:
    edited = {}
    for name, frame in frames.items():
        work = frame.copy()
        work["simo_listed"] = work["session_id"].astype(str).map(simo_of).fillna(work["simo_listed"]).astype(float)
        edited[name] = work
    mapping = _scores(edited, model, columns, categories, apply)
    return _levels_from_map(bundle, edited, mapping, tau_hi), mapping


def main() -> None:
    frames, bundle = prepare()
    sessions, transactions = _payee_of_session()
    observed_accounts = pd.read_parquet(SIM / "out" / "observed" / "accounts.parquet", columns=["account_id", "customer_id"])
    observed_accounts["account_id"] = observed_accounts["account_id"].astype(str)
    observed_accounts["customer_id"] = observed_accounts["customer_id"].astype(str)
    mule_flag = pd.read_parquet(SIM / "out" / "truth" / "accounts_truth.parquet", columns=["account_id", "is_mule"])
    mule_flag["account_id"] = mule_flag["account_id"].astype(str)
    accounts = observed_accounts.merge(mule_flag, on="account_id", how="left")
    accounts["is_mule"] = accounts["is_mule"].fillna(False).astype(bool)
    owner = _customer_of(accounts)
    sessions["sender_customer"] = sessions["customer_id"]
    sessions["payee_customer"] = sessions["payee_account"].map(owner)
    ways = _both_ways(transactions, owner)
    start_ns = dict(zip(sessions["session_id"], sessions["start_ns"]))
    familiar_ids = []
    for row in sessions.itertuples(index=False):
        src, dst, began = row.sender_customer, row.payee_customer, int(row.start_ns)
        if not isinstance(dst, str) or src == dst:
            familiar_ids.append(False)
            continue
        forward = ways.get((src, dst))
        backward = ways.get((dst, src))
        familiar_ids.append(forward is not None and backward is not None and forward < began and backward < began)
    familiar_of = pd.Series(familiar_ids, index=sessions["session_id"])
    listed = _load_listed()
    session_payee = dict(zip(sessions["session_id"], sessions["payee_account"]))
    as_of_of = dict(zip(sessions["session_id"], sessions["as_of"]))
    population = bundle["population"]
    sid = population["session_id"].astype(str)
    payee = sid.map(session_payee)
    as_of = sid.map(as_of_of).to_numpy(dtype=np.int64)
    simo = _simo_hits(payee, as_of, listed)
    feature_simo = pd.concat(
        [frames[name][["session_id", "simo_listed"]] for name in ("train", "test", "buffer")],
        ignore_index=True,
    ).drop_duplicates("session_id")
    feature_simo["session_id"] = feature_simo["session_id"].astype(str)
    feature_flag = sid.map(dict(zip(feature_simo["session_id"], pd.to_numeric(feature_simo["simo_listed"], errors="coerce").fillna(0)))).to_numpy(dtype=float) >= 1
    print(f"SIMO khớp cột simo_listed: {float((simo == feature_flag).mean()):.4%}  lệch {(simo != feature_flag).sum()}", flush=True)
    simo = feature_flag
    manip = population["call"].to_numpy(dtype=bool) | population["entry"].to_numpy(dtype=bool) | population["funding"].to_numpy(dtype=bool)
    familiar = sid.map(familiar_of).fillna(False).to_numpy(dtype=bool)
    legit = bundle["legit"]

    model, policy, categories = load_profile("advanced_ctx_payee")
    columns = policy["columns"]
    calibrator = policy["calibrator"]

    def apply(raw: np.ndarray) -> np.ndarray:
        fitted = isotonic_apply(raw, np.asarray(calibrator["edges"]), np.asarray(calibrator["values"]))
        return shift_rate(fitted, calibrator["source"], calibrator["target"])

    raw_oof = np.load(ARTIFACTS / "advanced_ctx_payee" / "oof.npy")
    mapping = dict(zip(frames["train"]["session_id"].astype(str), apply(raw_oof)))
    mapping.update(zip(frames["test"]["session_id"].astype(str), apply(model.predict(_matrix(frames["test"], columns, categories)))))
    mapping.update(zip(frames["buffer"]["session_id"].astype(str), apply(model.predict(_matrix(frames["buffer"], columns, categories)))))
    level_c = _levels_from_map(bundle, frames, mapping, policy["tau_hi"])
    probability = sid.map(mapping).to_numpy(dtype=float)
    level_a = np.full(len(population), "none", dtype=object)
    level_a[simo] = "banner"
    level_d = _apply_rule(level_c, simo, manip, familiar, legit, probability)

    print("1. seed 45", flush=True)
    rows = {"a SIMO": _report(bundle, level_a), "c FIDES+SIMO": _report(bundle, level_c), "d FIDES+SIMO+luật": _report(bundle, level_d)}
    for name, row in rows.items():
        _line(name, row)

    no_simo = [name for name in ADVANCED_CTX if name != "simo_listed"]
    seed_rows = []
    level_b = None
    for seed in TRAIN_SEEDS:
        print(f"seed {seed} không simo_listed", flush=True)
        fitted = fit_profile(frames, no_simo, seed)
        metrics = _metrics(bundle, frames, fitted, None, payee_gate=True, payee_normal="strict")
        row = _report(bundle, metrics["_pop_level"])
        row["recall_at_1"] = metrics["recall_at_1"]
        seed_rows.append(row)
        _line(f"b seed {seed}", row)
        if seed == 45:
            level_b = metrics["_pop_level"]
    print(
        "b mean: tiền {:,.0f} ± {:,.0f}  recall≥Med {:.1f}% ± {:.1f}  MH {:.2f}%  vay {:.1f}%  nhà {:.1f}%  investment {:.1f}%".format(
            float(np.mean([row["saved"] for row in seed_rows])),
            float(np.std([row["saved"] for row in seed_rows], ddof=1)),
            100 * float(np.mean([row["recall"] for row in seed_rows])),
            100 * float(np.std([row["recall"] for row in seed_rows], ddof=1)),
            100 * float(np.mean([row["legit_mh"] for row in seed_rows])),
            100 * float(np.mean([row["medium_loan"] for row in seed_rows])),
            100 * float(np.mean([row["medium_guided"] for row in seed_rows])),
            100 * float(np.mean([row["scenario"]["investment"]["recall"] for row in seed_rows])),
        ),
        flush=True,
    )
    for name in SCENARIOS:
        text = "  ".join(
            f"{label.split()[0]} {100 * rows[label]['scenario'][name]['recall']:.1f}%"
            for label in rows
        )
        b45 = _report(bundle, level_b)
        print(f"  {name}: {text}  b45 {100 * b45['scenario'][name]['recall']:.1f}%", flush=True)

    print("2. recall ≥Medium theo SIMO tại as_of", flush=True)
    b45 = _report(bundle, level_b)
    on_test = sid.isin(bundle["test_ids"]).to_numpy()
    fraud_pop = population["is_fraud"].to_numpy(dtype=bool) & on_test
    listed_test = simo & fraud_pop
    order = pd.Series(np.arange(len(population)), index=sid)
    for label, report in (("b", b45), ("c", rows["c FIDES+SIMO"]), ("d", rows["d FIDES+SIMO+luật"])):
        answered_sid = report["session_id"]
        listed_answered = pd.Series(simo, index=sid).loc[answered_sid].to_numpy(dtype=bool)
        for flag, title in ((listed_answered, "đã listed"), (~listed_answered, "chưa listed")):
            mask = report["fraud"] & flag
            recall = float(np.isin(report["level"][mask], ["medium", "high"]).mean()) if mask.any() else float("nan")
            money = float(report["saved_row"][mask].sum()) if mask.any() else 0.0
            print(f"  {label} {title}: n {int(mask.sum())}  recall≥Med {100 * recall:.1f}%  tiền {money:,.0f}", flush=True)
    _ = (listed_test, order, start_ns)

    print("3. vòng phản hồi, GIẢ ĐỊNH 6 giờ sau khi chiều nhận thấy chuyển tiếp", flush=True)
    mules = set(accounts.loc[accounts["is_mule"].astype(bool), "account_id"].astype(str))
    detected = _detect_mules(transactions, mules)
    first_in = transactions.loc[transactions["to_account"].isin(mules)].copy()
    first_in["ts_ns"] = _ns(pd.to_datetime(first_in["ts"]))
    first_ns = first_in.groupby("to_account", sort=False)["ts_ns"].min()
    current = listed.groupby("account_id", sort=False)["listed_ns"].min()
    delays = []
    for account, listed_ns in current.items():
        if account not in mules or account not in first_ns.index:
            continue
        delays.append((int(listed_ns) - int(first_ns.loc[account])) / 3_600_000_000_000)
    print(f"  mule phát hiện bởi chuyển tiếp {len(detected)}/{len(mules)}  trung vị trễ listed−nhận đầu {np.median(delays):.1f} giờ", flush=True)
    six_h = 6 * 3_600_000_000_000
    faster = listed.copy()
    extra = []
    for account, stamp in detected.items():
        extra.append({"account_id": account, "listed_ns": stamp + six_h, "source": "inbound_monitor"})
    if extra:
        faster = pd.concat([faster, pd.DataFrame(extra)], ignore_index=True)
    fast_simo = _simo_hits(payee, as_of, faster)
    base_map = pd.Series(simo, index=sid)
    fast_map = pd.Series(fast_simo, index=sid)
    changed = int((fast_map & ~base_map).sum())
    print(f"  phiên được listed sớm hơn: {changed}", flush=True)
    level_fast, _ = _rescore(frames, bundle, model, columns, categories, apply, policy["tau_hi"], fast_map.groupby(level=0).first())
    level_base_model, _ = _rescore(frames, bundle, model, columns, categories, apply, policy["tau_hi"], base_map.groupby(level=0).first())
    fast_row = _report(bundle, level_fast)
    base_row = _report(bundle, level_base_model)
    level_a_fast = np.full(len(population), "none", dtype=object)
    level_a_fast[fast_simo] = "banner"
    a_fast = _report(bundle, level_a_fast)
    a_base = _report(bundle, level_a)
    print(f"  a tiền ≥Medium {a_fast['saved'] - a_base['saved']:,.0f}  tiền banner {a_fast['banner_money'] - a_base['banner_money']:,.0f}", flush=True)
    print(f"  c tiền ≥Medium {fast_row['saved'] - base_row['saved']:,.0f}  ({base_row['saved']:,.0f} → {fast_row['saved']:,.0f})", flush=True)
    print(f"  c investment {100 * base_row['scenario']['investment']['recall']:.1f}% → {100 * fast_row['scenario']['investment']['recall']:.1f}%", flush=True)

    print("4. thêm 5% tài khoản hợp lệ lên SIMO từ đầu sim, seed 45", flush=True)
    legit_accounts = accounts.loc[~accounts["is_mule"].astype(bool), "account_id"].astype(str).unique()
    rng = np.random.default_rng(45)
    picked = rng.choice(legit_accounts, size=int(round(0.05 * len(legit_accounts))), replace=False)
    sim_start = pd.Timestamp("2026-07-01", tz="Asia/Ho_Chi_Minh")
    noise = listed.copy()
    noise = pd.concat(
        [noise, pd.DataFrame({"account_id": picked, "listed_ns": _ns(pd.Series([sim_start] * len(picked))), "source": "false_list"})],
        ignore_index=True,
    )
    noisy = _simo_hits(payee, as_of, noise)
    noisy_map = pd.Series(noisy, index=sid)
    level_c_noise, map_noise = _rescore(frames, bundle, model, columns, categories, apply, policy["tau_hi"], noisy_map.groupby(level=0).first())
    prob_noise = sid.map(map_noise).to_numpy(dtype=float)
    level_d_noise = _apply_rule(level_c_noise, noisy, manip, familiar, legit, prob_noise)
    level_a_noise = np.full(len(population), "none", dtype=object)
    level_a_noise[noisy] = "banner"
    for label, level in (("a", level_a_noise), ("c", level_c_noise), ("d", level_d_noise)):
        _line(label + " nhiễu", _report(bundle, level))


if __name__ == "__main__":
    main()
