"""Replay sổ qua ingest của feature store, /decide HTTP cho mọi phiên test."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(ROOT), str(REPO)]
os.environ.setdefault("FIDES_HMAC_KEY", "fides-demo-hmac")
os.environ.setdefault("FIDES_API_KEY", "fides-demo-key")
os.environ.setdefault("DATABASE_URL", "postgresql://hainamtran@localhost:5432/fides_w1")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from engine.app import Handler
from engine.audit import audit_conn, ensure_audit
from engine.common.hashing import reset_key_cache
from engine.decide import assign_level, gated_families
from engine.features.stream import IdCache, to_us
from engine.model import Scorer
from eval.decision import isotonic_apply, scale_for, shift_rate
from eval.model_v21 import _assign_levels, _eligible, _family_counts, _matrix, load_profile, prepare, score_saved
from eval.split import build_split
from features.service import session_families
from scripts.replay import SIM_END, run
from scripts.snapshot_features import configure

OBSERVED = REPO / "fides-sim" / "out" / "observed"
ICT = ZoneInfo("Asia/Ho_Chi_Minh")
SAVED = 3_250_266_440
RECALL = 0.714987714987715
MH = 0.0012247336944980814
MEDIUM_LOAN = 0.22077922077922077
MEDIUM_GUIDED = 0.125


def _iso(us: int) -> str:
    return datetime.fromtimestamp(us / 1_000_000, ICT).isoformat(timespec="microseconds")


def _post(port: int, path: str, payload: dict) -> tuple[int, dict, float]:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": os.environ["FIDES_API_KEY"]},
    )
    last = None
    for _ in range(5):
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode())
                status = response.status
            return status, body, time.perf_counter() - started
        except urllib.error.URLError as exc:
            last = exc
            time.sleep(0.05)
    raise last


def _official_test_levels(frames, bundle) -> dict[str, str]:
    model, policy, categories = load_profile("advanced_ctx_payee")
    columns = policy["columns"]
    calibrator = policy["calibrator"]

    def apply(raw):
        fitted = isotonic_apply(raw, np.asarray(calibrator["edges"]), np.asarray(calibrator["values"]))
        return shift_rate(fitted, calibrator["source"], calibrator["target"])

    raw_oof = np.load(REPO / "engine" / "artifacts" / "model-v2.1" / "advanced_ctx_payee" / "oof.npy")
    mapping = dict(zip(frames["train"]["session_id"].astype(str), apply(raw_oof)))
    mapping.update(zip(frames["test"]["session_id"].astype(str), apply(model.predict(_matrix(frames["test"], columns, categories)))))
    mapping.update(zip(frames["buffer"]["session_id"].astype(str), apply(model.predict(_matrix(frames["buffer"], columns, categories)))))
    population = bundle["population"]
    probability = population["session_id"].map(mapping).to_numpy(dtype=float)
    amount = population["amount"].to_numpy(dtype=float)
    scale = scale_for(population["segment"], bundle["fp_table"])
    legit_train = population["session_id"].isin(bundle["train_ids"]).to_numpy() & ~population["is_fraud"].to_numpy(dtype=bool)
    families, _, _ = _family_counts(population, legit_train, True, "strict")
    eligible, _, _ = _eligible(population, legit_train)
    level = _assign_levels(probability, amount, families, scale, 0.8125, eligible)
    on_test = population["session_id"].isin(bundle["test_ids"]).to_numpy()
    return dict(zip(population.loc[on_test, "session_id"].astype(str), level[on_test]))


def test_decide_http_matches_seed45():
    reset_key_cache()
    import redis

    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    client.ping()
    configure(type("S", (), {"redis": client})())
    with audit_conn() as conn:
        conn.execute("DROP TABLE IF EXISTS responses")
        conn.execute("DROP TABLE IF EXISTS decisions")
        conn.commit()
        ensure_audit(conn)
    test_ids = set(build_split()["test"]["session_id"].astype(str))
    if os.environ.get("FIDES_SKIP_REPLAY") == "1":
        from engine.features.online import OnlineStore

        stats = {"store": OnlineStore(flush=False)}
        results = None
        print("reuse redis", len(test_ids), flush=True)
    else:
        print("replay", len(test_ids), flush=True)
        results, stats = run(OBSERVED, test_ids, SIM_END, os.environ["REDIS_URL"])
        print(f"replay sessions {len(results)} bytes {stats['redis_bytes']}", flush=True)
        families = session_families(OBSERVED).set_index("session_id")
        family_bad = []
        for session_id, row in results.items():
            expected = families.loc[session_id]
            got = (int(row["call_family"]), int(row["entry_family"]), int(row["payee_family"]), int(row["funding_family"]))
            want = (int(expected["call"]), int(expected["entry"]), int(expected["payee"]), int(expected["funding"]))
            if got != want:
                family_bad.append((session_id, got, want))
                if len(family_bad) >= 8:
                    break
        print("family mismatches", len(family_bad), family_bad[:5], flush=True)
        assert family_bad == []
    import engine.app as appmod

    appmod.STORE = stats["store"]
    appmod.MODEL = Scorer.load()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    official_box: dict = {}

    def _official() -> None:
        try:
            frames, bundle = prepare()
            official_box["metrics"] = score_saved("advanced_ctx_payee", frames, bundle)
            official_box["levels"] = _official_test_levels(frames, bundle)
        except Exception as exc:
            official_box["error"] = exc

    official_thread = threading.Thread(target=_official, daemon=True)
    official_thread.start()
    sessions = pd.read_parquet(OBSERVED / "sessions.parquet", columns=["session_id", "account_id", "end_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    sessions = sessions.loc[sessions["session_id"].isin(test_ids)]
    cache = IdCache(OBSERVED)
    end_us = to_us(sessions["end_ts"])
    jobs = []
    for index, row in enumerate(sessions.itertuples(index=False)):
        jobs.append(
            {
                "decision_id": row.session_id,
                "profile": "advanced",
                "sender_hash": cache.get(str(row.account_id)),
                "payee_hash": "0" * 64,
                "amount": 1,
                "ts": _iso(int(end_us[index]) + 1),
                "session_id": row.session_id,
                "device": {},
            }
        )

    def one(body):
        status, payload, elapsed = _post(port, "/decide", body)
        return body["decision_id"], status, payload, elapsed

    contract_bad = []
    times = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        done = 0
        for session_id, status, payload, elapsed in pool.map(one, jobs):
            assert status == 200, (session_id, status, payload)
            card = payload.get("evidence_card") or {}
            if payload.get("level") not in (0, 1, 2, 3) or not isinstance(payload.get("rules_fired"), list):
                contract_bad.append(session_id)
            if payload.get("intervention") not in {"none", "banner", "three_button", "security_workflow"}:
                contract_bad.append(session_id)
            if "signals" not in card or "reasons_vi" not in card:
                contract_bad.append(session_id)
            times.append(elapsed)
            done += 1
            if done % 5000 == 0:
                print(f"  decide {done}", flush=True)
    assert contract_bad == [], contract_bad[:5]
    print("official", flush=True)
    official_thread.join()
    if "error" in official_box:
        raise official_box["error"]
    metrics = official_box["metrics"]
    official = official_box["levels"]
    # /decide now returns the v2.4 integer. Parity with the packaged model stays on assign_level.
    online = {}
    if results is not None:
        for session_id, row in results.items():
            families_hit = gated_families(row)
            amount = 0.0 if row.get("amount") is None else float(row["amount"])
            online[session_id] = assign_level(appmod.MODEL.score(row), amount, families_hit, str(row.get("segment") or "thường"), row)
        miss = [key for key in online if online[key] != official[key]]
        print("assign_level vs official", len(miss), miss[:8], flush=True)
        assert miss == []
    else:
        print("skip v2.1 level parity: replay rows absent and /decide level is now 0-3", flush=True)
    fraud = metrics["fraud_levels"]
    recall = fraud["medium"] + fraud["high"]
    print(
        f"saved {metrics['saved']:.0f} recall {recall:.6%} mh {metrics['legit_mh']:.6%} "
        f"nhà {metrics['medium_guided']:.4%} vay {metrics['medium_loan']:.4%}",
        flush=True,
    )
    assert int(metrics["saved"]) == SAVED
    assert abs(recall - RECALL) < 1e-12
    assert abs(metrics["legit_mh"] - MH) < 1e-12
    assert abs(metrics["medium_loan"] - MEDIUM_LOAN) < 1e-12
    assert abs(metrics["medium_guided"] - MEDIUM_GUIDED) < 1e-12
    p95 = float(np.percentile(times, 95))
    print(f"p95_decide_s {p95:.6f} n {len(times)}", flush=True)
    server.shutdown()
