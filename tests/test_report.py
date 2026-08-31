import tempfile
import unittest
from pathlib import Path

from hoistway_audit.report import build_report
from hoistway_audit.storage import Store


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "audit.db")
        self.store.reset(started_at_ms=0)

    def tearDown(self):
        self.directory.cleanup()

    def add(self, call_id, session, tool, input_digest, output_digest, latency, mutating=False):
        self.store.add_observation(
            call_id=call_id,
            session_id=session,
            tool_name=tool,
            input_digest=input_digest,
            output_digest=output_digest,
            latency_ms=latency,
            observed_at_ms=1,
            mutating=mutating,
        )

    def test_stable_cross_session_repeats_are_eligible(self):
        self.add("1", "a", "read_policy", "same-input", "same-output", 1000)
        self.add("2", "b", "read_policy", "same-input", "same-output", 900)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["timing_context"]["repeated_tool_boundary_ms_lower_bound"], 900)
        self.assertEqual(report["timing_context"]["repeated_tool_boundary_ms_upper_bound"], 900)
        self.assertEqual(report["candidates"][0]["classification"], "eligible")

    def test_changed_outputs_are_excluded(self):
        self.add("1", "a", "exchange_rate", "same-input", "output-a", 400)
        self.add("2", "b", "exchange_rate", "same-input", "output-b", 450)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["timing_context"]["repeated_tool_boundary_ms_upper_bound"], 0)
        self.assertEqual(report["candidates"][0]["classification"], "output_changed")

    def test_mutating_tools_are_excluded(self):
        self.add("1", "a", "send_email", "same-input", "same-output", 500, True)
        self.add("2", "b", "send_email", "same-input", "same-output", 510, True)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["timing_context"]["repeated_tool_boundary_ms_upper_bound"], 0)
        self.assertEqual(report["candidates"][0]["classification"], "mutating")

    def test_same_session_repeats_are_not_cross_session_evidence(self):
        self.add("1", "a", "lookup", "same-input", "same-output", 500)
        self.add("2", "a", "lookup", "same-input", "same-output", 510)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["candidates"][0]["classification"], "single_session")

    def test_timing_range_respects_multi_call_batches(self):
        self.store.add_observation("a1", "s1", "lookup_a", "a", "stable-a", 100, 1, batch_id="b1", batch_size=2)
        self.store.add_observation("b1", "s1", "lookup_b", "b", "stable-b", 100, 1, batch_id="b1", batch_size=2)
        self.store.add_observation("a2", "s2", "lookup_a", "a", "stable-a", 400, 2, batch_id="b2", batch_size=2)
        self.store.add_observation("b2", "s2", "lookup_b", "b", "changed-b", 400, 2, batch_id="b2", batch_size=2)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["timing_context"]["repeated_tool_boundary_ms_lower_bound"], 0)
        self.assertEqual(report["timing_context"]["repeated_tool_boundary_ms_upper_bound"], 400)
        self.assertEqual(report["timing_context"]["batch_size_distribution"], [{"batch_size": 2, "turns": 2}])

    def test_stability_curve_preserves_the_time_dimension(self):
        self.store.add_observation("1", "a", "search", "same", "result-a", 100, 0)
        self.store.add_observation("2", "b", "search", "same", "result-a", 100, 5 * 60_000)
        self.store.add_observation("3", "c", "search", "same", "result-b", 100, 6 * 60 * 60_000)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        row = next(row for row in report["stability"]["rows"] if row["scope"] == "search")
        self.assertEqual(len(row["buckets"]), 7)
        self.assertEqual(row["observed_safe_reuse_window_ms"], 3_600_000)
        ten_minutes = next(bucket for bucket in row["buckets"] if bucket["window"] == "10m")
        six_hours = next(bucket for bucket in row["buckets"] if bucket["window"] == "6h")
        self.assertEqual(ten_minutes["exact_match_rate"], 1.0)
        self.assertEqual(six_hours["exact_match_rate"], 0.5)
        self.assertEqual(report["answer"]["eligible_repeated_calls"], 1)

    def test_stability_reports_exact_and_near_identical_separately(self):
        near = 0b111111
        self.store.add_observation("1", "a", "search", "same", "exact-a", 100, 0, output_simhash=near)
        self.store.add_observation("2", "b", "search", "same", "exact-b", 100, 10_000, output_simhash=near)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        row = next(row for row in report["stability"]["rows"] if row["scope"] == "search")
        bucket = next(bucket for bucket in row["buckets"] if bucket["window"] == "30s")
        self.assertEqual(bucket["exact_match_rate"], 0.0)
        self.assertEqual(bucket["near_identical_rate"], 1.0)
        self.assertEqual(report["answer"]["eligible_repeated_calls"], 1)

    def test_reports_within_and_cross_session_repetition_separately(self):
        self.store.add_observation("1", "a", "lookup", "same", "stable", 100, 0)
        self.store.add_observation("2", "a", "lookup", "same", "stable", 100, 1)
        self.store.add_observation("3", "b", "lookup", "same", "stable", 100, 2)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["repetition"]["eligible_calls"], 3)
        self.assertEqual(report["repetition"]["cross_session"]["repeat_rate"], 1 / 3)
        self.assertEqual(report["repetition"]["within_session"]["repeat_rate"], 1 / 3)
        self.assertEqual(report["repetition"]["total"]["repeat_rate"], 2 / 3)

    def test_report_ends_with_pre_registered_pass_kill_verdicts(self):
        self.store.add_observation("1", "a", "lookup", "same", "stable", 100, 0)
        self.store.add_observation("2", "b", "lookup", "same", "stable", 100, 1)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(list(report)[-1], "verdicts")
        self.assertEqual([item["threshold"] for item in report["verdicts"]], [0.40, 0.95, 0.10])
        self.assertTrue(all(item["verdict"] in {"PASS", "KILL"} for item in report["verdicts"]))
        self.assertIn("dropped_argument_keys", report["canonicalisation"])
        self.assertIn("stripped_url_parameters", report["canonicalisation"])


if __name__ == "__main__":
    unittest.main()
