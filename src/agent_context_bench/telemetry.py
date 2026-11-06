"""Optional a2a-otel-kit lifecycle; token accounting stays in local result files."""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any


class Telemetry:
    """No backend or optional package needed when disabled."""

    def __init__(self, endpoint: str | None = None) -> None:
        """Configure the kit only when an OTLP endpoint was requested."""
        self.observability: Any = None
        if endpoint:
            from a2a_otel_kit import Observability, ObservabilitySettings

            self.observability = Observability.configure(
                ObservabilitySettings(
                    service_name="agent-context-bench",
                    service_version="0.1.0",
                    environment="benchmark",
                    enabled=True,
                    otlp_endpoint=endpoint,
                )
            )

    @contextmanager
    def span(self, operation: str, run_id: str) -> Iterator[str | None]:
        """Emit fixed metadata, omitting exception descriptions and payloads."""
        if self.observability is None:
            yield None
            return
        with self.observability.start_span(
            "context_bench.call",
            record_exception=False,
            attributes={"operation": operation, "correlation_id": run_id},
        ) as span:
            started = perf_counter()
            try:
                yield f"{span.get_span_context().trace_id:032x}"
            except Exception:
                span.set_attribute("outcome", "error")
                raise
            else:
                span.set_attribute("outcome", "success")
            finally:
                span.set_attribute("duration_ms", (perf_counter() - started) * 1000)

    def close(self) -> None:
        """Flush and shut down even when a benchmark failed."""
        if self.observability is not None:
            self.observability.shutdown()
