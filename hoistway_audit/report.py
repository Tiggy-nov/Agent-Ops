from __future__ import annotations

import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass

from .storage import Observation, Store


@dataclass(frozen=True)
class Candidate:
    tool_name: str
    fingerprint_id: str
    calls: int
    sessions: int
    output_stability: float
    median_latency_ms: int
    removable_calls: int
    removable_latency_ms: int
    classification: str


def build_report(store: Store, audit_hours: int, now_ms: int | None = None) -> dict:
    observations = store.observations()
    current_ms = now_ms or int(time.time() * 1000)
    started_at = store.started_at_ms()
    elapsed_ms = max(0, current_ms - started_at)
    target_ms = audit_hours * 60 * 60 * 1000

    grouped: defaultdict[tuple[str, str], list[Observation]] = defaultdict(list)
    for observation in observations:
        grouped[(observation.tool_name, observation.input_digest)].append(observation)

    candidates: list[Candidate] = []
    for (tool_name, input_digest), calls in grouped.items():
        sessions = {call.session_id for call in calls if call.session_id != "unscoped"}
        output_counts: defaultdict[str, int] = defaultdict(int)
        for call in calls:
            output_counts[call.output_digest] += 1
        stability = max(output_counts.values()) / len(calls)
        cross_session = len(sessions) >= 2
        mutating = any(call.mutating for call in calls)
        safe = cross_session and stability == 1.0 and not mutating
        removable_calls = max(0, len(calls) - 1) if safe else 0
        removable_latency = sum(call.latency_ms for call in calls[1:]) if safe else 0
        classification = (
            "eligible"
            if safe
            else "mutating"
            if mutating
            else "output_changed"
            if stability < 1.0
            else "single_session"
        )
        if len(calls) > 1:
            candidates.append(
                Candidate(
                    tool_name=tool_name,
                    fingerprint_id=input_digest[0:8],
                    calls=len(calls),
                    sessions=len(sessions),
                    output_stability=stability,
                    median_latency_ms=int(statistics.median(call.latency_ms for call in calls)),
                    removable_calls=removable_calls,
                    removable_latency_ms=removable_latency,
                    classification=classification,
                )
            )

    candidates.sort(key=lambda item: item.removable_latency_ms, reverse=True)
    eligible = [item for item in candidates if item.classification == "eligible"]
    total_latency = sum(item.latency_ms for item in observations)
    removable_latency = sum(item.removable_latency_ms for item in eligible)
    sessions = {item.session_id for item in observations if item.session_id != "unscoped"}
    completed = elapsed_ms >= target_ms
    progress = min(1.0, elapsed_ms / target_ms) if target_ms else 1.0

    return {
        "audit": {
            "started_at_ms": started_at,
            "elapsed_hours": elapsed_ms / 3_600_000,
            "target_hours": audit_hours,
            "progress": progress,
            "complete": completed,
            "mode": "read_only",
        },
        "coverage": {
            "tool_calls": len(observations),
            "sessions": len(sessions),
            "tools": len({item.tool_name for item in observations}),
            "unscoped_calls": sum(item.session_id == "unscoped" for item in observations),
        },
        "answer": {
            "observed_tool_latency_ms": total_latency,
            "removable_latency_ms": removable_latency,
            "removable_share": removable_latency / total_latency if total_latency else 0.0,
            "eligible_fingerprints": len(eligible),
            "eligible_repeated_calls": sum(item.removable_calls for item in eligible),
        },
        "candidates": [asdict(item) for item in candidates],
        "guardrails": {
            "raw_payloads_stored": False,
            "traffic_modified": False,
            "mutating_tools_excluded": True,
            "output_match_required": True,
            "cross_session_required": True,
        },
    }
