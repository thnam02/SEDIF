"""Seed 45, chỉ test, không train lại. Tách in_call và bản né cuộc gọi.

Cấu hình là artifact advanced_ctx_payee. LightGBM không có cột in_call.
Ép in_call=0 và call_ended_in_session=0 chỉ tắt họ call của phiên lừa.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.decision import isotonic_apply, shift_rate
from eval.model_v21 import (
    _context,
    _matrix,
    _metrics,
    demo_frame,
    load_profile,
)
from eval.policy_ablation import attach_t4c, load_part
from eval.split import build_split

CALL_TYPES = ("police", "bank_staff", "tax_authority")


def _frames() -> tuple[dict[str, pd.DataFrame], dict]:
    parts = build_split()
    for name in ("train", "test"):
        parts[name]["session_id"] = parts[name]["session_id"].astype(str)
    demo = demo_frame({name: parts[name] for name in ("train", "test")})
    frames = {}
    for name in ("train", "test"):
        frame = load_part(name, parts[name])
        frame = attach_t4c(frame, name)
        extra = demo.drop(columns=[column for column in demo.columns if column != "session_id" and column in frame.columns])
        frame = frame.merge(extra.drop_duplicates("session_id"), on="session_id", how="left")
        meta = parts[name].drop_duplicates("session_id").set_index("session_id")
        sid = frame["session_id"].astype(str)
        frame["label"] = sid.map(meta["label"]).astype(int)
        frame["sample_weight"] = sid.map(meta["sample_weight"]).astype(float)
        frame["is_fraud"] = sid.map(meta["is_fraud"]).astype(bool)
        frame["start_ts"] = pd.to_datetime(sid.map(meta["start_ts"]))
        frame["case_id"] = sid.map(meta["case_id"])
        frame["customer_id"] = sid.map(meta["customer_id"]).astype(str)
        frame["scam_type"] = sid.map(meta["scam_type"]).astype(str)
        frames[name] = frame
    frames["buffer"] = frames["test"].iloc[0:0].copy()
    return frames, _context(frames)


def _scores(frames: dict[str, pd.DataFrame]) -> tuple[dict, dict]:
    model, policy, categories = load_profile("advanced_ctx_payee")
    columns = policy["columns"]
    raw_test = model.predict(_matrix(frames["test"], columns, categories))
    calibrator = policy["calibrator"]

    def apply(raw: np.ndarray) -> np.ndarray:
        fitted = isotonic_apply(raw, np.asarray(calibrator["edges"]), np.asarray(calibrator["values"]))
        return shift_rate(fitted, calibrator["source"], calibrator["target"])

    mapping = dict(zip(frames["test"]["session_id"].astype(str), apply(raw_test)))
    return {"map": mapping, "raw_test": raw_test}, policy


def _table(title: str, base: dict, other: dict) -> None:
    print(title, flush=True)
    print(f"{'scenario':<24} {'≥Med gốc':>10} {'≥Med né':>10} {'tiền gốc':>16} {'tiền né':>16}", flush=True)
    names = list(base["scenario_levels"])
    for name in names:
        left = base["scenario_levels"][name]
        right = other["scenario_levels"][name]
        ge_left = left["medium"] + left["high"]
        ge_right = right["medium"] + right["high"]
        print(
            f"{name:<24} {100 * ge_left:9.1f}% {100 * ge_right:9.1f}% "
            f"{base['money_by_scenario'][name]:16,.0f} {other['money_by_scenario'][name]:16,.0f}",
            flush=True,
        )


def _call_split(frames: dict[str, pd.DataFrame], level: np.ndarray, population: pd.DataFrame) -> None:
    test = frames["test"][["session_id", "in_call", "scam_type", "is_fraud"]].copy()
    test["session_id"] = test["session_id"].astype(str)
    test["level"] = test["session_id"].map(dict(zip(population["session_id"].astype(str), level)))
    fraud = test["is_fraud"].to_numpy(dtype=bool)
    kind = test["scam_type"].astype(str).to_numpy()
    call = pd.to_numeric(test["in_call"], errors="coerce").fillna(0).eq(1).to_numpy()
    ge = test["level"].isin(["medium", "high"]).to_numpy()
    print("recall ≥Medium theo in_call, seed 45, không train lại", flush=True)
    print(f"{'scenario':<16} {'in_call':>8} {'n':>6} {'≥Medium':>10}", flush=True)
    for name in CALL_TYPES:
        for flag, label in ((1, "1"), (0, "0")):
            mask = fraud & (kind == name) & (call if flag else ~call)
            rate = float(ge[mask].mean()) if mask.any() else float("nan")
            print(f"{name:<16} {label:>8} {int(mask.sum()):6d} {100 * rate:9.1f}%", flush=True)


def main() -> None:
    frames, bundle = _frames()
    scores, policy = _scores(frames)
    assert int(policy["seed"]) == 45
    original = _metrics(bundle, frames, scores, policy["tau_hi"], payee_gate=True, payee_normal=str(policy.get("payee_normal", "strict")))
    _call_split(frames, original["_pop_level"], bundle["population"])
    evaded = bundle["population"].copy()
    fraud = evaded["is_fraud"].to_numpy(dtype=bool) & evaded["session_id"].astype(str).isin(bundle["test_ids"])
    evaded.loc[fraud, "call"] = False
    swapped = dict(bundle)
    swapped["population"] = evaded
    hidden = _metrics(swapped, frames, scores, policy["tau_hi"], payee_gate=True, payee_normal=str(policy.get("payee_normal", "strict")))
    print("né tránh: in_call=0 và call_ended_in_session=0 trên mọi phiên lừa. Điểm LightGBM giữ nguyên.", flush=True)
    print("Cấu hình hiện tại: advanced_ctx_payee seed 45. Repo không có cấu hình riêng tên lớp B + E.", flush=True)
    _table("so với bản gốc", original, hidden)
    print(
        f"tổng tiền gốc {original['saved']:,.0f}  né {hidden['saved']:,.0f}  "
        f"MH hợp lệ gốc {100 * original['legit_mh']:.2f}%  né {100 * hidden['legit_mh']:.2f}%",
        flush=True,
    )
    print("Kết quả trên dữ liệu giả lập.", flush=True)


if __name__ == "__main__":
    main()
