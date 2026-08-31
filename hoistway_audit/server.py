from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .capture import Capture
from .config import Config
from .privacy import argument_digest, keyed_digest, simhash64, url_set_digest
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


def _seed_demo(store: Store, config: Config) -> None:
    now = int(time.time() * 1000)
    store.reset(started_at_ms=now - 48 * 3_600_000)
    samples = [
        ("search_accounts", {"domain": "acme.test"}, {"id": 42, "status": "active"}, [2_100, 1_980, 2_240], False),
        ("fetch_pricing", {"plan": "growth", "region": "eu"}, {"currency": "EUR", "price": 149}, [840, 910, 865, 890], False),
        ("read_policy", {"policy": "refunds", "version": "current"}, {"version": 7, "text_hash": "stable"}, [1_240, 1_180], False),
        ("get_inventory", {"sku": "A-14"}, {"quantity": 18}, [630, 710], False),
        ("get_inventory", {"sku": "B-22"}, {"quantity": 7}, [740, 690], False),
        ("send_email", {"template": "welcome"}, {"sent": True}, [520, 510], True),
        ("get_exchange_rate", {"pair": "GBP-EUR"}, {"rate": 1.18}, [390, 410], False),
    ]
    sequence = 0
    for tool_name, arguments, output, intervals, mutating in samples:
        input_digest = argument_digest(config.hash_secret, arguments)
        for index, interval in enumerate(intervals):
            result = {"rate": 1.19} if tool_name == "get_exchange_rate" and index == 1 else output
            store.add_observation(
                call_id=f"demo-{sequence}",
                session_id=f"session-{index + 1}",
                tool_name=tool_name,
                input_digest=input_digest,
                output_digest=keyed_digest(config.hash_secret, result),
                output_simhash=simhash64(result),
                output_url_set_digest=url_set_digest(config.hash_secret, result),
                latency_ms=interval,
                observed_at_ms=now - (len(intervals) - index) * 3_600_000,
                mutating=mutating,
            )
            sequence += 1


def create_app(config: Config) -> Starlette:
    store = Store(config.database_path)
    capture = Capture(store, config.hash_secret)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=0),
        )
        yield
        await app.state.client.aclose()

    async def report_page(_: Request) -> Response:
        if not config.dashboard_enabled:
            return Response(status_code=404)
        return FileResponse(Path(__file__).with_name("static") / "index.html")

    async def asset(request: Request) -> Response:
        if not config.dashboard_enabled:
            return Response(status_code=404)
        static_root = Path(__file__).with_name("static").resolve()
        target = (static_root / request.path_params["name"]).resolve()
        if static_root not in target.parents or not target.is_file():
            return Response(status_code=404)
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    async def audit_status(_: Request) -> Response:
        return JSONResponse(build_report(store, config.audit_hours)["audit"])

    async def audit_report(_: Request) -> Response:
        return JSONResponse(build_report(store, config.audit_hours))

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "mode": "read_only"})

    async def seed(_: Request) -> Response:
        _seed_demo(store, config)
        return JSONResponse(build_report(store, config.audit_hours))

    async def proxy(request: Request) -> Response:
        body = await request.body()
        header_map = {key.lower(): value for key, value in request.headers.items()}
        session = capture.observe_request(header_map, body)
        target_url = f"{config.upstream_url}{request.url.path}"
        if request.url.query:
            target_url += f"?{request.url.query}"
        forwarded_headers = [
            (key.decode("latin-1"), value.decode("latin-1"))
            for key, value in request.headers.raw
            if key.decode("latin-1").lower() not in HOP_BY_HOP | {"host", "content-length"}
            and not key.decode("latin-1").lower().startswith("x-hoistway-")
        ]
        upstream_request = request.app.state.client.build_request(
            request.method,
            target_url,
            headers=forwarded_headers,
            content=body,
        )
        try:
            upstream = await request.app.state.client.send(upstream_request, stream=True)
        except httpx.HTTPError as error:
            return JSONResponse(
                {"error": "upstream_unavailable", "detail": str(error)},
                status_code=502,
            )
        content_type = upstream.headers.get("content-type", "application/octet-stream")
        observer = capture.stream_observer(session, content_type)

        async def relay():
            try:
                async for chunk in upstream.aiter_raw():
                    observer.feed(chunk)
                    yield chunk
            finally:
                observer.finish()
                await upstream.aclose()

        response_headers = {
            key: value
            for key, value in upstream.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    routes = [
        Route("/", report_page, methods=["GET"]),
        Route("/report", report_page, methods=["GET"]),
        Route("/assets/{name:path}", asset, methods=["GET"]),
        Route("/audit/status", audit_status, methods=["GET"]),
        Route("/audit/report", audit_report, methods=["GET"]),
        Route("/audit/demo/seed", seed, methods=["POST"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.store = store
    app.state.capture = capture
    app.state.config = config
    return app


def run(config: Config | None = None) -> None:
    active = config or Config.from_env()
    print(f"Hoistway audit proxy listening on http://{active.host}:{active.port}")
    print(f"Forwarding unchanged traffic to {active.upstream_url}")
    uvicorn.run(create_app(active), host=active.host, port=active.port, log_level="info")
