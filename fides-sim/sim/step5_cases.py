"""Bước 5: kế hoạch vụ lừa đảo. Chưa sinh phiên, cuộc gọi hay giao dịch thật.

Đọc bước 1–4. Cùng seed cho cùng kết quả.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.io import write
from sim.step1_population import assign_msisdn
from sim.step4_mules import as_timestamp, largest_remainder, next_ids

TZ = "Asia/Ho_Chi_Minh"
PURPOSE = {
    "police": ["phoi hop dieu tra", "chuyen tien an toan", "nop tien bao lanh"],
    "bank_staff": ["nang cap bao mat", "xac thuc tai khoan", "chuyen tien an toan"],
    "investment": ["gop von", "nap dau tu", "phi giai ngan"],
    "romance": ["chuyen tien", "giup do", "vay gap"],
    "deposit": ["dat coc", "phi ho so", "giu cho"],
    "relative": ["chuyen tien gap", "vien phi", "bao lanh"],
    "tax_authority": ["nop thue bo sung", "phat hanh chinh", "phi xu ly ho so"],
}
FEE_KINDS = {"police", "bank_staff", "tax_authority", "investment"}
MOBILIZE = (
    "savings_liquidation",
    "quick_loan_disbursement",
    "credit_card_cash_advance",
    "limit_raise",
    "split_near_limit",
)


def load_config() -> dict:
    import yaml

    with (ROOT / "config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def auc_score(label: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=float)
    ranks[order] = np.arange(1, len(score) + 1, dtype=float)
    sorted_score = score[order]
    start = 0
    while start < len(score):
        stop = start + 1
        while stop < len(score) and sorted_score[stop] == sorted_score[start]:
            stop += 1
        if stop > start + 1:
            ranks[order[start:stop]] = 0.5 * (start + 1 + stop)
        start = stop
    positive = label.astype(bool)
    n_pos = int(positive.sum())
    n_neg = len(label) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.0
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def to_ns(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.int64, copy=False)
    if len(values) == 0:
        return values
    sample = np.nanmedian(np.abs(values[: min(len(values), 1000)]))
    if sample < 1e14:
        return values * 1_000_000_000
    return values


def build_curves(transactions: pd.DataFrame, owned: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    ids = set(owned["account_id"].astype(str))
    opening = dict(zip(owned["account_id"].astype(str), owned["opening_balance"].astype(np.int64)))
    outbound = transactions.loc[transactions["from_account"].isin(ids), ["ts", "from_account", "amount"]].rename(
        columns={"from_account": "account_id"}
    )
    outbound["delta"] = -outbound["amount"].astype(np.int64)
    inbound = transactions.dropna(subset=["to_account"])
    inbound = inbound.loc[inbound["to_account"].isin(ids), ["ts", "to_account", "amount"]].rename(
        columns={"to_account": "account_id"}
    )
    inbound["delta"] = inbound["amount"].astype(np.int64)
    events = pd.concat(
        [outbound[["ts", "account_id", "delta"]], inbound[["ts", "account_id", "delta"]]],
        ignore_index=True,
    )
    events["account_id"] = events["account_id"].astype(str)
    events = events.sort_values(["account_id", "ts"], kind="mergesort")
    curves = {}
    for account, part in events.groupby("account_id", sort=False):
        curves[str(account)] = (
            to_ns(part["ts"].astype("int64").to_numpy()),
            opening[str(account)] + np.cumsum(part["delta"].to_numpy(dtype=np.int64)),
        )
    return curves


def balance_at(account: str, when: pd.Timestamp, opening: dict[str, int], opened: dict[str, pd.Timestamp], curves) -> int:
    if when < opened[account]:
        return 0
    curve = curves.get(account)
    base = int(opening[account])
    if curve is None:
        return base
    stamps, running = curve
    index = int(np.searchsorted(stamps, np.int64(when.value), side="left")) - 1
    if index < 0:
        return base
    return int(running[index])


def type_fit(age: np.ndarray) -> dict[str, np.ndarray]:
    police = 0.25 + 0.75 / (1.0 + np.exp(-(age - 58.0) / 7.0))
    invest = 0.20 + 0.80 * np.exp(-((age - 37.0) / 14.0) ** 2)
    deposit = 0.25 + 0.75 / (1.0 + np.exp(-(30.0 - age) / 6.0))
    relative = 0.55 + 0.45 / (1.0 + np.exp(-(age - 48.0) / 10.0))
    return {
        "police": police,
        "tax_authority": police,
        "bank_staff": police,
        "investment": invest,
        "romance": invest,
        "deposit": deposit,
        "relative": relative,
    }


def case_span(kind: str, rng) -> pd.Timedelta:
    if kind in {"police", "bank_staff", "tax_authority"}:
        return pd.Timedelta(hours=int(rng.integers(3, 8)))
    if kind == "relative":
        return pd.Timedelta(hours=int(rng.integers(2, 6)))
    if kind == "deposit":
        return pd.Timedelta(days=int(rng.integers(1, 4)))
    if kind == "romance":
        return pd.Timedelta(days=int(rng.integers(8, 25)))
    return pd.Timedelta(days=int(rng.integers(12, 34)))


def round_vnd(value: float, cap: int) -> int:
    amount = int(value // 1000) * 1000
    cap = int(cap // 1000) * 1000
    if amount > cap:
        amount = cap
    return max(amount, 0)


def increasing_amounts(cap: int, rng, split: bool) -> list[int]:
    cap = int(cap // 1000) * 1000
    if cap < 4_000:
        return [max(cap, 0)]
    n_pay = int(rng.integers(4, 6))
    start = int(rng.integers(1_000_000, 5_000_001)) if cap >= 20_000_000 else max(100_000, cap // (n_pay * 4))
    raw = [float(start)]
    for _ in range(n_pay - 1):
        raw.append(raw[-1] * float(rng.uniform(1.9, 2.7)))
    levels = np.array(raw)
    if levels.sum() > cap:
        levels *= cap / levels.sum()
    amounts = [max(int(levels[0] // 1000) * 1000, 1000)]
    for value in levels[1:]:
        amounts.append(max(int(value // 1000) * 1000, amounts[-1] + 1000))
    while sum(amounts) > cap and amounts[-1] > amounts[-2] + 1000:
        amounts[-1] -= 1000
    if split and len(amounts) >= 2 and amounts[-1] > amounts[-2] * 2 + 2000:
        previous = amounts[-2]
        last = amounts[-1]
        first_part = int((previous + last / 2) // 1000) * 1000
        second = last - first_part
        if second > first_part > previous:
            amounts = amounts[:-1] + [first_part, second]
    return amounts


def flatten_peak(amounts: list[int], rng) -> list[int]:
    """Sau đỉnh: chững lại hoặc giảm. Cả chuỗi không còn tăng dần."""
    if len(amounts) < 2:
        return amounts
    peak = amounts[-2] if len(amounts) >= 3 else amounts[0]
    if rng.random() < 0.45:
        amounts[-1] = int(peak)
    else:
        lowered = int(peak * float(rng.uniform(0.40, 0.80)) // 1000 * 1000)
        amounts[-1] = max(1000, min(lowered, peak - 1000 if peak > 1000 else peak))
    return amounts


def split_amount(amount: int, rng) -> list[int]:
    amount = int(amount // 1000) * 1000
    n_parts = int(rng.integers(2, 6))
    if amount < n_parts * 1000:
        n_parts = max(2, amount // 1000)
    if n_parts < 2:
        return [amount]
    weights = rng.random(n_parts)
    weights = weights / weights.sum()
    parts = [int(amount * weight // 1000) * 1000 for weight in weights[:-1]]
    parts.append(amount - sum(parts))
    if min(parts) <= 0:
        base = amount // n_parts // 1000 * 1000
        parts = [base] * (n_parts - 1)
        parts.append(amount - base * (n_parts - 1))
    return parts


def place_own_name_accounts(cfg, rng, cases, transfers, accounts, customers) -> tuple[pd.DataFrame, pd.DataFrame]:
    """~15% vụ police và bank_staff trả vào tài khoản mới đứng tên nạn nhân."""
    from sim.step4_mules import next_ids

    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"
    share = float(cfg["cases"]["own_name_safe_account_share"])
    links = pd.read_parquet(observed / "account_device.parquet")
    devices = pd.read_parquet(observed / "devices.parquet")
    rings = pd.read_parquet(truth / "mule_rings.parquet")
    truth_accounts = pd.read_parquet(truth / "accounts_truth.parquet")
    if "mule_type" in truth_accounts.columns:
        old_ids = set(truth_accounts.loc[truth_accounts["mule_type"].astype(str).eq("own_name_safe_account"), "account_id"].astype(str))
        if old_ids:
            accounts = accounts.loc[~accounts["account_id"].astype(str).isin(old_ids)].reset_index(drop=True)
            links = links.loc[~links["account_id"].astype(str).isin(old_ids)].reset_index(drop=True)
            truth_accounts = truth_accounts.loc[~truth_accounts["account_id"].astype(str).isin(old_ids)].reset_index(drop=True)
            rings = rings.loc[~rings["account_id"].astype(str).isin(old_ids)].reset_index(drop=True)
    voice = cases["scam_type"].astype(str).isin(["police", "bank_staff"])
    pool = cases.loc[voice, "case_id"].astype(str).to_numpy()
    n_take = int(round(share * len(pool)))
    if n_take == 0 or len(pool) == 0:
        write_side(accounts, links, truth_accounts, rings)
        return accounts, transfers
    chosen = set(rng.choice(pool, size=n_take, replace=False).tolist())
    name_of = dict(zip(customers["customer_id"].astype(str), customers["name_ascii"].astype(str)))
    banks = [bank for bank in cfg["accounts"]["banks"] if bank != "B01"]
    l2 = rings.loc[rings["layer"].astype(str).eq("l2"), ["ring_id", "account_id"]]
    l2_of: dict[str, list[str]] = {}
    for ring, account in zip(l2["ring_id"].astype(str), l2["account_id"].astype(str)):
        l2_of.setdefault(ring, []).append(account)
    farm = devices.loc[devices["customer_id"].isna(), "device_id"].astype(str)
    farm_ids = set(farm)
    ring_accounts = rings.groupby(rings["ring_id"].astype(str))["account_id"].apply(lambda series: set(series.astype(str))).to_dict()
    device_first = dict(zip(devices["device_id"].astype(str), devices["first_seen"]))
    device_last = dict(zip(devices["device_id"].astype(str), devices["last_seen"]))
    new_ids = next_ids("A", accounts["account_id"], n_take)
    new_accounts = []
    new_links = []
    new_truth = []
    retarget = {}
    for offset, case_id in enumerate(sorted(chosen)):
        case = cases.loc[cases["case_id"].astype(str).eq(case_id)].iloc[0]
        victim = str(case.victim_customer_id)
        ring = str(case.ring_id)
        positive = transfers.loc[transfers["case_id"].astype(str).eq(case_id) & (transfers["planned_amount"] > 0)]
        if positive.empty:
            continue
        first = positive["planned_ts"].min()
        age_minutes = int(rng.integers(1, 2 * 24 * 60 + 1))
        opened = first - pd.Timedelta(minutes=age_minutes)
        account_id = new_ids[offset]
        wallet = bool(rng.random() < 0.30)
        bank = str(rng.choice(banks))
        members = ring_accounts.get(ring, set())
        linked = links.loc[links["account_id"].astype(str).isin(members)]
        farm_links = linked.loc[linked["device_id"].astype(str).isin(farm_ids)]
        device_id = None
        if len(farm_links):
            device_id = str(farm_links["device_id"].astype(str).mode().iloc[0])
        elif len(linked):
            device_id = str(linked["device_id"].astype(str).iloc[0])
        dests = l2_of.get(ring, [])
        if not dests:
            cash = rings.loc[(rings["ring_id"].astype(str).eq(ring)) & (rings["layer"].astype(str).eq("cashout")), "account_id"].astype(str)
            dests = cash.tolist()
        onward = str(rng.choice(dests)) if dests else ""
        delay = int(rng.integers(2, 16))
        new_accounts.append(
            {
                "account_id": account_id,
                "customer_id": pd.NA,
                "phone_number_id": pd.NA,
                "bank_id": bank,
                "account_type": "ewallet" if wallet else "personal",
                "holder_name_ascii": name_of.get(victim, ""),
                "opened_at": opened,
                "opening_balance": np.int64(0),
                "status": "active",
                "merchant_verified": False,
            }
        )
        if device_id:
            first_seen = opened
            device_seen = device_first.get(device_id)
            if pd.notna(device_seen) and device_seen > first_seen:
                first_seen = device_seen
            new_links.append(
                {
                    "account_id": account_id,
                    "device_id": device_id,
                    "login_count": int(rng.integers(1, 6)),
                    "first_seen": first_seen,
                    "last_seen": device_last.get(device_id, pd.NaT),
                }
            )
        new_truth.append(
            {
                "account_id": account_id,
                "is_mule": True,
                "mule_type": "own_name_safe_account",
                "ring_id": ring,
                "layer": "safe",
                "recruited_at": opened,
                "simo_listing_correct": False,
                "forward_to": onward,
                "forward_delay_minutes": delay,
                "source_l1": str(positive["to_mule_account_id"].iloc[0]),
            }
        )
        retarget[case_id] = account_id
    if new_accounts:
        extra = pd.DataFrame(new_accounts)
        for column in accounts.columns:
            if column not in extra.columns:
                extra[column] = pd.NA
        accounts = pd.concat([accounts, extra[accounts.columns]], ignore_index=True)
        accounts["account_id"] = accounts["account_id"].astype("string")
        accounts["customer_id"] = accounts["customer_id"].astype("string")
        accounts["bank_id"] = accounts["bank_id"].astype("string")
        accounts["account_type"] = accounts["account_type"].astype("string")
        accounts["holder_name_ascii"] = accounts["holder_name_ascii"].astype("string")
        accounts["status"] = accounts["status"].astype("string")
        accounts["opening_balance"] = accounts["opening_balance"].astype(np.int64)
    if new_links:
        links = pd.concat([links, pd.DataFrame(new_links)], ignore_index=True)
        links["account_id"] = links["account_id"].astype("string")
        links["device_id"] = links["device_id"].astype("string")
        links["login_count"] = links["login_count"].astype(np.int64)
    truth_extra = pd.DataFrame(new_truth)
    for column in ("forward_to", "forward_delay_minutes", "source_l1"):
        if column not in truth_accounts.columns:
            truth_accounts[column] = pd.NA
    if len(truth_extra):
        for column in truth_accounts.columns:
            if column not in truth_extra.columns:
                truth_extra[column] = pd.NA
        truth_accounts = pd.concat([truth_accounts, truth_extra[truth_accounts.columns]], ignore_index=True)
    truth_accounts["account_id"] = truth_accounts["account_id"].astype("string")
    truth_accounts["is_mule"] = truth_accounts["is_mule"].astype(bool)
    truth_accounts["mule_type"] = truth_accounts["mule_type"].astype("string")
    fee_rows = transfers["extra_fee"].fillna(False).astype(bool) if "extra_fee" in transfers.columns else False
    mask = transfers["case_id"].astype(str).isin(retarget) & (transfers["planned_amount"] > 0) & ~fee_rows
    transfers.loc[mask, "to_mule_account_id"] = transfers.loc[mask, "case_id"].astype(str).map(retarget)
    write_side(accounts, links, truth_accounts, rings)
    print(f"  Tài khoản đứng tên nạn nhân: {len(retarget)}")
    return accounts, transfers


def write_side(accounts, links, truth_accounts, rings) -> None:
    from sim.io import write

    write(accounts, "observed", "accounts")
    write(links, "observed", "account_device")
    write(truth_accounts, "truth", "accounts_truth")
    write(rings, "truth", "mule_rings")


FAST_KINDS = {"police", "bank_staff", "tax_authority", "relative"}


def open_early_rings(rings: pd.DataFrame, sim_start: pd.Timestamp) -> pd.DataFrame:
    """Một mule ngủ không được giữ cả đường dây đóng đến sau ngày 60.

    Giữ đúng 30% đường dây đang hoạt động mở muộn; các đường dây còn lại
    mở trong 20 ngày đầu. Lần chạy sau không kéo thêm vì số ring muộn đã đủ.
    """
    rings = rings.copy()
    rings["ring_id"] = rings["ring_id"].astype(str)
    active_ids = sorted(rings.loc[rings["active_to"] >= sim_start, "ring_id"].unique())
    start_of = rings.groupby("ring_id")["active_from"].min()
    late = [ring for ring in active_ids if start_of[ring] >= sim_start + pd.Timedelta(days=60)]
    keep_n = int(round(0.30 * len(active_ids)))
    if len(late) <= keep_n:
        return rings
    keep = set(sorted(late, key=lambda ring: (start_of[ring], ring), reverse=True)[:keep_n])
    clock = np.random.default_rng(42)
    for ring in sorted(ring for ring in late if ring not in keep):
        opened = sim_start + pd.Timedelta(days=int(clock.integers(0, 20)))
        rings.loc[rings["ring_id"].eq(ring), "active_from"] = opened
    print(f"  Đường dây mở trong 20 ngày đầu: {len(late) - len(keep)}; giữ mở muộn: {len(keep)}")
    return rings


def _pay_times(kind: str, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    if kind in FAST_KINDS or kind == "deposit":
        return [end - pd.Timedelta(minutes=20)]
    if kind == "romance":
        return [start + (end - start) * 0.55, end - pd.Timedelta(hours=8)]
    return [start + (end - start) * frac for frac in (0.35, 0.50, 0.62, 0.74, 0.86, 0.95)]


def schedule_cases(rng, types, spans, rings, sim_start, sim_end, max_cases: int):
    """Rải vụ trong cửa sổ ring. Mỗi mule hoạt động vài ngày; chỉ nghỉ sau khi bị liệt kê hoặc đóng băng.

    Khoản nhanh (police, bank_staff, relative) nằm sau khoản chậm, trong hạn liệt kê sớm nhất
    của kiểu chậm, và không quá 4 vụ trong một ngày.
    """
    ring_span = rings.groupby("ring_id", sort=False).agg(active_from=("active_from", "min"), active_to=("active_to", "max"))
    active_rings = [
        str(ring)
        for ring, row in ring_span.iterrows()
        if row["active_from"] < sim_end and row["active_to"] > sim_start and row["active_to"] >= sim_start
    ]
    l1 = rings.loc[rings["layer"].astype(str) == "l1", ["ring_id", "account_id"]]
    l1_of: dict[str, list[str]] = {}
    for ring, account in zip(l1["ring_id"].astype(str), l1["account_id"].astype(str)):
        l1_of.setdefault(ring, []).append(account)
    n_cases = len(types)
    ring_of = [""] * n_cases
    mule_of = [""] * n_cases
    start_of: list[pd.Timestamp] = [sim_start] * n_cases
    end_of: list[pd.Timestamp] = [sim_start] * n_cases
    mules = []
    for ring in active_rings:
        window_from = max(sim_start, pd.Timestamp(ring_span.loc[ring, "active_from"]))
        window_to = min(sim_end - pd.Timedelta(minutes=1), pd.Timestamp(ring_span.loc[ring, "active_to"]))
        if window_to <= window_from:
            continue
        for account in sorted(l1_of.get(ring, [])):
            mules.append(
                {
                    "id": account,
                    "ring": ring,
                    "window_from": window_from,
                    "window_to": window_to,
                    "days": (window_to - window_from).total_seconds() / 86400.0,
                    "usage": 0,
                    "n_invest": 0,
                    "romance": False,
                    "cushion": 0,
                    "spell_start": None,
                    "spell_end": None,
                    "day_count": {},
                }
            )
    long_mules = sorted((mule for mule in mules if mule["days"] >= 28), key=lambda mule: (-mule["days"], mule["id"]))[:40]
    long_ids = {mule["id"] for mule in long_mules}
    mid_mules = [mule for mule in mules if mule["id"] not in long_ids]
    by_kind: dict[str, list[int]] = {}
    for index, kind in enumerate(types):
        by_kind.setdefault(str(kind), []).append(index)
    spell_long = pd.Timedelta(days=24)
    spell_mid = pd.Timedelta(days=7)
    margin = pd.Timedelta(hours=48)

    def reserve_spell(mule, spell: pd.Timedelta) -> bool:
        if mule["spell_start"] is not None:
            return True
        room = mule["window_to"] - mule["window_from"] - spell - margin
        if room.total_seconds() < 0:
            return False
        seconds = max(int(room.total_seconds()), 1)
        shift = int(rng.integers(0, seconds))
        mule["spell_start"] = mule["window_from"] + pd.to_timedelta(shift, unit="s")
        mule["spell_end"] = mule["spell_start"] + spell
        return True

    def pay_days(kind: str, start: pd.Timestamp, end: pd.Timestamp) -> set:
        return {stamp.tz_convert(TZ).date() for stamp in _pay_times(kind, start, end)}

    def accept(mule, index: int, kind: str, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        if mule["usage"] >= max_cases or start < mule["window_from"] or end > mule["window_to"] or end >= sim_end:
            return False
        days = pay_days(kind, start, end)
        if any(mule["day_count"].get(day, 0) >= 4 for day in days):
            return False
        for day in days:
            mule["day_count"][day] = mule["day_count"].get(day, 0) + 1
        mule["usage"] += 1
        if kind == "investment":
            mule["n_invest"] += 1
            mule["cushion"] = max(mule["cushion"], 3)
        elif kind == "romance":
            mule["romance"] = True
            mule["cushion"] = max(mule["cushion"], 7)
        ring_of[index] = mule["ring"]
        mule_of[index] = mule["id"]
        start_of[index] = start
        end_of[index] = end
        return True

    def place_slow(index: int, kind: str, pool, spell: pd.Timedelta, limit: int) -> bool:
        for mule in pool:
            if kind == "investment" and mule["n_invest"] >= limit:
                continue
            if kind == "romance" and mule["romance"]:
                continue
            if not reserve_spell(mule, spell):
                continue
            if accept(mule, index, kind, mule["spell_start"], mule["spell_end"]):
                return True
        return False

    for index in by_kind.get("investment", []):
        if place_slow(index, "investment", long_mules, spell_long, 4):
            continue
        if place_slow(index, "investment", mid_mules, spell_mid, 3):
            continue
        if not place_slow(index, "investment", mules, spell_mid, max_cases):
            raise RuntimeError("Không xếp được vụ đầu tư vào mule lớp 1")
    for index in by_kind.get("romance", []):
        if place_slow(index, "romance", mid_mules, spell_mid, max_cases):
            continue
        if not place_slow(index, "romance", mules, spell_mid, max_cases):
            raise RuntimeError("Không xếp được vụ tình cảm vào mule lớp 1")

    slots = []
    for mule in mules:
        if mule["spell_start"] is None and not reserve_spell(mule, pd.Timedelta(hours=6)):
            continue
        for cluster in (0, 1):
            if cluster == 1 and mule["cushion"] < 3:
                continue
            for slot in range(4):
                slots.append((0 if cluster == 0 else 1 if mule["id"] in long_ids else 2, mule, cluster, slot))
    slots.sort(key=lambda item: (item[0], item[1]["id"], item[3]))
    used_slots: set[tuple[str, int, int]] = set()

    def take_slot(index: int, kind: str, span: pd.Timedelta) -> bool:
        for _order, mule, cluster, slot in slots:
            key = (mule["id"], cluster, slot)
            if key in used_slots or mule["usage"] >= max_cases:
                continue
            stamp = mule["spell_end"] + pd.Timedelta(hours=20 if cluster == 0 else 44) + pd.Timedelta(minutes=15 * slot)
            end = stamp + pd.Timedelta(minutes=20)
            start = end - span
            if start < mule["window_from"]:
                start = mule["window_from"]
            if accept(mule, index, kind, start, end):
                used_slots.add(key)
                return True
        return False

    for kind in ("deposit", "police", "bank_staff", "tax_authority", "relative"):
        for index in by_kind.get(kind, []):
            if not take_slot(index, kind, spans[index]):
                raise RuntimeError(f"Không xếp được vụ {kind} vào mule lớp 1")
    return ring_of, mule_of, start_of, end_of


def main() -> None:
    cfg = load_config()
    spec = cfg["cases"]
    rng = np.random.default_rng(int(cfg["seed"]))
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    observed = ROOT / "out" / "observed"
    truth = ROOT / "out" / "truth"

    customers = pd.read_parquet(observed / "customers.parquet")
    latent = pd.read_parquet(truth / "customers_latent.parquet")
    phones = pd.read_parquet(observed / "phone_numbers.parquet")
    accounts = pd.read_parquet(observed / "accounts.parquet")
    transactions = pd.read_parquet(observed / "transactions.parquet")
    floor_path = truth / "step6_floor.parquet"
    filled = "session_id" in transactions.columns and transactions["session_id"].notna().any()
    if filled and floor_path.exists():
        floor = int(pd.read_parquet(floor_path)["first_tx_num"].iloc[0])
        tx_num = transactions["tx_id"].astype(str).str[1:].astype(int)
        transactions = transactions.loc[tx_num < floor].reset_index(drop=True)
    rings = open_early_rings(pd.read_parquet(truth / "mule_rings.parquet"), sim_start)
    write(rings, "truth", "mule_rings")
    prior_numbers = truth / "truth_numbers.parquet"
    if prior_numbers.exists():
        old_ids = set(pd.read_parquet(prior_numbers, columns=["number_id"])["number_id"].astype(str))
        phones = phones.loc[~phones["number_id"].astype(str).isin(old_ids)].reset_index(drop=True)

    customers = customers.merge(latent[["customer_id", "susceptibility"]], on="customer_id", how="left")
    customers["customer_id"] = customers["customer_id"].astype(str)
    age = (sim_start.year - customers["birth_year"].to_numpy()).astype(float)
    sus = np.clip(customers["susceptibility"].to_numpy(dtype=float), 1e-4, 1 - 1e-4)
    logit_s = np.log(sus / (1.0 - sus))
    fit = type_fit(age)

    mule_accounts = set(rings["account_id"].astype(str))
    mule_customers = set(
        accounts.loc[accounts["account_id"].isin(mule_accounts) & accounts["customer_id"].notna(), "customer_id"].astype(str)
    )
    owned = accounts.loc[
        accounts["customer_id"].notna()
        & accounts["account_type"].isin(["personal", "savings"])
        & accounts["status"].astype(str).eq("active"),
        ["account_id", "customer_id", "opening_balance", "opened_at", "bank_id"],
    ].copy()
    owned["account_id"] = owned["account_id"].astype(str)
    owned["customer_id"] = owned["customer_id"].astype(str)
    opening_sum = owned.groupby("customer_id")["opening_balance"].sum()
    wealth = customers["customer_id"].map(opening_sum).fillna(0).to_numpy(dtype=np.int64)
    not_mule = ~customers["customer_id"].isin(mule_customers).to_numpy()
    floors = {
        "police": 1_000_000,
        "tax_authority": 1_000_000,
        "bank_staff": 1_000_000,
        "investment": 30_000_000,
        "romance": 3_000_000,
        "deposit": 2_000_000,
        "relative": 8_000_000,
    }
    n_invest_pool = int((not_mule & (wealth >= floors["investment"])).sum())
    if n_invest_pool < int(spec["type_share"]["investment"] * int(spec["n_cases"])) * 2:
        floors["investment"] = 15_000_000

    n_cases = int(spec["n_cases"])
    type_names = list(spec["type_share"])
    type_counts = largest_remainder(type_names, [float(spec["type_share"][name]) for name in type_names], n_cases)
    types = np.repeat(type_names, [type_counts[name] for name in type_names]).astype(object)
    rng.shuffle(types)

    def draw_flag(share: float) -> np.ndarray:
        mask = np.zeros(n_cases, dtype=bool)
        count = int(round(float(share) * n_cases))
        mask[rng.choice(n_cases, size=count, replace=False)] = True
        return mask

    is_ott = draw_flag(spec["ott_share"])
    is_spoof = draw_flag(spec["spoofed_caller_share"])
    is_split = draw_flag(spec["split_share"])
    is_decline = draw_flag(spec["declines_permission_share"])
    is_hangup = draw_flag(spec["hangs_up_before_transfer_share"])
    invest_slots = np.flatnonzero(types == "investment")
    n_flat = int(round(float(cfg["sessions"]["investment_non_monotonic_share"]) * len(invest_slots)))
    flat_slots = set(rng.choice(invest_slots, size=n_flat, replace=False).tolist()) if n_flat else set()

    eligible = {kind: not_mule & (wealth >= floors[kind]) for kind in type_names}
    noise = rng.standard_normal(len(customers))

    def select(alpha: float) -> np.ndarray:
        used = np.zeros(len(customers), dtype=bool)
        chosen = np.full(n_cases, -1, dtype=int)
        for kind in type_names:
            slots = np.flatnonzero(types == kind)
            score = alpha * logit_s + 0.35 * fit[kind] + noise
            score = np.where(eligible[kind] & ~used, score, -1e18)
            take = np.argpartition(-score, len(slots) - 1)[: len(slots)]
            if float(score[take].min()) < -1e17:
                raise RuntimeError(f"Không đủ nạn nhân cho kiểu {kind}")
            chosen[slots] = take
            used[take] = True
        return chosen

    best_alpha, best_gap = 0.8, 1.0
    low, high = -0.4, 2.5
    for _ in range(16):
        mid = (low + high) / 2.0
        picked = select(mid)
        label = np.zeros(len(customers), dtype=bool)
        label[np.unique(picked)] = True
        score = auc_score(label, sus)
        if abs(score - 0.70) < best_gap:
            best_alpha, best_gap = mid, abs(score - 0.70)
        if score > 0.70:
            high = mid
        else:
            low = mid
    victim_row = select(best_alpha)

    spans = [case_span(str(kind), rng) for kind in types]
    ring_of, mule_of, start_of, end_of = schedule_cases(
        rng, types, spans, rings, sim_start, sim_end, int(spec["l1_mule_max_cases"])
    )

    n_repeat = int(round(float(spec["repeat_victim_share"]) * n_cases))
    by_time = sorted(range(n_cases), key=lambda index: start_of[index])
    hosts = {index: False for index in range(n_cases)}
    repeats = 0
    for index in reversed(by_time):
        if repeats >= n_repeat:
            break
        candidates = [
            other
            for other in by_time
            if other != index
            and end_of[other] <= start_of[index]
            and not hosts[other]
            and victim_row[other] != victim_row[index]
        ]
        if not candidates:
            continue
        host = int(candidates[int(rng.integers(0, len(candidates)))])
        victim_row[index] = victim_row[host]
        hosts[host] = True
        hosts[index] = True
        repeats += 1
    if repeats < n_repeat:
        raise RuntimeError(f"Chỉ ghép được {repeats}/{n_repeat} nạn nhân tái phạm")

    opening = dict(zip(owned["account_id"], owned["opening_balance"].astype(np.int64)))
    opened = dict(zip(owned["account_id"], owned["opened_at"]))
    accounts_of = owned.groupby("customer_id")["account_id"].apply(list).to_dict()
    curves = build_curves(transactions, owned)
    customer_ids = customers["customer_id"].to_numpy()

    def pool(customer: str, when: pd.Timestamp, committed: list[tuple[pd.Timestamp, int]]) -> int:
        total = 0
        for account in accounts_of.get(customer, []):
            total += max(0, balance_at(account, when, opening, opened, curves))
        spent = sum(amount for stamp, amount in committed if stamp <= when)
        return int(total - spent)

    # Kẻ gian: một phần dùng nhiều số. Vụ giả mạo chỉ dùng nhóm số giả mạo.
    n_scammers = 1000
    sizes = np.ones(n_scammers, dtype=int)
    multi = rng.choice(n_scammers, size=int(round(0.22 * n_scammers)), replace=False)
    sizes[multi] = 2
    sizes[rng.choice(multi, size=max(1, len(multi) // 5), replace=False)] = 3
    number_owner = np.repeat(np.arange(n_scammers), sizes)
    n_numbers = len(number_owner)
    spoof_scammers = set(rng.choice(n_scammers, size=max(40, n_scammers // 8), replace=False).tolist())
    spoof_cases = np.flatnonzero(is_spoof)
    plain_cases = np.flatnonzero(~is_spoof)
    spoof_list = sorted(spoof_scammers)
    plain_list = [index for index in range(n_scammers) if index not in spoof_scammers]
    scammer_of = np.full(n_cases, -1, dtype=int)
    scammer_of[spoof_cases] = np.resize(np.array(spoof_list), len(spoof_cases))
    rng.shuffle(scammer_of[spoof_cases])
    scammer_of[plain_cases] = np.resize(np.array(plain_list), len(plain_cases))
    rng.shuffle(scammer_of[plain_cases])
    numbers_of = [np.flatnonzero(number_owner == scammer).tolist() for scammer in range(n_scammers)]

    new_number_ids = next_ids("P", phones["number_id"], n_numbers)
    telco_names = list(cfg["population"]["telco_share"])
    telco_counts = largest_remainder(telco_names, [float(cfg["population"]["telco_share"][name]) for name in telco_names], n_numbers)
    telco = np.repeat(telco_names, [telco_counts[name] for name in telco_names]).astype(object)
    rng.shuffle(telco)
    msisdn = assign_msisdn(telco, rng)
    taken = set(phones["msisdn"].astype(str))
    for _ in range(6):
        clash = np.array([item in taken for item in msisdn])
        if not clash.any():
            break
        msisdn[clash] = assign_msisdn(telco[clash], rng)
    if any(item in taken for item in msisdn) or len(set(msisdn)) != len(msisdn):
        raise RuntimeError("msisdn kẻ gian bị trùng")

    new_sim = np.zeros(n_numbers, dtype=bool)
    new_sim[rng.choice(n_numbers, size=int(round(float(spec["scammer_number_new_sim_share"]) * n_numbers)), replace=False)] = True
    reported = np.zeros(n_numbers, dtype=np.int64)
    reported_idx = rng.choice(n_numbers, size=int(round(float(spec["scammer_number_prereported_share"]) * n_numbers)), replace=False)
    reported[reported_idx] = rng.integers(1, 7, size=len(reported_idx))
    business_numbers = phones.loc[phones["owner_type"].astype(str) == "business", "number_id"].astype(str).to_numpy()
    if len(business_numbers) == 0:
        business_numbers = phones["number_id"].astype(str).to_numpy()
    display_of = {}
    for scammer in spoof_scammers:
        display_of[scammer] = str(business_numbers[int(rng.integers(0, len(business_numbers)))])

    contact_rows = []
    transfer_rows = []
    first_use = [sim_end] * n_numbers
    committed_of: dict[str, list[tuple[pd.Timestamp, int]]] = {}
    fee_share = float(rng.uniform(float(spec["extra_fee_share_min"]), float(spec["extra_fee_share_max"])))
    fee_slots = np.flatnonzero(np.isin(types, list(FEE_KINDS)))
    n_fee_cases = int(round(fee_share * len(fee_slots)))
    fee_cases = set(rng.choice(fee_slots, size=n_fee_cases, replace=False).tolist()) if n_fee_cases else set()
    personal_limit = (
        accounts.loc[
            accounts["customer_id"].notna() & accounts["account_type"].astype(str).eq("personal"),
            ["customer_id", "limit_per_txn"],
        ]
        .assign(customer_id=lambda frame: frame["customer_id"].astype(str))
        .groupby("customer_id")["limit_per_txn"]
        .max()
        .astype(np.int64)
        .to_dict()
    )
    savings_of = (
        accounts.loc[accounts["account_type"].astype(str).eq("savings") & accounts["customer_id"].notna()]
        .assign(customer_id=lambda frame: frame["customer_id"].astype(str))
        .groupby("customer_id")["account_id"]
        .apply(lambda values: str(values.iloc[0]))
        .to_dict()
    )
    n_payees = max(n_fee_cases * 3, 1)
    payee_ids = next_ids("A", accounts["account_id"], n_payees)
    payee_cursor = 0

    def remember(number_index: int, stamp: pd.Timestamp) -> None:
        if stamp < first_use[number_index]:
            first_use[number_index] = stamp

    for index in sorted(range(n_cases), key=lambda item: (start_of[item], item)):
        customer = str(customer_ids[int(victim_row[index])])
        kind = str(types[index])
        channel = "ott" if is_ott[index] else "cellular"
        case_id = f"K{index + 1:06d}"
        number_indexes = numbers_of[int(scammer_of[index])]
        number_ids = [new_number_ids[item] for item in number_indexes]
        start = start_of[index]
        end = end_of[index]
        mule = mule_of[index]
        committed = committed_of.setdefault(customer, [])
        if kind in {"police", "bank_staff", "tax_authority", "relative", "deposit"}:
            pay_times = [end - pd.Timedelta(minutes=20)]
        elif kind == "romance":
            pay_times = [start + (end - start) * 0.55, end - pd.Timedelta(hours=8)]
        else:
            pay_times = [start + (end - start) * frac for frac in (0.35, 0.50, 0.62, 0.74, 0.86, 0.95)]
        pay_times = [min(max(stamp, start + pd.Timedelta(minutes=40)), end - pd.Timedelta(minutes=15)) for stamp in pay_times]
        avail = [pool(customer, stamp, committed) for stamp in pay_times]
        cap = min(avail) if avail else 0
        savings_first = kind == "police" and rng.random() < 0.75 or kind == "tax_authority" and rng.random() < 0.55 or kind == "bank_staff" and rng.random() < 0.4
        amounts: list[int] = []
        interest: list[int] = []
        if kind == "investment":
            if rng.random() < 0.72:
                interest = [-int(rng.integers(200_000, 1_500_001) // 1000 * 1000)]
                if rng.random() < 0.35:
                    interest.append(-int(rng.integers(200_000, 1_000_001) // 1000 * 1000))
            amounts = increasing_amounts(max(cap, 0), rng, bool(is_split[index]))
        elif kind == "police":
            amounts = [round_vnd(float(rng.uniform(0.60, 0.98)) * max(cap, 0), max(cap, 0))]
        elif kind == "tax_authority":
            amounts = [round_vnd(float(rng.uniform(0.45, 0.90)) * max(cap, 0), max(cap, 0))]
        elif kind == "bank_staff":
            amounts = [round_vnd(float(rng.uniform(0.25, 0.70)) * max(cap, 0), max(cap, 0))]
        elif kind == "deposit":
            amounts = [round_vnd(float(rng.integers(1_000_000, 10_000_001)), max(cap, 0))]
        elif kind == "relative":
            amounts = [round_vnd(float(rng.integers(5_000_000, 30_000_001)), max(cap, 0))]
        else:
            gifts = int(rng.integers(1, 3))
            amounts = [round_vnd(float(rng.integers(1_000_000, 15_000_001)), max(cap // gifts, 0)) for _ in range(gifts)]
        if kind != "investment" and is_split[index] and amounts:
            amounts = split_amount(sum(amounts), rng)
        if kind == "investment" and (len(amounts) < 2 or any(amounts[i] <= amounts[i - 1] for i in range(1, len(amounts)))):
            amounts = increasing_amounts(max(cap, 0), rng, False)
        if kind == "investment" and index in flat_slots:
            amounts = flatten_peak(amounts, rng)
        seq = 1
        interest_times = [start + pd.Timedelta(days=1 + offset) for offset in range(len(interest))]
        if kind == "investment":
            interest_times = [min(stamp, pay_times[0] - pd.Timedelta(hours=6)) for stamp in interest_times]
        for stamp, amount in zip(interest_times, interest):
            transfer_rows.append(
                {
                    "case_id": case_id,
                    "seq": seq,
                    "planned_ts": stamp,
                    "planned_amount": int(amount),
                    "to_mule_account_id": mule,
                    "from_own_savings_first": False,
                    "coached_purpose_text": "tra lai",
                }
            )
            committed.append((stamp, int(amount)))
            seq += 1
        cursor = 0
        for amount in amounts:
            if amount <= 0:
                continue
            if kind == "investment":
                stamp = pay_times[min(cursor, len(pay_times) - 1)] + pd.Timedelta(minutes=int(cursor))
                cursor += 1
            elif is_split[index]:
                slot = seq - (1 if not interest else len(interest) + 1)
                stamp = pay_times[0] - pd.Timedelta(minutes=int(35 * (len(amounts) - 1 - max(slot, 0))))
            else:
                stamp = pay_times[min(max(seq - 1, 0), len(pay_times) - 1)]
            if stamp <= start:
                stamp = start + pd.Timedelta(minutes=30 + seq)
            if stamp >= end:
                stamp = end - pd.Timedelta(minutes=10 + seq)
            room = pool(customer, stamp, committed)
            amount = min(int(amount), round_vnd(amount, max(room, 0)))
            if amount <= 0:
                continue
            transfer_rows.append(
                {
                    "case_id": case_id,
                    "seq": seq,
                    "planned_ts": stamp,
                    "planned_amount": int(amount),
                    "to_mule_account_id": mule,
                    "from_own_savings_first": bool(savings_first and seq == (1 if not interest else len(interest) + 1)),
                    "coached_purpose_text": str(rng.choice(PURPOSE[kind])),
                }
            )
            committed.append((stamp, int(amount)))
            seq += 1

        if index in fee_cases:
            positives = [row for row in transfer_rows if row["case_id"] == case_id and int(row["planned_amount"]) > 0]
            anchor = min(row["planned_ts"] for row in positives) if positives else end - pd.Timedelta(minutes=30)
            per_txn = int(personal_limit.get(customer, 100_000_000))
            n_fee = int(rng.integers(1, 4))
            for fee_i in range(n_fee):
                stamp = anchor - pd.Timedelta(minutes=15 + 8 * fee_i)
                if stamp <= start:
                    stamp = start + pd.Timedelta(minutes=8 + fee_i)
                if stamp >= end:
                    stamp = end - pd.Timedelta(minutes=12 + fee_i)
                room = pool(customer, stamp, committed)
                mobilize = str(MOBILIZE[int(rng.integers(0, len(MOBILIZE)))])
                inflow = 0
                if mobilize == "split_near_limit" and room < int(1.2 * per_txn):
                    mobilize = "limit_raise"
                if mobilize in {"quick_loan_disbursement", "credit_card_cash_advance"}:
                    amount = int(rng.integers(2_000_000, 12_000_001) // 1000 * 1000)
                    inflow = amount
                elif mobilize == "split_near_limit":
                    pieces = int(rng.integers(2, 4))
                    amount = int(min(room, pieces * per_txn) // 1000 * 1000)
                    if amount < 2_000_000:
                        mobilize = "limit_raise"
                        amount = int(min(max(room // 5, 0), 5_000_000) // 1000 * 1000)
                elif mobilize == "savings_liquidation" and customer not in savings_of:
                    mobilize = "quick_loan_disbursement"
                    amount = int(rng.integers(2_000_000, 12_000_001) // 1000 * 1000)
                    inflow = amount
                else:
                    amount = int(min(max(room // 4, 0), int(rng.integers(1_000_000, 8_000_001))) // 1000 * 1000)
                if mobilize != "split_near_limit" and inflow == 0 and amount > room:
                    amount = int(room // 1000 * 1000)
                if amount < 100_000:
                    continue
                if inflow == 0:
                    reducible = [row for row in transfer_rows if row["case_id"] == case_id and int(row["planned_amount"]) > 0 and not row.get("extra_fee")]
                    left = amount
                    for row in reversed(reducible):
                        take = min(int(row["planned_amount"]) - 1000, left)
                        if take <= 0:
                            continue
                        previous = int(row["planned_amount"])
                        row["planned_amount"] = previous - take
                        for slot, (old_stamp, old_amount) in enumerate(committed):
                            if old_stamp == row["planned_ts"] and old_amount == previous:
                                committed[slot] = (old_stamp, previous - take)
                                break
                        left -= take
                        if left <= 0:
                            break
                    amount -= left
                    amount = int(amount // 1000 * 1000)
                if amount < 100_000:
                    continue
                payee = payee_ids[payee_cursor % n_payees]
                payee_cursor += 1
                transfer_rows.append(
                    {
                        "case_id": case_id,
                        "seq": seq,
                        "planned_ts": stamp,
                        "planned_amount": int(amount),
                        "to_mule_account_id": payee,
                        "from_own_savings_first": False,
                        "coached_purpose_text": "phi bo sung",
                        "extra_fee": True,
                        "mobilize": mobilize,
                        "inflow_amount": int(inflow),
                    }
                )
                committed.append((stamp, int(amount) - int(inflow)))
                seq += 1

        transfer_ts = [row["planned_ts"] for row in transfer_rows if row["case_id"] == case_id and row["planned_amount"] > 0]
        anchor = transfer_ts[0] if transfer_ts else end - pd.Timedelta(minutes=20)
        if kind in {"police", "bank_staff", "tax_authority"}:
            duration = int(rng.integers(20, 91)) * 60
            call_at = anchor - pd.Timedelta(seconds=duration + int(rng.integers(60, 480)))
            if call_at < start:
                call_at = start
            contact_rows.append((case_id, call_at, channel, number_ids[0], duration, number_indexes[0]))
            for extra, number_index in enumerate(number_indexes[1:], start=1):
                sms_at = call_at + pd.Timedelta(minutes=extra)
                contact_rows.append((case_id, sms_at, "sms", new_number_ids[number_index], 15, number_index))
        elif kind == "relative":
            duration = int(rng.integers(30, 181))
            call_at = max(start, anchor - pd.Timedelta(minutes=int(rng.integers(2, 25))))
            contact_rows.append((case_id, call_at, channel, number_ids[0], duration, number_indexes[0]))
            for extra, number_index in enumerate(number_indexes[1:], start=1):
                contact_rows.append(
                    (case_id, call_at + pd.Timedelta(minutes=extra), "sms", new_number_ids[number_index], 10, number_index)
                )
        else:
            n_msg = int(rng.integers(12, 34) if kind == "investment" else rng.integers(6, 18) if kind == "romance" else rng.integers(2, 6))
            span_s = max(int((anchor - start).total_seconds()), 60)
            offsets = np.sort(rng.integers(0, span_s, size=n_msg))
            msg_channel = "ott" if channel == "ott" else "sms"
            for offset_i, offset in enumerate(offsets):
                number_index = number_indexes[offset_i % len(number_indexes)]
                contact_rows.append(
                    (
                        case_id,
                        start + pd.to_timedelta(int(offset), unit="s"),
                        msg_channel,
                        new_number_ids[number_index],
                        int(rng.integers(5, 45)),
                        number_index,
                    )
                )

    for row in contact_rows:
        remember(int(row[5]), row[1])

    registered = []
    for index in range(n_numbers):
        first = first_use[index]
        if first >= sim_end:
            first = sim_start + pd.Timedelta(days=1)
            first_use[index] = first
        if new_sim[index]:
            registered.append(first - pd.to_timedelta(int(rng.integers(1, 31)) * 86400 + int(rng.integers(0, 86400)), unit="s"))
        else:
            old_start = as_timestamp("2014-01-01")
            old_end = as_timestamp("2022-06-01")
            seconds = int((old_end - old_start).total_seconds())
            registered.append(old_start + pd.to_timedelta(int(rng.integers(0, seconds)), unit="s"))

    new_phones = pd.DataFrame(
        {
            "number_id": pd.Series(new_number_ids, dtype="string"),
            "msisdn": pd.Series(msisdn, dtype="string"),
            "telco": pd.Series(telco, dtype="string"),
            "owner_type": pd.Series(["external_person"] * n_numbers, dtype="string"),
            "owner_customer_id": pd.Series([pd.NA] * n_numbers, dtype="string"),
            "registered_at": registered,
            "reported_count": reported.astype(np.int64),
        }
    )
    phones_out = pd.concat([phones, new_phones], ignore_index=True)
    phones_out["number_id"] = phones_out["number_id"].astype("string")
    phones_out["msisdn"] = phones_out["msisdn"].astype("string")
    phones_out["telco"] = phones_out["telco"].astype("string")
    phones_out["owner_type"] = phones_out["owner_type"].astype("string")
    phones_out["owner_customer_id"] = phones_out["owner_customer_id"].astype("string")
    phones_out["reported_count"] = phones_out["reported_count"].astype(np.int64)

    truth_numbers = pd.DataFrame(
        {
            "number_id": pd.Series(new_number_ids, dtype="string"),
            "is_scammer": True,
            "spoofs_display": [int(number_owner[index]) in spoof_scammers for index in range(n_numbers)],
            "spoofed_as_number_id": [
                display_of.get(int(number_owner[index]), pd.NA) for index in range(n_numbers)
            ],
        }
    )
    truth_numbers["is_scammer"] = truth_numbers["is_scammer"].astype(bool)
    truth_numbers["spoofs_display"] = truth_numbers["spoofs_display"].astype(bool)
    truth_numbers["spoofed_as_number_id"] = truth_numbers["spoofed_as_number_id"].astype("string")

    contacts = pd.DataFrame(contact_rows, columns=["case_id", "ts", "channel", "number_id", "duration_s", "number_index"])
    contacts = contacts.drop(columns=["number_index"])
    contacts["case_id"] = contacts["case_id"].astype("string")
    contacts["channel"] = contacts["channel"].astype("string")
    contacts["number_id"] = contacts["number_id"].astype("string")
    contacts["duration_s"] = contacts["duration_s"].astype(np.int64)

    transfers = pd.DataFrame(transfer_rows)
    transfers["case_id"] = transfers["case_id"].astype("string")
    transfers["seq"] = transfers["seq"].astype(np.int64)
    transfers["planned_amount"] = transfers["planned_amount"].astype(np.int64)
    transfers["to_mule_account_id"] = transfers["to_mule_account_id"].astype("string")
    transfers["from_own_savings_first"] = transfers["from_own_savings_first"].astype(bool)
    transfers["coached_purpose_text"] = transfers["coached_purpose_text"].astype("string")
    if "extra_fee" not in transfers.columns:
        transfers["extra_fee"] = False
    transfers["extra_fee"] = transfers["extra_fee"].fillna(False).astype(bool)
    if "mobilize" not in transfers.columns:
        transfers["mobilize"] = ""
    transfers["mobilize"] = transfers["mobilize"].fillna("").astype("string")
    if "inflow_amount" not in transfers.columns:
        transfers["inflow_amount"] = 0
    transfers["inflow_amount"] = transfers["inflow_amount"].fillna(0).astype(np.int64)
    victim_of_case = {f"K{index + 1:06d}": str(customer_ids[int(victim_row[index])]) for index in range(n_cases)}
    spent_clip: dict[str, list[tuple[pd.Timestamp, int]]] = {}
    for idx in transfers.sort_values(["planned_ts", "case_id", "seq"], kind="mergesort").index:
        customer = victim_of_case[str(transfers.at[idx, "case_id"])]
        when = transfers.at[idx, "planned_ts"]
        base = sum(max(0, balance_at(account, when, opening, opened, curves)) for account in accounts_of.get(customer, []))
        already = sum(amount for stamp, amount in spent_clip.get(customer, []) if stamp <= when)
        inflow = int(transfers.at[idx, "inflow_amount"])
        available = base - already + inflow
        amount = int(transfers.at[idx, "planned_amount"])
        if amount > available:
            amount = int(max(available, 0) // 1000 * 1000)
            transfers.at[idx, "planned_amount"] = amount
        spent_clip.setdefault(customer, []).append((when, amount - inflow))
    used_payees = payee_cursor
    given = ["nguyen", "tran", "le", "pham", "hoang", "vu", "dang", "bui"]
    family = ["an", "binh", "cuong", "dung", "hoa", "khoa", "lan", "minh", "nam", "phuong"]
    payee_rows = []
    for offset in range(n_payees):
        payee_rows.append(
            {
                "account_id": payee_ids[offset],
                "customer_id": pd.NA,
                "phone_number_id": pd.NA,
                "bank_id": "B03",
                "account_type": "personal",
                "holder_name_ascii": f"{given[offset % len(given)]} {family[(offset // len(given)) % len(family)]} {offset % 1000}",
                "opened_at": sim_start - pd.Timedelta(days=400 + offset % 2000),
                "opening_balance": np.int64(0),
                "status": "active",
                "merchant_verified": False,
                "limit_per_txn": np.int64(int(cfg["limits"]["per_txn"]["normal"])),
                "limit_per_day": np.int64(int(cfg["limits"]["per_day"]["normal"])),
            }
        )
    payee_accounts = pd.DataFrame(payee_rows)
    for column in accounts.columns:
        if column not in payee_accounts.columns:
            payee_accounts[column] = pd.NA
    accounts = pd.concat([accounts, payee_accounts[accounts.columns]], ignore_index=True)
    normal_txn = np.int64(int(cfg["limits"]["per_txn"]["normal"]))
    normal_day = np.int64(int(cfg["limits"]["per_day"]["normal"]))
    accounts["limit_per_txn"] = accounts["limit_per_txn"].fillna(normal_txn).astype(np.int64)
    accounts["limit_per_day"] = accounts["limit_per_day"].fillna(normal_day).astype(np.int64)
    _ = used_payees
    planned_total = (
        transfers.loc[transfers["planned_amount"] > 0]
        .groupby("case_id")["planned_amount"]
        .sum()
    )
    case_numbers = []
    for index in range(n_cases):
        case_numbers.append([new_number_ids[item] for item in numbers_of[int(scammer_of[index])]])
    cases = pd.DataFrame(
        {
            "case_id": pd.Series([f"K{index + 1:06d}" for index in range(n_cases)], dtype="string"),
            "victim_customer_id": pd.Series(customer_ids[victim_row], dtype="string"),
            "scam_type": pd.Series(types, dtype="string"),
            "scammer_number_ids": case_numbers,
            "ring_id": pd.Series(ring_of, dtype="string"),
            "start_ts": start_of,
            "end_ts": end_of,
            "channel": pd.Series(np.where(is_ott, "ott", "cellular"), dtype="string"),
            "spoofed": is_spoof,
            "split": is_split,
            "declines_permission": is_decline,
            "hangs_up_before_transfer": is_hangup,
            "planned_total": [int(planned_total.get(f"K{index + 1:06d}", 0)) for index in range(n_cases)],
            "l1_account_id": pd.Series(mule_of, dtype="string"),
            "raise_during_call": False,
            "fake_hotline": False,
        }
    )
    for kind, share in spec["limit_raise_on_call"].items():
        slots = np.flatnonzero(cases["scam_type"].astype(str).eq(kind))
        n_raise = int(round(float(share) * len(slots)))
        if n_raise:
            cases.loc[cases.index[rng.choice(slots, size=n_raise, replace=False)], "raise_during_call"] = True
    bank_slots = np.flatnonzero(cases["scam_type"].astype(str).eq("bank_staff"))
    n_fake = int(round(float(spec["bank_staff_fake_hotline_share"]) * len(bank_slots)))
    if n_fake:
        cases.loc[cases.index[rng.choice(bank_slots, size=n_fake, replace=False)], "fake_hotline"] = True
    cases["planned_total"] = cases["planned_total"].astype(np.int64)
    for column in ("spoofed", "split", "declines_permission", "hangs_up_before_transfer", "raise_during_call", "fake_hotline"):
        cases[column] = cases[column].astype(bool)

    # Mốc vụ là cửa sổ đã gán cho đường dây. Kéo liên lạc và khoản tiền vào trong cửa sổ đó.
    window = dict(zip(cases["case_id"].astype(str), zip(cases["start_ts"], cases["end_ts"])))

    def clamp_stamp(case_id: str, stamp: pd.Timestamp) -> pd.Timestamp:
        start, end = window[str(case_id)]
        if stamp < start:
            return start + pd.Timedelta(seconds=1)
        if stamp >= end:
            return end - pd.Timedelta(seconds=1)
        return stamp

    contacts["ts"] = [clamp_stamp(case_id, stamp) for case_id, stamp in zip(contacts["case_id"], contacts["ts"])]
    transfers["planned_ts"] = [
        clamp_stamp(case_id, stamp) for case_id, stamp in zip(transfers["case_id"], transfers["planned_ts"])
    ]
    accounts, transfers = place_own_name_accounts(cfg, rng, cases, transfers, accounts, customers)
    spent_final: dict[str, list[tuple[pd.Timestamp, int]]] = {}
    for idx in transfers.sort_values(["planned_ts", "case_id", "seq"], kind="mergesort").index:
        customer = victim_of_case[str(transfers.at[idx, "case_id"])]
        when = transfers.at[idx, "planned_ts"]
        base = sum(max(0, balance_at(account, when, opening, opened, curves)) for account in accounts_of.get(customer, []))
        already = sum(amount for stamp, amount in spent_final.get(customer, []) if stamp <= when)
        inflow = int(transfers.at[idx, "inflow_amount"])
        available = base - already + inflow
        amount = int(transfers.at[idx, "planned_amount"])
        if amount > available:
            amount = int(max(available, 0) // 1000 * 1000)
            transfers.at[idx, "planned_amount"] = np.int64(amount)
        spent_final.setdefault(customer, []).append((when, amount - inflow))
    transfers["planned_amount"] = transfers["planned_amount"].astype(np.int64)
    totals = transfers.loc[transfers["planned_amount"] > 0].groupby(transfers["case_id"].astype(str))["planned_amount"].sum()
    cases["planned_total"] = cases["case_id"].astype(str).map(totals).fillna(0).astype(np.int64)

    label = np.zeros(len(customers), dtype=bool)
    label[np.unique(victim_row)] = True
    print(f"  AUC susceptibility (nội bộ): {auc_score(label, sus):.3f}")
    write(phones_out, "observed", "phone_numbers")
    write(truth_numbers, "truth", "truth_numbers")
    write(cases, "truth", "truth_cases")
    write(contacts, "truth", "case_contacts")
    write(transfers, "truth", "case_transfers_plan")
    print(
        f"Đã lập {len(cases)} vụ, {cases['victim_customer_id'].nunique()} nạn nhân, "
        f"{n_numbers} số kẻ gian, {len(transfers)} khoản kế hoạch."
    )


if __name__ == "__main__":
    main()
