from __future__ import annotations

import json
import time
import codecs
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .privacy import argument_digest, appears_mutating, byte_size, keyed_digest, simhash64, url_set_digest
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


def _gemini_id(name: str, occurrence: int, explicit: Any = None) -> str:
    return str(explicit) if explicit else f"gemini:{name}:{occurrence}"


def session_id(headers: dict[str, str], payload: dict[str, Any]) -> str:
    explicit = headers.get("x-hoistway-session-id")
    if explicit:
        return explicit[:200]
    metadata = payload.get("metadata") or {}
    if isinstance(metadata, dict):
        candidate = metadata.get("session_id") or metadata.get("thread_id")
        if candidate:
            return str(candidate)[:200]
    return "unscoped"


def request_outputs(payload: dict[str, Any]) -> list[ToolOutput]:
    outputs: list[ToolOutput] = []
    messages = payload.get("messages") or []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if call_id:
                    outputs.append(ToolOutput(str(call_id), message.get("content")))
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                        outputs.append(ToolOutput(str(block["tool_use_id"]), block.get("content")))

    inputs = payload.get("input") or []
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict) or item.get("type") != "function_call_output":
                continue
            call_id = item.get("call_id")
            if call_id:
                outputs.append(ToolOutput(str(call_id), item.get("output")))

    occurrences: defaultdict[str, int] = defaultdict(int)
    for content in payload.get("contents") or []:
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if not isinstance(part, dict) or not isinstance(part.get("functionResponse"), dict):
                continue
            response = part["functionResponse"]
            name = str(response.get("name") or "unknown")
            occurrence = occurrences[name]
            occurrences[name] += 1
            outputs.append(
                ToolOutput(
                    _gemini_id(name, occurrence, response.get("id")),
                    response.get("response"),
                )
            )
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

    for block in payload.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("id") and block.get("name"):
            calls.append(ToolCall(str(block["id"]), str(block["name"]), block.get("input", {})))

    occurrences: defaultdict[str, int] = defaultdict(int)
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict) or not isinstance(part.get("functionCall"), dict):
                continue
            function = part["functionCall"]
            name = str(function.get("name") or "")
            if not name:
                continue
            occurrence = occurrences[name]
            occurrences[name] += 1
            calls.append(
                ToolCall(
                    _gemini_id(name, occurrence, function.get("id")),
                    name,
                    function.get("args", {}),
                )
            )
    return calls


class SSECallParser:
    def __init__(self) -> None:
        self.complete: list[ToolCall] = []
        self.chat_parts: dict[int, dict[str, str]] = {}
        self.anthropic_parts: dict[int, dict[str, Any]] = {}
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._text = ""

    def feed(self, chunk: bytes) -> None:
        self._text += self._decoder.decode(chunk)
        lines = self._text.split("\n")
        self._text = lines.pop()
        for raw_line in lines:
            self._line(raw_line.rstrip("\r"))

    def _line(self, raw_line: str) -> None:
        if not raw_line.startswith("data:"):
            return
        data = raw_line[5:].strip()
        if not data or data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return

        self.complete.extend(response_calls(event))

        if event.get("type") == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use" and block.get("id") and block.get("name"):
                self.anthropic_parts[int(event.get("index", 0))] = {
                    "id": str(block["id"]),
                    "name": str(block["name"]),
                    "input": block.get("input", {}),
                    "partial": "",
                }
        elif event.get("type") == "content_block_delta":
            delta = event.get("delta") or {}
            index = int(event.get("index", 0))
            if index in self.anthropic_parts and delta.get("type") == "input_json_delta":
                self.anthropic_parts[index]["partial"] += str(delta.get("partial_json") or "")

        if event.get("type") in {"response.output_item.done", "response.function_call_arguments.done"}:
            item = event.get("item") or event
            call_id = item.get("call_id") or item.get("item_id")
            name = item.get("name")
            arguments = item.get("arguments", "")
            if call_id and name:
                self.complete.append(ToolCall(str(call_id), str(name), arguments))

        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            for part in delta.get("tool_calls") or []:
                index = int(part.get("index", 0))
                current = self.chat_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                current["id"] += str(part.get("id") or "")
                function = part.get("function") or {}
                current["name"] += str(function.get("name") or "")
                current["arguments"] += str(function.get("arguments") or "")

    def finish(self) -> list[ToolCall]:
        self._text += self._decoder.decode(b"", final=True)
        if self._text:
            self._line(self._text.rstrip("\r"))
            self._text = ""
        complete = list(self.complete)
        complete.extend(
            ToolCall(part["id"], part["name"], part["arguments"])
            for part in self.chat_parts.values()
            if part["id"] and part["name"]
        )
        complete.extend(
            ToolCall(part["id"], part["name"], part["partial"] or part["input"])
            for part in self.anthropic_parts.values()
        )
        deduplicated: dict[str, ToolCall] = {}
        for call in complete:
            deduplicated[call.call_id] = call
        return list(deduplicated.values())


def sse_calls(body: bytes) -> list[ToolCall]:
    parser = SSECallParser()
    parser.feed(body)
    return parser.finish()


class ResponseStreamObserver:
    def __init__(self, capture: "Capture", session: str, content_type: str) -> None:
        self.capture = capture
        self.session = session
        self.is_sse = "text/event-stream" in content_type
        self.sse = SSECallParser() if self.is_sse else None
        self.json_body = bytearray()

    def feed(self, chunk: bytes) -> None:
        if self.sse:
            self.sse.feed(chunk)
        else:
            self.json_body.extend(chunk)

    def finish(self) -> int:
        if self.sse:
            calls = self.sse.finish()
        else:
            try:
                calls = response_calls(json.loads(self.json_body))
            except (json.JSONDecodeError, UnicodeDecodeError):
                calls = []
        return self.capture.observe_calls(self.session, calls)


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
            if sid == "unscoped":
                continue
            self.store.complete_call(
                keyed_digest(self.hash_secret, {"session": sid, "call_id": output.call_id})[:32],
                sid,
                keyed_digest(self.hash_secret, output.output),
                simhash64(output.output),
                url_set_digest(self.hash_secret, output.output),
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
        return self.observe_calls(session, calls, now_ms)

    def stream_observer(self, session: str, content_type: str) -> ResponseStreamObserver:
        return ResponseStreamObserver(self, session, content_type)

    def observe_calls(
        self,
        session: str,
        calls: Iterable[ToolCall],
        now_ms: int | None = None,
    ) -> int:
        issued_at = now_ms or int(time.time() * 1000)
        call_list = list(calls)
        if session == "unscoped":
            self.store.increment_dropped_missing_session(len(call_list))
            return 0
        batch_size = len(call_list)
        batch_id = keyed_digest(
            self.hash_secret,
            {"session": session, "issued_at_ms": issued_at, "call_ids": sorted(call.call_id for call in call_list)},
        )[:24]
        pending = [
            PendingCall(
                call_id=keyed_digest(self.hash_secret, {"session": session, "call_id": call.call_id})[:32],
                batch_id=batch_id,
                batch_size=batch_size,
                session_id=session,
                tool_name=call.name,
                input_digest=argument_digest(self.hash_secret, call.arguments),
                input_bytes=byte_size(call.arguments),
                issued_at_ms=issued_at,
                mutating=appears_mutating(call.name),
            )
            for call in call_list
        ]
        self.store.save_pending(pending)
        return len(pending)
