"""SIMO giả. Listing trong kỳ, mule cũ, và 5% liệt kê nhầm theo config."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from engine.common.hashing import account_hash, require_key

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = ROOT / "fides-sim" / "out" / "observed"
TRUTH = ROOT / "fides-sim" / "out" / "truth"
CONFIG = ROOT / "fides-sim" / "config.yaml"

LISTED: dict[str, dict] = {}


class Report(BaseModel):
    account_hash: str
    source: str = "victim_report"
    listed_at: str | None = None


def _load() -> None:
    spec = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    rate = float(spec["simo"]["false_list_rate"])
    seed = int(spec["simo"]["false_list_seed"])
    start = pd.Timestamp(spec["sim_start"], tz="Asia/Ho_Chi_Minh")
    end = pd.Timestamp(spec["sim_end"], tz="Asia/Ho_Chi_Minh")
    simo = pd.read_parquet(OBSERVED / "simo_list.parquet")
    simo["account_id"] = simo["account_id"].astype(str)
    simo["listed_at"] = pd.to_datetime(simo["listed_at"])
    truth = pd.read_parquet(TRUTH / "accounts_truth.parquet", columns=["account_id", "is_mule"])
    truth["account_id"] = truth["account_id"].astype(str)
    mules = set(truth.loc[truth["is_mule"].fillna(False), "account_id"])
    in_period = simo.loc[(simo["listed_at"] >= start) & (simo["listed_at"] < end)]
    old = simo.loc[(simo["listed_at"] < start) & simo["account_id"].isin(mules)]
    for frame, kind in ((in_period, "trong_ky"), (old, "mule_cu")):
        for row in frame.itertuples(index=False):
            hashed = account_hash(row.account_id)
            slot = LISTED.setdefault(hashed, {"account_hash": hashed, "sources": [], "false_positive": False, "kind": kind})
            slot["sources"].append({"source": str(row.source), "listed_at": pd.Timestamp(row.listed_at).isoformat()})
    accounts = pd.read_parquet(OBSERVED / "accounts.parquet", columns=["account_id"])
    already = set(simo["account_id"])
    pool = [item for item in accounts["account_id"].astype(str) if item not in mules and item not in already]
    take = int(rate * len(pool))
    rng = np.random.default_rng(seed)
    for raw in rng.choice(pool, size=take, replace=False):
        hashed = account_hash(str(raw))
        LISTED[hashed] = {
            "account_hash": hashed,
            "sources": [{"source": "false_list", "listed_at": start.isoformat()}],
            "false_positive": True,
            "kind": "liet_ke_nham",
        }


def _post_engine(body: dict) -> None:
    url = os.environ.get("ENGINE_URL", "http://127.0.0.1:8091").rstrip("/") + "/events"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": os.environ.get("FIDES_API_KEY", "")},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


app = FastAPI(title="SIMO mock")


@app.on_event("startup")
def startup() -> None:
    require_key()
    if not LISTED:
        _load()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "simo-mock", "listed": len(LISTED)}


@app.get("/simo/lookup")
def lookup(account_hash: str) -> dict:
    row = LISTED.get(account_hash)
    if row is None:
        return {"account_hash": account_hash, "listed": False, "sources": [], "false_positive": False}
    return {"account_hash": account_hash, "listed": True, "sources": row["sources"], "false_positive": row["false_positive"], "kind": row["kind"]}


@app.post("/simo/report")
def report(body: Report) -> dict:
    if len(body.account_hash) != 64:
        raise HTTPException(400, "account_hash phải là HMAC 64 ký tự hex")
    listed_at = body.listed_at or "2026-09-20T00:00:00+07:00"
    slot = LISTED.setdefault(
        body.account_hash,
        {"account_hash": body.account_hash, "sources": [], "false_positive": False, "kind": "report"},
    )
    slot["sources"].append({"source": body.source, "listed_at": listed_at})
    event_id = f"simo:report:{body.account_hash[:16]}:{len(slot['sources'])}"
    _post_engine(
        {
            "event_id": event_id,
            "type": "simo_listing",
            "ts": listed_at,
            "account_hash": body.account_hash,
            "listed_at": listed_at,
            "source": body.source,
        }
    )
    return {"account_hash": body.account_hash, "listed": True, "event_id": event_id}
