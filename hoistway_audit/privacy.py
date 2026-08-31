from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any


MUTATION_PATTERN = re.compile(
    r"(^|[._\-/])(create|write|update|delete|remove|send|post|publish|pay|purchase|book|cancel|execute)([._\-/]|$)",
    re.IGNORECASE,
)


def canonical_json(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value.strip()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def keyed_digest(secret: str, value: Any) -> str:
    canonical = canonical_json(value).encode("utf-8", errors="replace")
    return hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()


def byte_size(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8", errors="replace"))


def appears_mutating(tool_name: str) -> bool:
    return bool(MUTATION_PATTERN.search(tool_name))
