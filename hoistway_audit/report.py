from __future__ import annotations

import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass

from .privacy import CANONICALIZATION_RULES, simhash_similarity
from .storage import Observation, RepeatPair, Store


STABILITY_BUCKETS = (
    ("30s", 30_000),
    ("1m", 60_000),
    ("5m", 300_000),
    ("10m", 600_000),
    ("1h", 3_600_000),
    ("6h", 21_600_000),
    ("24h", 86_400_000),
)
STABILITY_THRESHOLD = 0.95
NEAR_IDENTICAL_THRESHOLD = 0.95
CROSS_SESSION_SHARE_THRESHOLD = 0.40
TEN_MINUTE_STABILITY_THRESHOLD = 0.95
ELIGIBLE_CALL_SHARE_THRESHOLD = 0.10


@dataclass(frozen=True)
class Candidate:
    tool_name: str
    fingerprint_id: str
    calls: int
    sessions: int
    output_stability: float
    observed_safe_reuse_window_ms: int
    median_observed_tool_boundary_ms: int
    eligible_repeated_calls: int
    repeated_observed_tool_boundary_ms: int
    classification: str


def _exact_rate(pairs: list[RepeatPair]) -> float | None:
    if not pairs:
        return None
    return sum(pair.prev_output_digest == pair.output_digest for pair in pairs) / len(pairs)


def _pair_similarity(pair: RepeatPair) -> float | None:
    return simhash_similarity(pair.prev_output_simhash, pair.output_simhash)


def _near_identical(pair: RepeatPair) -> bool:
    if pair.prev_output_digest == pair.output_digest:
        return True
    if (
        pair.prev_url_set_digest is not None
        and pair.prev_url_set_digest == pair.output_url_set_digest
    ):
        return True
    similarity = _pair_similarity(pair)
    return similarity is not None and similarity >= NEAR_IDENTICAL_THRESHOLD


def _stability_row(scope: str, pairs: list[RepeatPair]) -> dict:
    buckets = []
    safe_window_ms = 0
    for label, window_ms in STABILITY_BUCKETS:
        in_window = [pair for pair in pairs if 0 <= pair.ts - pair.prev_ts <= window_ms]
        exact_rate = _exact_rate(in_window)
        near_rate = (
            sum(_near_identical(pair) for pair in in_window) / len(in_window)
            if in_window
            else None
        )
        similarities = [value for pair in in_window if (value := _pair_similarity(pair)) is not None]
        if near_rate is not None and near_rate >= STABILITY_THRESHOLD:
            safe_window_ms = window_ms
        buckets.append(
            {
                "window": label,
                "window_ms": window_ms,
                "repeat_pairs": len(in_window),
                "exact_match_rate": exact_rate,
                "near_identical_rate": near_rate,
                "mean_simhash_similarity": sum(similarities) / len(similarities) if similarities else None,
            }
        )
    return {
        "scope": scope,
        "repeat_pairs": len(pairs),
        "observed_safe_reuse_window_ms": safe_window_ms,
        "buckets": buckets,
    }


def build_report(store: Store, audit_hours: int, now_ms: int | None = None) -> dict:
    all_observations = store.observations()
    observations = [item for item in all_observations if item.session_id != "unscoped"]
    repeat_pairs = [
        pair for pair in store.repeat_pairs()
        if pair.session_id != "unscoped" and pair.prev_session_id != "unscoped"
    ]
    current_ms = now_ms or int(time.time() * 1000)
    started_at = store.started_at_ms()
    elapsed_ms = max(0, current_ms - started_at)
    target_ms = audit_hours * 60 * 60 * 1000

    mutating_tools = {item.tool_name for item in observations if item.mutating}
    stability_pairs = [pair for pair in repeat_pairs if pair.tool_name not in mutating_tools]
    pairs_by_tool: defaultdict[str, list[RepeatPair]] = defaultdict(list)
    for pair in stability_pairs:
        pairs_by_tool[pair.tool_name].append(pair)
    stability_rows = [_stability_row("all_tools", stability_pairs)] + [
        _stability_row(tool_name, pairs_by_tool[tool_name])
        for tool_name in sorted(pairs_by_tool)
    ]
    safe_window_by_tool = {
        row["scope"]: row["observed_safe_reuse_window_ms"]
        for row in stability_rows
        if row["scope"] != "all_tools"
    }
    analysis_eligible_calls = [item for item in observations if not item.mutating]
    analysis_eligible_pairs = [pair for pair in repeat_pairs if pair.tool_name not in mutating_tools]
    within_session_pairs = [
        pair for pair in analysis_eligible_pairs if pair.session_id == pair.prev_session_id
    ]
    cross_session_pairs_all = [
        pair for pair in analysis_eligible_pairs if pair.session_id != pair.prev_session_id
    ]
    eligible_denominator = len(analysis_eligible_calls)
    dropped_missing_session = (
        store.dropped_missing_session()
        + sum(item.session_id == "unscoped" for item in all_observations)
    )
    eligible_call_ids = {
        pair.call_id
        for pair in repeat_pairs
        if pair.tool_name not in mutating_tools
        and pair.session_id != pair.prev_session_id
        and _near_identical(pair)
        and 0 <= pair.ts - pair.prev_ts <= safe_window_by_tool.get(pair.tool_name, 0)
    }

    pairs_by_fingerprint: defaultdict[tuple[str, str], list[RepeatPair]] = defaultdict(list)
    for pair in repeat_pairs:
        pairs_by_fingerprint[(pair.tool_name, pair.call_digest)].append(pair)
    grouped: defaultdict[tuple[str, str], list[Observation]] = defaultdict(list)
    for observation in observations:
        grouped[(observation.tool_name, observation.input_digest)].append(observation)

    candidates: list[Candidate] = []
    for (tool_name, input_digest), calls in grouped.items():
        if len(calls) < 2:
            continue
        pairs = pairs_by_fingerprint[(tool_name, input_digest)]
        sessions = {call.session_id for call in calls if call.session_id != "unscoped"}
        exact_rate = _exact_rate(pairs) or 0.0
        mutating = any(call.mutating for call in calls)
        eligible_for_pattern = [pair for pair in pairs if pair.call_id in eligible_call_ids]
        cross_session_pairs = [pair for pair in pairs if pair.session_id != pair.prev_session_id]
        classification = (
            "mutating"
            if mutating
            else "eligible"
            if eligible_for_pattern
            else "single_session"
            if not cross_session_pairs
            else "output_changed"
            if not any(_near_identical(pair) for pair in cross_session_pairs)
            else "outside_safe_window"
        )
        eligible_ids = {pair.call_id for pair in eligible_for_pattern}
        candidates.append(
            Candidate(
                tool_name=tool_name,
                fingerprint_id=input_digest[:8],
                calls=len(calls),
                sessions=len(sessions),
                output_stability=exact_rate,
                observed_safe_reuse_window_ms=safe_window_by_tool.get(tool_name, 0),
                median_observed_tool_boundary_ms=int(statistics.median(call.latency_ms for call in calls)),
                eligible_repeated_calls=len(eligible_ids),
                repeated_observed_tool_boundary_ms=sum(
                    call.latency_ms for call in calls if call.call_id in eligible_ids
                ),
                classification=classification,
            )
        )

    candidates.sort(key=lambda item: item.repeated_observed_tool_boundary_ms, reverse=True)
    total_boundary = sum(item.latency_ms for item in observations)
    repeated_boundary_upper = sum(
        item.latency_ms for item in observations if item.call_id in eligible_call_ids
    )
    by_batch: defaultdict[str, list[Observation]] = defaultdict(list)
    for item in observations:
        by_batch[item.batch_id].append(item)
    repeated_boundary_lower = sum(
        max(item.latency_ms for item in batch)
        for batch in by_batch.values()
        if len(batch) == batch[0].batch_size
        and all(item.call_id in eligible_call_ids for item in batch)
    )
    eligible_in_multi_call_batches = sum(
        item.call_id in eligible_call_ids and item.batch_size > 1 for item in observations
    )
    batch_size_distribution: defaultdict[int, int] = defaultdict(int)
    for batch in by_batch.values():
        batch_size_distribution[batch[0].batch_size] += 1

    sessions = {item.session_id for item in observations if item.session_id != "unscoped"}
    completed = elapsed_ms >= target_ms
    progress = min(1.0, elapsed_ms / target_ms) if target_ms else 1.0
    cross_session_share_of_repeats = (
        len(cross_session_pairs_all) / len(analysis_eligible_pairs)
        if analysis_eligible_pairs else 0.0
    )
    overall_ten_minute = next(
        bucket for bucket in stability_rows[0]["buckets"] if bucket["window"] == "10m"
    )
    ten_minute_stability = overall_ten_minute["near_identical_rate"] or 0.0
    all_tool_calls = len(all_observations) + store.dropped_missing_session()
    eligible_call_share = len(eligible_call_ids) / all_tool_calls if all_tool_calls else 0.0
    verdicts = [
        {
            "criterion": "Cross-session share of eligible repeated calls",
            "measured": cross_session_share_of_repeats,
            "threshold": CROSS_SESSION_SHARE_THRESHOLD,
            "verdict": "PASS" if cross_session_share_of_repeats >= CROSS_SESSION_SHARE_THRESHOLD else "KILL",
        },
        {
            "criterion": "Output stability at a 10-minute reuse window",
            "measured": ten_minute_stability,
            "threshold": TEN_MINUTE_STABILITY_THRESHOLD,
            "verdict": "PASS" if ten_minute_stability >= TEN_MINUTE_STABILITY_THRESHOLD else "KILL",
        },
        {
            "criterion": "Eligible calls as a share of all tool calls",
            "measured": eligible_call_share,
            "threshold": ELIGIBLE_CALL_SHARE_THRESHOLD,
            "verdict": "PASS" if eligible_call_share >= ELIGIBLE_CALL_SHARE_THRESHOLD else "KILL",
        },
    ]

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
            "calls_dropped_missing_session_identity": dropped_missing_session,
            "repeat_pairs": len(repeat_pairs),
        },
        "answer": {
            "question": "How much of this fleet's tool work is repeated across sessions with identical results?",
            "eligible_repeated_calls": len(eligible_call_ids),
            "eligible_repeat_share": eligible_call_share,
        },
        "repetition": {
            "denominator": "non_mutating_calls_with_session_identity",
            "eligible_calls": eligible_denominator,
            "cross_session": {
                "repeated_calls": len(cross_session_pairs_all),
                "repeat_rate": len(cross_session_pairs_all) / eligible_denominator if eligible_denominator else 0.0,
            },
            "within_session": {
                "repeated_calls": len(within_session_pairs),
                "repeat_rate": len(within_session_pairs) / eligible_denominator if eligible_denominator else 0.0,
            },
            "total": {
                "repeated_calls": len(analysis_eligible_pairs),
                "repeat_rate": len(analysis_eligible_pairs) / eligible_denominator if eligible_denominator else 0.0,
            },
        },
        "stability": {
            "threshold": STABILITY_THRESHOLD,
            "near_identical_threshold": NEAR_IDENTICAL_THRESHOLD,
            "bucket_mode": "cumulative_gap_at_or_below_window",
            "rows": stability_rows,
        },
        "timing_context": {
            "observed_tool_boundary_ms": total_boundary,
            "repeated_tool_boundary_ms_lower_bound": repeated_boundary_lower,
            "repeated_tool_boundary_ms_upper_bound": repeated_boundary_upper,
            "eligible_calls_in_multi_call_batches_share": (
                eligible_in_multi_call_batches / len(eligible_call_ids) if eligible_call_ids else 0.0
            ),
            "batch_size_distribution": [
                {"batch_size": size, "turns": count}
                for size, count in sorted(batch_size_distribution.items())
            ],
            "caveat": (
                "Measured from model emission to result re-entry and includes local orchestration overhead. "
                "The lower bound counts a turn only when every call in its batch is eligible; the upper "
                "bound assumes serial execution. The truth depends on concurrency the proxy cannot observe "
                "and requires framework timing metadata or tool-side capture."
            ),
        },
        "candidates": [asdict(item) for item in candidates],
        "canonicalisation": CANONICALIZATION_RULES,
        "guardrails": {
            "raw_payloads_stored": False,
            "traffic_modified": False,
            "mutating_tools_excluded": True,
            "per_tool_stability_window_required": True,
            "cross_session_required": True,
        },
        "verdicts": verdicts,
    }
