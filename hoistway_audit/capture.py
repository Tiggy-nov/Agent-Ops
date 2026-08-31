from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

from .privacy import appears_mutating, byte_size, keyed_digest
from .storage import PendingCall, Store


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class ToolOutput:
    call_id: str
    output: Any


def session_id(headers: dict[str, str], payload: dict[str, Any]) -> str:
    explicit = headers.get("x-hoistway-session-id") or headers.get("x-session-id")
    if explicit:
        return explicit[:200]
    metadata = payload.get("metadata") or {}
    if isinstance(metadata, dict):
        candidate = metadata.get("session_id") or metadata.get("thread_id")
        if candidate:
            return str(candidate)[:200]
    if payload.get("user"):
        return str(payload["user"])[:200]
    return "unscoped"


def request_outputs(payload: dict[str, Any]) -> list[ToolOutput]:
    outputs: list[ToolOutput] = []
    messages = payload.get("messages") or []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            call_id = message.get("tool_call_id")
            if call_id:
                outputs.append(ToolOutput(str(call_id), message.get("content")))

    inputs = payload.get("input") or []
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict) or item.get("type") != "function_call_output":
                continue
            call_id = item.get("call_id")
            if call_id:
                outputs.append(ToolOutput(str(call_id), item.get("output")))
    return outputs


def response_calls(payload: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    choices = payload.get("choices") or []
    for choice in choices if isinstance(choices, list) else []:
        message = choice.get("message") or {}
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            if item.get("id") and function.get("name"):
                calls.append(ToolCall(str(item["id"]), str(function["name"]), function.get("arguments", "")))

    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") not in {"function_call", "tool_call"}:
            continue
        call_id = item.get("call_id") or item.get("id")
        if call_id and item.get("name"):
            calls.append(ToolCall(str(call_id), str(item["name"]), item.get("arguments", "")))
    return calls


def sse_calls(body: bytes) -> list[ToolCall]:
    complete: list[ToolCall] = []
    chat_parts: dict[int, dict[str, str]] = {}
    for raw_line in body.decode("utf-8", errors="replace").splitlines():
        if not raw_line.startswith("data:"):
            continue
        data = raw_line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue

        if event.get("type") in {"response.output_item.done", "response.function_call_arguments.done"}:
            item = event.get("item") or event
            call_id = item.get("call_id") or item.get("item_id")
            name = item.get("name")
            arguments = item.get("arguments", "")
            if call_id and name:
                complete.append(ToolCall(str(call_id), str(name), arguments))

        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            for part in delta.get("tool_calls") or []:
                index = int(part.get("index", 0))
                current = chat_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                current["id"] += str(part.get("id") or "")
                function = part.get("function") or {}
                current["name"] += str(function.get("name") or "")
                current["arguments"] += str(function.get("arguments") or "")

    complete.extend(
        ToolCall(part["id"], part["name"], part["arguments"])
        for part in chat_parts.values()
        if part["id"] and part["name"]
    )
    return complete


class Capture:
    def __init__(self, store: Store, hash_secret: str):
        self.store = store
        self.hash_secret = hash_secret

    def observe_request(self, headers: dict[str, str], body: bytes, now_ms: int | None = None) -> str:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return headers.get("x-hoistway-session-id", "unscoped")
        sid = session_id(headers, payload)
        completed_at = now_ms or int(time.time() * 1000)
        for output in request_outputs(payload):
            self.store.complete_call(
                output.call_id,
                sid,
                keyed_digest(self.hash_secret, output.output),
                byte_size(output.output),
                completed_at,
            )
        return sid

    def observe_response(
        self,
        session: str,
        body: bytes,
        content_type: str,
        now_ms: int | None = None,
    ) -> int:
        calls: Iterable[ToolCall]
        if "text/event-stream" in content_type:
            calls = sse_calls(body)
        else:
            try:
                calls = response_calls(json.loads(body))
            except (json.JSONDecodeError, UnicodeDecodeError):
                calls = []
        issued_at = now_ms or int(time.time() * 1000)
        pending = [
            PendingCall(
                call_id=call.call_id,
                session_id=session,
                tool_name=call.name,
                input_digest=keyed_digest(self.hash_secret, call.arguments),
                input_bytes=byte_size(call.arguments),
                issued_at_ms=issued_at,
                mutating=appears_mutating(call.name),
            )
            for call in calls
        ]
        self.store.save_pending(pending)
        return len(pending)
