import asyncio
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import uvicorn

from hoistway_audit.config import Config
from hoistway_audit.report import build_report
from hoistway_audit.server import create_app


STREAM_BODY = (
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"stream-call",'
    b'"function":{"name":"search","arguments":"{\\"q\\":\\"agents\\"}"}}]}}]}\n\n'
    b'data: [DONE]\n\n'
)


class UpstreamHandler(BaseHTTPRequestHandler):
    bodies = []

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(STREAM_BODY)))
            self.end_headers()
            midpoint = len(STREAM_BODY) // 2
            self.wfile.write(STREAM_BODY[:midpoint])
            self.wfile.flush()
            self.wfile.write(STREAM_BODY[midpoint:])
            return
        self.send_error(404)

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


class LoadHTTPServer(ThreadingHTTPServer):
    request_queue_size = 128


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        UpstreamHandler.bodies = []
        self.upstream = LoadHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()

        proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        proxy_socket.bind(("127.0.0.1", 0))
        proxy_socket.listen(128)
        self.proxy_port = proxy_socket.getsockname()[1]
        config = Config(
            host="127.0.0.1",
            port=self.proxy_port,
            upstream_url=f"http://127.0.0.1:{self.upstream.server_port}",
            database_path=Path(self.directory.name) / "audit.db",
            dashboard_enabled=False,
            audit_hours=48,
            hash_secret="integration-secret",
        )
        self.app = create_app(config)
        self.proxy = uvicorn.Server(
            uvicorn.Config(self.app, log_level="error", lifespan="on")
        )
        self.proxy_thread = threading.Thread(
            target=self.proxy.run, kwargs={"sockets": [proxy_socket]}, daemon=True
        )
        self.proxy_thread.start()
        deadline = time.time() + 5
        while not self.proxy.started and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.proxy.started)

    def tearDown(self):
        self.proxy.should_exit = True
        self.proxy_thread.join(timeout=5)
        self.upstream.shutdown()
        self.upstream.server_close()
        self.directory.cleanup()

    def post(self, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy_port, timeout=5)
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
        report = build_report(self.app.state.store, 48)
        self.assertEqual(report["coverage"]["tool_calls"], 1)
        self.assertEqual(report["coverage"]["sessions"], 1)

    def test_stream_is_byte_identical_under_fifty_concurrent_connections(self):
        async def exercise():
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{self.proxy_port}", timeout=10
            ) as client:
                async def fetch(index):
                    async with client.stream(
                        "GET",
                        "/stream",
                        headers={"X-Hoistway-Session-ID": f"load-{index}"},
                    ) as response:
                        self.assertEqual(response.status_code, 200)
                        return b"".join([chunk async for chunk in response.aiter_raw()])

                return await asyncio.gather(*(fetch(index) for index in range(50)))

        bodies = asyncio.run(exercise())
        self.assertEqual(len(bodies), 50)
        self.assertTrue(all(body == STREAM_BODY for body in bodies))


if __name__ == "__main__":
    unittest.main()
