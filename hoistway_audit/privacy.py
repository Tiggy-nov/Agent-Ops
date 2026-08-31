from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


MUTATION_PATTERN = re.compile(
    r"(^|[._\-/])(create|write|update|delete|remove|send|post|publish|pay|purchase|book|cancel|execute)([._\-/]|$)",
    re.IGNORECASE,
)

CANONICALIZATION_RULES = {
    "object_keys": "sorted_recursively",
    "string_whitespace": "collapsed",
    "dropped_argument_keys": (
        "request_id",
        "trace_id",
        "nonce",
        "timestamp",
        "ts",
        "idempotency_key",
        "correlation_id",
    ),
    "url_host": "lowercase_and_strip_www",
    "url_fragment": "removed",
    "stripped_url_parameters": ("utm_*", "gclid", "fbclid", "ref"),
    "url_query_parameters": "sorted",
}


def canonical_json(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value.strip()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return value
    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    port = f":{parsed.port}" if parsed.port else ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"gclid", "fbclid", "ref"}
    ]
    query.sort()
    return urlunparse(
        (parsed.scheme.lower(), f"{userinfo}{host}{port}", parsed.path, parsed.params, urlencode(query), "")
    )


def _canonical_argument(value: Any) -> Any:
    if isinstance(value, dict):
        dropped = set(CANONICALIZATION_RULES["dropped_argument_keys"])
        return {
            key: _canonical_argument(item)
            for key, item in sorted(value.items())
            if str(key).lower() not in dropped
        }
    if isinstance(value, list):
        return [_canonical_argument(item) for item in value]
    if isinstance(value, str):
        collapsed = re.sub(r"\s+", " ", value).strip()
        return _canonical_url(collapsed)
    return value


def canonical_arguments(value: Any) -> str:
    candidate = value
    if isinstance(value, str):
        try:
            candidate = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            candidate = value
    return json.dumps(
        _canonical_argument(candidate), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def keyed_digest(secret: str, value: Any) -> str:
    canonical = canonical_json(value).encode("utf-8", errors="replace")
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def argument_digest(secret: str, value: Any) -> str:
    canonical = canonical_arguments(value).encode("utf-8", errors="replace")
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def simhash64(value: Any) -> int:
    """Return a signed 64-bit SimHash over token 3-shingles."""
    tokens = re.findall(r"\w+|[^\w\s]", canonical_json(value).lower(), flags=re.UNICODE)
    shingles = ["\x1f".join(tokens[index:index + 3]) for index in range(max(1, len(tokens) - 2))]
    if not tokens:
        shingles = [""]
    weights = [0] * 64
    for shingle in shingles:
        fingerprint = int.from_bytes(hashlib.sha256(shingle.encode("utf-8")).digest()[:8], "big")
        for bit in range(64):
            weights[bit] += 1 if fingerprint & (1 << bit) else -1
    unsigned = sum((1 << bit) for bit, weight in enumerate(weights) if weight >= 0)
    return unsigned if unsigned < 1 << 63 else unsigned - (1 << 64)


def simhash_similarity(left: int | None, right: int | None) -> float | None:
    if left is None or right is None:
        return None
    distance = ((left & ((1 << 64) - 1)) ^ (right & ((1 << 64) - 1))).bit_count()
    return 1.0 - distance / 64


def _urls(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found.update(_urls(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_urls(item))
    elif isinstance(value, str):
        parsed = urlparse(value)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            found.add(value)
    return found


def url_set_digest(secret: str, value: Any) -> str | None:
    candidate = value
    if isinstance(value, str):
        try:
            candidate = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    urls = sorted(_urls(candidate))
    return keyed_digest(secret, urls) if urls else None


def byte_size(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8", errors="replace"))


def appears_mutating(tool_name: str) -> bool:
    return bool(MUTATION_PATTERN.search(tool_name))
