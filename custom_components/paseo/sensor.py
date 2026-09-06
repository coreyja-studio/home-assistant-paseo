"""Sensors for Paseo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PaseoConfigEntry
from .coordinator import PaseoCoordinator
from .entity import PaseoEntity, PaseoProviderEntity
from .models import PaseoSnapshot


@dataclass(frozen=True, slots=True)
class PaseoSensorDefinition:
    """Definition of a host-level sensor."""

    key: str
    icon: str
    value: Callable[[PaseoSnapshot], str | int]


HOST_SENSORS = (
    PaseoSensorDefinition("fleet_state", "mdi:robot-industrial", lambda data: data.fleet_state),
    PaseoSensorDefinition("open_agents", "mdi:robot", lambda data: len(data.open_agents)),
    PaseoSensorDefinition(
        "working_agents", "mdi:robot-industrial", lambda data: data.working_count
    ),
    PaseoSensorDefinition("idle_agents", "mdi:robot-outline", lambda data: data.idle_count),
    PaseoSensorDefinition("finished_agents", "mdi:robot-happy", lambda data: data.finished_count),
    PaseoSensorDefinition(
        "attention_agents", "mdi:robot-confused", lambda data: data.attention_count
    ),
    PaseoSensorDefinition("failed_agents", "mdi:robot-dead", lambda data: data.failed_count),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaseoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Paseo sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(PaseoHostSensor(coordinator, definition) for definition in HOST_SENSORS)

    seen: set[str] = set()

    def add_provider_entities() -> None:
        entities: list[SensorEntity] = []
        for provider_id in coordinator.data.provider_ids:
            for metric in ("open", "working"):
                unique_key = f"provider:{provider_id}:{metric}"
                if unique_key not in seen:
                    seen.add(unique_key)
                    entities.append(PaseoProviderCountSensor(coordinator, provider_id, metric))
            usage = coordinator.data.usage.get(provider_id)
            if usage is None:
                continue
            for window in usage.windows:
                unique_key = f"quota:{provider_id}:{window.window_id}"
                if unique_key not in seen:
                    seen.add(unique_key)
                    entities.append(
                        PaseoQuotaSensor(coordinator, provider_id, window.window_id)
                    )
        if entities:
            async_add_entities(entities)

    add_provider_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_provider_entities))


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
        if definition.key != "fleet_state":
            self._attr_native_unit_of_measurement = "agents"
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> str | int:
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
