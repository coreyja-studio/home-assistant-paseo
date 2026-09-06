"""Privacy-safe Paseo data models."""

from __future__ import annotations

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
class PaseoAgent:
    """Safe subset of an agent record."""

    agent_id: str
    provider_id: str
    status: str
    requires_attention: bool
    attention_reason: str | None
    attention_timestamp: str | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PaseoAgent:
        """Create an agent while discarding all potentially sensitive fields."""
        return cls(
            agent_id=str(payload.get("id", "")),
            provider_id=_optional_string(payload.get("provider") or payload.get("providerId"))
            or "unknown",
            status=_optional_string(payload.get("status")) or "unknown",
            requires_attention=bool(payload.get("requiresAttention", False)),
            attention_reason=_optional_string(payload.get("attentionReason")),
            attention_timestamp=_optional_string(payload.get("attentionTimestamp")),
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
