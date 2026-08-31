# Hoistway

## A free 48-hour redundancy audit for agent fleets

Hoistway is a read-only reverse proxy that a team places in front of its model API traffic for two days. It modifies nothing and stores no raw prompts, tool arguments or tool results by default.

At the end of the audit, it answers one question:

> How much observed end-to-end tool latency could a cross-session tool-result cache remove without changing what our agents do?

This repository is the measurement instrument. It is not a cache and it does not alter agent execution.

## What it measures

When an agent model emits a tool call, Hoistway records:

- A keyed digest of the normalised tool name and arguments
- The agent session identifier
- When the tool call was emitted

When the tool output is sent back to the model, Hoistway records:

- A keyed digest of the output
- The observed tool round-trip latency
- Payload sizes, not payload contents

The report only classifies a repeated call as eligible when:

1. The same normalised call appears in at least two different sessions.
2. Every observed output digest matches.
3. The tool name does not appear to represent a mutation.

This deliberately under-counts rather than over-claiming.

## Quick start

Python 3.11 or newer is the only runtime requirement.

```sh
python -m hoistway_audit
```

By default:

- Proxy: `http://localhost:8787`
- Report: `http://localhost:8787/report`
- Upstream: `https://api.openai.com`
- Audit database: `./data/audit.db`

Point an OpenAI-compatible client at the proxy and attach a stable session identifier:

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-provider-key",
    base_url="http://localhost:8787/v1",
    default_headers={"X-Hoistway-Session-ID": "agent-session-123"},
)
```

Hoistway forwards the existing `Authorization` header to the upstream provider. It does not need a separate provider key.

## Run the demonstration report

Start the proxy, then load a synthetic 48-hour audit:

```sh
curl -X POST http://localhost:8787/audit/demo/seed
```

Refresh `http://localhost:8787/report`.

The demonstration includes:

- Stable cross-session reads that qualify
- A mutating tool that is excluded
- A repeated call whose output changed and is excluded

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `HOISTWAY_UPSTREAM_URL` | `https://api.openai.com` | Provider or gateway receiving unchanged traffic |
| `HOISTWAY_HOST` | `127.0.0.1` | Bind address |
| `HOISTWAY_PORT` | `8787` | Bind port |
| `HOISTWAY_DATA_DIR` | `./data` | Local audit database directory |
| `HOISTWAY_AUDIT_HOURS` | `48` | Observation window |
| `HOISTWAY_HASH_SECRET` | Random per process | HMAC secret used for payload digests |
| `HOISTWAY_DASHBOARD` | `true` | Serve the local report UI |

For a real 48-hour audit, set and preserve `HOISTWAY_HASH_SECRET`. A changing secret makes calls from separate proxy restarts incomparable.

## Docker

```sh
docker build -t hoistway-audit .
docker run --rm \
  -p 8787:8787 \
  -v "$(pwd)/data:/app/data" \
  -e HOISTWAY_HOST=0.0.0.0 \
  -e HOISTWAY_HASH_SECRET="replace-with-a-long-random-secret" \
  hoistway-audit
```

## API

- `GET /healthz` returns proxy health and confirms read-only mode.
- `GET /audit/status` returns audit progress.
- `GET /audit/report` returns the complete machine-readable report.
- `POST /audit/demo/seed` replaces local observations with synthetic demonstration data.

All other routes and methods are forwarded to `HOISTWAY_UPSTREAM_URL`.

## Supported capture formats

The first release recognises OpenAI-compatible:

- Chat Completions tool calls and `role: tool` outputs
- Responses API `function_call` and `function_call_output` items
- JSON responses
- Server-sent event streams

The proxy forwards other traffic unchanged even when it cannot classify it.

## Privacy and operating boundary

- No raw tool arguments or outputs are persisted.
- No prompt or model output is persisted.
- Provider credentials are forwarded in memory and never written to the audit database.
- Traffic is not rewritten, routed, cached, retried or reordered.
- Mutation-like tool names are excluded from the eligible result.
- Output equality is required for eligibility.
- Cross-session repetition is required for eligibility.

The automatic mutation classifier is a safety filter, not a formal proof of side-effect freedom. Any production recommendation still requires a human review of tool semantics and freshness requirements.

## Known MVP limits

- Session identity must be supplied using `X-Hoistway-Session-ID`, `metadata.session_id`, `metadata.thread_id` or `user`.
- Tool latency is measured from model emission until the tool result re-enters the model boundary. It includes local orchestration overhead around the tool.
- The counterfactual does not yet model cache lookup overhead, TTL expiry or partial equivalence.
- Exact argument and output equality is intentionally stricter than semantic equivalence.
- Streaming is relayed progressively, but the proxy currently uses a simple standard-library networking stack rather than a production edge proxy.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Why this wedge

The report creates a falsifiable adoption conversation. If cross-session redundancy is negligible, the customer learns that quickly and Hoistway should not sell them a cache. If it is material, the audit produces the evidence needed to define the next product boundary.
