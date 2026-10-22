"""Chọn persona từ sổ sim seed 42 và ghi docs/personas.json.

Sinh viên là người segment thường, tuổi không quá 25 trong năm 2026, nhiều giao dịch nhất.
Nhân viên văn phòng là segment thường, nhóm tuổi 31-50, có ít nhất ba lần nhận lương.
Chủ doanh nghiệp nhỏ là segment doanh nghiệp nhỏ, tuổi từ 30 đến 55 trong năm 2026.
Cao tuổi có giám hộ và chủ doanh nghiệp nhỏ lấy người gần trung vị số giao dịch.
Ba mule là ba tài khoản khác ring, còn hoạt động, nhiều giao dịch đi nhất trong 22–29/9/2026.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = ROOT / "fides-sim" / "out" / "observed"
TRUTH = ROOT / "fides-sim" / "out" / "truth"
OUT = ROOT / "docs" / "personas.json"

SIM_START = "2026-07-01"
SIM_END = "2026-09-29"
TEST_START = "2026-08-30"
MULE_RECENT_START = "2026-09-22"
TZ = "Asia/Ho_Chi_Minh"


def stamp(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz=TZ)


def customer_record(row: pd.Series, accounts: list[str], tx_count: int, extra: dict) -> dict:
    return {
        "customer_id": str(row.customer_id),
        "full_name": str(row.full_name),
        "gender": str(row.gender),
        "birth_year": int(row.birth_year),
        "age_group": str(row.age_group),
        "segment": str(row.segment),
        "city": str(row.city),
        "has_guardian": bool(row.has_guardian),
        "account_ids": accounts,
        "tx_count": int(tx_count),
        **extra,
    }


def nearest_median(frame: pd.DataFrame, counts: pd.Series) -> pd.Series:
    scored = frame.copy()
    scored["tx_count"] = scored["customer_id"].astype(str).map(counts).fillna(0).astype(int)
    scored = scored[scored["tx_count"] > 0]
    median = float(scored["tx_count"].median())
    scored["distance"] = (scored["tx_count"] - median).abs()
    scored = scored.sort_values(["distance", "customer_id"])
    return scored.iloc[0]


def main() -> None:
    customers = pd.read_parquet(
        OBSERVED / "customers.parquet",
        columns=["customer_id", "full_name", "gender", "birth_year", "age_group", "segment", "city", "has_guardian"],
    )
    customers["customer_id"] = customers["customer_id"].astype(str)
    accounts = pd.read_parquet(OBSERVED / "accounts.parquet", columns=["account_id", "customer_id", "bank_id"])
    accounts["account_id"] = accounts["account_id"].astype(str)
    accounts["customer_id"] = accounts["customer_id"].astype(str)
    holders = accounts.drop_duplicates("account_id").set_index("account_id")["customer_id"]

    tx = pd.read_parquet(
        OBSERVED / "transactions.parquet",
        columns=["ts", "from_account", "to_account", "tx_type", "amount"],
    )
    tx["from_account"] = tx["from_account"].astype(str)
    tx["to_account"] = tx["to_account"].astype(str)
    outgoing = tx["from_account"].map(holders).value_counts()
    incoming = tx["to_account"].map(holders).value_counts()
    tx_count = outgoing.add(incoming, fill_value=0).astype(int)

    salary = tx.loc[tx["tx_type"].astype(str).eq("salary_in"), "to_account"].map(holders)
    salary_count = salary.value_counts()

    rings = pd.read_parquet(
        TRUTH / "mule_rings.parquet",
        columns=["ring_id", "account_id", "layer", "active_from", "active_to"],
    )
    rings["account_id"] = rings["account_id"].astype(str)
    rings["ring_id"] = rings["ring_id"].astype(str)
    mule_holders = set(holders.reindex(rings["account_id"]).dropna().astype(str))

    people = customers[~customers["customer_id"].isin(mule_holders)].copy()
    by_accounts = accounts.groupby("customer_id")["account_id"].agg(lambda values: sorted(set(values))).to_dict()

    elderly_pool = people[(people["segment"].astype(str) == "cao tuổi") & people["has_guardian"].astype(bool)]
    elderly = nearest_median(elderly_pool, tx_count)

    office_pool = people[(people["segment"].astype(str) == "thường") & (people["age_group"].astype(str) == "31-50")]
    office_ids = set(salary_count[salary_count >= 3].index.astype(str))
    office_pool = office_pool[office_pool["customer_id"].isin(office_ids)]
    office = nearest_median(office_pool, tx_count)

    age = 2026 - people["birth_year"].astype(int)
    business_pool = people[(people["segment"].astype(str) == "doanh nghiệp nhỏ") & (age >= 30) & (age <= 55)]
    business = nearest_median(business_pool, tx_count)
    student_pool = people[(people["segment"].astype(str) == "thường") & (age <= 25)]
    student_pool = student_pool.copy()
    student_pool["tx_count"] = student_pool["customer_id"].map(tx_count).fillna(0).astype(int)
    student = student_pool.sort_values(["tx_count", "customer_id"], ascending=[False, True]).iloc[0]

    guardians = pd.read_parquet(
        OBSERVED / "guardians.parquet",
        columns=["customer_id", "guardian_customer_id", "guardian_phone_number_id", "relation"],
    )
    guardians["customer_id"] = guardians["customer_id"].astype(str)
    guardian_row = guardians[guardians["customer_id"] == elderly.customer_id]
    guardian = None
    if len(guardian_row):
        one = guardian_row.iloc[0]
        guardian = {
            "guardian_customer_id": None if pd.isna(one.guardian_customer_id) else str(one.guardian_customer_id),
            "guardian_phone_number_id": str(one.guardian_phone_number_id),
            "relation": str(one.relation),
        }

    chosen = {str(elderly.customer_id), str(office.customer_id), str(business.customer_id), str(student.customer_id)}
    if len(chosen) != 4:
        raise SystemExit("bốn persona không tách nhau")

    recent_start = stamp(MULE_RECENT_START)
    recent_end = stamp(SIM_END)
    recent = tx[(tx["ts"] >= recent_start) & (tx["ts"] < recent_end) & tx["from_account"].isin(set(rings["account_id"]))]
    recent_count = recent.groupby("from_account").size()
    last_ts = recent.groupby("from_account")["ts"].max()
    mule_rows = rings[rings["account_id"].isin(recent_count.index)].copy()
    mule_rows["recent_tx"] = mule_rows["account_id"].map(recent_count).astype(int)
    mule_rows["last_ts"] = mule_rows["account_id"].map(last_ts)
    mule_rows = mule_rows[mule_rows["active_to"] >= recent_start]
    mule_rows = mule_rows.sort_values(["recent_tx", "last_ts", "ring_id", "account_id"], ascending=[False, False, True, True])
    picked = []
    seen_rings: set[str] = set()
    for row in mule_rows.itertuples(index=False):
        if row.ring_id in seen_rings:
            continue
        seen_rings.add(row.ring_id)
        holder = holders.get(row.account_id)
        picked.append(
            {
                "account_id": row.account_id,
                "ring_id": row.ring_id,
                "layer": str(row.layer),
                "customer_id": None if holder is None or (isinstance(holder, float)) else str(holder),
                "bank_id": str(accounts.loc[accounts["account_id"] == row.account_id, "bank_id"].iloc[0]),
                "active_from": pd.Timestamp(row.active_from).isoformat(),
                "active_to": pd.Timestamp(row.active_to).isoformat(),
                "tx_count_recent": int(row.recent_tx),
                "last_tx_ts": pd.Timestamp(row.last_ts).isoformat(),
            }
        )
        if len(picked) == 3:
            break
    if len(picked) != 3:
        raise SystemExit(f"chỉ tìm được {len(picked)} mule cuối giai đoạn test")

    payload = {
        "seed": 42,
        "sim_window": {"start": SIM_START, "end_exclusive": SIM_END},
        "test_window": {"start": TEST_START, "end_exclusive": SIM_END},
        "mule_recent_window": {"start": MULE_RECENT_START, "end_exclusive": SIM_END},
        "demo_clock": {
            "start": "2026-09-20T00:00:00+07:00",
            "end_exclusive": SIM_END + "T00:00:00+07:00",
            "speed": 60,
            "note": "Đồng hồ demo bắt đầu 20/9/2026 và chạy nhanh gấp 60, phủ 9 ngày cuối đến trước 29/9/2026.",
        },
        "rules": {
            "cao_tuoi_co_giam_ho": "segment cao tuổi, has_guardian, không giữ tài khoản mule, số giao dịch gần trung vị",
            "nhan_vien_van_phong": "segment thường, tuổi 31-50, ít nhất 3 lần salary_in, số giao dịch gần trung vị",
            "chu_dn_nho": "segment doanh nghiệp nhỏ, tuổi 30–55 trong năm 2026, không giữ tài khoản mule, số giao dịch gần trung vị",
            "sinh_vien": "segment thường, tuổi không quá 25 trong năm 2026, nhiều giao dịch nhất",
            "mule": "ba tài khoản khác ring, active_to còn tới 22/9/2026, nhiều giao dịch đi nhất từ 22/9 đến trước 29/9/2026",
        },
        "personas": [
            customer_record(
                elderly,
                by_accounts.get(str(elderly.customer_id), []),
                int(elderly.tx_count),
                {"role": "cao_tuoi_co_giam_ho", "guardian": guardian, "salary_in_count": int(salary_count.get(elderly.customer_id, 0))},
            ),
            customer_record(
                office,
                by_accounts.get(str(office.customer_id), []),
                int(office.tx_count),
                {"role": "nhan_vien_van_phong", "salary_in_count": int(salary_count.get(office.customer_id, 0))},
            ),
            customer_record(
                business,
                by_accounts.get(str(business.customer_id), []),
                int(business.tx_count),
                {"role": "chu_dn_nho", "salary_in_count": int(salary_count.get(business.customer_id, 0))},
            ),
            customer_record(
                student,
                by_accounts.get(str(student.customer_id), []),
                int(student.tx_count),
                {"role": "sinh_vien", "salary_in_count": int(salary_count.get(student.customer_id, 0))},
            ),
        ],
        "mules": picked,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ghi {OUT}")
    for person in payload["personas"]:
        print(person["role"], person["customer_id"], person["tx_count"])
    for mule in payload["mules"]:
        print("mule", mule["account_id"], mule["ring_id"], mule["tx_count_recent"], mule["last_tx_ts"])


if __name__ == "__main__":
    main()
