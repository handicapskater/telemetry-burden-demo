"""Deterministic summaries of the normalized WHOOP workout records."""

from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import Any, Iterable

import numpy as np
import pandas as pd


def _mapping(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return record
    if hasattr(record, "model_dump"):
        return record.model_dump()
    raise TypeError("records must be mappings or TelemetryRecord models")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _identity(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    original = row.get("original_timestamps") or {}
    activities = row.get("original_activities") or {}
    return (
        original.get("whoop_start") or row.get("timestamp"),
        activities.get("whoop") or row.get("activity"),
        _number(row.get("duration_minutes")),
    )


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 2) if values else None


def _median(values: list[float]) -> float | None:
    return round(float(pd.Series(values, dtype="float64").median()), 2) if values else None


def _max(values: list[float]) -> float | None:
    return round(float(np.max(values)), 2) if values else None


def _burden_class(value: float | None) -> str | None:
    if value is None:
        return "INSUFFICIENT DATA"
    return "LOWER OBSERVED BURDEN" if value < 40 else "MODERATE OBSERVED BURDEN" if value < 100 else "ELEVATED OBSERVED BURDEN"


def _difference(first: float | None, second: float | None) -> dict[str, float | None]:
    if first is None or second is None:
        return {"signed_absolute": None, "signed_percent_of_first": None}
    signed = round(second - first, 2)
    return {"signed_absolute": signed, "signed_percent_of_first": round(signed / abs(first) * 100, 2) if first else None}


def analyze_records(records: Iterable[Any]) -> dict[str, Any]:
    """Summarize unique WHOOP workouts; preserve row IDs as audit evidence.

    Burden is a descriptive proxy: duration (minutes) × mean workout HR (bpm)
    / 100. It is not a clinical load, strain, or energy estimate.
    """
    rows = [_mapping(record) for record in records]
    seen: set[tuple[Any, Any, Any]] = set()
    unique: list[dict[str, Any]] = []
    duplicate_count = 0
    for row in rows:
        identity = _identity(row)
        if identity in seen:
            duplicate_count += 1
            continue
        seen.add(identity)
        unique.append(row)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in unique:
        groups[str(row.get("activity") or "unknown")].append(row)

    activities: dict[str, dict[str, Any]] = {}
    for activity, group in sorted(groups.items()):
        durations = [v for row in group if (v := _number(row.get("duration_minutes"))) is not None]
        heart_rates = [v for row in group if (v := _number((row.get("heart_rate") or {}).get("mean_bpm"))) is not None]
        max_heart_rates = [v for row in group if (v := _number((row.get("heart_rate") or {}).get("max_bpm"))) is not None]
        daily_hrv = [v for row in group if (v := _number((row.get("hrv") or {}).get("hrv_ms"))) is not None]
        burdens = []
        for row in group:
            duration = _number(row.get("duration_minutes"))
            heart_rate = _number((row.get("heart_rate") or {}).get("mean_bpm"))
            if duration is not None and heart_rate is not None and duration >= 0 and heart_rate >= 0:
                burdens.append(duration * heart_rate / 100)
        activities[activity] = {
            "count": len(group),
            "mean_duration_minutes": _mean(durations),
            "median_duration_minutes": _median(durations),
            "max_duration_minutes": _max(durations),
            "mean_heart_rate_bpm": _mean(heart_rates),
            "heart_rate_sample_count": len(heart_rates),
            "median_heart_rate_bpm": _median(heart_rates),
            "max_heart_rate_bpm": _max(max_heart_rates),
            "mean_daily_hrv_ms": _mean(daily_hrv),
            "median_daily_hrv_ms": _median(daily_hrv),
            "daily_hrv_sample_count": len(daily_hrv),
            "mean_burden_proxy": _mean(burdens),
            "burden_classification": _burden_class(_mean(burdens)),
            "burden_sample_count": len(burdens),
            "evidence_ids": [str(row["record_id"]) for row in group if row.get("record_id") is not None],
        }
    comparisons = []
    targets = [category for category in ("walking", "skating", "transportation") if category in activities]
    for first_index, first in enumerate(targets):
      for second in targets[first_index + 1:]:
        left, right = activities[first], activities[second]
        comparisons.append({
            "activity_a": first, "activity_b": second,
            "sample_size_a": left["heart_rate_sample_count"], "sample_size_b": right["heart_rate_sample_count"],
            "median_hr_bpm_a": left["median_heart_rate_bpm"],
            "median_hr_bpm_b": right["median_heart_rate_bpm"],
            "median_hr_difference_b_minus_a": _difference(left["median_heart_rate_bpm"], right["median_heart_rate_bpm"]),
            "mean_daily_hrv_ms_a": left["mean_daily_hrv_ms"],
            "mean_daily_hrv_ms_b": right["mean_daily_hrv_ms"],
            "daily_hrv_contextual_difference_b_minus_a": _difference(left["mean_daily_hrv_ms"], right["mean_daily_hrv_ms"]),
            "interpretation": "Descriptive observed workout median HR comparison; no causal or clinical conclusion.",
            "limitations": ["WHOOP daily HRV is recovery context, not activity HRV.", "Workout duration is not elevated-HR duration; elevated-HR duration is unavailable without time series.", "Different activity samples may differ in exertion, fitness, medication, and temperature."],
        })
    return {
        "input_rows": len(rows),
        "unique_workouts": len(unique),
        "duplicate_rows": duplicate_count,
        "activities": activities,
        "comparisons": comparisons,
        "observed_activities": list(activities),
        "burden_method": "duration_minutes × mean_workout_heart_rate_bpm / 100; descriptive heuristic, not clinical load, WHOOP strain, or calories",
        "burden_classification_method": "mean burden proxy <40: LOWER OBSERVED BURDEN; 40 to <100: MODERATE OBSERVED BURDEN; >=100: ELEVATED OBSERVED BURDEN; missing: INSUFFICIENT DATA. Descriptive thresholds only, not health risk categories.",
        "hrv_context": "WHOOP daily recovery HRV; not an activity HRV measurement",
        "limitations": ["Only observed WHOOP workout categories are summarized.", "Duration is reported WHOOP workout duration, not moving time.", "Elevated-HR duration is unavailable without heart-rate time series.", "Daily recovery HRV is contextual and cannot be attributed to a workout."],
    }
