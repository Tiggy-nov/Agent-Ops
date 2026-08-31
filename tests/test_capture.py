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
        self.capture.observe_request(
            {"x-hoistway-session-id": "session"}, json.dumps(request).encode(), now_ms=20
        )
        database_bytes = (Path(self.directory.name) / "audit.db").read_bytes()
        self.assertNotIn(b"private@example.com", database_bytes)
        self.assertNotIn(b"private result", database_bytes)

    def test_user_and_generic_session_headers_are_not_session_identity(self):
        response = {
            "choices": [{"message": {"tool_calls": [{
                "id": "unscoped-call",
                "function": {"name": "lookup", "arguments": "{}"},
            }]}}]
        }
        count = self.capture.observe_request(
            {"x-session-id": "generic"},
            json.dumps({"user": "person-1"}).encode(),
        )
        self.assertEqual(count, "unscoped")
        captured = self.capture.observe_response(
            "unscoped", json.dumps(response).encode(), "application/json", now_ms=10
        )
        self.assertEqual(captured, 0)
        self.assertEqual(self.store.dropped_missing_session(), 1)

    def test_anthropic_non_streaming_tool_use_and_result_are_paired(self):
        response = {
            "content": [{
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_weather",
                "input": {"city": "Dublin"},
            }]
        }
        self.assertEqual(
            self.capture.observe_response("anthropic-session", json.dumps(response).encode(), "application/json", now_ms=100),
            1,
        )
        request = {
            "messages": [{
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "18C"}],
            }]
        }
        self.capture.observe_request(
            {"x-hoistway-session-id": "anthropic-session"}, json.dumps(request).encode(), now_ms=250
        )
        self.assertEqual(self.store.observations()[0].tool_name, "get_weather")

    def test_anthropic_streaming_tool_use_is_reconstructed(self):
        stream = b"\n".join([
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_stream","name":"search","input":{}}}',
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"q\\":\\"agents\\"}"}}',
            b'data: {"type":"content_block_stop","index":0}',
        ])
        self.assertEqual(self.capture.observe_response("s", stream, "text/event-stream", now_ms=10), 1)

    def test_gemini_function_call_and_response_are_paired(self):
        response = {
            "candidates": [{"content": {"parts": [{
                "functionCall": {"name": "lookup_policy", "args": {"id": 7}}
            }]}}]
        }
        self.assertEqual(self.capture.observe_response("gemini-session", json.dumps(response).encode(), "application/json", now_ms=10), 1)
        request = {
            "contents": [{"role": "function", "parts": [{
                "functionResponse": {"name": "lookup_policy", "response": {"version": 3}}
            }]}]
        }
        self.capture.observe_request(
            {"x-hoistway-session-id": "gemini-session"}, json.dumps(request).encode(), now_ms=30
        )
        self.assertEqual(self.store.observations()[0].tool_name, "lookup_policy")

    def test_gemini_sse_function_call_is_recognised(self):
        stream = b'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"search","args":{"q":"agents"}}}]}}]}\n\n'
        self.assertEqual(self.capture.observe_response("gemini-sse", stream, "text/event-stream", now_ms=10), 1)


if __name__ == "__main__":
    unittest.main()
