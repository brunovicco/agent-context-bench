"""Thin official-SDK adapters; streaming, explicit cache control, no hidden retries."""

import json
from time import perf_counter
from typing import Any, Protocol

import anthropic
import httpx
import openai

from agent_context_bench.models import Config, Message, Reply, Usage, normalize_usage


class Provider(Protocol):
    """Only the single operation used by the runner."""

    def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
        """Return text, accounting and measured timing for one request."""
        ...


def request_payload(
    config: Config, system: str, messages: list[Message], cache: bool
) -> dict[str, Any]:
    """Build equivalent text-only requests with short cache lifetimes."""
    if config.provider == "anthropic":
        payload: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_output_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if cache:
            # Extra body keeps support for fields newer than the SDK's typed signatures.
            payload["extra_body"] = {"cache_control": {"type": "ephemeral", "ttl": "5m"}}
        return payload

    prefix: dict[str, Any] = {"type": "input_text", "text": system}
    if cache:
        prefix["prompt_cache_breakpoint"] = {"mode": "explicit"}
    items: list[dict[str, Any]] = [{"role": "developer", "content": [prefix]}]
    for message in messages:
        # Explicit lookups only consider breakpoints in the current request.
        # Retain earlier user boundaries so a growing history can reuse writes.
        if cache and message.role == "user":
            content: Any = [
                {
                    "type": "input_text",
                    "text": message.content,
                    "prompt_cache_breakpoint": {"mode": "explicit"},
                }
            ]
        else:
            content = message.content
        items.append({"role": message.role, "content": content})
    return {
        "model": config.model,
        "input": items,
        "max_output_tokens": config.max_output_tokens,
        "store": False,
        # Explicit mode without breakpoints is the documented no-cache control.
        # Requires a compatible model/API (GPT-5.6+); never silently fall back.
        "extra_body": {"prompt_cache_options": {"mode": "explicit", "ttl": "30m"}},
    }


class LiveProvider:
    """One SDK client; key resolution happens only on an explicit live run."""

    def __init__(self, config: Config, http_client: httpx.Client | None = None) -> None:
        """Set bounded timeouts and disable SDK retries to preserve accounting."""
        self.config = config
        self.request_bytes = 0
        client = http_client or httpx.Client(timeout=config.timeout_seconds)
        client.event_hooks.setdefault("request", []).append(self._measure_request)
        self.client: Any
        if config.provider == "openai":
            self.client = openai.OpenAI(
                http_client=client,
                max_retries=0,
                timeout=config.timeout_seconds,
            )
        else:
            self.client = anthropic.Anthropic(
                http_client=client,
                max_retries=0,
                timeout=config.timeout_seconds,
            )

    def _measure_request(self, request: httpx.Request) -> None:
        """Measure serialized body size without retaining headers or content."""
        self.request_bytes += len(request.content)

    def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
        """Stream until the final usage arrives; never estimate missing usage."""
        payload = request_payload(self.config, system, messages, cache)
        self.request_bytes = 0
        started = perf_counter()
        first_text: float | None = None
        text_parts: list[str] = []
        final: Any = None
        if self.config.provider == "openai":
            with self.client.responses.create(**payload, stream=True) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta" and event.delta:
                        if first_text is None:
                            first_text = (perf_counter() - started) * 1000
                        text_parts.append(event.delta)
                    if event.type in ("response.completed", "response.incomplete"):
                        final = event.response
                    if event.type in ("error", "response.failed"):
                        raise RuntimeError("Provider stream failed")
            if final is None or final.usage is None:
                raise RuntimeError("Missing terminal response usage")
            complete = final.status == "completed"
        else:
            with self.client.messages.stream(**payload) as stream:
                for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        if event.delta.text and first_text is None:
                            first_text = (perf_counter() - started) * 1000
                        text_parts.append(event.delta.text)
                final = stream.get_final_message()
            complete = final.stop_reason == "end_turn"
        elapsed = (perf_counter() - started) * 1000
        raw = final.usage.model_dump(mode="json", exclude_none=True)
        return Reply(
            text="".join(text_parts),
            usage=normalize_usage(self.config.provider, raw),
            raw_usage=raw,
            model=str(final.model),
            latency_ms=elapsed,
            ttft_text_ms=first_text,
            request_bytes=self.request_bytes,
            complete=complete,
        )

    def close(self) -> None:
        """Release the owned SDK HTTP client."""
        self.client.close()


class DemoProvider:
    """Offline plumbing demo, deliberately not a model/cache performance simulation."""

    def generate(self, system: str, messages: list[Message], *, cache: bool) -> Reply:
        """Return a deterministic placeholder; synthetic usage cannot support claims."""
        text = "Synthetic acknowledgement."
        if messages[-1].content.startswith("Summarize"):
            text = (
                "Synthetic summary: retry_storm; E3; approval required; database_latency ruled out."
            )
        elif messages[-1].content.startswith("Final turn"):
            text = json.dumps(
                {
                    "cause": "retry_storm",
                    "action": "rollback_after_approval",
                    "approval_required": True,
                    "discarded_hypothesis": "database_latency",
                    "evidence": ["E3"],
                }
            )
        return Reply(text, Usage(0, 0, 0, 0), {}, "synthetic-demo", 0.0, None, 0)
