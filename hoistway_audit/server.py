from __future__ import annotations

import http.client
import json
import mimetypes
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .capture import Capture
from .config import Config
from .privacy import keyed_digest
from .report import build_report
from .storage import Store


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class AuditServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config.database_path)
        self.capture = Capture(self.store, config.hash_secret)
        super().__init__((config.host, config.port), AuditHandler)


class AuditHandler(BaseHTTPRequestHandler):
    server: AuditServer
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        if self.path in {"/", "/report"} and self.server.config.dashboard_enabled:
            return self._static("index.html")
        if self.path.startswith("/assets/") and self.server.config.dashboard_enabled:
            return self._static(self.path.removeprefix("/assets/"))
        if self.path == "/audit/status":
            return self._json(build_report(self.server.store, self.server.config.audit_hours)["audit"])
        if self.path == "/audit/report":
            return self._json(build_report(self.server.store, self.server.config.audit_hours))
        if self.path == "/healthz":
            return self._json({"status": "ok", "mode": "read_only"})
        self._proxy()

    def do_POST(self) -> None:
        if self.path == "/audit/demo/seed":
            self._seed_demo()
            return self._json(build_report(self.server.store, self.server.config.audit_hours))
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_PATCH(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def _proxy(self) -> None:
        body_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(body_length) if body_length else b""
        header_map = {key.lower(): value for key, value in self.headers.items()}
        session = self.server.capture.observe_request(header_map, body)

        upstream = urlparse(self.server.config.upstream_url)
        incoming = urlparse(self.path)
        base_path = upstream.path.rstrip("/")
        target_path = f"{base_path}{incoming.path}"
        if incoming.query:
            target_path += f"?{incoming.query}"

        connection_class = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(upstream.hostname, upstream.port, timeout=120)
        forwarded_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP | {"host", "content-length", "accept-encoding"}
            and not key.lower().startswith("x-hoistway-")
        }
        forwarded_headers["Host"] = upstream.netloc
        forwarded_headers["Accept-Encoding"] = "identity"
        if body:
            forwarded_headers["Content-Length"] = str(len(body))

        try:
            connection.request(self.command, target_path, body=body or None, headers=forwarded_headers)
            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            content_type = response.getheader("Content-Type", "application/octet-stream")
            for key, value in response.getheaders():
                if key.lower() in HOP_BY_HOP | {"content-length", "content-encoding"}:
                    continue
                self.send_header(key, value)
            self.send_header("X-Hoistway-Audit", "observed")
            self.send_header("Connection", "close")
            self.end_headers()

            captured = bytearray()
            while True:
                chunk = response.read(32 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                if len(captured) < 16 * 1024 * 1024:
                    captured.extend(chunk[: 16 * 1024 * 1024 - len(captured)])
            self.server.capture.observe_response(session, bytes(captured), content_type)
        except (OSError, http.client.HTTPException) as error:
            if not self.wfile.closed:
                self._json({"error": "upstream_unavailable", "detail": str(error)}, HTTPStatus.BAD_GATEWAY)
        finally:
            connection.close()

    def _static(self, name: str) -> None:
        static_root = Path(__file__).with_name("static").resolve()
        target = (static_root / name).resolve()
        if static_root not in target.parents or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _seed_demo(self) -> None:
        now = int(time.time() * 1000)
        self.server.store.reset(started_at_ms=now - 48 * 3_600_000)
        samples = [
            ("search_accounts", {"domain": "acme.test"}, {"id": 42, "status": "active"}, [2100, 1980, 2240], False),
            ("fetch_pricing", {"plan": "growth", "region": "eu"}, {"currency": "EUR", "price": 149}, [840, 910, 865, 890], False),
            ("read_policy", {"policy": "refunds", "version": "current"}, {"version": 7, "text_hash": "stable"}, [1240, 1180], False),
            ("get_inventory", {"sku": "A-14"}, {"quantity": 18}, [630, 710], False),
            ("get_inventory", {"sku": "B-22"}, {"quantity": 7}, [740, 690], False),
            ("send_email", {"template": "welcome"}, {"sent": True}, [520, 510], True),
            ("get_exchange_rate", {"pair": "GBP-EUR"}, {"rate": 1.18}, [390, 410], False),
        ]
        sequence = 0
        for tool_name, arguments, output, latencies, mutating in samples:
            input_digest = keyed_digest(self.server.config.hash_secret, arguments)
            for index, latency in enumerate(latencies):
                result = output
                if tool_name == "get_exchange_rate" and index == 1:
                    result = {"rate": 1.19}
                self.server.store.add_observation(
                    call_id=f"demo-{sequence}",
                    session_id=f"session-{index + 1}",
                    tool_name=tool_name,
                    input_digest=input_digest,
                    output_digest=keyed_digest(self.server.config.hash_secret, result),
                    latency_ms=latency,
                    observed_at_ms=now - (len(latencies) - index) * 3_600_000,
                    mutating=mutating,
                )
                sequence += 1

    def log_message(self, format: str, *args) -> None:
        print(f"[hoistway] {self.client_address[0]} {format % args}")


def run(config: Config | None = None) -> None:
    active = config or Config.from_env()
    server = AuditServer(active)
    print(f"Hoistway audit proxy listening on http://{active.host}:{active.port}")
    print(f"Forwarding unchanged traffic to {active.upstream_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Hoistway audit")
    finally:
        server.server_close()
