"""Tests for Paseo's privacy-safe models."""

from custom_components.paseo.models import (
    PaseoAgent,
    PaseoAgentUsage,
    PaseoProviderUsage,
    PaseoSnapshot,
    anonymous_session_key,
    normalize_websocket_url,
)


def test_agent_discards_sensitive_fields() -> None:
    """Agent prompts, paths, output, and titles must never enter snapshots."""
    agent = PaseoAgent.from_payload(
        {
            "id": "agent-1",
            "provider": "claude",
            "status": "running",
            "requiresAttention": False,
            "title": "Secret title",
            "cwd": "/secret/customer/path",
            "lastError": "Sensitive model output",
            "lastUsage": {
                "inputTokens": 120,
                "cachedInputTokens": 80,
                "outputTokens": 40,
                "totalCostUsd": 0.125,
                "contextWindowMaxTokens": 200_000,
                "contextWindowUsedTokens": 50_000,
                "prompt": "Still secret",
            },
        }
    )

    assert agent.agent_id == "agent-1"
    assert agent.provider_id == "claude"
    assert not hasattr(agent, "title")
    assert not hasattr(agent, "cwd")
    assert not hasattr(agent, "last_error")
    assert agent.last_usage == PaseoAgentUsage(
        input_tokens=120,
        cached_input_tokens=80,
        output_tokens=40,
        total_cost_usd=0.125,
        context_window_max_tokens=200_000,
        context_window_used_tokens=50_000,
    )
    assert not hasattr(agent.last_usage, "prompt")


def test_snapshot_counts_and_precedence() -> None:
    """Fleet state should prioritize failures over all other activity."""
    agents = {
        "working": PaseoAgent("working", "claude", "running", False, None, None),
        "idle": PaseoAgent("idle", "codex", "idle", False, None, None),
        "failed": PaseoAgent("failed", "claude", "error", True, "error", "now"),
        "closed": PaseoAgent("closed", "claude", "closed", False, None, None),
    }
    snapshot = PaseoSnapshot(connected=True, agents=agents)

    assert len(snapshot.open_agents) == 3
    assert snapshot.working_count == 1
    assert snapshot.idle_count == 1
    assert snapshot.attention_count == 1
    assert snapshot.failed_count == 1
    assert snapshot.fleet_state == "error"
    assert snapshot.provider_ids == ("claude", "codex")


def test_usage_accepts_wire_names_and_derives_remaining() -> None:
    """Quota fields should follow Paseo's wire schema."""
    usage = PaseoProviderUsage.from_payload(
        {
            "providerId": "zai",
            "displayName": "Z.ai",
            "status": "available",
            "planLabel": "Coding Plan",
            "fetchedAt": "2026-09-06T12:00:00Z",
            "windows": [{"id": "weekly", "label": "Weekly", "usedPct": 23}],
        }
    )

    assert usage.plan == "Coding Plan"
    assert usage.windows[0].remaining_percent == 77


def test_token_metrics_aggregate_fleet_and_provider_snapshots() -> None:
    """Usage snapshots should aggregate without inventing missing cost data."""
    snapshot = PaseoSnapshot(
        connected=True,
        agents={
            "claude": PaseoAgent(
                "claude",
                "claude",
                "idle",
                False,
                None,
                None,
                PaseoAgentUsage(100, 80, 20, 1.25, 1_000, 500),
            ),
            "codex": PaseoAgent(
                "codex",
                "codex",
                "running",
                False,
                None,
                None,
                PaseoAgentUsage(200, 150, 30, None, 1_000, 250),
            ),
            "closed": PaseoAgent(
                "closed",
                "claude",
                "closed",
                False,
                None,
                None,
                PaseoAgentUsage(999, 999, 999, 99.0, 1_000, 1_000),
            ),
        },
    )

    fleet = snapshot.token_metrics()
    assert fleet.context_tokens == 750
    assert fleet.context_capacity == 2_000
    assert fleet.context_utilization == 37.5
    assert fleet.latest_input_tokens == 300
    assert fleet.latest_cached_input_tokens == 230
    assert fleet.latest_output_tokens == 50
    assert fleet.reported_session_cost == 1.25

    codex = snapshot.token_metrics("codex")
    assert codex.context_tokens == 250
    assert codex.context_capacity == 1_000
    assert codex.context_utilization == 25.0
    assert codex.reported_session_cost is None


def test_token_metrics_are_unknown_without_usage() -> None:
    """Missing provider usage should stay unknown instead of looking like zero."""
    metrics = PaseoSnapshot().token_metrics("missing")

    assert metrics.context_tokens is None
    assert metrics.context_capacity is None
    assert metrics.context_utilization is None
    assert metrics.latest_input_tokens is None
    assert metrics.latest_cached_input_tokens is None
    assert metrics.latest_output_tokens is None
    assert metrics.reported_session_cost is None


def test_session_context_is_per_agent_and_anonymous() -> None:
    """Session utilization should preserve no raw Paseo identifier."""
    raw_agent_id = "agent-secret-identifier"
    usage = PaseoAgentUsage(100, 80, 20, None, 200_000, 50_000)
    snapshot = PaseoSnapshot(
        agents={
            raw_agent_id: PaseoAgent(
                raw_agent_id, "claude", "running", False, None, None, usage
            ),
            "hotter": PaseoAgent(
                "hotter",
                "codex",
                "idle",
                False,
                None,
                None,
                PaseoAgentUsage(100, 0, 20, None, 100_000, 80_000),
            ),
        }
    )

    assert usage.context_utilization == 25.0
    assert snapshot.hottest_session_context == 80.0
    assert anonymous_session_key(raw_agent_id) == anonymous_session_key(raw_agent_id)
    assert raw_agent_id not in anonymous_session_key(raw_agent_id)


def test_normalize_websocket_url() -> None:
    """HTTP URLs become WebSocket URLs and bare hosts are rejected."""
    assert normalize_websocket_url("https://paseo.example.ts.net") == (
        "wss://paseo.example.ts.net/ws"
    )
    assert normalize_websocket_url("ws://localhost:6767/ws/") == "ws://localhost:6767/ws"

    try:
        normalize_websocket_url("localhost:6767")
    except ValueError:
        pass
    else:
        raise AssertionError("Bare host should have been rejected")
