"""Nạp gói model-v2.0 và chấm một phiên hoặc tra một hành động."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from eval.decision import (
    SEED,
    _long_matrix,
    apply_isolation_forest,
    build_isolation_forest,
    family_counts,
    g3_apply,
    isotonic_apply,
    predict_model,
    shift_rate,
)
from features import compute_features
from features.service import COST_ETA, cost_tau, levels_from_counts, session_families
from features.store import LONG_TERM, FeatureStore, _as_ns, _ns, using_store

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"
PACK = ROOT / "out" / "model-v2.0"
RESPONSE_KEYS = (
    "scenario_group",
    "model_segment",
    "action",
    "guardian_notified",
    "coached",
    "lives_alone",
    "hangs_up_before_transfer",
)


class UnknownSession(KeyError):
    pass


class BadRequest(ValueError):
    pass


def _bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "có"}
    return bool(value)


def action_for_level(level: str, segment: str, has_guardian: bool) -> str:
    if level == "high":
        return "hangup_required"
    if level == "medium" and segment == "cao tuổi" and has_guardian:
        return "guardian_confirm"
    if level == "medium":
        return "pause_and_verify"
    if level == "banner":
        return "g1_banner"
    return "allow"


class Runtime:
    def __init__(self, policy: dict, model, categories, g3_bundle: tuple, families: pd.DataFrame, store: FeatureStore):
        self.policy = policy
        self.model = model
        self.categories = categories
        self.g3_bundle = g3_bundle
        self.families = families
        self.store = store
        self.columns = list(policy["columns"])
        sessions = pd.read_parquet(
            SIM / "out" / "observed" / "sessions.parquet",
            columns=["session_id", "customer_id", "account_id", "start_ts", "end_ts"],
        )
        sessions["session_id"] = sessions["session_id"].astype(str)
        sessions["customer_id"] = sessions["customer_id"].astype(str)
        sessions["account_id"] = sessions["account_id"].astype(str)
        self.end_of = dict(zip(sessions["session_id"], sessions["end_ts"]))
        self.customer_of = dict(zip(sessions["session_id"], sessions["customer_id"]))
        self.by_account: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for account, part in sessions.groupby("account_id", sort=False):
            order = np.argsort(_ns(part["start_ts"]), kind="mergesort")
            part = part.iloc[order]
            self.by_account[str(account)] = (
                _ns(part["start_ts"]),
                _ns(part["end_ts"]) + 1_000,
                part["session_id"].to_numpy(),
            )
        reference, cusum, fill = g3_bundle
        self.g3_cusum_sorted = np.sort(np.asarray(cusum, dtype=float))
        self.g3_fill = fill
        self.g3_forest, self.g3_norm = build_isolation_forest(np.asarray(reference, dtype=float), seed=SEED)
        self.family_rows = {
            str(row.session_id): (bool(row.call), bool(row.entry), bool(row.payee), bool(row.funding))
            for row in families.itertuples(index=False)
        }
        customers = pd.read_parquet(
            SIM / "out" / "observed" / "customers.parquet",
            columns=["customer_id", "segment", "has_guardian"],
        )
        customers["customer_id"] = customers["customer_id"].astype(str)
        self.segment_of = dict(zip(customers["customer_id"], customers["segment"].astype(str)))
        self.guardian_of = dict(zip(customers["customer_id"], customers["has_guardian"].fillna(False).astype(bool)))
        response = pd.read_parquet(SIM / "out" / "truth" / "response_model.parquet")
        response = response.rename(columns={"segment": "model_segment"})
        self.responses = {}
        for row in response.itertuples(index=False):
            key = tuple(getattr(row, name) if name not in {"guardian_notified", "coached", "lives_alone", "hangs_up_before_transfer"} else bool(getattr(row, name)) for name in RESPONSE_KEYS)
            self.responses[key] = row
        scenarios = pd.read_parquet(
            SIM / "out" / "truth" / "truth_scenarios.parquet",
            columns=["session_id", "scenario_type", "hangs_up_before_transfer"],
        )
        scenarios["session_id"] = scenarios["session_id"].astype(str)
        self.scenario_of = dict(zip(scenarios["session_id"], scenarios["scenario_type"].astype(str)))
        self.hang_of = dict(zip(scenarios["session_id"], scenarios["hangs_up_before_transfer"].fillna(False).astype(bool)))
        latent = pd.read_parquet(SIM / "out" / "truth" / "customers_latent.parquet", columns=["customer_id", "lives_alone"])
        latent["customer_id"] = latent["customer_id"].astype(str)
        self.alone_of = dict(zip(latent["customer_id"], latent["lives_alone"].fillna(False).astype(bool)))
        fraud_groups = {
            "police",
            "bank_staff",
            "tax_authority",
            "relative",
            "deposit",
            "investment",
            "romance",
            "own_name_safe_account",
        }
        self.fraud_groups = fraud_groups

    @classmethod
    def load(cls) -> "Runtime":
        policy = json.loads((PACK / "policy.json").read_text(encoding="utf-8"))
        model = lgb.Booster(model_file=str(PACK / "model.txt"))
        categories = pd.api.types.CategoricalDtype(policy["categories"])
        arrays = np.load(PACK / "g3_reference.npz")
        fill = pd.Series({name: policy["g3_fill"].get(name) for name in LONG_TERM})
        bundle = (arrays["reference"], arrays["cusum"], fill)
        store = FeatureStore(SIM / "out" / "observed")
        contacts_path = SIM / "out" / "truth" / "contacts.parquet"
        if contacts_path.exists():
            contacts = pd.read_parquet(contacts_path, columns=["customer_id", "number_id", "first_contact"])
            store.attach_contacts(contacts)
        families = session_families(SIM / "out" / "observed")
        families["session_id"] = families["session_id"].astype(str)
        return cls(policy, model, categories, bundle, families, store)

    def _calibrate(self, raw: float) -> float:
        spec = self.policy["calibrator"]
        fitted = isotonic_apply(np.array([raw]), np.array(spec["edges"]), np.array(spec["values"]))
        return float(shift_rate(fitted, spec["source"], spec["target"])[0])

    def find_session(self, account: str, ts) -> str | None:
        bucket = self.by_account.get(str(account))
        if bucket is None:
            return None
        starts, ends, ids = bucket
        as_of = _as_ns(ts)
        cursor = int(np.searchsorted(starts, as_of, side="right")) - 1
        for _ in range(8):
            if cursor < 0:
                break
            if int(starts[cursor]) <= as_of <= int(ends[cursor]):
                return str(ids[cursor])
            cursor -= 1
        return None

    def score_session(self, session_id: str, as_of, profile: str) -> dict:
        if session_id not in self.store.session_of:
            raise UnknownSession(session_id)
        if profile not in {"basic", "standard", "advanced"}:
            raise BadRequest("profile phải là basic, standard hoặc advanced")
        with using_store(self.store):
            features = compute_features(session_id, as_of, "single_bank", profile)
        behavior = self.store.behavior_at(session_id, as_of, "single_bank")
        frame = pd.DataFrame([{name: features.get(name) for name in self.columns}])
        frame["session_id"] = session_id
        for name in LONG_TERM:
            frame[name] = behavior.get(name)
        matrix = frame[self.columns].copy()
        matrix["add_method"] = matrix["add_method"].astype("string").astype(self.categories)
        for name in self.columns:
            if name != "add_method":
                matrix[name] = pd.to_numeric(matrix[name], errors="coerce")
        raw = float(self.model.predict(matrix)[0])
        probability = self._calibrate(raw)
        query, query_cusum = _long_matrix(frame, self.g3_fill)
        forest = apply_isolation_forest(self.g3_forest, self.g3_norm, query)
        cdf = np.searchsorted(self.g3_cusum_sorted, query_cusum, side="right") / max(len(self.g3_cusum_sorted), 1)
        g3 = float(0.5 * (forest + cdf)[0])
        call, entry, payee, funding = self.family_rows.get(session_id, (False, False, False, False))
        anomaly = g3 >= float(self.policy["g3_cutoff"])
        count = int(call) + int(entry) + int(payee) + int(funding) + int(anomaly)
        amount = features.get("amount")
        amount = 0.0 if amount is None else float(amount)
        customer = self.customer_of.get(session_id, "")
        segment = self.segment_of.get(customer, "")
        scale = float(self.policy["c_fp_scale"].get(segment, 1.0))
        level = str(
            levels_from_counts(
                np.array([probability]),
                np.array([amount]),
                np.array([count]),
                COST_ETA,
                np.array([scale]),
                float(self.policy["tau_hi"]),
            )[0]
        )
        contrib = self.model.predict(matrix, pred_contrib=True)[0]
        guardian = bool(self.guardian_of.get(customer, False))
        vector = {}
        for name in self.columns:
            value = matrix.iloc[0][name]
            if name == "add_method":
                vector[name] = None if pd.isna(value) else str(value)
            else:
                vector[name] = None if pd.isna(value) else float(value)
        return {
            "session_id": session_id,
            "customer_id": customer,
            "p_raw": raw,
            "p_fraud": probability,
            "amount": amount,
            "segment": segment,
            "has_guardian": guardian,
            "level": level,
            "g3": g3,
            "anomaly": anomaly,
            "call": call,
            "entry": entry,
            "payee": payee,
            "funding": funding,
            "contrib": contrib,
            "feature_vector": vector,
            "missing_signals": list(features.get("missing_signals") or []),
            "profile": profile,
        }

    def decide(self, session_id: str, as_of=None) -> dict:
        session_id = str(session_id)
        if session_id not in self.store.session_of:
            raise UnknownSession(session_id)
        if as_of is None:
            as_of = pd.Timestamp(self.end_of[session_id]) + pd.Timedelta(microseconds=1)
        with using_store(self.store):
            features = compute_features(session_id, as_of, "single_bank")
        behavior = self.store.behavior_at(session_id, as_of, "single_bank")
        frame = pd.DataFrame([{name: features.get(name) for name in self.columns}])
        frame["session_id"] = session_id
        for name in LONG_TERM:
            frame[name] = behavior.get(name)
        raw = float(predict_model(self.model, self.categories, frame, self.columns)[0])
        probability = self._calibrate(raw)
        g3 = float(g3_apply(self.g3_bundle, frame)[0])
        anomaly = np.array([g3 >= float(self.policy["g3_cutoff"])])
        count = int(family_counts(frame, self.families, anomaly)[0])
        amount = features.get("amount")
        amount = 0.0 if amount is None else float(amount)
        customer = self.customer_of.get(session_id, "")
        segment = self.segment_of.get(customer, "")
        scale = float(self.policy["c_fp_scale"].get(segment, 1.0))
        level = str(
            levels_from_counts(
                np.array([probability]),
                np.array([amount]),
                np.array([count]),
                COST_ETA,
                np.array([scale]),
                float(self.policy["tau_hi"]),
            )[0]
        )
        guardian = bool(self.guardian_of.get(customer, False))
        indexed = self.families.set_index("session_id")
        present = session_id in indexed.index
        return {
            "model": self.policy["tag"],
            "contract": self.policy["contract"],
            "session_id": session_id,
            "p_raw": raw,
            "p_cal": probability,
            "amount": int(round(amount)),
            "tau": float(cost_tau(np.array([amount]), COST_ETA, scale)[0]),
            "tau_hi": float(self.policy["tau_hi"]),
            "fp_scale": scale,
            "segment": segment,
            "families": {
                "call": bool(indexed.at[session_id, "call"]) if present else False,
                "entry": bool(indexed.at[session_id, "entry"]) if present else False,
                "payee": bool(indexed.at[session_id, "payee"]) if present else False,
                "funding": bool(indexed.at[session_id, "funding"]) if present else False,
                "anomaly": bool(anomaly[0]),
                "count": count,
            },
            "level": level,
            "action": action_for_level(level, segment, guardian),
            "g3": g3,
        }

    def respond(self, body: dict) -> dict:
        filled = dict(body)
        session_id = str(filled["session_id"]) if filled.get("session_id") else ""
        if session_id:
            if session_id not in self.customer_of and "action" not in filled:
                raise UnknownSession(session_id)
            customer = self.customer_of.get(session_id, "")
            filled.setdefault("segment", self.segment_of.get(customer, "thường"))
            filled.setdefault("lives_alone", self.alone_of.get(customer, False))
            filled.setdefault("hangs_up_before_transfer", self.hang_of.get(session_id, False))
            scenario = self.scenario_of.get(session_id, "")
            if scenario == "legit_guided_by_relative":
                filled.setdefault("scenario_group", "legit_guided_by_relative")
            elif scenario in self.fraud_groups:
                filled.setdefault("scenario_group", scenario)
            else:
                filled.setdefault("scenario_group", "legit")
            if "action" not in filled:
                filled["action"] = self.decide(session_id, filled.get("as_of"))["action"]
        missing = [name for name in ("action", "scenario_group", "segment") if name not in filled]
        if missing:
            raise BadRequest("thiếu " + ", ".join(missing))
        segment = str(filled["segment"])
        model_segment = "cao tuổi" if segment == "cao tuổi" else "thường"
        key = (
            str(filled["scenario_group"]),
            model_segment,
            str(filled["action"]),
            _bool(filled.get("guardian_notified"), False),
            _bool(filled.get("coached"), str(filled["scenario_group"]) in {"police", "bank_staff", "tax_authority", "own_name_safe_account"}),
            _bool(filled.get("lives_alone"), False),
            _bool(filled.get("hangs_up_before_transfer"), False),
        )
        found = self.responses.get(key)
        if found is None:
            raise BadRequest("không có hàng phản ứng cho khóa đã gửi")
        return {
            "model": self.policy["tag"],
            "contract": self.policy["contract"],
            "action": key[2],
            "scenario_group": key[0],
            "segment": segment,
            "model_segment": model_segment,
            "guardian_notified": key[3],
            "coached": key[4],
            "lives_alone": key[5],
            "hangs_up_before_transfer": key[6],
            "p_stop": float(found.p_stop),
            "p_abandon": float(found.p_abandon),
            "p_complaint": float(found.p_complaint),
            "source": str(found.source),
        }
