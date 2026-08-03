from __future__ import annotations

from typing import TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
PhaseSample: TypeAlias = dict[str, float]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _stats(values: list[float]) -> JsonObject:
    return {
        "samples_ms": [round(value, 3) for value in values],
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
    }


def phase_scenario(
    samples: list[PhaseSample],
    walls: list[float],
    phases: tuple[str, ...],
) -> JsonObject:
    return {
        "sample_count": len(samples),
        "phases": {
            phase: _stats([sample[phase] for sample in samples]) for phase in phases
        },
        "group_wall": _stats(walls),
    }


def actual_scenario(samples: list[float], walls: list[float]) -> JsonObject:
    return {
        "sample_count": len(samples),
        "total": _stats(samples),
        "group_wall": _stats(walls),
    }


def statusless_invocations(raw: JsonValue) -> int:
    if not isinstance(raw, dict):
        return 0
    turns = raw.get("active_turns")
    if not isinstance(turns, dict):
        return 0
    return sum(
        1
        for turn in turns.values()
        if isinstance(turn, dict)
        for invocations in (turn.get("invocations"),)
        if isinstance(invocations, dict)
        for entry in invocations.values()
        if isinstance(entry, dict) and "status" not in entry
    )
