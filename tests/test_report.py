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
        self.assertEqual(report["answer"]["removable_latency_ms"], 900)
        self.assertEqual(report["candidates"][0]["classification"], "eligible")

    def test_changed_outputs_are_excluded(self):
        self.add("1", "a", "exchange_rate", "same-input", "output-a", 400)
        self.add("2", "b", "exchange_rate", "same-input", "output-b", 450)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["answer"]["removable_latency_ms"], 0)
        self.assertEqual(report["candidates"][0]["classification"], "output_changed")

    def test_mutating_tools_are_excluded(self):
        self.add("1", "a", "send_email", "same-input", "same-output", 500, True)
        self.add("2", "b", "send_email", "same-input", "same-output", 510, True)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["answer"]["removable_latency_ms"], 0)
        self.assertEqual(report["candidates"][0]["classification"], "mutating")

    def test_same_session_repeats_are_not_cross_session_evidence(self):
        self.add("1", "a", "lookup", "same-input", "same-output", 500)
        self.add("2", "a", "lookup", "same-input", "same-output", 510)
        report = build_report(self.store, 48, now_ms=48 * 3_600_000)
        self.assertEqual(report["candidates"][0]["classification"], "single_session")


if __name__ == "__main__":
    unittest.main()
