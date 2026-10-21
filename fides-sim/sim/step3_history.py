"""Bước 3: danh bạ và lịch sử giao dịch 90 ngày.

Đọc output bước 1–2. Chưa có mule, kẻ gian hay phiên. Cùng seed cho cùng kết quả.
Số dư khách hàng chạy theo thời gian; thiếu tiền thì giảm hoặc bỏ giao dịch.
"""

from __future__ import annotations

import heapq
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.io import write
from sim.limits import format_limit

TZ = "Asia/Ho_Chi_Minh"
SCHOOL_AGE = 22
# Lớp 1, lớp 6, lớp 10 và năm nhất đại học.
ENTRY_AGES = (6, 11, 15, 18)
TX_SIGMA = 0.7
# kind
SALARY, BUSINESS_IN, BILL, RENT, CARD, CASH, TRANSFER, OWN, SCHOOL, LARGE, DRAIN, PASS_IN, PASS_OUT, SAVINGS, LOAN, CASH_ADV = range(16)
KIND_NAME = np.array(
    [
        "salary_in", "business_in", "bill_payment", "rent", "card_payment", "cash_withdrawal",
        "transfer", "own_transfer", "school_fee", "large_oneoff", "own_transfer_drain",
        "passthrough_in", "passthrough_out", "savings_liquidation", "quick_loan_disbursement",
        "credit_card_cash_advance",
    ],
    dtype=object,
)
INBOUND = {SALARY, BUSINESS_IN, PASS_IN, LOAN, CASH_ADV}
# 23h giảm dần tới đáy 3–4h, rồi tăng từ 5h. 0–6h khoảng 5,5% tổng trọng số.
# Bậc tối 20–22h và 0–1h dốc hơn 25% để đáy đêm đủ thấp; 6–8h là bậc sáng.
WEEKDAY_HOUR = np.array(
    [100, 72, 54, 46, 46, 54, 200, 265, 340, 400, 460, 530, 530, 460, 390, 340, 370, 400, 420, 460, 460, 280, 170, 110],
    dtype=float,
)
WEEKEND_HOUR = WEEKDAY_HOUR.copy()
WEEKDAY_HOUR /= WEEKDAY_HOUR.sum()
WEEKEND_HOUR /= WEEKEND_HOUR.sum()
PURPOSE = {
    SALARY: ["luong", "luong thang", "nhan luong", "luong huu", "tro cap"],
    BUSINESS_IN: ["thanh toan", "chuyen tien", "tien hang", "ck", "khach chuyen"],
    BILL: ["tien dien", "tien nuoc", "internet", "cuoc dien thoai", "hoa don"],
    RENT: ["tien nha", "tien nha thang", "thue nha", "chuyen tien nha"],
    CARD: ["an trua", "an toi", "cafe", "mua sam", "grab", "sieu thi", "xang"],
    CASH: ["rut tien", "rut tien mat", "rut atm"],
    TRANSFER: ["chuyen tien", "ck", "chuyen khoan", "gui tien", "tra no"],
    OWN: ["chuyen tien", "chuyen khoan", "gui tiet kiem", "chuyen noi bo"],
    SCHOOL: ["hoc phi", "nop hoc phi", "hoc phi thang", "giu cho", "nhap hoc"],
    LARGE: ["dat coc", "coc nha", "coc xe", "vien phi", "nhap vien"],
    DRAIN: ["chuyen tien", "chuyen khoan", "ck"],
    PASS_IN: ["gop tien", "dong quy", "chuyen tien", "dong tien"],
    PASS_OUT: ["chuyen tien", "chuyen giup", "tra lai", "chuyen cho con"],
    SAVINGS: ["tat toan dao han", "tat toan truoc han"],
    LOAN: ["giai ngan vay nhanh", "vay nhanh"],
    CASH_ADV: ["rut tien the", "ung tien the"],
}


def load_config(path: Path | None = None) -> dict:
    path = path or (ROOT / "config.yaml")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def as_timestamp(value) -> pd.Timestamp:
    stamp = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(TZ)
    return stamp.tz_convert(TZ)


def largest_remainder(keys, weights, total: int) -> dict:
    weights = np.asarray(list(weights), dtype=float)
    weights = weights / weights.sum()
    raw = weights * total
    counts = np.floor(raw).astype(int)
    short = int(total - counts.sum())
    order = np.argsort(-(raw - counts), kind="stable")
    for i in range(short):
        counts[order[i]] += 1
    return {key: int(count) for key, count in zip(keys, counts)}


def ids(prefix: str, n: int, width: int = 8) -> np.ndarray:
    return np.char.add(prefix, np.char.zfill(np.arange(1, n + 1).astype(str), width))


def merchant_category(name: str) -> str:
    if name.startswith("BENH VIEN"):
        return "hospital"
    if name.startswith("TRUONG") or name.startswith("DAI HOC"):
        return "school"
    if name.startswith("CHU DAU TU"):
        return "real_estate"
    if name.startswith("CUA HANG"):
        return "shop"
    return "company"


def sample_hours(day: np.ndarray, weekend: np.ndarray, rng) -> np.ndarray:
    hour = np.empty(len(day), dtype=np.int16)
    is_we = weekend[day]
    n_we = int(is_we.sum())
    n_wd = len(day) - n_we
    if n_we:
        hour[is_we] = rng.choice(24, size=n_we, p=WEEKEND_HOUR)
    if n_wd:
        hour[~is_we] = rng.choice(24, size=n_wd, p=WEEKDAY_HOUR)
    return hour


def unix_seconds(values) -> np.ndarray:
    """Đưa mốc thời gian int về giây Unix, dù nguồn là giây hay nano giây."""
    values = np.asarray(values, dtype=np.int64)
    if len(values) and int(np.max(np.abs(values))) > 10**14:
        return values // 1_000_000_000
    return values


def stamp_from_parts(origin: pd.Timestamp, day, hour, minute, second) -> pd.Series:
    seconds = (
        np.asarray(day, dtype=np.int64) * 86400
        + np.asarray(hour, dtype=np.int64) * 3600
        + np.asarray(minute, dtype=np.int64) * 60
        + np.asarray(second, dtype=np.int64)
    )
    return origin + pd.to_timedelta(seconds, unit="s")


def round_vnd(amount: np.ndarray, do_round: np.ndarray) -> np.ndarray:
    amount = amount.astype(np.int64, copy=True)
    unit = np.where(amount >= 1_000_000, 100_000, 1_000).astype(np.int64)
    rounded = (amount // unit) * unit
    rounded = np.where(rounded <= 0, amount, rounded)
    return np.where(do_round, rounded, amount).astype(np.int64)


def blank_purpose(purpose: np.ndarray, rng, share: float = 0.15) -> np.ndarray:
    drop = rng.random(len(purpose)) < share
    purpose = purpose.copy()
    purpose[drop] = ""
    return purpose


class Events:
    def __init__(self):
        self.parts: list[dict] = []

    def add(self, **columns) -> None:
        if len(columns["kind"]) == 0:
            return
        width = len(columns["kind"])
        if "raise_limit" not in columns:
            columns["raise_limit"] = np.zeros(width, dtype=bool)
        if "note" not in columns:
            columns["note"] = np.full(width, "", dtype=object)
        lengths = {key: len(value) for key, value in columns.items()}
        if len(set(lengths.values())) != 1:
            raise RuntimeError(f"Lệch độ dài mảng giao dịch: {lengths}")
        self.parts.append(columns)

    def frame(self) -> pd.DataFrame:
        keys = self.parts[0].keys()
        data = {key: np.concatenate([part[key] for part in self.parts]) for key in keys}
        return pd.DataFrame(data)


def load_world(cfg):
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    customers = pd.read_parquet(observed / "customers.parquet").sort_values("customer_id").reset_index(drop=True)
    latent = pd.read_parquet(truth / "customers_latent.parquet").set_index("customer_id").loc[customers["customer_id"]].reset_index()
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet").reset_index(drop=True)
    ref_year = int(as_timestamp(cfg["sim_start"]).year)
    customers["cidx"] = np.arange(len(customers), dtype=np.int32)
    cidx = dict(zip(customers["customer_id"].astype(str), customers["cidx"].to_numpy()))
    age = (ref_year - customers["birth_year"].to_numpy()).astype(np.int16)
    accounts["row"] = np.arange(len(accounts), dtype=np.int32)
    accounts["cidx"] = accounts["customer_id"].map(cidx).fillna(-1).astype(np.int32)
    holder = accounts["holder_name_ascii"].astype(str).to_numpy()
    accounts["category"] = ""
    merchant = accounts["customer_id"].isna() & (accounts["account_type"] == "business") & accounts["phone_number_id"].isna()
    categories = np.array([merchant_category(name) for name in holder], dtype=object)
    company = merchant.to_numpy() & (categories == "company")
    # Một nửa công ty là tiện ích; bước 2 không có danh mục utility.
    company_rows = np.flatnonzero(company)
    categories[company_rows[::2]] = "utility"
    categories[company_rows[1::2]] = "shop"
    accounts["category"] = categories
    return customers, latent, phones, accounts, age


def choose_primary(accounts: pd.DataFrame, n_customers: int):
    owned = accounts.loc[accounts["cidx"] >= 0, ["row", "cidx", "account_type", "bank_id", "opening_balance", "opened_at"]].copy()
    personal = owned.loc[owned["account_type"] == "personal"].sort_values(
        ["cidx", "opening_balance", "row"], ascending=[True, False, True], kind="mergesort"
    )
    primary = np.full(n_customers, -1, dtype=np.int32)
    primary[personal.drop_duplicates("cidx")["cidx"].to_numpy()] = personal.drop_duplicates("cidx")["row"].to_numpy()
    primary_bank = np.empty(n_customers, dtype=object)
    bank_of = accounts["bank_id"].to_numpy()
    primary_bank[primary >= 0] = bank_of[primary[primary >= 0]]

    alt = np.full(n_customers, -1, dtype=np.int32)
    other = owned.loc[owned["row"].to_numpy() != primary[owned["cidx"].to_numpy()]].copy()
    other["same_bank"] = other["bank_id"].to_numpy() == primary_bank[other["cidx"].to_numpy()]
    other["rank"] = np.where(other["account_type"].eq("personal") & ~other["same_bank"], 0, np.where(~other["same_bank"], 1, 2))
    other = other.sort_values(["cidx", "rank", "row"], kind="mergesort").drop_duplicates("cidx")
    alt[other["cidx"].to_numpy()] = other["row"].to_numpy()
    savings = owned.loc[owned["account_type"] == "savings"].drop_duplicates("cidx")
    drain_to = alt.copy()
    drain_to[savings["cidx"].to_numpy()] = savings["row"].to_numpy()
    # Nếu khoản tiết kiệm trùng tài khoản chi thay thế, giữ tài khoản ngân hàng khác cho giao dịch thường.
    return primary, alt, drain_to


def build_contacts(customers, latent, phones, accounts, age, rng, sim_start, sim_end, hist):
    n = len(customers)
    phones_of = customers["phone_number_id"].astype(str).to_numpy()
    family = latent["family_group_id"].astype(str).to_numpy()
    role = latent["role"].astype(str).to_numpy()
    groups: dict[str, list[int]] = {}
    for index, group_id in enumerate(family):
        groups.setdefault(group_id, []).append(index)

    fam_c, fam_num = [], []
    school_households = []
    head_of = {}
    child_of = {}
    for group_id, members in groups.items():
        for person in members:
            if role[person] == "head":
                head_of[group_id] = person
            if role[person] == "child" and int(age[person]) <= SCHOOL_AGE:
                child_of.setdefault(group_id, []).append(person)
        if group_id in child_of and group_id in head_of:
            school_households.append(group_id)
        if len(members) < 2:
            continue
        for person in members:
            for other in members:
                if other != person:
                    fam_c.append(person)
                    fam_num.append(phones_of[other])

    city = customers["city"].astype(str).to_numpy()
    by_city: dict[str, np.ndarray] = {}
    for name in pd.unique(city):
        by_city[name] = np.flatnonzero(city == name).astype(np.int32)
    n_friends = rng.integers(5, 31, size=n)
    friend_c = []
    friend_num = []
    friend_rel = []
    external = phones.loc[phones["owner_type"] == "external_person", "number_id"].astype(str).to_numpy()
    # Bạn bè: ưu tiên cùng thành phố. 25% là số ngoài, 25% số còn lại là đồng nghiệp.
    slot = np.arange(30)
    cust_pick = np.empty((n, 30), dtype=np.int32)
    for name, pool in by_city.items():
        rows = by_city[name]
        drawn = rng.choice(pool, size=(len(rows), 30))
        same = drawn == rows[:, None]
        if np.any(same):
            drawn[same] = rng.choice(pool, size=int(same.sum()))
        cust_pick[rows] = drawn
    ext_pick = rng.choice(external, size=(n, 30))
    use_ext = rng.random((n, 30)) < 0.25
    is_work = rng.random((n, 30)) < 0.25
    active = slot[None, :] < n_friends[:, None]
    cust_i, slot_i = np.nonzero(active)
    use = use_ext[cust_i, slot_i]
    friend_c.append(cust_i)
    nums = np.empty(len(cust_i), dtype=object)
    nums[use] = ext_pick[cust_i[use], slot_i[use]]
    nums[~use] = phones_of[cust_pick[cust_i[~use], slot_i[~use]]]
    friend_num.append(nums)
    rel = np.where(is_work[cust_i, slot_i] & ~use, "work", "friend")
    friend_rel.append(rel)

    merchant = accounts.loc[accounts["category"].isin(["utility", "school", "hospital", "shop", "real_estate"])].copy()
    business_numbers = phones.loc[phones["owner_type"] == "business", "number_id"].astype(str).sort_values().to_numpy()
    merchant = merchant.sort_values("row", kind="mergesort")
    if len(business_numbers) < len(merchant):
        raise RuntimeError("Không đủ số business để gán cho merchant")
    merchant_phone = dict(zip(merchant["row"].to_numpy(), business_numbers[: len(merchant)]))
    pools = {name: merchant.loc[merchant["category"] == name, "row"].to_numpy() for name in ("utility", "school", "hospital", "shop", "real_estate")}
    for name, pool in pools.items():
        if len(pool) == 0:
            raise RuntimeError(f"Thiếu merchant danh mục {name}")

    util = pools["utility"][np.arange(n) % len(pools["utility"])]
    shop = pools["shop"][np.arange(n) % len(pools["shop"])]
    ship = pools["shop"][(np.arange(n) + 17) % len(pools["shop"])]
    merch_rows = np.concatenate([util, shop, ship])
    merch_c = np.tile(np.arange(n, dtype=np.int32), 3)
    merch_num = np.array([merchant_phone[int(row)] for row in merch_rows], dtype=object)

    school_hh = np.array(school_households, dtype=object)
    chosen_hh = np.array([], dtype=object)
    if len(school_hh):
        take = int(round(float(hist["school_fee_parent_share"]) * len(school_hh)))
        chosen_hh = rng.choice(school_hh, size=take, replace=False) if take else np.array([], dtype=object)
    holder = accounts["holder_name_ascii"].astype(str).to_numpy()
    school_plan = plan_school_fees(
        chosen_hh, head_of, child_of, age, pools["school"], merchant_phone, holder, hist, rng,
    )

    contacts = pd.DataFrame(
        {
            "cidx": np.concatenate([np.asarray(fam_c, dtype=np.int32), friend_c[0], merch_c]),
            "number_id": np.concatenate([np.asarray(fam_num, dtype=object), friend_num[0], merch_num]),
            "contact_type": np.concatenate([
                np.full(len(fam_c), "family", dtype=object),
                friend_rel[0],
                np.full(len(merch_c), "merchant", dtype=object),
            ]),
        }
    )
    contacts = contacts.loc[contacts["number_id"].to_numpy() != phones_of[contacts["cidx"].to_numpy()]]
    contacts = contacts.drop_duplicates(["cidx", "number_id"], keep="first")
    strength = np.empty(len(contacts), dtype=float)
    low = {"family": 0.75, "friend": 0.35, "work": 0.25, "merchant": 0.45}
    high = {"family": 1.0, "friend": 0.85, "work": 0.70, "merchant": 0.95}
    rel_arr = contacts["contact_type"].to_numpy()
    draw = rng.random(len(contacts))
    for name in low:
        mask = rel_arr == name
        strength[mask] = low[name] + (high[name] - low[name]) * draw[mask]
    old = rng.random(len(contacts)) < 0.90
    first = pd.Series(pd.NaT, index=contacts.index, dtype=f"datetime64[ns, {TZ}]")
    old_start = as_timestamp("2016-01-01")
    old_span = int((sim_start - old_start).total_seconds())
    win_span = int((sim_end - sim_start).total_seconds())
    n_old = int(old.sum())
    n_new = len(contacts) - n_old
    first.iloc[np.flatnonzero(old)] = old_start + pd.to_timedelta(rng.integers(0, old_span, size=n_old), unit="s")
    first.iloc[np.flatnonzero(~old)] = sim_start + pd.to_timedelta(rng.integers(0, win_span, size=n_new), unit="s")
    contacts["strength"] = strength
    contacts["first_contact"] = first.to_numpy()
    contacts["customer_id"] = customers["customer_id"].to_numpy()[contacts["cidx"].to_numpy()]
    if school_plan:
        school_contacts = pd.DataFrame(school_plan)[["cidx", "number_id", "is_new"]].drop_duplicates(["cidx", "number_id"])
        school_contacts["contact_type"] = "merchant"
        school_contacts["strength"] = 0.70
        school_contacts["customer_id"] = customers["customer_id"].to_numpy()[school_contacts["cidx"].to_numpy()]
        old_start = as_timestamp("2023-09-01")
        old_end = as_timestamp("2026-06-01")
        old_span = max(int((old_end - old_start).total_seconds()), 1)
        new_start = as_timestamp("2026-08-01")
        first_school = pd.Series(pd.NaT, index=school_contacts.index, dtype=f"datetime64[ns, {TZ}]")
        is_new = school_contacts["is_new"].to_numpy()
        n_old_school = int((~is_new).sum())
        n_new_school = int(is_new.sum())
        if n_old_school:
            first_school.iloc[np.flatnonzero(~is_new)] = old_start + pd.to_timedelta(rng.integers(0, old_span, size=n_old_school), unit="s")
        if n_new_school:
            first_school.iloc[np.flatnonzero(is_new)] = new_start + pd.to_timedelta(rng.integers(0, 10 * 86400, size=n_new_school), unit="s")
        school_contacts["first_contact"] = first_school.to_numpy()
        contacts = pd.concat(
            [contacts, school_contacts[["customer_id", "cidx", "number_id", "contact_type", "strength", "first_contact"]]],
            ignore_index=True,
        )
        contacts = contacts.drop_duplicates(["cidx", "number_id"], keep="first")
    out = contacts[["customer_id", "number_id", "contact_type", "strength", "first_contact"]].reset_index(drop=True)
    out["customer_id"] = out["customer_id"].astype("string")
    out["number_id"] = out["number_id"].astype("string")
    out["contact_type"] = out["contact_type"].astype("string")

    directory = {
        "pools": pools,
        "merchant_phone": merchant_phone,
        "chosen_school_hh": set(chosen_hh.tolist()),
        "head_of": head_of,
        "child_of": child_of,
        "groups": groups,
        "school_plan": school_plan,
    }
    return out, directory


FEE_BAND = {
    "preschool": (2_000_000, 5_000_000),
    "basic": (1_000_000, 8_000_000),
    "high": (2_000_000, 10_000_000),
    "university": (8_000_000, 30_000_000),
}


def school_level(name: str) -> str:
    if name.startswith("DAI HOC"):
        return "university"
    if "THPT" in name:
        return "high"
    if "MAM NON" in name:
        return "preschool"
    return "basic"


def plan_school_fees(chosen_hh, head_of, child_of, age, school_pool, merchant_phone, holder, hist, rng) -> list[dict]:
    """Mỗi con một trường. Người nhận mới chỉ khi vào cấp mới, đổi trường, hoặc em bắt đầu đi học."""
    if len(chosen_hh) == 0:
        return []
    levels = [school_level(holder[int(row)]) for row in school_pool]
    low = np.array([FEE_BAND[level][0] for level in levels], dtype=float)
    high = np.array([FEE_BAND[level][1] for level in levels], dtype=float)
    center = np.sqrt(low * high)
    sigma = np.log(high / low) / 3.0
    base = np.clip(np.rint(center * np.exp(sigma * rng.standard_normal(len(school_pool)))), low, high).astype(np.int64)
    monthly_flag = rng.random(len(school_pool)) < float(hist["school_fee_monthly_share"])
    due_day = rng.integers(1, 21, size=len(school_pool))
    enroll = rng.random(len(school_pool)) < float(hist["school_enrollment_fee_share"])
    enroll_base = np.rint(base * rng.uniform(0.25, 0.55, size=len(school_pool))).astype(np.int64)
    profile = {}
    for index, row in enumerate(school_pool):
        key = int(row)
        profile[key] = {
            "level": levels[index],
            "base": int(base[index]),
            "enroll_base": int(enroll_base[index]),
            "due_day": int(due_day[index]),
            "enrollment": bool(enroll[index]),
            "monthly": bool(monthly_flag[index]) and levels[index] != "university",
        }
    change_p = float(hist["school_change_yearly_share"])
    plan = []
    for group_id in sorted((str(value) for value in chosen_hh)):
        head = head_of.get(group_id)
        children = sorted(child_of.get(group_id, []))
        if head is None or not children:
            continue
        youngest = min(children, key=lambda person: (int(age[person]), person))
        assigned: list[tuple[int, bool]] = []
        for child in children:
            entry = int(age[child]) in ENTRY_AGES
            sibling_start = child == youngest and len(children) >= 2 and entry
            change = (not entry) and (not sibling_start) and (rng.random() < change_p)
            is_new = bool(entry or sibling_start or change)
            blocked = {row for row, new in assigned if new != is_new}
            start = (int(child) * 7 + (11 if is_new else 3)) % len(school_pool)
            school_row = int(school_pool[start])
            for step in range(len(school_pool)):
                candidate = int(school_pool[(start + step) % len(school_pool)])
                if candidate not in blocked:
                    school_row = candidate
                    break
            assigned.append((school_row, is_new))
            info = profile[school_row]
            plan.append(
                {
                    "cidx": int(head),
                    "child": int(child),
                    "school_row": school_row,
                    "number_id": merchant_phone[school_row],
                    "is_new": is_new,
                    "monthly": info["monthly"],
                    "level": info["level"],
                    "base": info["base"],
                    "enroll_base": info["enroll_base"],
                    "due_day": info["due_day"],
                    "enrollment": info["enrollment"],
                }
            )
    return plan


def school_payment_slots(monthly: bool, is_new: bool, enrollment: bool, level: str) -> list[tuple[int, str]]:
    """Học kỳ và đại học thu tháng 9. Trường thu tháng đóng thêm tháng 7–8 nếu đã nhập học.

    Học sinh mới có thể đóng thêm phí giữ chỗ trong tháng 8.
    """
    semester = level == "university" or not monthly
    slots = [(9, "tuition")] if semester or is_new else [(7, "tuition"), (8, "tuition"), (9, "tuition")]
    if is_new and enrollment:
        slots.append((8, "enroll"))
    return slots


def _clock(n, day, weekend, rng):
    return (
        np.asarray(day, dtype=np.int16),
        sample_hours(np.asarray(day, dtype=np.int16), weekend, rng),
        rng.integers(0, 60, size=n, dtype=np.int16),
        rng.integers(0, 60, size=n, dtype=np.int16),
    )


def _round_flags(n, share, rng) -> np.ndarray:
    flag = np.zeros(n, dtype=bool)
    n_flag = int(round(share * n))
    if n_flag:
        flag[:n_flag] = True
        rng.shuffle(flag)
    return flag


def _blank_row(n: int) -> dict:
    return {
        "from_row": np.empty(n, dtype=np.int32),
        "to_row": np.empty(n, dtype=np.int32),
        "actor": np.empty(n, dtype=np.int32),
        "amount": np.empty(n, dtype=np.int64),
        "kind": np.empty(n, dtype=np.int8),
        "day": np.empty(n, dtype=np.int16),
        "hour": np.empty(n, dtype=np.int16),
        "minute": np.empty(n, dtype=np.int16),
        "second": np.empty(n, dtype=np.int16),
        "old_payee": np.ones(n, dtype=bool),
        "do_round": np.zeros(n, dtype=bool),
        "raise_limit": np.zeros(n, dtype=bool),
        "note": np.full(n, "", dtype=object),
    }


def _append_products(frame, latent, accounts, primary, directory, hist, rng, weekend, limit_txn, opening_bal, drain_who, pass_who, large_who, origin):
    """Sổ tiết kiệm, vay nhanh, rút thẻ, người nhận phổ biến, và lệnh lớn sau nâng hạn mức."""
    n = len(primary)
    cidx = accounts["cidx"].to_numpy()
    bank = accounts["bank_id"].astype(str).to_numpy()
    rows = []

    def clock_for(day):
        return _clock(len(day), np.asarray(day, dtype=np.int16), weekend, rng)

    savings = np.flatnonzero((accounts["account_type"].astype(str) == "savings").to_numpy() & (cidx >= 0))
    owner = cidx[savings]
    usable = primary[owner] >= 0
    savings = savings[usable]
    owner = owner[usable]
    different = primary[owner] != savings
    savings = savings[different]
    owner = owner[different]
    n_mat = int(round(float(hist["savings_maturity_share"]) * len(savings))) if len(savings) else 0
    n_early = int(round(float(hist["savings_early_share"]) * len(savings))) if len(savings) else 0
    order = rng.permutation(len(savings)) if len(savings) else np.array([], dtype=int)
    maturity = savings[order[:n_mat]]
    early = savings[order[n_mat:n_mat + n_early]]
    for chosen, note, share in ((maturity, "tat toan dao han", 0.80), (early, "tat toan truoc han", 0.50)):
        if len(chosen) == 0:
            continue
        who = cidx[chosen]
        amount = np.maximum((opening_bal[chosen] * share).astype(np.int64), np.int64(100_000))
        amount = round_vnd(amount, _round_flags(len(amount), float(hist["round_amount_share"]), rng))
        day = rng.integers(10, 80, size=len(chosen), dtype=np.int16)
        day, hour, minute, second = clock_for(day)
        block = _blank_row(len(chosen))
        block["from_row"] = chosen.astype(np.int32)
        block["to_row"] = primary[who].astype(np.int32)
        block["actor"] = who.astype(np.int32)
        block["amount"] = amount.astype(np.int64)
        block["kind"] = np.full(len(chosen), SAVINGS, dtype=np.int8)
        block["day"] = day
        block["hour"] = hour
        block["minute"] = minute
        block["second"] = second
        block["note"] = np.full(len(chosen), note, dtype=object)
        rows.append(pd.DataFrame(block))

    lender = int(directory["pools"]["utility"][0])
    blocked = np.zeros(n, dtype=bool)
    blocked[np.asarray(drain_who, dtype=int)] = True
    blocked[np.asarray(pass_who, dtype=int)] = True
    free = np.flatnonzero((primary >= 0) & ~blocked)
    n_loan = int(round(float(hist["quick_loan_share"]) * n))
    n_card = int(round(float(hist["card_cash_advance_share"]) * n))
    # Hai tập rút độc lập nên một khách có thể vừa vay nhanh vừa rút thẻ.
    loan_who = rng.choice(free, size=min(n_loan, len(free)), replace=False) if n_loan and len(free) else np.array([], dtype=int)
    card_who = rng.choice(free, size=min(n_card, len(free)), replace=False) if n_card and len(free) else np.array([], dtype=int)
    for who, kind, median, low, high, note in (
        (loan_who, LOAN, 20_000_000, 2_000_000, 100_000_000, ""),
        (card_who, CASH_ADV, 3_000_000, 500_000, 20_000_000, ""),
    ):
        if len(who) == 0:
            continue
        raw = np.rint(median * np.exp(0.45 * rng.standard_normal(len(who)))).astype(np.int64)
        amount = np.clip(raw, low, high).astype(np.int64)
        amount = round_vnd(amount, _round_flags(len(amount), float(hist["round_amount_share"]), rng))
        day = rng.integers(0, 90, size=len(who), dtype=np.int16)
        day, hour, minute, second = clock_for(day)
        block = _blank_row(len(who))
        block["from_row"] = np.full(len(who), lender, dtype=np.int32)
        block["to_row"] = primary[who].astype(np.int32)
        block["actor"] = who.astype(np.int32)
        block["amount"] = amount
        block["kind"] = np.full(len(who), kind, dtype=np.int8)
        block["day"] = day
        block["hour"] = hour
        block["minute"] = minute
        block["second"] = second
        block["old_payee"] = np.ones(len(who), dtype=bool)
        block["note"] = np.full(len(who), note, dtype=object)
        rows.append(pd.DataFrame(block))

    external = np.flatnonzero(
        (cidx < 0) & accounts["phone_number_id"].notna().to_numpy() & (accounts["account_type"].astype(str) == "personal").to_numpy()
    )
    n_pop = int(round(float(hist["popular_payee_share"]) * len(external))) if len(external) else 0
    b01 = np.flatnonzero((primary >= 0) & (bank[primary] == "B01"))
    family = latent["family_group_id"].astype(str).to_numpy()
    low_m = int(hist["popular_payee_per_month"]["min"])
    high_m = int(hist["popular_payee_per_month"]["max"])
    young_ids: list[str] = []
    if n_pop and len(b01) >= high_m:
        popular = rng.choice(external, size=n_pop, replace=False)
        young_n = int(round(float(hist["popular_payee_young_share"]) * len(popular)))
        if young_n:
            age_days = rng.integers(2, 180, size=young_n)
            opened = origin - pd.to_timedelta(age_days, unit="D")
            accounts.loc[popular[:young_n], "opened_at"] = pd.Series(opened, index=popular[:young_n])
            young_ids = accounts.loc[popular[:young_n], "account_id"].astype(str).tolist()
        purposes = np.array(["tien nha thang", "hoc them", "tien hang"], dtype=object)
        for slot, payee in enumerate(popular):
            pool = []
            seen_family = set()
            shuffled = rng.permutation(b01)
            for person in shuffled:
                fam = family[int(person)]
                if fam in seen_family:
                    continue
                seen_family.add(fam)
                pool.append(int(person))
                if len(pool) >= high_m:
                    break
            if len(pool) < low_m:
                continue
            note = str(purposes[slot % len(purposes)])
            for month_i, day0 in enumerate((0, 31, 62)):
                k = int(rng.integers(low_m, min(high_m, len(pool)) + 1))
                payers = rng.choice(np.asarray(pool, dtype=int), size=k, replace=False)
                day = day0 + rng.integers(0, 28, size=k)
                day = np.clip(day, 0, 89).astype(np.int16)
                day, hour, minute, second = clock_for(day)
                amount = rng.integers(200_000, 3_000_001, size=k).astype(np.int64)
                amount = round_vnd(amount, np.ones(k, dtype=bool))
                block = _blank_row(k)
                block["from_row"] = primary[payers].astype(np.int32)
                block["to_row"] = np.full(k, int(payee), dtype=np.int32)
                block["actor"] = payers.astype(np.int32)
                block["amount"] = amount
                block["kind"] = np.full(k, TRANSFER, dtype=np.int8)
                block["day"] = day
                block["hour"] = hour
                block["minute"] = minute
                block["second"] = second
                block["old_payee"] = np.ones(k, dtype=bool)
                block["note"] = np.full(k, note, dtype=object)
                rows.append(pd.DataFrame(block))

    can = (primary >= 0) & ((opening_bal[primary] * 90) // 100 > limit_txn[primary] + 1_000_000)
    can[np.asarray(drain_who, dtype=int)] = False
    can[np.asarray(pass_who, dtype=int)] = False
    can[np.asarray(large_who, dtype=int)] = False
    n_raise = int(round(float(hist["limit_raise_share"]) * n))
    prefer = np.asarray(large_who, dtype=int)
    prefer = prefer[(primary[prefer] >= 0)] if len(prefer) else prefer
    n_fill = max(0, n_raise - len(prefer))
    pool = np.flatnonzero(can)
    fill = rng.choice(pool, size=min(n_fill, len(pool)), replace=False) if n_fill and len(pool) else np.array([], dtype=int)
    if len(fill):
        bal = opening_bal[primary[fill]]
        lim = limit_txn[primary[fill]]
        amount = np.minimum((bal * 70) // 100, lim * 2).astype(np.int64)
        amount = round_vnd(amount, _round_flags(len(amount), float(hist["round_amount_share"]), rng))
        amount = np.maximum(amount, lim + 1_000_000)
        dealer = directory["pools"]["real_estate"][fill % len(directory["pools"]["real_estate"])]
        day = np.zeros(len(fill), dtype=np.int16)
        hour = np.full(len(fill), 6, dtype=np.int16)
        minute = rng.integers(0, 40, size=len(fill)).astype(np.int16)
        second = rng.integers(0, 60, size=len(fill)).astype(np.int16)
        block = _blank_row(len(fill))
        block["from_row"] = primary[fill].astype(np.int32)
        block["to_row"] = dealer.astype(np.int32)
        block["actor"] = fill.astype(np.int32)
        block["amount"] = amount.astype(np.int64)
        block["kind"] = np.full(len(fill), TRANSFER, dtype=np.int8)
        block["day"] = day
        block["hour"] = hour
        block["minute"] = minute
        block["second"] = second
        block["old_payee"] = np.zeros(len(fill), dtype=bool)
        block["raise_limit"] = np.zeros(len(fill), dtype=bool)
        notes = np.array(["coc xe", "dat coc", "hoc phi"], dtype=object)
        block["note"] = notes[rng.integers(0, len(notes), size=len(fill))]
        rows.append(pd.DataFrame(block))
    if rows:
        extra = pd.concat(rows, ignore_index=True)
        frame = pd.concat([frame, extra[frame.columns]], ignore_index=True)
    return frame, young_ids


def build_events(customers, latent, accounts, age, primary, alt, drain_to, directory, cfg, rng, origin, weekend, payday):
    hist = cfg["history"]
    n = len(customers)
    income = latent["income_monthly"].to_numpy().astype(np.int64)
    activity = latent["activity_level"].to_numpy().astype(float)
    segment = customers["segment"].astype(str).to_numpy()
    normal = segment == "thường"
    elderly = segment == "cao tuổi"
    business = segment == "doanh nghiệp nhỏ"
    cidx = np.arange(n, dtype=np.int32)
    events = Events()
    share_round = float(hist["round_amount_share"])
    scale = activity / np.median(activity)
    month_median = float(hist["tx_per_month_median"]) * scale
    month_count = np.rint(month_median * np.exp(TX_SIGMA * rng.standard_normal(n))).astype(int)
    month_count = np.clip(month_count, 1, 120)

    # Lương / hưu một ngày cố định mỗi tháng.
    wage = cidx[~business]
    if len(wage):
        day = payday[wage].ravel()
        actor = np.repeat(wage, 3)
        factor = rng.uniform(0.93, 1.07, size=len(actor))
        base = income[actor].astype(float)
        base[elderly[actor]] *= rng.uniform(0.40, 0.60, size=int(elderly[actor].sum()))
        amount = np.rint(base * factor).astype(np.int64)
        amount = round_vnd(amount, _round_flags(len(amount), share_round, rng))
        day, hour, minute, second = _clock(len(actor), day, weekend, rng)
        employer = directory["pools"]["utility"][actor % len(directory["pools"]["utility"])]
        events.add(
            from_row=employer.astype(np.int32),
            to_row=primary[actor],
            actor=actor.astype(np.int32),
            amount=amount,
            kind=np.full(len(actor), SALARY, dtype=np.int8),
            day=day, hour=hour, minute=minute, second=second,
            old_payee=np.ones(len(actor), dtype=bool),
            do_round=np.zeros(len(actor), dtype=bool),
        )

    # Doanh nghiệp nhỏ: nhiều khoản thu nhỏ, tổng gần thu nhập tháng.
    biz = cidx[business]
    if len(biz):
        per_month = rng.integers(8, 16, size=len(biz))
        actor = np.repeat(biz, per_month * 3)
        n_in = len(actor)
        month_income = np.repeat(income[biz] * rng.uniform(0.93, 1.07, size=len(biz)), per_month * 3)
        counts = np.repeat(per_month, 3)
        # Chia đều trong tháng rồi thêm nhiễu, chuẩn lại theo tháng ở dưới bằng hệ số.
        noise = rng.uniform(0.5, 1.5, size=n_in)
        amount = noise
        # Hệ số theo từng khách-tháng để tổng đúng monthly target.
        owner_month = np.repeat(np.arange(len(biz) * 3), np.repeat(per_month, 3))
        target = np.repeat(income[biz] * rng.uniform(0.93, 1.07, size=len(biz)), 3)
        sum_noise = np.bincount(owner_month, weights=noise, minlength=len(target))
        amount = np.rint(noise * (target[owner_month] / sum_noise[owner_month])).astype(np.int64)
        amount = np.maximum(amount, 10_000)
        amount = round_vnd(amount, _round_flags(n_in, share_round, rng))
        ext_pool = accounts.index[(accounts["cidx"] < 0) & accounts["phone_number_id"].notna() & (accounts["account_type"] == "personal")].to_numpy()
        day = rng.integers(0, 90, size=n_in, dtype=np.int16)
        day, hour, minute, second = _clock(n_in, day, weekend, rng)
        events.add(
            from_row=rng.choice(ext_pool, size=n_in).astype(np.int32),
            to_row=primary[actor],
            actor=actor.astype(np.int32),
            amount=amount,
            kind=np.full(n_in, BUSINESS_IN, dtype=np.int8),
            day=day, hour=hour, minute=minute, second=second,
            old_payee=np.zeros(n_in, dtype=bool),
            do_round=np.zeros(n_in, dtype=bool),
        )

    # Con trong hộ chuyển cho cha mẹ cao tuổi.
    support_from, support_to, support_actor = [], [], []
    for group_id, children in directory["child_of"].items():
        members = directory["groups"][group_id]
        elders = [person for person in members if elderly[person]]
        if not elders or not children:
            continue
        parent = elders[0]
        child = children[0]
        if primary[child] < 0 or primary[parent] < 0 or child == parent:
            continue
        support_from.append(primary[child])
        support_to.append(primary[parent])
        support_actor.append(child)
    if support_from:
        base = np.asarray(support_actor, dtype=np.int32)
        actor = np.repeat(base, 3)
        n_sup = len(actor)
        amount = rng.integers(500_000, 2_000_001, size=n_sup).astype(np.int64)
        amount = round_vnd(amount, _round_flags(n_sup, share_round, rng))
        day = payday[base].ravel()
        day, hour, minute, second = _clock(n_sup, day, weekend, rng)
        events.add(
            from_row=np.repeat(np.asarray(support_from, dtype=np.int32), 3),
            to_row=np.repeat(np.asarray(support_to, dtype=np.int32), 3),
            actor=actor,
            amount=amount,
            kind=np.full(n_sup, TRANSFER, dtype=np.int8),
            day=day, hour=hour, minute=minute, second=second,
            old_payee=np.ones(n_sup, dtype=bool),
            do_round=np.zeros(n_sup, dtype=bool),
        )

    # Hóa đơn tháng: điện, nước, internet, điện thoại.
    bill_days = []
    dates = pd.date_range(origin, periods=90, freq="D")
    for month, day_of_month in ((7, 4), (7, 9), (7, 16), (7, 22), (8, 4), (8, 9), (8, 16), (8, 22), (9, 4), (9, 9), (9, 16), (9, 22)):
        hits = np.flatnonzero((dates.month == month) & (dates.day == day_of_month))
        bill_days.append(int(hits[0]) if len(hits) else None)
    bill_days = [day for day in bill_days if day is not None]
    # 4 loại × 3 tháng, cùng thứ tự lặp.
    kind_cycle = np.resize(np.arange(4), len(bill_days))
    ranges = [(150_000, 1_200_000), (50_000, 350_000), (165_000, 350_000), (50_000, 250_000)]
    util_acct = directory["pools"]["utility"][cidx % len(directory["pools"]["utility"])]
    for day, kind_i in zip(bill_days, kind_cycle):
        low, high = ranges[int(kind_i)]
        amount = rng.integers(low, high + 1, size=n).astype(np.int64)
        amount = round_vnd(amount, _round_flags(n, share_round, rng))
        day_a, hour, minute, second = _clock(n, np.full(n, day, dtype=np.int16), weekend, rng)
        events.add(
            from_row=primary.copy(),
            to_row=util_acct.astype(np.int32),
            actor=cidx.copy(),
            amount=amount,
            kind=np.full(n, BILL, dtype=np.int8),
            day=day_a, hour=hour, minute=minute, second=second,
            old_payee=np.ones(n, dtype=bool),
            do_round=np.zeros(n, dtype=bool),
        )

    # Tiền nhà người 18–35 tuổi.
    young = np.flatnonzero((age >= 18) & (age <= 35))
    n_rent = int(round(float(hist["renter_share_young"]) * len(young)))
    renters = rng.choice(young, size=n_rent, replace=False) if n_rent else np.array([], dtype=int)
    if len(renters):
        rent_days = []
        for month in (7, 8, 9):
            hits = np.flatnonzero((dates.month == month) & (dates.day == 6))
            if len(hits):
                rent_days.append(int(hits[0]))
        day = np.tile(np.asarray(rent_days, dtype=np.int16), len(renters))
        actor = np.repeat(renters.astype(np.int32), len(rent_days))
        amount = np.rint(income[actor] * rng.uniform(0.12, 0.28, size=len(actor))).astype(np.int64)
        amount = np.maximum(amount, 500_000)
        amount = round_vnd(amount, _round_flags(len(amount), share_round, rng))
        landlord = directory["pools"]["real_estate"][actor % len(directory["pools"]["real_estate"])]
        day, hour, minute, second = _clock(len(actor), day, weekend, rng)
        events.add(
            from_row=primary[actor],
            to_row=landlord.astype(np.int32),
            actor=actor,
            amount=amount,
            kind=np.full(len(actor), RENT, dtype=np.int8),
            day=day, hour=hour, minute=minute, second=second,
            old_payee=np.ones(len(actor), dtype=bool),
            do_round=np.zeros(len(actor), dtype=bool),
        )

    # Học phí: cùng một trường cho mỗi con. Con đã nhập học từ trước thì người nhận là payee cũ.
    fee_actor, fee_school, fee_day, fee_new, fee_amount = [], [], [], [], []
    med_income = float(np.median(income))

    def fee_day_index(month: int, due_day: int, offset: int):
        match = np.flatnonzero((dates.month == month) & (dates.day == due_day))
        if len(match) == 0:
            return None
        index = int(match[0])
        room_before = index
        room_after = len(dates) - 1 - index
        offset = int(np.clip(offset, -room_before, room_after))
        return index + offset

    for item in directory["school_plan"]:
        head = int(item["cidx"])
        if primary[head] < 0:
            continue
        factor = float(np.clip((float(income[head]) / med_income) ** 0.45, 0.65, 1.70))
        slots = school_payment_slots(bool(item["monthly"]), bool(item["is_new"]), bool(item["enrollment"]), str(item["level"]))
        for month, slot in slots:
            on_time = rng.random() < 0.70
            offset = int(rng.integers(-3, 4) if on_time else rng.integers(1, 15))
            day_index = fee_day_index(month, int(item["due_day"]), offset)
            if day_index is None:
                continue
            raw = item["enroll_base"] if slot == "enroll" else item["base"]
            fee_actor.append(head)
            fee_school.append(int(item["school_row"]))
            fee_day.append(day_index)
            fee_new.append(bool(item["is_new"]))
            fee_amount.append(max(1_000, int(round(raw * factor))))
    if fee_actor:
        actor = np.asarray(fee_actor, dtype=np.int32)
        amount = np.asarray(fee_amount, dtype=np.int64)
        day, hour, minute, second = _clock(len(actor), np.asarray(fee_day, dtype=np.int16), weekend, rng)
        events.add(
            from_row=primary[actor],
            to_row=np.asarray(fee_school, dtype=np.int32),
            actor=actor,
            amount=amount,
            kind=np.full(len(actor), SCHOOL, dtype=np.int8),
            day=day, hour=hour, minute=minute, second=second,
            old_payee=~np.asarray(fee_new, dtype=bool),
            do_round=np.zeros(len(actor), dtype=bool),
        )

    # Mẫu khó: đặt cọc / viện phí, rút gần hết sang tài khoản mình, thủ quỹ.
    rich = np.flatnonzero(accounts["opening_balance"].to_numpy()[primary] >= 60_000_000)
    n_large = int(round(float(hist["large_oneoff_share"]) * n))
    n_drain = int(round(float(hist["own_transfer_drain_share"]) * n))
    n_pass = int(round(float(hist["legit_passthrough_share"]) * n))
    large_who = rng.choice(rich, size=min(n_large, len(rich)), replace=False) if n_large and len(rich) else np.array([], dtype=int)
    limit_txn = accounts["limit_per_txn"].to_numpy().astype(np.int64)
    opening_bal = accounts["opening_balance"].to_numpy().astype(np.int64)
    # Rút gần hết phải nằm trong một lệnh: chia nhỏ sẽ không còn > 80% số dư.
    drain_fits = (opening_bal[primary] * 85 // 100) <= limit_txn[primary]
    multi = np.flatnonzero(
        (drain_to >= 0) & (primary >= 0) & (drain_to != primary) & ~np.isin(cidx, large_who) & drain_fits
    )
    # Ưu tiên ngân hàng B01 để phiên rút gần hết đủ mẫu, tỷ lệ khách không đổi.
    primary_bank = accounts["bank_id"].astype(str).to_numpy()
    multi_home = multi[primary_bank[primary[multi]] == "B01"] if len(multi) else multi
    drain_pool = multi_home if len(multi_home) >= n_drain else multi
    drain_who = rng.choice(drain_pool, size=min(n_drain, len(drain_pool)), replace=False) if n_drain and len(drain_pool) else np.array([], dtype=int)
    blocked = np.zeros(n, dtype=bool)
    blocked[large_who] = True
    blocked[drain_who] = True
    pass_pool = np.flatnonzero(~blocked)
    pass_who = rng.choice(pass_pool, size=min(n_pass, len(pass_pool)), replace=False) if n_pass and len(pass_pool) else np.array([], dtype=int)

    if len(large_who):
        actor = large_who.astype(np.int32)
        cap = (accounts["opening_balance"].to_numpy()[primary[actor]] * 0.85).astype(np.int64)
        raw = np.rint(np.exp(np.log(50_000_000) + rng.random(len(actor)) * np.log(10))).astype(np.int64)
        amount = np.minimum(np.maximum(raw, 50_000_000), np.maximum(cap, 50_000_000))
        amount = np.minimum(amount, cap)
        hospital = rng.random(len(actor)) < 0.35
        dest = np.empty(len(actor), dtype=np.int32)
        dest[hospital] = directory["pools"]["hospital"][actor[hospital] % len(directory["pools"]["hospital"])]
        dest[~hospital] = directory["pools"]["real_estate"][actor[~hospital] % len(directory["pools"]["real_estate"])]
        day = rng.integers(0, 12, size=len(actor), dtype=np.int16)
        day, hour, minute, second = _clock(len(actor), day, weekend, rng)
        hour[:] = 8
        events.add(
            from_row=primary[actor], to_row=dest, actor=actor, amount=amount.astype(np.int64),
            kind=np.full(len(actor), LARGE, dtype=np.int8),
            day=day, hour=hour, minute=minute, second=second,
            old_payee=np.zeros(len(actor), dtype=bool), do_round=np.zeros(len(actor), dtype=bool),
        )

    if len(drain_who):
        actor = drain_who.astype(np.int32)
        # Đầu cửa sổ, trước các khoản chi, để lấy đúng hơn 80% số dư mở.
        day = np.zeros(len(actor), dtype=np.int16)
        hour = np.zeros(len(actor), dtype=np.int16)
        minute = np.zeros(len(actor), dtype=np.int16)
        second = np.zeros(len(actor), dtype=np.int16)
        events.add(
            from_row=primary[actor], to_row=drain_to[actor], actor=actor,
            amount=np.zeros(len(actor), dtype=np.int64),
            kind=np.full(len(actor), DRAIN, dtype=np.int8),
            day=day, hour=hour, minute=minute, second=second,
            old_payee=np.ones(len(actor), dtype=bool), do_round=np.zeros(len(actor), dtype=bool),
        )

    if len(pass_who):
        # Mỗi thủ quỹ có 3–15 người quen. Mỗi lần nhận được chuyển 70–95% đi sau 1–12 giờ,
        # và các lần cách nhau hơn 24 giờ để tỷ lệ 24h của từng khoản vào không bị cộng dồn.
        ext_pool = accounts.index[
            (accounts["cidx"] < 0) & accounts["phone_number_id"].notna() & (accounts["account_type"] == "personal")
        ].to_numpy()
        fam = latent["family_group_id"].astype(str).to_numpy()
        in_from, in_to, in_actor, in_amt = [], [], [], []
        in_day, in_hour, in_min, in_sec = [], [], [], []
        out_from, out_to, out_actor, out_amt = [], [], [], []
        out_day, out_hour, out_min, out_sec = [], [], [], []
        for person in pass_who.astype(int):
            if primary[person] < 0 or len(ext_pool) < 3:
                continue
            n_friends = int(rng.integers(3, 16))
            friends = rng.choice(ext_pool, size=min(n_friends, len(ext_pool)), replace=False)
            children = [child for child in directory["child_of"].get(fam[person], []) if child != person and primary[child] >= 0]
            dest = int(children[0]) if children else int((person + 997) % n)
            if dest == person or primary[dest] < 0:
                dest = int((person + 1) % n)
            n_slots = 48
            for slot in range(n_slots):
                when_h = 20 + slot * 32
                delay_h = int(rng.integers(1, 13))
                out_h = when_h + delay_h
                if out_h >= 89 * 24:
                    break
                amount = int(rng.integers(20_000, 50_001)) * 1000
                sent = int(amount * float(rng.uniform(0.70, 0.95)) // 1000 * 1000)
                if sent < 1000:
                    continue
                in_from.append(int(friends[slot % len(friends)]))
                in_to.append(int(primary[person]))
                in_actor.append(person)
                in_amt.append(amount)
                in_day.append(when_h // 24)
                in_hour.append(when_h % 24)
                in_min.append(int(rng.integers(0, 50)))
                in_sec.append(int(rng.integers(0, 60)))
                out_from.append(int(primary[person]))
                out_to.append(int(primary[dest]))
                out_actor.append(person)
                out_amt.append(sent)
                out_day.append(out_h // 24)
                out_hour.append(out_h % 24)
                out_min.append(int(rng.integers(0, 50)))
                out_sec.append(int(rng.integers(0, 60)))
        if in_from:
            n_in = len(in_from)
            events.add(
                from_row=np.asarray(in_from, dtype=np.int32),
                to_row=np.asarray(in_to, dtype=np.int32),
                actor=np.asarray(in_actor, dtype=np.int32),
                amount=np.asarray(in_amt, dtype=np.int64),
                kind=np.full(n_in, PASS_IN, dtype=np.int8),
                day=np.asarray(in_day, dtype=np.int16),
                hour=np.asarray(in_hour, dtype=np.int16),
                minute=np.asarray(in_min, dtype=np.int16),
                second=np.asarray(in_sec, dtype=np.int16),
                old_payee=np.ones(n_in, dtype=bool),
                do_round=np.zeros(n_in, dtype=bool),
            )
            n_out = len(out_from)
            events.add(
                from_row=np.asarray(out_from, dtype=np.int32),
                to_row=np.asarray(out_to, dtype=np.int32),
                actor=np.asarray(out_actor, dtype=np.int32),
                amount=np.asarray(out_amt, dtype=np.int64),
                kind=np.full(n_out, PASS_OUT, dtype=np.int8),
                day=np.asarray(out_day, dtype=np.int16),
                hour=np.asarray(out_hour, dtype=np.int16),
                minute=np.asarray(out_min, dtype=np.int16),
                second=np.asarray(out_sec, dtype=np.int16),
                old_payee=np.ones(n_out, dtype=bool),
                do_round=np.zeros(n_out, dtype=bool),
            )

    # Giao dịch tùy ý: thẻ, chuyển khoản, rút tiền, chuyển nội bộ.
    # Bắt buộc (lương, hóa đơn) đã chiếm khoảng 5 giao dịch/tháng; phần còn lại đạt trung vị 25.
    obligatory = np.full(n, 5.0)
    obligatory[business] = 4.0
    discretionary = np.maximum(0, month_count * 3 - np.rint(obligatory * 3).astype(int))
    discretionary = np.clip(discretionary, 0, 250)
    actor = np.repeat(cidx, discretionary)
    n_tx = len(actor)
    if n_tx:
        u = rng.random(n_tx)
        eld = elderly[actor]
        kind = np.full(n_tx, TRANSFER, dtype=np.int8)
        kind[u < 0.42] = CARD
        kind[(u >= 0.42) & (u < 0.50)] = CASH
        kind[(u >= 0.50) & (u < 0.58)] = OWN
        # Cao tuổi rút tiền mặt nhiều hơn.
        extra_cash = eld & (u >= 0.58) & (u < 0.66)
        kind[extra_cash] = CASH
        no_alt = alt[actor] < 0
        kind[(kind == OWN) & no_alt] = TRANSFER
        use_alt = (alt[actor] >= 0) & (rng.random(n_tx) < 0.40) & (kind != OWN)
        from_row = primary[actor].copy()
        from_row[use_alt] = alt[actor[use_alt]]
        from_row[kind == OWN] = primary[actor[kind == OWN]]
        to_row = np.full(n_tx, -1, dtype=np.int32)
        old_payee = np.ones(n_tx, dtype=bool)
        # Đối tác chuyển khoản: phần lớn quen, 12% người mới.
        friend = (actor * 17 + np.arange(n_tx)) % n
        same = friend == actor
        friend[same] = (friend[same] + 1) % n
        new_face = (kind == TRANSFER) & (rng.random(n_tx) < 0.12)
        shop = directory["pools"]["shop"][actor % len(directory["pools"]["shop"])]
        to_row[kind == CARD] = shop[kind == CARD]
        to_row[kind == TRANSFER] = primary[friend[kind == TRANSFER]]
        to_row[kind == OWN] = alt[actor[kind == OWN]]
        old_payee[new_face] = False
        ext_pool = accounts.index[(accounts["cidx"] < 0) & accounts["phone_number_id"].notna() & (accounts["account_type"] == "personal")].to_numpy()
        if len(ext_pool):
            to_ext = new_face & (rng.random(n_tx) < 0.5)
            to_row[to_ext] = rng.choice(ext_pool, size=int(to_ext.sum())).astype(np.int32)
        med = np.where(kind == CARD, 0.012, np.where(kind == CASH, 0.08, np.where(kind == OWN, 0.10, 0.035))) * income[actor]
        amount = np.rint(med * np.exp(0.65 * rng.standard_normal(n_tx))).astype(np.int64)
        amount = np.clip(amount, 10_000, income[actor] * 2)
        flags = _round_flags(n_tx, share_round, rng)
        amount = round_vnd(amount, flags)
        use_boost = rng.random(n_tx) < 0.40
        month = rng.integers(0, 3, size=n_tx)
        offset = rng.integers(0, 4, size=n_tx)
        boost_day = np.clip(payday[actor, month] + offset, 0, 89)
        day = rng.integers(0, 90, size=n_tx, dtype=np.int16)
        day = np.where(use_boost, boost_day, day).astype(np.int16)
        day, hour, minute, second = _clock(n_tx, day, weekend, rng)
        events.add(
            from_row=from_row.astype(np.int32),
            to_row=to_row,
            actor=actor.astype(np.int32),
            amount=amount,
            kind=kind,
            day=day, hour=hour, minute=minute, second=second,
            old_payee=old_payee,
            do_round=flags,
        )
    frame = events.frame()
    if bool(hist["card_payments_aggregate_daily"]):
        card = frame["kind"].to_numpy() == CARD
        if card.any():
            cards = frame.loc[card].copy()
            rest = frame.loc[~card]
            grouped = cards.groupby(["from_row", "day"], sort=False)
            chosen = cards.loc[grouped["amount"].idxmax()].drop(columns=["amount", "old_payee", "do_round"])
            totals = grouped.agg(amount=("amount", "sum"), old_payee=("old_payee", "max"), do_round=("do_round", "max")).reset_index()
            packed = chosen.merge(totals, on=["from_row", "day"], how="left")
            packed["amount"] = packed["amount"].to_numpy().astype(np.int64)
            frame = pd.concat([rest, packed[rest.columns]], ignore_index=True)
    frame, young_ids = _append_products(
        frame, latent, accounts, primary, directory, hist, rng, weekend,
        limit_txn, opening_bal, drain_who, pass_who, large_who, origin,
    )
    return frame, young_ids


def apply_balances(frame: pd.DataFrame, accounts: pd.DataFrame, origin: pd.Timestamp, rng, sme_account: np.ndarray, split_share: float, split_share_sme: float):
    tracked = (accounts["cidx"].to_numpy() >= 0)
    opening = accounts["opening_balance"].to_numpy().astype(np.int64)
    opened = accounts["opened_at"]
    opened_s = unix_seconds(opened.astype("int64").to_numpy())
    bal = opening.copy()
    n = len(frame)
    from_row = frame["from_row"].to_numpy().astype(np.int32)
    to_row = frame["to_row"].to_numpy().astype(np.int32)
    amount = frame["amount"].to_numpy().astype(np.int64).copy()
    kind = frame["kind"].to_numpy().astype(np.int8)
    do_round = frame["do_round"].to_numpy().astype(bool)
    ts = stamp_from_parts(origin, frame["day"], frame["hour"], frame["minute"], frame["second"])
    ts_s = unix_seconds(np.asarray(ts.astype("int64")))
    if n == 0:
        raise RuntimeError("Không có giao dịch trước khi áp số dư")
    order = np.lexsort((np.arange(n), ts_s))
    keep = np.ones(n, dtype=bool)
    dropped_open = 0
    fbb = np.zeros(n, dtype=np.int64)
    day_col = frame["day"].to_numpy().astype(np.int16)
    hour_col = frame["hour"].to_numpy().astype(np.int16)
    minute_col = frame["minute"].to_numpy().astype(np.int16)
    second_col = frame["second"].to_numpy().astype(np.int16)
    actor_col = frame["actor"].to_numpy().astype(np.int32)
    old_col = frame["old_payee"].to_numpy().astype(bool)
    probe = stamp_from_parts(origin, np.array([0]), np.array([0]), np.array([0]), np.array([0]))
    origin_s = int(unix_seconds(probe.astype("int64").to_numpy())[0])
    limit_txn = accounts["limit_per_txn"].to_numpy().astype(np.int64).copy()
    limit_day = accounts["limit_per_day"].to_numpy().astype(np.int64).copy()
    spent = np.zeros(len(accounts), dtype=np.int64)
    spent_on = np.full(len(accounts), -1, dtype=np.int16)
    raised_done = np.zeros(len(accounts), dtype=bool)
    account_ids = accounts["account_id"].astype(str).to_numpy()
    raise_col = frame["raise_limit"].to_numpy().astype(bool) if "raise_limit" in frame.columns else np.zeros(n, dtype=bool)
    note_col = frame["note"].astype(str).to_numpy() if "note" in frame.columns else np.full(n, "", dtype=object)
    changes: list[dict] = []
    stats = {"outbound": 0, "split": 0, "over": 0, "over_split": 0, "over_sme": 0, "over_sme_split": 0}
    deferred: list[tuple] = []
    extras: list[dict] = []
    seq = 0

    def delay_within(day_i: int, want: int) -> int | None:
        room = 89 - int(day_i)
        if room < 1:
            return None
        return min(int(want), room)

    def push_school(day_i, amt, fr, tr, actor_i, hour_i, minute_i, second_i, old_i, attempts) -> bool:
        nonlocal seq
        if amt < 1_000 or not (0 <= int(day_i) < 90) or attempts > 2:
            return False
        ts_i = origin_s + int(day_i) * 86400 + int(hour_i) * 3600 + int(minute_i) * 60 + int(second_i)
        seq += 1
        heapq.heappush(
            deferred,
            (ts_i, seq, int(day_i), int(amt), int(fr), int(tr), int(actor_i), int(hour_i), int(minute_i), int(second_i), bool(old_i), int(attempts), int(SCHOOL), False, "", True),
        )
        return True

    def bump_clock(day_i, hour_i, minute_i, second_i, add_day: bool):
        second_i = int(second_i) + (0 if add_day else 1)
        minute_i = int(minute_i)
        hour_i = int(hour_i)
        day_i = int(day_i) + (1 if add_day else 0)
        if second_i >= 60:
            second_i -= 60
            minute_i += 1
        if minute_i >= 60:
            minute_i -= 60
            hour_i += 1
        if hour_i >= 24:
            hour_i -= 24
            day_i += 1
        if not (0 <= day_i <= 89):
            return None
        return day_i, hour_i, minute_i, second_i

    def push_rest(day_i, hour_i, minute_i, second_i, amt, fr, tr, actor_i, old_i, kind_i, note_i, count_logical, add_day) -> bool:
        nonlocal seq
        clock = bump_clock(day_i, hour_i, minute_i, second_i, add_day)
        if clock is None or int(amt) < 1_000:
            return False
        day_i, hour_i, minute_i, second_i = clock
        ts_i = origin_s + int(day_i) * 86400 + int(hour_i) * 3600 + int(minute_i) * 60 + int(second_i)
        seq += 1
        heapq.heappush(
            deferred,
            (ts_i, seq, int(day_i), int(amt), int(fr), int(tr), int(actor_i), int(hour_i), int(minute_i), int(second_i), bool(old_i), 0, int(kind_i), False, str(note_i), bool(count_logical)),
        )
        return True

    def touch(fr, day_i) -> None:
        if int(spent_on[fr]) != int(day_i):
            spent_on[fr] = np.int16(day_i)
            spent[fr] = 0

    def maybe_raise(fr, paid, day_i, hour_i, minute_i, second_i) -> None:
        touch(fr, day_i)
        old_t = int(limit_txn[fr])
        old_d = int(limit_day[fr])
        covered = int(((max(int(paid), 1) + 999_999) // 1_000_000) * 1_000_000)
        new_t = max(old_t, covered)
        if new_t == old_t:
            new_t = old_t + 50_000_000
        new_d = max(old_d, new_t, int(spent[fr]) + int(paid))
        limit_txn[fr] = np.int64(new_t)
        limit_day[fr] = np.int64(new_d)
        raised_done[fr] = True
        if rng.random() < 0.12:
            lead = int(rng.integers(5 * 60, 25 * 60))
        else:
            lead = int(rng.integers(2 * 3600, 36 * 3600))
        stamp = origin_s + int(day_i) * 86400 + int(hour_i) * 3600 + int(minute_i) * 60 + int(second_i) - lead
        if stamp < origin_s:
            stamp = origin_s
        changes.append(
            {
                "account_id": account_ids[fr],
                "ts": origin + pd.to_timedelta(int(stamp - origin_s), unit="s"),
                "change_type": "limit_raise",
                "old_value": format_limit(old_t, old_d),
                "new_value": format_limit(new_t, new_d),
                "channel": "app" if int(fr) % 10 < 7 else "quầy",
            }
        )

    def clip_limit(fr, day_i, paid, want_raise, hour_i, minute_i, second_i) -> int:
        if want_raise:
            maybe_raise(fr, paid, day_i, hour_i, minute_i, second_i)
        touch(fr, day_i)
        room = int(limit_day[fr]) - int(spent[fr])
        cap = min(int(paid), int(limit_txn[fr]), room)
        if cap < 1_000:
            return 0
        spent[fr] += np.int64(cap)
        return int(cap)

    def choose_raise(fr, paid, day_i, count_logical: bool) -> bool:
        """Lệnh vượt hạn mức: SME khoảng 40% chia nhỏ, còn lại khoảng 25%; phần kia nâng hạn mức."""
        if not count_logical:
            return False
        touch(fr, day_i)
        room = int(limit_day[fr]) - int(spent[fr])
        if int(paid) <= int(limit_txn[fr]) and int(paid) <= room:
            return False
        stats["over"] += 1
        sme = bool(sme_account[fr])
        if sme:
            stats["over_sme"] += 1
        if rng.random() < (split_share_sme if sme else split_share):
            stats["over_split"] += 1
            if sme:
                stats["over_sme_split"] += 1
            return False
        return True

    def settle_school(day_i, amt, avail, fr, tr, actor_i, hour_i, minute_i, second_i, old_i, attempts):
        """Thiếu số dư thì chia đôi hoặc dời nguyên số tiền. Không hạ học phí xuống đúng số dư."""
        half = int(amt) // 2
        split_delay = delay_within(day_i, 10)
        late_delay = delay_within(day_i, 7 + (int(fr) % 8))
        if attempts < 2 and half >= 1_000 and avail >= half and split_delay is not None:
            push_school(int(day_i) + split_delay, int(amt) - half, fr, tr, actor_i, hour_i, minute_i, second_i, old_i, attempts + 1)
            return half
        if attempts < 2 and late_delay is not None:
            push_school(int(day_i) + late_delay, amt, fr, tr, actor_i, hour_i, minute_i, second_i, old_i, attempts + 1)
        return None

    def apply_outbound(fr, tr, k, amt, avail, day_i, hour_i, minute_i, second_i, actor_i, old_i, do_round_i, attempts):
        if k == SCHOOL and amt > avail:
            paid = settle_school(day_i, amt, avail, fr, tr, actor_i, hour_i, minute_i, second_i, old_i, attempts)
            if paid is None:
                return None
            amt = paid
        elif amt > avail:
            floor = 50_000_000 if k == LARGE else 1_000
            if avail < floor:
                return None
            amt = avail
        if do_round_i and k not in (SCHOOL, LARGE, DRAIN):
            unit = 100_000 if amt >= 1_000_000 else 1_000
            snapped = (amt // unit) * unit
            if snapped >= 1_000:
                amt = snapped
        if amt <= 0 or amt > avail:
            return None
        return int(amt)

    pos = 0
    n_order = len(order)
    while pos < n_order or deferred:
        next_orig = int(ts_s[order[pos]]) if pos < n_order else 2**62
        if deferred and deferred[0][0] < next_orig:
            ts_i, _, day_i, amt, fr, tr, actor_i, hour_i, minute_i, second_i, old_i, attempts, kind_i, raise_i, note_i, count_logical = heapq.heappop(deferred)
            if ts_i < opened_s[fr] or (tr >= 0 and ts_i < opened_s[tr]):
                dropped_open += 1
                continue
            if not tracked[fr]:
                continue
            avail = int(bal[fr])
            paid = apply_outbound(fr, tr, int(kind_i), amt, avail, day_i, hour_i, minute_i, second_i, actor_i, old_i, False, attempts)
            if paid is None:
                continue
            want_raise = choose_raise(fr, paid, day_i, bool(count_logical))
            cap = clip_limit(fr, day_i, paid, want_raise, hour_i, minute_i, second_i)
            if cap <= 0:
                push_rest(day_i, hour_i, minute_i, second_i, paid, fr, tr, actor_i, old_i, kind_i, note_i, count_logical, True)
                continue
            if count_logical:
                stats["outbound"] += 1
            if cap < paid:
                if count_logical:
                    stats["split"] += 1
                room_left = int(limit_day[fr]) - int(spent[fr])
                push_rest(day_i, hour_i, minute_i, second_i, paid - cap, fr, tr, actor_i, old_i, kind_i, note_i, False, room_left < 1_000)
            bal[fr] = avail - cap
            if tr >= 0 and tracked[tr]:
                bal[tr] += cap
            extras.append(
                {
                    "from_row": fr,
                    "to_row": tr,
                    "actor": actor_i,
                    "amount": cap,
                    "kind": int(kind_i),
                    "day": day_i,
                    "hour": hour_i,
                    "minute": minute_i,
                    "second": second_i,
                    "old_payee": old_i,
                    "do_round": False,
                    "raise_limit": False,
                    "note": note_i,
                    "from_balance_before": avail,
                    "ts": origin + pd.to_timedelta(int(day_i) * 86400 + int(hour_i) * 3600 + int(minute_i) * 60 + int(second_i), unit="s"),
                }
            )
            continue
        i = int(order[pos])
        pos += 1
        fr = int(from_row[i])
        tr = int(to_row[i])
        k = int(kind[i])
        if ts_s[i] < opened_s[fr] or (tr >= 0 and ts_s[i] < opened_s[tr]):
            keep[i] = False
            dropped_open += 1
            continue
        if k == DRAIN:
            avail = int(bal[fr])
            amt = (avail * 85) // 100
            touch(fr, int(day_col[i]))
            room = int(limit_day[fr]) - int(spent[fr])
            if amt <= (avail * 80) // 100 or amt < 1_000 or tr < 0 or amt > int(limit_txn[fr]) or amt > room:
                keep[i] = False
                continue
            spent[fr] += np.int64(amt)
            stats["outbound"] += 1
            fbb[i] = avail
            amount[i] = amt
            bal[fr] = avail - amt
            if tr >= 0 and tracked[tr]:
                bal[tr] += amt
            continue
        amt = int(amount[i])
        if k in INBOUND or not tracked[fr]:
            fbb[i] = int(opening[fr])
            if tr >= 0 and tracked[tr] and amt > 0:
                bal[tr] += amt
            else:
                keep[i] = False
            continue
        avail = int(bal[fr])
        paid = apply_outbound(
            fr, tr, k, amt, avail,
            int(day_col[i]), int(hour_col[i]), int(minute_col[i]), int(second_col[i]),
            int(actor_col[i]), bool(old_col[i]), bool(do_round[i]), 0,
        )
        if paid is None:
            keep[i] = False
            continue
        want_raise = choose_raise(fr, paid, int(day_col[i]), True)
        cap = clip_limit(
            fr, int(day_col[i]), paid, want_raise,
            int(hour_col[i]), int(minute_col[i]), int(second_col[i]),
        )
        if cap <= 0:
            keep[i] = False
            push_rest(
                int(day_col[i]), int(hour_col[i]), int(minute_col[i]), int(second_col[i]),
                paid, fr, tr, int(actor_col[i]), bool(old_col[i]), k, str(note_col[i]), True, True,
            )
            continue
        stats["outbound"] += 1
        if cap < paid:
            stats["split"] += 1
            room_left = int(limit_day[fr]) - int(spent[fr])
            push_rest(
                int(day_col[i]), int(hour_col[i]), int(minute_col[i]), int(second_col[i]),
                paid - cap, fr, tr, int(actor_col[i]), bool(old_col[i]), k, str(note_col[i]), False, room_left < 1_000,
            )
        fbb[i] = avail
        amount[i] = cap
        bal[fr] = avail - cap
        if tr >= 0 and tracked[tr]:
            bal[tr] += cap
    if not keep.any() and not extras:
        raise RuntimeError(
            f"Mọi giao dịch bị loại. n={n}, mở sau ts={dropped_open}, "
            f"ts0={int(ts_s[int(order[0])])}, opened0={int(opened_s[int(from_row[int(order[0])])])}"
        )
    kept = frame.loc[keep].copy()
    kept["amount"] = amount[keep]
    kept["from_balance_before"] = fbb[keep]
    kept["ts"] = ts[keep].to_numpy()
    if extras:
        kept = pd.concat([kept, pd.DataFrame(extras)], ignore_index=True)
    kept = kept.sort_values(["ts"], kind="mergesort").reset_index(drop=True)
    # Giữ thứ tự xử lý số dư: sort theo ts là ổn vì lexsort đã theo ts, và các dòng bị loại không đổi thứ tự tương đối.
    return kept.reset_index(drop=True), stats, changes


def purposes_and_channels(kept: pd.DataFrame, accounts: pd.DataFrame, customers: pd.DataFrame, age_elderly: np.ndarray, rng) -> pd.DataFrame:
    kind = kept["kind"].to_numpy()
    purpose = np.empty(len(kept), dtype=object)
    for code, options in PURPOSE.items():
        mask = kind == code
        n_mask = int(mask.sum())
        if n_mask:
            purpose[mask] = rng.choice(np.array(options, dtype=object), size=n_mask)
    transfer = kind == TRANSFER
    named = transfer & (rng.random(len(kept)) < 0.22)
    if named.any():
        holder = accounts["holder_name_ascii"].astype(str).to_numpy()
        to_row = kept["to_row"].to_numpy()
        token = np.array([holder[int(row)].split()[-1] if int(row) >= 0 else "BAN" for row in to_row[named]], dtype=object)
        purpose[named] = np.char.add(token.astype(str), " chuyen tien")
    if "note" in kept.columns:
        noted = kept["note"].fillna("").astype(str).to_numpy()
        purpose[noted != ""] = noted[noted != ""]
    purpose = blank_purpose(purpose, rng, 0.15)
    if "note" in kept.columns:
        purpose[noted != ""] = noted[noted != ""]
    actor = kept["actor"].to_numpy()
    elderly = np.zeros(len(kept), dtype=bool)
    valid = actor >= 0
    elderly[valid] = age_elderly[actor[valid]]
    draw = rng.random(len(kept))
    channel = np.empty(len(kept), dtype=object)
    # Thường: app 0.86, web 0.09, quầy 0.05. Cao tuổi: app 0.62, web 0.13, quầy 0.25.
    channel[~elderly] = np.where(draw[~elderly] < 0.86, "app", np.where(draw[~elderly] < 0.95, "web", "counter"))
    channel[elderly] = np.where(draw[elderly] < 0.62, "app", np.where(draw[elderly] < 0.75, "web", "counter"))
    kept["purpose_text"] = purpose
    kept["channel"] = channel
    return kept


def build_payees(kept: pd.DataFrame, accounts: pd.DataFrame, hist, rng, sim_start) -> pd.DataFrame:
    tracked = accounts["cidx"].to_numpy() >= 0
    from_row = kept["from_row"].to_numpy()
    to_row = kept["to_row"].to_numpy()
    mask = tracked[from_row] & (to_row >= 0) & (kept["kind"].to_numpy() != CASH)
    sub = kept.loc[mask, ["from_row", "to_row", "ts", "old_payee"]]
    grouped = sub.groupby(["from_row", "to_row"], sort=False)
    first_ts = grouped["ts"].min()
    old = grouped["old_payee"].max().astype(bool)
    payees = first_ts.reset_index()
    payees["old_payee"] = old.to_numpy()
    n = len(payees)
    added = pd.Series(payees["ts"].to_numpy(), dtype=f"datetime64[ns, {TZ}]")
    n_old = int(payees["old_payee"].sum())
    if n_old:
        old_start = as_timestamp("2019-01-01")
        span = max(int((sim_start - old_start).total_seconds()), 1)
        added.iloc[np.flatnonzero(payees["old_payee"].to_numpy())] = old_start + pd.to_timedelta(rng.integers(0, span, size=n_old), unit="s")
    payees["added_at"] = added.to_numpy()
    new = ~payees["old_payee"].to_numpy()
    f = float(new.mean()) if n else 0.0
    p_new = 0.55
    if f <= 0:
        p_old = 0.25
        p_new = 0.25
    elif f >= 1:
        p_new = 0.25
        p_old = 0.25
    elif f * p_new > 0.25:
        p_new = 0.25 / f * 0.95
        p_old = (0.25 - f * p_new) / (1 - f)
    else:
        p_old = (0.25 - f * p_new) / (1 - f)
    paste = np.zeros(n, dtype=bool)
    paste[new] = rng.random(int(new.sum())) < p_new
    paste[~new] = rng.random(int((~new).sum())) < p_old
    rest = rng.random(n)
    # Phần không paste chia contact/qr/typed theo 0.55 : 0.15 : 0.05.
    method = np.empty(n, dtype=object)
    method[paste] = "paste"
    cut_qr = 0.55 / 0.75
    cut_typed = (0.55 + 0.15) / 0.75
    method[~paste] = np.where(rest[~paste] < cut_qr, "contact", np.where(rest[~paste] < cut_typed, "qr", "typed"))
    ids_of = accounts["account_id"].astype(str).to_numpy()
    out = pd.DataFrame(
        {
            "from_account": pd.Series(ids_of[payees["from_row"].to_numpy()], dtype="string"),
            "to_account": pd.Series(ids_of[payees["to_row"].to_numpy()], dtype="string"),
            "added_at": payees["added_at"].to_numpy(),
            "add_method": pd.Series(method, dtype="string"),
        }
    )
    return out


def to_transactions(kept: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    ids_of = accounts["account_id"].astype(str).to_numpy()
    from_row = kept["from_row"].to_numpy()
    to_row = kept["to_row"].to_numpy()
    to_id = np.array([ids_of[int(row)] if int(row) >= 0 else None for row in to_row], dtype=object)
    out = pd.DataFrame(
        {
            "tx_id": pd.Series(ids("T", len(kept)), dtype="string"),
            "ts": kept["ts"].to_numpy(),
            "from_account": pd.Series(ids_of[from_row], dtype="string"),
            "to_account": pd.Series(to_id, dtype="string"),
            "amount": kept["amount"].to_numpy().astype(np.int64),
            "tx_type": pd.Series(KIND_NAME[kept["kind"].to_numpy()], dtype="string"),
            "purpose_text": pd.Series(kept["purpose_text"].to_numpy(), dtype="string"),
            "channel": pd.Series(kept["channel"].to_numpy(), dtype="string"),
            "session_id": pd.Series(pd.NA, index=np.arange(len(kept)), dtype="string"),
            "from_balance_before": kept["from_balance_before"].to_numpy().astype(np.int64),
        }
    )
    return out


def add_idle_raises(changes: list[dict], transactions: pd.DataFrame, accounts: pd.DataFrame, rng, sim_start, sim_end, idle_share: float) -> list[dict]:
    """Một phần lần nâng không gắn với khoản chi lớn trong 7 ngày sau."""
    tied = [row for row in changes if row["change_type"] == "limit_raise"]
    n_idle = int(round(len(tied) * float(idle_share) / max(1.0 - float(idle_share), 1e-6)))
    if n_idle <= 0:
        return changes
    ids = accounts["account_id"].astype(str)
    txn = dict(zip(ids, accounts["limit_per_txn"].astype(np.int64)))
    day_lim = dict(zip(ids, accounts["limit_per_day"].astype(np.int64)))
    owned = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str))
    raised = {str(row["account_id"]) for row in tied}
    outbound = transactions.loc[transactions["from_account"].astype(str).isin(owned), ["from_account", "ts", "amount", "tx_type"]].copy()
    outbound["from_account"] = outbound["from_account"].astype(str)
    large_type = {"large_oneoff", "school_fee", "savings_liquidation"}
    large = outbound.loc[(outbound["amount"] >= 20_000_000) | outbound["tx_type"].astype(str).isin(large_type)]
    large_at: dict[str, list[pd.Timestamp]] = {}
    for account, part in large.groupby("from_account", sort=False):
        large_at[str(account)] = list(part["ts"])
    too_big = set()
    if len(outbound):
        peak = outbound.groupby("from_account")["amount"].max()
        for account, amount in peak.items():
            if int(amount) > int(txn.get(str(account), 0)):
                too_big.add(str(account))
        daily = outbound.groupby([outbound["from_account"], outbound["ts"].dt.floor("D")])["amount"].sum()
        for (account, _), amount in daily.items():
            if int(amount) > int(day_lim.get(str(account), 0)):
                too_big.add(str(account))
    eligible = [account for account in owned if account not in raised and account not in too_big]
    rng.shuffle(eligible)
    span = max(int((sim_end - sim_start).total_seconds()) - 8 * 86400, 1)
    extra = []
    for account in eligible:
        if len(extra) >= n_idle:
            break
        times = large_at.get(account, [])
        stamp = None
        for _ in range(12):
            candidate = sim_start + pd.to_timedelta(int(rng.integers(0, span)), unit="s")
            if any((candidate < when) and (when <= candidate + pd.Timedelta(days=7)) for when in times):
                continue
            stamp = candidate
            break
        if stamp is None:
            continue
        old_t = int(txn[account])
        old_d = int(day_lim[account])
        new_t = old_t + 50_000_000
        new_d = max(old_d, new_t)
        extra.append(
            {
                "account_id": account,
                "ts": stamp,
                "change_type": "limit_raise",
                "old_value": format_limit(old_t, old_d),
                "new_value": format_limit(new_t, new_d),
                "channel": "app" if rng.random() < 0.7 else "quầy",
            }
        )
    return changes + extra


def build_account_changes(customers, accounts, primary, phones, limit_changes, cfg, rng, sim_start, sim_end) -> pd.DataFrame:
    hist = cfg["history"]
    n = len(customers)
    ids = accounts["account_id"].astype(str).to_numpy()
    primary_account = ids[primary]
    span = max(int((sim_end - sim_start).total_seconds()), 1)
    rows = list(limit_changes)

    n_phone = int(round(float(hist["phone_change_share"]) * n))
    if n_phone:
        who = rng.choice(n, size=n_phone, replace=False)
        msisdn = dict(zip(phones["number_id"].astype(str), phones["msisdn"].astype(str)))
        used = set(msisdn.values())
        old = customers["phone_number_id"].astype(str).map(msisdn).to_numpy()
        when = rng.integers(0, span, size=n_phone)
        channel = np.where(rng.random(n_phone) < 0.80, "app", "quầy")
        for person, offset, how in zip(who, when, channel):
            fresh = None
            for _ in range(20):
                candidate = "09" + f"{int(rng.integers(0, 100_000_000)):08d}"
                if candidate not in used:
                    fresh = candidate
                    used.add(candidate)
                    break
            if fresh is None:
                continue
            rows.append(
                {
                    "account_id": str(primary_account[int(person)]),
                    "ts": sim_start + pd.to_timedelta(int(offset), unit="s"),
                    "change_type": "phone_change",
                    "old_value": str(old[int(person)]),
                    "new_value": fresh,
                    "channel": str(how),
                }
            )

    devices = pd.read_parquet(ROOT / "out" / "observed" / "devices.parquet")
    owned = devices.loc[devices["customer_id"].notna()].copy()
    owned["customer_id"] = owned["customer_id"].astype(str)
    in_window = owned.loc[(owned["first_seen"] >= sim_start) & (owned["first_seen"] < sim_end)]
    cidx = dict(zip(customers["customer_id"].astype(str), np.arange(n)))
    retired = owned.loc[owned["last_seen"].notna(), ["customer_id", "device_id", "last_seen"]]
    previous = {
        customer: part.sort_values("last_seen").iloc[-1]["device_id"]
        for customer, part in retired.groupby("customer_id", sort=False)
    }
    for record in in_window.itertuples(index=False):
        person = cidx.get(str(record.customer_id))
        if person is None or primary[person] < 0:
            continue
        rows.append(
            {
                "account_id": str(primary_account[person]),
                "ts": record.first_seen,
                "change_type": "new_device",
                "old_value": str(previous.get(str(record.customer_id), "")),
                "new_value": str(record.device_id),
                "channel": "app",
            }
        )

    changes = pd.DataFrame(rows)
    if changes.empty:
        changes = pd.DataFrame(columns=["account_id", "ts", "change_type", "old_value", "new_value", "channel"])
    changes["account_id"] = changes["account_id"].astype("string")
    changes["ts"] = pd.to_datetime(changes["ts"], utc=True).dt.tz_convert(TZ)
    changes["change_type"] = changes["change_type"].astype("string")
    changes["old_value"] = changes["old_value"].astype("string")
    changes["new_value"] = changes["new_value"].astype("string")
    changes["channel"] = changes["channel"].astype("string")
    return changes.sort_values(["ts", "account_id"], kind="mergesort").reset_index(drop=True)


def build_bank_calls(customers, cfg, rng, sim_start, sim_end) -> pd.DataFrame:
    n = len(customers)
    per_month = float(cfg["history"]["bank_calls_per_month"])
    keep_p = float(cfg["bank_call_log_completeness"])
    n_call = int(round(per_month * n))
    dates = pd.date_range(sim_start, periods=90, freq="D")
    agents = np.array([f"AG{i:04d}" for i in range(1, 41)], dtype=object)
    purposes = np.array(["xac minh", "cham soc", "ban hang"], dtype=object)
    rows_customer = []
    rows_start = []
    rows_end = []
    rows_agent = []
    rows_purpose = []
    ids = customers["customer_id"].astype(str).to_numpy()
    for month in (7, 8, 9):
        pool = dates[dates.month == month]
        if n_call == 0 or len(pool) == 0:
            continue
        who = rng.choice(n, size=n_call, replace=False)
        day_offset = np.array([(stamp - sim_start).days for stamp in pool], dtype=np.int64)
        second = day_offset[rng.integers(0, len(pool), size=n_call)] * 86400 + rng.integers(8 * 3600, 18 * 3600, size=n_call)
        duration = rng.integers(120, 901, size=n_call)
        keep = rng.random(n_call) < keep_p
        if not keep.any():
            continue
        start = sim_start + pd.to_timedelta(second[keep], unit="s")
        rows_customer.append(ids[who[keep]])
        rows_start.append(start)
        rows_end.append(start + pd.to_timedelta(duration[keep], unit="s"))
        rows_agent.append(rng.choice(agents, size=int(keep.sum())))
        rows_purpose.append(rng.choice(purposes, size=int(keep.sum())))
    frame = pd.DataFrame(
        {
            "customer_id": pd.Series(np.concatenate(rows_customer) if rows_customer else [], dtype="string"),
            "ts_start": pd.concat([pd.Series(part) for part in rows_start], ignore_index=True) if rows_start else pd.Series(dtype=f"datetime64[ns, {TZ}]"),
            "ts_end": pd.concat([pd.Series(part) for part in rows_end], ignore_index=True) if rows_end else pd.Series(dtype=f"datetime64[ns, {TZ}]"),
            "agent_id": pd.Series(np.concatenate(rows_agent) if rows_agent else [], dtype="string"),
            "purpose": pd.Series(np.concatenate(rows_purpose) if rows_purpose else [], dtype="string"),
        }
    )
    late = frame["ts_end"] >= sim_end
    frame.loc[late, "ts_end"] = sim_end - pd.to_timedelta(1, unit="s")
    return frame.sort_values("ts_start", kind="mergesort").reset_index(drop=True)


def main() -> None:
    cfg = load_config()
    rng = np.random.default_rng(int(cfg["seed"]))
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    customers, latent, phones, accounts, age = load_world(cfg)
    primary, alt, drain_to = choose_primary(accounts, len(customers))
    if np.any(primary < 0):
        raise RuntimeError("Có khách hàng không có tài khoản personal")
    contacts, directory = build_contacts(customers, latent, phones, accounts, age, rng, sim_start, sim_end, cfg["history"])
    dates = pd.date_range(sim_start, periods=90, freq="D")
    weekend = dates.dayofweek.to_numpy() >= 5
    anchors = rng.choice(np.array([5, 10, 15, 25]), size=len(customers))
    jitter = rng.integers(-2, 3, size=(len(customers), 3))
    day_numbers = anchors[:, None] + jitter
    table = np.full((13, 32), -1, dtype=np.int16)
    for index, stamp in enumerate(dates):
        table[stamp.month, stamp.day] = index
    payday = np.empty((len(customers), 3), dtype=np.int16)
    for column, month in enumerate((7, 8, 9)):
        payday[:, column] = table[month, day_numbers[:, column]]
    frame, young_ids = build_events(customers, latent, accounts, age, primary, alt, drain_to, directory, cfg, rng, sim_start, weekend, payday)
    sme_customer = customers["segment"].eq("doanh nghiệp nhỏ").to_numpy()
    cidx = accounts["cidx"].to_numpy()
    sme_account = np.zeros(len(accounts), dtype=bool)
    known = cidx >= 0
    sme_account[known] = sme_customer[cidx[known]]
    hist = cfg["history"]
    kept, stats, limit_changes = apply_balances(
        frame, accounts, sim_start, rng, sme_account,
        float(hist["over_limit_split_share"]), float(hist["over_limit_split_share_sme"]),
    )
    kept = purposes_and_channels(kept, accounts, customers, customers["segment"].eq("cao tuổi").to_numpy(), rng)
    # apply_balances sắp theo ts nhưng thứ tự xử lý số dư dùng lexsort. Gán tx sau khi sort theo ts rồi seq xử lý.
    transactions = to_transactions(kept, accounts)
    limit_changes = add_idle_raises(
        limit_changes, transactions, accounts, rng, sim_start, sim_end, float(hist["limit_raise_idle_share"]),
    )
    payees = build_payees(kept, accounts, cfg["history"], rng, sim_start)
    if young_ids:
        opened_at = dict(zip(accounts["account_id"].astype(str), accounts["opened_at"]))
        young_mask = payees["to_account"].astype(str).isin(set(young_ids))
        payees.loc[young_mask, "added_at"] = payees.loc[young_mask, "to_account"].astype(str).map(opened_at) + pd.to_timedelta(1, unit="D")
    changes = build_account_changes(
        customers, accounts, primary, phones, limit_changes, cfg, rng, sim_start, sim_end,
    )
    calls = build_bank_calls(customers, cfg, rng, sim_start, sim_end)
    contacts_path = write(contacts.drop(columns=[], errors="ignore"), "truth", "contacts")
    account_path = write(accounts.drop(columns=["row", "cidx", "category"], errors="ignore"), "observed", "accounts")
    tx_path = write(transactions, "observed", "transactions")
    payee_path = write(payees, "observed", "payees")
    change_path = write(changes, "observed", "account_changes")
    call_path = write(calls, "observed", "bank_calls")
    split_rate = stats["split"] / stats["outbound"] if stats["outbound"] else 0.0
    over_rate = stats["over_split"] / stats["over"] if stats["over"] else 0.0
    sme_rate = stats["over_sme_split"] / stats["over_sme"] if stats["over_sme"] else 0.0
    loan_customers = set(transactions.loc[transactions["tx_type"].eq("quick_loan_disbursement"), "to_account"].astype(str))
    card_customers = set(transactions.loc[transactions["tx_type"].eq("credit_card_cash_advance"), "to_account"].astype(str))
    print(f"Đã sinh {len(contacts)} liên hệ, {len(transactions)} giao dịch, {len(payees)} người nhận.")
    print(f"  Giao dịch hợp lệ bị chia do hạn mức: {stats['split']}/{stats['outbound']} ({split_rate:.2%})")
    print(f"  Trong lệnh vượt hạn mức, chia nhỏ: {stats['over_split']}/{stats['over']} ({over_rate:.2%}); SME {stats['over_sme_split']}/{stats['over_sme']} ({sme_rate:.2%})")
    print(f"  Khách trùng vay nhanh và rút thẻ: {len(loan_customers & card_customers)}")
    print(f"  contacts: {contacts_path}")
    print(f"  accounts: {account_path}")
    print(f"  transactions: {tx_path}")
    print(f"  payees: {payee_path}")
    print(f"  account_changes: {change_path}")
    print(f"  bank_calls: {call_path}")


if __name__ == "__main__":
    main()
