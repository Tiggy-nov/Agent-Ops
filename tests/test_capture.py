import json
import tempfile
import unittest
from pathlib import Path

from hoistway_audit.capture import Capture
from hoistway_audit.storage import Store


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "audit.db")
        self.capture = Capture(self.store, "test-secret")

    def tearDown(self):
        self.directory.cleanup()

    def test_chat_completion_call_and_output_are_paired(self):
        response = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"Dublin"}'},
                    }]
                }
            }]
        }
        count = self.capture.observe_response(
            "session-a", json.dumps(response).encode(), "application/json", now_ms=1_000
        )
        self.assertEqual(count, 1)

        request = {
            "messages": [{"role": "tool", "tool_call_id": "call-1", "content": '{"temp":18}'}]
        }
        self.capture.observe_request(
            {"x-hoistway-session-id": "session-a"}, json.dumps(request).encode(), now_ms=1_750
        )
        observations = self.store.observations()
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].tool_name, "get_weather")
        self.assertEqual(observations[0].latency_ms, 750)

    def test_raw_values_are_not_stored(self):
        response = {
            "output": [{
                "type": "function_call",
                "call_id": "call-secret",
                "name": "lookup_customer",
                "arguments": '{"email":"private@example.com"}',
            }]
        }
        self.capture.observe_response("session", json.dumps(response).encode(), "application/json", now_ms=10)
        request = {
            "input": [{"type": "function_call_output", "call_id": "call-secret", "output": "private result"}]
        }
        self.capture.observe_request({}, json.dumps(request).encode(), now_ms=20)
        database_bytes = (Path(self.directory.name) / "audit.db").read_bytes()
        self.assertNotIn(b"private@example.com", database_bytes)
        self.assertNotIn(b"private result", database_bytes)


if __name__ == "__main__":
    unittest.main()
