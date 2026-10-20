"""Bước 4: tài khoản mule, thiết bị trang trại, ngụy trang và SIMO.

Chưa sinh tiền nạn nhân. Cùng seed cho cùng kết quả.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.io import write
from sim.names import sample_name_body, surname_table, to_name_ascii

TZ = "Asia/Ho_Chi_Minh"
PROTECTED = {"school_fee", "large_oneoff", "own_transfer_drain", "passthrough_in", "passthrough_out"}


def load_config() -> dict:
    import yaml

    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
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
    for index in range(short):
        counts[order[index]] += 1
    return {key: int(count) for key, count in zip(keys, counts)}


def next_ids(prefix: str, existing: pd.Series, n: int) -> list[str]:
    text = existing.astype(str)
    if len(text) == 0:
        width, start = 8, 1
    else:
        width = int(text.str.len().mode().iloc[0]) - len(prefix)
        start = int(text.str[len(prefix):].astype(int).max()) + 1
    return [f"{prefix}{i:0{width}d}" for i in range(start, start + n)]


def ring_sizes(n: int, low: int, high: int, rng) -> np.ndarray:
    center = float(np.sqrt(low * high))
    draw = np.rint(center * np.exp(0.35 * rng.standard_normal(n))).astype(int)
    return np.clip(draw, low, high)


def split_layers(size: int, shares: dict) -> dict[str, int]:
    keys = ["l1", "l2", "cashout"]
    counts = largest_remainder(keys, [shares[key] for key in keys], size)
    guard = 0
    while min(counts.values()) < 1 and guard < 6:
        donor = max(keys, key=lambda key: counts[key])
        needy = min(keys, key=lambda key: counts[key])
        if counts[donor] <= 1:
            break
        counts[donor] -= 1
        counts[needy] += 1
        guard += 1
    return counts


def wire_ring(grouped: dict[str, list[str]], pattern: str, rng) -> tuple[dict[str, list[str]], dict[str, int | None]]:
    """Fan-out, scatter-gather hoặc stack. Mỗi nút có một danh sách lớp sau."""
    layer1 = grouped["l1"]
    layer2 = grouped["l2"]
    cash = grouped["cashout"]
    forward = {account: [] for account in layer1 + layer2 + cash}
    delay: dict[str, int | None] = {account: None for account in cash}
    if pattern == "fan-out":
        for account in layer1:
            take = min(len(layer2), 4)
            chosen = np.atleast_1d(rng.choice(np.array(layer2, dtype=object), size=take, replace=False))
            forward[account] = [str(item) for item in chosen]
            delay[account] = int(rng.integers(2, 31))
        for account in layer2:
            forward[account] = [str(cash[int(rng.integers(0, len(cash)))])]
            delay[account] = int(rng.integers(5, 61))
    elif pattern == "scatter-gather":
        hub = cash[0]
        for index, account in enumerate(layer1):
            forward[account] = [layer2[index % len(layer2)]]
            delay[account] = int(rng.integers(2, 31))
        for account in layer2:
            forward[account] = [hub]
            delay[account] = int(rng.integers(5, 61))
    else:
        chain = layer1 + layer2 + cash[:1]
        layer1_set = set(layer1)
        for source, dest in zip(chain, chain[1:]):
            forward[source] = [dest]
            delay[source] = int(rng.integers(2, 31)) if source in layer1_set else int(rng.integers(5, 61))
        if len(cash) > 1 and layer2:
            forward[layer2[-1]].extend(cash[1:])
    return forward, delay


def gumbel_top(score: np.ndarray, k: int, rng) -> np.ndarray:
    noise = rng.gumbel(size=len(score))
    return np.argsort(-(score + noise), kind="stable")[:k]


def assign_balances(tx: pd.DataFrame, opening: dict[str, int], tracked: set[str]) -> tuple[np.ndarray, np.ndarray]:
    """Số dư chạy của tài khoản khách và mule. Người gửi không theo dõi giữ số dư mở."""
    sender = tx["from_account"].astype(str).to_numpy()
    raw_to = tx["to_account"]
    receiver = raw_to.astype(str).to_numpy()
    receiver[raw_to.isna().to_numpy()] = ""
    amount = tx["amount"].to_numpy().astype(np.int64)
    stamp = tx["ts"].astype("int64").to_numpy()
    order_key = tx["tx_id"].astype(str).to_numpy()
    order = np.lexsort((order_key, stamp))
    bal = {account: int(opening[account]) for account in tracked}
    before = np.zeros(len(tx), dtype=np.int64)
    keep = np.ones(len(tx), dtype=bool)
    for index in order:
        source = sender[index]
        dest = receiver[index]
        amt = int(amount[index])
        if source in tracked:
            avail = bal[source]
            if amt <= 0 or amt > avail:
                keep[index] = False
                continue
            before[index] = avail
            bal[source] = avail - amt
        else:
            before[index] = int(opening.get(source, 0))
            if amt <= 0:
                keep[index] = False
                continue
        if dest and dest in tracked:
            bal[dest] = bal.get(dest, 0) + amt
    return before, keep


def main() -> None:
    cfg = load_config()
    rng = np.random.default_rng(int(cfg["seed"]))
    spec = cfg["mules"]
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"

    customers = pd.read_parquet(observed / "customers.parquet")
    latent = pd.read_parquet(truth / "customers_latent.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    devices = pd.read_parquet(observed / "devices.parquet")
    links = pd.read_parquet(observed / "account_device.parquet")
    transactions = pd.read_parquet(observed / "transactions.parquet")
    devices_latent = pd.read_parquet(truth / "devices_latent.parquet")

    customers["customer_id"] = customers["customer_id"].astype(str)
    latent["customer_id"] = latent["customer_id"].astype(str)
    accounts["account_id"] = accounts["account_id"].astype(str)
    people = customers.merge(latent, on="customer_id", how="left")
    ref_year = int(sim_start.year)
    age = ref_year - people["birth_year"].to_numpy()
    income = people["income_monthly"].to_numpy().astype(float)
    z_income = (np.log(np.maximum(income, 1.0)) - np.log(np.median(income))) / 0.6
    young = ((age >= 18) & (age <= 35)).astype(float)

    personal = accounts.loc[
        accounts["customer_id"].notna() & (accounts["account_type"] == "personal"),
        ["account_id", "customer_id", "bank_id", "opened_at", "opening_balance", "holder_name_ascii"],
    ].copy()
    personal["customer_id"] = personal["customer_id"].astype(str)
    personal = personal.sort_values(["customer_id", "opening_balance", "account_id"], ascending=[True, False, True], kind="mergesort")
    primary = personal.drop_duplicates("customer_id", keep="first")
    account_of = dict(zip(primary["customer_id"], primary["account_id"]))
    customer_of = dict(zip(primary["account_id"], primary["customer_id"]))

    protected_accounts = set(
        transactions.loc[transactions["tx_type"].isin(PROTECTED), "from_account"].dropna().astype(str)
    )
    link_people = links.merge(accounts[["account_id", "customer_id"]], on="account_id", how="left").dropna(subset=["customer_id"])
    link_people["customer_id"] = link_people["customer_id"].astype(str)
    link_people["device_id"] = link_people["device_id"].astype(str)
    link_people = link_people.merge(latent[["customer_id", "family_group_id"]], on="customer_id", how="left")
    busy_devices = set(
        link_people.groupby("device_id")["family_group_id"].nunique().loc[lambda series: series > 2].index.astype(str)
    )
    busy_customers = set(link_people.loc[link_people["device_id"].isin(busy_devices), "customer_id"].astype(str))
    eligible_ids = [
        cid for cid in people["customer_id"]
        if cid in account_of and account_of[cid] not in protected_accounts and cid not in busy_customers
    ]
    eligible = people.set_index("customer_id").loc[eligible_ids]
    score_index = {cid: index for index, cid in enumerate(people["customer_id"])}
    eligible_pos = np.array([score_index[cid] for cid in eligible_ids], dtype=int)

    n_active = int(spec["n_active_rings"])
    n_old = int(spec["n_old_rings"])
    sizes = ring_sizes(n_active + n_old, int(spec["ring_size"]["min"]), int(spec["ring_size"]["max"]), rng)
    n_slots = int(sizes.sum())
    type_counts = largest_remainder(["rented", "new_opened", "recruited_victim"], list(spec["type_share"].values()), n_slots)
    n_recruited = type_counts["recruited_victim"]
    n_rented = type_counts["rented"]
    n_new = type_counts["new_opened"]
    if n_recruited + n_rented > len(eligible_pos):
        raise RuntimeError("Không đủ khách hàng cho tài khoản mule thuê và bị lôi kéo")

    recruited_score = 0.4 * young[eligible_pos] - 0.15 * z_income[eligible_pos]
    rented_score = 1.2 * young[eligible_pos] - 0.45 * z_income[eligible_pos]
    recruited_pick = gumbel_top(recruited_score, n_recruited, rng)
    remaining = np.ones(len(eligible_pos), dtype=bool)
    remaining[recruited_pick] = False
    rented_pool = np.flatnonzero(remaining)
    rented_local = gumbel_top(rented_score[rented_pool], n_rented, rng)
    rented_pick = rented_pool[rented_local]
    recruited_customers = [eligible_ids[int(index)] for index in recruited_pick]
    rented_customers = [eligible_ids[int(index)] for index in rented_pick]

    balance_of = dict(zip(primary["account_id"], primary["opening_balance"].astype(np.int64)))
    rented_accounts = [account_of[cid] for cid in rented_customers]
    rented_balance = np.array([int(balance_of[acc]) for acc in rented_accounts])
    n_dormant = int(round(float(spec["dormant_share_rented"]) * n_rented))
    rich = np.flatnonzero(rented_balance >= np.median(rented_balance))
    dormant_local = rng.choice(rich, size=min(n_dormant, len(rich)), replace=False) if n_dormant else np.array([], dtype=int)
    dormant_accounts = {rented_accounts[int(index)] for index in dormant_local}

    bank_names = list(cfg["accounts"]["bank_share"])
    bank_weights = [cfg["accounts"]["bank_share"][name] for name in bank_names]
    bank_counts = largest_remainder(bank_names, bank_weights, n_slots)
    banks = np.repeat(bank_names, [bank_counts[name] for name in bank_names])
    rng.shuffle(banks)

    slot_type = np.repeat(["rented", "new_opened", "recruited_victim"], [n_rented, n_new, n_recruited])
    rng.shuffle(slot_type)
    ring_id = []
    ring_active = []
    for index, size in enumerate(sizes):
        active = index < n_active
        ring_id.extend([f"R{index + 1:04d}"] * int(size))
        ring_active.extend([active] * int(size))
    ring_id = np.array(ring_id, dtype=object)
    ring_active = np.array(ring_active, dtype=bool)
    order = np.arange(n_slots)
    rng.shuffle(order)
    slot_type = slot_type[order]
    banks = banks[order]

    patterns = np.array(["fan-out", "scatter-gather", "stack"], dtype=object)
    ring_pattern = {f"R{index + 1:04d}": str(patterns[index % 3]) for index in range(n_active + n_old)}

    active_from = {}
    active_to = {}

    external_phones = phones.loc[phones["owner_type"] == "external_person", "number_id"].astype(str)
    used_phones = set(accounts["phone_number_id"].dropna().astype(str))
    free_phones = external_phones.loc[~external_phones.isin(used_phones)].sort_values().to_numpy()
    if len(free_phones) < n_new:
        raise RuntimeError("Không đủ số external chưa có tài khoản")
    chosen_phones = rng.choice(free_phones, size=n_new, replace=False)
    male = rng.random(n_new) < 0.5
    body = sample_name_body(rng, male, np.ones(n_new, dtype=bool))
    surnames, weights = surname_table()
    surname = rng.choice(surnames, size=n_new, p=weights)
    full = np.char.add(np.char.add(surname.astype(str), " "), body.astype(str))
    new_names = np.array([to_name_ascii(name) for name in full], dtype=object)
    new_ids = next_ids("A", accounts["account_id"], n_new)
    new_balance = np.clip(
        np.rint(2_000_000 * np.exp(1.1 * rng.standard_normal(n_new))).astype(np.int64),
        200_000,
        40_000_000,
    )

    rows = []
    rented_iter = iter(rented_customers)
    recruited_iter = iter(recruited_customers)
    new_iter = iter(range(n_new))
    for index in range(n_slots):
        kind = str(slot_type[index])
        if kind == "rented":
            customer = next(rented_iter)
            account = account_of[customer]
        elif kind == "recruited_victim":
            customer = next(recruited_iter)
            account = account_of[customer]
        else:
            cursor = next(new_iter)
            account = new_ids[cursor]
            customer = None
        rows.append(
            {
                "slot": index,
                "ring_id": str(ring_id[index]),
                "active_ring": bool(ring_active[index]),
                "mule_type": kind,
                "customer_id": customer,
                "account_id": account,
                "bank_id": str(banks[index]),
                "new_cursor": None if kind != "new_opened" else int(cursor),
            }
        )
    members = pd.DataFrame(rows)
    # Ràng đúng ngân hàng đã xáo: tài khoản cũ giữ ngân hàng sẵn có, tài khoản mới nhận bank_id của slot.
    existing_bank = dict(zip(accounts["account_id"].astype(str), accounts["bank_id"].astype(str)))
    reuse = members["mule_type"] != "new_opened"
    members.loc[reuse, "bank_id"] = members.loc[reuse, "account_id"].map(existing_bank)
    active_rented = members.loc[members["active_ring"] & members["mule_type"].eq("rented"), "account_id"].astype(str)
    dormant_accounts &= set(active_rented)
    short = n_dormant - len(dormant_accounts)
    if short > 0:
        pool = [account for account in active_rented if account not in dormant_accounts]
        pool.sort(key=lambda account: int(balance_of.get(account, 0)))
        dormant_accounts.update(pool[-short:])

    rented_rows = members.loc[members["mule_type"].eq("rented"), ["ring_id", "account_id"]]
    dormant_share: dict[str, float] = {}
    for ring, part in rented_rows.groupby(rented_rows["ring_id"].astype(str), sort=False):
        owned = part["account_id"].astype(str)
        dormant_share[str(ring)] = float(owned.isin(dormant_accounts).mean()) if len(owned) else 0.0
    active_ids = sorted({str(ring) for ring, active in zip(members["ring_id"], members["active_ring"]) if bool(active)})
    # Một mule ngủ không được đẩy cả đường dây qua ngày 60: chỉ đường dây
    # mà phần lớn tài khoản thuê đang ngủ mới mở muộn.
    late_rings = {ring for ring in active_ids if dormant_share.get(ring, 0.0) >= 0.5}
    ring_has_dormant = late_rings
    for ring in sorted(set(members["ring_id"].astype(str))):
        active = bool(members.loc[members["ring_id"].astype(str) == ring, "active_ring"].iloc[0])
        if not active:
            active_from[ring] = sim_start - pd.to_timedelta(int(rng.integers(180, 900)), unit="D")
            active_to[ring] = sim_start - pd.to_timedelta(int(rng.integers(1, 40)), unit="D")
        elif ring in late_rings:
            active_from[ring] = sim_start + pd.to_timedelta(int(rng.integers(62, 78)), unit="D")
            active_to[ring] = sim_end
        else:
            active_from[ring] = sim_start + pd.to_timedelta(int(rng.integers(0, 20)), unit="D")
            active_to[ring] = sim_end

    age_min = int(spec["new_opened_age_days"]["min"])
    age_max = int(spec["new_opened_age_days"]["max"])
    opened_at = {}
    for record in members.itertuples(index=False):
        if record.mule_type != "new_opened":
            continue
        cursor = int(record.new_cursor)
        age_days = int(rng.integers(age_min, age_max + 1))
        opened_at[record.account_id] = active_from[record.ring_id] - pd.to_timedelta(age_days, unit="D")
        new_ids_pos = cursor
        _ = new_ids_pos

    new_rows = []
    for record in members.itertuples(index=False):
        if record.mule_type != "new_opened":
            continue
        cursor = int(record.new_cursor)
        new_rows.append(
            {
                "account_id": record.account_id,
                "customer_id": pd.NA,
                "phone_number_id": str(chosen_phones[cursor]),
                "bank_id": record.bank_id,
                "account_type": "personal",
                "holder_name_ascii": str(new_names[cursor]),
                "opened_at": opened_at[record.account_id],
                "opening_balance": int(new_balance[cursor]),
                "status": "active",
                "merchant_verified": False,
            }
        )
    new_accounts = pd.DataFrame(new_rows)
    for column in accounts.columns:
        if column not in new_accounts.columns:
            new_accounts[column] = pd.NA
    new_accounts = new_accounts[accounts.columns]
    new_accounts["account_id"] = new_accounts["account_id"].astype("string")
    new_accounts["phone_number_id"] = new_accounts["phone_number_id"].astype("string")
    new_accounts["bank_id"] = new_accounts["bank_id"].astype("string")
    new_accounts["account_type"] = new_accounts["account_type"].astype("string")
    new_accounts["holder_name_ascii"] = new_accounts["holder_name_ascii"].astype("string")
    new_accounts["status"] = new_accounts["status"].astype("string")
    new_accounts["opening_balance"] = new_accounts["opening_balance"].astype(np.int64)
    new_accounts["merchant_verified"] = new_accounts["merchant_verified"].astype(bool)
    accounts = pd.concat([accounts, new_accounts], ignore_index=True)
    old_rings = {ring for ring, active in zip(members["ring_id"], members["active_ring"]) if not active}
    accounts.loc[accounts["account_id"].isin(set(members.loc[members["ring_id"].isin(old_rings), "account_id"])), "status"] = "frozen"
    accounts["status"] = accounts["status"].astype("string")
    target_b01 = int(round(float(cfg["accounts"]["bank_share"]["B01"]) * len(members)))
    bank_now = accounts.set_index("account_id")["bank_id"].astype(str)
    current_b01 = int(bank_now.loc[members["account_id"]].eq("B01").sum())
    if current_b01 < target_b01:
        movable = members.loc[members["mule_type"].eq("new_opened") & members["bank_id"].ne("B01"), "account_id"].astype(str)
        flip = movable.iloc[: target_b01 - current_b01]
        accounts.loc[accounts["account_id"].isin(set(flip)), "bank_id"] = "B01"
        members.loc[members["account_id"].isin(set(flip)), "bank_id"] = "B01"

    opening = dict(zip(accounts["account_id"].astype(str), accounts["opening_balance"].astype(np.int64)))
    customer_accounts = set(accounts.loc[accounts["customer_id"].notna(), "account_id"].astype(str))
    before, keep = assign_balances(transactions, opening, customer_accounts)
    sender = transactions["from_account"].astype(str)
    comparable = sender.isin(customer_accounts).to_numpy() & keep
    mismatch = int((before[comparable] != transactions.loc[comparable, "from_balance_before"].to_numpy()).sum())
    if mismatch:
        raise RuntimeError(f"Replay bước 3 lệch {mismatch} giao dịch trước khi sửa mule")

    own_target = int(round(float(spec["own_device_share"]) * n_slots))
    members["own_device"] = members["mule_type"] == "recruited_victim"
    need_own = own_target - int(members["own_device"].sum())
    candidates = np.flatnonzero(~members["own_device"].to_numpy())
    if need_own > 0:
        chosen = rng.choice(candidates, size=min(need_own, len(candidates)), replace=False)
        members.loc[members.index[chosen], "own_device"] = True
    elif need_own < 0:
        recruited_idx = np.flatnonzero(members["mule_type"].eq("recruited_victim").to_numpy())
        members.loc[members.index[recruited_idx[need_own:]], "own_device"] = False

    layer_of = {}
    forward_of = {}
    delay_of = {}
    method_of = {}
    for ring, part in members.groupby("ring_id", sort=True):
        counts = split_layers(len(part), spec["layer_share"])
        ordered = part.sort_values("account_id", kind="mergesort")
        cursor = 0
        grouped = {}
        for layer in ("l1", "l2", "cashout"):
            chunk = ordered.iloc[cursor: cursor + counts[layer]]["account_id"].astype(str).tolist()
            grouped[layer] = chunk
            for account in chunk:
                layer_of[account] = layer
            cursor += counts[layer]
        forward, delay = wire_ring(grouped, ring_pattern[ring], rng)
        forward_of.update(forward)
        delay_of.update(delay)
        for account in grouped["cashout"]:
            method_of[account] = str(rng.choice(["cash_withdrawal", "crypto_purchase"]))

    farm_min = int(spec["farm_devices_per_ring"]["min"])
    farm_max = int(spec["farm_devices_per_ring"]["max"])
    device_rows = []
    link_rows = []
    ring_devices: dict[str, list[str]] = {}
    device_ids = next_ids("D", devices["device_id"], n_active + n_old + (n_active + n_old) * farm_max + n_new + n_slots)
    device_cursor = 0

    def take_device() -> str:
        nonlocal device_cursor
        device_id = device_ids[device_cursor]
        device_cursor += 1
        return device_id

    for ring, part in members.groupby("ring_id", sort=True):
        n_dev = int(rng.integers(farm_min, farm_max + 1))
        ids = [take_device() for _ in range(n_dev)]
        ring_devices[ring] = ids
        start = active_from[ring] - pd.to_timedelta(int(rng.integers(20, 200)), unit="D")
        if start >= sim_end:
            start = sim_end - pd.to_timedelta(2, unit="D")
        retired = None if bool(part["active_ring"].iloc[0]) else active_to[ring]
        for device_id in ids:
            device_rows.append(
                {
                    "device_id": device_id,
                    "customer_id": pd.NA,
                    "os": "Android" if rng.random() < 0.7 else "iOS",
                    "is_rooted": bool(rng.random() < 0.04),
                    "first_seen": start,
                    "last_seen": retired,
                }
            )

    old_device_ids = [device_id for ring, ids in ring_devices.items() if ring in old_rings for device_id in ids]
    n_reuse = int(round(float(spec["old_ring_device_reuse"]) * len(old_device_ids)))
    reused = set(rng.choice(old_device_ids, size=n_reuse, replace=False)) if n_reuse else set()
    for device_id in reused:
        for row in device_rows:
            if row["device_id"] == device_id:
                row["last_seen"] = pd.NaT

    active_rings = [f"R{index + 1:04d}" for index in range(n_active)]
    reuse_cycle = list(reused)
    rng.shuffle(reuse_cycle)
    assigned_reuse = {}
    for index, device_id in enumerate(reuse_cycle):
        assigned_reuse.setdefault(active_rings[index % len(active_rings)], []).append(device_id)

    for ring, part in members.groupby("ring_id", sort=True):
        farm_people = part.loc[~part["own_device"], "account_id"].astype(str).tolist()
        devices_for_ring = list(ring_devices[ring])
        if ring in assigned_reuse:
            devices_for_ring = assigned_reuse[ring] + devices_for_ring
        if not farm_people:
            continue
        if len(farm_people) >= 3:
            devices_for_ring = devices_for_ring[: max(1, min(len(devices_for_ring), len(farm_people) // 3))]
        for index, account in enumerate(farm_people):
            device_id = devices_for_ring[index % len(devices_for_ring)]
            first = device_rows[[item["device_id"] for item in device_rows].index(device_id)]["first_seen"] if False else None
            link_rows.append((account, device_id, ring))

    own_new = members.loc[members["own_device"] & members["mule_type"].eq("new_opened")]
    own_device_of = {}
    for record in own_new.itertuples(index=False):
        device_id = take_device()
        own_device_of[record.account_id] = device_id
        device_rows.append(
            {
                "device_id": device_id,
                "customer_id": pd.NA,
                "os": "Android" if rng.random() < 0.7 else "iOS",
                "is_rooted": False,
                "first_seen": opened_at[record.account_id],
                "last_seen": None if record.active_ring else active_to[record.ring_id],
            }
        )
        link_rows.append((record.account_id, device_id, record.ring_id))

    # Thêm máy trang trại đúng 3 hộ để máy > 2 hộ khác nhau vẫn chủ yếu là mule,
    # sau khi bước 2 đã có máy cửa hàng hợp lệ.
    family_of = dict(zip(people["customer_id"].astype(str), people["family_group_id"].astype(str)))

    def member_household(customer_id, account_id) -> str:
        if customer_id is None or pd.isna(customer_id):
            return "A:" + str(account_id)
        return "F:" + family_of.get(str(customer_id), str(customer_id))

    hh_of = {
        str(account): member_household(customer, account)
        for account, customer in zip(members["account_id"], members["customer_id"])
    }
    base_links = links.merge(accounts[["account_id", "customer_id"]], on="account_id", how="left")
    base_links["household"] = base_links["customer_id"].map(
        lambda customer: "F:" + family_of.get(str(customer), str(customer)) if pd.notna(customer) else "X"
    )
    shop_like = int((base_links.groupby("device_id")["household"].nunique() > 2).sum())
    device_households: dict[str, set[str]] = {}
    account_nlinks: dict[str, int] = {}
    for account, device_id, _ring in link_rows:
        device_households.setdefault(device_id, set()).add(hh_of[account])
        account_nlinks[account] = account_nlinks.get(account, 0) + 1
    mule_crowded = sum(1 for households in device_households.values() if len(households) > 2)
    pool = members.loc[~members["own_device"] & members["active_ring"], "account_id"].astype(str).tolist()
    rng.shuffle(pool)
    max_links = 2

    def capacity_for(limit: int) -> int:
        spare = sum(max(0, limit - account_nlinks.get(account, 0)) for account in pool)
        return mule_crowded + spare // 3

    if shop_like:
        low = math.ceil(0.85 * shop_like / 0.15)
        high = math.floor(0.95 * shop_like / 0.05)
        if capacity_for(max_links) < low:
            max_links = 3
        goal = min(max(int(round(0.90 * shop_like / 0.10)), low), high, capacity_for(max_links))
        if goal < low:
            raise RuntimeError(
                f"Không đủ mule cho tỷ lệ 85% máy nhiều hộ (cần {low}, có {capacity_for(max_links)}, cửa hàng {shop_like})"
            )
    else:
        low = 0
        goal = mule_crowded
    while mule_crowded < goal:
        chosen = []
        seen: set[str] = set()
        for account in pool:
            if account_nlinks.get(account, 0) >= max_links:
                continue
            household = hh_of[account]
            if household in seen:
                continue
            chosen.append(account)
            seen.add(household)
            if len(chosen) == 3:
                break
        if len(chosen) < 3:
            break
        device_id = take_device()
        device_rows.append(
            {
                "device_id": device_id,
                "customer_id": pd.NA,
                "os": "Android" if rng.random() < 0.7 else "iOS",
                "is_rooted": False,
                "first_seen": sim_start - pd.to_timedelta(5, unit="D"),
                "last_seen": pd.NaT,
            }
        )
        for account in chosen:
            link_rows.append((account, device_id, ""))
            account_nlinks[account] = account_nlinks.get(account, 0) + 1
        mule_crowded += 1
    if shop_like and mule_crowded < low:
        raise RuntimeError(f"Máy mule nhiều hộ dừng ở {mule_crowded}, cần ≥ {low}")

    device_frame = pd.DataFrame(device_rows)
    device_frame["device_id"] = device_frame["device_id"].astype("string")
    device_frame["os"] = device_frame["os"].astype("string")
    device_frame["is_rooted"] = device_frame["is_rooted"].astype(bool)
    device_first = dict(zip(device_frame["device_id"].astype(str), device_frame["first_seen"]))
    device_last = dict(zip(device_frame["device_id"].astype(str), device_frame["last_seen"]))
    opened_lookup = dict(zip(accounts["account_id"].astype(str), accounts["opened_at"]))
    login = rng.integers(4, 90, size=len(link_rows))
    mule_links = []
    for (account, device_id, _ring), count in zip(link_rows, login):
        first = device_first[device_id]
        opened = opened_lookup[account]
        if pd.notna(opened) and opened > first:
            first = opened
        mule_links.append(
            {
                "account_id": account,
                "device_id": device_id,
                "login_count": int(count),
                "first_seen": first,
                "last_seen": device_last[device_id],
            }
        )
    mule_link_frame = pd.DataFrame(mule_links)
    if "first_seen" not in links.columns:
        links = links.merge(
            devices[["device_id", "first_seen", "last_seen"]],
            on="device_id",
            how="left",
        )
    links = pd.concat([links, mule_link_frame], ignore_index=True)
    links["account_id"] = links["account_id"].astype("string")
    links["device_id"] = links["device_id"].astype("string")
    links["login_count"] = links["login_count"].astype(np.int64)
    devices = pd.concat([devices, device_frame[devices.columns]], ignore_index=True)
    devices["device_id"] = devices["device_id"].astype("string")

    latent_extra = pd.DataFrame(
        {
            "device_id": device_frame["device_id"].astype("string"),
            "baseline_accessibility": [[] for _ in range(len(device_frame))],
            "remote_access_app_installed": False,
        }
    )
    devices_latent = pd.concat([devices_latent, latent_extra], ignore_index=True)

    # Ngụy trang và cầu nối mule cũ ↔ mới.
    funding_pool = accounts.loc[
        accounts["customer_id"].isna()
        & (accounts["account_type"] == "personal")
        & ~accounts["account_id"].isin(set(members["account_id"])),
        "account_id",
    ].astype(str).to_numpy()
    extra_tx = []
    tx_seq = int(transactions["tx_id"].astype(str).str[1:].astype(int).max())

    def add_tx(when, source, dest, amount, purpose):
        nonlocal tx_seq
        tx_seq += 1
        extra_tx.append(
            {
                "tx_id": f"T{tx_seq:08d}",
                "ts": when,
                "from_account": source,
                "to_account": dest,
                "amount": int(amount),
                "tx_type": "transfer",
                "purpose_text": purpose,
                "channel": "app",
                "session_id": pd.NA,
                "from_balance_before": np.int64(0),
            }
        )

    for ring, part in members.groupby("ring_id", sort=True):
        if ring in old_rings:
            continue
        fresh = part.loc[part["mule_type"] == "new_opened", "account_id"].astype(str).tolist()
        if len(fresh) < 1:
            continue
        start = active_from[ring]
        for offset, account in enumerate(fresh):
            when = max(start, opened_at[account] + pd.to_timedelta(2, unit="h")) + pd.to_timedelta(int(offset), unit="h")
            if not (sim_start <= when < sim_end):
                when = min(max(when, sim_start + pd.to_timedelta(1, unit="h")), sim_end - pd.to_timedelta(2, unit="h"))
            fund = str(rng.choice(funding_pool))
            add_tx(when, fund, account, int(rng.integers(50_000, 200_001)), "nap tien")
            if len(fresh) == 1:
                continue
            peer = fresh[(offset + 1) % len(fresh)]
            hop = when + pd.to_timedelta(int(rng.integers(30, 180)), unit="m")
            back = hop + pd.to_timedelta(int(rng.integers(20, 120)), unit="m")
            if back >= sim_end:
                continue
            add_tx(hop, account, peer, int(rng.integers(30_000, 150_001)), "chuyen tien")
            add_tx(back, peer, account, int(rng.integers(20_000, 100_001)), "chuyen tien")

    connected = set()
    for ring, device_list in assigned_reuse.items():
        if device_list:
            connected.add(ring)
    need_links = int(np.ceil(n_active / 3))
    old_members = members.loc[members["ring_id"].isin(old_rings) & members["mule_type"].ne("new_opened")]
    if old_members.empty:
        old_members = members.loc[members["ring_id"].isin(old_rings)]
    bridge_targets = [ring for ring in active_rings if ring not in connected]
    rng.shuffle(bridge_targets)
    for ring in bridge_targets:
        if len(connected) >= need_links and len(extra_tx) > need_links:
            break
        part = members.loc[members["ring_id"] == ring]
        receiver = part.loc[part["mule_type"] != "new_opened", "account_id"]
        if receiver.empty:
            receiver = part["account_id"]
        dest = str(receiver.iloc[0])
        source = str(old_members["account_id"].iloc[int(rng.integers(0, len(old_members)))])
        opened = opened_lookup.get(dest, sim_start)
        when = sim_start + pd.to_timedelta(int(rng.integers(1, 4)), unit="D") + pd.to_timedelta(int(rng.integers(8, 20)), unit="h")
        if pd.notna(opened) and when < opened:
            continue
        add_tx(when, source, dest, int(rng.integers(100_000, 400_001)), "chuyen tien")
        connected.add(ring)

    if len(connected) < need_links:
        missing = [ring for ring in active_rings if ring not in connected]
        spare = [device_id for device_id in old_device_ids]
        for ring, device_id in zip(missing, spare):
            account = str(members.loc[members["ring_id"] == ring, "account_id"].iloc[0])
            mule_links_extra = {
                "account_id": account,
                "device_id": device_id,
                "login_count": np.int64(int(rng.integers(3, 20))),
                "first_seen": device_first[device_id],
                "last_seen": pd.NaT,
            }
            links = pd.concat([links, pd.DataFrame([mule_links_extra])], ignore_index=True)
            connected.add(ring)

    original_ids = set(transactions["tx_id"].astype(str))
    transactions = pd.concat([transactions, pd.DataFrame(extra_tx)], ignore_index=True)
    transactions["tx_id"] = transactions["tx_id"].astype("string")
    transactions["from_account"] = transactions["from_account"].astype("string")
    transactions["to_account"] = transactions["to_account"].astype("string")
    transactions["tx_type"] = transactions["tx_type"].astype("string")
    transactions["purpose_text"] = transactions["purpose_text"].astype("string")
    transactions["channel"] = transactions["channel"].astype("string")
    transactions["amount"] = transactions["amount"].astype(np.int64)
    transactions["session_id"] = transactions["session_id"].astype("string")

    dormant_cutoff = {
        str(account): active_from[ring]
        for account, ring in zip(members["account_id"], members["ring_id"])
        if account in dormant_accounts and ring in ring_has_dormant
    }
    cutoff_ns = {account: int(stamp.value) for account, stamp in dormant_cutoff.items()}
    tx_ns = transactions["ts"].astype("int64").to_numpy()
    original = transactions["tx_id"].astype(str).isin(original_ids).to_numpy()
    from_cut = transactions["from_account"].astype(str).map(cutoff_ns)
    to_cut = transactions["to_account"].astype(str).map(cutoff_ns)
    drop_from = from_cut.notna().to_numpy() & (tx_ns < from_cut.fillna(0).to_numpy(dtype=np.int64))
    drop_to = to_cut.notna().to_numpy() & (tx_ns < to_cut.fillna(0).to_numpy(dtype=np.int64))
    transactions = transactions.loc[~(original & (drop_from | drop_to))].reset_index(drop=True)

    tracked = set(customer_accounts) | set(members["account_id"].astype(str))
    before, keep = assign_balances(transactions, opening, tracked)
    dropped_types = set(transactions.loc[~keep, "tx_type"].astype(str))
    if dropped_types & PROTECTED:
        raise RuntimeError(f"Sửa mule làm rơi giao dịch được bảo vệ: {sorted(dropped_types & PROTECTED)}")
    transactions = transactions.loc[keep].reset_index(drop=True)
    transactions["from_balance_before"] = before[keep]
    transactions["from_balance_before"] = transactions["from_balance_before"].astype(np.int64)

    # SIMO: mule cũ + 5% tài khoản hợp lệ báo nhầm.
    mule_ids = set(members["account_id"].astype(str))
    old_ids = set(members.loc[members["ring_id"].isin(old_rings), "account_id"].astype(str))
    legit_ids = accounts.loc[~accounts["account_id"].isin(mule_ids), "account_id"].astype(str).to_numpy()
    n_false = int(round(float(spec["simo_false_listing_rate"]) * len(legit_ids)))
    false_ids = set(rng.choice(legit_ids, size=n_false, replace=False))
    sources = np.array(["victim", "bank", "police"], dtype=object)
    source_p = np.array([0.55, 0.35, 0.10])
    listed = list(old_ids) + list(false_ids)
    listed_at = []
    listed_source = []
    old_span = int((sim_start - as_timestamp("2023-01-01")).total_seconds())
    for account in listed:
        if account in old_ids:
            listed_at.append(as_timestamp("2023-01-01") + pd.to_timedelta(int(rng.integers(0, old_span)), unit="s"))
        else:
            listed_at.append(sim_start - pd.to_timedelta(int(rng.integers(1, 400)), unit="D"))
        listed_source.append(str(rng.choice(sources, p=source_p)))
    simo = pd.DataFrame(
        {
            "account_id": pd.Series(listed, dtype="string"),
            "listed_at": listed_at,
            "source": pd.Series(listed_source, dtype="string"),
        }
    )

    truth_rows = []
    false_set = false_ids
    for record in members.itertuples(index=False):
        truth_rows.append(
            {
                "account_id": record.account_id,
                "is_mule": True,
                "mule_type": record.mule_type,
                "ring_id": record.ring_id,
                "layer": layer_of[record.account_id],
                "recruited_at": active_from[record.ring_id] - pd.to_timedelta(int(rng.integers(1, 15)), unit="D"),
                "simo_listing_correct": True,
            }
        )
    for account in sorted(false_set):
        truth_rows.append(
            {
                "account_id": account,
                "is_mule": False,
                "mule_type": pd.NA,
                "ring_id": pd.NA,
                "layer": pd.NA,
                "recruited_at": pd.NaT,
                "simo_listing_correct": False,
            }
        )
    accounts_truth = pd.DataFrame(truth_rows)
    accounts_truth["account_id"] = accounts_truth["account_id"].astype("string")
    accounts_truth["is_mule"] = accounts_truth["is_mule"].astype(bool)
    accounts_truth["mule_type"] = accounts_truth["mule_type"].astype("string")
    accounts_truth["ring_id"] = accounts_truth["ring_id"].astype("string")
    accounts_truth["layer"] = accounts_truth["layer"].astype("string")
    accounts_truth["simo_listing_correct"] = accounts_truth["simo_listing_correct"].astype(bool)

    ring_rows = []
    for record in members.itertuples(index=False):
        ring_rows.append(
            {
                "ring_id": record.ring_id,
                "account_id": record.account_id,
                "layer": layer_of[record.account_id],
                "forward_to": forward_of.get(record.account_id, []),
                "forward_delay_minutes": delay_of.get(record.account_id),
                "cashout_method": method_of.get(record.account_id),
                "active_from": active_from[record.ring_id],
                "active_to": active_to[record.ring_id],
            }
        )
    mule_rings = pd.DataFrame(ring_rows)
    mule_rings["ring_id"] = mule_rings["ring_id"].astype("string")
    mule_rings["account_id"] = mule_rings["account_id"].astype("string")
    mule_rings["layer"] = mule_rings["layer"].astype("string")
    mule_rings["cashout_method"] = mule_rings["cashout_method"].astype("string")
    mule_rings["forward_delay_minutes"] = mule_rings["forward_delay_minutes"].astype("Int64")

    accounts["customer_id"] = accounts["customer_id"].astype("string")
    devices["customer_id"] = devices["customer_id"].astype("string")
    write(accounts, "observed", "accounts")
    write(devices, "observed", "devices")
    write(links, "observed", "account_device")
    write(transactions, "observed", "transactions")
    write(simo, "observed", "simo_list")
    write(devices_latent, "truth", "devices_latent")
    write(accounts_truth, "truth", "accounts_truth")
    write(mule_rings, "truth", "mule_rings")
    print(
        f"Đã sinh {members['ring_id'].nunique()} đường dây, {len(members)} mule, "
        f"{len(device_frame)} thiết bị trang trại, {len(simo)} dòng SIMO, {len(extra_tx)} giao dịch ngụy trang."
    )


if __name__ == "__main__":
    main()
