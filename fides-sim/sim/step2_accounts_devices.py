"""Bước 2: tài khoản, thiết bị và liên kết đăng nhập.

Đọc dân số bước 1 và config. Cùng `seed` cho cùng kết quả. Không sinh tài khoản mule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.io import write
from sim.names import sample_name_body, surname_table, to_name_ascii

TZ = "Asia/Ho_Chi_Minh"
TALKBACK_ELDERLY_SHARE = 0.02
PASSWORD_MANAGER_SHARE = 0.05
REMOTE_ACCESS_SHARE = 0.01
SCHOOL_HOSPITAL_VERIFIED = 0.98
MERCHANT_TURNOVER_MEDIAN = 40_000_000
MERCHANT_TURNOVER_SIGMA = 0.8

MERCHANT_MIX = {
    "school": 0.18,
    "hospital": 0.12,
    "developer": 0.15,
    "store": 0.25,
    "company": 0.30,
}
SCHOOL_PREFIX = np.array(["TRUONG THPT", "TRUONG THCS", "TRUONG TIEU HOC", "DAI HOC"])
NAME_STEMS = np.array(
    [
        "NGUYEN HUE", "LE QUY DON", "TRAN PHU", "CHU VAN AN", "NGUYEN TRAI", "LE HONG PHONG",
        "QUANG TRUNG", "HUNG VUONG", "LY TU TRONG", "PHAN CHAU TRINH", "MINH PHAT", "HOANG GIA",
        "THANH CONG", "KIM OANH", "NAM VIET", "DONG A", "BAC SON", "PHUONG NAM", "AN BINH",
        "HUNG THINH", "THAI BINH", "HOA MAI", "SON TINH", "VAN LANG", "HONG HA", "BINH MINH",
        "DONG DO", "TAY DO", "MEKONG", "TRUONG SON",
    ]
)


def load_config(path: Path | None = None) -> dict:
    path = path or (ROOT / "config.yaml")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def as_timestamp(value) -> pd.Timestamp:
    stamp = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(TZ)
    else:
        stamp = stamp.tz_convert(TZ)
    return stamp


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


def ids(prefix: str, n: int, width: int = 7) -> np.ndarray:
    return np.char.add(prefix, np.char.zfill(np.arange(1, n + 1).astype(str), width))


def repeat_labels(counts: dict, rng: np.random.Generator) -> np.ndarray:
    parts = [np.full(count, key, dtype=object) for key, count in counts.items() if count]
    labels = np.concatenate(parts) if parts else np.array([], dtype=object)
    rng.shuffle(labels)
    return labels


def choose_free_bank(used: np.ndarray, log_weights: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Mỗi hàng chọn một ngân hàng chưa dùng, theo trọng số log_weights."""
    draw = np.clip(rng.random(used.shape), 1e-12, 1.0 - 1e-12)
    score = log_weights + (-np.log(-np.log(draw)))
    score = np.where(used, -np.inf, score)
    if np.any(np.isneginf(score).all(axis=1)):
        raise RuntimeError("Có khách hàng không còn ngân hàng để mở thêm tài khoản")
    return score.argmax(axis=1)


def mix_timestamps(n: int, in_window: np.ndarray, sim_start, sim_end, rng) -> pd.Series:
    stamps = pd.Series(pd.NaT, index=np.arange(n), dtype=f"datetime64[ns, {TZ}]")
    n_new = int(in_window.sum())
    if n_new:
        span = int((sim_end - sim_start).total_seconds())
        stamps.iloc[np.flatnonzero(in_window)] = sim_start + pd.to_timedelta(rng.integers(0, span, size=n_new), unit="s")
    old_idx = np.flatnonzero(~in_window)
    if len(old_idx):
        old_start = as_timestamp("2012-01-01")
        old_end = as_timestamp("2023-01-01")
        span = int((old_end - old_start).total_seconds())
        stamps.iloc[old_idx] = old_start + pd.to_timedelta(rng.integers(0, span, size=len(old_idx)), unit="s")
    return stamps


def lognormal_money(median: np.ndarray, sigma: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    shock = rng.standard_normal(len(median))
    return np.rint(np.exp(np.log(median) + sigma * shock)).astype(np.int64)


def balance_for(income: np.ndarray, elderly: np.ndarray, spec: dict, rng) -> np.ndarray:
    median = np.where(elderly, float(spec["elderly"]["median"]), float(spec["default"]["median"]))
    sigma = np.where(elderly, float(spec["elderly"]["sigma"]), float(spec["default"]["sigma"]))
    multiple = np.exp(np.log(median) + sigma * rng.standard_normal(len(income)))
    return np.rint(income.astype(float) * multiple).astype(np.int64)


def load_population():
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    customers = pd.read_parquet(observed / "customers.parquet").sort_values("customer_id").reset_index(drop=True)
    latent = pd.read_parquet(truth / "customers_latent.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    people = customers.merge(
        latent[["customer_id", "family_group_id", "income_monthly", "activity_level", "role"]],
        on="customer_id",
        how="left",
        validate="one_to_one",
    )
    if people[["income_monthly", "role", "family_group_id"]].isna().any().any():
        raise RuntimeError("Thiếu bảng truth bước 1 cho một số khách hàng")
    return people, phones


def assign_personal_banks(n_acc: np.ndarray, banks: list[str], bank_share: dict, home: str, rng):
    """Ngân hàng chính theo bank_share. Tài khoản thêm không dùng home bank nếu chưa có."""
    n = len(n_acc)
    n_banks = len(banks)
    bank_index = {bank: i for i, bank in enumerate(banks)}
    home_i = bank_index[home]
    primary = repeat_labels(largest_remainder(banks, [bank_share[bank] for bank in banks], n), rng)
    primary_i = np.array([bank_index[bank] for bank in primary], dtype=int)
    personal = np.full((n, 3), -1, dtype=int)
    personal[:, 0] = primary_i
    used = np.zeros((n, n_banks), dtype=bool)
    used[np.arange(n), primary_i] = True
    used[:, home_i] = True
    log_w = np.log(np.array([float(bank_share[bank]) for bank in banks]))

    extra1 = choose_free_bank(used, log_w, rng)
    take2 = n_acc >= 2
    personal[take2, 1] = extra1[take2]
    used[np.arange(n)[take2], extra1[take2]] = True
    extra2 = choose_free_bank(used, log_w, rng)
    take3 = n_acc >= 3
    personal[take3, 2] = extra2[take3]
    used[np.arange(n)[take3], extra2[take3]] = True
    return personal, used


def product_bank(has_product: np.ndarray, used: np.ndarray, log_w: np.ndarray, rng) -> np.ndarray:
    choice = choose_free_bank(used, log_w, rng)
    chosen = np.full(len(has_product), -1, dtype=int)
    chosen[has_product] = choice[has_product]
    used[np.arange(len(has_product))[has_product], choice[has_product]] = True
    return chosen


def offspring_map(people: pd.DataFrame) -> dict[str, list[str]]:
    """Con trong cùng hộ: con của chủ hộ/vợ chồng, hoặc chủ hộ nếu người đó là cha mẹ."""
    by_family: dict[str, list[tuple[str, str]]] = {}
    for customer_id, family, role in zip(people["customer_id"], people["family_group_id"], people["role"]):
        by_family.setdefault(family, []).append((customer_id, role))
    children: dict[str, list[str]] = {}
    for members in by_family.values():
        kids = [customer_id for customer_id, role in members if role == "child"]
        heads = [customer_id for customer_id, role in members if role == "head"]
        for customer_id, role in members:
            if role in {"head", "spouse"}:
                children[customer_id] = kids
            elif role == "parent" and heads:
                children[customer_id] = heads
            else:
                children[customer_id] = []
    return children


def build_customer_accounts(people, cfg_accounts, rng):
    n = len(people)
    banks = [str(bank) for bank in cfg_accounts["banks"]]
    share = {str(k): float(v) for k, v in cfg_accounts["bank_share"].items()}
    if list(share) != banks:
        raise RuntimeError("bank_share phải đi cùng thứ tự và tập banks")
    home = str(cfg_accounts["home_bank"])
    per = {int(k): float(v) for k, v in cfg_accounts["accounts_per_customer"].items()}
    n_acc = repeat_labels(largest_remainder([1, 2, 3], [per[1], per[2], per[3]], n), rng).astype(int)
    personal, used = assign_personal_banks(n_acc, banks, share, home, rng)
    log_w = np.log(np.array([share[bank] for bank in banks]))

    has_savings = np.zeros(n, dtype=bool)
    has_savings[rng.choice(n, size=int(round(float(cfg_accounts["savings_share"]) * n)), replace=False)] = True
    has_securities = np.zeros(n, dtype=bool)
    has_securities[rng.choice(n, size=int(round(float(cfg_accounts["securities_share"]) * n)), replace=False)] = True
    savings_i = product_bank(has_savings, used, log_w, rng)
    securities_i = product_bank(has_securities, used, log_w, rng)
    is_business = people["segment"].to_numpy() == "doanh nghiệp nhỏ"
    business_i = product_bank(is_business, used, log_w, rng)

    rows_customer = []
    rows_bank = []
    rows_type = []
    for slot, label in ((0, "personal"), (1, "personal"), (2, "personal")):
        take = personal[:, slot] >= 0
        rows_customer.append(np.flatnonzero(take))
        rows_bank.append(personal[take, slot])
        rows_type.append(np.full(int(take.sum()), label, dtype=object))
    for mask, bank_i, label in (
        (has_savings, savings_i, "savings"),
        (has_securities, securities_i, "securities"),
        (is_business, business_i, "business"),
    ):
        take = np.flatnonzero(mask)
        rows_customer.append(take)
        rows_bank.append(bank_i[take])
        rows_type.append(np.full(len(take), label, dtype=object))

    customer_row = np.concatenate(rows_customer)
    bank_i = np.concatenate(rows_bank)
    account_type = np.concatenate(rows_type)
    holder = people["name_ascii"].to_numpy()[customer_row]
    income = people["income_monthly"].to_numpy()[customer_row]
    age = int(as_timestamp(load_config()["sim_start"]).year) - people["birth_year"].to_numpy()
    elderly = age >= int(load_config()["population"]["elderly_age"])
    return {
        "customer_row": customer_row,
        "customer_id": people["customer_id"].to_numpy()[customer_row],
        "bank_id": np.array(banks, dtype=object)[bank_i],
        "account_type": account_type,
        "holder_name_ascii": holder,
        "income": income,
        "elderly": elderly[customer_row],
        "n_products": np.bincount(customer_row, minlength=n),
        "activity": people["activity_level"].to_numpy(),
        "people": people,
    }


def build_external_accounts(phones, banks, share, share_with_account, rng):
    external = phones.loc[phones["owner_type"] == "external_person", "number_id"].sort_values().to_numpy()
    n_take = int(round(float(share_with_account) * len(external)))
    chosen = rng.choice(external, size=n_take, replace=False)
    bank = rng.choice(np.array(banks, dtype=object), size=n_take, p=[share[b] for b in banks])
    male = rng.random(n_take) < 0.5
    young = rng.random(n_take) < 0.33
    body = sample_name_body(rng, male, young)
    surnames, weights = surname_table()
    surname = rng.choice(surnames, size=n_take, p=weights)
    full = np.char.add(np.char.add(surname.astype(str), " "), body.astype(str))
    holder = np.array([to_name_ascii(name) for name in full], dtype=object)
    income_median = np.full(n_take, 9_000_000.0)
    income = lognormal_money(income_median, np.full(n_take, 0.6), rng)
    return chosen, bank, holder, income


def build_merchants(n_merchants: int, verified_share: float, banks, share, rng):
    mix = largest_remainder(list(MERCHANT_MIX), list(MERCHANT_MIX.values()), n_merchants)
    kinds = repeat_labels(mix, rng)
    stem = rng.choice(NAME_STEMS, size=n_merchants)
    school_prefix = rng.choice(SCHOOL_PREFIX, size=n_merchants)
    names = np.empty(n_merchants, dtype=object)
    school = kinds == "school"
    hospital = kinds == "hospital"
    developer = kinds == "developer"
    store = kinds == "store"
    company = kinds == "company"
    names[school] = np.char.add(np.char.add(school_prefix[school].astype(str), " "), stem[school].astype(str))
    names[hospital] = np.char.add("BENH VIEN ", stem[hospital].astype(str))
    names[developer] = np.char.add("CHU DAU TU ", stem[developer].astype(str))
    names[store] = np.char.add("CUA HANG ", stem[store].astype(str))
    names[company] = np.char.add("CONG TY TNHH ", stem[company].astype(str))

    verified = np.zeros(n_merchants, dtype=bool)
    protected = school | hospital
    n_protected = int(round(SCHOOL_HOSPITAL_VERIFIED * int(protected.sum())))
    if n_protected:
        verified[rng.choice(np.flatnonzero(protected), size=n_protected, replace=False)] = True
    n_target = int(round(float(verified_share) * n_merchants))
    n_rest = n_target - int(verified.sum())
    others = np.flatnonzero(~protected & ~verified)
    if n_rest < 0 or n_rest > len(others):
        raise RuntimeError("Không khớp được tỷ lệ merchant verified")
    if n_rest:
        verified[rng.choice(others, size=n_rest, replace=False)] = True
    bank = rng.choice(np.array(banks, dtype=object), size=n_merchants, p=[share[b] for b in banks])
    turnover = lognormal_money(np.full(n_merchants, MERCHANT_TURNOVER_MEDIAN), np.full(n_merchants, MERCHANT_TURNOVER_SIGMA), rng)
    return names, bank, verified, turnover, kinds


def assemble_accounts(people, phones, cfg, rng):
    block = cfg["accounts"]
    banks = [str(bank) for bank in block["banks"]]
    share = {str(k): float(v) for k, v in block["bank_share"].items()}
    customer = build_customer_accounts(people, block, rng)
    ext_phone, ext_bank, ext_holder, ext_income = build_external_accounts(
        phones, banks, share, block["external_with_account_share"], rng
    )
    merch_name, merch_bank, merch_verified, merch_income, merch_kind = build_merchants(
        int(block["n_merchants"]), float(block["merchant_verified_share"]), banks, share, rng
    )
    n_customer = len(customer["customer_id"])
    n_ext = len(ext_phone)
    n_merch = len(merch_name)
    n = n_customer + n_ext + n_merch

    customer_id = np.full(n, None, dtype=object)
    customer_id[:n_customer] = customer["customer_id"]
    phone_number_id = np.full(n, None, dtype=object)
    phone_number_id[n_customer : n_customer + n_ext] = ext_phone
    bank_id = np.concatenate([customer["bank_id"], ext_bank, merch_bank])
    account_type = np.concatenate([
        customer["account_type"],
        np.full(n_ext, "personal", dtype=object),
        np.full(n_merch, "business", dtype=object),
    ])
    holder = np.concatenate([customer["holder_name_ascii"], ext_holder, merch_name])
    income = np.concatenate([customer["income"], ext_income, merch_income])
    elderly = np.concatenate([
        customer["elderly"],
        np.zeros(n_ext + n_merch, dtype=bool),
    ])
    verified = np.zeros(n, dtype=bool)
    verified[n_customer + n_ext :] = merch_verified
    customer_row = np.full(n, -1, dtype=int)
    customer_row[:n_customer] = customer["customer_row"]
    age_for_weight = np.ones(n, dtype=float)
    young = customer["people"]["birth_year"].to_numpy()
    ref_year = int(as_timestamp(cfg["sim_start"]).year)
    customer_age = ref_year - young
    age_for_weight[:n_customer] = np.where(customer_age[customer["customer_row"]] <= 30, 8.0, 1.0)
    age_for_weight[n_customer:] = 0.35

    balance = balance_for(income, elderly, block["balance_multiple_of_income"], rng)
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    n_new = int(round(float(block["new_account_in_window_share"]) * n))
    probabilities = age_for_weight / age_for_weight.sum()
    new_idx = rng.choice(n, size=n_new, replace=False, p=probabilities)
    in_window = np.zeros(n, dtype=bool)
    in_window[new_idx] = True
    opened_at = mix_timestamps(n, in_window, sim_start, sim_end, rng)

    accounts = pd.DataFrame(
        {
            "account_id": pd.Series(ids("A", n), dtype="string"),
            "customer_id": pd.Series(customer_id, dtype="string"),
            "phone_number_id": pd.Series(phone_number_id, dtype="string"),
            "bank_id": pd.Series(bank_id, dtype="string"),
            "account_type": pd.Series(account_type, dtype="string"),
            "holder_name_ascii": pd.Series(holder, dtype="string"),
            "opened_at": opened_at,
            "opening_balance": balance,
            "status": pd.Series(np.full(n, "active", dtype=object), dtype="string"),
            "merchant_verified": verified,
        }
    )
    return accounts, customer, merch_kind


def build_devices(people, block, cfg, rng):
    """Hai cờ độc lập: 8% dùng hai máy song song, 5% đổi một máy trong cửa sổ.

    Khách chỉ đổi máy có máy cũ (last_seen) và máy mới. Khách vừa dùng hai máy vừa đổi
    thì giữ máy chính, ngừng máy thứ hai và thêm máy thay thế.
    """
    n = len(people)
    parallel = np.zeros(n, dtype=bool)
    replace = np.zeros(n, dtype=bool)
    n_parallel = int(round(float(block["second_device_share"]) * n))
    n_replace = int(round(float(block["new_device_in_window_share"]) * n))
    parallel[rng.choice(n, size=n_parallel, replace=False)] = True
    replace[rng.choice(n, size=n_replace, replace=False)] = True

    parallel_rows = np.flatnonzero(parallel)
    replace_rows = np.flatnonzero(replace)
    # slot 0: máy đang dùng chính. slot 1: máy song song đang dùng. slot 2: máy đã ngừng.
    owner_row = np.concatenate([np.arange(n), parallel_rows, replace_rows])
    slot = np.concatenate([
        np.zeros(n, dtype=int),
        np.ones(len(parallel_rows), dtype=int),
        np.full(len(replace_rows), 2, dtype=int),
    ])
    n_devices = len(owner_row)
    in_window = np.zeros(n_devices, dtype=bool)
    # Chỉ đổi máy: máy chính là máy mới. Vừa song song vừa đổi: máy thứ hai là máy mới.
    in_window[np.flatnonzero(replace & ~parallel)] = True
    second_pos = n + np.arange(len(parallel_rows))
    in_window[second_pos[replace[parallel_rows]]] = True

    os_counts = largest_remainder(["Android", "iOS"], [float(block["android_share"]), 1.0 - float(block["android_share"])], n_devices)
    os_name = repeat_labels(os_counts, rng)
    rooted = np.zeros(n_devices, dtype=bool)
    n_rooted = int(round(float(block["rooted_share"]) * n_devices))
    android_idx = np.flatnonzero(os_name == "Android")
    if n_rooted > len(android_idx):
        raise RuntimeError("rooted_share lớn hơn số máy Android")
    rooted[rng.choice(android_idx, size=n_rooted, replace=False)] = True

    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    first_seen = mix_timestamps(n_devices, in_window, sim_start, sim_end, rng)
    last_seen = pd.Series(pd.NaT, index=np.arange(n_devices), dtype=f"datetime64[ns, {TZ}]")
    new_index = np.arange(n)
    new_index[parallel_rows] = second_pos
    gap = rng.integers(1, 7 * 24 * 3600, size=len(replace_rows))
    retired_at = first_seen.iloc[new_index[replace_rows]].reset_index(drop=True) - pd.to_timedelta(gap, unit="s")
    retired_pos = n + len(parallel_rows) + np.arange(len(replace_rows))
    last_seen.iloc[retired_pos] = retired_at.to_numpy()

    devices = pd.DataFrame(
        {
            "device_id": pd.Series(ids("D", n_devices), dtype="string"),
            "customer_id": pd.Series(people["customer_id"].to_numpy()[owner_row], dtype="string"),
            "os": pd.Series(os_name, dtype="string"),
            "is_rooted": rooted,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }
    )
    devices["_owner_row"] = owner_row
    devices["_slot"] = slot
    return devices, parallel


def build_links(accounts, devices, people, activity, block, rng):
    customer_accounts = accounts["customer_id"].notna()
    owned = accounts.loc[customer_accounts, ["account_id", "customer_id", "account_type"]].copy()
    row_of = pd.Series(np.arange(len(people)), index=people["customer_id"].to_numpy())
    owned["_row"] = owned["customer_id"].map(row_of).to_numpy()
    owned = owned.sort_values(["_row", "account_type", "account_id"], kind="mergesort").reset_index(drop=True)
    owned["_rank"] = owned.groupby("_row", sort=False).cumcount()
    sizes = owned.groupby("_row", sort=False).size()
    second_rows = devices.loc[devices["_slot"] == 1, "_owner_row"].to_numpy()
    n_dev = np.ones(len(people), dtype=int)
    n_dev[second_rows] = 2
    split = np.ceil(sizes.to_numpy() / n_dev[sizes.index.to_numpy()]).astype(int)
    split = pd.Series(split, index=sizes.index)
    owned["_split"] = owned["_row"].map(split)
    owned["_slot"] = (owned["_rank"] >= owned["_split"]).astype(int)
    key = {(int(row), int(slot)): device_id for row, slot, device_id in zip(devices["_owner_row"], devices["_slot"], devices["device_id"])}
    owned["device_id"] = [key[(int(row), int(slot))] for row, slot in zip(owned["_row"], owned["_slot"])]

    links = owned[["account_id", "device_id", "customer_id", "account_type"]].copy()
    links = attach_unused_second_devices(links, devices)
    links = share_with_children(links, devices, people, block, rng)
    holder_row = links["customer_id"].map(row_of).to_numpy()
    login = np.rint(2.0 + 150.0 * activity[holder_row] + rng.normal(0.0, 4.0, size=len(links)))
    links["login_count"] = np.clip(login, 1, None).astype(np.int64)
    out = links[["account_id", "device_id", "login_count"]].reset_index(drop=True)
    return out


def attach_unused_second_devices(links: pd.DataFrame, devices: pd.DataFrame) -> pd.DataFrame:
    """Máy song song hoặc máy đã ngừng, nếu chưa có tài khoản, nhận tài khoản personal."""
    second = devices.loc[devices["_slot"] >= 1, ["device_id", "customer_id"]]
    missing = second.loc[~second["device_id"].isin(set(links["device_id"]))]
    if missing.empty:
        return links
    first = links.loc[links["account_type"] == "personal", ["customer_id", "account_id", "account_type"]].drop_duplicates("customer_id")
    extra = missing.merge(first, on="customer_id", how="inner")
    return pd.concat([links, extra[["account_id", "device_id", "customer_id", "account_type"]]], ignore_index=True)


def share_with_children(links, devices, people, block, rng) -> pd.DataFrame:
    ref_year = int(people.attrs.get("ref_year", as_timestamp(load_config()["sim_start"]).year))
    elderly_age = int(load_config()["population"]["elderly_age"])
    age = ref_year - people["birth_year"].to_numpy()
    elderly_ids = people.loc[age >= elderly_age, "customer_id"].to_numpy()
    quota = int(round(float(block["family_shared_device_share"]) * len(elderly_ids)))
    children = offspring_map(people)
    active_devices = devices.loc[devices["_slot"] < 2]
    devices_of = active_devices.groupby("customer_id")["device_id"].apply(list).to_dict()
    holders = {device_id: set(group["customer_id"]) for device_id, group in links.groupby("device_id", sort=False)}
    personal = (
        links.loc[links["account_type"] == "personal", ["account_id", "customer_id"]]
        .drop_duplicates("customer_id")
        .set_index("customer_id")["account_id"]
        .to_dict()
    )
    order = rng.permutation(len(elderly_ids))
    extra_accounts = []
    extra_devices = []
    placed = 0
    for index in order:
        if placed >= quota:
            break
        elder = elderly_ids[index]
        account_id = personal.get(elder)
        if account_id is None:
            continue
        candidates = []
        for child in children.get(elder, []):
            for device_id in devices_of.get(child, []):
                current = holders.get(device_id, set())
                if len(current | {elder}) <= 2:
                    candidates.append(device_id)
        if not candidates:
            continue
        device_id = sorted(candidates)[0]
        extra_accounts.append(account_id)
        extra_devices.append(device_id)
        holders[device_id] = holders.get(device_id, set()) | {elder}
        placed += 1
    if placed < quota:
        raise RuntimeError(f"Chỉ gắn được {placed}/{quota} thiết bị dùng chung trong hộ")
    if placed:
        owner_of = links.drop_duplicates("account_id").set_index("account_id")["customer_id"]
        addition = pd.DataFrame(
            {
                "account_id": extra_accounts,
                "device_id": extra_devices,
                "customer_id": owner_of.loc[extra_accounts].to_numpy(),
                "account_type": "personal",
            }
        )
        links = pd.concat([links, addition], ignore_index=True)
    return links


def latent_devices(devices: pd.DataFrame, people: pd.DataFrame, rng) -> pd.DataFrame:
    ref_year = int(as_timestamp(load_config()["sim_start"]).year)
    elderly_age = int(load_config()["population"]["elderly_age"])
    age = pd.Series(ref_year - people["birth_year"].to_numpy(), index=people["customer_id"].to_numpy())
    owner_elderly = devices["customer_id"].map(age).to_numpy() >= elderly_age
    n = len(devices)
    access = np.empty(n, dtype=object)
    access[:] = [[] for _ in range(n)]
    elderly_idx = np.flatnonzero(owner_elderly)
    n_talk = int(round(TALKBACK_ELDERLY_SHARE * len(elderly_idx)))
    talk_idx = rng.choice(elderly_idx, size=n_talk, replace=False) if n_talk else np.array([], dtype=int)
    for index in talk_idx:
        access[index] = ["talkback"]
    remaining = np.flatnonzero(~np.isin(np.arange(n), talk_idx))
    n_password = int(round(PASSWORD_MANAGER_SHARE * n))
    if n_password > len(remaining):
        raise RuntimeError("Không đủ thiết bị cho password manager sau TalkBack")
    password_idx = rng.choice(remaining, size=n_password, replace=False)
    for index in password_idx:
        access[index] = ["password_manager"]
    remote = np.zeros(n, dtype=bool)
    n_remote = int(round(REMOTE_ACCESS_SHARE * n))
    remote[rng.choice(n, size=n_remote, replace=False)] = True
    return pd.DataFrame(
        {
            "device_id": devices["device_id"],
            "baseline_accessibility": pd.Series(access),
            "remote_access_app_installed": remote,
        }
    )


def _sanity(accounts, devices, links, latent, people, cfg) -> None:
    home = str(cfg["accounts"]["home_bank"])
    personal = accounts[(accounts["account_type"] == "personal") & accounts["customer_id"].notna()]
    holders = set(personal["customer_id"])
    if holders != set(people["customer_id"]):
        raise RuntimeError("Có khách hàng không có tài khoản personal")
    with_home = personal.loc[personal["bank_id"] == home, "customer_id"].nunique()
    # Savings/securities không được đẩy thêm khách vào home bank.
    any_home = accounts.loc[accounts["bank_id"] == home, "customer_id"].dropna().nunique()
    if any_home != with_home or abs(with_home / len(people) - float(cfg["accounts"]["bank_share"][home])) > 1e-9:
        raise RuntimeError(f"Tỷ lệ khách có tài khoản {home} = {with_home / len(people):.4f}")
    banks = personal.groupby("customer_id")["bank_id"].nunique()
    counts = personal.groupby("customer_id").size()
    if not banks.eq(counts).all():
        raise RuntimeError("Tài khoản personal của một khách bị trùng ngân hàng")
    name = people.set_index("customer_id")["name_ascii"]
    owned = accounts.dropna(subset=["customer_id"])
    if not owned["holder_name_ascii"].eq(owned["customer_id"].map(name)).all():
        raise RuntimeError("holder_name_ascii không khớp name_ascii của chủ")
    if accounts["opening_balance"].dtype != np.int64 or links["login_count"].dtype != np.int64:
        raise RuntimeError("Tiền hoặc login_count không phải int64")
    sim_end = as_timestamp(cfg["sim_end"])
    if accounts["opened_at"].max() >= sim_end or devices["first_seen"].max() >= sim_end:
        raise RuntimeError("opened_at hoặc first_seen vượt sim_end")
    if set(accounts["status"]) != {"active"}:
        raise RuntimeError("Bước 2 không được có tài khoản khác active")
    observed = set(accounts.columns) | set(devices.drop(columns=["_owner_row", "_slot"]).columns) | set(links.columns)
    truth_cols = set(pd.read_parquet(ROOT / "out" / "truth" / "customers_latent.parquet").columns) | set(latent.columns)
    leaked = (observed & truth_cols) - {"customer_id", "device_id"}
    if leaked:
        raise RuntimeError(f"Cột observed trùng truth ngoài khóa: {leaked}")


def main() -> None:
    cfg = load_config()
    rng = np.random.default_rng(int(cfg["seed"]))
    people, phones = load_population()
    accounts, customer, _kinds = assemble_accounts(people, phones, cfg, rng)
    devices, _second = build_devices(people, cfg["accounts"], cfg, rng)
    links = build_links(accounts, devices, people, customer["activity"], cfg["accounts"], rng)
    latent = latent_devices(devices, people, rng)
    _sanity(accounts, devices, links, latent, people, cfg)
    devices_out = devices.drop(columns=["_owner_row", "_slot"])
    paths = {
        "accounts": write(accounts, "observed", "accounts"),
        "devices": write(devices_out, "observed", "devices"),
        "account_device": write(links, "observed", "account_device"),
        "devices_latent": write(latent, "truth", "devices_latent"),
    }
    print(f"Đã sinh {len(accounts)} tài khoản, {len(devices_out)} thiết bị, {len(links)} liên kết.")
    for label, path in paths.items():
        print(f"  {label}: {path}")


if __name__ == "__main__":
    main()
