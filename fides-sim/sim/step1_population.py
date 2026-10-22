"""Bước 1: sinh dân số khách hàng, số điện thoại và người giám hộ.

Chạy lại với cùng `seed` cho cùng một kết quả. Vòng lặp Python chỉ nằm ở
bước ghép hộ (khuôn hộ, ràng buộc tuổi, thành phố). Các bước còn lại
dùng phép toán mảng của numpy/pandas.
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
AGE_MIN = 18
AGE_MAX = 85
MAX_CHILD_GAP = 18
MAX_SPOUSE_GAP = 15
SPOUSE_DIFF_RATE = 0.55
ELDERLY_ALONE_RATE = 0.22
INCOME_RHO = 0.35
CHILD_OTHER_CITY = 0.20
EXTERNAL_BUSINESS_SHARE = 0.15

SEGMENT_ELDERLY = "cao tuổi"
SEGMENT_BUSINESS = "doanh nghiệp nhỏ"
SEGMENT_NORMAL = "thường"
SEGMENT_TO_INCOME = {
    SEGMENT_ELDERLY: "elderly",
    SEGMENT_BUSINESS: "small_business",
    SEGMENT_NORMAL: "normal",
}

PREFIXES = {
    "Viettel": ["086", "096", "097", "098", "032", "033", "034", "035", "036", "037", "038", "039"],
    "VNPT": ["081", "082", "083", "084", "085", "088", "091", "094"],
    "MobiFone": ["070", "076", "077", "078", "079", "089", "090", "093"],
    "Vietnamobile": ["052", "056", "058", "092"],
}

# Khuôn hộ: (loại, số con, trọng số). Ghép hộ nhiều con trước, vợ chồng đơn sau.
TEMPLATES = {
    5: [("couple_kids", 3, 0.55), ("parent_kids", 4, 0.25), ("couple_parent", 2, 0.20)],
    4: [("couple_kids", 2, 0.60), ("parent_kids", 3, 0.25), ("couple_parent", 1, 0.15)],
    3: [("couple_kids", 1, 0.65), ("parent_kids", 2, 0.25), ("couple_parent", 0, 0.10)],
    2: [("parent_kids", 1, 0.35), ("couple", 0, 0.65)],
}


def load_config(path: Path | None = None) -> dict:
    path = path or (ROOT / "config.yaml")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def as_timestamp(value) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        stamp = value
    else:
        stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(TZ)
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


def age_supply(n: int, bands: list[dict]) -> np.ndarray:
    band_counts = largest_remainder(range(len(bands)), [band["share"] for band in bands], n)
    supply = np.zeros(AGE_MAX + 1, dtype=int)
    for index, band in enumerate(bands):
        years = list(range(int(band["min"]), int(band["max"]) + 1))
        for year, count in largest_remainder(years, np.ones(len(years)), band_counts[index]).items():
            supply[year] = count
    if int(supply.sum()) != n:
        raise RuntimeError(f"Tổng tuổi = {supply.sum()} khác {n}")
    return supply


def solve_household_counts(n: int, size_shares: dict) -> dict[int, int]:
    """Số hộ theo từng quy mô, đúng tổng người và sát tỷ lệ trong config."""
    sizes = np.array(sorted(int(size) for size in size_shares), dtype=int)
    shares = np.array([float(size_shares[int(size)]) for size in sizes], dtype=float)
    shares = shares / shares.sum()
    if list(sizes) != [1, 2, 3, 4, 5]:
        raise ValueError("household_size cần đủ các cỡ 1..5")
    expected = shares * (n / float(np.dot(sizes, shares)))
    radius = 50
    windows = []
    for center in expected[2:]:
        mid = int(round(center))
        windows.append(np.arange(max(0, mid - radius), mid + radius + 1))
    h3g, h4g, h5g = np.meshgrid(windows[0], windows[1], windows[2], indexing="ij")
    remaining = n - 3 * h3g - 4 * h4g - 5 * h5g
    best_dev = None
    best = None
    for delta in range(-20, 21):
        h2 = remaining // 3 + delta
        h1 = remaining - 2 * h2
        ok = (h1 >= 0) & (h2 >= 0)
        total_h = h1 + h2 + h3g + h4g + h5g
        stacked = np.stack([h1, h2, h3g, h4g, h5g], axis=0).astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            actual = stacked / total_h
        dev = np.max(np.abs(actual - shares.reshape(5, 1, 1, 1)), axis=0)
        dev = np.where(ok, dev, np.inf)
        flat = int(np.argmin(dev))
        score = float(dev.ravel()[flat])
        if best_dev is None or score < best_dev:
            coords = np.unravel_index(flat, dev.shape)
            best_dev = score
            best = (
                int(h1[coords]),
                int(h2[coords]),
                int(h3g[coords]),
                int(h4g[coords]),
                int(h5g[coords]),
            )
    if best is None or not np.isfinite(best_dev):
        raise RuntimeError("Không tìm được phân bổ quy mô hộ")
    counts = {size: count for size, count in zip(sizes.tolist(), best)}
    people = sum(size * count for size, count in counts.items())
    if people != n:
        raise RuntimeError(f"Quy mô hộ ra {people} người, cần {n}")
    return counts


class AgePool:
    def __init__(self, supply: np.ndarray, rng: np.random.Generator):
        self.rng = rng
        self.stacks: dict[int, list[int]] = {}
        self.counts = np.zeros(AGE_MAX + 1, dtype=int)
        self.age_of: dict[int, int] = {}
        pid = 0
        for age in range(AGE_MIN, AGE_MAX + 1):
            count = int(supply[age])
            ids = list(range(pid, pid + count))
            rng.shuffle(ids)
            self.stacks[age] = ids
            self.counts[age] = count
            for person_id in ids:
                self.age_of[person_id] = age
            pid += count

    def remaining(self) -> int:
        return int(self.counts.sum())

    def give(self, person_id: int, age: int) -> None:
        self.stacks[age].append(person_id)
        self.counts[age] += 1

    def _pop(self, age: int) -> tuple[int, int]:
        person_id = self.stacks[age].pop()
        self.counts[age] -= 1
        return person_id, age

    def take_most(self, lo: int, hi: int) -> tuple[int, int] | None:
        if lo > hi:
            return None
        window = self.counts[lo : hi + 1]
        if window.sum() == 0:
            return None
        age = lo + int(np.argmax(window))
        return self._pop(age)

    def take_kids(self, k: int) -> list[tuple[int, int]] | None:
        taken = []
        for _ in range(k):
            item = self.take_most(AGE_MIN, AGE_MAX - MAX_CHILD_GAP)
            if item is None:
                for person_id, age in taken:
                    self.give(person_id, age)
                return None
            taken.append(item)
        return taken

    def take_pair(self, min_age: int, max_age: int = AGE_MAX) -> tuple[tuple[int, int], tuple[int, int]] | None:
        """Hai người trong [min_age, max_age], lệch nhau không quá 15 tuổi."""
        best = None
        counts = self.counts
        hi_cap = min(AGE_MAX, max_age)
        for age in range(min_age, hi_cap + 1):
            if counts[age] <= 0:
                continue
            for other in range(age, min(hi_cap, age + MAX_SPOUSE_GAP) + 1):
                if age == other:
                    if counts[age] < 2:
                        continue
                    score = int(counts[age])
                elif counts[other] <= 0:
                    continue
                else:
                    score = int(counts[age] + counts[other])
                if best is None or score > best[0]:
                    best = (score, age, other)
        if best is None:
            return None
        _, age, other = best
        return self._pop(age), self._pop(other)

    def extract(self, person_ids: np.ndarray) -> list[tuple[int, int, str]]:
        chosen = {int(person_id) for person_id in person_ids}
        for age in range(AGE_MIN, AGE_MAX + 1):
            stack = self.stacks[age]
            kept = [person_id for person_id in stack if person_id not in chosen]
            self.counts[age] -= len(stack) - len(kept)
            self.stacks[age] = kept
        return [(int(person_id), self.age_of[int(person_id)], "head") for person_id in person_ids]


def _restore(pool: AgePool, people: list[tuple[int, int]]) -> None:
    for person_id, age in people:
        pool.give(person_id, age)


def build_couple_kids(pool: AgePool, n_kids: int):
    kids = pool.take_kids(n_kids)
    if kids is None:
        return None
    pair = pool.take_pair(max(age for _, age in kids) + MAX_CHILD_GAP)
    if pair is None:
        _restore(pool, kids)
        return None
    (head, head_age), (spouse, spouse_age) = pair
    return [(head, head_age, "head"), (spouse, spouse_age, "spouse")] + [
        (person_id, age, "child") for person_id, age in kids
    ]


def build_parent_kids(pool: AgePool, n_kids: int):
    kids = pool.take_kids(n_kids)
    if kids is None:
        return None
    parent = pool.take_most(max(age for _, age in kids) + MAX_CHILD_GAP, AGE_MAX)
    if parent is None:
        _restore(pool, kids)
        return None
    return [(parent[0], parent[1], "head")] + [(person_id, age, "child") for person_id, age in kids]


def build_couple_parent(pool: AgePool, n_kids: int):
    kids = pool.take_kids(n_kids) if n_kids else []
    if kids is None:
        return None
    min_parent = max((age for _, age in kids), default=AGE_MIN - MAX_CHILD_GAP) + MAX_CHILD_GAP
    max_head = AGE_MAX - MAX_CHILD_GAP
    if min_parent > max_head:
        _restore(pool, kids)
        return None
    pair = pool.take_pair(min_parent, max_head)
    if pair is None:
        _restore(pool, kids)
        return None
    (head, head_age), (spouse, spouse_age) = pair
    parent = pool.take_most(head_age + MAX_CHILD_GAP, AGE_MAX)
    if parent is None:
        pool.give(head, head_age)
        pool.give(spouse, spouse_age)
        _restore(pool, kids)
        return None
    return [
        (head, head_age, "head"),
        (spouse, spouse_age, "spouse"),
        (parent[0], parent[1], "parent"),
    ] + [(person_id, age, "child") for person_id, age in kids]


def build_couple(pool: AgePool, n_kids: int = 0):
    del n_kids
    pair = pool.take_pair(AGE_MIN, AGE_MAX)
    if pair is None:
        return None
    (head, head_age), (spouse, spouse_age) = pair
    return [(head, head_age, "head"), (spouse, spouse_age, "spouse")]


BUILDERS = {
    "couple_kids": build_couple_kids,
    "parent_kids": build_parent_kids,
    "couple_parent": build_couple_parent,
    "couple": build_couple,
}


def _template_queue(size: int, n_households: int) -> list[tuple[str, int]]:
    specs = TEMPLATES[size]
    counts = largest_remainder(range(len(specs)), [spec[2] for spec in specs], n_households)
    order = sorted(range(len(specs)), key=lambda index: specs[index][1], reverse=True)
    queue = []
    for index in order:
        kind, n_kids, _ = specs[index]
        queue.extend([(kind, n_kids)] * counts[index])
    return queue


def _fallbacks(size: int, kind: str, n_kids: int) -> list[tuple[str, int]]:
    options = [(spec[0], spec[1]) for spec in TEMPLATES[size] if (spec[0], spec[1]) != (kind, n_kids)]
    options.sort(key=lambda item: item[1], reverse=True)
    return options


def assemble_households(supply: np.ndarray, hh_counts: dict[int, int], rng: np.random.Generator):
    """Ghép hộ. Trả về tuổi, vai trò và chỉ số hộ của từng người."""
    pool = AgePool(supply, rng)
    n = int(supply.sum())
    elderly_ids = [person_id for age in range(60, AGE_MAX + 1) for person_id in pool.stacks[age]]
    other_ids = [person_id for age in range(AGE_MIN, 60) for person_id in pool.stacks[age]]
    n_elderly = len(elderly_ids)
    n_single = hh_counts[1]
    min_elderly_singles = int(np.floor(n_single * n_elderly / n)) + 1
    target_elderly_singles = int(round(ELDERLY_ALONE_RATE * n_elderly))
    n_elderly_singles = min(n_elderly - 1, n_single - 1, max(min_elderly_singles, target_elderly_singles))
    chosen_elderly = rng.choice(np.array(elderly_ids), size=n_elderly_singles, replace=False)
    chosen_other = rng.choice(np.array(other_ids), size=n_single - n_elderly_singles, replace=False)
    singles = pool.extract(np.concatenate([chosen_elderly, chosen_other]))

    ages: list[int] = []
    roles: list[str] = []
    hh_index: list[int] = []
    hh_members: list[list[int]] = []

    def commit(members: list[tuple[int, int, str]]) -> None:
        _validate_members(members)
        household_id = len(hh_members)
        indices = []
        for _, age, role in members:
            indices.append(len(ages))
            ages.append(int(age))
            roles.append(role)
            hh_index.append(household_id)
        hh_members.append(indices)

    for member in singles:
        commit([member])

    for size in (5, 4, 3, 2):
        for kind, n_kids in _template_queue(size, hh_counts[size]):
            members = BUILDERS[kind](pool, n_kids)
            if members is None:
                for alt_kind, alt_kids in _fallbacks(size, kind, n_kids):
                    members = BUILDERS[alt_kind](pool, alt_kids)
                    if members is not None:
                        break
            if members is None:
                raise RuntimeError(f"Không ghép được hộ cỡ {size} ({kind}, {n_kids} con); còn {pool.remaining()} người")
            if len(members) != size:
                raise RuntimeError("Khuôn hộ trả về sai số người")
            commit(members)

    if pool.remaining() != 0 or len(ages) != n:
        raise RuntimeError(f"Ghép hộ xong còn {pool.remaining()} người, đã xếp {len(ages)}/{n}")
    return (
        np.array(ages, dtype=int),
        np.array(roles, dtype=object),
        np.array(hh_index, dtype=int),
        hh_members,
    )


def _validate_members(members: list[tuple[int, int, str]]) -> None:
    by_role: dict[str, list[int]] = {}
    for _, age, role in members:
        by_role.setdefault(role, []).append(int(age))
    heads = by_role.get("head", [])
    spouses = by_role.get("spouse", [])
    children = by_role.get("child", [])
    parents = by_role.get("parent", [])
    if len(heads) != 1:
        raise RuntimeError("Mỗi hộ cần đúng một chủ hộ")
    head_age = heads[0]
    if len(spouses) > 1:
        raise RuntimeError("Mỗi hộ có tối đa một vợ/chồng")
    if len(spouses) == 1 and abs(head_age - spouses[0]) > MAX_SPOUSE_GAP:
        raise RuntimeError("Vợ chồng lệch quá 15 tuổi")
    for child_age in children:
        if child_age > head_age - MAX_CHILD_GAP:
            raise RuntimeError("Con không nhỏ hơn chủ hộ ít nhất 18 tuổi")
        if len(spouses) == 1 and child_age > spouses[0] - MAX_CHILD_GAP:
            raise RuntimeError("Con không nhỏ hơn vợ/chồng chủ hộ ít nhất 18 tuổi")
    for parent_age in parents:
        if head_age > parent_age - MAX_CHILD_GAP:
            raise RuntimeError("Chủ hộ không nhỏ hơn cha mẹ ít nhất 18 tuổi")


def assign_cities(hh_members, roles, city_targets: dict, rng: np.random.Generator) -> np.ndarray:
    """Cả hộ cùng thành phố; khoảng 20% con ở thành phố khác. Biên đếm đúng config."""
    cities = list(city_targets)
    target = np.array([int(city_targets[city]) for city in cities], dtype=int)
    n_cities = len(cities)
    n = int(sum(len(members) for members in hh_members))
    n_hh = len(hh_members)
    child_ids = np.array(
        [person for members in hh_members for person in members if roles[person] == "child"],
        dtype=int,
    )
    n_movers = int(round(CHILD_OTHER_CITY * len(child_ids)))
    mover_ids = set(int(person) for person in rng.choice(child_ids, size=n_movers, replace=False))

    stayers_of: list[list[int]] = []
    movers_of: list[list[int]] = []
    stayer_w = np.zeros(n_hh, dtype=int)
    for household, members in enumerate(hh_members):
        stayers = [person for person in members if person not in mover_ids]
        movers = [person for person in members if person in mover_ids]
        if not stayers:
            raise RuntimeError("Hộ không còn người ở lại thành phố của hộ")
        stayers_of.append(stayers)
        movers_of.append(movers)
        stayer_w[household] = len(stayers)

    seats = target.copy()
    hh_city = np.full(n_hh, -1, dtype=int)
    order = np.lexsort((rng.random(n_hh), -stayer_w))
    deferred = []
    for household in order:
        weight = int(stayer_w[household])
        options = np.flatnonzero(seats >= weight)
        if len(options) == 0:
            deferred.append(int(household))
            continue
        city = int(options[np.argmax(seats[options])])
        seats[city] -= weight
        hh_city[household] = city

    def free_space(city: int, need: int) -> bool:
        guard = 0
        while seats[city] < need and guard < n_hh + 5:
            guard += 1
            members = np.flatnonzero(hh_city == city)
            members = members[np.argsort(stayer_w[members], kind="stable")]
            moved = False
            for other in members:
                weight = int(stayer_w[other])
                alt = np.flatnonzero((np.arange(n_cities) != city) & (seats >= weight))
                if len(alt) == 0:
                    continue
                destination = int(alt[np.argmax(seats[alt])])
                seats[city] += weight
                seats[destination] -= weight
                hh_city[other] = destination
                moved = True
                if seats[city] >= need:
                    return True
            if not moved:
                return False
        return bool(seats[city] >= need)

    for household in deferred:
        weight = int(stayer_w[household])
        placed = False
        for city in np.argsort(-seats):
            if free_space(int(city), weight):
                seats[city] -= weight
                hh_city[household] = int(city)
                placed = True
                break
        if not placed:
            raise RuntimeError("Không xếp được thành phố cho mọi hộ")

    if int(seats.sum()) != n_movers or np.any(seats < 0) or np.any(hh_city < 0):
        raise RuntimeError(f"Suất thành phố lệch: còn {seats.tolist()}, cần {n_movers} suất cho con")

    person_city = np.full(n, -1, dtype=int)
    for household in range(n_hh):
        person_city[stayers_of[household]] = hh_city[household]

    assigned: list[tuple[int, int, int]] = []
    mover_queue = [(person, int(hh_city[household])) for household, people in enumerate(movers_of) for person in people]
    mover_queue.sort(key=lambda item: -int(seats[item[1]]))
    for person, home in mover_queue:
        options = np.flatnonzero((seats > 0) & (np.arange(n_cities) != home))
        if len(options) == 0:
            swapped = False
            if seats[home] > 0:
                for index, (other, other_city, other_home) in enumerate(assigned):
                    if other_home != home and other_city != home:
                        seats[home] -= 1
                        seats[other_city] += 1
                        assigned[index] = (other, home, other_home)
                        seats[other_city] -= 1
                        person_city[other] = home
                        person_city[person] = other_city
                        assigned.append((person, other_city, home))
                        swapped = True
                        break
            if not swapped:
                raise RuntimeError("Không gán được thành phố khác cho con chuyển đi")
            continue
        city = int(options[np.argmax(seats[options])])
        seats[city] -= 1
        person_city[person] = city
        assigned.append((person, city, home))

    if np.any(person_city < 0) or int(np.bincount(person_city, minlength=n_cities).sum()) != n:
        raise RuntimeError("Thiếu thành phố của một số người")
    counts = np.bincount(person_city, minlength=n_cities)
    if not np.array_equal(counts, target):
        raise RuntimeError(f"Phân bố thành phố {counts.tolist()} khác {target.tolist()}")

    for household in range(n_hh):
        home = int(hh_city[household])
        for person in stayers_of[household]:
            if int(person_city[person]) != home:
                raise RuntimeError("Người ở lại không cùng thành phố với hộ")
        for person in movers_of[household]:
            if int(person_city[person]) == home:
                raise RuntimeError("Con chuyển đi vẫn ở thành phố của hộ")
    return np.array(cities, dtype=object)[person_city]


def _draw_other_surnames(rng, forbidden: np.ndarray, weights: np.ndarray) -> np.ndarray:
    drawn = rng.choice(len(weights), size=len(forbidden), p=weights)
    for _ in range(12):
        same = drawn == forbidden
        if not np.any(same):
            return drawn
        drawn[same] = rng.choice(len(weights), size=int(same.sum()), p=weights)
    if np.any(drawn == forbidden):
        raise RuntimeError("Không rút được họ khác cho vợ/chồng")
    return drawn


def offspring_matrix(hh_members, roles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mỗi người: danh sách con trong hộ (tối đa 4). Cha/mẹ của chủ hộ nhận chủ hộ là con."""
    n = len(roles)
    child_mat = -np.ones((n, 4), dtype=int)
    n_off = np.zeros(n, dtype=int)
    for members in hh_members:
        children = [person for person in members if roles[person] == "child"]
        heads = [person for person in members if roles[person] == "head"]
        spouses = [person for person in members if roles[person] == "spouse"]
        parents = [person for person in members if roles[person] == "parent"]
        if len(children) > 4:
            raise RuntimeError("Hộ có quá 4 con")
        for person in heads + spouses:
            child_mat[person, : len(children)] = children
            n_off[person] = len(children)
        for person in parents:
            child_mat[person, 0] = heads[0]
            n_off[person] = 1
    return child_mat, n_off


def skewed_timestamps(origin: pd.Timestamp, end: pd.Timestamp, n: int, rng, power: float) -> pd.Series:
    """Mốc thời gian trong [origin, end), dồn về phía `end`."""
    span = int((end - origin).total_seconds())
    frac = rng.random(n) ** power
    offsets = np.minimum(np.floor(frac * span).astype(np.int64), span - 1)
    return origin + pd.to_timedelta(offsets, unit="s")


def assign_msisdn(prefix_source: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Đầu số theo nhà mạng gốc + 7 chữ số, không trùng. Lặp theo đầu số, không theo thuê bao."""
    msisdn = np.empty(len(prefix_source), dtype=object)
    for telco, prefixes in PREFIXES.items():
        rows = np.flatnonzero(prefix_source == telco)
        if len(rows) == 0:
            continue
        which = rng.integers(0, len(prefixes), size=len(rows))
        for index, prefix in enumerate(prefixes):
            chosen = rows[which == index]
            if len(chosen) == 0:
                continue
            suffixes = rng.choice(10_000_000, size=len(chosen), replace=False)
            msisdn[chosen] = np.char.add(prefix, np.char.zfill(suffixes.astype(str), 7))
    if pd.Series(msisdn).duplicated().any() or pd.Series(msisdn).isna().any():
        raise RuntimeError("msisdn trùng hoặc thiếu")
    return msisdn


def build_population(cfg: dict) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(int(cfg["seed"]))
    pop = cfg["population"]
    n = int(pop["n_customers"])
    n_external = int(pop["n_external_numbers"])
    sim_start = as_timestamp(cfg["sim_start"])
    sim_end = as_timestamp(cfg["sim_end"])
    ref_year = int(sim_start.year)

    supply = age_supply(n, pop["age_bands"])
    hh_counts = solve_household_counts(n, {int(k): float(v) for k, v in pop["household_size"].items()})
    ages, roles, hh_index, hh_members = assemble_households(supply, hh_counts, rng)
    n_hh = len(hh_members)

    cities = assign_cities(hh_members, roles, _city_counts(pop, n), rng)

    male_head = rng.random(n_hh) < 0.5
    gender = np.where(rng.random(n) < 0.5, "nam", "nữ").astype(object)
    is_head = roles == "head"
    is_spouse = roles == "spouse"
    gender[is_head] = np.where(male_head[hh_index[is_head]], "nam", "nữ")
    gender[is_spouse] = np.where(male_head[hh_index[is_spouse]], "nữ", "nam")

    surname_names, surname_p = surname_table()
    hh_surname = rng.choice(len(surname_names), size=n_hh, p=surname_p)
    surname_idx = hh_surname[hh_index].copy()
    spouse_pos = np.flatnonzero(is_spouse)
    n_diff = int(round(SPOUSE_DIFF_RATE * len(spouse_pos)))
    diff_pos = rng.choice(spouse_pos, size=n_diff, replace=False)
    surname_idx[diff_pos] = _draw_other_surnames(rng, hh_surname[hh_index[diff_pos]], surname_p)
    surnames = surname_names[surname_idx]

    young = (ref_year - ages) > 1995
    body = sample_name_body(rng, gender == "nam", young)
    full_name = np.char.add(np.char.add(surnames.astype(str), " "), body.astype(str))
    name_ascii = np.array([to_name_ascii(name) for name in full_name], dtype=object)

    segment = np.full(n, SEGMENT_NORMAL, dtype=object)
    elderly = ages >= int(pop["elderly_age"])
    segment[elderly] = SEGMENT_ELDERLY
    non_elderly = np.flatnonzero(~elderly)
    n_business = int(round(float(pop["small_business_share"]) * len(non_elderly)))
    business_idx = rng.choice(non_elderly, size=n_business, replace=False)
    segment[business_idx] = SEGMENT_BUSINESS

    customer_id = np.char.add("C", np.char.zfill(np.arange(1, n + 1).astype(str), 6))
    family_group_id = np.char.add("H", np.char.zfill((hh_index + 1).astype(str), 6))
    hh_size = np.bincount(hh_index)
    lives_alone = hh_size[hh_index] == 1

    income_spec = pop["income_lognormal"]
    medians = np.empty(n, dtype=float)
    sigmas = np.empty(n, dtype=float)
    for label, key in SEGMENT_TO_INCOME.items():
        mask = segment == label
        medians[mask] = float(income_spec[key]["median"])
        sigmas[mask] = float(income_spec[key]["sigma"])
    shared = rng.standard_normal(n_hh)
    individual = rng.standard_normal(n)
    shock = np.sqrt(INCOME_RHO) * shared[hh_index] + np.sqrt(1.0 - INCOME_RHO) * individual
    income = np.rint(np.exp(np.log(medians) + sigmas * shock)).astype(np.int64)

    log_income = np.log(income.astype(float))
    z_income = (log_income - log_income.mean()) / log_income.std()
    age_effect = -((ages.astype(float) - 38.0) / 20.0) ** 2
    z_age = (age_effect - age_effect.mean()) / age_effect.std()
    activity = 1.0 / (1.0 + np.exp(-(0.75 * z_income + 0.55 * z_age + 0.45 * rng.standard_normal(n))))

    elderly_f = elderly.astype(float)
    alone_f = lives_alone.astype(float)
    susceptibility = 1.0 / (1.0 + np.exp(-(-0.25 + 0.62 * elderly_f + 0.60 * alone_f + 1.20 * rng.standard_normal(n))))

    created_at = pd.Series(skewed_timestamps(as_timestamp("2015-01-01"), sim_start, n, rng, power=0.30))
    if created_at.dt.tz is None:
        created_at = created_at.dt.tz_localize(TZ)
    else:
        created_at = created_at.dt.tz_convert(TZ)

    age_group = np.select(
        [ages <= 30, ages <= 50, ages <= 65],
        ["18-30", "31-50", "51-65"],
        default="66-85",
    ).astype(object)
    birth_year = ref_year - ages

    n_phones = n + n_external
    number_id = np.char.add("P", np.char.zfill(np.arange(1, n_phones + 1).astype(str), 6))
    telco_counts = largest_remainder(list(pop["telco_share"]), list(pop["telco_share"].values()), n_phones)
    telco = np.concatenate([np.full(count, name, dtype=object) for name, count in telco_counts.items()])
    rng.shuffle(telco)

    n_ported = int(round(float(pop["ported_share"]) * n_phones))
    ported = np.zeros(n_phones, dtype=bool)
    ported[rng.choice(n_phones, size=n_ported, replace=False)] = True
    prefix_source = telco.copy()
    telco_names = list(PREFIXES)
    for origin in telco_names:
        rows = np.flatnonzero(ported & (telco == origin))
        if len(rows) == 0:
            continue
        others = [name for name in telco_names if name != origin]
        weights = np.array([float(pop["telco_share"][name]) for name in others], dtype=float)
        weights = weights / weights.sum()
        prefix_source[rows] = rng.choice(np.array(others, dtype=object), size=len(rows), p=weights)
    msisdn = assign_msisdn(prefix_source, rng)

    owner_type = np.empty(n_phones, dtype=object)
    owner_type[:n] = "customer"
    owner_type[n:] = "external_person"
    n_business_lines = int(round(EXTERNAL_BUSINESS_SHARE * n_external))
    business_lines = rng.choice(np.arange(n, n_phones), size=n_business_lines, replace=False)
    owner_type[business_lines] = "business"
    owner_customer_id = np.full(n_phones, None, dtype=object)
    owner_customer_id[:n] = customer_id

    n_recent = int(round(float(pop["recent_sim_share"]) * n_phones))
    recent_idx = rng.choice(n_phones, size=n_recent, replace=False)
    is_recent = np.zeros(n_phones, dtype=bool)
    is_recent[recent_idx] = True
    registered_at = pd.Series(pd.NaT, index=np.arange(n_phones), dtype=f"datetime64[ns, {TZ}]")
    sim_span = int((sim_end - sim_start).total_seconds())
    registered_at.iloc[recent_idx] = sim_start + pd.to_timedelta(rng.integers(0, sim_span, size=n_recent), unit="s")
    old_start = as_timestamp("2011-01-01")
    old_end = as_timestamp("2022-01-01")
    old_span = int((old_end - old_start).total_seconds())
    old_idx = np.flatnonzero(~is_recent)
    registered_at.iloc[old_idx] = old_start + pd.to_timedelta(rng.integers(0, old_span, size=len(old_idx)), unit="s")

    child_mat, n_off = offspring_matrix(hh_members, roles)
    n_guard_el = int(round(float(pop["guardian_share"]["elderly"]) * int(elderly.sum())))
    n_guard_ot = int(round(float(pop["guardian_share"]["other"]) * int((~elderly).sum())))
    guard_idx = np.concatenate([
        rng.choice(np.flatnonzero(elderly), size=n_guard_el, replace=False),
        rng.choice(np.flatnonzero(~elderly), size=n_guard_ot, replace=False),
    ])
    guard_idx.sort()
    has_child = n_off[guard_idx] > 0
    pick = np.minimum((rng.random(len(guard_idx)) * np.maximum(n_off[guard_idx], 1)).astype(int), np.maximum(n_off[guard_idx] - 1, 0))
    child_choice = child_mat[guard_idx, pick]

    external_person_phones = np.flatnonzero(owner_type == "external_person")
    n_external_guards = int((~has_child).sum())
    external_choice = rng.choice(external_person_phones, size=n_external_guards, replace=False)

    relations = np.empty(len(guard_idx), dtype=object)
    guardian_phone = np.empty(len(guard_idx), dtype=object)
    guardian_customer = np.full(len(guard_idx), None, dtype=object)
    relations[has_child] = "con"
    guardian_phone[has_child] = number_id[child_choice[has_child]]
    guardian_customer[has_child] = customer_id[child_choice[has_child]]
    external_relations = np.array(["vợ_chồng", "anh_chị_em", "khác"], dtype=object)
    relations[~has_child] = rng.choice(external_relations, size=n_external_guards, p=[0.25, 0.25, 0.50])
    guardian_phone[~has_child] = number_id[external_choice]

    guard_created = created_at.iloc[guard_idx].reset_index(drop=True)
    span_g = np.maximum((sim_start - guard_created).dt.total_seconds().to_numpy().astype(np.int64), 1)
    guard_offsets = np.minimum(np.floor(rng.random(len(guard_idx)) * span_g).astype(np.int64), span_g - 1)
    guard_registered = guard_created + pd.to_timedelta(guard_offsets, unit="s")

    has_guardian = np.zeros(n, dtype=bool)
    has_guardian[guard_idx] = True

    customers = pd.DataFrame(
        {
            "customer_id": pd.Series(customer_id, dtype="string"),
            "full_name": pd.Series(full_name, dtype="string"),
            "name_ascii": pd.Series(name_ascii, dtype="string"),
            "gender": pd.Series(gender, dtype="string"),
            "birth_year": birth_year.astype(np.int64),
            "age_group": pd.Series(age_group, dtype="string"),
            "segment": pd.Series(segment, dtype="string"),
            "city": pd.Series(cities, dtype="string"),
            "phone_number_id": pd.Series(number_id[:n], dtype="string"),
            "has_guardian": has_guardian,
            "created_at": created_at,
        }
    )

    latent = pd.DataFrame(
        {
            "customer_id": customers["customer_id"],
            "family_group_id": pd.Series(family_group_id, dtype="string"),
            "lives_alone": lives_alone,
            "income_monthly": income,
            "activity_level": activity.astype(float),
            "susceptibility": susceptibility.astype(float),
            "role": pd.Series(roles, dtype="string"),
        }
    )

    phones = pd.DataFrame(
        {
            "number_id": pd.Series(number_id, dtype="string"),
            "msisdn": pd.Series(msisdn, dtype="string"),
            "telco": pd.Series(telco, dtype="string"),
            "owner_type": pd.Series(owner_type, dtype="string"),
            "owner_customer_id": pd.Series(owner_customer_id, dtype="string"),
            "registered_at": registered_at,
            "reported_count": np.zeros(n_phones, dtype=np.int64),
        }
    )

    guardians = pd.DataFrame(
        {
            "customer_id": pd.Series(customer_id[guard_idx], dtype="string"),
            "guardian_phone_number_id": pd.Series(guardian_phone, dtype="string"),
            "guardian_customer_id": pd.Series(guardian_customer, dtype="string"),
            "relation": pd.Series(relations, dtype="string"),
            "registered_at": guard_registered,
        }
    )

    _sanity(customers, latent, phones, guardians, pop, sim_start, sim_end, ages, hh_members)
    return {
        "customers": customers,
        "customers_latent": latent,
        "phone_numbers": phones,
        "guardians": guardians,
    }


def _city_counts(pop: dict, n: int) -> dict:
    return largest_remainder(list(pop["city_share"]), list(pop["city_share"].values()), n)


def _sanity(customers, latent, phones, guardians, pop, sim_start, sim_end, ages, hh_members) -> None:
    observed_cols = set(customers.columns) | set(phones.columns) | set(guardians.columns)
    truth_cols = set(latent.columns)
    overlap = observed_cols & truth_cols
    if overlap != {"customer_id"}:
        raise RuntimeError(f"Cột observed và truth giao nhau ngoài customer_id: {overlap}")
    if customers["created_at"].max() >= sim_start or phones["registered_at"].max() >= sim_end or guardians["registered_at"].max() >= sim_end:
        raise RuntimeError("Mốc thời gian vượt cửa sổ cho phép")
    if latent["income_monthly"].dtype != np.int64:
        raise RuntimeError("Thu nhập không phải int64")
    if not (0.0 <= latent["susceptibility"]).all() or not (latent["susceptibility"] <= 1.0).all():
        raise RuntimeError("susceptibility ngoài [0, 1]")
    elderly = ages >= int(pop["elderly_age"])
    alone_el = float(latent.loc[elderly, "lives_alone"].mean())
    alone_ot = float(latent.loc[~elderly, "lives_alone"].mean())
    if not alone_el > alone_ot:
        raise RuntimeError("Cao tuổi không sống một mình nhiều hơn nhóm còn lại")
    if float(latent.loc[elderly, "susceptibility"].mean()) <= float(latent.loc[~elderly, "susceptibility"].mean()):
        raise RuntimeError("susceptibility trung bình của cao tuổi không cao hơn")
    if float(latent.loc[latent["lives_alone"], "susceptibility"].mean()) <= float(latent.loc[~latent["lives_alone"], "susceptibility"].mean()):
        raise RuntimeError("susceptibility trung bình của người sống một mình không cao hơn")
    log_income = np.log(latent["income_monthly"].to_numpy(dtype=float))
    if float(np.corrcoef(log_income, latent["activity_level"])[0, 1]) <= 0.3:
        raise RuntimeError("activity_level không tương quan đủ với thu nhập")
    if float(np.corrcoef(ages.astype(float), latent["activity_level"])[0, 1]) >= 0:
        raise RuntimeError("activity_level không giảm theo tuổi")
    # Trùng họ trong hộ.
    surname = customers["full_name"].str.split().str[0].to_numpy()
    same = 0
    total = 0
    for members in hh_members:
        names = surname[members]
        m = len(names)
        if m < 2:
            continue
        total += m * (m - 1) // 2
        same += int(np.triu(names[:, None] == names[None, :], 1).sum())
    if total == 0 or same / total <= 0.70:
        raise RuntimeError(f"Tỷ lệ cặp cùng hộ trùng họ = {same / max(total, 1):.3f}")


def main() -> None:
    cfg = load_config()
    frames = build_population(cfg)
    paths = {
        "customers": write(frames["customers"], "observed", "customers"),
        "phone_numbers": write(frames["phone_numbers"], "observed", "phone_numbers"),
        "guardians": write(frames["guardians"], "observed", "guardians"),
        "customers_latent": write(frames["customers_latent"], "truth", "customers_latent"),
    }
    n = len(frames["customers"])
    print(f"Đã sinh {n} khách hàng, {len(frames['phone_numbers'])} số, {len(frames['guardians'])} giám hộ.")
    for label, path in paths.items():
        print(f"  {label}: {path}")


if __name__ == "__main__":
    main()
