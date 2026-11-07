import gzip
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from agent_context_bench.telemetry import Telemetry


def test_disabled_needs_no_backend() -> None:
    telemetry = Telemetry()
    with telemetry.span("task", "session") as trace:
        assert trace is None
    telemetry.close()


def test_otlp_receipt_and_exception_privacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_on")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", raising=False)
    pytest.importorskip("a2a_otel_kit")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    received: list[bytes] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            received.append(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    telemetry = Telemetry(f"http://127.0.0.1:{server.server_port}/v1/traces")
    try:
        with telemetry.span("task", "run-safe") as trace:
            assert trace is not None and len(trace) == 32
        with (
            pytest.raises(ValueError, match="PRIVATE"),
            telemetry.span("delegate", "run-safe", "subagent"),
        ):
            raise ValueError("PRIVATE_PROMPT_AND_CREDENTIAL")
        telemetry.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert received
    request = ExportTraceServiceRequest()
    request.ParseFromString(received[0])
    spans = [
        s
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for s in scope.spans
    ]
    assert len(spans) == 2
    assert {s.name for s in spans} == {"context_bench.call"}
    assert not any(s.events for s in spans)
    assert all(b"PRIVATE" not in payload for payload in received)
    assert any(
        a.key == "outcome" and a.value.string_value == "error" for s in spans for a in s.attributes
    )

    assert any(
        a.key == "component" and a.value.string_value == "subagent"
        for s in spans
        for a in s.attributes
    )
