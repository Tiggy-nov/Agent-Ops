# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Engineering teams operating production AI agents. The primary operator is a developer or infrastructure engineer evaluating whether shared tool-result reuse is justified by observed fleet behaviour.

## Product Purpose

Hoistway is a local, read-only 48-hour redundancy audit. It sits in front of model API traffic and measures how often normalised tool work repeats across agent sessions, how stable the corresponding outputs remain over time, and whether the evidence clears pre-registered adoption thresholds.

## Positioning

The product is a measurement instrument, not a cache or agent runtime. It produces falsifiable evidence before a team changes agent execution.

## Operating Context

Teams point OpenAI, Anthropic or Gemini model traffic through the proxy and provide an explicit session identifier. The audit runs locally, stores fixed-width digests and comparison signals in SQLite, and presents a local report after the observation period.

## Capabilities and Constraints

- Traffic is observed and forwarded without caching, routing, retries or rewriting.
- Raw prompts, tool arguments, tool results and provider credentials are not persisted.
- The report separates cross-session, within-session and total repetition.
- Tool-boundary timing is secondary context. It includes local orchestration overhead and is not a latency-savings claim.
- Output stability is reported across fixed reuse windows using exact, SimHash and URL-set signals.
- Calls without an accepted session identity are excluded and counted.
- Three PASS or KILL thresholds are fixed in source before the audit runs.

## Brand Commitments

The product name is Hoistway. The established voice is direct, technical and conservative about what the evidence proves. UK English is used in visible copy.

## Evidence on Hand

- The running audit implementation and report schema in `hoistway_audit/`.
- Unit, integration and 50-connection byte-identity tests in `tests/`.
- Synthetic demonstration data is available through `/audit/demo/seed` and must not be presented as customer evidence.
- No customer logos, testimonials or production benchmarks are available and none should be fabricated.

## Product Principles

- Measure before changing execution.
- Lead with cross-session evidence because it is the shared-infrastructure signal.
- Make uncertainty and architecture limits visible.
- Keep the security boundary read-only and local.
- Allow the audit to return KILL.

## Accessibility & Inclusion

The report must remain keyboard-accessible, readable at narrow widths and usable with reduced motion.
