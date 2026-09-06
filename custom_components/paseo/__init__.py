"""The Paseo integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PaseoClient
from .const import CONF_PASSWORD, CONF_URL
from .coordinator import PaseoCoordinator
from .models import PaseoError, PaseoSnapshot

PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT)


@dataclass(slots=True)
class PaseoRuntimeData:
    """Runtime data for one Paseo config entry."""

    coordinator: PaseoCoordinator


type PaseoConfigEntry = ConfigEntry[PaseoRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: PaseoConfigEntry) -> bool:
    """Set up Paseo from a config entry."""
    coordinator: PaseoCoordinator

    def receive_snapshot(snapshot: PaseoSnapshot) -> None:
        coordinator.async_receive_snapshot(snapshot)

    client = PaseoClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data.get(CONF_PASSWORD),
        f"home-assistant-{entry.entry_id}",
        receive_snapshot,
    )
    coordinator = PaseoCoordinator(hass, client)
    try:
        await coordinator.async_start()
    except PaseoError as err:
        await coordinator.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = PaseoRuntimeData(coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PaseoConfigEntry) -> bool:
    """Unload a Paseo config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.coordinator.async_stop()
    return unload_ok
