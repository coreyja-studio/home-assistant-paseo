"""Event entity for Paseo activity."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PaseoConfigEntry
from .const import EVENT_TYPES
from .coordinator import PaseoCoordinator
from .entity import PaseoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PaseoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Paseo activity event entity."""
    async_add_entities([PaseoActivityEvent(entry.runtime_data.coordinator)])


class PaseoActivityEvent(PaseoEntity, EventEntity):
    """Report privacy-safe Paseo agent activity."""

    _attr_translation_key = "activity"
    _attr_icon = "mdi:robot-excited"
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: PaseoCoordinator) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, "activity")
        self._last_sequence = 0

    def _handle_coordinator_update(self) -> None:
        activity = self.coordinator.data.activity
        if activity is not None and activity.sequence > self._last_sequence:
            self._last_sequence = activity.sequence
            self._trigger_event(
                activity.event_type,
                {
                    "provider": activity.provider_id,
                    "agent_id": activity.agent_id,
                    "timestamp": activity.timestamp,
                },
            )
        self.async_write_ha_state()
