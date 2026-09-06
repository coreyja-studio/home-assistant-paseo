"""Tests for Paseo's privacy-safe models."""

from custom_components.paseo.models import (
    PaseoAgent,
    PaseoProviderUsage,
    PaseoSnapshot,
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
        }
    )

    assert agent.agent_id == "agent-1"
    assert agent.provider_id == "claude"
    assert not hasattr(agent, "title")
    assert not hasattr(agent, "cwd")
    assert not hasattr(agent, "last_error")


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
