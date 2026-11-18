"""Experiment settings and normalized, disjoint token accounting."""

import math
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

type Strategy = Literal[
    "full",
    "cache",
    "cache_small_model",
    "summary",
    "delegate_same_model",
    "delegate_small_model",
    "status_full_history",
    "status_notes_same_model",
    "status_notes_small_model",
    "status_full_history_small_model",
    "status_notes_template",
    "status_small_notes_small_model",
]
STRATEGIES: tuple[Strategy, ...] = ("full", "cache", "summary")
DELEGATION_STRATEGIES: tuple[Strategy, ...] = ("delegate_same_model", "delegate_small_model")
# Preserve the schema-3 comparison matrix for legacy readers and public reproduction.
STATUS_STRATEGIES: tuple[Strategy, ...] = (
    "status_full_history",
    "status_notes_same_model",
    "status_notes_small_model",
)
ALL_STATUS_STRATEGIES: tuple[Strategy, ...] = (
    *STATUS_STRATEGIES,
    "status_full_history_small_model",
    "status_notes_template",
    "status_small_notes_small_model",
)
SMALL_MODEL_STRATEGIES: tuple[Strategy, ...] = (
    "cache_small_model",
    "delegate_small_model",
    "status_notes_small_model",
    "status_full_history_small_model",
    "status_small_notes_small_model",
)


@dataclass(frozen=True)
class Rates:
    """USD per million tokens; absent prices are deliberately unknown."""

    input: float
    cache_read: float
    cache_write: float
    output: float
    as_of: str

    def __post_init__(self) -> None:
        """Reject negative, non-finite or undocumented prices."""
        values = (self.input, self.cache_read, self.cache_write, self.output)
        if not self.as_of or any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Prices must be finite, nonnegative and dated")


@dataclass(frozen=True)
class Usage:
    """Disjoint counts: ordinary input excludes both reads and writes."""

    ordinary: int
    cache_read: int
    cache_write: int
    output: int

    def __post_init__(self) -> None:
        """Reject malformed provider accounting rather than clamping it."""
        if any(v < 0 for v in (self.ordinary, self.cache_read, self.cache_write, self.output)):
            raise ValueError("Negative or overlapping token usage")

    @property
    def total_input(self) -> int:
        """Return total model input, including cache reads and writes."""
        return self.ordinary + self.cache_read + self.cache_write

    def cost(self, rates: Rates | None) -> float | None:
        """Price this call only when all rates are known."""
        if rates is None:
            return None
        return (
            self.ordinary * rates.input
            + self.cache_read * rates.cache_read
            + self.cache_write * rates.cache_write
            + self.output * rates.output
        ) / 1_000_000


@dataclass(frozen=True)
class Message:
    """Portable text-only conversation item."""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class Reply:
    """Transient response; content is evaluated in memory and never logged."""

    text: str
    usage: Usage
    raw_usage: dict[str, Any]
    model: str
    latency_ms: float
    ttft_text_ms: float | None
    request_bytes: int
    complete: bool = True


@dataclass(frozen=True)
class SubagentConfig:
    """Optional cheaper model on the same provider, with independent prices."""

    model: str
    rates: Rates | None = None

    def __post_init__(self) -> None:
        """Reject an empty worker model before any API call."""
        if not self.model.strip():
            raise ValueError("Choose a nonempty subagent model")


@dataclass(frozen=True)
class Config:
    """One provider, a main model and an optional cheaper worker model."""

    provider: Literal["openai", "anthropic"]
    model: str
    repetitions: int = 3
    summary_after: int = 3
    max_output_tokens: int = 2048
    timeout_seconds: float = 60
    seed: int = 42
    rates: Rates | None = None
    strategies: tuple[Strategy, ...] = field(default=STRATEGIES)
    subagent: SubagentConfig | None = None
    status_updates_per_turn: int = 1
    scenario: Literal["replay", "ambiguous", "corrections", "dense"] = "replay"
    task_schedule: Literal["every_turn", "final_only"] = "every_turn"
    notes_format: Literal["text", "structured"] = "text"
    status_baseline: Strategy = "status_full_history"

    def __post_init__(self) -> None:
        """Fail configuration before any network access."""
        if self.provider not in ("openai", "anthropic") or not self.model.strip():
            raise ValueError("Choose openai or anthropic and a nonempty model")
        if not 1 <= self.repetitions <= 100:
            raise ValueError("repetitions must be between 1 and 100")
        if not 1 <= self.summary_after < 6:
            raise ValueError("summary_after must be between 1 and 5")
        if not 256 <= self.max_output_tokens <= 16384:
            raise ValueError("max_output_tokens must be between 256 and 16384")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if not self.strategies or any(
            s
            not in (
                *STRATEGIES,
                "cache_small_model",
                *DELEGATION_STRATEGIES,
                *ALL_STATUS_STRATEGIES,
            )
            for s in self.strategies
        ):
            raise ValueError("Unknown or empty strategies")
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("Duplicate strategies")
        if self.scenario not in ("replay", "ambiguous", "corrections", "dense"):
            raise ValueError("Unknown scenario")
        if self.task_schedule not in ("every_turn", "final_only"):
            raise ValueError("Unknown task schedule")
        if self.notes_format not in ("text", "structured"):
            raise ValueError("Unknown notes format")
        if self.status_baseline not in ALL_STATUS_STRATEGIES:
            raise ValueError("Choose a status baseline strategy")
        if "status_notes_template" in self.strategies and self.notes_format != "structured":
            raise ValueError("Template rendering requires structured notes")
        if self.task_schedule == "final_only" and "summary" in self.strategies:
            raise ValueError("Summary requires the every_turn task schedule")
        if (
            type(self.status_updates_per_turn) is not int
            or not 1 <= self.status_updates_per_turn <= 20
        ):
            raise ValueError("status_updates_per_turn must be an integer between 1 and 20")
        if any(s in SMALL_MODEL_STRATEGIES for s in self.strategies) and (
            self.subagent is None or self.subagent.model == self.model
        ):
            raise ValueError("The cheaper-model strategy needs a different subagent.model")

    def worker_config(self) -> "Config":
        """Reuse cache/timeout/output settings without inheriting main-model prices."""
        if self.subagent is None:
            raise ValueError("Missing subagent configuration")
        return replace(
            self,
            model=self.subagent.model,
            rates=self.subagent.rates,
            strategies=("cache",),
            subagent=None,
        )

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Read the small TOML contract, rejecting unknown fields."""
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Validate a manifest or TOML configuration without changing its source mapping."""
        data = dict(data)
        if "rates" in data:
            data["rates"] = Rates(**data["rates"]) if data["rates"] is not None else None
        if data.get("subagent") is not None:
            worker = dict(data["subagent"])
            if worker.get("rates") is not None:
                worker["rates"] = Rates(**worker["rates"])
            data["subagent"] = SubagentConfig(**worker)
        if "strategies" in data:
            data["strategies"] = tuple(data["strategies"])
        return cls(**data)


def normalize_usage(provider: str, raw: dict[str, Any]) -> Usage:
    """Normalize current API usage without double-counting cached tokens."""
    if provider == "anthropic":
        return Usage(
            ordinary=int(raw["input_tokens"]),
            cache_read=int(raw.get("cache_read_input_tokens") or 0),
            cache_write=int(raw.get("cache_creation_input_tokens") or 0),
            output=int(raw["output_tokens"]),
        )
    if provider != "openai":
        raise ValueError("Unknown provider")
    details = raw.get("input_tokens_details") or {}
    reads = int(details.get("cached_tokens") or 0)
    # This project targets the explicit-cache API; absence is not a zero write count.
    if "cache_write_tokens" not in details:
        raise ValueError("Model/API did not return explicit-cache write accounting")
    writes = int(details["cache_write_tokens"] or 0)
    return Usage(
        int(raw["input_tokens"]) - reads - writes, reads, writes, int(raw["output_tokens"])
    )
