"""Binary sensors for Paseo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PaseoConfigEntry
from .coordinator import PaseoCoordinator
from .entity import PaseoEntity
from .models import PaseoSnapshot


@dataclass(frozen=True, slots=True)
class PaseoBinarySensorDefinition:
    """Definition of a Paseo binary sensor."""

    key: str
    icon: str
    value: Callable[[PaseoSnapshot], bool]


BINARY_SENSORS = (
    PaseoBinarySensorDefinition("connected", "mdi:lan-connect", lambda data: data.connected),
    PaseoBinarySensorDefinition(
        "attention_needed", "mdi:robot-confused", lambda data: data.attention_count > 0
    ),
    PaseoBinarySensorDefinition(
        "agent_failure", "mdi:robot-dead", lambda data: data.failed_count > 0
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaseoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Paseo binary sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        PaseoBinarySensor(coordinator, definition) for definition in BINARY_SENSORS
    )


class PaseoBinarySensor(PaseoEntity, BinarySensorEntity):
    """A Paseo binary sensor."""

    def __init__(
        self, coordinator: PaseoCoordinator, definition: PaseoBinarySensorDefinition
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, definition.key)
        self._definition = definition
        self._attr_translation_key = definition.key
        self._attr_icon = definition.icon

    @property
    def is_on(self) -> bool:
        """Return whether the condition is active."""
        return self._definition.value(self.coordinator.data)
