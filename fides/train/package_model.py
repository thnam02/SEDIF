"""Đóng gói bản đã chốt: không p95, có c_FP doanh nghiệp nhỏ ×3, không T4c."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.policy_ablation import evaluate, fit_scores, prepare

OUT = ROOT / "out" / "model-v2.0"


def main() -> None:
    bundle = prepare()
    columns = list(bundle["base"])
    scores = fit_scores(bundle, columns)
    metrics = evaluate(bundle, scores, use_scale=True)
    OUT.mkdir(parents=True, exist_ok=True)
    scores["model"].save_model(str(OUT / "model.txt"))
    reference, cusum, fill = bundle["g3_bundle"]
    np.savez_compressed(OUT / "g3_reference.npz", reference=np.asarray(reference), cusum=np.asarray(cusum))
    apply = scores["apply"]
    policy = {
        "tag": "model-v2.0",
        "contract": "v2.1",
        "seed": 42,
        "columns": columns,
        "categories": [str(item) for item in scores["categories"].categories],
        "tau_hi": metrics["tau_hi"],
        "g3_cutoff": float(bundle["g3_cutoff"]),
        "g3_fill": {str(key): None if pd.isna(fill[key]) else float(fill[key]) for key in fill.index},
        "eta": 1.0,
        "c_fp_fixed": 50_000.0,
        "c_fp_rate": 0.002,
        "c_fp_scale": bundle["fp_table"],
        "calibrator": {
            "edges": [float(item) for item in apply.edges],
            "values": [float(item) for item in apply.values],
            "source": float(apply.source),
            "target": float(apply.target),
        },
        "p95": False,
        "t4c": False,
        "saved_vnd": metrics["saved"],
        "saved_rate": metrics["rate"],
    }
    (OUT / "policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {OUT}")
    print(f"τ_hi {metrics['tau_hi']:.4f}, tiền tránh được {metrics['saved']:,.0f} VND ({metrics['rate']:.1%})")


if __name__ == "__main__":
    main()
