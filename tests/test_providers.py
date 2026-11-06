import json
from typing import Any, Literal

import httpx
import openai
import pytest

from agent_context_bench.models import Config, Message, Usage
from agent_context_bench.providers import LiveProvider, request_payload


def sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def openai_events(status: str = "completed") -> list[dict[str, Any]]:
    response = {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": "test-model",
        "output": [],
        "usage": {
            "input_tokens": 600,
            "output_tokens": 10,
            "total_tokens": 610,
            "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }
    return [
        {
            "type": "response.output_text.delta",
            "delta": "answer",
            "sequence_number": 0,
            "item_id": "msg_test",
            "output_index": 0,
            "content_index": 0,
        },
        {"type": f"response.{status}", "response": response, "sequence_number": 1},
    ]


def anthropic_events(stop: str = "end_turn") -> list[dict[str, Any]]:
    return [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "test-model",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 300,
                },
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "answer"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": 10},
        },
        {"type": "message_stop"},
    ]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("cache", [False, True])
def test_official_sdk_stream_and_wire_payload(
    provider: Literal["openai", "anthropic"],
    cache: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-credential")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-credential")
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        events = openai_events() if provider == "openai" else anthropic_events()
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse(events)
        )

    client = httpx.Client(transport=httpx.MockTransport(handle))
    adapter = LiveProvider(Config(provider, "test-model"), client)
    try:
        reply = adapter.generate("stable instructions", [Message("user", "question")], cache=cache)
    finally:
        adapter.close()
    assert client.is_closed and len(requests) == 1
    assert reply.text == "answer" and reply.complete
    assert reply.usage == Usage(100, 200, 300, 10)
    assert reply.request_bytes == len(requests[0].content)
    assert reply.ttft_text_ms is not None and reply.latency_ms >= reply.ttft_text_ms >= 0
    body = json.loads(requests[0].content)
    assert body["stream"] is True
    if provider == "openai":
        assert body["store"] is False
        assert body["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
        assert ("prompt_cache_breakpoint" in body["input"][0]["content"][0]) == cache
        assert ("prompt_cache_breakpoint" in json.dumps(body["input"][-1])) == cache
    else:
        assert ("cache_control" in body) == cache
        if cache:
            assert body["cache_control"]["ttl"] == "5m"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_output_limit_retains_usage(
    provider: Literal["openai", "anthropic"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    events = openai_events("incomplete") if provider == "openai" else anthropic_events("max_tokens")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=sse(events)
            )
        )
    )
    adapter = LiveProvider(Config(provider, "test"), client)
    try:
        reply = adapter.generate("system", [Message("user", "q")], cache=False)
        assert not reply.complete and reply.usage.output == 10
    finally:
        adapter.close()


@pytest.mark.parametrize("events", [[], [{"type": "error", "message": "PRIVATE"}]])
def test_broken_stream_does_not_invent_usage(
    events: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=sse(events)
            )
        )
    )
    adapter = LiveProvider(Config("openai", "test"), client)
    try:
        with pytest.raises(RuntimeError):
            adapter.generate("system", [Message("user", "q")], cache=False)
    finally:
        adapter.close()


def test_rate_limit_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "PRIVATE", "type": "rate_limit"}})

    adapter = LiveProvider(
        Config("openai", "test"), httpx.Client(transport=httpx.MockTransport(handle))
    )
    try:
        with pytest.raises(openai.RateLimitError):
            adapter.generate("system", [Message("user", "q")], cache=True)
        assert len(requests) == 1
    finally:
        adapter.close()


@pytest.mark.parametrize("cache", [False, True])
def test_appending_turns_preserves_prior_prefix_and_cache_boundaries(cache: bool) -> None:
    config = Config("openai", "test")
    messages = [Message("user", "first evidence " * 1500)]
    previous = request_payload(config, "stable system", messages, cache)
    original = list(messages)
    for turn in range(1, 6):
        messages.extend([Message("assistant", "ack"), Message("user", f"evidence {turn}")])
        current = request_payload(config, "stable system", messages, cache)
        # Reusing an explicitly cached prefix requires the same lookup boundary
        # on the next request, even after newer messages have been appended.
        assert current["input"][: len(previous["input"])] == previous["input"]
        assert current["extra_body"]["prompt_cache_options"]["mode"] == "explicit"
        assert ("prompt_cache_breakpoint" in json.dumps(current["input"])) == cache
        previous = current
    assert messages[0] == original[0]
    assert messages[-1].content == "evidence 5"


def test_default_http_client_has_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    for name in (
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    adapter = LiveProvider(Config("openai", "test", timeout_seconds=17))
    try:
        assert adapter.client.max_retries == 0 and adapter.client.timeout == 17
    finally:
        adapter.close()
