import http.client
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hoistway_audit.config import Config
from hoistway_audit.report import build_report
from hoistway_audit.server import AuditServer


class UpstreamHandler(BaseHTTPRequestHandler):
    bodies = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.bodies.append(body)
        payload = json.loads(body)
        if any(message.get("role") == "tool" for message in payload.get("messages", [])):
            response = {"choices": [{"message": {"role": "assistant", "content": "done"}}]}
        else:
            response = {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "proxy-call-1",
                            "type": "function",
                            "function": {"name": "read_policy", "arguments": '{"id":7}'},
                        }],
                    }
                }]
            }
        data = json.dumps(response, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        UpstreamHandler.bodies = []
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        config = Config(
            host="127.0.0.1",
            port=0,
            upstream_url=f"http://127.0.0.1:{self.upstream.server_port}",
            database_path=Path(self.directory.name) / "audit.db",
            dashboard_enabled=False,
            audit_hours=48,
            hash_secret="integration-secret",
        )
        self.proxy = AuditServer(config)
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()

    def tearDown(self):
        self.proxy.shutdown()
        self.proxy.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.directory.cleanup()

    def post(self, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=5)
        body = json.dumps(payload, separators=(",", ":")).encode()
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Authorization": "Bearer test-provider-key",
                "X-Hoistway-Session-ID": "session-integration",
            },
        )
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_forwards_unchanged_and_records_round_trip(self):
        first_request = {"model": "test", "messages": [{"role": "user", "content": "read policy"}]}
        status, first_response = self.post(first_request)
        self.assertEqual(status, 200)
        self.assertEqual(UpstreamHandler.bodies[0], json.dumps(first_request, separators=(",", ":")).encode())
        parsed = json.loads(first_response)
        self.assertEqual(parsed["choices"][0]["message"]["tool_calls"][0]["id"], "proxy-call-1")

        second_request = {
            "model": "test",
            "messages": [{"role": "tool", "tool_call_id": "proxy-call-1", "content": '{"version":7}'}],
        }
        status, second_response = self.post(second_request)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(second_response)["choices"][0]["message"]["content"], "done")
        report = build_report(self.proxy.store, 48)
        self.assertEqual(report["coverage"]["tool_calls"], 1)
        self.assertEqual(report["coverage"]["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
