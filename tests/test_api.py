"""Tests for the Paseo WebSocket client."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from aiohttp import web

from custom_components.paseo.api import PaseoClient
from custom_components.paseo.models import PaseoSnapshot


async def test_client_bootstraps_and_receives_safe_push_updates() -> None:
    """The client should combine RPC snapshots and subscribed agent updates."""
    connected_websocket: asyncio.Future[web.WebSocketResponse] = (
        asyncio.get_running_loop().create_future()
    )

    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        websocket = web.WebSocketResponse()
        await websocket.prepare(request)
        hello = await websocket.receive_json()
        assert hello["type"] == "hello"
        assert hello["capabilities"] == {"all_providers": True}
        await websocket.send_json(
            _session(
                "status",
                {
                    "status": "server_info",
                    "serverId": "server-1",
                    "hostname": "byte-vm",
                    "version": "0.7.2",
                },
            )
        )
        connected_websocket.set_result(websocket)

        async for frame in websocket:
            request_message = frame.json()["message"]
            if request_message["type"] == "fetch_agents_request":
                await websocket.send_json(
                    _session(
                        "fetch_agents_response",
                        {
                            "requestId": request_message["requestId"],
                            "subscriptionId": request_message["subscribe"]["subscriptionId"],
                            "entries": [
                                {
                                    "agent": {
                                        "id": "agent-1",
                                        "provider": "claude",
                                        "status": "idle",
                                        "requiresAttention": False,
                                        "title": "Private title",
                                        "cwd": "/private/path",
                                    },
                                    "project": {},
                                }
                            ],
                            "pageInfo": {
                                "nextCursor": None,
                                "prevCursor": None,
                                "hasMore": False,
                            },
                        },
                    )
                )
            elif request_message["type"] == "provider.usage.list.request":
                await websocket.send_json(
                    _session(
                        "provider.usage.list.response",
                        {
                            "requestId": request_message["requestId"],
                            "fetchedAt": "2026-09-06T12:00:00Z",
                            "providers": [
                                {
                                    "providerId": "claude",
                                    "displayName": "Claude",
                                    "status": "available",
                                    "planLabel": "Max",
                                    "windows": [
                                        {
                                            "id": "five-hour",
                                            "label": "Five hour",
                                            "remainingPct": 68,
                                        }
                                    ],
                                }
                            ],
                        },
                    )
                )
        return websocket

    app = web.Application()
    app.router.add_get("/ws", websocket_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # noqa: SLF001
    port = sockets[0].getsockname()[1]

    updates: list[PaseoSnapshot] = []
    activity_received = asyncio.Event()

    def receive(snapshot: PaseoSnapshot) -> None:
        updates.append(snapshot)
        if snapshot.activity is not None:
            activity_received.set()

    async with aiohttp.ClientSession() as session:
        client = PaseoClient(
            session,
            f"ws://127.0.0.1:{port}/ws",
            None,
            "home-assistant-test",
            receive,
        )
        snapshot = await client.async_start()
        assert snapshot.connected
        assert snapshot.hostname == "byte-vm"
        assert len(snapshot.open_agents) == 1
        assert snapshot.usage["claude"].windows[0].remaining_percent == 68
        assert not hasattr(snapshot.agents["agent-1"], "title")

        websocket = await connected_websocket
        await websocket.send_json(
            _session(
                "agent_update",
                {
                    "kind": "upsert",
                    "agent": {
                        "id": "agent-1",
                        "provider": "claude",
                        "status": "running",
                        "requiresAttention": False,
                        "title": "Still private",
                        "cwd": "/still/private",
                    },
                },
            )
        )
        async with asyncio.timeout(2):
            await activity_received.wait()
        assert updates[-1].activity is not None
        assert updates[-1].activity.event_type == "started"
        assert updates[-1].working_count == 1

        await client.async_stop()

    await runner.cleanup()


def _session(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "session",
        "message": {"type": message_type, "payload": payload},
    }
