"""Async WebSocket client for Paseo."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import aiohttp

from .const import (
    CONNECT_TIMEOUT_SECONDS,
    EVENT_FAILED,
    EVENT_FINISHED,
    EVENT_NEEDS_INPUT,
    EVENT_STARTED,
    INTEGRATION_VERSION,
    MAX_MESSAGE_SIZE,
    USAGE_REFRESH_INTERVAL,
)
from .models import (
    PaseoActivity,
    PaseoAgent,
    PaseoAuthenticationError,
    PaseoConnectionError,
    PaseoProviderUsage,
    PaseoSnapshot,
    normalize_websocket_url,
    utc_now_iso,
)

_LOGGER = logging.getLogger(__name__)

SnapshotCallback = Callable[[PaseoSnapshot], None]


class PaseoClient:
    """Maintain a read-only connection to a Paseo server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        password: str | None,
        client_id: str,
        on_update: SnapshotCallback,
    ) -> None:
        """Initialize a Paseo client."""
        self._session = session
        self._url = normalize_websocket_url(url)
        self._password = password
        self._client_id = client_id
        self._on_update = on_update
        self._snapshot = PaseoSnapshot()
        self._runner: asyncio.Task[None] | None = None
        self._usage_timer: asyncio.Task[None] | None = None
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._initial: asyncio.Future[PaseoSnapshot] | None = None
        self._sequence = 0

    @property
    def snapshot(self) -> PaseoSnapshot:
        """Return the latest snapshot."""
        return self._snapshot

    async def async_start(self) -> PaseoSnapshot:
        """Start the client and wait for its first complete snapshot."""
        if self._runner is not None:
            return self._snapshot
        self._initial = asyncio.get_running_loop().create_future()
        self._runner = asyncio.create_task(self._run(), name="paseo-websocket")
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                return await asyncio.shield(self._initial)
        except TimeoutError as err:
            raise PaseoConnectionError("Timed out waiting for Paseo") from err

    async def async_stop(self) -> None:
        """Stop the client."""
        for task in (self._usage_timer, self._runner):
            if task is not None:
                task.cancel()
        if self._websocket is not None:
            await self._websocket.close()
        for task in (self._usage_timer, self._runner):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._usage_timer = None
        self._runner = None
        self._websocket = None

    async def _run(self) -> None:
        delay = 1
        while True:
            try:
                await self._connect_and_listen()
                delay = 1
            except asyncio.CancelledError:
                raise
            except PaseoAuthenticationError as err:
                self._fail_initial(err)
                _LOGGER.error("Paseo authentication failed")
                return
            except (aiohttp.ClientError, OSError, PaseoConnectionError) as err:
                self._fail_initial(PaseoConnectionError(str(err)))
                _LOGGER.warning("Paseo connection lost; retrying in %s seconds: %s", delay, err)
            self._set_connected(False)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _connect_and_listen(self) -> None:
        protocols = (f"paseo.bearer.{self._password}",) if self._password else ()
        try:
            websocket = await self._session.ws_connect(
                self._url,
                protocols=protocols,
                max_msg_size=MAX_MESSAGE_SIZE,
                heartbeat=30,
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status in {401, 403}:
                raise PaseoAuthenticationError("Paseo rejected the password") from err
            raise

        self._websocket = websocket
        try:
            await websocket.send_json(_hello(self._client_id))
            await self._listen(websocket)
        finally:
            self._websocket = None
            if self._usage_timer is not None:
                self._usage_timer.cancel()
                self._usage_timer = None
            await websocket.close()

    async def _listen(self, websocket: aiohttp.ClientWebSocketResponse) -> None:
        server_ready = False
        agents_ready = False
        usage_ready = False
        agent_request_id = _request_id("agents")
        usage_request_id = _request_id("usage")

        async for frame in websocket:
            if frame.type is aiohttp.WSMsgType.TEXT:
                try:
                    envelope = json.loads(frame.data)
                except json.JSONDecodeError:
                    _LOGGER.debug("Ignoring malformed Paseo frame")
                    continue
                if envelope.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                message = _session_message(envelope)
                if message is None:
                    continue
                message_type = message.get("type")
                payload = message.get("payload")
                if not isinstance(payload, dict):
                    continue

                if message_type == "status" and payload.get("status") == "server_info":
                    self._apply_server_info(payload)
                    server_ready = True
                    await websocket.send_json(
                        _wrap(
                            {
                                "type": "fetch_agents_request",
                                "requestId": agent_request_id,
                                "scope": "active",
                                "page": {"limit": 200},
                                "subscribe": {"subscriptionId": f"ha-{self._client_id}"},
                            }
                        )
                    )
                    await websocket.send_json(
                        _wrap(
                            {
                                "type": "provider.usage.list.request",
                                "requestId": usage_request_id,
                            }
                        )
                    )
                elif (
                    message_type == "fetch_agents_response"
                    and payload.get("requestId") == agent_request_id
                ):
                    self._apply_agents(payload)
                    agents_ready = True
                elif message_type == "agent_update":
                    self._apply_agent_update(payload)
                elif message_type == "provider.usage.list.response":
                    self._apply_usage(payload)
                    if payload.get("requestId") == usage_request_id:
                        usage_ready = True

                if server_ready and agents_ready and usage_ready and not self._snapshot.connected:
                    self._snapshot = self._copy_snapshot(connected=True)
                    self._publish()
                    self._resolve_initial()
                    self._usage_timer = asyncio.create_task(
                        self._refresh_usage(), name="paseo-usage-refresh"
                    )
            elif frame.type is aiohttp.WSMsgType.ERROR:
                raise PaseoConnectionError(str(websocket.exception()))
            elif frame.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}:
                break

        if websocket.close_code == 4401:
            raise PaseoAuthenticationError("Paseo rejected the password")
        raise PaseoConnectionError(f"Paseo closed the connection ({websocket.close_code})")

    async def _refresh_usage(self) -> None:
        while True:
            await asyncio.sleep(USAGE_REFRESH_INTERVAL.total_seconds())
            websocket = self._websocket
            if websocket is not None and not websocket.closed:
                await websocket.send_json(
                    _wrap(
                        {
                            "type": "provider.usage.list.request",
                            "requestId": _request_id("usage"),
                        }
                    )
                )

    def _apply_server_info(self, payload: dict[str, Any]) -> None:
        self._snapshot = PaseoSnapshot(
            connected=self._snapshot.connected,
            server_id=str(payload.get("serverId") or "unknown"),
            hostname=str(payload.get("hostname") or "Paseo"),
            version=str(payload["version"]) if payload.get("version") is not None else None,
            agents=self._snapshot.agents,
            usage=self._snapshot.usage,
            activity=self._snapshot.activity,
        )

    def _apply_agents(self, payload: dict[str, Any]) -> None:
        agents: dict[str, PaseoAgent] = {}
        for entry in payload.get("entries", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("agent"), dict):
                continue
            agent = PaseoAgent.from_payload(entry["agent"])
            if agent.agent_id:
                agents[agent.agent_id] = agent
        self._snapshot = self._copy_snapshot(agents=agents)

    def _apply_agent_update(self, payload: dict[str, Any]) -> None:
        agents = dict(self._snapshot.agents)
        activity: PaseoActivity | None = None
        if payload.get("kind") == "remove":
            agents.pop(str(payload.get("agentId", "")), None)
        elif payload.get("kind") == "upsert" and isinstance(payload.get("agent"), dict):
            agent = PaseoAgent.from_payload(payload["agent"])
            previous = agents.get(agent.agent_id)
            agents[agent.agent_id] = agent
            activity = self._activity_for(previous, agent)
        self._snapshot = self._copy_snapshot(agents=agents, activity=activity)
        if self._snapshot.connected:
            self._publish()

    def _apply_usage(self, payload: dict[str, Any]) -> None:
        usage: dict[str, PaseoProviderUsage] = {}
        for provider_payload in payload.get("providers", []):
            if not isinstance(provider_payload, dict):
                continue
            provider = PaseoProviderUsage.from_payload(provider_payload)
            usage[provider.provider_id] = provider
        self._snapshot = self._copy_snapshot(usage=usage)
        if self._snapshot.connected:
            self._publish()

    def _activity_for(
        self, previous: PaseoAgent | None, current: PaseoAgent
    ) -> PaseoActivity | None:
        event_type: str | None = None
        if (
            previous is not None
            and previous.status != current.status
            and current.status == "running"
        ):
            event_type = EVENT_STARTED
        if previous is not None and previous.attention_timestamp != current.attention_timestamp:
            event_type = {
                "finished": EVENT_FINISHED,
                "error": EVENT_FAILED,
                "permission": EVENT_NEEDS_INPUT,
            }.get(current.attention_reason)
        if (
            event_type is None
            and previous is not None
            and previous.status != "error"
            and current.status == "error"
        ):
            event_type = EVENT_FAILED
        if event_type is None:
            return None
        self._sequence += 1
        return PaseoActivity(
            sequence=self._sequence,
            event_type=event_type,
            provider_id=current.provider_id,
            agent_id=current.agent_id,
            timestamp=current.attention_timestamp or utc_now_iso(),
        )

    def _copy_snapshot(self, **changes: Any) -> PaseoSnapshot:
        values = {
            "connected": self._snapshot.connected,
            "server_id": self._snapshot.server_id,
            "hostname": self._snapshot.hostname,
            "version": self._snapshot.version,
            "agents": self._snapshot.agents,
            "usage": self._snapshot.usage,
            "activity": self._snapshot.activity,
        }
        values.update(changes)
        return PaseoSnapshot(**values)

    def _set_connected(self, connected: bool) -> None:
        if self._snapshot.connected == connected:
            return
        self._snapshot = self._copy_snapshot(connected=connected)
        self._publish()

    def _publish(self) -> None:
        self._on_update(self._snapshot)

    def _resolve_initial(self) -> None:
        if self._initial is not None and not self._initial.done():
            self._initial.set_result(self._snapshot)

    def _fail_initial(self, error: Exception) -> None:
        if self._initial is not None and not self._initial.done():
            self._initial.set_exception(error)


async def async_probe_paseo(
    session: aiohttp.ClientSession, url: str, password: str | None
) -> dict[str, str]:
    """Connect briefly and return safe server identity information."""
    normalized_url = normalize_websocket_url(url)
    protocols = (f"paseo.bearer.{password}",) if password else ()
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
            websocket = await session.ws_connect(
                normalized_url,
                protocols=protocols,
                max_msg_size=MAX_MESSAGE_SIZE,
            )
            try:
                await websocket.send_json(_hello(f"home-assistant-probe-{uuid4().hex}"))
                async for frame in websocket:
                    if frame.type is not aiohttp.WSMsgType.TEXT:
                        break
                    envelope = json.loads(frame.data)
                    message = _session_message(envelope)
                    payload = message.get("payload") if message else None
                    if (
                        message
                        and message.get("type") == "status"
                        and isinstance(payload, dict)
                        and payload.get("status") == "server_info"
                    ):
                        return {
                            "server_id": str(payload.get("serverId") or normalized_url),
                            "hostname": str(payload.get("hostname") or "Paseo"),
                        }
            finally:
                await websocket.close()
    except aiohttp.WSServerHandshakeError as err:
        if err.status in {401, 403}:
            raise PaseoAuthenticationError("Paseo rejected the password") from err
        raise PaseoConnectionError(str(err)) from err
    except (TimeoutError, aiohttp.ClientError, OSError, json.JSONDecodeError) as err:
        raise PaseoConnectionError(str(err)) from err
    raise PaseoConnectionError("Paseo closed before identifying itself")


def _hello(client_id: str) -> dict[str, Any]:
    return {
        "type": "hello",
        "clientId": client_id,
        "clientType": "cli",
        "protocolVersion": 1,
        "appVersion": INTEGRATION_VERSION,
        "capabilities": {"all_providers": True},
    }


def _wrap(message: dict[str, Any]) -> dict[str, Any]:
    return {"type": "session", "message": message}


def _session_message(envelope: Any) -> dict[str, Any] | None:
    if not isinstance(envelope, dict) or envelope.get("type") != "session":
        return None
    message = envelope.get("message")
    return message if isinstance(message, dict) else None


def _request_id(prefix: str) -> str:
    return f"ha-{prefix}-{uuid4().hex}"
