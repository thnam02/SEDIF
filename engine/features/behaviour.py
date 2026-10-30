"""Tầng thao tác v2.4.

Baseline theo (user, task_type) trên Redis db đang mở, tiền tố beh:v24:.
Một phiên được chấm trước khi được ghi vào baseline. Chỉ phiên có mức cuối
dưới 2 mới được ghi. Production chờ inclusion_delay_hours. Thí nghiệm ghi ngay.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
PREFIX = "beh:v24:"
CONSISTENCY = 1.4826
EPS = 1e-6
REF_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "behaviour_ref"

TASKS = ("saved_payee", "new_typed", "new_pasted", "qr")
CONTINUOUS = (
    "median_gap_ms",
    "p90_gap_ms",
    "pause_to_action_ratio",
    "review_dwell_ms",
    "background_total_ms",
)
COUNTS = (
    "burst_count",
    "edit_count",
    "backtrack_count",
    "screen_revisit_count",
    "background_count",
)
SCORED = CONTINUOUS + COUNTS
STORED = SCORED + ("session_duration_ms",)
GROUPS = (
    ("rhythm", ("median_gap_ms", "p90_gap_ms")),
    ("bursts", ("burst_count", "pause_to_action_ratio")),
    ("review", ("review_dwell_ms",)),
    ("corrections", ("edit_count", "backtrack_count")),
    ("navigation", ("screen_revisit_count",)),
    ("app_switch", ("background_count", "background_total_ms")),
)
PRIMARY = ("pause_to_action_ratio", "p90_gap_ms", "background_total_ms")
QUALITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}

# Phân phối tổng hợp để dựng reference khi chưa có số liệu thí nghiệm.
_LOC = {
    "median_gap_ms": (800.0, 70.0),
    "p90_gap_ms": (2200.0, 180.0),
    "burst_count": (2.0, 0.8),
    "pause_to_action_ratio": (0.18, 0.035),
    "review_dwell_ms": (1400.0, 220.0),
    "edit_count": (1.0, 0.7),
    "backtrack_count": (0.4, 0.45),
    "screen_revisit_count": (1.0, 0.55),
    "background_count": (0.15, 0.35),
    "background_total_ms": (350.0, 140.0),
    "session_duration_ms": (40000.0, 4000.0),
}
_TASK_SCALE = {"saved_payee": 1.0, "new_typed": 1.04, "new_pasted": 1.1, "qr": 0.96}
_SCALED = {"median_gap_ms", "p90_gap_ms", "review_dwell_ms", "background_total_ms", "session_duration_ms"}
_REFS: dict[str, dict] = {}


@dataclass
class View:
    user: str
    task_type: str
    ts_us: int
    features: dict
    experiment: bool
    n: int
    quality: str
    usable: bool
    score: float
    p_rarity: float | None
    groups: dict
    feature_scores: dict
    reasons: list[str]
    delay_us: int
    max_sessions: int


@dataclass
class Score:
    score: float
    groups: dict
    feature_scores: dict
    reasons: list[str] = field(default_factory=list)


def _policy():
    from engine.policy.v24 import load_policy

    return load_policy()


def settings(policy: dict | None = None) -> dict:
    policy = policy if policy is not None else _policy()
    block = policy.get("behavioural_baseline") or {}
    directions = dict(policy.get("behaviour_directions") or {})
    for name in SCORED:
        directions.setdefault(name, "up")
    return {
        "prior": float(block.get("shrink_prior", 5)),
        "floor": float(block.get("mad_floor_fraction_of_population", 0.5)),
        "max_sessions": int(block.get("max_sessions", 30)),
        "delay_hours": float(block.get("inclusion_delay_hours", 24)),
        "min_quality": str(block.get("min_quality") or "medium"),
        "min_sessions": int(block.get("min_sessions_for_level_2", 3)),
        "medium": int(block.get("medium_sessions", 3)),
        "high": int(block.get("high_sessions", 8)),
        "directions": directions,
    }


def quality_name(sessions: int, medium: int = 3, high: int = 8) -> str:
    count = int(sessions)
    if count <= 0:
        return "none"
    if count >= high:
        return "high"
    if count >= medium:
        return "medium"
    return "low"


def usable_quality(quality: str, minimum: str, sessions: int, min_sessions: int) -> bool:
    if int(sessions) < int(min_sessions):
        return False
    return QUALITY_RANK.get(quality, 0) >= QUALITY_RANK.get(minimum, 2)


def median(values: list[float]) -> float:
    ordered = sorted(float(item) for item in values)
    count = len(ordered)
    if count == 0:
        return 0.0
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: list[float], center: float) -> float:
    if not values:
        return 0.0
    return median([abs(float(item) - center) for item in values])


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number < 0:
        return None
    return number


def parse_features(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    parsed = {}
    for name in SCORED:
        number = _number(payload.get(name))
        if number is None:
            return None
        parsed[name] = float(number)
    duration = _number(payload.get("session_duration_ms"))
    parsed["session_duration_ms"] = 0.0 if duration is None else float(duration)
    return parsed


def population_stats(rows: list[dict]) -> dict:
    stats = {}
    for name in STORED:
        values = [float(row[name]) for row in rows if name in row]
        center = median(values)
        stats[name] = {"median": center, "mad": mad(values, center)}
    return stats


def robust_z(value: float, baseline: list[float], pop_median: float, pop_mad: float, prior: float, floor_fraction: float, direction: str) -> float:
    """z một phía. Trung vị và MAD co về phân phối cùng task_type. MAD không xuống dưới sàn."""
    count = len(baseline)
    weight = count / (count + prior) if count else 0.0
    personal_median = median(baseline) if count else pop_median
    personal_mad = mad(baseline, personal_median) if count else pop_mad
    center = weight * personal_median + (1.0 - weight) * pop_median
    shrunk = weight * personal_mad + (1.0 - weight) * pop_mad
    used = max(shrunk, floor_fraction * pop_mad)
    score = (float(value) - center) / (CONSISTENCY * used + EPS)
    if direction == "down":
        score = -score
    return score if score > 0 else 0.0


def count_score(value: float, baseline: list[float]) -> float:
    """1 trừ tần suất đuôi trên. Điểm tăng khi giá trị hiếm hơn, cùng chiều với p_rarity."""
    count = len(baseline)
    at_least = sum(1 for item in baseline if float(item) >= float(value))
    tail = (1.0 + at_least) / (count + 1.0)
    return 1.0 - tail


def _phrase(group: str, features: dict, feature_scores: dict) -> str:
    if group == "rhythm":
        return "dừng lâu hơn thường lệ"
    if group == "bursts":
        if feature_scores.get("pause_to_action_ratio", 0.0) >= feature_scores.get("burst_count", 0.0):
            return "nghỉ lâu giữa các thao tác"
        return "thao tác thành cụm hơn thường lệ"
    if group == "review":
        return "xem lại lâu hơn thường lệ"
    if group == "corrections":
        return "sửa nhiều hơn thường lệ"
    if group == "navigation":
        return "quay lại màn hình nhiều hơn thường lệ"
    count = int(round(float(features.get("background_count") or 0)))
    if count > 0 and feature_scores.get("background_count", 0.0) >= feature_scores.get("background_total_ms", 0.0):
        return f"rời app {count} lần"
    return "ở ngoài app lâu hơn thường lệ"


def score_features(features: dict, baseline: list[dict], population: dict, directions: dict, prior: float = 5.0, floor_fraction: float = 0.5) -> Score:
    feature_scores = {}
    for name in SCORED:
        column = [float(row[name]) for row in baseline if name in row]
        pop = population.get(name) or {"median": 0.0, "mad": 0.0}
        if name in COUNTS:
            feature_scores[name] = count_score(float(features[name]), column)
        else:
            feature_scores[name] = robust_z(
                float(features[name]),
                column,
                float(pop.get("median") or 0.0),
                float(pop.get("mad") or 0.0),
                prior,
                floor_fraction,
                str(directions.get(name) or "up"),
            )
    groups = {}
    for group, names in GROUPS:
        groups[group] = max(feature_scores[name] for name in names)
    session = sum(groups.values()) / float(len(GROUPS))
    ranked = sorted(groups, key=lambda name: (-groups[name], name))
    reasons = []
    for name in ranked:
        if groups[name] <= 0:
            continue
        reasons.append(_phrase(name, features, feature_scores))
        if len(reasons) == 2:
            break
    return Score(score=session, groups=groups, feature_scores=feature_scores, reasons=reasons)


def empirical_p(score: float, reference: list[float]) -> float | None:
    if not reference:
        return None
    at_least = sum(1 for item in reference if float(item) >= float(score))
    return (1.0 + at_least) / (len(reference) + 1.0)


def load_reference(task_type: str) -> dict | None:
    if task_type in _REFS:
        return _REFS[task_type]
    path = REF_DIR / f"{task_type}.json"
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    _REFS[task_type] = loaded
    return loaded


def key_for(user: str, task_type: str) -> str:
    return f"{PREFIX}{user}:{task_type}"


def _records(client, user: str, task_type: str) -> list[dict]:
    raw = client.get(key_for(user, task_type))
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    rows = loaded.get("records") if isinstance(loaded, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def baseline_rows(records: list[dict], at_us: int, limit: int) -> list[dict]:
    ready = [row for row in records if int(row.get("ts") or 0) < int(at_us) and int(row.get("eligible_at") or 0) <= int(at_us)]
    ready.sort(key=lambda row: int(row.get("ts") or 0))
    if len(ready) > limit:
        return ready[-limit:]
    return ready


def _to_us(stamp: datetime) -> int:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ICT)
    return int(stamp.timestamp() * 1_000_000)


def assess(client, user: str, task_type: str | None, features, decided: datetime, experiment: bool = False) -> View | None:
    parsed = parse_features(features)
    if parsed is None or task_type not in TASKS or not user:
        return None
    cfg = settings()
    reference = load_reference(task_type) or {}
    population = reference.get("population") or {}
    rows = baseline_rows(_records(client, user, task_type), _to_us(decided), cfg["max_sessions"])
    history = [row.get("features") or {} for row in rows]
    scored = score_features(parsed, history, population, cfg["directions"], cfg["prior"], cfg["floor"])
    quality = quality_name(len(rows), cfg["medium"], cfg["high"])
    rarity = empirical_p(scored.score, list(reference.get("scores") or []))
    delay_us = 0 if experiment else int(cfg["delay_hours"] * 3600 * 1_000_000)
    return View(
        user=user,
        task_type=task_type,
        ts_us=_to_us(decided),
        features=parsed,
        experiment=experiment,
        n=len(rows),
        quality=quality,
        usable=usable_quality(quality, cfg["min_quality"], len(rows), cfg["min_sessions"]),
        score=scored.score,
        p_rarity=rarity,
        groups=scored.groups,
        feature_scores=scored.feature_scores,
        reasons=scored.reasons,
        delay_us=delay_us,
        max_sessions=cfg["max_sessions"],
    )


def remember(client, view: View | None, level: int) -> None:
    if view is None or int(level) >= 2:
        return
    rows = _records(client, view.user, view.task_type)
    if any(int(row.get("ts") or 0) == view.ts_us for row in rows):
        return
    rows.append(
        {
            "ts": view.ts_us,
            "eligible_at": view.ts_us + view.delay_us,
            "features": view.features,
        }
    )
    rows.sort(key=lambda row: int(row.get("ts") or 0))
    if len(rows) > view.max_sessions:
        rows = rows[-view.max_sessions :]
    client.set(key_for(view.user, view.task_type), json.dumps({"records": rows}, ensure_ascii=False))


def draw_person(rng: random.Random) -> dict:
    """Tốc độ và tần suất rời app riêng từng người. sd 0,4 trên log của khoảng dừng và dwell."""
    return {
        "speed": math.exp(rng.gauss(0.0, 0.4)),
        "pause": math.exp(rng.gauss(0.0, 0.4)),
        "background_rate": math.exp(rng.gauss(math.log(1.5), 0.55)),
        "background_ms": math.exp(rng.gauss(math.log(1500.0), 0.3)),
    }


def _poisson(rng: random.Random, rate: float) -> int:
    if rate <= 0:
        return 0
    limit = math.exp(-rate)
    count = 0
    product = 1.0
    while product > limit:
        count += 1
        product *= rng.random()
    return count - 1


def _apply_shift(features: dict, shift: dict | None) -> None:
    for name, spec in (shift or {}).items():
        if name not in features:
            continue
        kind, amount = spec
        updated = features[name] * float(amount) if kind == "mul" else features[name] + float(amount)
        if name in COUNTS:
            features[name] = float(max(0, int(round(updated))))
        else:
            features[name] = max(0.0, float(updated))


_TIMED = ("median_gap_ms", "p90_gap_ms", "review_dwell_ms")


def draw_session(rng: random.Random, task_type: str, person: dict, shift: dict | None = None) -> dict:
    features = draw_features(rng, task_type)
    for name in _TIMED:
        features[name] *= person["speed"]
    features["pause_to_action_ratio"] *= person["pause"]
    switches = _poisson(rng, person["background_rate"])
    features["background_count"] = float(switches)
    features["background_total_ms"] = float(switches) * person["background_ms"]
    _apply_shift(features, shift)
    return features


def draw_features(rng: random.Random, task_type: str, shift: dict | None = None) -> dict:
    scale = _TASK_SCALE.get(task_type, 1.0)
    drawn = {}
    for name, (loc, width) in _LOC.items():
        center = loc * scale if name in _SCALED else loc
        value = rng.gauss(center, width)
        if name in COUNTS:
            drawn[name] = float(max(0, int(round(value))))
        else:
            drawn[name] = max(0.0, float(value))
    for name, spec in (shift or {}).items():
        if name not in drawn:
            continue
        kind, amount = spec
        updated = drawn[name] * float(amount) if kind == "mul" else drawn[name] + float(amount)
        if name in COUNTS:
            drawn[name] = float(max(0, int(round(updated))))
        else:
            drawn[name] = max(0.0, float(updated))
    return drawn


def write_synthetic_references(root: Path | None = None, seed: int = 42, users: int = 800, sessions: int = 16, pool_people: int = 80, pool_sessions: int = 8) -> None:
    """Reference S từ generator có khác biệt giữa người, cùng giao thức chấm rồi mới ghi."""
    target = root or REF_DIR
    target.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    cfg = settings()
    for task in TASKS:
        pool_rows = []
        for _person in range(pool_people):
            person = draw_person(rng)
            for _session in range(pool_sessions):
                pool_rows.append(draw_session(rng, task, person))
        population = population_stats(pool_rows)
        scores = []
        baseline_n = []
        for _user in range(users):
            person = draw_person(rng)
            history: list[dict] = []
            for _session in range(sessions):
                features = draw_session(rng, task, person)
                scored = score_features(features, history, population, cfg["directions"], cfg["prior"], cfg["floor"])
                scores.append(scored.score)
                baseline_n.append(len(history))
                history.append(features)
                if len(history) > cfg["max_sessions"]:
                    history = history[-cfg["max_sessions"] :]
        document = {
            "task_type": task,
            "provenance": "synthetic, demo only",
            "protocol": "chronological personal baseline; scored before inclusion; level < 2; last 30; heterogeneous persons",
            "seed": seed,
            "population": population,
            "scores": scores,
            "baseline_n": baseline_n,
        }
        (target / f"{task}.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _REFS.pop(task, None)


def main() -> None:
    write_synthetic_references()
    print(f"wrote synthetic behaviour references in {REF_DIR}")


if __name__ == "__main__":
    main()
