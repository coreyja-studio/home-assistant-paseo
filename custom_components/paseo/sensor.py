"""Sensors for Paseo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PaseoConfigEntry
from .coordinator import PaseoCoordinator
from .entity import PaseoEntity, PaseoProviderEntity
from .models import PaseoSnapshot, anonymous_session_key


@dataclass(frozen=True, slots=True)
class PaseoSensorDefinition:
    """Definition of a host-level sensor."""

    key: str
    icon: str
    value: Callable[[PaseoSnapshot], str | int | float | None]
    unit: str | None = None
    precision: int | None = None


HOST_SENSORS = (
    PaseoSensorDefinition("fleet_state", "mdi:robot-industrial", lambda data: data.fleet_state),
    PaseoSensorDefinition(
        "open_agents", "mdi:robot", lambda data: len(data.open_agents), "agents"
    ),
    PaseoSensorDefinition(
        "working_agents",
        "mdi:robot-industrial",
        lambda data: data.working_count,
        "agents",
    ),
    PaseoSensorDefinition(
        "idle_agents", "mdi:robot-outline", lambda data: data.idle_count, "agents"
    ),
    PaseoSensorDefinition(
        "finished_agents", "mdi:robot-happy", lambda data: data.finished_count, "agents"
    ),
    PaseoSensorDefinition(
        "attention_agents",
        "mdi:robot-confused",
        lambda data: data.attention_count,
        "agents",
    ),
    PaseoSensorDefinition(
        "failed_agents", "mdi:robot-dead", lambda data: data.failed_count, "agents"
    ),
    PaseoSensorDefinition(
        "context_tokens",
        "mdi:brain",
        lambda data: data.token_metrics().context_tokens,
        "tokens",
    ),
    PaseoSensorDefinition(
        "context_capacity",
        "mdi:brain-freeze",
        lambda data: data.token_metrics().context_capacity,
        "tokens",
    ),
    PaseoSensorDefinition(
        "context_utilization",
        "mdi:gauge",
        lambda data: data.token_metrics().context_utilization,
        "%",
        1,
    ),
    PaseoSensorDefinition(
        "latest_input_tokens",
        "mdi:arrow-collapse-down",
        lambda data: data.token_metrics().latest_input_tokens,
        "tokens",
    ),
    PaseoSensorDefinition(
        "latest_cached_input_tokens",
        "mdi:database-clock",
        lambda data: data.token_metrics().latest_cached_input_tokens,
        "tokens",
    ),
    PaseoSensorDefinition(
        "latest_output_tokens",
        "mdi:arrow-collapse-up",
        lambda data: data.token_metrics().latest_output_tokens,
        "tokens",
    ),
    PaseoSensorDefinition(
        "reported_session_cost",
        "mdi:currency-usd",
        lambda data: data.token_metrics().reported_session_cost,
        "USD",
        2,
    ),
    PaseoSensorDefinition(
        "hottest_session_context",
        "mdi:thermometer-alert",
        lambda data: data.hottest_session_context,
        "%",
        1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaseoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Paseo sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(PaseoHostSensor(coordinator, definition) for definition in HOST_SENSORS)

    seen_providers: set[str] = set()
    session_entities: dict[str, PaseoSessionContextSensor] = {}
    registry = er.async_get(hass)
    session_unique_id_prefix = f"{coordinator.data.server_id}_session_"
    current_session_unique_ids = {
        f"{session_unique_id_prefix}{anonymous_session_key(agent.agent_id)}_context"
        for agent in coordinator.data.open_agents
        if agent.last_usage is not None
        and agent.last_usage.context_utilization is not None
    }
    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registry_entry.unique_id.startswith(session_unique_id_prefix)
            and registry_entry.unique_id not in current_session_unique_ids
        ):
            registry.async_remove(registry_entry.entity_id)

    async def remove_session_entity(entity: PaseoSessionContextSensor) -> None:
        entity_id = entity.entity_id
        await entity.async_remove()
        if entity_id is not None and registry.async_get(entity_id) is not None:
            registry.async_remove(entity_id)

    def sync_dynamic_entities() -> None:
        entities: list[SensorEntity] = []
        for provider_id in coordinator.data.provider_ids:
            for metric in ("open", "working"):
                unique_key = f"provider:{provider_id}:{metric}"
                if unique_key not in seen_providers:
                    seen_providers.add(unique_key)
                    entities.append(PaseoProviderCountSensor(coordinator, provider_id, metric))
            usage = coordinator.data.usage.get(provider_id)
            if usage is None:
                continue
            for window in usage.windows:
                unique_key = f"quota:{provider_id}:{window.window_id}"
                if unique_key not in seen_providers:
                    seen_providers.add(unique_key)
                    entities.append(
                        PaseoQuotaSensor(coordinator, provider_id, window.window_id)
                    )

        current_session_keys: set[str] = set()
        for agent in coordinator.data.open_agents:
            session_key = anonymous_session_key(agent.agent_id)
            current_session_keys.add(session_key)
            usage = agent.last_usage
            if usage is None or usage.context_utilization is None:
                continue
            if session_key not in session_entities:
                entity = PaseoSessionContextSensor(coordinator, agent.agent_id)
                session_entities[session_key] = entity
                entities.append(entity)

        for session_key in session_entities.keys() - current_session_keys:
            entity = session_entities.pop(session_key)
            hass.async_create_task(remove_session_entity(entity))

        if entities:
            async_add_entities(entities)

    sync_dynamic_entities()
    entry.async_on_unload(coordinator.async_add_listener(sync_dynamic_entities))


class PaseoHostSensor(PaseoEntity, SensorEntity):
    """A host-level Paseo sensor."""

    def __init__(
        self, coordinator: PaseoCoordinator, definition: PaseoSensorDefinition
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, definition.key)
        self._definition = definition
        self._attr_translation_key = definition.key
        self._attr_icon = definition.icon
        if definition.unit is not None:
            self._attr_native_unit_of_measurement = definition.unit
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = definition.precision

    @property
    def native_value(self) -> str | int | float | None:
        """Return the current value."""
        return self._definition.value(self.coordinator.data)


class PaseoProviderCountSensor(PaseoProviderEntity, SensorEntity):
    """Number of agents on one provider."""

    _attr_native_unit_of_measurement = "agents"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: PaseoCoordinator, provider_id: str, metric: str
    ) -> None:
        """Initialize a provider count sensor."""
        super().__init__(coordinator, provider_id, metric)
        self.metric = metric
        self._attr_name = "Active turns" if metric == "working" else "Open agents"
        self._attr_icon = "mdi:robot-industrial" if metric == "working" else "mdi:robot"

    @property
    def native_value(self) -> int:
        """Return the provider agent count."""
        agents = self.coordinator.data.provider_agents(self.provider_id)
        if self.metric == "working":
            return sum(agent.status in {"running", "initializing"} for agent in agents)
        return len(agents)


class PaseoSessionContextSensor(PaseoEntity, SensorEntity):
    """Current context utilization for one anonymous agent session."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator: PaseoCoordinator, agent_id: str) -> None:
        """Initialize an anonymous session context sensor."""
        self.agent_id = agent_id
        session_key = anonymous_session_key(agent_id)
        agent = coordinator.data.agents[agent_id]
        provider_name = coordinator.data.provider_name(agent.provider_id)
        super().__init__(coordinator, f"session_{session_key}_context")
        self._attr_name = f"{provider_name} session {session_key} context"

    @property
    def _usage(self) -> Any:
        agent = self.coordinator.data.agents.get(self.agent_id)
        if agent is None or agent.status == "closed":
            return None
        return agent.last_usage

    @property
    def native_value(self) -> float | None:
        """Return this session's current context utilization."""
        usage = self._usage
        return usage.context_utilization if usage is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return numeric usage without exposing session content or raw identifiers."""
        agent = self.coordinator.data.agents.get(self.agent_id)
        usage = self._usage
        if agent is None or usage is None:
            return {}
        return {
            "provider": agent.provider_id,
            "context_tokens": usage.context_window_used_tokens,
            "context_capacity": usage.context_window_max_tokens,
            "latest_input_tokens": usage.input_tokens,
            "latest_cached_input_tokens": usage.cached_input_tokens,
            "latest_output_tokens": usage.output_tokens,
            "reported_session_cost": usage.total_cost_usd,
        }


class PaseoQuotaSensor(PaseoProviderEntity, SensorEntity):
    """Remaining percentage in one provider quota window."""

    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:gauge"

    def __init__(
        self, coordinator: PaseoCoordinator, provider_id: str, window_id: str
    ) -> None:
        """Initialize a quota sensor."""
        super().__init__(coordinator, provider_id, f"quota_{window_id}")
        self.window_id = window_id
        window = self._window
        self._attr_name = f"{window.label} remaining" if window else "Quota remaining"

    @property
    def _window(self) -> Any:
        usage = self.coordinator.data.usage.get(self.provider_id)
        if usage is None:
            return None
        return next((item for item in usage.windows if item.window_id == self.window_id), None)

    @property
    def native_value(self) -> float | None:
        """Return the quota remaining percentage."""
        window = self._window
        return window.remaining_percent if window else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return quota timing and health details."""
        window = self._window
        usage = self.coordinator.data.usage.get(self.provider_id)
        if window is None or usage is None:
            return {}
        return {
            "used_percent": window.used_percent,
            "resets_at": window.resets_at,
            "runs_out_at": window.runs_out_at,
            "shortfall_percent": window.shortfall_percent,
            "tone": window.tone,
            "provider_status": usage.status,
            "plan": usage.plan,
            "fetched_at": usage.fetched_at,
        }
