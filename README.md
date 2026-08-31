# Hoistway

## A free 48-hour redundancy audit for agent fleets

Hoistway is a read-only reverse proxy that a team places in front of its model API traffic for two days. It modifies nothing and stores no raw prompts, tool arguments or tool results by default.

At the end of the audit, it answers one question:

> How much of this fleet's tool work is repeated across sessions with identical results?

This repository is the measurement instrument. It is not a cache and it does not alter agent execution.

## What it measures

When an agent model emits a tool call, Hoistway records:

- A keyed digest of the normalised tool name and arguments
- The agent session identifier
- When the tool call was emitted

Arguments are canonicalised before digesting: object keys are recursively sorted, string whitespace is collapsed, declared nonce keys are removed, and URLs are normalised by host, fragment and query parameters. The exact dropped keys and stripped tracking parameters are constants in source and are printed in every report so an operator can audit the equivalence assumptions.

When the tool output is sent back to the model, Hoistway records:

- A keyed digest of the output
- The observed tool-boundary interval
- Payload sizes, not payload contents

The report stores one fixed-width row for every observed repeat and builds a stability curve over 30-second, 1-minute, 5-minute, 10-minute, 1-hour, 6-hour and 24-hour gaps. Each tool receives an observed safe reuse window: the largest measured window where at least 95% of repeat outputs were near-identical.

Before discarding each output payload, Hoistway records a 64-bit SimHash over token 3-shingles. When JSON output contains URLs, it also records a keyed digest of the sorted URL set. The stability table reports exact and near-identical rates separately; no raw output is retained.

A repeated call is eligible when:

1. The same normalised call appears in at least two different sessions.
2. Its output matches the preceding result inside that tool's observed safe reuse window.
3. The tool name does not appear to represent a mutation.

This preserves the time dimension: a result can be stable over ten minutes without being assumed stable for six hours. The tool-boundary interval runs from model emission to result re-entry. It includes local framework and orchestration overhead, so it is reported only as secondary context and is not a claim about end-to-end latency saved.

For that secondary timing context, Hoistway records how many tool calls appeared in each assistant turn and reports a range, never a point estimate:

- Lower bound: a turn is counted only when every call in its batch is eligible.
- Upper bound: eligible call intervals are summed as if tool execution were fully serial.

The report also shows the batch-size distribution and the share of eligible calls in multi-call batches. A wide range means framework timing metadata or tool-side capture is needed to resolve concurrency.

## Pre-registered decision thresholds

The pass/fail bar is fixed in source before any audit data arrives. Every report ends with PASS or KILL for each criterion:

| Criterion | Threshold |
|---|---:|
| Cross-session share of eligible repeated calls | at least 40% |
| Output stability at a 10-minute reuse window | at least 95% |
| Eligible calls as a share of all tool calls | at least 10% |

## Quick start

Install the package on Python 3.11 or newer:

```sh
python -m pip install -e .
hoistway-audit
```

By default:

- Proxy: `http://localhost:8787`
- Report: `http://localhost:8787/report`
- Upstream: `https://api.openai.com`
- Audit database: `./data/audit.db`

Point a model client at the proxy and attach a stable session identifier. For example, with an OpenAI-compatible client:

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

- OpenAI Chat Completions and Responses APIs
- Anthropic Messages API `tool_use` and `tool_result` blocks
- Gemini `functionCall` and `functionResponse` parts
- JSON responses and server-sent event streams for all three formats

The proxy forwards other traffic unchanged even when it cannot classify it.

## Privacy and operating boundary

- No raw tool arguments or outputs are persisted.
- No prompt or model output is persisted.
- Provider credentials are forwarded in memory and never written to the audit database.
- Traffic is not rewritten, routed, cached, retried or reordered.
- Mutation-like tool names are excluded from the eligible result.
- A tool-specific stability window and near-identical output evidence are required for eligibility; exact and near-identical rates remain separate in the report.
- Cross-session repetition is required for eligibility.

The automatic mutation classifier is a safety filter, not a formal proof of side-effect freedom. Any production recommendation still requires a human review of tool semantics and freshness requirements.

## Known MVP limits

- Session identity must be supplied using `X-Hoistway-Session-ID`, `metadata.session_id` or `metadata.thread_id`. User identifiers are intentionally rejected because one user can span many sessions.
- Calls without usable session identity are excluded and counted in report coverage.
- `observed_tool_boundary_ms` spans model emission until the tool result re-enters the model boundary. It includes local orchestration overhead and does not identify actual tool execution time.
- The counterfactual does not yet model cache lookup overhead, TTL expiry or partial equivalence.
- Exact argument and output equality is intentionally stricter than semantic equivalence.
- SSE is parsed incrementally while `httpx`, Starlette and Uvicorn relay upstream bytes asynchronously. The capture path does not buffer a streamed response.

## Tests

```sh
python -m unittest discover -s tests -v
```

## Why this wedge

The report creates a falsifiable adoption conversation. If cross-session redundancy is negligible, the customer learns that quickly and Hoistway should not sell them a cache. If it is material, the audit produces the evidence needed to define the next product boundary.
