"""Shared Paseo entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PaseoCoordinator


class PaseoEntity(CoordinatorEntity[PaseoCoordinator]):
    """Base entity attached to the Paseo host."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PaseoCoordinator, key: str) -> None:
        """Initialize a Paseo entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.server_id}_{key}"

    @property
    def available(self) -> bool:
        """Keep stale counts visible while exposing connection separately."""
        return self.coordinator.data is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return Paseo host device information."""
        data = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, data.server_id)},
            name=data.hostname,
            manufacturer="Paseo",
            model="Agent orchestration server",
            sw_version=data.version,
        )


class PaseoProviderEntity(PaseoEntity):
    """Base entity attached to a provider child device."""

    def __init__(self, coordinator: PaseoCoordinator, provider_id: str, key: str) -> None:
        """Initialize a provider entity."""
        self.provider_id = provider_id
        super().__init__(coordinator, f"provider_{provider_id}_{key}")

    @property
    def device_info(self) -> DeviceInfo:
        """Return provider device information."""
        data = self.coordinator.data
        usage = data.usage.get(self.provider_id)
        return DeviceInfo(
            identifiers={(DOMAIN, f"{data.server_id}:provider:{self.provider_id}")},
            name=f"{data.provider_name(self.provider_id)} provider",
            manufacturer="Paseo",
            model=usage.plan if usage and usage.plan else "Agent provider",
            via_device=(DOMAIN, data.server_id),
        )
