"""Đánh giá tầng thao tác ngoài luồng /decide.

Đọc bank.experiment_sessions khi có dữ liệu thật. Không có thì chạy bộ tổng hợp
và ghi rõ synthetic. Một phiên không thấy chính nó hay phiên sau của cùng người.
Người hiệu chỉnh dựng reference. Người đánh giá bị giữ riêng.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO)]

from engine.features.behaviour import (
    PRIMARY,
    draw_person,
    draw_session,
    empirical_p,
    load_reference,
    population_stats,
    score_features,
    settings,
)

GUIDED = {
    "pause_to_action_ratio": ("mul", 1.8),
    "p90_gap_ms": ("mul", 1.6),
    "background_total_ms": ("mul", 4.0),
    "background_count": ("add", 2),
}
EVASION = {
    "pause_to_action_ratio": ("mul", 1.15),
    "p90_gap_ms": ("mul", 1.1),
    "background_total_ms": ("mul", 1.3),
}
LEGIT = {
    "pause_to_action_ratio": ("mul", 1.05),
    "p90_gap_ms": ("mul", 1.05),
}
GAP_SHIFTS = (0.10, 0.20, 0.30, 0.50)
BASELINE_SIZES = (3, 6, 10, 15)
ALIASES = {
    "baseline": "baseline",
    "normal": "normal_call",
    "normal_call": "normal_call",
    "guided": "guided",
    "guidance": "guided",
    "evasion": "evasion",
    "evade": "evasion",
    "legit": "legit_guidance",
    "legit_guidance": "legit_guidance",
    "legitimate_guidance": "legit_guidance",
}
Z_ALPHA = 1.959963984540054
Z_BETA = 0.8416212335729143


def auc(positive: list[float], negative: list[float]) -> float:
    if not positive or not negative:
        return float("nan")
    wins = 0.0
    for left in positive:
        for right in negative:
            if left > right:
                wins += 1.0
            elif left == right:
                wins += 0.5
    return wins / (len(positive) * len(negative))


def _primary(scored, names: tuple[str, ...]) -> float:
    return sum(scored.feature_scores[name] for name in names) / float(len(names))


def _keep(condition: str, rarity: float | None, sessions: int) -> bool:
    if condition in {"baseline", "normal_call"}:
        return True
    rare = rarity is not None and rarity <= 0.05 and sessions >= 3
    return not rare


def walk(sessions: list[dict], population: dict, directions: dict, cfg: dict, reference: list[float] | None) -> list[dict]:
    scored_rows = []
    ordered = sorted(sessions, key=lambda row: (row["participant_id"], int(row["session_index"])))
    grouped: dict[str, list] = defaultdict(list)
    for row in ordered:
        grouped[row["participant_id"]].append(row)
    for rows in grouped.values():
        history: dict[str, list] = defaultdict(list)
        for row in rows:
            task = row["task_type"]
            base = history[task]
            pop = population.get(task) or {}
            personal = score_features(row["features"], base, pop, directions, cfg["prior"], cfg["floor"])
            pooled = score_features(row["features"], [], pop, directions, cfg["prior"], cfg["floor"])
            rarity = None if reference is None else empirical_p(personal.score, reference)
            scored_rows.append(
                {
                    **row,
                    "primary": _primary(personal, PRIMARY),
                    "primary_population": _primary(pooled, PRIMARY),
                    "S": personal.score,
                    "p": rarity,
                    "n": len(base),
                }
            )
            if reference is None:
                if row["condition"] in {"baseline", "normal_call"}:
                    base.append(row["features"])
            elif _keep(row["condition"], rarity, len(base)):
                base.append(row["features"])
            if len(base) > 30:
                del base[:-30]
    return scored_rows


def population_of(sessions: list[dict]) -> dict:
    by_task: dict[str, list] = defaultdict(list)
    for row in sessions:
        if row["condition"] in {"baseline", "normal_call"}:
            by_task[row["task_type"]].append(row["features"])
    return {task: population_stats(rows) for task, rows in by_task.items() if rows}


def _classes(rows: list[dict], condition: str, field: str) -> tuple[list[float], list[float]]:
    positive = [row[field] for row in rows if row["condition"] == condition]
    negative = [row[field] for row in rows if row["condition"] == "normal_call"]
    return positive, negative


def bootstrap_auc(rows: list[dict], condition: str, field: str, reps: int = 1000, seed: int = 42) -> tuple[float, float, float]:
    by_person: dict[str, list] = defaultdict(list)
    for row in rows:
        if row["condition"] in {condition, "normal_call"}:
            by_person[row["participant_id"]].append(row)
    ids = sorted(by_person)
    point_pos, point_neg = _classes(rows, condition, field)
    point = auc(point_pos, point_neg)
    if len(ids) < 2:
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(reps):
        chosen = rng.choice(ids, size=len(ids), replace=True)
        positive = []
        negative = []
        for person in chosen:
            for row in by_person[person]:
                if row["condition"] == condition:
                    positive.append(row[field])
                else:
                    negative.append(row[field])
        samples.append(auc(positive, negative))
    samples = [item for item in samples if item == item]
    if not samples:
        return point, float("nan"), float("nan")
    low, high = np.percentile(samples, [2.5, 97.5])
    return point, float(low), float(high)


def legit_share(rows: list[dict]) -> tuple[int, int]:
    chosen = [row for row in rows if row["condition"] == "legit_guidance"]
    hit = [row for row in chosen if row["p"] is not None and row["p"] <= 0.05 and row["n"] >= 3]
    return len(hit), len(chosen)


def describe_shift(shift: dict) -> str:
    parts = []
    for name, spec in shift.items():
        kind, amount = spec
        if kind == "mul":
            parts.append(f"{name} ×{amount:g}")
        else:
            sign = "+" if amount >= 0 else ""
            parts.append(f"{name} {sign}{amount:g}")
    return ", ".join(parts)


def synthetic_people(count: int = 40, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    sessions = []
    for index in range(count):
        person_id = f"P{index:03d}"
        traits = draw_person(rng)
        cursor = 0

        def add(condition: str, shift: dict | None) -> None:
            nonlocal cursor
            sessions.append(
                {
                    "participant_id": person_id,
                    "condition": condition,
                    "session_index": cursor,
                    "task_type": "new_typed",
                    "features": draw_session(rng, "new_typed", traits, shift),
                    "call": condition != "baseline",
                    "new_payee": condition != "baseline",
                }
            )
            cursor += 1

        for _ in range(6):
            add("baseline", None)
        for _ in range(2):
            add("normal_call", None)
        for _ in range(3):
            add("guided", GUIDED)
        for _ in range(3):
            add("evasion", EVASION)
        for _ in range(3):
            add("legit_guidance", LEGIT)
    return sessions


def load_real() -> tuple[list[dict], str]:
    try:
        import psycopg
    except ImportError:
        return [], "psycopg missing"
    url = os.environ.get("DATABASE_URL", "postgresql://hainamtran@localhost:5432/fides_w1")
    try:
        with psycopg.connect(url) as conn:
            fetched = conn.execute(
                """
                SELECT participant_id, condition, session_index, task_type, behaviour_features
                FROM bank.experiment_sessions
                ORDER BY participant_id, session_index, created_at
                """
            ).fetchall()
    except Exception as exc:
        return [], str(exc)
    rows = []
    for person, condition, index, task, features in fetched:
        name = ALIASES.get(str(condition))
        if name is None or task not in {"saved_payee", "new_typed", "new_pasted", "qr"}:
            continue
        if isinstance(features, str):
            features = json.loads(features)
        if not isinstance(features, dict):
            continue
        rows.append(
            {
                "participant_id": str(person),
                "condition": name,
                "session_index": int(index),
                "task_type": task,
                "features": features,
            }
        )
    return rows, ""


def _split(sessions: list[dict]) -> tuple[list[dict], list[dict]]:
    ids = sorted({row["participant_id"] for row in sessions})
    cut = max(1, len(ids) // 2)
    if cut >= len(ids):
        cut = len(ids) - 1
    calibration_ids = set(ids[:cut])
    calibration = [row for row in sessions if row["participant_id"] in calibration_ids]
    evaluation = [row for row in sessions if row["participant_id"] not in calibration_ids]
    return calibration, evaluation


def analyse(sessions: list[dict], label: str) -> None:
    cfg = settings()
    calibration, evaluation = _split(sessions)
    population = population_of(calibration)
    fitted = walk(calibration, population, cfg["directions"], cfg, None)
    reference = [row["S"] for row in fitted if row["condition"] in {"baseline", "normal_call"}]
    held = walk(evaluation, population, cfg["directions"], cfg, reference)
    personal = bootstrap_auc(held, "guided", "primary")
    population_only = bootstrap_auc(held, "guided", "primary_population")
    evasion = bootstrap_auc(held, "evasion", "primary")
    reached, legit_n = legit_share(held)
    print(f"source: {label}")
    print(f"participants calibration {len({row['participant_id'] for row in calibration})} evaluation {len({row['participant_id'] for row in evaluation})}")
    print(f"reference sessions {len(reference)}")
    print(
        f"primary AUC guided vs normal-call (pause_to_action_ratio, p90_gap_ms, background_total_ms) "
        f"{personal[0]:.3f} CI95 {personal[1]:.3f} {personal[2]:.3f}"
    )
    if label == "synthetic":
        print(f"synthetic effect size chosen by us: {describe_shift(GUIDED)}")
    print(f"population-only baseline AUC {population_only[0]:.3f} CI95 {population_only[1]:.3f} {population_only[2]:.3f}")
    print(f"personal baseline AUC {personal[0]:.3f}")
    drop = personal[0] - evasion[0]
    print(f"evasion AUC {evasion[0]:.3f} CI95 {evasion[1]:.3f} {evasion[2]:.3f} drop {drop:.3f}")
    share = 0.0 if legit_n == 0 else reached / legit_n
    print(
        f"legitimate-guidance level-2 share {share:.3f} ({reached}/{legit_n}) "
        "assuming a call to a new payee and no other rule"
    )


def _rarity(features: dict, history: list[dict], population: dict, reference: list[float], cfg: dict) -> float | None:
    scored = score_features(features, history, population, cfg["directions"], cfg["prior"], cfg["floor"])
    return empirical_p(scored.score, reference)


def _artifact(task: str = "new_typed") -> tuple[dict, list[float], list[int], str]:
    loaded = load_reference(task)
    if not loaded:
        raise SystemExit(f"missing behaviour reference for {task}")
    scores = [float(item) for item in loaded["scores"]]
    sizes = [int(item) for item in loaded.get("baseline_n") or []]
    return loaded["population"], scores, sizes, str(loaded.get("provenance") or "")


def _slice_reference(scores: list[float], sizes: list[int], n_prior: int | None) -> list[float]:
    if n_prior is None or len(sizes) != len(scores):
        return scores
    chosen = [score for score, size in zip(scores, sizes) if size == n_prior]
    return chosen or scores


def _probes(rng: random.Random, people: int, n_prior: int) -> list[tuple[list[dict], dict]]:
    probes = []
    for _ in range(people):
        traits = draw_person(rng)
        history = [draw_session(rng, "new_typed", traits) for _ in range(n_prior)]
        plain = draw_session(rng, "new_typed", traits)
        probes.append((history, plain))
    return probes


def _hit_rate(probes, population, reference, cfg, gap: float, extra: int) -> tuple[int, int]:
    detected = 0
    for history, plain in probes:
        moved = dict(plain)
        if gap:
            moved["median_gap_ms"] = plain["median_gap_ms"] * (1.0 + gap)
            moved["p90_gap_ms"] = plain["p90_gap_ms"] * (1.0 + gap)
        if extra:
            moved["background_count"] = float(plain["background_count"] + extra)
        rarity = _rarity(moved, history, population, reference, cfg)
        if rarity is not None and rarity <= 0.05:
            detected += 1
    return detected, len(probes)


def recovery(people: int = 400) -> None:
    """Đường phát hiện. p_rarity dùng reference đã ghi, cùng số phiên nền với probe."""
    cfg = settings()
    population, scores, sizes, provenance = _artifact()
    same = _slice_reference(scores, sizes, 6)
    rng = random.Random(7)
    probes = _probes(rng, people, 6)
    same_alarms, total = _hit_rate(probes, population, same, cfg, 0.0, 0)
    false_alarms, _total = _hit_rate(probes, population, scores, cfg, 0.0, 0)
    print("recovery curve (synthetic, between-person heterogeneity)")
    print(f"recovery reference: {provenance}; pooled chronological scores n={len(scores)}")
    print(
        f"false-alarm prior=6 pooled {false_alarms / total:.3f} ({false_alarms}/{total}) "
        f"same-baseline-size {same_alarms / total:.3f} ({same_alarms}/{total}) reference {len(same)}"
    )
    false_rate = false_alarms / total
    for gap in GAP_SHIFTS:
        for extra in (0, 1):
            detected, count = _hit_rate(probes, population, scores, cfg, gap, extra)
            print(
                f"gap +{int(gap * 100)}% background +{extra} "
                f"detection {detected / count:.3f} ({detected}/{count}) "
                f"false-alarm {false_rate:.3f} ({false_alarms}/{total})"
            )


def sensitivity(people: int = 400) -> None:
    cfg = settings()
    population, scores, sizes, provenance = _artifact()
    rng = random.Random(11)
    print(f"baseline-size sensitivity ({provenance}, gaps only, null matched to prior-session count)")
    print("prior false-alarm detection_+30% detection_+50%")
    for n_prior in BASELINE_SIZES:
        reference = _slice_reference(scores, sizes, n_prior)
        probes = _probes(rng, people, n_prior)
        alarms, total = _hit_rate(probes, population, reference, cfg, 0.0, 0)
        hit30, _ = _hit_rate(probes, population, reference, cfg, 0.30, 0)
        hit50, _ = _hit_rate(probes, population, reference, cfg, 0.50, 0)
        print(
            f"{n_prior} {alarms / total:.3f} ({alarms}/{total}) "
            f"{hit30 / total:.3f} ({hit30}/{total}) "
            f"{hit50 / total:.3f} ({hit50}/{total}) "
            f"reference {len(reference)}"
        )


def _t_crit(df: int) -> float:
    try:
        from scipy.stats import t

        return float(t.ppf(0.975, df))
    except Exception:
        return Z_ALPHA


def power() -> None:
    print("power simulation (synthetic design: 6 baseline + 2 normal-call + 3 guided per participant)")
    print("paired participant contrast, two-sided alpha 0.05, target power 80%")
    for dz in (0.5, 0.8):
        analytic = math.ceil((Z_ALPHA + Z_BETA) ** 2 / (dz * dz))
        needed = analytic
        powered = 0.0
        for size in range(max(4, analytic - 5), analytic + 40):
            powered = _power_at(size, dz)
            needed = size
            if powered >= 0.80:
                break
        at_analytic = _power_at(analytic, dz)
        print(
            f"dz {dz:.1f} analytic n {analytic} simulated power {at_analytic:.3f} "
            f"t-test n for 80% {needed} simulated power {powered:.3f}"
        )


def _power_at(participants: int, dz: float, reps: int = 2000, seed: int = 1) -> float:
    rng = np.random.default_rng(seed + participants * 10 + int(dz * 10))
    diffs = rng.normal(dz, 1.0, size=(reps, participants))
    means = diffs.mean(axis=1)
    stds = diffs.std(axis=1, ddof=1)
    stat = means / (stds / math.sqrt(participants))
    return float(np.mean(np.abs(stat) >= _t_crit(participants - 1)))


def main() -> None:
    real, reason = load_real()
    conditions = {row["condition"] for row in real}
    usable = "guided" in conditions and "normal_call" in conditions and len({row["participant_id"] for row in real}) >= 4
    if usable:
        print("behaviour_eval data: real")
        analyse(real, "real")
    else:
        why = reason or f"rows {len(real)} conditions {sorted(conditions)}"
        print(f"behaviour_eval data: synthetic ({why})")
        print("heterogeneity: log-normal sd 0.4 on gaps, dwell, and pause_to_action_ratio; personal background-switch rate")
        analyse(synthetic_people(), "synthetic")
    recovery()
    sensitivity()
    power()


if __name__ == "__main__":
    main()
