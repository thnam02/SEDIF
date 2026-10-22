"""Chia phiên theo thời gian, rồi gỡ khỏi train mọi phiên của vụ đã lọt sang test."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FRAUD_TYPES = ["police", "tax_authority", "bank_staff", "relative", "deposit", "investment", "romance", "own_name_safe_account"]
ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT.parent / "fides-sim"


def sim_start_of(config: Path | None = None) -> pd.Timestamp:
    import yaml

    path = config or (SIM / "config.yaml")
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)["sim_start"]
    return pd.Timestamp(raw, tz="Asia/Ho_Chi_Minh")


def build_split(
    observed: Path | None = None,
    truth: Path | None = None,
    sim_start: pd.Timestamp | None = None,
) -> dict[str, pd.DataFrame]:
    """Train trước ngày 50, nhãn ngân hàng biết ở ngày 60. Test từ ngày 60, nhãn sự thật."""
    observed = Path(observed or SIM / "out" / "observed")
    truth = Path(truth or SIM / "out" / "truth")
    sim_start = sim_start or sim_start_of()
    train_before = sim_start + pd.Timedelta(days=50)
    known_at = sim_start + pd.Timedelta(days=60)

    sessions = pd.read_parquet(observed / "sessions.parquet", columns=["session_id", "customer_id", "start_ts", "end_ts"])
    sessions["session_id"] = sessions["session_id"].astype(str)
    tx = pd.read_parquet(observed / "transactions.parquet", columns=["tx_id", "session_id", "to_account", "amount", "from_account"])
    tx = tx.loc[tx["session_id"].notna(), ["tx_id", "session_id", "to_account", "amount", "from_account"]].copy()
    tx["tx_id"] = tx["tx_id"].astype(str)
    tx["session_id"] = tx["session_id"].astype(str)
    labels = pd.read_parquet(
        truth / "labels.parquet",
        columns=["txn_id", "is_fraud", "label_observed", "label_available_at"],
    )
    labels["txn_id"] = labels["txn_id"].astype(str)
    scenarios = pd.read_parquet(truth / "truth_scenarios.parquet", columns=["session_id", "case_id", "scenario_type", "sample_weight"])
    scenarios["session_id"] = scenarios["session_id"].astype(str)
    scenarios["case_id"] = scenarios["case_id"].astype("string")
    cases = pd.read_parquet(truth / "truth_cases.parquet", columns=["case_id", "scam_type"])
    cases["case_id"] = cases["case_id"].astype(str)
    scam_of = dict(zip(cases["case_id"], cases["scam_type"].astype(str)))

    joined = tx.merge(labels, left_on="tx_id", right_on="txn_id", how="left")
    known = joined["label_observed"].fillna(0).astype(int).eq(1) & joined["label_available_at"].notna() & (
        joined["label_available_at"] <= known_at
    )
    joined["known_positive"] = known
    joined["is_fraud"] = joined["is_fraud"].fillna(False).astype(bool)
    by_session = joined.groupby("session_id", sort=False).agg(known_positive=("known_positive", "any"), is_fraud=("is_fraud", "any"))

    frame = sessions.merge(scenarios, on="session_id", how="left")
    frame = frame.merge(by_session, on="session_id", how="left")
    frame["known_positive"] = frame["known_positive"].fillna(False).astype(bool)
    frame["is_fraud"] = frame["is_fraud"].fillna(False).astype(bool)
    frame["sample_weight"] = frame["sample_weight"].fillna(1.0).astype(float)
    frame["case_id"] = frame["case_id"].astype("string")
    frame["scam_type"] = frame["case_id"].map(scam_of).fillna("legit")
    truth_accounts = pd.read_parquet(truth / "accounts_truth.parquet", columns=["account_id", "mule_type"])
    safe_ids = set(
        truth_accounts.loc[truth_accounts["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str)
    )
    banks = pd.read_parquet(observed / "accounts.parquet", columns=["account_id", "bank_id"])
    b01 = set(banks.loc[banks["bank_id"].astype(str).eq("B01"), "account_id"].astype(str))
    fraud_tx = joined.loc[joined["is_fraud"], ["session_id", "tx_id"]].merge(
        tx[["tx_id", "to_account", "amount", "from_account"]], on="tx_id", how="left"
    )
    fraud_tx["to_account"] = fraud_tx["to_account"].astype(str)
    fraud_tx["from_account"] = fraud_tx["from_account"].astype(str)
    own = set(fraud_tx.loc[fraud_tx["to_account"].isin(safe_ids), "session_id"].astype(str))
    frame.loc[frame["session_id"].isin(own), "scam_type"] = "own_name_safe_account"
    b01_fraud = fraud_tx.loc[fraud_tx["from_account"].isin(b01)]
    frame["fraud_vnd"] = frame["session_id"].map(b01_fraud.groupby("session_id")["amount"].sum()).fillna(0).astype(np.int64)
    start = pd.to_datetime(frame["start_ts"])
    frame["part"] = np.where(start < train_before, "train", np.where(start >= known_at, "test", "buffer"))

    test_cases = set(frame.loc[frame["part"].eq("test") & frame["case_id"].notna(), "case_id"].astype(str))
    test_cases.discard("<NA>")
    test_cases.discard("nan")
    leaked = frame["part"].eq("train") & frame["case_id"].isin(test_cases)
    frame.loc[leaked, "part"] = "held_case"

    frame["label"] = np.where(frame["part"].eq("train"), frame["known_positive"].astype(int), frame["is_fraud"].astype(int))
    columns = ["session_id", "customer_id", "start_ts", "end_ts", "case_id", "scam_type", "label", "is_fraud", "known_positive", "sample_weight", "fraud_vnd", "part"]
    return {name: frame.loc[frame["part"].eq(name), columns].reset_index(drop=True) for name in ("train", "test", "buffer", "held_case")}


def group_folds(groups: np.ndarray, n_splits: int = 5, seed: int = 42):
    """GroupKFold. Cùng một nhóm không đứng ở cả hai phía. Nhóm không được đưa vào mô hình."""
    if n_splits < 2:
        raise ValueError("cần ít nhất 2 fold")
    codes, inverse, counts = np.unique(groups.astype(str), return_inverse=True, return_counts=True)
    rng = np.random.default_rng(seed)
    loads = np.zeros(n_splits, dtype=np.int64)
    fold_of = np.empty(len(codes), dtype=np.int64)
    for item in rng.permutation(len(codes)):
        fold = int(np.argmin(loads))
        fold_of[item] = fold
        loads[fold] += int(counts[item])
    row_fold = fold_of[inverse]
    for fold in range(n_splits):
        test = row_fold == fold
        yield ~test, test


def _label_groups(key: pd.Series, session_id: pd.Series, prefix: str) -> np.ndarray:
    """Nhóm có khóa thì dùng khóa. Thiếu khóa thì mỗi phiên một nhóm."""
    text = key.astype("string").fillna("")
    session = session_id.astype("string").fillna("")
    missing = text.isin(["", "<NA>", "nan", "None"]).to_numpy()
    grouped = np.array([f"{prefix}:{item}" for item in text.astype(str).to_numpy()], dtype=object)
    alone = np.array([f"s:{item}" for item in session.astype(str).to_numpy()], dtype=object)
    return np.where(missing, alone, grouped)


def ring_groups(case_id: pd.Series, session_id: pd.Series, ring_of: dict[str, str]) -> np.ndarray:
    """Đường dây để chia fold. Phiên không thuộc đường dây đứng một mình."""
    case = case_id.astype("string")
    ring = case.map(ring_of)
    return _label_groups(ring, session_id, "r")


def describe(parts: dict[str, pd.DataFrame]) -> str:
    lines = []
    for name in ("train", "test"):
        part = parts[name]
        fraud = int(part["label"].sum())
        cases = part.loc[part["case_id"].notna() & ~part["case_id"].isin(["<NA>", "nan"]), "case_id"].nunique()
        lines.append(f"{name}: {len(part)} phiên, {fraud} phiên lừa đảo theo nhãn của phần này, {cases} vụ")
        for kind in FRAUD_TYPES + ["legit"]:
            chunk = part.loc[part["scam_type"].astype(str).eq(kind)]
            if chunk.empty:
                continue
            case_n = chunk.loc[chunk["case_id"].notna() & ~chunk["case_id"].isin(["<NA>", "nan"]), "case_id"].nunique()
            lines.append(f"  {kind}: {len(chunk)} phiên, {int(chunk['label'].sum())} nhãn dương, {case_n} vụ")
    lost = parts["train"].loc[parts["train"]["is_fraud"] & ~parts["train"]["known_positive"]]
    lines.append(f"Train mất {len(lost)} phiên lừa đảo vì đến ngày 60 vẫn chưa có nhãn.")
    for kind in FRAUD_TYPES:
        count = int(lost["scam_type"].astype(str).eq(kind).sum())
        if count:
            lines.append(f"  {kind}: {count}")
    lines.append(f"Đệm ngày 50–60, không dùng: {len(parts['buffer'])} phiên.")
    lines.append(f"Bỏ khỏi train vì vụ có phiên ở test: {len(parts['held_case'])} phiên.")
    return "\n".join(lines)


def main() -> None:
    parts = build_split()
    print(describe(parts))


if __name__ == "__main__":
    main()
