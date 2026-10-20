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

TZ = "Asia/Ho_Chi_Minh"
SCHOOL_AGE = 22
# Lớp 1, lớp 6, lớp 10 và năm nhất đại học.
ENTRY_AGES = (6, 11, 15, 18)
TX_SIGMA = 0.7
# kind
SALARY, BUSINESS_IN, BILL, RENT, CARD, CASH, TRANSFER, OWN, SCHOOL, LARGE, DRAIN, PASS_IN, PASS_OUT = range(13)
KIND_NAME = np.array(
    [
        "salary_in", "business_in", "bill_payment", "rent", "card_payment", "cash_withdrawal",
        "transfer", "own_transfer", "school_fee", "large_oneoff", "own_transfer_drain",
        "passthrough_in", "passthrough_out",
    ],
    dtype=object,
)
INBOUND = {SALARY, BUSINESS_IN, PASS_IN}
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
    multi = np.flatnonzero((drain_to >= 0) & (primary >= 0) & (drain_to != primary) & ~np.isin(cidx, large_who))
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
    return frame


def apply_balances(frame: pd.DataFrame, accounts: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
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
            (ts_i, seq, int(day_i), int(amt), int(fr), int(tr), int(actor_i), int(hour_i), int(minute_i), int(second_i), bool(old_i), int(attempts)),
        )
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
        if k == LARGE and amt < 50_000_000:
            return None
        return int(amt)

    pos = 0
    n_order = len(order)
    while pos < n_order or deferred:
        next_orig = int(ts_s[order[pos]]) if pos < n_order else 2**62
        if deferred and deferred[0][0] < next_orig:
            ts_i, _, day_i, amt, fr, tr, actor_i, hour_i, minute_i, second_i, old_i, attempts = heapq.heappop(deferred)
            if ts_i < opened_s[fr] or (tr >= 0 and ts_i < opened_s[tr]):
                dropped_open += 1
                continue
            avail = int(bal[fr])
            paid = apply_outbound(fr, tr, SCHOOL, amt, avail, day_i, hour_i, minute_i, second_i, actor_i, old_i, False, attempts)
            if paid is None:
                continue
            bal[fr] = avail - paid
            if tr >= 0 and tracked[tr]:
                bal[tr] += paid
            extras.append(
                {
                    "from_row": fr,
                    "to_row": tr,
                    "actor": actor_i,
                    "amount": paid,
                    "kind": SCHOOL,
                    "day": day_i,
                    "hour": hour_i,
                    "minute": minute_i,
                    "second": second_i,
                    "old_payee": old_i,
                    "do_round": False,
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
            if amt <= (avail * 80) // 100 or amt < 1_000 or tr < 0:
                keep[i] = False
                continue
            amount[i] = amt
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
        fbb[i] = avail
        amount[i] = paid
        bal[fr] = avail - paid
        if tr >= 0 and tracked[tr]:
            bal[tr] += paid
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
    return kept.reset_index(drop=True)


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
    purpose = blank_purpose(purpose, rng, 0.15)
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
    frame = build_events(customers, latent, accounts, age, primary, alt, drain_to, directory, cfg, rng, sim_start, weekend, payday)
    kept = apply_balances(frame, accounts, sim_start)
    kept = purposes_and_channels(kept, accounts, customers, customers["segment"].eq("cao tuổi").to_numpy(), rng)
    # apply_balances sắp theo ts nhưng thứ tự xử lý số dư dùng lexsort. Gán tx sau khi sort theo ts rồi seq xử lý.
    transactions = to_transactions(kept, accounts)
    payees = build_payees(kept, accounts, cfg["history"], rng, sim_start)
    contacts_path = write(contacts.drop(columns=[], errors="ignore"), "truth", "contacts")
    tx_path = write(transactions, "observed", "transactions")
    payee_path = write(payees, "observed", "payees")
    print(f"Đã sinh {len(contacts)} liên hệ, {len(transactions)} giao dịch, {len(payees)} người nhận.")
    print(f"  contacts: {contacts_path}")
    print(f"  transactions: {tx_path}")
    print(f"  payees: {payee_path}")


if __name__ == "__main__":
    main()
