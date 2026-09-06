"""Privacy-safe Paseo data models."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse


class PaseoError(Exception):
    """Base exception for Paseo client failures."""


class PaseoAuthenticationError(PaseoError):
    """Raised when Paseo rejects authentication."""


class PaseoConnectionError(PaseoError):
    """Raised when Paseo cannot be reached."""


@dataclass(frozen=True, slots=True)
class PaseoAgentUsage:
    """Privacy-safe numeric usage snapshot for one agent."""

    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    total_cost_usd: float | None
    context_window_max_tokens: int | None
    context_window_used_tokens: int | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PaseoAgentUsage:
        """Create a usage snapshot from Paseo's numeric wire fields."""
        return cls(
            input_tokens=_optional_token_count(payload.get("inputTokens")),
            cached_input_tokens=_optional_token_count(payload.get("cachedInputTokens")),
            output_tokens=_optional_token_count(payload.get("outputTokens")),
            total_cost_usd=_optional_number(payload.get("totalCostUsd")),
            context_window_max_tokens=_optional_token_count(
                payload.get("contextWindowMaxTokens")
            ),
            context_window_used_tokens=_optional_token_count(
                payload.get("contextWindowUsedTokens")
            ),
        )


@dataclass(frozen=True, slots=True)
class PaseoAgent:
    """Safe subset of an agent record."""

    agent_id: str
    provider_id: str
    status: str
    requires_attention: bool
    attention_reason: str | None
    attention_timestamp: str | None
    last_usage: PaseoAgentUsage | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PaseoAgent:
        """Create an agent while discarding all potentially sensitive fields."""
        raw_usage = payload.get("lastUsage")
        return cls(
            agent_id=str(payload.get("id", "")),
            provider_id=_optional_string(payload.get("provider") or payload.get("providerId"))
            or "unknown",
            status=_optional_string(payload.get("status")) or "unknown",
            requires_attention=bool(payload.get("requiresAttention", False)),
            attention_reason=_optional_string(payload.get("attentionReason")),
            attention_timestamp=_optional_string(payload.get("attentionTimestamp")),
            last_usage=(
                PaseoAgentUsage.from_payload(raw_usage) if isinstance(raw_usage, dict) else None
            ),
        )


@dataclass(frozen=True, slots=True)
class PaseoUsageWindow:
    """One provider quota window."""

    window_id: str
    label: str
    used_percent: float | None
    remaining_percent: float | None
    resets_at: str | None
    runs_out_at: str | None
    shortfall_percent: float | None
    tone: str | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PaseoUsageWindow:
        """Create a quota window from the Paseo response."""
        used = _optional_number(payload.get("usedPct"))
        remaining = _optional_number(payload.get("remainingPct"))
        if remaining is None and used is not None:
            remaining = max(0.0, min(100.0, 100.0 - used))
        return cls(
            window_id=str(payload.get("id", "unknown")),
            label=_optional_string(payload.get("label")) or "Quota",
            used_percent=used,
            remaining_percent=remaining,
            resets_at=_optional_string(payload.get("resetsAt")),
            runs_out_at=_optional_string(payload.get("runsOutAt")),
            shortfall_percent=_optional_number(payload.get("shortfallPct")),
            tone=_optional_string(payload.get("tone")),
        )


@dataclass(frozen=True, slots=True)
class PaseoProviderUsage:
    """Usage information for a provider."""

    provider_id: str
    display_name: str
    status: str | None
    plan: str | None
    fetched_at: str | None
    windows: tuple[PaseoUsageWindow, ...]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PaseoProviderUsage:
        """Create provider usage from the Paseo response."""
        windows = tuple(
            PaseoUsageWindow.from_payload(window)
            for window in payload.get("windows", [])
            if isinstance(window, dict)
        )
        return cls(
            provider_id=str(payload.get("providerId", "unknown")),
            display_name=_optional_string(payload.get("displayName")) or "Unknown",
            status=_optional_string(payload.get("status")),
            plan=_optional_string(payload.get("planLabel")),
            fetched_at=_optional_string(payload.get("fetchedAt")),
            windows=windows,
        )


@dataclass(frozen=True, slots=True)
class PaseoActivity:
    """A privacy-safe activity event."""

    sequence: int
    event_type: str
    provider_id: str
    agent_id: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class PaseoTokenMetrics:
    """Aggregated usage snapshots for a fleet or provider."""

    context_tokens: int | None
    context_capacity: int | None
    context_utilization: float | None
    latest_input_tokens: int | None
    latest_cached_input_tokens: int | None
    latest_output_tokens: int | None
    reported_session_cost: float | None


@dataclass(frozen=True, slots=True)
class PaseoSnapshot:
    """Current safe state of the Paseo fleet."""

    connected: bool = False
    server_id: str = "unknown"
    hostname: str = "Paseo"
    version: str | None = None
    agents: dict[str, PaseoAgent] = field(default_factory=dict)
    usage: dict[str, PaseoProviderUsage] = field(default_factory=dict)
    activity: PaseoActivity | None = None

    @property
    def open_agents(self) -> tuple[PaseoAgent, ...]:
        """Return agents that are not archived/closed."""
        return tuple(agent for agent in self.agents.values() if agent.status != "closed")

    @property
    def working_count(self) -> int:
        """Return the number of actively working agents."""
        return sum(agent.status in {"running", "initializing"} for agent in self.open_agents)

    @property
    def idle_count(self) -> int:
        """Return the number of idle agents."""
        return sum(agent.status == "idle" for agent in self.open_agents)

    @property
    def attention_count(self) -> int:
        """Return the number of agents needing attention."""
        return sum(agent.requires_attention for agent in self.open_agents)

    @property
    def failed_count(self) -> int:
        """Return the number of failed agents."""
        return sum(
            agent.status == "error" or agent.attention_reason == "error"
            for agent in self.open_agents
        )

    @property
    def finished_count(self) -> int:
        """Return the number of agents waiting after finishing."""
        return sum(agent.attention_reason == "finished" for agent in self.open_agents)

    @property
    def fleet_state(self) -> str:
        """Return the most important fleet state."""
        if self.failed_count:
            return "error"
        if self.attention_count:
            return "attention"
        if self.working_count:
            return "working"
        if self.open_agents:
            return "idle"
        return "quiet"

    @property
    def provider_ids(self) -> tuple[str, ...]:
        """Return all runtime and usage provider identifiers."""
        runtime_ids = {agent.provider_id for agent in self.open_agents}
        return tuple(sorted(runtime_ids | self.usage.keys()))

    def provider_agents(self, provider_id: str) -> tuple[PaseoAgent, ...]:
        """Return open agents for one provider."""
        return tuple(
            agent for agent in self.open_agents if agent.provider_id == provider_id
        )

    def provider_name(self, provider_id: str) -> str:
        """Return a human-readable provider name."""
        if provider_id in self.usage:
            return self.usage[provider_id].display_name
        return provider_id.replace("-", " ").replace("_", " ").title()

    def token_metrics(self, provider_id: str | None = None) -> PaseoTokenMetrics:
        """Aggregate numeric snapshots without claiming lifetime token consumption."""
        agents = (
            self.open_agents if provider_id is None else self.provider_agents(provider_id)
        )
        usages = tuple(agent.last_usage for agent in agents if agent.last_usage is not None)
        context_tokens = _sum_optional(
            usage.context_window_used_tokens for usage in usages
        )
        context_capacity = _sum_optional(
            usage.context_window_max_tokens for usage in usages
        )
        utilization = None
        if context_tokens is not None and context_capacity:
            utilization = round(context_tokens / context_capacity * 100, 1)
        reported_cost = _sum_optional(usage.total_cost_usd for usage in usages)
        return PaseoTokenMetrics(
            context_tokens=context_tokens,
            context_capacity=context_capacity,
            context_utilization=utilization,
            latest_input_tokens=_sum_optional(usage.input_tokens for usage in usages),
            latest_cached_input_tokens=_sum_optional(
                usage.cached_input_tokens for usage in usages
            ),
            latest_output_tokens=_sum_optional(usage.output_tokens for usage in usages),
            reported_session_cost=(
                round(reported_cost, 4) if reported_cost is not None else None
            ),
        )


def normalize_websocket_url(value: str) -> str:
    """Normalize an HTTP or WebSocket Paseo URL."""
    value = value.strip()
    parsed = urlparse(value)
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    if scheme not in {"ws", "wss"} or not parsed.netloc:
        msg = "Paseo URL must be an http(s) or ws(s) URL with a hostname"
        raise ValueError(msg)
    path = parsed.path.rstrip("/")
    if not path:
        path = "/ws"
    return urlunparse((scheme, parsed.netloc, path, "", parsed.query, ""))


def utc_now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now().astimezone().isoformat()


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_token_count(value: Any) -> int | None:
    number = _optional_number(value)
    if number is None or number < 0:
        return None
    return int(number)


def _sum_optional(values: Iterable[int | float | None]) -> int | float | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None
